"""Generic OpenAI-compatible provider session.

Accepts any concrete (model_id, base_url, api_key) triple. Used for OmniRoute,
OpenRouter, Groq, Together, Fireworks, Novita, z.ai, NVIDIA NIM, Ollama,
LM Studio, vLLM, SGLang, and any other OpenAI-compatible endpoint.

Uses the standard chat.completions.create API for maximum compatibility across
all providers and proxies.
"""

import asyncio
import json
import uuid
from typing import Any, Dict, List, Optional

import httpx
import openai
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from agent.providers.base import (
    EventSink,
    ExecutedToolCall,
    ProviderSession,
    ProviderTurn,
    StreamEvent,
)
from costs.pricing import MODEL_PRICING, ModelPricing
from costs.token_usage import TokenUsage
from agent.tools import CanonicalToolDefinition, ToolCall, parse_json_arguments
from agent.variant_config import VariantModelConfig
from fs_logging.agent_runs import AgentRunRecorder
from fs_logging.prompt_reports import PromptReportLogger
from llm import Llm


def serialize_chat_completion_tools(tools: List[CanonicalToolDefinition]) -> List[Dict[str, Any]]:
    """Serialize canonical tools into standard OpenAI chat completion tool format."""
    result: List[Dict[str, Any]] = []
    for tool in tools:
        result.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
        )
    return result


def _convert_message_to_chat_completion(message: Any) -> Dict[str, Any]:
    if isinstance(message, dict):
        return dict(message)
    if hasattr(message, "items"):
        return dict(message.items())
    return {"role": "user", "content": str(message)}


def _extract_chat_completion_usage(usage: Any) -> TokenUsage:
    """Extract unified token usage from a chat.completions usage block.

    OpenAI-compatible endpoints report ``prompt_tokens`` inclusive of cached
    tokens, so cached tokens are subtracted to get the non-cached input count.
    Gateways that omit ``prompt_tokens_details`` simply report zero cache reads.
    """
    prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
    completion_tokens = getattr(usage, "completion_tokens", 0) or 0
    total_tokens = getattr(usage, "total_tokens", 0) or 0

    details = getattr(usage, "prompt_tokens_details", None)
    cached_tokens = (getattr(details, "cached_tokens", 0) or 0) if details else 0

    return TokenUsage(
        input=prompt_tokens - cached_tokens,
        output=completion_tokens,
        cache_read=cached_tokens,
        total=total_tokens,
    )


def _lookup_pricing(model_id: str) -> Optional[ModelPricing]:
    """Find pricing for a possibly gateway-prefixed model id.

    Gateways commonly namespace ids ("openrouter/openai/gpt-5.5",
    "antigravity/gemini-3.6-flash-high"); try the exact id first, then
    progressively strip leading path segments. Unknown ids stay unpriced.
    """
    pricing = MODEL_PRICING.get(model_id)
    if pricing is not None:
        return pricing
    parts = model_id.split("/")
    for start in range(1, len(parts)):
        pricing = MODEL_PRICING.get("/".join(parts[start:]))
        if pricing is not None:
            return pricing
    return None


class OpenAICompatibleProviderSession(ProviderSession):
    def __init__(
        self,
        client: AsyncOpenAI,
        model_id: str,
        reasoning_effort: Optional[str],
        prompt_messages: List[ChatCompletionMessageParam],
        tools: List[CanonicalToolDefinition],
        recorder: Optional[AgentRunRecorder] = None,
    ):
        self._client = client
        self._model_id = model_id
        self._reasoning_effort = reasoning_effort
        self._model = Llm.GPT_5_5_LOW
        self._tools = serialize_chat_completion_tools(tools)
        self._total_usage = TokenUsage()
        self._recorder = recorder
        self._prompt_report_logger = PromptReportLogger(
            provider="openai-compatible",
            model=self._model,
            api_model_name=model_id,
        )
        self._messages: List[Dict[str, Any]] = [
            _convert_message_to_chat_completion(msg) for msg in prompt_messages
        ]

    # Extra attempts for transient failures when creating the stream (nothing
    # has been emitted yet at that point, so a retry is always safe).
    _TRANSIENT_RETRIES = 2

    async def _create_stream(self, params: Dict[str, Any]) -> Any:
        """Create the streaming request with transient-error retries.

        Connection/timeout/5xx failures are retried with a short exponential
        backoff (on top of the SDK's own request retries). If the endpoint
        rejects ``stream_options`` (older gateways), it is dropped once and
        the request is retried without usage reporting.
        """
        attempt = 0
        while True:
            try:
                return await self._client.chat.completions.create(**params)  # type: ignore
            except openai.BadRequestError as e:
                if "stream_options" in params and "stream_options" in str(e):
                    params = {
                        k: v for k, v in params.items() if k != "stream_options"
                    }
                    continue
                raise
            except (openai.APIConnectionError, openai.InternalServerError) as e:
                if attempt >= self._TRANSIENT_RETRIES:
                    raise
                delay = 1.0 * (2**attempt)
                attempt += 1
                print(
                    f"[OPENAI-COMPAT] Transient error ({type(e).__name__}), "
                    f"retry {attempt}/{self._TRANSIENT_RETRIES} in {delay:.0f}s: {e}"
                )
                await asyncio.sleep(delay)

    async def stream_turn(self, on_event: EventSink) -> ProviderTurn:
        params: Dict[str, Any] = {
            "model": self._model_id,
            "messages": self._messages,
            "stream": True,
            # Ask for a final usage chunk so token/cost accounting works
            # through gateways (OmniRoute, OpenRouter, ...).
            "stream_options": {"include_usage": True},
        }
        if self._reasoning_effort:
            # Forward the OpenAI-style hint; providers that don't support it
            # generally ignore unknown fields, matching OpenAI's behavior.
            params["reasoning_effort"] = self._reasoning_effort
        if self._tools:
            params["tools"] = self._tools
            params["tool_choice"] = "auto"

        self._prompt_report_logger.record_request(params)
        if self._recorder is not None:
            self._recorder.record_llm_request(
                "openai-compatible", self._model_id, params
            )

        assistant_text_chunks: List[str] = []
        tool_calls_dict: Dict[int, Dict[str, Any]] = {}
        turn_usage: Optional[TokenUsage] = None

        response_stream = await self._create_stream(params)

        async for chunk in response_stream:  # type: ignore
            # The usage chunk usually arrives last with empty choices; some
            # gateways attach it to the final content chunk instead. Keep the
            # most recent value either way.
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage is not None:
                turn_usage = _extract_chat_completion_usage(chunk_usage)
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta

            # Text content
            if delta.content:
                assistant_text_chunks.append(delta.content)
                await on_event(StreamEvent(type="assistant_delta", text=delta.content))

            # Tool calls streaming
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_calls_dict:
                        tool_calls_dict[idx] = {
                            "id": (tc_delta.id if hasattr(tc_delta, "id") and tc_delta.id else None) or f"call_{uuid.uuid4().hex[:6]}",
                            "name": "",
                            "arguments": "",
                        }

                    tc_entry = tool_calls_dict[idx]
                    if tc_delta.id and not tc_entry["id"].startswith("call_"):
                        tc_entry["id"] = tc_delta.id

                    if tc_delta.function:
                        if tc_delta.function.name:
                            tc_entry["name"] = tc_delta.function.name
                        args_delta = tc_delta.function.arguments or ""
                        if args_delta:
                            tc_entry["arguments"] += args_delta

                        await on_event(
                            StreamEvent(
                                type="tool_call_delta",
                                tool_call_id=tc_entry["id"],
                                tool_name=tc_entry["name"],
                                args_delta=args_delta,
                            )
                        )

        assistant_text = "".join(assistant_text_chunks)

        if turn_usage is not None:
            self._prompt_report_logger.record_usage(turn_usage)
            self._total_usage.accumulate(turn_usage)

        parsed_tool_calls: List[ToolCall] = []

        for idx in sorted(tool_calls_dict.keys()):
            tc_data = tool_calls_dict[idx]
            args_str = tc_data["arguments"]
            try:
                args, parse_error = parse_json_arguments(args_str)
                if parse_error or not isinstance(args, dict):
                    args = {"INVALID_JSON": args_str}
            except Exception:
                args = {"INVALID_JSON": args_str}

            if tc_data["name"]:
                parsed_tool_calls.append(
                    ToolCall(
                        id=tc_data["id"],
                        name=tc_data["name"],
                        arguments=args,
                    )
                )

        assistant_msg: Dict[str, Any] = {"role": "assistant"}
        if assistant_text:
            assistant_msg["content"] = assistant_text
        if parsed_tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in parsed_tool_calls
            ]

        turn = ProviderTurn(
            assistant_text=assistant_text,
            tool_calls=parsed_tool_calls,
            assistant_turn=assistant_msg,
        )

        if self._recorder is not None:
            self._recorder.record_llm_response(
                turn.assistant_text, turn.tool_calls, turn_usage
            )
        return turn

    async def append_tool_results(
        self,
        turn: ProviderTurn,
        executed_tool_calls: list[ExecutedToolCall],
    ) -> None:
        if turn.assistant_turn:
            self._messages.append(turn.assistant_turn)  # type: ignore

        for executed in executed_tool_calls:
            self._messages.append(
                {
                    "role": "tool",
                    "tool_call_id": executed.tool_call.id,
                    "content": json.dumps(executed.result.result),
                }
            )

    async def append_user_turn(
        self,
        turn: Optional[ProviderTurn],
        text: str,
    ) -> None:
        if turn is not None and turn.assistant_turn:
            assistant_msg: Dict[str, Any] = turn.assistant_turn
            # An assistant message with neither content nor tool_calls is
            # rejected by some endpoints; skip it rather than fail the turn.
            if assistant_msg.get("content") or assistant_msg.get("tool_calls"):
                self._messages.append(assistant_msg)
        self._messages.append({"role": "user", "content": text})

    def total_cost_usd(self) -> Optional[float]:
        pricing = _lookup_pricing(self._model_id)
        if pricing is None:
            return None
        return self._total_usage.cost(pricing)

    def total_usage(self) -> TokenUsage:
        return self._total_usage

    async def close(self) -> None:
        u = self._total_usage
        if u.total > 0:
            pricing = _lookup_pricing(self._model_id)
            cost_str = f" cost=${u.cost(pricing):.4f}" if pricing else ""
            print(
                f"[TOKEN USAGE] provider=openai-compatible model={self._model_id} | "
                f"input={u.input} output={u.output} cache_read={u.cache_read} "
                f"total={u.total}{cost_str}"
            )
        await self._client.close()


def create_openai_compatible_session(
    cfg: VariantModelConfig,
    prompt_messages: List[ChatCompletionMessageParam],
    tools: List[CanonicalToolDefinition],
    recorder: Optional[AgentRunRecorder] = None,
) -> OpenAICompatibleProviderSession:
    """Build a session against any OpenAI-compatible endpoint.

    The client gets an explicit timeout (generous read window for slow local
    gateways, but a bounded connect so an unreachable endpoint fails fast)
    and SDK-level request retries for transient failures.
    """
    client = AsyncOpenAI(
        api_key=cfg.api_key,
        base_url=cfg.base_url,
        timeout=httpx.Timeout(600.0, connect=10.0),
        max_retries=2,
    )
    return OpenAICompatibleProviderSession(
        client=client,
        model_id=cfg.model_id,
        reasoning_effort=cfg.reasoning_effort,
        prompt_messages=prompt_messages,
        tools=tools,
        recorder=recorder,
    )
