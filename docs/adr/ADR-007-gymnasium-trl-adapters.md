# ADR-007: Gymnasium and TRL as adapters, not core dependencies

**Status:** accepted

**Decision.** Core environment semantics are TraceForge's own (`TaskEnvironment` + `ToolRouter`).
TRL GRPO integration is an optional adapter implementing the installed TRL `environment_factory`
protocol (`reset`, public tool methods, `get_reward`), behind a compatibility check
(transformers ≥ 5.2 and `environment_factory` present). It fails with an actionable error rather than
silently degrading. A Gymnasium wrapper (`modules/environments/gym_adapter.py`, text action/observation
spaces, terminal reward from verification) passes `gymnasium.utils.env_checker.check_env` for use with
generic RL tooling.

**Consequences.** Library API churn is isolated to `modules/training/grpo`.
