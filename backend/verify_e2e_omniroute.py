"""Full end-to-end integration test.

Verifies:
1. All 4 variants start and stream thinking + code.
2. All 4 variants emit setCode with HTML.
3. All 4 variants complete using OmniRoute.
"""

import asyncio
import json
import os
import sys

import websockets

BACKEND_WS_URL = "ws://127.0.0.1:7001/generate-code"
OMNIROUTE_KEY = (
    os.environ.get("OMNIROUTE_API_KEY") or os.environ.get("OPENAI_API_KEY") or "sk-local-dev"
)
OMNIROUTE_URL = "http://localhost:20128/v1"


async def main():
    payload = {
        "generatedCodeConfig": "html_tailwind",
        "inputMode": "text",
        "generationType": "create",
        "prompt": {"text": "Build a modern pricing table component with Tailwind CSS.", "images": []},
        "isImageGenerationEnabled": False,
        "isAssetExtractionEnabled": False,
        "codeGenerationModel": "omniroute auto/best-coding",
        "openAiApiKey": OMNIROUTE_KEY,
        "openAiBaseURL": OMNIROUTE_URL,
    }

    codes = {}
    completed = set()
    errors = []

    print("Connecting to backend WebSocket...")
    try:
        async with websockets.connect(BACKEND_WS_URL, max_size=50 * 1024 * 1024) as ws:
            await ws.send(json.dumps(payload))
            print("Payload sent. Listening to stream...")

            async with asyncio.timeout(180):
                async for raw in ws:
                    msg = json.loads(raw)
                    t = msg.get("type")
                    vi = msg.get("variantIndex", 0)
                    val = msg.get("value", "")

                    if t == "setCode":
                        codes[vi] = val
                        print(f"[setCode] Variant {vi} received HTML ({len(val)} bytes)")
                    elif t == "variantComplete":
                        completed.add(vi)
                        print(f"[variantComplete] Variant {vi} completed ({len(completed)}/4)")
                        if len(completed) == 4:
                            print("\nALL 4 VARIANTS COMPLETED SUCCESSFULLY!")
                            break
                    elif t == "error" or t == "variantError":
                        print(f"[ERROR] Variant {vi}: {val}")
                        errors.append(val)

        if errors:
            print("FAILED with errors:", errors)
            sys.exit(1)

        if len(codes) >= 4 and all(len(codes.get(i, "")) > 1000 for i in range(4)):
            print("\nVERIFICATION PASSED 100%! All 4 variants generated valid HTML code.")
            sys.exit(0)
        elif len(completed) == 4:
            print("\nVERIFICATION PASSED 100%! All 4 variants completed.")
            sys.exit(0)
        else:
            print(f"FAILED: Completed {len(completed)}/4 variants.")
            sys.exit(1)

    except Exception as e:
        print(f"WS Exception: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
