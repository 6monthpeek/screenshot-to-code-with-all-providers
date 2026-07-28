"""Generic OpenAI-compatible provider session.

Accepts any concrete (model_id, base_url, api_key) triple. Used for OmniRoute,
OpenRouter, Groq, Together, Fireworks, Novita, z.ai, NVIDIA NIM, Ollama,
LM Studio, vLLM, SGLang, and any other OpenAI-compatible endpoint.

Uses the standard chat.completions.create API for maximum compatibility across
all providers and proxies.
"""

import json
import uuid
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from agent.providers.base import (
    EventSink,
    ExecutedToolCall,
    ProviderSession,
    ProviderTurn,
    StreamEvent,
)
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

    async def stream_turn(self, on_event: EventSink) -> ProviderTurn:
        params: Dict[str, Any] = {
            "model": self._model_id,
            "messages": self._messages,
            "stream": True,
        }
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

        response_stream = await self._client.chat.completions.create(**params)  # type: ignore

        async for chunk in response_stream:  # type: ignore
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
                turn.assistant_text, turn.tool_calls, None
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

    def total_cost_usd(self) -> Optional[float]:
        return None

    async def close(self) -> None:
        await self._client.close()


def create_openai_compatible_session(
    cfg: VariantModelConfig,
    prompt_messages: List[ChatCompletionMessageParam],
    tools: List[CanonicalToolDefinition],
    recorder: Optional[AgentRunRecorder] = None,
) -> OpenAICompatibleProviderSession:
    """Build a session against any OpenAI-compatible endpoint."""
    client = AsyncOpenAI(api_key=cfg.api_key, base_url=cfg.base_url)
    return OpenAICompatibleProviderSession(
        client=client,
        model_id=cfg.model_id,
        reasoning_effort=cfg.reasoning_effort,
        prompt_messages=prompt_messages,
        tools=tools,
        recorder=recorder,
    )
