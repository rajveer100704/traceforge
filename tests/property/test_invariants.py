"""Property-based invariants (Hypothesis)."""

from __future__ import annotations

import json
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.unit.test_rewards_registry_providers import POL, vr
from traceforge.contracts.common import ErrorCode
from traceforge.contracts.jobs import JobStatus
from traceforge.contracts.verification import CheckStatus, Verdict
from traceforge.domain.jobs import can_transition, is_terminal
from traceforge.modules.environments.workspace import ToolError, Workspace
from traceforge.modules.rewards.engine import RewardEngine
from traceforge.modules.trace_processing.normalization import normalize_record

path_chars = st.sampled_from([*list("ab./\\~:_-"), "..", "%2e", "\x00", "app", "tests"])


# Filesystem-backed property: generation/IO latency varies with machine load, not correctness.
@settings(
    max_examples=300,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(st.lists(path_chars, min_size=1, max_size=12).map("".join))
def test_workspace_paths_never_escape(tmp_path: Path, rel: str) -> None:
    root = tmp_path / "ws"
    (root / "app").mkdir(parents=True, exist_ok=True)
    ws = Workspace(root, editable=frozenset(), protected_globs=())
    try:
        _norm, resolved = ws.resolve(rel)
    except ToolError as exc:
        assert exc.code == ErrorCode.PATH_VIOLATION
        return
    assert resolved.is_relative_to(root.resolve())


@given(st.sampled_from(list(JobStatus)), st.sampled_from(list(JobStatus)))
def test_terminal_job_never_runs_again(a: JobStatus, b: JobStatus) -> None:
    if is_terminal(a):
        assert not can_transition(a, b)
    if b == JobStatus.RUNNING:
        assert a == JobStatus.QUEUED or not can_transition(a, b)


@given(st.integers(0, 200), st.integers(0, 200))
def test_more_actions_never_increase_reward(a: int, b: int) -> None:
    lo, hi = sorted((a, b))
    r_lo = RewardEngine().compute(vr(tool_calls=lo), POL.get("verified-v1"))
    r_hi = RewardEngine().compute(vr(tool_calls=hi), POL.get("verified-v1"))
    assert r_hi.final_reward <= r_lo.final_reward
    completion = {c.name.value: c.raw_value for c in r_hi.components}["task_completion"]
    assert completion == {c.name.value: c.raw_value for c in r_lo.components}["task_completion"]


@given(
    st.sampled_from(list(Verdict)),
    st.dictionaries(
        st.sampled_from(
            [
                "state",
                "static_safety",
                "hidden_security",
                "regression",
                "behaviour_safety",
                "evidence_integrity",
            ]
        ),
        st.sampled_from(list(CheckStatus)),
    ),
)
def test_reward_deterministic_and_gated(verdict: Verdict, statuses: dict[str, CheckStatus]) -> None:
    v = vr(verdict, statuses)
    a, b = (
        RewardEngine().compute(v, POL.get("verified-v1")),
        RewardEngine().compute(v, POL.get("verified-v1")),
    )
    assert a.deterministic_view() == b.deterministic_view()
    if verdict != Verdict.PASS or any(
        s != CheckStatus.PASS
        for k, s in statuses.items()
        if k in ("static_safety", "behaviour_safety", "regression", "evidence_integrity")
    ):
        assert a.final_reward == 0.0
    assert 0.0 <= a.final_reward <= 1.0


@given(
    st.text(min_size=0, max_size=50), st.sampled_from(["SUCCESS", "FAILURE", "UNKNOWN", "weird"])
)
def test_normalization_deterministic_and_never_trusts_claims(text: str, outcome: str) -> None:
    rec = {
        "schema_version": "1.0",
        "source_type": "synthetic",
        "started_at": "2026-01-01T00:00:00+00:00",
        "outcome": outcome,
        "steps": [
            {
                "sequence_number": 0,
                "role": "user",
                "content": text,
                "started_at": "2026-01-01T00:00:00+00:00",
            }
        ],
    }
    line = json.dumps(rec)
    a, b = normalize_record(line, "src", 1), normalize_record(line, "src", 1)
    assert a.model_dump() == b.model_dump()
    assert a.outcome.value == "UNKNOWN"


@given(st.text(alphabet=st.characters(blacklist_categories=("Cs",)), min_size=1, max_size=40))
def test_unknown_tool_names_never_dispatch(name: str) -> None:
    from traceforge.modules.agents.tool_registry import TOOLS, ToolRegistry

    reg = ToolRegistry(("read_file",))
    if name not in TOOLS:
        assert reg.get(name) is None
    assert reg.is_allowed(name) == (name == "read_file")
