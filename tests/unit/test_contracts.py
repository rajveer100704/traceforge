from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from traceforge.config import Settings
from traceforge.contracts.rewards import ComponentKind, ComponentSpec, GateKind, RewardPolicy
from traceforge.contracts.tasks import TaskSpec
from traceforge.contracts.traces import Outcome, SourceType, StepRole, Trajectory, TrajectoryStep

T = datetime(2026, 1, 1, tzinfo=UTC)


def step(
    i: int, role: StepRole = StepRole.USER, offset: int | None = None, **kw: object
) -> TrajectoryStep:
    return TrajectoryStep(
        sequence_number=i,
        role=role,
        content=f"m{i}",
        started_at=T + timedelta(seconds=offset if offset is not None else i),
        **kw,
    )


def traj(**kw: object) -> Trajectory:
    base: dict[str, object] = dict(
        source_id="s", source_type=SourceType.SYNTHETIC, started_at=T, steps=[step(0), step(1)]
    )
    base.update(kw)
    return Trajectory(**base)  # type: ignore[arg-type]


def test_sequence_must_increase() -> None:
    with pytest.raises(ValidationError, match="strictly increasing"):
        traj(steps=[step(1), step(1)])


def test_naive_timestamp_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        TrajectoryStep(sequence_number=0, role=StepRole.USER, started_at=datetime(2026, 1, 1))


def test_success_requires_verification() -> None:
    with pytest.raises(ValidationError, match="requires verification_id"):
        traj(outcome=Outcome.SUCCESS)
    assert traj(outcome=Outcome.SUCCESS, verification_id=uuid.uuid4()).outcome == Outcome.SUCCESS


def test_unknown_is_default_and_claim_separate() -> None:
    t = traj(claimed_outcome=Outcome.SUCCESS)
    assert t.outcome == Outcome.UNKNOWN and t.claimed_outcome == Outcome.SUCCESS


def test_tool_step_requires_tool_name() -> None:
    with pytest.raises(ValidationError, match="tool_name"):
        step(0, role=StepRole.TOOL)


def test_completed_before_started_rejected() -> None:
    with pytest.raises(ValidationError):
        traj(completed_at=T - timedelta(seconds=1))


def test_content_hash_ignores_ids_and_timestamps() -> None:
    a = traj()
    b = traj(steps=[step(0, offset=50), step(1, offset=60)], started_at=T)
    assert a.content_hash == b.content_hash
    assert a.trajectory_id != b.trajectory_id
    c = traj(steps=[step(0), step(1, role=StepRole.ASSISTANT)])
    assert c.content_hash != a.content_hash


def test_supplied_content_hash_not_trusted_by_normalizer() -> None:
    from traceforge.modules.trace_processing.normalization import normalize_record

    payload = traj().model_dump(mode="json")
    payload["content_hash"] = "f" * 64
    import json

    t = normalize_record(json.dumps(payload), "x", 1)
    assert t.content_hash != "f" * 64


def _policy(**kw: object) -> RewardPolicy:
    base: dict[str, object] = dict(
        policy_id="p",
        version="1.0.0",
        components=(
            ComponentSpec(kind=ComponentKind.TASK_COMPLETION, weight=0.8),
            ComponentSpec(kind=ComponentKind.EFFICIENCY, weight=0.2),
        ),
        hard_gates=(GateKind.AUTHORITATIVE, GateKind.VERDICT_PASS, GateKind.SAFETY),
    )
    base.update(kw)
    return RewardPolicy(**base)  # type: ignore[arg-type]


def test_reward_policy_validation() -> None:
    assert _policy().policy_hash == _policy().policy_hash
    with pytest.raises(ValidationError, match=r"sum to 1.0"):
        _policy(components=(ComponentSpec(kind=ComponentKind.TASK_COMPLETION, weight=0.5),))
    with pytest.raises(ValidationError, match="efficiency weight"):
        _policy(
            components=(
                ComponentSpec(kind=ComponentKind.TASK_COMPLETION, weight=0.4),
                ComponentSpec(kind=ComponentKind.EFFICIENCY, weight=0.6),
            )
        )
    with pytest.raises(ValidationError, match="require gates"):
        _policy(hard_gates=(GateKind.VERDICT_PASS,))


def test_task_spec_requires_tools(registry) -> None:  # type: ignore[no-untyped-def]
    spec = registry.list()[0].spec
    data = spec.model_dump()
    data["allowed_tools"] = ()
    with pytest.raises(ValidationError):
        TaskSpec.model_validate(data)


def test_task_spec_schema_has_no_oracle_fields() -> None:
    schema = TaskSpec.model_json_schema()
    text = str(schema)
    for forbidden in ("hidden_test_module", "fixture_hash", "split", "template_id", "verifier_id"):
        assert forbidden not in text


def test_settings_prod_guards(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValidationError, match=r"API_KEY"):
        Settings(env="prod", data_dir=tmp_path)
    with pytest.raises(ValidationError, match="forbidden in prod"):
        Settings(env="prod", data_dir=tmp_path, api_key="x", allow_untrusted_local_execution=True)
    assert Settings(env="test", data_dir=tmp_path).resolved_database_url.startswith("sqlite:///")
