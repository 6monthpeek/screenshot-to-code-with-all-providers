"""E2E test: hit the WebSocket endpoint with variantModelConfigs and verify
that the backend honors the per-variant OmniRoute provider.

Run while backend is on :7001 and OmniRoute is on :20128.
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
        "codeGenerationModel": "omniroute antigravity/gemini-3.6-flash-high",
        "variantModelConfigs": [
            {
                "family": "openai",
                "model_id": "antigravity/gemini-3.6-flash-high",
                "label": "OmniRoute gemini-3.6-flash-high",
                "api_key": "sk-a6ceada84a74c6a2-5f96cd-0229c046",
                "base_url": "http://localhost:20128/v1",
            }
        ],
    }

    messages: list[dict] = []
    try:
        async with websockets.connect(uri, max_size=50 * 1024 * 1024) as ws:
            await ws.send(json.dumps(payload))
            try:
                async with asyncio.timeout(60):
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        messages.append(msg)
                        t = msg.get("type")
                        v = msg.get("value")
                        vi = msg.get("variantIndex")
                        print(f"[{t}] variant={vi} value={str(v)[:120]!r}")
                        if t == "error":
                            return 2
                        if t == "variantComplete":
                            return 0
            except asyncio.TimeoutError:
                print("TIMEOUT waiting for variantComplete")
                return 3
    except Exception as e:
        print(f"WS ERROR: {type(e).__name__}: {e}")
        return 4
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
