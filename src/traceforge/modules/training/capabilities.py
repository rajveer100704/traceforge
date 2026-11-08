"""Capability detection for optional training integrations (no heavy imports at module load)."""

from __future__ import annotations

import importlib.metadata as md
import importlib.util
import inspect
import shutil
from dataclasses import dataclass, field
from typing import Any

GRPO_MIN_TRANSFORMERS = (5, 2, 0)  # TRL docs: environment_factory requires transformers>=5.2.0


def _version(pkg: str) -> str | None:
    try:
        return md.version(pkg)
    except md.PackageNotFoundError:
        return None


def _vt(v: str | None) -> tuple[int, ...]:
    if not v:
        return ()
    out = []
    for part in v.split(".")[:3]:
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


@dataclass
class ModeStatus:
    available: bool
    reason: str


@dataclass
class TrainingCapabilities:
    versions: dict[str, str | None] = field(default_factory=dict)
    cuda: bool = False
    cuda_devices: list[str] = field(default_factory=list)
    free_disk_gb: float | None = None
    modes: dict[str, ModeStatus] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "versions": self.versions,
            "cuda": self.cuda,
            "cuda_devices": self.cuda_devices,
            "free_disk_gb": self.free_disk_gb,
            "modes": {
                k: {"available": v.available, "reason": v.reason} for k, v in self.modes.items()
            },
        }


def grpo_environment_support() -> tuple[bool, str]:
    trl_v, tf_v = _version("trl"), _version("transformers")
    if trl_v is None or tf_v is None:
        return False, "trl/transformers not installed (uv sync --extra ml)"
    if _vt(tf_v) < GRPO_MIN_TRANSFORMERS:
        return False, (
            f"transformers {tf_v} < 5.2.0 required by TRL environment_factory; "
            "upgrade the ml extra or run GRPO in a dedicated environment"
        )
    try:
        from trl import GRPOTrainer
    except Exception as exc:  # pragma: no cover - environment specific
        return False, f"cannot import trl.GRPOTrainer: {exc}"
    params = inspect.signature(GRPOTrainer.__init__).parameters
    if "environment_factory" not in params:
        return False, f"installed trl {trl_v} GRPOTrainer has no environment_factory parameter"
    return True, f"trl {trl_v}, transformers {tf_v} support environment_factory"


def detect(path_for_disk: str = ".") -> TrainingCapabilities:
    caps = TrainingCapabilities()
    for pkg in ("torch", "transformers", "trl", "peft", "datasets", "accelerate", "mlflow-skinny"):
        caps.versions[pkg] = _version(pkg)
    try:
        caps.free_disk_gb = round(shutil.disk_usage(path_for_disk).free / 1e9, 1)
    except OSError:
        caps.free_disk_gb = None
    if importlib.util.find_spec("torch") is not None:
        try:
            import torch

            caps.cuda = bool(torch.cuda.is_available())
            caps.cuda_devices = [
                torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())
            ]
        except Exception:  # pragma: no cover
            caps.cuda = False
    caps.modes["demo"] = ModeStatus(True, "deterministic scripted provider; no GPU/API required")
    ml_core = all(caps.versions.get(p) for p in ("torch", "transformers"))
    caps.modes["cpu-test"] = ModeStatus(
        ml_core,
        "tiny locally-initialised model (no download)"
        if ml_core
        else "torch/transformers not installed",
    )
    sft_ready = ml_core and bool(caps.versions.get("peft"))
    if not sft_ready:
        caps.modes["sft"] = ModeStatus(
            False, "requires torch, transformers, peft (uv sync --extra ml)"
        )
    elif not caps.cuda:
        caps.modes["sft"] = ModeStatus(
            True, "available on CPU only (no CUDA); practical only for tiny models"
        )
    else:
        caps.modes["sft"] = ModeStatus(True, f"CUDA devices: {caps.cuda_devices}")
    ok, why = grpo_environment_support()
    if ok and not caps.cuda:
        why += "; no CUDA device — real GRPO runs are impractical on this machine"
    caps.modes["grpo"] = ModeStatus(ok, why)
    return caps
