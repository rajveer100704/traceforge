# Reproduce experiment F

- Source commit: `c5d242b993a4a5aa728ff38304b894f12b5f34fd` (working tree was DIRTY)
- Environment, versions, config hashes and seeds: `bundle.json`
- Aggregate metrics and confidence intervals: `metrics.json`
- Raw episodes: `episodes.jsonl.gz` (one JSON object per episode)

## Commands

```bash
git checkout c5d242b993a4a5aa728ff38304b894f12b5f34fd
uv sync --frozen --extra ml
uv run traceforge experiment run F --seeds 0,1,2
```

## Verify this bundle

```bash
uv run python -c "from pathlib import Path; from traceforge.application.reproducibility import verify_bundle; print(verify_bundle(Path('.')) or 'OK')"
```

Scripted-provider experiments are deterministic: rerunning at the same commit reproduces the
same verdicts and rewards (identifiers and timings differ). Model-provider and training
results additionally depend on the recorded model revision, hardware and library versions.
