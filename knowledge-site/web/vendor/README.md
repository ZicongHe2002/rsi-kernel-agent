# Vendored browser libraries

These libraries are served locally. `VERSIONS.json` records their upstream npm
archive URLs, npm SHA-512 integrity values, and entry-file SHA-256 hashes.
Keep the upstream license files alongside the assets.

## Mermaid 11.17.2

Source: <https://registry.npmjs.org/mermaid/-/mermaid-11.17.2.tgz>

Verified archive integrity:
`sha512-V6K3C8EBdEsPFZXSKMJe6ppQOENxuHARr9GvHX4hh47lAbhMRD9qf4oEK7LoaRQxULMa80/qt5gHO73aCleBBg==`

`mermaid-11.17.2/` contains the official `dist/mermaid.esm.min.mjs` and all
103 `.mjs` files from `dist/chunks/mermaid.esm.min/`, copied without modification.
This is the complete minified ESM distribution, retaining every bundled diagram
type; it is not a reduced or custom Mermaid build. No npm installation, package
lifecycle script, transpiler, or bundler is needed to create these assets.

`mermaid-11.17.2/manifest.json` lists all 104 files with their relative paths,
sizes, and SHA-256 hashes. Its `flowchart_preload` list contains the 27-file
static dependency closure of the main entry, flowchart renderer, and default
Dagre layout engine. It excludes optional math/layout and other diagram chunks;
the official library loads those on demand.

`mermaid.js` is a generated ES module wrapper. To reproduce it, write a default
import of `./mermaid-11.17.2/mermaid.esm.min.mjs`, then one side-effect import for
each other path in the manifest's sorted `flowchart_preload` list, and finally
`window.mermaid = mermaid;`. Load the wrapper using `script.type = 'module'`.
Direct imports allow the browser to discover all common flowchart dependencies
at once instead of waiting for their nested import chains. Other diagrams still
load their own official chunks as needed.

The original IIFE was 3,572,661 bytes (979,546 bytes with gzip level 6). The
27 common ESM files total 859,596 bytes (235,028 bytes as individually gzipped
responses), plus the small wrapper. This changes resource loading only: the
application still initializes Mermaid with strict security settings and
sanitizes rendered SVG separately.

`mermaid-full.js` retains the original verified official IIFE as a recovery
path. If the module entry or a dynamically imported diagram chunk fails to
load, the next explicit retry loads this classic script instead. It avoids
the browser's cached failed-module graph, keeps the current document and
drafts, and uses the same strict initialization and SVG sanitation. A failed
classic-script request can itself be retried without reloading the page.

When updating Mermaid, verify the new upstream archive integrity, preserve the
complete matching chunk set, regenerate hashes and the wrapper together, and
run the diagram, lazy-loading, and permissions browser regressions. Do not mix
chunk files from different releases.
