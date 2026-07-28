from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal, Optional, Protocol

from agent.tools import ToolCall, ToolExecutionResult
from costs.token_usage import TokenUsage


StreamEventType = Literal[
    "assistant_delta",
    "thinking_delta",
    "tool_call_delta",
]


@dataclass
class StreamEvent:
    type: StreamEventType
    text: str = ""
    tool_call_id: Optional[str] = None
    tool_name: Optional[str] = None
    tool_arguments: Any = None
    args_delta: Optional[str] = None


@dataclass
class ProviderTurn:
    assistant_text: str
    tool_calls: list[ToolCall]
    # Provider-native assistant turn object required to continue the conversation.
    assistant_turn: Any = None


@dataclass
class ExecutedToolCall:
    tool_call: ToolCall
    result: ToolExecutionResult


EventSink = Callable[[StreamEvent], Awaitable[None]]


class ProviderSession(Protocol):
    async def stream_turn(self, on_event: EventSink) -> ProviderTurn:
        ...

    async def append_tool_results(
        self,
        turn: ProviderTurn,
        executed_tool_calls: list[ExecutedToolCall],
    ) -> None:
        ...

    async def append_user_turn(
        self,
        turn: Optional[ProviderTurn],
        text: str,
    ) -> None:
        """Append the finished assistant turn (if any) plus a follow-up user
        text message, so the session can continue past a final answer.

        Used by the engine's stack-repair pass: a turn without tool calls
        ends the loop before append_tool_results runs, so the assistant
        turn has not been persisted yet.
        """
        ...

    def total_cost_usd(self) -> Optional[float]:
        """USD spent so far this session; None when the model is unpriced."""
        ...

    def total_usage(self) -> TokenUsage:
        """Accumulated token usage across all turns this session."""
        ...

    async def close(self) -> None:
        ...
