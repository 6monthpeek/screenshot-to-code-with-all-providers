"""Tests for the one-shot stack repair pass in AgentEngine.

When the final output ignores the selected stack (e.g. plain HTML for
react_tailwind), the engine appends a repair user message to the same
session and runs one more loop. Repair is best effort: if it fails or
stays non-compliant, the original output is kept.
"""

from typing import Any, cast

import pytest
from openai.types.chat import ChatCompletionMessageParam

from agent.engine import AgentEngine
from agent.providers.base import EventSink, ExecutedToolCall, ProviderTurn
from agent.tools import ToolCall
from llm import Llm
from prompts.policies import build_stack_repair_message

PLAIN_HTML = "<!DOCTYPE html><html><body><div>plain</div></body></html>"
REACT_HTML = (
    "<!DOCTYPE html><html><head>"
    '<script src="https://unpkg.com/react@18.0.0/umd/react.development.js"></script>'
    '<script src="https://cdn.tailwindcss.com"></script>'
    '</head><body><div id="root"></div>'
    '<script type="text/babel">const App = () => <div/>;</script>'
    "</body></html>"
)


def _create_file_turn(turn_id: str, content: str) -> ProviderTurn:
    return ProviderTurn(
        assistant_text="",
        tool_calls=[
            ToolCall(
                id=turn_id,
                name="create_file",
                arguments={"path": "index.html", "content": content},
            )
        ],
    )


class RepairableSession:
    """First produces plain HTML, then obeys the repair message."""

    def __init__(self, repair_content: str | None) -> None:
        # None means: reply with text only, never fixing the file.
        self.repair_content = repair_content
        self.turn_index = 0
        self.user_turns: list[str] = []
        self.closed = False

    async def stream_turn(self, on_event: EventSink) -> ProviderTurn:
        self.turn_index += 1
        if self.turn_index == 1:
            return _create_file_turn("t1", PLAIN_HTML)
        if self.turn_index == 2:
            return ProviderTurn(assistant_text="Done.", tool_calls=[])
        if self.turn_index == 3 and self.repair_content is not None:
            return _create_file_turn("t2", self.repair_content)
        return ProviderTurn(assistant_text="Sorry, done.", tool_calls=[])

    async def append_tool_results(
        self,
        turn: ProviderTurn,
        executed_tool_calls: list[ExecutedToolCall],
    ) -> None:
        return None

    async def append_user_turn(
        self,
        turn: ProviderTurn | None,
        text: str,
    ) -> None:
        self.user_turns.append(text)

    def total_cost_usd(self) -> float | None:
        return None

    async def close(self) -> None:
        self.closed = True


def _prompt() -> list[ChatCompletionMessageParam]:
    return cast(
        list[ChatCompletionMessageParam],
        [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Build this."},
        ],
    )


def _build_engine(
    monkeypatch: pytest.MonkeyPatch,
    session: RepairableSession,
    stack: str,
) -> AgentEngine:
    def fake_create_provider_session(**kwargs: Any) -> RepairableSession:
        return session

    monkeypatch.setattr(
        "agent.engine.create_provider_session", fake_create_provider_session
    )

    async def send_message(
        message_type: str,
        value: str | None,
        variant_index: int,
        data: dict[str, Any] | None,
        event_id: str | None,
    ) -> None:
        return None

    return AgentEngine(
        send_message=send_message,
        variant_index=0,
        openai_api_key=None,
        openai_base_url=None,
        anthropic_api_key=None,
        gemini_api_key="gemini-key",
        replicate_api_key=None,
        should_generate_images=False,
        should_extract_assets=False,
        stack=stack,
    )


@pytest.mark.asyncio
async def test_non_compliant_react_output_is_repaired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = RepairableSession(repair_content=REACT_HTML)
    engine = _build_engine(monkeypatch, session, "react_tailwind")

    result = await engine.run(Llm.GEMINI_3_FLASH_PREVIEW_MINIMAL, _prompt())

    assert result == REACT_HTML
    assert session.user_turns == [build_stack_repair_message("react_tailwind")]
    assert session.closed is True


@pytest.mark.asyncio
async def test_failed_repair_keeps_original_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = RepairableSession(repair_content=None)
    engine = _build_engine(monkeypatch, session, "react_tailwind")

    result = await engine.run(Llm.GEMINI_3_FLASH_PREVIEW_MINIMAL, _prompt())

    # Repair was attempted but the model never fixed the file, so the
    # working (if non-compliant) original is preserved.
    assert result == PLAIN_HTML
    assert len(session.user_turns) == 1


@pytest.mark.asyncio
async def test_compliant_output_skips_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = RepairableSession(repair_content=None)
    engine = _build_engine(monkeypatch, session, "html_tailwind")

    result = await engine.run(Llm.GEMINI_3_FLASH_PREVIEW_MINIMAL, _prompt())

    assert result == PLAIN_HTML
    assert session.user_turns == []
    # Only the create_file turn and the final answer ran.
    assert session.turn_index == 2


class TestRepairMessage:
    def test_react_repair_message_demands_conversion(self) -> None:
        msg = build_stack_repair_message("react_tailwind")
        assert "React + Tailwind (react_tailwind)" in msg
        assert "create_file" in msg
        assert "Keep the visual design" in msg

    def test_unknown_stack_falls_back_to_raw_name(self) -> None:
        msg = build_stack_repair_message("custom_stack")
        assert "custom_stack" in msg
