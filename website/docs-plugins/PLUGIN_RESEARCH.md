# Plugin stack research

Working file for the plugin additions that layer on top of the docs
refactor. Versions verified against the npm registry on 2026-04-18
using `pnpm view`. Each section: resolved version, peer-dep check
against Docusaurus 3.10.0 (currently in package.json), caveats.

Not committed to the shipping branch; kept under `docs-plugins/` so
it stays out of the Docusaurus glob (which picks up `.md` only under
`docs/`) and out of the production site.

## @docusaurus/plugin-client-redirects

- **Already installed in the refactor.** Version `^3.10.0`, wired
  in `docusaurus.config.ts` with 13 populated redirects (all URLs
  preserved from pre-refactor paths).
- No install or config touch needed. Leave the redirects list alone.

## @docusaurus/faster

- **Already enabled in the refactor** via `future.faster: true`.
- The spec in the user's brief said `future.experimental_faster: true`.
  Docusaurus 3.10.0 renamed this key; the correct name today is
  `future.faster: true`. Using the old name produces a hard
  validation error at build start. Already corrected in Phase 8a of
  the refactor.

## @docusaurus/plugin-ideal-image

- Latest: **3.10.0** (2026-04-13). Matches our Docusaurus core
  version.
- Direct deps: `sharp@^0.32.3`, `@docusaurus/responsive-loader`,
  `@docusaurus/lqip-loader`. **Sharp is the pnpm 10 install-script
  issue** (facebook/docusaurus#11173). Fix: add `sharp` to
  `pnpm.onlyBuiltDependencies` in `package.json`.
- **Format support: PNG and JPG only.** WebP and SVG are not
  processed by the plugin; for those the component falls back to
  regular `<img>`. Houndarr already standardized on PNG in the
  refactor, so all migration targets are valid.
- Usage: swap `![alt](/img/foo.png)` to the `<Image>` component. One
  import per file: `import Image from '@theme/IdealImage';`.
- Default quality 85; user's threshold for migration is "screenshots
  over 500KB OR wider than 1200px". Current screenshot inventory
  already trimmed to <400 KB each during the refactor, but most are
  2400 px wide (>1200), so they still qualify for migration.

## @easyops-cn/docusaurus-search-local

- Latest: **0.55.1** (2026-02-28). User's brief cited 0.52.3; the
  current release is newer. Last known issue on Docusaurus 3.9
  (#542, `DocsPreferredVersionContextProvider` hook error) was
  **closed 2025-12-01**, fixed in the 0.52+ line, re-verified on
  0.55.x in the npm release history.
- Peer deps compatible: `@docusaurus/theme-common: ^2 || ^3`,
  `react: ^16 || ^17 || ^18 || ^19`. Matches our stack.
- **Registers as a theme, not a plugin.** Goes in the `themes`
  array with `require.resolve(...)` wrapping. Config options we
  care about: `hashed: true` (long-term cache of the search index),
  `language: ["en"]`, `docsRouteBasePath: "/docs"` (our case),
  `indexBlog: false` (blog disabled in our preset),
  `indexPages: true` (so the landing page is searchable too).
- Zero doc-content edits required to make it work. Good
  front-matter descriptions improve snippet quality; the refactor
  already ships those.

## docusaurus-plugin-image-zoom

- Latest: **3.0.1** (2025-02-07). Author: Gabriel J. Csapo
  (the correct "gabrielcsapo" package; not the abandoned
  flexanalytics fork).
- Peer dep: `@docusaurus/theme-classic: >=3.0.0`. Compatible.
- Config: `themeConfig.zoom` with `selector`, optional
  `background` (light/dark), and `config` (passed to medium-zoom).
- **Selector: `.markdown :not(em) > img`** per the brief. Authors
  opt out any image by wrapping in underscores (`_![alt](path)_`),
  which the plugin's `:not(em)` check excludes. Default selector is
  `.markdown img`; we override.
- Zero doc-content edits; zoom works on existing images.

## @docusaurus/theme-mermaid

- Bundled under the `@docusaurus/` scope at **3.10.0** (matches
  core). **Not installed transitively** by `preset-classic`; needs
  an explicit `pnpm add`.
- Config is a theme plus a markdown flag:
  ```ts
  themes: ['@docusaurus/theme-mermaid'],
  markdown: { mermaid: true },
  ```
- Dark-mode theme: set
  `themeConfig.mermaid.theme = { light: 'default', dark: 'dark' }`
  to match the site's default-dark palette.

## pnpm 10 sharp fix

The project is on `pnpm@10.33.0`. With pnpm 10, install scripts for
native-dep packages are not run by default. `sharp` (pulled in by
ideal-image) needs its install script to build the platform binary.

Without the allowlist entry, `pnpm install` succeeds without error
but the first build fails with something like:

    Error: Could not load the "sharp" module using the darwin-arm64
    runtime

The fix is to extend the existing `pnpm.onlyBuiltDependencies` list
in `package.json` (currently `["@swc/core", "core-js"]`) to include
`sharp`. After updating, run `pnpm install` again to trigger the
install script.

## Decisions carried into implementation

- Install all four plugins at their latest: `@docusaurus/plugin-ideal-image@3.10.0`,
  `@docusaurus/theme-mermaid@3.10.0`, `@easyops-cn/docusaurus-search-local@0.55.1`,
  `docusaurus-plugin-image-zoom@3.0.1`.
- Add `sharp` to `pnpm.onlyBuiltDependencies`.
- Keep the existing client-redirects entry and its 13-redirect list
  untouched.
- Faster key stays at `future.faster: true`.
- Markdown config: add `markdown.mermaid: true` alongside the
  existing `markdown.hooks.onBrokenMarkdownLinks: 'throw'`.
- `editUrl` and broken-link policy are already set.
  `showLastUpdateTime: true` and `showLastUpdateAuthor: true` added
  to `presets[classic].docs`.

## Build-time comparison

Recorded after wiring faster + search-local + ideal-image + mermaid
+ image-zoom. First build timing on this machine:

- Before plugin stack (refactor-only baseline): ~9 s cold, ~2 s
  warm, measured informally during the refactor work.
- After plugin stack: filled in after first build on this branch.

## Page-weight comparison

Recorded before and after migrating screenshots to `<Image>`. The
heaviest screenshot page in the refactor is
`reference/instance-settings.md` (two screenshots) and
`guides/first-run-setup.md` (three screenshots).

Numbers filled in after the migration commit lands.

## Open flags for user

1. `pnpm build` emits one warning from Mermaid's transitive
   `vscode-languageserver-types` UMD module:

       Critical dependency: require function is used in a way in
       which dependencies cannot be statically extracted

   This is a well-known Webpack warning about how the Monaco
   language-server package packages its `require()`. It does not
   affect output. Suppressing it via `webpack.ignoreWarnings`
   would technically meet a zero-warning gate, but the brief says
   "Do NOT suppress warnings to make the build pass." Leaving it
   visible. If this becomes a CI gate in the future, the fix is
   to add a webpack rule in `docusaurus.config.ts` narrowly
   ignoring that one module.

2. Sharp install scripts blocked by pnpm 10 on first install, as
   predicted. Fixed by adding `"sharp"` to
   `pnpm.onlyBuiltDependencies` in `package.json`. Build then
   succeeds.

3. `@easyops-cn/docusaurus-search-local@0.55.1` started cleanly
   against Docusaurus 3.10.0 on first build. No errors from the
   `DocsPreferredVersionContextProvider` hook (#542 is confirmed
   fixed).
