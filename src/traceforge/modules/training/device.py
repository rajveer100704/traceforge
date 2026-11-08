"""Explicit training device and precision selection.

``auto`` picks CUDA when available, else CPU. ``cuda`` fails closed (CapabilityUnavailable) when
no CUDA device exists: a run must never silently fall back to CPU and then be reported as a GPU
run. Precision ``auto`` resolves to bf16 on CUDA devices that support it, fp16 on other CUDA
devices and fp32 on CPU. The resolved configuration and the hardware/software environment are
recorded with every training run.

The torch module is injectable so CUDA behaviour can be tested on machines without a GPU.
"""

from __future__ import annotations

import platform
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from traceforge.contracts.common import CapabilityUnavailable

DeviceSetting = Literal["auto", "cpu", "cuda"]
PrecisionSetting = Literal["auto", "fp32", "bf16", "fp16"]
DEVICE_PATTERN = "^(auto|cpu|cuda)$"
PRECISION_PATTERN = "^(auto|fp32|bf16|fp16)$"


@dataclass(frozen=True)
class DeviceInfo:
    requested_device: str
    requested_precision: str
    device: str  # torch device string, e.g. "cpu" or "cuda:0"
    device_type: str  # "cpu" | "cuda"
    precision: str  # "fp32" | "bf16" | "fp16"
    torch_version: str | None
    cuda_available: bool
    cuda_version: str | None
    cudnn_version: int | None
    gpu_count: int
    gpu_names: tuple[str, ...] = field(default_factory=tuple)
    gpu_memory_gb: tuple[float, ...] = field(default_factory=tuple)

    @property
    def is_cuda(self) -> bool:
        return self.device_type == "cuda"

    @property
    def autocast_dtype_name(self) -> str | None:
        return {"bf16": "bfloat16", "fp16": "float16"}.get(self.precision)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["gpu_names"] = list(self.gpu_names)
        d["gpu_memory_gb"] = list(self.gpu_memory_gb)
        return d


def _torch() -> Any:
    try:
        import torch
    except ImportError as exc:
        raise CapabilityUnavailable("training requires torch (uv sync --extra ml)") from exc
    return torch


def resolve_device(
    requested: str = "auto", precision: str = "auto", *, torch_module: Any | None = None
) -> DeviceInfo:
    """Resolve the requested device/precision against the actual hardware (fail closed)."""
    if requested not in ("auto", "cpu", "cuda"):
        raise ValueError(f"device must be auto|cpu|cuda, got {requested!r}")
    if precision not in ("auto", "fp32", "bf16", "fp16"):
        raise ValueError(f"precision must be auto|fp32|bf16|fp16, got {precision!r}")
    torch = torch_module if torch_module is not None else _torch()
    cuda_available = bool(torch.cuda.is_available())
    if requested == "cuda" and not cuda_available:
        raise CapabilityUnavailable(
            "device=cuda requested but torch reports no CUDA device "
            f"(torch {torch.__version__}, built for CUDA {getattr(torch.version, 'cuda', None)}); "
            "refusing to fall back to CPU silently — use device=cpu or device=auto"
        )
    use_cuda = requested == "cuda" or (requested == "auto" and cuda_available)
    count = int(torch.cuda.device_count()) if cuda_available else 0
    names = tuple(str(torch.cuda.get_device_name(i)) for i in range(count))
    memory: tuple[float, ...] = ()
    if cuda_available:
        try:
            memory = tuple(
                round(torch.cuda.get_device_properties(i).total_memory / 1e9, 1)
                for i in range(count)
            )
        except Exception:  # pragma: no cover - driver specific
            memory = ()
    bf16_ok = bool(use_cuda and torch.cuda.is_bf16_supported())
    auto_precision = ("bf16" if bf16_ok else "fp16") if use_cuda else "fp32"
    resolved = auto_precision if precision == "auto" else precision
    if resolved == "fp16" and not use_cuda:
        raise CapabilityUnavailable(
            "precision=fp16 requires a CUDA device; use fp32 or bf16 on CPU"
        )
    if resolved == "bf16" and use_cuda and not bf16_ok:
        raise CapabilityUnavailable(
            "precision=bf16 requested but the CUDA device does not support bfloat16; use fp16"
        )
    cudnn = None
    if cuda_available:
        try:
            cudnn = int(torch.backends.cudnn.version())
        except Exception:  # pragma: no cover
            cudnn = None
    return DeviceInfo(
        requested_device=requested,
        requested_precision=precision,
        device="cuda:0" if use_cuda else "cpu",
        device_type="cuda" if use_cuda else "cpu",
        precision=resolved,
        torch_version=str(torch.__version__),
        cuda_available=cuda_available,
        cuda_version=getattr(torch.version, "cuda", None),
        cudnn_version=cudnn,
        gpu_count=count,
        gpu_names=names,
        gpu_memory_gb=memory,
    )


def host_environment() -> dict[str, Any]:
    """Software/hardware facts recorded alongside training and evaluation runs."""
    import importlib.metadata as md
    import os

    versions: dict[str, str | None] = {}
    for pkg in ("torch", "transformers", "trl", "peft", "accelerate", "datasets", "traceforge"):
        try:
            versions[pkg] = md.version(pkg)
        except md.PackageNotFoundError:
            versions[pkg] = None
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
        "cpu_count": os.cpu_count(),
        "packages": versions,
    }


def autocast_context(info: DeviceInfo, torch_module: Any | None = None) -> Any:
    """Autocast for bf16/fp16; a no-op context for fp32."""
    import contextlib

    if info.autocast_dtype_name is None:
        return contextlib.nullcontext()
    torch = torch_module if torch_module is not None else _torch()
    return torch.autocast(
        device_type=info.device_type, dtype=getattr(torch, info.autocast_dtype_name)
    )


def grpo_device_arguments(info: DeviceInfo) -> dict[str, Any]:
    """TrainingArguments device/precision flags derived from the resolved device (no defaults
    hidden in the trainer: CPU runs say ``use_cpu=True`` explicitly, CUDA runs never do)."""
    return {
        "use_cpu": not info.is_cuda,
        "bf16": info.precision == "bf16",
        "fp16": info.precision == "fp16",
    }
