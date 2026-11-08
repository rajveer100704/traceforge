# ADR-008: SFT before online RL

**Status:** accepted

**Decision.** The training path is baseline → SFT on verified trajectories (assistant-only loss,
LoRA) → optional GRPO. SFT is cheaper, debuggable, and gives GRPO a reasonable starting policy.

**Consequences.** Dataset curation (D1–D4) is a first-class concern; GRPO results are only meaningful
relative to an SFT baseline evaluated on the same benchmark version.
