# Test artifacts

Pinned reference values consumed by the pytest suite.  Each file in
this directory is the authoritative expected value for one test;
drift here is never silent.

## `app.built.css.sha256`

sha256 of the Tailwind-compiled stylesheet at
`src/houndarr/static/css/app.built.css`.  The bundle itself is
gitignored (regenerated in the Dockerfile `css-build` stage), but
the hash is pinned so any intentional CSS change has to land with
a recorded rationale.

`tests/test_build/test_css_hash_pinning.py` reads this file and
compares against the current build.  The test skips when the bundle
is absent (e.g. a dev checkout that has not run Tailwind), so a
fresh clone with no Node toolchain installed does not break the
default suite.

### Refresh policy

The one-line format mirrors `sha256sum`: `<64 hex chars>  <relative
path>`.  No trailing whitespace other than the single newline at
end-of-file.

Refresh whenever a commit intentionally changes:

- `src/houndarr/static/css/input.css` (new @layer components rules,
  new @utility rules, daisyUI overrides, @theme tokens)
- `src/houndarr/static/css/tokens.css` (CSS variable source of truth)
- `src/houndarr/static/css/app.css` (non-Tailwind component rules)
- `src/houndarr/static/css/auth.css` / `auth-fields.css` (currently
  off-limits, but a rebuild still has to be recorded if a comment
  addition somehow mutates the output)
- `src/houndarr/templates/**/*.html` (Tailwind v4 scans templates
  for utility names, so a new class appearing anywhere in a
  template can emit a new rule into the bundle)

The refresh sequence:

```bash
pnpm run build-css
shasum -a 256 src/houndarr/static/css/app.built.css \
  > tests/_artifacts/app.built.css.sha256
```

The commit that refreshes the pin must explain what changed and why
the new hash is expected.  A hash delta without rationale is a
warning sign: either the template scanner picked up a stray class
name, or daisyUI silently shifted its output.  Either way, the
reviewer wants to know before the pin moves.

### Comment-only CSS edits

Tailwind strips comments during minification.  A commit that only
adds or rewords comments inside a `.css` file will produce a
byte-equal `app.built.css` and the pin does not need to move.  The
commit body should still note that the rebuild was checked (matching
baseline) so later readers can trust the hash stability.
