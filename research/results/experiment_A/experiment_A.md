# Experiment A — trajectory quality (D1–D4)

Trajectories: 81; quality statuses: {'accepted': 77, 'duplicate': 1, 'rejected': 3}

Corpus = verified demo rollouts + the defective synthetic example traces (parsed records).
'Refuted claims' = trajectories whose agent claimed success but whose verified outcome is not
SUCCESS (or is unverifiable). A pipeline that trusted claims would train on these as successes.

| Level | Selected | Train rows | Val rows | Verified-success fraction | Refuted/unverified claims |
|---|---|---|---|---|---|
| d1_raw | 81 | 45 | 36 | 31% | 51 (63%) |
| d2_schema_valid | 77 | 41 | 36 | 31% | 50 (65%) |
| d3_verified_success | 24 | 12 | 12 | 100% | 0 (0%) |
| d4_verified_plus_informative | 48 | 24 | 24 | 50% | 24 (50%) |

Downstream SFT comparison: **NOT EXECUTED: downstream SFT comparison across D1–D4 requires a real base model; see Experiment F (sft mode: available on CPU only (no CUDA); practical only for tiny models)**
