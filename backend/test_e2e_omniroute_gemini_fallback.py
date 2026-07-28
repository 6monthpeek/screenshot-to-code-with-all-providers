"""E2E test for Gemini model fallback when using OmniRoute / OpenAI proxy.

When a user selects a Gemini model (or keeps the default Gemini choice) while
having an OpenAI-compatible provider (like OmniRoute) configured with no direct
Gemini API key:
- ModelSelectionStage allows the model selection (since OpenAI proxy is available).
- create_provider_session routes the Gemini model via OpenAICompatibleProviderSession
  instead of failing with "Gemini API key is missing."
"""

import asyncio
import json
import os
import sys

import websockets

OMNIROUTE_KEY = (
    os.environ.get("OMNIROUTE_API_KEY") or os.environ.get("OPENAI_API_KEY") or "sk-local-dev"
)


async def main() -> int:
    uri = "ws://127.0.0.1:7001/generate-code"
    payload = {
        "generatedCodeConfig": "html_tailwind",
        "inputMode": "text",
        "generationType": "create",
        "prompt": {"text": "Build a centered hello world hero box.", "images": []},
        "isImageGenerationEnabled": False,
        "isAssetExtractionEnabled": False,
        # Default model choice in the UI:
        "codeGenerationModel": "gemini-3-flash-preview (minimal thinking)",
        # User configured an OpenAI / OmniRoute provider in Settings:
        "openAiApiKey": OMNIROUTE_KEY,
        "openAiBaseURL": "http://localhost:20128/v1",
        # NO Gemini API key provided!
        "geminiApiKey": None,
    }

    saw_set_code = False
    try:
        async with websockets.connect(uri, max_size=50 * 1024 * 1024) as ws:
            await ws.send(json.dumps(payload))
            try:
                async with asyncio.timeout(90):
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        t = msg.get("type")
                        v = msg.get("value")
                        vi = msg.get("variantIndex")
                        print(f"[{t}] variant={vi} value={str(v)[:100]!r}")
                        if t == "error":
                            print(f"ERROR MSG: {v}")
                            return 2
                        if t == "setCode":
                            saw_set_code = True
                        if t == "variantComplete":
                            return 0 if saw_set_code else 5
            except asyncio.TimeoutError:
                print("TIMEOUT waiting for variantComplete")
                return 3
    except Exception as e:
        print(f"WS ERROR: {type(e).__name__}: {e}")
        return 4
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
