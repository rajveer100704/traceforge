from __future__ import annotations

import json

from gymnasium.utils.env_checker import check_env

from traceforge.modules.agents.providers.fixers import reference_edits
from traceforge.modules.environments.gym_adapter import TraceForgeGymEnv
from traceforge.modules.rewards.policies import PolicyRegistry
from traceforge.modules.verification.engine import VerificationEngine


def _env(registry, sandbox, tasks=None):  # type: ignore[no-untyped-def]
    return TraceForgeGymEnv(
        registry,
        sandbox,
        VerificationEngine(sandbox),
        PolicyRegistry().get("verified-v1"),
        tasks or ["sqli-t0-s000"],
    )


def test_gym_episode_reward_from_verification(registry, sandbox) -> None:  # type: ignore[no-untyped-def]
    env = _env(registry, sandbox)
    obs, info = env.reset(seed=0, options={"task_id": "sqli-t0-s000"})
    assert "sqli-t0-s000" in obs and "test_sec_" not in obs
    module = str(registry.bundle("sqli-t0-s000").spec.public_metadata["module"])
    out, r, term, trunc, _ = env.step(
        json.dumps({"tool": "read_file", "arguments": {"path": module}})
    )
    assert r == 0.0 and not term and not trunc
    src = json.loads(out)["output"]["content"]
    for e in reference_edits(src):
        env.step(
            json.dumps(
                {
                    "tool": "apply_patch",
                    "arguments": {
                        "path": module,
                        "mode": "search_replace",
                        "old": e.old,
                        "new": e.new,
                    },
                }
            )
        )
    run = json.loads(env.step(json.dumps({"tool": "run_visible_tests", "arguments": {}}))[0])[
        "output"
    ]
    env.step(
        json.dumps(
            {
                "tool": "submit_evidence",
                "arguments": {
                    "evidence_type": "test_run",
                    "artifact_id": run["run_id"],
                    "explanation": "ok",
                },
            }
        )
    )
    _, reward, term, trunc, info = env.step(
        json.dumps(
            {
                "tool": "submit_final_report",
                "arguments": {"summary": "fixed", "claimed_success": True},
            }
        )
    )
    assert term and not trunc and info["verdict"] == "PASS" and reward > 0.9
    env.close()
    assert sandbox.live_sandboxes() == []


def test_gym_malformed_action_and_false_claim(registry, sandbox) -> None:  # type: ignore[no-untyped-def]
    env = _env(registry, sandbox)
    env.reset(options={"task_id": "sqli-t0-s000"})
    out, *_ = env.step("not json")
    assert json.loads(out)["ok"] is False
    _, reward, term, _, info = env.step(
        json.dumps(
            {
                "tool": "submit_final_report",
                "arguments": {"summary": "done", "claimed_success": True},
            }
        )
    )
    assert term and reward == 0.0 and info["verdict"] != "PASS"
    env.close()


def test_gymnasium_env_checker(registry, sandbox) -> None:  # type: ignore[no-untyped-def]
    env = _env(registry, sandbox)
    check_env(env, skip_render_check=True)
    env.close()
    assert sandbox.live_sandboxes() == []
