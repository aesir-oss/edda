# Edda

A Knowledge OS: an LLM-maintained personal wiki of linked, cited Markdown pages, stored as an
[Open Knowledge Format v0.2](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md) bundle.

[![CI](https://github.com/aesir-oss/edda/actions/workflows/ci.yml/badge.svg)](https://github.com/aesir-oss/edda/actions/workflows/ci.yml)
![coverage](https://img.shields.io/badge/coverage-%E2%89%A590%25-brightgreen)
![not published](https://img.shields.io/badge/PyPI-not%20published-lightgrey)

Rust is the kernel (store, index, journal), Python runs the services (FastAPI), and a React + Vite app is the shell.
Edda is deployed from this repository and never published: the `edda` names on crates.io, PyPI and npm belong to
unrelated projects.

## Installation

```sh
pixi install
pixi run bootstrap
```

`pixi install` sets up Rust, Python 3.14 and Node from conda-forge and builds the native module; `bootstrap` installs
the pre-commit hooks and the web app's npm packages.

## Quick start

```sh
pixi run dev
```

Serves the API on <http://127.0.0.1:8000> (OpenAPI at `/docs`) and the app on <http://127.0.0.1:5173>, both reading
`examples/vault`. The app has two views: the Library (browse, filter by tag, search) and a Page view (the rendered
page, citations on hover, its sources and backlinks).

Set `EDDA_VAULT` to browse another vault. The ports are uvicorn's and Vite's defaults; when either is taken, pass
others, API first:

```sh
pixi run dev 8765 5199
```

The web dev server proxies `/api` to whichever API port you give, and fails rather than moving to another port.

The search index and link graph in `<vault>/.edda/index` are rebuilt from the files whenever they change, so
deleting them loses nothing.

## Local models

Jobs use a local model by default; nothing leaves the machine and nothing is billed.

```sh
pixi run llm-fetch            # downloads Qwen3-14B (4-bit, ~9 GB) to ~/.cache/edda/models and checks its SHA-256
pixi run -e llm llm-serve     # llama.cpp's llama-server on http://127.0.0.1:8080, 32k context, all layers on the GPU
pixi run llm-smoke            # one tool-calling round trip against the configured model
```

`pixi run -e llm llm-serve <model file> <port> <context>` changes the defaults. Ollama, mlx-lm or LM Studio work too:
point `[llm.local] base_url` in `edda.toml` at their OpenAI-compatible endpoint.

To use Claude for a job instead, set it to `"anthropic"` under `[llm]` in `edda.toml` and supply your own API key in the
environment (`ANTHROPIC_API_KEY`, or `ant auth login`). You pay Anthropic for what it uses; the key never goes in the
vault. Claude's context is much larger than a local model's, so raise `[budgets]` for those jobs.

## Development

| Task | What it runs |
|---|---|
| `pixi run test` | `cargo test`, `pytest tests/unit`, `vitest` |
| `pixi run test-integration` | `pytest tests/integration` |
| `pixi run lint` | clippy `-D warnings`, ruff, eslint |
| `pixi run check` | mypy (strict), `tsc` |
| `pixi run cov` | coverage of Rust, Python and the web app, each at 90% or more |
| `pixi run ci` | pre-commit, build, check, lint, cov |

## License

Apache-2.0
