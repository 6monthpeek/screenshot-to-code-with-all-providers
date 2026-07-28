"""Deterministic visual similarity scoring between two page images.

Used by the variant scoring pass: after a variant completes, its HTML is
rendered with the preview-screenshot backend and compared against the
original input screenshot. Pure Pillow (no numpy) and fully deterministic,
so scores are cheap, reproducible and testable.

The score blends two signals:

- Color similarity: mean absolute RGB difference on small thumbnails.
  Captures palette/background/layout-block placement.
- Structural similarity: difference-hash (dHash) Hamming similarity on a
  grayscale grid. Captures edges and layout structure regardless of color.

Both images are resized to the same fixed grid first, so differing page
heights (full-page renders vs. cropped screenshots) degrade the score
gracefully instead of breaking the comparison.
"""

import base64
import io
from typing import Sequence, cast

from PIL import Image

from preview_screenshot import capture_preview_screenshot

_COLOR_THUMB_SIZE = 32
_DHASH_SIZE = 16
# Color and structure carry equal weight; neither alone separates a good
# reproduction from a bad one (color misses moved blocks, dHash misses hue).
_COLOR_WEIGHT = 0.5
_STRUCTURE_WEIGHT = 0.5


def _color_similarity(image_a: Image.Image, image_b: Image.Image) -> float:
    size = (_COLOR_THUMB_SIZE, _COLOR_THUMB_SIZE)
    thumb_a = image_a.convert("RGB").resize(size, Image.Resampling.LANCZOS)
    thumb_b = image_b.convert("RGB").resize(size, Image.Resampling.LANCZOS)

    pixels_a = cast(Sequence[tuple[int, int, int]], list(thumb_a.getdata()))
    pixels_b = cast(Sequence[tuple[int, int, int]], list(thumb_b.getdata()))
    total_diff = 0
    for pixel_a, pixel_b in zip(pixels_a, pixels_b):
        total_diff += (
            abs(pixel_a[0] - pixel_b[0])
            + abs(pixel_a[1] - pixel_b[1])
            + abs(pixel_a[2] - pixel_b[2])
        )
    mean_diff = total_diff / (_COLOR_THUMB_SIZE * _COLOR_THUMB_SIZE * 3)
    return 1.0 - mean_diff / 255.0


def _dhash_bits(image: Image.Image) -> list[int]:
    gray = image.convert("L").resize(
        (_DHASH_SIZE + 1, _DHASH_SIZE), Image.Resampling.LANCZOS
    )
    pixels = cast(Sequence[int], list(gray.getdata()))
    bits: list[int] = []
    for row in range(_DHASH_SIZE):
        row_start = row * (_DHASH_SIZE + 1)
        for col in range(_DHASH_SIZE):
            bits.append(1 if pixels[row_start + col] > pixels[row_start + col + 1] else 0)
    return bits


def _structure_similarity(image_a: Image.Image, image_b: Image.Image) -> float:
    bits_a = _dhash_bits(image_a)
    bits_b = _dhash_bits(image_b)
    matches = sum(1 for a, b in zip(bits_a, bits_b) if a == b)
    return matches / len(bits_a)


def compute_visual_similarity(png_a: bytes, png_b: bytes) -> float:
    """Similarity of two encoded images in [0.0, 1.0] (1.0 = identical)."""
    image_a = Image.open(io.BytesIO(png_a))
    image_b = Image.open(io.BytesIO(png_b))
    score = (
        _COLOR_WEIGHT * _color_similarity(image_a, image_b)
        + _STRUCTURE_WEIGHT * _structure_similarity(image_a, image_b)
    )
    return round(min(1.0, max(0.0, score)), 3)


def decode_image_data_url(data_url: str) -> bytes | None:
    """Decode a data:image/... URL to raw bytes; None if it isn't one."""
    if not data_url.startswith("data:image/") or "," not in data_url:
        return None
    try:
        _, encoded = data_url.split(",", 1)
        return base64.b64decode(encoded)
    except Exception:
        return None


async def score_generated_page(html: str, reference_data_url: str) -> float | None:
    """Render ``html`` and score it against the reference screenshot.

    Best effort: any failure (renderer down, undecodable reference, corrupt
    image) returns None so callers can simply skip the score.
    """
    reference = decode_image_data_url(reference_data_url)
    if reference is None:
        return None
    try:
        rendered = await capture_preview_screenshot(
            html, device="desktop", full_page=True
        )
        return compute_visual_similarity(rendered, reference)
    except Exception as exc:
        print(f"[VISUAL] Variant scoring failed: {exc}")
        return None
