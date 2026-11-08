# ADR-013: Separate development and held-out reward-hacking attacks

**Status:** accepted (v0.2)

**Decision.** Attacks used while designing the verifier/reward (development set) are kept apart
from attacks written after a verifier version is frozen (held-out set). A versioned catalogue
records, per attack, which verifier versions were designed with knowledge of it. When a verifier
change responds to a held-out attack, the attack moves into the known set for that new version.

**Consequences.** A 0 % false-positive rate on development attacks (Experiment B) is reported as
"handles known attacks", not as robustness. Robustness claims come only from held-out attacks
(Experiment H), and first-contact failures are reported as findings rather than patched silently.
