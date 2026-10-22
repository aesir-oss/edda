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

Serves the API on <http://127.0.0.1:8000> (OpenAPI at `/docs`) and the app on <http://127.0.0.1:5173>. The app has
three views: the Library (browse, filter, search, add a Markdown file or a web page), a Page view (the rendered page,
citations on hover, its sources and backlinks) and Review (the model's proposals, to accept, edit or reject).

Both serve `.edda/dev-vault`, a copy of `examples/vault` made on the first run and kept between runs, so what you
import or accept never changes the committed example. To start again from a fresh copy:

```sh
pixi run dev-init
```

The ports are uvicorn's and Vite's defaults; when either is taken, pass others, API first (`dev-init` takes them too):

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
add them under `[llm.endpoints]` in `edda.toml` with their OpenAI-compatible `base_url`.

### Hosted models, with your own key

Each job in `[llm]` names an endpoint or `anthropic`. Hosted models send your sources off the machine, and keys come
from the environment, never from `edda.toml` or the vault.

| Provider | Set a job to | Key | Model |
|---|---|---|---|
| NVIDIA (Nemotron) | `"nvidia"` | `NVIDIA_API_KEY`, from build.nvidia.com | `nvidia/nemotron-3-ultra-550b-a55b`; `nvidia/nemotron-3.5-lightning-30b-a3b` is faster. Which models an account can call varies |
| Anthropic (Claude) | `"anthropic"` | `ANTHROPIC_API_KEY`, or `ant auth login` | Sonnet 5.5, Opus 5.5 or Haiku 5.5 per job |

```sh
export NVIDIA_API_KEY=...
pixi run llm-smoke examples/vault nvidia   # check the endpoint before setting a job to it
```

Hosted models have much larger contexts than a local one, so raise `[budgets]` for those jobs; Anthropic bills by the
token, and NVIDIA's terms and rate limits are theirs to set.

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
