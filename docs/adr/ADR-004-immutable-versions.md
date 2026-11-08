# ADR-004: Immutable task, dataset, verifier and reward-policy versions

**Status:** accepted

**Decision.** `task_id@version` binds to a fixture hash; dataset versions are derived from a content
hash of sources + transformation + split policy; reward policies are `(policy_id, version)` with a
policy hash; verifier results carry verifier/engine versions and a config hash. Re-registering a key
with different content raises a conflict.

**Consequences.** Every metric can be traced to exact inputs. Changing anything requires a new version.
