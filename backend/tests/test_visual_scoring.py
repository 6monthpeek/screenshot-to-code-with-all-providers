"""Tests for the visual verification scoring pass.

Covers the pure similarity metric, the render+compare helper, and the
_score_variant gating logic in the WS pipeline stage.
"""

import base64
import io
from typing import Any, Dict, List

import pytest
from PIL import Image

from routes.generate_code import AgenticGenerationStage
from visual_verification import (
    compute_visual_similarity,
    decode_image_data_url,
    score_generated_page,
)


def _png_bytes(color: tuple[int, int, int], size: tuple[int, int] = (100, 80)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _png_data_url(color: tuple[int, int, int]) -> str:
    encoded = base64.b64encode(_png_bytes(color)).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _split_png(top: tuple[int, int, int], bottom: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (100, 80), top)
    image.paste(Image.new("RGB", (100, 40), bottom), (0, 40))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class TestComputeVisualSimilarity:
    def test_identical_images_score_one(self) -> None:
        image = _png_bytes((30, 120, 200))
        assert compute_visual_similarity(image, image) == 1.0

    def test_opposite_images_score_low(self) -> None:
        white = _png_bytes((255, 255, 255))
        black = _png_bytes((0, 0, 0))
        assert compute_visual_similarity(white, black) < 0.6

    def test_closer_image_scores_higher(self) -> None:
        reference = _split_png((255, 255, 255), (20, 20, 20))
        close = _split_png((250, 250, 250), (30, 30, 30))
        far = _split_png((20, 20, 20), (255, 255, 255))

        close_score = compute_visual_similarity(reference, close)
        far_score = compute_visual_similarity(reference, far)
        assert close_score > far_score

    def test_score_is_bounded_and_rounded(self) -> None:
        score = compute_visual_similarity(
            _png_bytes((10, 10, 10)), _png_bytes((200, 100, 50))
        )
        assert 0.0 <= score <= 1.0
        assert score == round(score, 3)


class TestDecodeImageDataUrl:
    def test_decodes_valid_png_data_url(self) -> None:
        raw = _png_bytes((1, 2, 3))
        encoded = base64.b64encode(raw).decode("ascii")
        assert decode_image_data_url(f"data:image/png;base64,{encoded}") == raw

    def test_rejects_non_image_urls(self) -> None:
        assert decode_image_data_url("https://example.com/a.png") is None
        assert decode_image_data_url("data:text/plain;base64,aGk=") is None
        assert decode_image_data_url("data:image/png") is None


class TestScoreGeneratedPage:
    @pytest.mark.asyncio
    async def test_scores_rendered_page_against_reference(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rendered = _png_bytes((30, 120, 200))

        async def fake_capture(html: str, device: str, full_page: bool) -> bytes:
            assert device == "desktop"
            return rendered

        monkeypatch.setattr(
            "visual_verification.scoring.capture_preview_screenshot", fake_capture
        )

        score = await score_generated_page("<html></html>", _png_data_url((30, 120, 200)))
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_invalid_reference_returns_none(self) -> None:
        assert await score_generated_page("<html></html>", "not-a-data-url") is None

    @pytest.mark.asyncio
    async def test_render_failure_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def failing_capture(html: str, device: str, full_page: bool) -> bytes:
            raise RuntimeError("chromium exploded")

        monkeypatch.setattr(
            "visual_verification.scoring.capture_preview_screenshot", failing_capture
        )

        score = await score_generated_page("<html></html>", _png_data_url((0, 0, 0)))
        assert score is None


def _build_stage(
    sent: List[tuple[str, str | None, int, Dict[str, Any] | None]],
    *,
    generation_type: str = "create",
    input_mode: str = "image",
    reference_images: List[str] | None = None,
) -> AgenticGenerationStage:
    async def send_message(
        msg_type: Any,
        value: str | None,
        variant_index: int,
        data: Dict[str, Any] | None = None,
        event_id: str | None = None,
    ) -> None:
        sent.append((str(msg_type), value, variant_index, data))

    return AgenticGenerationStage(
        send_message=send_message,
        openai_api_key=None,
        openai_base_url=None,
        anthropic_api_key=None,
        gemini_api_key="key",
        replicate_api_key=None,
        should_generate_images=False,
        file_state=None,
        asset_base_url="",
        option_codes=[],
        stack="html_tailwind",
        input_mode=input_mode,
        generation_type=generation_type,
        reference_images=(
            reference_images
            if reference_images is not None
            else [_png_data_url((10, 20, 30))]
        ),
    )


class TestScoreVariantGating:
    @pytest.mark.asyncio
    async def test_sends_variant_score_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent: List[tuple[str, str | None, int, Dict[str, Any] | None]] = []
        stage = _build_stage(sent)

        async def fake_score(html: str, reference: str) -> float:
            return 0.87

        monkeypatch.setattr("routes.generate_code.score_generated_page", fake_score)
        monkeypatch.setattr(
            "routes.generate_code.is_screenshot_preview_available", lambda: True
        )

        await stage._score_variant(1, "<html></html>")

        assert sent == [("variantScore", None, 1, {"score": 0.87})]

    @pytest.mark.asyncio
    async def test_skips_update_flows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sent: List[tuple[str, str | None, int, Dict[str, Any] | None]] = []
        stage = _build_stage(sent, generation_type="update")
        monkeypatch.setattr(
            "routes.generate_code.is_screenshot_preview_available", lambda: True
        )

        await stage._score_variant(0, "<html></html>")
        assert sent == []

    @pytest.mark.asyncio
    async def test_skips_non_image_modes_and_missing_reference(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "routes.generate_code.is_screenshot_preview_available", lambda: True
        )
        sent: List[tuple[str, str | None, int, Dict[str, Any] | None]] = []

        await _build_stage(sent, input_mode="text")._score_variant(0, "<html></html>")
        await _build_stage(sent, reference_images=[])._score_variant(0, "<html></html>")
        assert sent == []

    @pytest.mark.asyncio
    async def test_skips_when_renderer_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent: List[tuple[str, str | None, int, Dict[str, Any] | None]] = []
        stage = _build_stage(sent)
        monkeypatch.setattr(
            "routes.generate_code.is_screenshot_preview_available", lambda: False
        )

        await stage._score_variant(0, "<html></html>")
        assert sent == []

    @pytest.mark.asyncio
    async def test_none_score_sends_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent: List[tuple[str, str | None, int, Dict[str, Any] | None]] = []
        stage = _build_stage(sent)

        async def fake_score(html: str, reference: str) -> None:
            return None

        monkeypatch.setattr("routes.generate_code.score_generated_page", fake_score)
        monkeypatch.setattr(
            "routes.generate_code.is_screenshot_preview_available", lambda: True
        )

        await stage._score_variant(0, "<html></html>")
        assert sent == []

    @pytest.mark.asyncio
    async def test_scoring_exception_is_swallowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent: List[tuple[str, str | None, int, Dict[str, Any] | None]] = []
        stage = _build_stage(sent)

        async def exploding_score(html: str, reference: str) -> float:
            raise RuntimeError("boom")

        monkeypatch.setattr(
            "routes.generate_code.score_generated_page", exploding_score
        )
        monkeypatch.setattr(
            "routes.generate_code.is_screenshot_preview_available", lambda: True
        )

        await stage._score_variant(0, "<html></html>")
        assert sent == []
