# ADR-009: Task-family-variant held-out private benchmark

**Status:** accepted

**Decision.** Split policy `template-holdout` v1: the last template of each family is the private test
(unseen structural variant); remaining instances split by seed into train / validation / public test.
Leakage checks cover fixture hash, template id, and dataset lineage; RL prompt datasets and SFT
datasets exclude held-out tasks.

**Consequences.** Private-test results measure transfer to unseen code structure, not memorisation of
instances. With 39 tasks, confidence intervals are wide and reported as such.
