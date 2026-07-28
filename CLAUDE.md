# Project Agent Instructions

This repo is a heavily enhanced fork of `screenshot-to-code`: screenshots / mockups /
videos are converted into working code by LLMs. The fork adds a generic provider
registry (any OpenAI-compatible endpoint, Anthropic, Gemini), per-variant model
selection, a Provider Manager UI, and a Variant Builder UI.

## Architecture map

### High-level flow

```
frontend (Vite/React, :5173)
  └─ WebSocket /generate-code ──► backend (FastAPI, :7001)
       routes/generate_code.py — middleware pipeline:
         WebSocketSetup → ParameterExtraction → StatusBroadcast
         → PromptCreation → AgenticGeneration (N parallel variants) → PostProcessing
              └─ each variant: agent/engine.py (AgentEngine tool-calling loop)
                   └─ agent/providers/* (openai_compatible | anthropic | gemini)
                   └─ agent/tools/* (create_file, edit_file, generate_images,
                                     edit_image, extract_assets, screenshot_preview)
```

### Backend modules (`backend/`)

- `main.py` — FastAPI app (lifespan startup), router registration, CORS.
- `routes/generate_code.py` — the WebSocket generation pipeline (stages above).
  Variant model resolution honors `VariantModelConfig` overrides from the UI.
- `agent/` — agentic generation core:
  - `engine.py` — `AgentEngine`: tool-calling loop (max 30 turns), streaming,
    finalization + stack-aware fallback document, and a one-shot stack repair
    pass (`_attempt_stack_repair`): non-compliant output triggers one extra
    turn via `ProviderSession.append_user_turn` asking for a conversion;
    failure keeps the original output. After each `run()` the engine exposes
    `last_cost_usd` / `last_token_usage` (captured before the provider session
    closes; None/absent for fakes without usage reporting).
  - `variant_config.py` — `VariantModelConfig` (family, model_id, label,
    api_key, base_url, reasoning_effort) — per-variant model overrides.
    `parse_variant_model_config(dict)` validates the wire-format dict from the
    frontend (raises a readable `ValueError` instead of `KeyError`).
  - `providers/` — provider sessions; `factory.py` picks by family/keys
    (Anthropic/Gemini models fall back to the OpenAI-compatible gateway when
    only an openai key + base_url exist). `openai_compatible.py` serves any
    gateway (OmniRoute, OpenRouter, Ollama, ...): requests
    `stream_options: {include_usage}` for token/cost accounting (dropped once
    and retried if the endpoint rejects it), retries transient
    connection/5xx errors with backoff before streaming starts, prices
    gateway-prefixed model ids by stripping path segments, and builds its
    client with an explicit connect timeout. Tests:
    `backend/tests/test_openai_compatible.py`.
  - `tools/` — canonical tool definitions + runtime (file state, images, assets).
- `prompts/` — prompt construction:
  - `pipeline.py` — `build_prompt_messages(...)` entry point.
  - `plan.py` — picks the construction strategy (create_from_input,
    update_from_history, update_from_file_snapshot).
  - `system_prompt.py` — **per-stack** system prompts via
    `build_system_prompt(stack)`; only the selected stack's section is included.
  - `policies.py` — `build_selected_stack_policy(stack)` (rich stack policy line
    in user messages) + image-generation policies.
  - `create/`, `update/` — message builders per input mode. `create/image.py`
    appends one image part per screenshot; with 2+ screenshots it adds a
    "Multiple screenshots" section instructing hash-based navigation
    (`#/page-1`, …) with Screenshot 1 as the default page (multi-page flows in
    a single file). Tests: `backend/tests/test_prompts.py`.
- `codegen/utils.py` — `extract_html_content`, `build_fallback_document(inner, stack)`
  (stack-aware CDN shells), `check_stack_compliance(html, stack)`.
- `codegen/project_export.py` — `build_project_files(html, stack)`: deterministic
  Vite project scaffold for `/api/export` with `format="project"` (all stacks get
  package.json/README/.gitignore; `react_tailwind` additionally extracts the
  inline `text/babel` JSX into `src/main.jsx` with a `@vitejs/plugin-react`
  toolchain, and when 2+ top-level `function Component()` declarations parse
  cleanly it splits them into `src/components/<Name>.jsx` files imported from
  `src/main.jsx`; each step silently falls back to the previous tier if the
  document shape differs).
  Tests: `backend/tests/test_project_export.py`.
- `routes/export.py` — `/api/export` ZIP download (asset fetching/rewriting +
  `format: "single" | "project"`).
- `visual_verification/` — deterministic variant scoring: after a variant
  completes in an image-based create flow, its HTML is rendered with the
  preview-screenshot backend and compared to the input screenshot
  (pure-Pillow color + dHash blend, 0..1). The score streams to the UI as a
  `variantScore` WS message; every failure is silently skipped (best effort,
  never blocks a variant). Tests: `backend/tests/test_visual_scoring.py`.
- `llm.py` — model enums/registry; `config.py` — env-driven settings.
- `evals/` — eval harness. `evals/core.py` passes `stack` to the Agent so eval
  outputs get the same stack-repair pass as production; `evals/runner.py`
  writes a mergeable `stack_compliance.json` per result folder via
  `record_stack_compliance` (A/B regression artifact; re-runs update the file
  map and recompute the rate). Tests: `backend/tests/test_eval_metrics.py`.
- `image_generation/`, `video/`, `fs_logging/`, `costs/` — supporting
  subsystems (image gen via Replicate/OpenAI, video-mode input,
  filesystem run logging, token/cost accounting).

### Stack system (important invariant)

- `Stack` literal lives in `backend/prompts/prompt_types.py` and must stay in
  sync with `frontend/src/lib/stacks.ts`.
- Supported: `html_css`, `html_tailwind`, `react_tailwind`, `bootstrap`,
  `ionic_tailwind`, `vue_tailwind`.
- Output is always a **single HTML file**; frameworks load via CDN
  (React via UMD + Babel standalone, Vue global build, Bootstrap CSS/JS, Ionic).
  The Vite project export converts that file into a runnable project after the
  fact — generation itself stays single-file.
- When adding a stack: update `prompt_types.py`, `system_prompt.py`
  (`_STACK_SECTIONS`), `policies.py` (display name + requirement),
  `codegen/utils.py` (head includes + compliance markers), and the frontend list.
  Tests in `backend/tests/test_stack_system_prompt.py` and
  `backend/tests/test_stack_repair.py` lock this behavior in.

### Frontend (`frontend/src/`)

- `App.tsx` — top-level layout, settings/design-system state, modals.
- `hooks/useGenerationOrchestrator.ts` — all generation orchestration
  (WS lifecycle, commits/variants, create/update/import/regenerate); App.tsx
  consumes it, so new generation behavior belongs in the hook, not App.tsx.
- `generateCode.ts` — WebSocket client for the generation stream. The
  `WebSocketResponse` type union must stay in sync with `MessageType` in
  `backend/routes/generate_code.py` (includes `variantScore` → similarity
  badge on variant thumbnails in `components/variants/Variants.tsx`).
  `variantComplete` optionally carries a usage payload
  (`{inputTokens, outputTokens, totalTokens, costUsd?}` from the engine's
  `last_token_usage`/`last_cost_usd`) → cost/token badge per variant in
  `Variants.tsx`, stored via `setVariantUsage` in the project store.
- `components/settings/` — Settings dialog, Provider Manager, Variant Builder.
- `store/` — Zustand stores (app/project/settings state).
- `lib/stacks.ts`, `lib/models.ts` — stack and model metadata shown in the UI.

## Python environment

- Always use the backend Poetry virtualenv for Python commands.
- Preferred invocation: `cd backend && poetry run <command>`.
- If you need to activate directly, use Poetry to discover it in the current environment:
  - `cd backend && poetry env activate` (then run the `source .../bin/activate` command it prints)
- **Windows fallback (this machine):** if `poetry` is not on PATH, the global
  Python has all deps installed — run `cd backend; python -m pytest -q` and
  `npx --yes pyright <files>` instead.

## Testing policy

- Always run backend tests after every code change: `cd backend && poetry run pytest`.
- Always run type checking after every code change: `cd backend && poetry run pyright`.
- Type checking policy: no new warnings in changed files (`pyright`).

## Frontend

- Frontend: `cd frontend && pnpm lint`

If changes touch both, run both sets.

## Prompt formatting

- Prefer triple-quoted strings (`"""..."""`) for multi-line prompt text.
- For interpolated multi-line prompts, prefer a single triple-quoted f-string over concatenated string fragments.
- System prompts are per-stack: never re-introduce a single "all stacks" prompt;
  extend `_STACK_SECTIONS` in `prompts/system_prompt.py` instead.

## Running the app

- Backend (FastAPI + WebSocket): from `backend/`, `poetry run uvicorn main:app --reload --port 7001`.
- Frontend (Vite/React): from `frontend/`, `pnpm dev` → open `http://localhost:5173`.
  The Vite dev server binds to `localhost` only, so use `http://localhost:5173`,
  not `http://127.0.0.1:5173` (the latter refuses the connection).
- Frontend talks to the backend over a WebSocket (`VITE_WS_BACKEND_URL`, default
  `ws://127.0.0.1:7001`); generation streams over that socket, other routes are plain HTTP.
- Windows convenience: `start-dev.bat` at the repo root launches both servers.

# Hosted

The hosted version is on the `hosted` branch. The `hosted` branch connects to a saas backend, which is a seperate codebase at ../screenshot-to-code-saas

## Cursor Cloud specific instructions

Dependencies are refreshed automatically on startup (`poetry install` in `backend/`, `pnpm install` in `frontend/`); no manual install is needed.
Cursor Cloud environment setup should run `bash /agent/repos/screenshot-to-code/scripts/cursor-cloud-install.sh`; the script changes to the repo root before installing so it works regardless of the startup working directory.

Non-obvious caveats:
- `poetry` is installed under `~/.local/bin` and is on PATH for interactive shells (`.bashrc`) but not necessarily for non-interactive scripts; use the full path `~/.local/bin/poetry` if `poetry` is not found.
- The Poetry virtualenv resolves to Python 3.12 (named like `backend-...-py3.12`), not 3.10 — `pyproject.toml` pins `^3.10`, which 3.12 satisfies. Just use `poetry run`.
- Core feature (screenshot → code) requires at least one LLM key: set `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `GEMINI_API_KEY` in `backend/.env` (restart backend after editing) or in the in-app Settings dialog. Without a key, generation fails fast with a "No OpenAI, Anthropic, or Gemini API key" message. `REPLICATE_API_KEY` (image gen/edit) only works via `backend/.env`, not the UI.
- Playwright Chromium is pre-installed for the optional "Screenshot preview" tool; Settings shows it as "Available".
- `pnpm install` prints an "Ignored build scripts (esbuild, puppeteer)" warning — this is harmless; Vite build/dev and tests work without approving builds.
- `cd frontend && pnpm lint` currently reports pre-existing errors (e.g. `@typescript-eslint/no-explicit-any` in `generateCode.ts`) because lint runs with `--max-warnings 0`; these are baseline issues, not environment problems.
