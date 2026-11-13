# M1 — Populate `knowledge-site/` Safely

M0 has been reviewed. The owner has approved the source folder, target root and exact write scope.

Populate `<PROJECT_ROOT>/knowledge-site/` from the approved knowledge source.

## Requirements
- Preserve meaningful source hierarchy and all documents/images/diagrams/attachments.
- Keep originals when converting formats.
- Preserve Mermaid source, not only rendered diagrams.
- Preserve or repair relative links; validate every link.
- Separate current design, supplements, references, archives and drafts.
- Avoid duplicate current documents; use an index and links.
- Record each item as copied, converted, linked, skipped or unavailable.

## Required structure
`README.md`, `index.md`, `architecture/`, `modules/`, `practice/`, `references/`, `images/`, `diagrams/`, `archives/`, `manifests/`, `access/ACCESS_POLICY.md`, `connections/README.md`, and sanitized MCP examples only if required.

## Permissions
Document roles and policy; do not pretend a text file applies ACLs. Git does not preserve full user/group ACL state. Do not run chmod/chown or alter account permissions without explicit authorization.

## ChatGPT/Codex connections
Keep only non-secret templates. Use `${INTERNAL_MCP_TOKEN}`-style placeholders, never actual values. Keep auth in secure local stores and interactive account authorization. Do not copy auth databases, OAuth refresh tokens, API keys, cookies or browser state. Do not put provider-auth overrides in project `.codex/config.toml`. Validate config syntax without connecting externally.

## Integrity checks
Compare source/destination file counts and sizes; checksum copied binary files; validate image references, Markdown links and Mermaid sources; verify source files unchanged; scan destination for likely secrets without printing values; verify `.gitignore`; create file/image/link/migration manifests.

Do not claim 100% complete if anything is missing or unreadable. Do not delete the source. Finish with the resulting tree, check results and unresolved items.
