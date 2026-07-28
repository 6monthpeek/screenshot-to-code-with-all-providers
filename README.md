# Screenshot to Code — All Providers Edition

> A heavily reworked fork of [abi/screenshot-to-code](https://github.com/abi/screenshot-to-code):
> screenshots, mockups and screen recordings become working code — through **any**
> model provider you choose, with per-variant model selection, cost tracking and
> measurable output quality.

https://github.com/user-attachments/assets/ec08a5e6-9606-41c5-b03a-1bf47dfeba75

## Why we rebuilt this repo

The upstream project is excellent, but it is built around a fixed, hardcoded set
of official providers (OpenAI, Anthropic, Gemini). In practice that model no
longer matches how people actually run LLMs today:

- **Models live behind gateways now.** OmniRoute, OpenRouter, Groq, Together,
  Fireworks, Ollama, LM Studio, vLLM — most teams reach models through an
  OpenAI-compatible endpoint, not through three official SDKs. Upstream had no
  first-class way to say *"use this base URL with this model id"*.
- **One model per run is a wasted comparison.** The app generates several
  variants in parallel, but upstream picks the models for you. We wanted each
  variant slot to be independently configurable — GPT vs Claude vs a local
  model, side by side, from the UI.
- **No visibility into cost or quality.** Generations ran with no token/cost
  accounting per variant, no budget ceiling, and no objective measure of how
  close the output looks to the input screenshot.
- **Single-file output stopped at the demo stage.** The generated HTML file is
  great for previewing, but there was no path from "demo file" to "runnable
  Vite project" or a component library.

So instead of maintaining a thin patch set, we changed the architecture where
it mattered: a generic provider registry, per-variant model configs threaded
end-to-end through the WebSocket pipeline, an agentic tool-calling engine with
stack enforcement and repair, plus measurement (visual scores, cost badges,
eval metrics) so changes can be verified instead of eyeballed.

## What this fork adds on top of upstream

| Area | Upstream | This fork |
|------|----------|-----------|
| Providers | OpenAI, Anthropic, Gemini (hardcoded) | Any OpenAI-compatible endpoint (OmniRoute, OpenRouter, Groq, Ollama, LM Studio, vLLM, …) + Anthropic + Gemini, managed from a **Provider Manager UI** |
| Model choice | Fixed model mix | **Per-variant model selection** via the Variant Builder UI — each generation slot gets its own provider, model id, base URL and key |
| Gateway support | Basic `OPENAI_BASE_URL` proxying | First-class gateway sessions: streamed tool calling, `include_usage` token accounting, transient-error retries with backoff, connect timeouts, gateway-aware error messages, graceful fallback when a native key is missing |
| Cost | None | Per-variant token/cost badges in the UI, `$` budget ceiling per generation (`GENERATION_MAX_COST_USD`), priced gateway model ids |
| Output quality | Manual inspection | **Visual similarity score** per variant (rendered output vs input screenshot), stack-compliance metric in the eval harness |
| Stack fidelity | Prompt-only | Per-stack system prompts + post-generation **stack repair pass** (non-compliant output triggers one conversion turn) + stack-aware fallback documents |
| Export | Single HTML file | **Deterministic Vite project export**: package.json/toolchain scaffold, JSX extraction into `src/main.jsx`, automatic **component split** into `src/components/*.jsx` |
| Multi-screenshot input | One page per run | Multiple screenshots become a **multi-page app** in one file (hash-based navigation, shared nav) |
| Tests | Sparse | 350+ backend unit tests covering the provider sessions, prompt pipeline, stack system, export, scoring and eval metrics |

Everything is documented for agents and humans alike in
[AGENTS.md](./AGENTS.md) (architecture map, invariants, testing policy).

## Supported stacks

HTML + Tailwind · HTML + CSS · React + Tailwind · Vue + Tailwind · Bootstrap · Ionic + Tailwind

Generation always produces a single self-contained HTML file (frameworks via
CDN); the project export converts it into a runnable Vite project afterwards.

## Getting started

The app is a React/Vite frontend (`:5173`) talking to a FastAPI backend
(`:7001`) over a WebSocket.

### 1. Credentials

You need **at least one** working model source. That can be:

- an official key: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` or `GEMINI_API_KEY`, **or**
- any OpenAI-compatible gateway: set `OPENAI_BASE_URL` (e.g.
  `http://localhost:20128/v1` for a local OmniRoute) plus its key, **or**
- a local model server (Ollama, LM Studio): point a provider at its base URL —
  no real key required.

| Key | Required? | What it unlocks |
|-----|-----------|-----------------|
| `OPENAI_API_KEY` (+ optional `OPENAI_BASE_URL`) | one of these | GPT variants, or *any* model behind an OpenAI-compatible gateway |
| `ANTHROPIC_API_KEY` | one of these | Claude code-gen variants |
| `GEMINI_API_KEY` | one of these — **recommended** | Gemini variants, real asset extraction from screenshots, video mode |
| `REPLICATE_API_KEY` | recommended | Image generation, editing and background removal |

Keys can also be entered in the in-app Settings dialog (gear icon), where you
can add any number of custom providers and assign them to variant slots.
`REPLICATE_API_KEY` works via `backend/.env` only.

> Never commit real keys. `backend/.env` is gitignored; the E2E scripts read
> `OMNIROUTE_API_KEY` / `OPENAI_API_KEY` from the environment.

### 2. Backend

```bash
cd backend
echo "OPENAI_API_KEY=sk-your-key" > .env
poetry install
# Chromium for the optional screenshot-preview tool (agent checks its own work):
poetry run playwright install chromium
poetry run uvicorn main:app --reload --port 7001
```

### 3. Frontend

```bash
cd frontend
pnpm install
pnpm dev
```

Open http://localhost:5173. If the backend runs elsewhere, set
`VITE_WS_BACKEND_URL` / `VITE_HTTP_BACKEND_URL` in `frontend/.env.local`.

### Docker

```bash
echo "OPENAI_API_KEY=sk-your-key" > .env
docker-compose up -d --build
```

## Using a gateway (OmniRoute / OpenRouter / local models)

1. Open Settings → Provider Manager → add a provider with its base URL
   (e.g. `http://localhost:20128/v1`) and key.
2. Open the Variant Builder and assign a `model_id` per variant slot
   (e.g. `antigravity/gemini-3.6-flash-high`, `auto/best-coding`).
3. Generate — each variant streams from its own provider, with token/cost
   badges when the gateway reports usage.

Anthropic/Gemini model choices automatically fall back to the gateway when
only a gateway credential is configured.

## Testing

```bash
cd backend && poetry run pytest      # 350+ unit tests
cd backend && poetry run pyright     # type checking
cd frontend && pnpm lint
```

## Credits & license

Built on [abi/screenshot-to-code](https://github.com/abi/screenshot-to-code) —
all credit for the original product concept and foundation goes to its authors.
The easiest way to try the upstream product is the hosted app at
[screenshottocode.com](https://screenshottocode.com). Licensed under the same
terms as upstream (see [LICENSE](./LICENSE)).
