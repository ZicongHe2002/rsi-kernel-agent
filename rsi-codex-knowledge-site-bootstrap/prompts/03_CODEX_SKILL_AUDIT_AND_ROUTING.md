# Codex — Audit Existing Skills and Map Them to RSI

Inspect actual Codex locations:
- repo `.agents/skills/` from cwd up to Git root;
- user `$HOME/.agents/skills/`;
- admin `/etc/codex/skills/` if readable;
- bundled/system skills and enabled plugin Skills where visible.
Also inspect root/nested `AGENTS.md`, user `~/.codex/AGENTS.md`, trusted project `.codex/config.toml`, MCP servers, subagents and CLI/Python tools as separate capability types.

Do not scan unrelated home folders or private data.

For each relevant Skill record exact name/description/path/scope/version or hash, dependencies, scripts, side effects (network/write/shell/install/TPU), current discoverability and verification status. A file existing does not prove it is available in this session.

Candidate system skills to check if present: `skill-installer`, `skill-creator`; check `openai-docs` and `gh-address-comments` only if exposed by the current installation. Do not assume they are installed.

Potential external candidate: Google Perfetto Skill for `trace_processor`/PerfettoSQL. Inspect its full instructions and scripts; verify XSpace/Perfetto trace compatibility and offline binary availability before use. Do not treat it as a TPU-specific LLO analyzer.

Map actual Skills/tools to repository audit, planning, Python implementation, testing, debugging, code review, research, Profile parsing, Analyzer, math, multi-plan generation, Benchmark, Memory and Controller.

Use REUSE / COMPOSE / EXTEND / MISSING / UNVERIFIED. Separately mark inspected_only / cpu_tested / tpu_tested / unavailable.
Separate development-time Skills used by Codex from runtime dependencies of the deployed RSI Agent. Do not install or create anything during this audit. Return the capability matrix and wait for approval.
