"""Paired benchmark comparison service: load two runs, compare, persist artifacts.

Runs load from the database (``EvaluationRun`` + its ``TaskRun`` summaries) or, read-only, from a
reproducibility bundle (``bundle.json`` + ``episodes.jsonl.gz``; never written). Reports are
stored as artifacts (``comparisons/{id}/``) and under ``reports_dir/comparisons/{id}/``; there is
no comparison table and no database migration.
"""

from __future__ import annotations

import gzip
import json
import uuid
from pathlib import Path
from typing import Any, NoReturn

from pydantic import ValidationError

from traceforge.application.reproducibility import verify_bundle
from traceforge.application.services import ArtifactWriter
from traceforge.contracts.common import NotFoundError
from traceforge.contracts.comparison import ComparisonRequest, RunRef
from traceforge.contracts.experiments import BenchmarkConfig, RolloutSummary
from traceforge.infrastructure.database.repositories import ExperimentRepository
from traceforge.infrastructure.database.session import Database
from traceforge.modules.evaluation import comparison_contract as cc
from traceforge.modules.evaluation.comparison import ComparisonRefused, RunData, compare
from traceforge.modules.evaluation.comparison_reports import (
    comparison_document,
    comparison_markdown,
    refusal_document,
)
from traceforge.modules.evaluation.scenarios import (
    CatalogueScenarioResolver,
    CompositeScenarioResolver,
    LegacyScenarioResolver,
)
from traceforge.modules.evaluation.serialization import dumps_finite
from traceforge.modules.tasks.registry import TaskRegistry


class ComparisonRefusedError(Exception):
    """The comparison was refused; ``paths`` locate the persisted refusal record."""

    def __init__(self, document: dict[str, Any], paths: dict[str, str]) -> None:
        codes = sorted({p["code"] for p in document["matching"]["problems"]})
        super().__init__(f"comparison refused: {', '.join(codes)}")
        self.document = document
        self.paths = paths


class _LoadRefusal(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code, self.detail = code, detail


def _normalised_config(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate a stored config as a BenchmarkConfig and dump it with all defaults filled."""
    return BenchmarkConfig.model_validate(raw).model_dump(mode="json")


def _summaries(raw: list[Any], where: str) -> list[RolloutSummary]:
    out = []
    for i, item in enumerate(raw):
        try:
            out.append(RolloutSummary.model_validate(item))
        except ValidationError as exc:
            raise _LoadRefusal(
                "invalid_record", f"{where}: record {i} is not a valid rollout summary: {exc}"
            ) from exc
    return out


def _check_bundle_consistency(cfg: BenchmarkConfig, records: list[RolloutSummary]) -> None:
    """A bundle's config map is not linked to its evaluation ids: verify that the selected
    config actually produced these records (provider, policy, verifier, reward, splits, seeds)."""
    if not records:
        raise _LoadRefusal("invalid_bundle", "no episodes of this evaluation in the bundle")
    seeds = set(cfg.effective_seeds)
    splits = {s.value for s in cfg.splits}
    reward = f"{cfg.reward_policy}@"
    for r in records:
        md = r.metadata
        mismatches = []
        if r.provider_kind != cfg.provider.kind:
            mismatches.append(f"provider_kind {r.provider_kind} != {cfg.provider.kind}")
        if cfg.provider.kind != "demo" and r.model_id != cfg.provider.model_id:
            mismatches.append(f"model_id {r.model_id} != {cfg.provider.model_id}")
        if cfg.provider.kind == "demo" and md.get("policy") not in (None, cfg.provider.policy):
            mismatches.append(f"policy {md.get('policy')} != {cfg.provider.policy}")
        if md.get("verifier_profile") not in (None, cfg.verifier_profile):
            mismatches.append(f"verifier_profile {md.get('verifier_profile')}")
        rp = md.get("reward_policy")
        if rp is not None and str(rp) != cfg.reward_policy and not str(rp).startswith(reward):
            mismatches.append(f"reward_policy {rp} != {cfg.reward_policy}")
        if md.get("split") is not None and str(md.get("split")) not in splits:
            mismatches.append(f"split {md.get('split')} not in {sorted(splits)}")
        if md.get("seed") not in seeds:
            mismatches.append(f"seed {md.get('seed')} not in {sorted(seeds)}")
        if mismatches:
            raise _LoadRefusal(
                "invalid_bundle",
                f"config {cfg.name!r} did not produce record {r.task_id}: {'; '.join(mismatches)}",
            )


class ComparisonService:
    def __init__(
        self,
        db: Database,
        artifacts: ArtifactWriter,
        registry: TaskRegistry,
        reports_dir: Path,
        catalogue: CatalogueScenarioResolver | None = None,
    ) -> None:
        self.db = db
        self.artifacts = artifacts
        self.registry = registry
        self.reports_dir = reports_dir
        self.catalogue = catalogue

    # ------------------------------------------------------------------ loading

    def load(self, ref: RunRef) -> RunData:
        if ref.source == "bundle":
            return self._load_bundle(ref)
        with self.db.session() as s:
            repo = ExperimentRepository(s)
            try:
                row = repo.get_evaluation(ref.evaluation_id)
            except NotFoundError as exc:
                raise _LoadRefusal("unknown_evaluation", str(exc)) from exc
            raw_config, status, name, config_hash = (
                row.config,
                row.status,
                row.name,
                row.config_hash,
            )
            metrics = dict(row.metrics or {})
            raw = [tr.summary for tr in repo.list_task_runs(ref.evaluation_id)]
        try:
            config = _normalised_config(raw_config)
        except ValidationError as exc:
            raise _LoadRefusal("invalid_record", f"evaluation config is invalid: {exc}") from exc
        leakage = metrics.get("leakage") or {}
        return RunData(
            ref=ref,
            config=config,
            records=_summaries(raw, f"evaluation {ref.evaluation_id}"),
            status=status,
            run_mode=str(metrics.get("run_mode") or config.get("run_mode") or "official"),
            leakage_ok=leakage.get("ok") if isinstance(leakage, dict) else None,
            name=name,
            config_hash=config_hash,
        )

    def _load_bundle(self, ref: RunRef) -> RunData:
        try:
            return self._read_bundle(ref)
        except _LoadRefusal:
            raise
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            # missing/malformed bundle files are refusals, never tracebacks
            raise _LoadRefusal(
                "invalid_bundle", f"{ref.bundle_dir}: {type(exc).__name__}: {exc}"
            ) from exc

    def _read_bundle(self, ref: RunRef) -> RunData:
        bundle = Path(str(ref.bundle_dir))
        if not (bundle / "bundle.json").is_file():
            raise _LoadRefusal("invalid_bundle", f"{bundle}: no bundle.json")
        problems = verify_bundle(bundle)
        if problems:
            raise _LoadRefusal("invalid_bundle", f"{bundle}: {problems}")
        meta = json.loads((bundle / "bundle.json").read_text(encoding="utf-8"))
        if "episodes.jsonl.gz" not in (meta.get("files_sha256") or {}):
            raise _LoadRefusal("invalid_bundle", "episodes file is not covered by bundle hashes")
        if str(ref.evaluation_id) not in {str(x) for x in meta.get("evaluation_ids") or []}:
            raise _LoadRefusal("invalid_bundle", f"evaluation {ref.evaluation_id} not in bundle")
        entry = (meta.get("configs") or {}).get(str(ref.config_name))
        if not isinstance(entry, dict) or "config" not in entry:
            raise _LoadRefusal("invalid_bundle", f"config {ref.config_name!r} not in bundle")
        try:
            cfg = BenchmarkConfig.model_validate(entry["config"])
        except ValidationError as exc:
            raise _LoadRefusal(
                "invalid_bundle", f"config is not a benchmark config: {exc}"
            ) from exc
        config = cfg.model_dump(mode="json")
        correlation = f"bench:{ref.evaluation_id}"
        raw = []
        with gzip.open(bundle / "episodes.jsonl.gz", "rt", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                item = json.loads(line)
                summary = item.get("summary") if isinstance(item, dict) else None
                if not isinstance(summary, dict):
                    continue
                if (summary.get("metadata") or {}).get("correlation_id") == correlation:
                    raw.append(summary)
        records = _summaries(raw, f"bundle {bundle}")
        _check_bundle_consistency(cfg, records)
        return RunData(
            ref=ref,
            config=config,
            records=records,
            status="completed",
            run_mode=str(config.get("run_mode") or "official"),
            leakage_ok=None,  # bundles record no per-run leakage verdict: always diagnostic
            name=cfg.name,
            config_hash=cfg.config_hash,  # same hash as the database path
        )

    # ------------------------------------------------------------------ comparing

    def resolver(self) -> CompositeScenarioResolver:
        return CompositeScenarioResolver(LegacyScenarioResolver(self.registry), self.catalogue)

    def compare(self, request: ComparisonRequest) -> tuple[dict[str, Any], dict[str, str]]:
        """Run a comparison; returns (document, paths). Raises ``ComparisonRefusedError``."""
        try:
            baseline = self.load(request.baseline)
            candidate = self.load(request.candidate)
            core = compare(baseline, candidate, request, self.resolver())
        except _LoadRefusal as exc:
            refusal = {
                "request": request.model_dump(mode="json"),
                "baseline": None,
                "candidate": None,
                "matching": {
                    "status": "refused",
                    "official": False,
                    "coverage_policy": request.coverage_policy,
                    "excluded": [],
                    "config_differences": [],
                    "n_pairs": 0,
                    "n_task_instances": 0,
                    "n_structural_scenarios": 0,
                    "n_families": 0,
                    "problems": [
                        {
                            "code": exc.code,
                            "severity": cc.PROBLEM_CODES[exc.code][0],
                            "detail": exc.detail[:4000],
                            "keys": [],
                            "count": 1,
                        }
                    ],
                },
            }
            self._refuse(refusal)
        except ComparisonRefused as exc:
            self._refuse(exc.document)
        doc = comparison_document(core)
        md = comparison_markdown(doc)
        paths = self._persist(doc["comparison_id"], doc, md)
        return doc, paths

    def _refuse(self, refusal: dict[str, Any]) -> NoReturn:
        doc = refusal_document(refusal)
        rid = str(uuid.uuid4())
        data = dumps_finite(doc, indent=2).encode()
        _, uri = self.artifacts.put(
            f"comparisons/{rid}/comparison_refusal.json",
            data,
            "benchmark_comparison",
            "application/json",
        )
        out = self.reports_dir / "comparisons" / rid
        out.mkdir(parents=True, exist_ok=True)
        (out / "comparison_refusal.json").write_bytes(data)
        raise ComparisonRefusedError(
            doc, {"refusal_json": str(out / "comparison_refusal.json"), "refusal_uri": uri}
        )

    def _persist(self, cid: str, doc: dict[str, Any], md: str) -> dict[str, str]:
        data = dumps_finite(doc, indent=2).encode()
        _, json_uri = self.artifacts.put(
            f"comparisons/{cid}/comparison.json", data, "benchmark_comparison", "application/json"
        )
        _, md_uri = self.artifacts.put(
            f"comparisons/{cid}/comparison.md",
            md.encode(),
            "benchmark_comparison_md",
            "text/markdown",
        )
        out = self.reports_dir / "comparisons" / cid
        out.mkdir(parents=True, exist_ok=True)
        (out / "comparison.json").write_bytes(data)
        (out / "comparison.md").write_text(md, encoding="utf-8")
        return {
            "comparison_json": str(out / "comparison.json"),
            "comparison_md": str(out / "comparison.md"),
            "comparison_uri": json_uri,
            "comparison_md_uri": md_uri,
        }
