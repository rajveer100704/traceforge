"""Training orchestration: resolve dataset, run SFT/GRPO, persist runs + checkpoints.

Unavailable capabilities are recorded as a BLOCKED training run and surfaced as an error;
they are never reported as completed training.
"""

from __future__ import annotations

import json
import logging
import tempfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from traceforge.contracts.common import (
    CapabilityUnavailable,
    JSONValue,
    NotFoundError,
    ValidationFailure,
)
from traceforge.infrastructure.database.repositories import DatasetRepository, ExperimentRepository
from traceforge.infrastructure.observability import METRICS

if TYPE_CHECKING:
    from traceforge.application.container import Platform

log = logging.getLogger(__name__)


def _mlflow_log(
    run_name: str, params: dict[str, Any], metrics: dict[str, float], tracking_dir: Path
) -> str:
    """Log to MLflow when installed (local file store). Returns a status string."""
    try:
        import mlflow
    except ImportError:
        return "unavailable: mlflow not installed"
    try:
        tracking_dir.mkdir(parents=True, exist_ok=True)
        mlflow.set_tracking_uri(f"sqlite:///{(tracking_dir / 'mlflow.db').resolve().as_posix()}")
        mlflow.set_experiment("traceforge")
        with mlflow.start_run(run_name=run_name):
            mlflow.log_params({k: str(v)[:250] for k, v in params.items()})
            for k, v in metrics.items():
                mlflow.log_metric(k, float(v))
        return "logged"
    except Exception as exc:  # tracking must never break training bookkeeping
        return f"failed: {type(exc).__name__}: {str(exc)[:200]}"


class TrainingService:
    def __init__(self, platform: Platform) -> None:
        self.p = platform

    def _dataset_rows(
        self, dataset_version_id: str | None, config: dict[str, Any]
    ) -> tuple[list[Any], list[Any], str | None]:
        from traceforge.modules.trace_processing.dataset_builder import read_partition

        if dataset_version_id:
            with self.p.db.session() as s:
                row = DatasetRepository(s).get(uuid.UUID(dataset_version_id))
                manifest = row.manifest
            prefix = f"datasets/{manifest['dataset_id']}/{manifest['version']}"
            parts = {
                f["split"]: read_partition(self.p.store.get_bytes(f"{prefix}/{f['name']}"))
                for f in manifest["partitions"]
            }
            return parts.get("train", []), parts.get("validation", []), dataset_version_id
        if config.get("dataset_dir"):
            from traceforge.modules.training.sft.trainer import load_rows

            d = Path(config["dataset_dir"])
            return load_rows(d / "train.parquet"), load_rows(d / "validation.parquet"), None
        raise NotFoundError("training requires dataset_version_id or config.dataset_dir")

    def _record(self, **kw: Any) -> uuid.UUID:
        with self.p.db.session() as s:
            return ExperimentRepository(s).create_training_run(**kw)

    def run(self, payload: dict[str, Any]) -> dict[str, JSONValue]:
        mode = payload["mode"]
        config: dict[str, Any] = dict(payload.get("config") or {})
        experiment_id = (
            uuid.UUID(payload["experiment_id"]) if payload.get("experiment_id") else None
        )
        try:
            if mode in ("cpu-test", "sft"):
                return self._sft(mode, config, payload.get("dataset_version_id"), experiment_id)
            if mode == "grpo":
                return self._grpo(config, experiment_id)
            raise CapabilityUnavailable(f"unknown training mode {mode}")
        except (CapabilityUnavailable, ValidationFailure) as exc:
            self._record(
                mode=mode,
                status="blocked",
                base_model=str(config.get("base_model", "n/a")),
                config=config,
                experiment_id=experiment_id,
                blocked_reason=exc.message,
            )
            raise

    def _sft(
        self,
        mode: str,
        config: dict[str, Any],
        dataset_version_id: str | None,
        experiment_id: uuid.UUID | None,
    ) -> dict[str, JSONValue]:
        from traceforge.modules.training.sft.trainer import SFTConfig, require_ml, run_sft

        require_ml()
        cfg = SFTConfig.model_validate({**config, "mode": mode})
        train_rows, val_rows, dv = self._dataset_rows(dataset_version_id, config)
        from traceforge.modules.evaluation.contamination import check_training_rows

        leaks = check_training_rows([*train_rows, *val_rows], self.p.registry)
        if leaks:
            raise ValidationFailure(
                f"training data rejected by leakage guard ({len(leaks)} problem(s)): "
                + "; ".join(leaks[:10])
            )
        training_task_ids = sorted(
            {str(r.get("task_id")) for r in [*train_rows, *val_rows] if r.get("task_id")}
        )
        with tempfile.TemporaryDirectory() as tmp:
            cfg = cfg.model_copy(update={"output_dir": str(Path(tmp) / "out")})
            meta = run_sft(cfg, train_rows, val_rows)
            ckpt = Path(str(meta["checkpoint"]))
            run_id = uuid.uuid4()
            key_prefix = f"checkpoints/{run_id}/step-{meta['step']}"
            _, ckpt_uri = self.p.artifacts.put(
                f"{key_prefix}/trainer_state.pt", ckpt.read_bytes(), "checkpoint"
            )
        meta["checkpoint"] = ckpt_uri
        meta_bytes = json.dumps(meta, indent=2, default=str).encode()
        self.p.artifacts.put(
            f"{key_prefix}/metadata.json", meta_bytes, "checkpoint_metadata", "application/json"
        )
        tracking = _mlflow_log(
            f"{mode}-{run_id}",
            cfg.model_dump(mode="json"),
            {
                k: v
                for k, v in (
                    ("first_loss", meta["first_loss"]),
                    ("last_loss", meta["last_loss"]),
                    ("validation_loss", meta["validation_loss"]),
                )
                if v is not None
            },
            self.p.settings.data_dir / "mlruns",
        )
        metrics = {
            k: meta[k]
            for k in (
                "first_loss",
                "last_loss",
                "validation_loss",
                "step",
                "train_examples",
                "validation_examples",
                "seconds",
            )
        }
        metrics["experiment_tracking"] = tracking
        metrics["training_task_ids"] = training_task_ids
        metrics["device"] = meta["device"]
        metrics["environment"] = meta["environment"]
        with self.p.db.session() as s:
            repo = ExperimentRepository(s)
            tr = repo.create_training_run(
                mode=mode,
                status="completed",
                base_model=cfg.base_model,
                base_model_revision=cfg.base_model_revision,
                config=cfg.model_dump(mode="json"),
                experiment_id=experiment_id,
                metrics=metrics,
                dataset_version_id=uuid.UUID(dv) if dv else None,
            )
            repo.add_checkpoint(
                tr,
                int(meta["step"]),
                ckpt_uri,
                str(meta["checkpoint_sha256"]),
                {"config_hash": cfg.config_hash},
            )
        METRICS.training_steps.labels(mode=mode).inc(int(meta["step"]))
        return {
            "training_run_id": str(tr),
            "status": "completed",
            "checkpoint_uri": ckpt_uri,
            **metrics,
        }

    def _grpo(
        self, config: dict[str, Any], experiment_id: uuid.UUID | None
    ) -> dict[str, JSONValue]:
        from traceforge.modules.training.grpo.adapter import (
            EnvironmentDeps,
            check_grpo_compatibility,
        )
        from traceforge.modules.training.grpo.trainer import GRPOTrainingConfig, run_grpo

        check_grpo_compatibility()
        if "base_model" not in config:
            raise CapabilityUnavailable(
                "GRPO requires config.base_model (a local or downloadable chat model)"
            )
        cfg = GRPOTrainingConfig.model_validate(config)
        deps = EnvironmentDeps(
            registry=self.p.registry,
            sandbox=self.p.sandbox,
            verifier=self.p.verifier,
            policies=self.p.policies,
            reward_policy=cfg.reward_policy,
            allow_untrusted_local_execution=self.p.settings.allow_untrusted_local_execution,
            model_id=cfg.base_model,
        )
        meta = run_grpo(cfg, deps)
        rewards = [r["reward"] for r in meta["rollouts"]]
        metrics = {
            "steps": meta["steps"],
            "rollouts": len(rewards),
            "mean_rollout_reward": sum(rewards) / len(rewards) if rewards else None,
            "seconds": meta["seconds"],
            "training_task_ids": meta["prompt_task_ids"],
            "device": meta["device"],
            "environment": meta["environment"],
        }
        tr = self._record(
            mode="grpo",
            status="completed",
            base_model=cfg.base_model,
            config=cfg.model_dump(mode="json"),
            experiment_id=experiment_id,
            metrics=metrics,
            base_model_revision=cfg.base_model_revision,
        )
        METRICS.training_steps.labels(mode="grpo").inc(int(meta["steps"]))
        return {"training_run_id": str(tr), "status": "completed", **metrics}
