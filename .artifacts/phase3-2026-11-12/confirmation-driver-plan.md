# Confirmation round auxiliary drivers implementation plan

**Goal:** Prepare and recover one explicit four-Run ABBA confirmation through the original Analyzer, Intake, Controller and fixed budget.

**Architecture:** `prepare_confirmation_round.py` validates explicit root-supplied identities, completed prior rounds, budget and frozen inputs, then prepares/restores a one-strategy session. `run_confirmation_round.py` validates the root's two Plans/four replicas and simulates original `choose_jobs` on a detached state before original publication/intake/Controller execution. No model generation, new Benchmark, direct promotion, budget reset or live calls during development.

**Tech stack:** Existing Python standard library, local temporary SQLite fixtures, existing RSI APIs; no installation.

**Spec:** Root assignment: two real rounds complete; no other unfinished jobs; same real context and exact candidate artifacts; four executions / 7200 declared seconds; explicit ABBA labels/priorities supplied by root; immutable inputs, session and operation recovery.

- [ ] Write tests for missing helper, immutable preparation/session recovery, finished-round and insufficient-budget rejection, wrong order/identity rejection and mid-run recovery without extra attempts.
- [ ] Run tests and retain genuine missing-implementation RED.
- [ ] Implement the two thin drivers and usage/schema notes. Reuse `real_round_aux` and original APIs. A successful Run is never retried.
- [ ] Run local synthetic tests; independently review. Root owns final full-suite verification and any production execution.

Review focus: cross-context/source substitution; stale completed-round markers; restart budget charged again; priority order differing from advertised ABBA; changed inputs silently replacing a completed session. All require behavioral tests or existing verifier coverage.
