# AGENTS.md — `wiki/`

Context for coding agents working on the documentation site.

## What this is

A VitePress-based wiki for `slz-readiness`. Source under `wiki/docs/`. Entry config: [`docs/.vitepress/config.mts`](docs/.vitepress/config.mts).

## Build & preview

```bash
cd wiki
npm install          # only on first run
npm run docs:dev     # local preview at http://localhost:5173
npm run docs:build   # static output under docs/.vitepress/dist/
```

> **Do not commit** `node_modules/` or `docs/.vitepress/dist/`.

## Authoring conventions

- **Every claim must cite a source file with a linked reference** to `https://github.com/msucharda/slz-readiness/blob/main/<path>#L<line>`. No unsourced assertions.
- **Dark-mode Mermaid palette** (applied via `theme/index.ts` init): fills `#2d333b`, borders `#6d5dfc`, text `#e6edf3`, subgraph bg `#161b22`, subgraph borders `#30363d`, lines `#8b949e`. Use `classDef` per-diagram.
- **Use `<br>` not `<br/>`** in diagram labels (VitePress + Mermaid compatibility).
- **Use `autonumber`** on every `sequenceDiagram`.
- **Escape bare generics** like `<T>` by wrapping in backticks; otherwise the Markdown parser eats them as HTML tags.
- **Link between pages using absolute paths** — e.g. `/deep-dive/hooks` not `./hooks.md`.

## Structure

```
wiki/
├── catalogue.json          # hierarchy source of truth
├── package.json            # VitePress deps
└── docs/
    ├── index.md            # homepage
    ├── getting-started/    # 4 pages
    ├── onboarding/         # 4 audience guides
    ├── deep-dive/          # ~13 technical pages
    └── .vitepress/
        ├── config.mts
        └── theme/
            ├── index.ts       # Mermaid init + click-to-zoom
            ├── custom.css
            └── components/
```

## Editing a page

1. Edit the `.md` file directly.
2. Preview with `npm run docs:dev`.
3. Confirm every new claim has a linked citation.
4. Confirm new Mermaid diagrams follow the palette + autonumber rules.

## Adding a new page

1. Create the `.md` file under the appropriate folder.
2. Add it to the sidebar in [`docs/.vitepress/config.mts`](docs/.vitepress/config.mts).
3. Update [`catalogue.json`](catalogue.json) if it's a top-level module.
4. Cross-link from related pages.

## Do not

- Modify anything outside `wiki/` from within a wiki-authoring task.
- Add VitePress plugins that require network at build time (air-gapped build requirement).
- Commit generated output (`dist/`, `node_modules/`, `.vitepress/cache/`).
