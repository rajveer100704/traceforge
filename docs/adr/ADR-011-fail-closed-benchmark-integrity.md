# ADR-011: Fail-closed benchmark integrity and task-clustered uncertainty

**Status:** accepted (v0.2)

**Decision.** Official benchmark runs refuse to start when split, task-lineage or training-overlap
validation fails; a run that must proceed anyway is an explicitly selected `diagnostic` run whose
artifacts are marked non-official. The complete leakage report is persisted with every benchmark
artifact, including refusals. Training paths enforce the same rule from the other side (SFT rows
and GRPO prompts must come from training-split tasks). Episode-level Wilson intervals are kept as
descriptive statistics; task-clustered bootstrap intervals are added and are the interval for
population claims, because episodes of one task (and repeated seeds of a deterministic provider)
are not independent.

**Consequences.** Contaminated evaluations cannot silently produce headline numbers. Reports show
two sample sizes (`n_tasks`, `n_episodes`); with 6–9 tasks per split, clustered intervals are wide.
Imported traces whose task ids are not registered do not block evaluation (they cannot collide by
id); content-level duplicate detection of held-out fixtures is future work.
