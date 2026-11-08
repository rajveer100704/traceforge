"""Supervised fine-tuning entry point (optional ``ml`` extra).

* Deterministic dataset adapter: Parquet rows -> chat messages -> token ids + labels.
* Loss masking: only assistant turns are targets (labels = -100 elsewhere), computed with a
  template-agnostic prefix-difference method over the tokenizer's chat template (or a plain
  fallback template).
* ``cpu-test`` mode builds a tiny randomly initialised model and a local tokenizer — no download —
  and performs real optimizer steps. It validates the integration; it is NOT a meaningful model.
* ``sft`` mode loads a configurable base model/revision and applies LoRA via PEFT.
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from traceforge.contracts.common import CapabilityUnavailable, sha256_bytes, sha256_json
from traceforge.modules.training.sft.formatting import render_plain

IGNORE_INDEX = -100


class LoraSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    r: int = Field(default=8, ge=1, le=256)
    alpha: int = 16
    dropout: float = Field(default=0.05, ge=0.0, le=0.5)
    target_modules: list[str] | None = None


class SFTConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = Field(default="cpu-test", pattern="^(cpu-test|sft)$")
    base_model: str = "tiny-random-local"
    base_model_revision: str | None = None
    chat_template: str = Field(default="auto", pattern="^(auto|plain)$")
    dataset_dir: str | None = None
    train_file: str = "train.parquet"
    validation_file: str = "validation.parquet"
    output_dir: str = ".traceforge/checkpoints/sft"
    max_length: int = Field(default=512, ge=32, le=32768)
    epochs: int = Field(default=1, ge=1, le=100)
    max_steps: int = Field(default=20, ge=1)
    learning_rate: float = Field(default=5e-4, gt=0)
    batch_size: int = Field(default=2, ge=1)
    seed: int = 0
    use_lora: bool = True
    lora: LoraSettings = LoraSettings()
    resume_from: str | None = None
    allow_download: bool = False

    @property
    def config_hash(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


def require_ml() -> None:
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except ImportError as exc:
        raise CapabilityUnavailable(
            "SFT requires the optional ml dependencies: `uv sync --extra ml`"
        ) from exc


# ----------------------------------------------------------------------------- tokenization


class CharTokenizer:
    """Tiny deterministic byte-level tokenizer for cpu-test mode (no download)."""

    pad_token_id = 0
    eos_token_id = 1
    vocab_size = 258

    def encode(self, text: str) -> list[int]:
        return [b + 2 for b in text.encode("utf-8")]

    def encode_with_spans(
        self, text: str, spans: list[tuple[int, int]]
    ) -> tuple[list[int], list[bool]]:
        ids: list[int] = []
        mask: list[bool] = []
        for idx, ch in enumerate(text):
            in_span = any(a <= idx < b for a, b in spans)
            for b in ch.encode("utf-8"):
                ids.append(b + 2)
                mask.append(in_span)
        return ids, mask


def encode_example(
    messages: list[dict[str, Any]], tokenizer: Any, max_length: int, template: str = "auto"
) -> tuple[list[int], list[int]]:
    """Return (input_ids, labels) with non-assistant tokens masked to IGNORE_INDEX."""
    if (
        isinstance(tokenizer, CharTokenizer)
        or template == "plain"
        or not getattr(tokenizer, "chat_template", None)
    ):
        if isinstance(tokenizer, CharTokenizer):
            text, spans = render_plain(messages)
            ids, mask = tokenizer.encode_with_spans(text, spans)
        else:
            ids, mask = [], []
            for i in range(len(messages)):
                prefix = render_plain(messages[:i])[0]
                full = render_plain(messages[: i + 1])[0]
                new = tokenizer(full, add_special_tokens=False)["input_ids"][
                    len(tokenizer(prefix, add_special_tokens=False)["input_ids"]) :
                ]
                ids += new
                mask += [messages[i]["role"] == "assistant"] * len(new)
    else:
        ids, mask = [], []
        previous: list[int] = []
        for i in range(len(messages)):
            current = tokenizer.apply_chat_template(messages[: i + 1], tokenize=True)
            new = current[len(previous) :]
            ids += new
            mask += [messages[i]["role"] == "assistant"] * len(new)
            previous = current
    ids, mask = ids[:max_length], mask[:max_length]
    labels = [t if m else IGNORE_INDEX for t, m in zip(ids, mask, strict=True)]
    return ids, labels


def load_rows(path: Path) -> list[dict[str, Any]]:
    import pyarrow.parquet as pq

    return pq.read_table(path).to_pylist()


# ----------------------------------------------------------------------------- training


def _tiny_model(vocab_size: int, seed: int, max_length: int) -> Any:
    import torch
    from transformers import GPT2Config, GPT2LMHeadModel

    torch.manual_seed(seed)
    cfg = GPT2Config(
        vocab_size=vocab_size,
        n_positions=max(1024, max_length),
        n_embd=64,
        n_layer=2,
        n_head=2,
        bos_token_id=1,
        eos_token_id=1,
        pad_token_id=0,
    )
    return GPT2LMHeadModel(cfg)


def _load_model(config: SFTConfig) -> tuple[Any, Any]:
    tok: Any
    model: Any
    if config.mode == "cpu-test":
        tok = CharTokenizer()
        return _tiny_model(tok.vocab_size, config.seed, config.max_length), tok
    if config.allow_download and not config.base_model_revision:
        raise CapabilityUnavailable(
            "allow_download requires base_model_revision (a pinned commit) for supply-chain integrity"
        )
    from transformers import AutoModelForCausalLM, AutoTokenizer

    kwargs: dict[str, Any] = {
        "revision": config.base_model_revision,
        "local_files_only": not config.allow_download,
    }
    try:
        tok = AutoTokenizer.from_pretrained(config.base_model, **kwargs)  # nosec B615 - revision passed via kwargs
        model = AutoModelForCausalLM.from_pretrained(  # nosec B615 - revision passed via kwargs
            config.base_model, use_safetensors=True, **kwargs
        )
    except OSError as exc:
        raise CapabilityUnavailable(
            f"base model {config.base_model} not available locally; download it first or set "
            "allow_download=true (model weights are a trust boundary: prefer safetensors)"
        ) from exc
    if tok.pad_token_id is None:
        tok.pad_token = tok.eos_token
    if config.use_lora:
        try:
            from peft import LoraConfig, get_peft_model
        except ImportError as exc:
            raise CapabilityUnavailable("use_lora requires peft (uv sync --extra ml)") from exc
        model = get_peft_model(
            model,
            LoraConfig(
                r=config.lora.r,
                lora_alpha=config.lora.alpha,
                lora_dropout=config.lora.dropout,
                target_modules=config.lora.target_modules,
                task_type="CAUSAL_LM",
            ),
        )
    return model, tok


def _batches(
    examples: list[tuple[list[int], list[int]]], size: int, pad: int, seed: int
) -> list[Any]:
    import torch

    order = list(range(len(examples)))
    random.Random(seed).shuffle(order)  # noqa: S311 - data order, not security
    out = []
    for i in range(0, len(order), size):
        chunk = [examples[j] for j in order[i : i + size]]
        width = max(len(ids) for ids, _ in chunk)
        ids = torch.full((len(chunk), width), pad, dtype=torch.long)
        labels = torch.full((len(chunk), width), IGNORE_INDEX, dtype=torch.long)
        attn = torch.zeros((len(chunk), width), dtype=torch.long)
        for k, (x, y) in enumerate(chunk):
            ids[k, : len(x)] = torch.tensor(x)
            labels[k, : len(y)] = torch.tensor(y)
            attn[k, : len(x)] = 1
        out.append((ids, labels, attn))
    return out


def run_sft(
    config: SFTConfig, train_rows: list[dict[str, Any]], val_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """Real training loop (plain PyTorch for transparency). Returns metrics + checkpoint metadata."""
    require_ml()
    import torch

    if not train_rows:
        raise CapabilityUnavailable(
            "training split is empty; build a dataset with verified successes first"
        )
    torch.manual_seed(config.seed)
    model, tok = _load_model(config)
    pad = tok.pad_token_id if tok.pad_token_id is not None else 0

    dropped = {"train": 0, "validation": 0}

    def encode(rows: list[dict[str, Any]], split: str) -> list[tuple[list[int], list[int]]]:
        out = []
        for r in rows:
            ids, labels = encode_example(
                json.loads(str(r["messages_json"])), tok, config.max_length, config.chat_template
            )
            if any(lbl != IGNORE_INDEX for lbl in labels):
                out.append((ids, labels))
            else:  # truncation left no assistant target in the window
                dropped[split] += 1
        return out

    train, val = encode(train_rows, "train"), encode(val_rows, "validation")
    if not train:
        raise CapabilityUnavailable(
            f"no trainable examples: all {len(train_rows)} training rows lost their assistant targets at "
            f"max_length={config.max_length}; increase max_length"
        )
    optimizer = torch.optim.AdamW(
        [p for p in model.parameters() if p.requires_grad], lr=config.learning_rate
    )
    start_step = 0
    out_dir = Path(config.output_dir)
    if config.resume_from:
        state = torch.load(Path(config.resume_from) / "trainer_state.pt", weights_only=True)
        model.load_state_dict(state["model"])
        optimizer.load_state_dict(state["optimizer"])
        start_step = int(state["step"])
    losses: list[float] = []
    step = start_step
    model.train()
    t0 = time.perf_counter()
    for epoch in range(config.epochs):
        for ids, labels, attn in _batches(train, config.batch_size, pad, config.seed + epoch):
            if step >= config.max_steps:
                break
            loss = model(input_ids=ids, attention_mask=attn, labels=labels).loss
            loss.backward()
            optimizer.step()
            optimizer.zero_grad()
            losses.append(float(loss.detach()))
            step += 1
    val_loss = None
    if val:
        model.eval()
        with torch.no_grad():
            vals = [
                float(model(input_ids=i, attention_mask=a, labels=lbl).loss)
                for i, lbl, a in _batches(val, config.batch_size, pad, 0)
            ]
        val_loss = sum(vals) / len(vals)
    ckpt_dir = out_dir / f"step-{step}"
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    state_path = ckpt_dir / "trainer_state.pt"
    torch.save(
        {"model": model.state_dict(), "optimizer": optimizer.state_dict(), "step": step}, state_path
    )
    meta = {
        "mode": config.mode,
        "base_model": config.base_model,
        "base_model_revision": config.base_model_revision,
        "config_hash": config.config_hash,
        "seed": config.seed,
        "step": step,
        "train_examples": len(train),
        "validation_examples": len(val),
        "dropped_no_target": dropped,
        "first_loss": losses[0] if losses else None,
        "last_loss": losses[-1] if losses else None,
        "validation_loss": val_loss,
        "loss_curve": losses,
        "seconds": time.perf_counter() - t0,
        "checkpoint": str(state_path),
        "checkpoint_sha256": sha256_bytes(state_path.read_bytes()),
        "torch_version": torch.__version__,
    }
    (ckpt_dir / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta
