"""Self-contained reproducibility bundles for official experiments.

Every bundle directory contains:

* ``bundle.json``      - provenance: git commit (+ dirty flag), versions of the task generator,
  split policies, verifier (with a hash of its effective configuration), reward policies (with
  their content hashes), attack catalogue, config hashes, seeds, hardware/software environment,
  dataset manifests and hashes, model/tokenizer revisions, checkpoint checksums, and a SHA-256 for
  every other file in the bundle;
* ``metrics.json``     - the experiment document (aggregate metrics and confidence intervals);
* ``episodes.jsonl.gz``- raw per-episode records (rollout summary, host-recorded episode facts,
  trajectory), when the experiment executed rollouts;
* ``REPRODUCE.md``     - the exact commands to regenerate the bundle and how to verify it.

Values are copied from what was executed; absent items are recorded as ``null`` with a reason,
never filled in.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from traceforge import __version__
from traceforge.contracts.common import sha256_json

if TYPE_CHECKING:
    from traceforge.application.container import Platform

BUNDLE_FORMAT_VERSION = "1"


def git_state(cwd: Path | None = None) -> dict[str, Any]:
    git = shutil.which("git")
    if git is None:
        return {"commit": None, "dirty": None, "reason": "git executable not found"}
    cwd = cwd or Path(__file__).resolve().parents[3]

    def run(*args: str) -> str | None:
        try:
            out = subprocess.run(  # nosec B603 - fixed argv, absolute git path, no shell
                [git, *args], cwd=cwd, capture_output=True, text=True, timeout=20, check=True
            )
        except (subprocess.SubprocessError, OSError):
            return None
        return out.stdout.strip()

    commit = run("rev-parse", "HEAD")
    if commit is None:
        return {"commit": None, "dirty": None, "reason": "not a git checkout"}
    status = run("status", "--porcelain", "--untracked-files=no")
    return {
        "commit": commit,
        "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(status),
        "dirty_files": status.splitlines()[:50] if status else [],
    }


def verifier_provenance() -> dict[str, Any]:
    from traceforge.modules.tasks.registry import HIDDEN_TEST_MODULE, VERIFIER_ID, VERIFIER_VERSION
    from traceforge.modules.verification import safety

    effective = {
        "verifier_id": VERIFIER_ID,
        "verifier_version": VERIFIER_VERSION,
        "hidden_test_module": HIDDEN_TEST_MODULE,
        "static_safety": {
            "forbidden_calls": sorted(safety.FORBIDDEN_CALLS),
            "forbidden_names": sorted(safety.FORBIDDEN_NAMES),
            "allowed_dunder_attrs": sorted(safety.ALLOWED_DUNDER_ATTRS),
            "always_allowed_imports": sorted(safety.ALWAYS_ALLOWED_IMPORTS),
        },
    }
    return {**effective, "config_hash": sha256_json(effective)}


def platform_provenance(p: Platform) -> dict[str, Any]:
    from traceforge.modules.evaluation.attack_catalog import CATALOG_VERSION
    from traceforge.modules.evaluation.contamination import LEAKAGE_CHECKS_VERSION
    from traceforge.modules.tasks.families.base import GENERATOR_VERSION
    from traceforge.modules.tasks.registry import SPLIT_POLICY, SPLIT_POLICY_VERSION, TASK_VERSION
    from traceforge.modules.trace_processing import dataset_builder as db

    policies = {}
    for pid in ("verified-v1", "self-report-r1", "visible-tests-r2"):
        pol = p.policies.get(pid)
        policies[pid] = {"version": pol.version, "policy_hash": pol.policy_hash}
    tasks = p.registry.list()
    return {
        "traceforge_version": __version__,
        "task_generator_version": GENERATOR_VERSION,
        "task_version": TASK_VERSION,
        "task_split_policy": {"id": SPLIT_POLICY, "version": SPLIT_POLICY_VERSION},
        "dataset_split_policy": {"id": db.SPLIT_POLICY, "version": db.SPLIT_POLICY_VERSION},
        "dataset_transformation": {
            "id": db.TRANSFORMATION_ID,
            "version": db.TRANSFORMATION_VERSION,
        },
        "task_catalogue_hash": sha256_json(
            sorted((b.spec.task_id, b.spec.version, b.oracle.fixture_hash) for b in tasks)
        ),
        "n_tasks": len(tasks),
        "verifier": verifier_provenance(),
        "reward_policies": policies,
        "attack_catalog_version": CATALOG_VERSION,
        "leakage_checks_version": LEAKAGE_CHECKS_VERSION,
    }


def environment() -> dict[str, Any]:
    from traceforge.contracts.common import CapabilityUnavailable
    from traceforge.modules.training.device import host_environment, resolve_device

    env = host_environment()
    try:
        env["device"] = resolve_device("auto", "auto").as_dict()
    except CapabilityUnavailable as exc:  # torch not installed
        env["device"] = {"available": False, "reason": exc.message}
    return env


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_bundle(
    p: Platform,
    out_dir: Path,
    *,
    experiment: str,
    document: dict[str, Any],
    configs: dict[str, Any] | None = None,
    seeds: list[int] | None = None,
    episodes: list[dict[str, Any]] | None = None,
    episodes_reason: str | None = None,
    datasets: list[dict[str, Any]] | None = None,
    models: list[dict[str, Any]] | None = None,
    checkpoints: list[dict[str, Any]] | None = None,
    evaluation_ids: list[str] | None = None,
    commands: list[str] | None = None,
) -> dict[str, Any]:
    bundle = out_dir / "bundle"
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "metrics.json").write_text(
        json.dumps(document, indent=2, default=str, sort_keys=True), encoding="utf-8", newline="\n"
    )
    if episodes:
        raw = "".join(json.dumps(e, default=str, sort_keys=True) + "\n" for e in episodes)
        # mtime=0 keeps the archive byte-stable for identical content
        with gzip.GzipFile(bundle / "episodes.jsonl.gz", "wb", mtime=0) as gz:
            gz.write(raw.encode("utf-8"))
    commands = commands or [f"uv run traceforge experiment run {experiment}"]
    git = git_state()
    (bundle / "REPRODUCE.md").write_text(
        _reproduce_md(experiment, git, commands, bool(episodes), episodes_reason),
        encoding="utf-8",
        newline="\n",
    )
    config_entries = {
        name: {"config_hash": sha256_json(cfg), "config": cfg}
        for name, cfg in (configs or {}).items()
    }
    files = {
        f.relative_to(bundle).as_posix(): _sha256_file(f)
        for f in sorted(bundle.rglob("*"))
        if f.is_file() and f.name != "bundle.json"
    }
    meta = {
        "bundle_format_version": BUNDLE_FORMAT_VERSION,
        "experiment": experiment,
        "created_at": datetime.now(UTC).isoformat(),
        "git": git,
        "provenance": platform_provenance(p),
        "configs": config_entries,
        "seeds": seeds or [],
        "environment": environment(),
        "datasets": datasets or [],
        "models": models
        or [{"model_id": None, "reason": "no learned model was trained or evaluated"}],
        "checkpoints": checkpoints or [],
        "evaluation_ids": evaluation_ids or [],
        "episodes": {
            "file": "episodes.jsonl.gz" if episodes else None,
            "count": len(episodes or []),
            "reason": None if episodes else episodes_reason,
        },
        "files_sha256": files,
    }
    (bundle / "bundle.json").write_text(
        json.dumps(meta, indent=2, default=str, sort_keys=True), encoding="utf-8", newline="\n"
    )
    return {"dir": str(bundle), "files": sorted([*files, "bundle.json"]), "git": git}


def verify_bundle(bundle_dir: Path) -> list[str]:
    """Return a list of integrity problems (empty when every file hash matches)."""
    meta = json.loads((bundle_dir / "bundle.json").read_text(encoding="utf-8"))
    problems = []
    for rel, digest in meta["files_sha256"].items():
        f = bundle_dir / rel
        if not f.is_file():
            problems.append(f"missing {rel}")
        elif _sha256_file(f) != digest:
            problems.append(f"hash mismatch {rel}")
    return problems


def _reproduce_md(
    experiment: str,
    git: dict[str, Any],
    commands: list[str],
    has_episodes: bool,
    episodes_reason: str | None,
) -> str:
    commit = git.get("commit") or "<unknown>"
    lines = [
        f"# Reproduce experiment {experiment}",
        "",
        f"- Source commit: `{commit}`" + (" (working tree was DIRTY)" if git.get("dirty") else ""),
        "- Environment, versions, config hashes and seeds: `bundle.json`",
        "- Aggregate metrics and confidence intervals: `metrics.json`",
        "- Raw episodes: "
        + (
            "`episodes.jsonl.gz` (one JSON object per episode)"
            if has_episodes
            else f"none ({episodes_reason})"
        ),
        "",
        "## Commands",
        "",
        "```bash",
        f"git checkout {commit}",
        "uv sync --frozen --extra ml",
        *commands,
        "```",
        "",
        "## Verify this bundle",
        "",
        "```bash",
        'uv run python -c "from pathlib import Path; from traceforge.application.reproducibility '
        "import verify_bundle; print(verify_bundle(Path('.')) or 'OK')\"",
        "```",
        "",
        "Scripted-provider experiments are deterministic: rerunning at the same commit reproduces the",
        "same verdicts and rewards (identifiers and timings differ). Model-provider and training",
        "results additionally depend on the recorded model revision, hardware and library versions.",
        "",
    ]
    return "\n".join(lines)
