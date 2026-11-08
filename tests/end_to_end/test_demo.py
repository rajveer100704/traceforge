"""The full lifecycle acceptance test (§18.5). Must never be skipped."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path


def test_demo_lifecycle_passes(platform) -> None:  # type: ignore[no-untyped-def]
    from traceforge.application.demo import run_demo

    result = asyncio.run(run_demo(platform, "local"))
    assert result["passed"], result["failures"]
    steps = result["steps"]
    assert steps["tasks"]["leakage_ok"] and steps["tasks"]["registered"] >= 30
    assert steps["benchmark"]["by_family"].keys() == {"sql_injection", "access_control", "ssrf"}
    assert steps["datasets"]["rebuild_same_version"]
    assert steps["cleanup"] == {"live_sandboxes": [], "leftover_dirs": []}
    assert steps["reward_recompute"]["matches_stored"] is True
    summary = json.loads(Path(result["summary_path"]).read_text(encoding="utf-8"))
    assert summary["passed"] is True
    assert (
        Path(steps["benchmark"]["report_md"])
        .read_text(encoding="utf-8")
        .startswith("# TraceForge benchmark")
    )
