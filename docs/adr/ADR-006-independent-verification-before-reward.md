# ADR-006: Independent verification before reward

**Status:** accepted

**Decision.** Reward is a deterministic function of a `VerificationResult` produced by a verifier that
(a) runs hidden tests in a fresh environment against the candidate's editable files plus *pristine*
protected files, (b) checks state, static safety, behaviour safety and evidence integrity, and
(c) never consults the agent's success claim. Verifier errors are `ERROR`, never `PASS`.
Self-report (R1) and visible-test (R2) verifiers exist only as research baselines.

**Consequences.** Reward hacking surfaces as false-positive gaps between R1/R2 and R3 (Experiment B).
