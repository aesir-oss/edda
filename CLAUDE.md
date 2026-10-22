# CLAUDE.md

Project guidance for aesir-oss/edda. The global CLAUDE.md (toolchain, harness, git workflow) applies; this file covers
what is specific here. The design is the Edda architecture document (draft 0.2); M1 is files, history and read-only
browsing, and nothing in M1 calls an LLM.

## Architecture

| Path | What it is |
|---|---|
| `crates/edda-store/` | Reads and writes the vault: `raw/` content-addressed by SHA-256, `wiki/` as the OKF v0.2 bundle. The only code that knows the page format |
| `crates/edda-py/` | PyO3 bindings, built by maturin into `edda._kernel`; a thin layer, tested from Python |
| `python/src/edda/` | The Python package, installed editable by pixi |
| `web/` | Vite + React + TypeScript shell |
| `examples/vault/` | A small hand-written vault the tests and `pixi run dev` use |
| `tests/unit`, `tests/integration` | Python tests; Rust tests live in each crate, web tests next to their components |

## Commands

Everything runs through pixi: `pixi run test`, `lint`, `check`, `cov`, `ci`, `dev`. After changing Rust,
`pixi run native` rebuilds the extension in place (the Python tasks depend on it).

## Off-limits and traps

- Never published. `publish = false` is set once in `[workspace.package]`; every crate uses `publish.workspace = true`
  and depends on siblings by path. Never add a registry dependency named `edda*`: crates.io, PyPI and npm have
  unrelated packages with those names. `web/package.json` stays `"private": true`.
- The web app is on TypeScript 5.9 because openapi-typescript does not accept 6 yet.
- `cov-rust` excludes `edda-py`: cargo cannot test PyO3 code without an interpreter, so the Python suite covers it.

## BMad Method

- Installed from conda-forge into its own pixi environment: `pixi run -e bmad bmad-install` installs or updates
  core, bmm, tea, bmb and cis for Claude Code (skills in `.claude/skills/`, modules in `_bmad/`, artifacts in
  `_bmad-output/`). Both directories are installer-managed: change answers by re-running the task, or pin values in
  `_bmad/custom/config.toml`.
- BMad skills and scripts say `uv run <script>.py` or `uv run --with pytest ...`. Run them as
  `pixi run -e bmad python <script>.py` (and `pixi run pytest ...` for their tests); never `uv`.
- `_bmad/` and `.claude/skills/` are excluded from ruff and the whitespace hooks; they are BMad's files, not ours.
