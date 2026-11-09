# ADR-012: Explicit training device selection that fails closed

**Status:** accepted (v0.2)

**Decision.** Training configs take `device: auto|cpu|cuda` and `precision: auto|fp32|bf16|fp16`,
resolved once by `modules/training/device.py` against the actual hardware. `cuda` without a CUDA
device raises `CapabilityUnavailable` instead of falling back to CPU. GRPO no longer hard-codes
`use_cpu=True`; its TrainingArguments device flags are derived from the resolved device. The
resolved device, GPU, CUDA/cuDNN, PyTorch version and precision are recorded with each run.
`cpu-test` defaults to CPU. New config fields are excluded from config hashes when left at their
defaults, so v0.1 config hashes are unchanged.

**Consequences.** A run labelled as a GPU run really ran on a GPU; CPU-only machines get an
explicit error. CUDA code paths are covered by tests with an injected fake torch module; a real
CUDA test runs only where a GPU exists (skipped in this development environment).
