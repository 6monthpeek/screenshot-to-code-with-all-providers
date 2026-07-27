"""E2E test: no variantModelConfigs, no .env — credentials come only from the
Settings-style params (openAiApiKey + openAiBaseURL), exactly what the frontend
now sends when an enabled openai provider exists and no manual key is set.

Expectation: backend must NOT throw "No API key" and must route the single
codeGenerationModel through OmniRoute at the provided base URL.

Run while backend (without .env) is on :7001 and OmniRoute is on :20128.
"""

import asyncio
import json
import sys

import websockets


async def main() -> int:
    uri = "ws://127.0.0.1:7001/generate-code"
    payload = {
        "generatedCodeConfig": "html_tailwind",
        "inputMode": "text",
        "generationType": "create",
        "prompt": {"text": "Build a simple hello-world page with a centered heading.", "images": []},
        "isImageGenerationEnabled": False,
        "isAssetExtractionEnabled": False,
        # No variantModelConfigs — legacy selection path.
        "codeGenerationModel": "omniroute antigravity/gemini-3.6-flash-high",
        # What App.tsx sends when an enabled OmniRoute provider exists:
        "openAiApiKey": "sk-a6ceada84a74c6a2-5f96cd-0229c046",
        "openAiBaseURL": "http://localhost:20128/v1",
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
