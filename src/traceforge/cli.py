"""TraceForge command-line interface. Non-zero exit codes on failure."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import typer
import yaml

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")

app = typer.Typer(
    help="TraceForge — verifiable agent training & evaluation infrastructure.", no_args_is_help=True
)
db_app = typer.Typer(help="Database operations.")
traces_app = typer.Typer(help="Trace ingestion and validation.")
datasets_app = typer.Typer(help="Dataset construction.")
tasks_app = typer.Typer(help="Task registry.")
rollout_app = typer.Typer(help="Agent rollouts.")
reward_app = typer.Typer(help="Reward computation.")
benchmark_app = typer.Typer(help="Benchmarks.")
train_app = typer.Typer(help="Training (optional ml extra).")
experiment_app = typer.Typer(help="Research experiments A–H.")
for name, sub in [
    ("db", db_app),
    ("traces", traces_app),
    ("datasets", datasets_app),
    ("tasks", tasks_app),
    ("rollout", rollout_app),
    ("reward", reward_app),
    ("benchmark", benchmark_app),
    ("train", train_app),
    ("experiment", experiment_app),
]:
    app.add_typer(sub, name=name)


def _out(data: Any) -> None:
    typer.echo(json.dumps(data, indent=2, default=str))


def _fail(message: str, code: int = 1) -> None:
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code)


def _settings() -> Any:
    from traceforge.config import Settings

    return Settings()


def _platform(migrate: bool = True) -> Any:
    from traceforge.application.container import Platform
    from traceforge.infrastructure.observability import configure_logging

    settings = _settings()
    configure_logging(
        settings.log_level if settings.log_level != "INFO" else "WARNING", json_output=True
    )
    return Platform.build(settings, migrate=migrate)


def _yaml(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    if not path.exists():
        _fail(f"config file not found: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _guard(fn: Any) -> Any:
    from traceforge.contracts.common import TraceForgeError

    try:
        return fn()
    except TraceForgeError as exc:
        _fail(
            f"{exc.code.value}: {exc.message}",
            2 if exc.code.value == "capability_unavailable" else 1,
        )


# ----------------------------------------------------------------------------- system


@app.command()
def doctor() -> None:
    """Report environment capabilities (sandboxes, providers, training modes)."""
    from traceforge.application.system import capabilities

    settings = _settings()
    caps = capabilities(settings)
    from traceforge.infrastructure.database.session import Database

    db = Database(settings.resolved_database_url)
    caps["database_reachable"] = db.ping()
    caps["database_migrations_current"] = caps["database_reachable"] and db.is_up_to_date()
    _out(caps)


@db_app.command("migrate")
def db_migrate() -> None:
    """Apply database migrations (alembic upgrade head)."""
    from traceforge.infrastructure.database.session import Database

    db = Database(_settings().resolved_database_url)
    db.migrate()
    _out({"current": db.current_revision(), "head": db.head_revision()})


@db_app.command("check")
def db_check() -> None:
    """Fail if the ORM metadata and migrations have drifted."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from traceforge.infrastructure.database.models import Base
    from traceforge.infrastructure.database.session import Database

    db = Database("sqlite:///:memory:")
    db.migrate()
    with db.engine.connect() as conn:
        diff = compare_metadata(
            MigrationContext.configure(conn, opts={"compare_type": False}), Base.metadata
        )
    if diff:
        _fail(f"schema drift between models and migrations: {diff}")
    _out({"drift": [], "head": db.head_revision()})


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the FastAPI control plane."""
    import uvicorn

    uvicorn.run("traceforge.api.app:app_factory", factory=True, host=host, port=port)


@app.command()
def worker(burst: bool = typer.Option(False, help="Exit when the queue is empty.")) -> None:
    """Run an RQ worker (requires TRACEFORGE_QUEUE_BACKEND=rq and Redis)."""
    from traceforge.workers.main import run_worker

    run_worker(burst=burst)


# ----------------------------------------------------------------------------- traces & datasets


@traces_app.command("ingest")
def traces_ingest(path: Path) -> None:
    """Ingest a JSONL trace file (raw bytes persisted before normalisation)."""
    p = _platform()
    summary = _guard(lambda: p.ingestion.ingest_bytes(path.name, path.read_bytes()))
    _out(
        {
            "source": summary.source_uri,
            "raw_artifact": summary.raw_artifact_uri,
            "stored": len(summary.stored_trajectory_ids),
            "quarantined": summary.quarantined,
            "report": summary.report.model_dump(mode="json"),
        }
    )


@traces_app.command("validate")
def traces_validate(path: Path) -> None:
    """Validate a JSONL trace file without storing anything."""
    from traceforge.modules.trace_processing.ingestion import IngestionPipeline, split_jsonl
    from traceforge.modules.trace_processing.quality import QualityEngine

    result = IngestionPipeline(QualityEngine()).run(path.name, split_jsonl(path.read_bytes()))
    _out(
        {
            "report": result.report.model_dump(mode="json"),
            "quarantined": [q.model_dump(mode="json") for q, _ in result.quarantined],
        }
    )


@datasets_app.command("build")
def datasets_build(
    config: Path = typer.Option(None, help="YAML with name/level/kind/source_prefix"),
    name: str = typer.Option("curated", help="Dataset name"),
    level: str = typer.Option(
        "d3_verified_success",
        help="d1_raw|d2_schema_valid|d3_verified_success|d4_verified_plus_informative",
    ),
) -> None:
    """Build an immutable Parquet dataset + manifest from stored trajectories."""
    from traceforge.contracts.datasets import CurationLevel, DatasetKind

    cfg = {"name": name, "level": level, **_yaml(config)}
    p = _platform()
    out = _guard(
        lambda: p.datasets.build(
            cfg["name"],
            level=CurationLevel(cfg["level"]),
            kind=DatasetKind(cfg.get("kind", "sft")),
            source_prefix=cfg.get("source_prefix"),
        )
    )
    _out(
        {
            "dataset_version_id": out["dataset_version_id"],
            "version": out["manifest"]["version"],
            "partitions": out["manifest"]["partitions"],
            "quality": out["quality"],
        }
    )


# ----------------------------------------------------------------------------- tasks / rollouts / rewards


@tasks_app.command("list")
def tasks_list(split: str = typer.Option(None), family: str = typer.Option(None)) -> None:
    """List registered task versions (public metadata only)."""
    from traceforge.modules.tasks.registry import default_registry

    rows = [
        {
            "task_id": b.spec.task_id,
            "version": b.spec.version,
            "family": b.oracle.family_id,
            "template": b.oracle.template_id,
            "split": b.oracle.split.value,
            "difficulty": b.spec.difficulty.value,
        }
        for b in default_registry().list()
        if (split is None or b.oracle.split.value == split)
        and (family is None or b.oracle.family_id == family)
    ]
    _out({"count": len(rows), "tasks": rows})


@tasks_app.command("validate")
def tasks_validate() -> None:
    """Validate task structure and split integrity."""
    from traceforge.modules.evaluation.contamination import check_task_splits
    from traceforge.modules.tasks.registry import default_registry, validate_registry

    reg = default_registry()
    problems = validate_registry(reg)
    leakage = check_task_splits(reg.list())
    _out(
        {
            "tasks": len(reg),
            "problems": problems,
            "leakage_ok": leakage.ok,
            "leakage": leakage.problems,
            "counts_by_split": leakage.counts_by_split,
        }
    )
    if problems or not leakage.ok:
        raise typer.Exit(1)


@rollout_app.command("run")
def rollout_run(
    task: str = typer.Option(..., help="Task id"),
    provider: str = typer.Option("demo", help="demo | openai_compatible"),
    policy: str = typer.Option("reference", help="Demo policy name"),
    model: str = typer.Option("demo_provider", help="Model id for openai_compatible"),
    base_url: str = typer.Option(None, help="Base URL for openai_compatible"),
    api_key_env: str = typer.Option(None, help="Env var holding the provider API key"),
    reward_policy: str = typer.Option("verified-v1"),
) -> None:
    """Run one rollout end-to-end (episode -> verification -> reward) and persist it."""
    from traceforge.contracts.experiments import ProviderConfig, RolloutRequest

    p = _platform()
    req = RolloutRequest(
        task_id=task,
        provider=ProviderConfig(
            kind=provider, policy=policy, model_id=model, base_url=base_url, api_key_env=api_key_env
        ),
        reward_policy=reward_policy,
    )
    s = _guard(lambda: asyncio.run(p.rollouts.run(req)))
    _out(
        {
            "rollout_id": s.rollout_id,
            "task_id": s.task_id,
            "verdict": s.verification.verdict.value,
            "reward": s.reward.final_reward,
            "tool_calls": s.tool_calls,
            "rejected_actions": s.rejected_actions,
            "termination": s.termination_reason,
            "failure_reasons": s.verification.failure_reasons,
            "verification_id": s.verification.verification_id,
            "reward_id": s.reward.reward_id,
            "checks": [(c.name, c.status.value, c.reason_code) for c in s.verification.checks],
        }
    )


@reward_app.command("recompute")
def reward_recompute(
    verification_id: str,
    policy: str = typer.Option("verified-v1"),
    policy_version: str = typer.Option(None),
) -> None:
    """Recompute a reward from an immutable verification record and compare with stored records."""
    p = _platform()
    out = _guard(lambda: p.rewards.recompute(uuid.UUID(verification_id), policy, policy_version))
    _out(out)
    if out["matches_stored"] is False:
        raise typer.Exit(1)


# ----------------------------------------------------------------------------- benchmark / report


@benchmark_app.command("run")
def benchmark_run(config: Path = typer.Option(None, help="Benchmark YAML")) -> None:
    """Run a benchmark and write JSON + Markdown reports."""
    from traceforge.contracts.experiments import BenchmarkConfig

    cfg = BenchmarkConfig.model_validate(_yaml(config))
    p = _platform()
    result, paths = _guard(lambda: asyncio.run(p.evaluations.run(cfg)))
    o = result.metrics["overall"]
    _out(
        {
            "evaluation_id": result.benchmark_id,
            "n": o["n"],
            "verified_success": o["verified_success"],
            "false_positive_reward_rate": o["false_positive_reward_rate"],
            **paths,
        }
    )


@app.command()
def report(run_id: str) -> None:
    """Print the Markdown report for an evaluation run id."""
    settings = _settings()
    path = settings.reports_dir / run_id / "report.md"
    if not path.exists():
        _fail(f"no report for {run_id} under {settings.reports_dir}")
    typer.echo(path.read_text(encoding="utf-8"))


# ----------------------------------------------------------------------------- training


@train_app.command("sft")
def train_sft(
    config: Path = typer.Option(None), dataset_version_id: str = typer.Option(None)
) -> None:
    """Run SFT (mode cpu-test or sft from config). Blocked capabilities exit with code 2."""
    from traceforge.application.training import TrainingService

    cfg = _yaml(config)
    mode = cfg.pop("mode", "cpu-test")
    p = _platform()
    _out(
        _guard(
            lambda: TrainingService(p).run(
                {
                    "mode": mode,
                    "config": cfg,
                    "dataset_version_id": dataset_version_id or cfg.pop("dataset_version_id", None),
                }
            )
        )
    )


@train_app.command("grpo")
def train_grpo(config: Path = typer.Option(None)) -> None:
    """Run online GRPO via TRL (requires compatible versions, a chat model and the docker sandbox)."""
    from traceforge.application.training import TrainingService

    cfg = _yaml(config)
    cfg.pop("mode", None)
    p = _platform()
    _out(_guard(lambda: TrainingService(p).run({"mode": "grpo", "config": cfg})))


# ----------------------------------------------------------------------------- experiments & demo


@experiment_app.command("run")
def experiment_run(
    name: str = typer.Argument(..., help="A|B|C|D|E|F|G|H"),
    out_dir: Path = typer.Option(None, help="Output directory"),
    seeds: str = typer.Option("0,1,2", help="Comma-separated seeds (repeated-seed execution)"),
    config: Path = typer.Option(
        None, help="Experiment YAML (F, G); default configs/experiment_<X>.yaml"
    ),
) -> None:
    """Run a research experiment; writes JSON + Markdown results and a reproducibility bundle."""
    from traceforge.application.experiments import run_experiment

    seed_tuple = tuple(int(x) for x in seeds.split(",") if x.strip())
    p = _platform()
    out = _guard(
        lambda: run_experiment(p, name.upper(), out_dir, seeds=seed_tuple, config_path=config)
    )
    _out(out)


@app.command()
def demo(profile: str = typer.Option("local", help="local (no GPU/API/Docker required)")) -> None:
    """Run the full lifecycle smoke test (non-zero exit if any acceptance assertion fails)."""
    from traceforge.application.demo import run_demo

    p = _platform()
    result = _guard(lambda: asyncio.run(run_demo(p, profile)))
    _out(result)
    if not result["passed"]:
        typer.secho("demo FAILED: " + "; ".join(result["failures"]), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    typer.secho("demo PASSED", fg=typer.colors.GREEN, err=True)


def main() -> None:  # pragma: no cover
    sys.exit(app())


if __name__ == "__main__":  # pragma: no cover
    main()
