"""Visual verification: deterministic variant scoring against the input
screenshot. See scoring.py for the metric details."""

from visual_verification.scoring import (
    compute_visual_similarity,
    decode_image_data_url,
    score_generated_page,
)

__all__ = [
    "compute_visual_similarity",
    "decode_image_data_url",
    "score_generated_page",
]
