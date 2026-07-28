"""Unit tests for the generic OpenAI-compatible provider session.

This is the session OmniRoute (and OpenRouter, Groq, Ollama, ...) runs
through, so stream assembly, usage/cost accounting, retry behavior and the
factory fallback branches are locked in here without needing a live gateway.
"""

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import httpx
import openai
import pytest

from agent.providers.base import StreamEvent
from agent.providers.openai_compatible import (
    OpenAICompatibleProviderSession,
    _extract_chat_completion_usage,
    _lookup_pricing,
    create_openai_compatible_session,
)
from agent.variant_config import VariantModelConfig, parse_variant_model_config
from costs.pricing import MODEL_PRICING


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _text_chunk(text: str) -> Any:
    return SimpleNamespace(
        usage=None,
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text, tool_calls=None))],
    )


def _tool_chunk(
    index: int,
    call_id: Optional[str] = None,
    name: Optional[str] = None,
    args: Optional[str] = None,
) -> Any:
    tc = SimpleNamespace(
        index=index,
        id=call_id,
        function=SimpleNamespace(name=name, arguments=args),
    )
    return SimpleNamespace(
        usage=None,
        choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=[tc]))],
    )


def _usage_chunk(
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    cached_tokens: int = 0,
) -> Any:
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cached_tokens),
    )
    return SimpleNamespace(usage=usage, choices=[])


async def _stream_of(chunks: List[Any]) -> Any:
    for chunk in chunks:
        yield chunk


class FakeClient:
    """Mimics AsyncOpenAI: records params, streams canned chunks/errors."""

    def __init__(self, results: List[Any]):
        # Each entry is either a list of chunks (success) or an Exception.
        self._results = list(results)
        self.calls: List[Dict[str, Any]] = []
        self.closed = False

        async def create(**params: Any) -> Any:
            self.calls.append(params)
            result = self._results.pop(0)
            if isinstance(result, Exception):
                raise result
            return _stream_of(result)

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

    async def close(self) -> None:
        self.closed = True


def _make_session(
    client: FakeClient, model_id: str = "antigravity/gemini-3.6-flash-high"
) -> OpenAICompatibleProviderSession:
    return OpenAICompatibleProviderSession(
        client=client,  # type: ignore[arg-type]
        model_id=model_id,
        reasoning_effort=None,
        prompt_messages=[{"role": "user", "content": "hi"}],
        tools=[],
    )


async def _collect_events(session: OpenAICompatibleProviderSession) -> Any:
    events: List[StreamEvent] = []

    async def on_event(event: StreamEvent) -> None:
        events.append(event)

    turn = await session.stream_turn(on_event)
    return turn, events


def _http_error(cls: Any, message: str, status: int) -> Exception:
    request = httpx.Request("POST", "http://localhost:20128/v1/chat/completions")
    return cls(message, response=httpx.Response(status, request=request), body=None)


# ---------------------------------------------------------------------------
# Stream assembly
# ---------------------------------------------------------------------------


class TestStreamAssembly:
    @pytest.mark.asyncio
    async def test_text_and_tool_call_assembly(self) -> None:
        client = FakeClient(
            [
                [
                    _text_chunk("Hello "),
                    _text_chunk("world"),
                    _tool_chunk(0, call_id="call_abc", name="create_file", args='{"pa'),
                    _tool_chunk(0, args='th": "index.html"}'),
                ]
            ]
        )
        session = _make_session(client)
        turn, events = await _collect_events(session)

        assert turn.assistant_text == "Hello world"
        assert len(turn.tool_calls) == 1
        call = turn.tool_calls[0]
        assert call.id == "call_abc"
        assert call.name == "create_file"
        assert call.arguments == {"path": "index.html"}
        assert [e.text for e in events if e.type == "assistant_delta"] == [
            "Hello ",
            "world",
        ]

    @pytest.mark.asyncio
    async def test_missing_tool_call_id_gets_fallback(self) -> None:
        client = FakeClient([[_tool_chunk(0, name="edit_file", args="{}")]])
        session = _make_session(client)
        turn, _ = await _collect_events(session)

        assert turn.tool_calls[0].id.startswith("call_")

    @pytest.mark.asyncio
    async def test_malformed_arguments_become_invalid_json(self) -> None:
        client = FakeClient([[_tool_chunk(0, name="create_file", args="{not json")]])
        session = _make_session(client)
        turn, _ = await _collect_events(session)

        assert turn.tool_calls[0].arguments == {"INVALID_JSON": "{not json"}


# ---------------------------------------------------------------------------
# Usage & cost accounting
# ---------------------------------------------------------------------------


class TestUsageAccounting:
    @pytest.mark.asyncio
    async def test_request_asks_for_usage_chunk(self) -> None:
        client = FakeClient([[_text_chunk("ok")]])
        session = _make_session(client)
        await _collect_events(session)

        assert client.calls[0]["stream_options"] == {"include_usage": True}

    @pytest.mark.asyncio
    async def test_usage_chunk_accumulates_across_turns(self) -> None:
        client = FakeClient(
            [
                [_text_chunk("a"), _usage_chunk(1200, 500, 1700, cached_tokens=200)],
                [_text_chunk("b"), _usage_chunk(100, 50, 150)],
            ]
        )
        session = _make_session(client)
        await _collect_events(session)
        await _collect_events(session)

        usage = session.total_usage()
        assert usage.input == (1200 - 200) + 100
        assert usage.cache_read == 200
        assert usage.output == 550
        assert usage.total == 1850

    @pytest.mark.asyncio
    async def test_no_usage_chunk_keeps_zero_usage(self) -> None:
        client = FakeClient([[_text_chunk("ok")]])
        session = _make_session(client)
        await _collect_events(session)

        assert session.total_usage().total == 0
        assert session.total_cost_usd() is None  # unpriced model id

    @pytest.mark.asyncio
    async def test_total_cost_usd_for_priced_model(self) -> None:
        client = FakeClient([[_usage_chunk(1_000_000, 0, 1_000_000)]])
        session = _make_session(client, model_id="gpt-5.5")
        await _collect_events(session)

        cost = session.total_cost_usd()
        assert cost == pytest.approx(MODEL_PRICING["gpt-5.5"].input)

    def test_extract_usage_without_details(self) -> None:
        usage = _extract_chat_completion_usage(
            SimpleNamespace(
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
                prompt_tokens_details=None,
            )
        )
        assert usage.input == 10
        assert usage.cache_read == 0
        assert usage.total == 15

    def test_lookup_pricing_strips_gateway_prefixes(self) -> None:
        assert _lookup_pricing("gpt-5.5") is MODEL_PRICING["gpt-5.5"]
        assert _lookup_pricing("openai/gpt-5.5") is MODEL_PRICING["gpt-5.5"]
        assert (
            _lookup_pricing("omniroute/openai/gpt-5.5") is MODEL_PRICING["gpt-5.5"]
        )
        assert _lookup_pricing("auto/best-coding") is None


# ---------------------------------------------------------------------------
# Resilience
# ---------------------------------------------------------------------------


class TestResilience:
    @pytest.mark.asyncio
    async def test_stream_options_dropped_when_endpoint_rejects_it(self) -> None:
        client = FakeClient(
            [
                _http_error(
                    openai.BadRequestError,
                    "unknown parameter: stream_options",
                    400,
                ),
                [_text_chunk("ok")],
            ]
        )
        session = _make_session(client)
        turn, _ = await _collect_events(session)

        assert turn.assistant_text == "ok"
        assert "stream_options" in client.calls[0]
        assert "stream_options" not in client.calls[1]

    @pytest.mark.asyncio
    async def test_other_bad_requests_are_not_retried(self) -> None:
        client = FakeClient(
            [_http_error(openai.BadRequestError, "model does not exist", 400)]
        )
        session = _make_session(client)

        with pytest.raises(openai.BadRequestError):
            await _collect_events(session)
        assert len(client.calls) == 1

    @pytest.mark.asyncio
    async def test_transient_errors_are_retried(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sleeps: List[float] = []

        async def fake_sleep(delay: float) -> None:
            sleeps.append(delay)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        request = httpx.Request("POST", "http://localhost:20128/v1/chat/completions")
        client = FakeClient(
            [
                openai.APIConnectionError(request=request),
                _http_error(openai.InternalServerError, "bad gateway", 502),
                [_text_chunk("recovered")],
            ]
        )
        session = _make_session(client)
        turn, _ = await _collect_events(session)

        assert turn.assistant_text == "recovered"
        assert len(client.calls) == 3
        assert sleeps == [1.0, 2.0]

    @pytest.mark.asyncio
    async def test_transient_errors_give_up_after_max_retries(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_sleep(_delay: float) -> None:
            pass

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        request = httpx.Request("POST", "http://localhost:20128/v1/chat/completions")
        client = FakeClient(
            [openai.APIConnectionError(request=request) for _ in range(3)]
        )
        session = _make_session(client)

        with pytest.raises(openai.APIConnectionError):
            await _collect_events(session)
        assert len(client.calls) == 3


# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------


class TestCreateSession:
    def test_client_gets_timeout_and_retries(self) -> None:
        cfg = VariantModelConfig(
            family="openai",
            model_id="auto/best-coding",
            label="OmniRoute best coding",
            api_key="sk-test",
            base_url="http://localhost:20128/v1",
        )
        session = create_openai_compatible_session(
            cfg=cfg, prompt_messages=[], tools=[]
        )
        client = session._client  # type: ignore[reportPrivateUsage]

        assert str(client.base_url).startswith("http://localhost:20128/v1")
        assert client.max_retries == 2
        assert client.timeout.connect == 10.0  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Variant config wire-format parsing
# ---------------------------------------------------------------------------


class TestParseVariantModelConfig:
    def test_full_config_parses(self) -> None:
        cfg = parse_variant_model_config(
            {
                "family": "openai",
                "model_id": "antigravity/gemini-3.6-flash-high",
                "label": "OmniRoute Gemini",
                "api_key": "sk-x",
                "base_url": "http://localhost:20128/v1",
                "reasoning_effort": "high",
            }
        )
        assert cfg.model_id == "antigravity/gemini-3.6-flash-high"
        assert cfg.base_url == "http://localhost:20128/v1"
        assert cfg.reasoning_effort == "high"

    def test_defaults_fill_optional_fields(self) -> None:
        cfg = parse_variant_model_config({"model_id": "m1", "api_key": "k1"})
        assert cfg.family == "openai"
        assert cfg.label == "m1"
        assert cfg.base_url is None
        assert cfg.reasoning_effort is None

    def test_missing_required_fields_raise_readable_error(self) -> None:
        with pytest.raises(ValueError, match="model_id, api_key"):
            parse_variant_model_config({})
        with pytest.raises(ValueError, match="api_key"):
            parse_variant_model_config({"model_id": "m1"})
        with pytest.raises(ValueError, match="model_id"):
            parse_variant_model_config({"model_id": "  ", "api_key": "k1"})

    def test_unknown_family_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown provider family"):
            parse_variant_model_config(
                {"family": "mistral", "model_id": "m1", "api_key": "k1"}
            )


# ---------------------------------------------------------------------------
# Factory fallback branches (Anthropic/Gemini model + only a gateway key)
# ---------------------------------------------------------------------------


class TestFactoryGatewayFallback:
    @pytest.fixture(autouse=True)
    def _no_chromium_probe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import agent.providers.factory as factory

        monkeypatch.setattr(
            factory, "is_screenshot_preview_available", lambda: False
        )

    def _create(self, model: Any, **overrides: Any) -> Any:
        from agent.providers.factory import create_provider_session

        kwargs: Dict[str, Any] = dict(
            model=model,
            prompt_messages=[],
            should_generate_images=False,
            openai_api_key="sk-gateway",
            openai_base_url="http://localhost:20128/v1",
            anthropic_api_key=None,
            gemini_api_key=None,
            replicate_api_key=None,
        )
        kwargs.update(overrides)
        return create_provider_session(**kwargs)

    def test_anthropic_model_falls_back_to_gateway(self) -> None:
        from llm import ANTHROPIC_MODELS

        model = next(iter(ANTHROPIC_MODELS))
        session = self._create(model)
        assert isinstance(session, OpenAICompatibleProviderSession)

    def test_gemini_model_falls_back_to_gateway(self) -> None:
        from llm import GEMINI_MODELS

        model = next(iter(GEMINI_MODELS))
        session = self._create(model)
        assert isinstance(session, OpenAICompatibleProviderSession)

    def test_anthropic_model_without_any_key_raises(self) -> None:
        from llm import ANTHROPIC_MODELS

        model = next(iter(ANTHROPIC_MODELS))
        with pytest.raises(Exception, match="Anthropic API key is missing"):
            self._create(model, openai_api_key=None, openai_base_url=None)

    def test_gateway_fallback_needs_base_url_too(self) -> None:
        from llm import GEMINI_MODELS

        model = next(iter(GEMINI_MODELS))
        with pytest.raises(Exception, match="Gemini API key is missing"):
            self._create(model, openai_base_url=None)
