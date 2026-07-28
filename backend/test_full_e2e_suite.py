"""Comprehensive E2E integration runner.

Tests:
1. Backend WebSocket generate-code stream with OmniRoute provider settings.
2. Multiple variant configs streaming code concurrently.
3. Verifies setCode events emit actual non-empty HTML.
4. Verifies variantComplete finishes successfully with 0 errors.
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


async def test_single_variant_omniroute() -> bool:
    print("--- Test 1: Single OmniRoute Variant ---")
    payload = {
        "generatedCodeConfig": "html_tailwind",
        "inputMode": "text",
        "generationType": "create",
        "prompt": {"text": "Build a modern hero banner with blue Tailwind styling.", "images": []},
        "isImageGenerationEnabled": False,
        "isAssetExtractionEnabled": False,
        "codeGenerationModel": "omniroute auto/best-coding",
        "openAiApiKey": OMNIROUTE_KEY,
        "openAiBaseURL": OMNIROUTE_URL,
    }

    codes = {}
    completed = set()
    errors = []

    try:
        async with websockets.connect(BACKEND_WS_URL, max_size=50 * 1024 * 1024) as ws:
            await ws.send(json.dumps(payload))
            async with asyncio.timeout(90):
                async for raw in ws:
                    msg = json.loads(raw)
                    t = msg.get("type")
                    vi = msg.get("variantIndex", 0)
                    val = msg.get("value", "")

                    if t == "setCode":
                        codes[vi] = val
                    elif t == "variantComplete":
                        completed.add(vi)
                        print(f"Variant {vi} completed successfully.")
                        break
                    elif t == "error" or t == "variantError":
                        print(f"Error on variant {vi}: {val}")
                        errors.append(val)
                        break

        if errors:
            print("FAILED: Errors encountered:", errors)
            return False

        if 0 in completed and len(codes.get(0, "")) > 100:
            print(f"PASSED: Generated {len(codes[0])} bytes of HTML.")
            return True
        else:
            print("FAILED: No valid HTML code returned.")
            return False

    except Exception as e:
        print(f"FAILED: Exception {e}")
        return False


async def test_four_variants_omniroute() -> bool:
    print("\n--- Test 2: 4 Concurrent OmniRoute Variants ---")
    payload = {
        "generatedCodeConfig": "html_tailwind",
        "inputMode": "text",
        "generationType": "create",
        "prompt": {"text": "Create a responsive pricing table card.", "images": []},
        "isImageGenerationEnabled": False,
        "isAssetExtractionEnabled": False,
        "openAiApiKey": OMNIROUTE_KEY,
        "openAiBaseURL": OMNIROUTE_URL,
        "variantModelConfigs": [
            {
                "family": "openai",
                "model_id": "auto/best-coding",
                "label": "OmniRoute Variant 1",
                "api_key": OMNIROUTE_KEY,
                "base_url": OMNIROUTE_URL,
            },
            {
                "family": "openai",
                "model_id": "auto/best-coding",
                "label": "OmniRoute Variant 2",
                "api_key": OMNIROUTE_KEY,
                "base_url": OMNIROUTE_URL,
            },
            {
                "family": "openai",
                "model_id": "auto/best-coding",
                "label": "OmniRoute Variant 3",
                "api_key": OMNIROUTE_KEY,
                "base_url": OMNIROUTE_URL,
            },
            {
                "family": "openai",
                "model_id": "auto/best-coding",
                "label": "OmniRoute Variant 4",
                "api_key": OMNIROUTE_KEY,
                "base_url": OMNIROUTE_URL,
            },
        ],
    }

    codes = {}
    completed = set()
    errors = []

    try:
        async with websockets.connect(BACKEND_WS_URL, max_size=50 * 1024 * 1024) as ws:
            await ws.send(json.dumps(payload))
            async with asyncio.timeout(120):
                async for raw in ws:
                    msg = json.loads(raw)
                    t = msg.get("type")
                    vi = msg.get("variantIndex", 0)
                    val = msg.get("value", "")

                    if t == "setCode":
                        codes[vi] = val
                    elif t == "variantComplete":
                        completed.add(vi)
                        print(f"Variant {vi} completed.")
                        if len(completed) == 4:
                            break
                    elif t == "error" or t == "variantError":
                        print(f"Error on variant {vi}: {val}")
                        errors.append(val)

        if errors:
            print("FAILED: Variant errors:", errors)
            return False

        if len(completed) == 4 and all(len(codes.get(i, "")) > 100 for i in range(4)):
            print("PASSED: All 4 OmniRoute variants completed with HTML.")
            return True
        else:
            print(f"FAILED: Completed variants: {len(completed)}/4")
            return False

    except Exception as e:
        print(f"FAILED: Exception {e}")
        return False


async def main():
    t1 = await test_single_variant_omniroute()
    t2 = await test_four_variants_omniroute()

    if t1 and t2:
        print("\nALL BACKEND E2E TESTS PASSED SUCCESSFULLY!")
        sys.exit(0)
    else:
        print("\nE2E TESTS FAILED!")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
