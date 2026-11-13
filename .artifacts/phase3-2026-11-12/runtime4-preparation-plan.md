# Runtime 4 preparation implementation plan

Goal: reuse original observation, readmission, stage execution and verification code for four Runtime 4 prerequisites without changing Runtime 3 evidence or executing live work during implementation.

1. Add a shared observation argument parser in runtime_readmission.py; preserve legacy flags and default suffix, accept explicit runtimeN, and reject conflicting generations. Update only setup/probe argument selection.
2. Add admit_runtime4.py. Validate allocated intent, generation pin, successful isolated probe and exact saved environment before writes. Under the original journal lock, require the archived R2 loss marker and no unfinished task jobs, validate all four Runtime 3 origins, then reuse remeasure and write only Runtime 4 auxiliary files.
3. Add an optional locked guard callback to advance_runtime2.advance; add advance_runtime4.py as a thin caller. The guard checks archive, journal, config and all four saved jobs before any backend construction/reservation/lookup/submit.
4. Add fixture tests before implementation and save red/green logs. Cover rejected generation conflicts, observation isolation, invalid/missing archival prerequisites, unfinished jobs, wrong probe or origins, four new Run identities, idempotence and callback ordering. No live Store or network calls.
5. Independently add a four-item read-only verification wrapper reusing verify_job and terminal_view; pending stays pending and reports use unique files. Review all changes and retain explicit hardware-validation limits.

Scope excludes allocating hardware, bootstrap, execution, active binding changes, core Store/Controller changes and recovery-round publication. Root owns those actions.

## Review result

The Runtime 4 recovery wrappers were independently parsed/compiled and their five fixture gate tests passed. The recovery gate keeps the old Runtime 3 ABBA Context for historical confirmation, requires the archived unsubmitted-loss marker before reading the old gates, requires a distinct Runtime 4 Context and unchanged source/config/build identities, and blocks foreign unfinished journal jobs. The four replacement prerequisite jobs are verified separately by the original `require_gates(..., runtime4)` before recovery preparation; their runtime-validation round IDs do not replace the original optimization journal.
