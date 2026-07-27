"""Generic OpenAI-compatible provider session.

Accepts any concrete (model_id, base_url, api_key) triple. Used for OmniRoute,
OpenRouter, Groq, Together, Fireworks, Novita, z.ai, NVIDIA NIM, Ollama,
LM Studio, vLLM, SGLang, and any other OpenAI-compatible endpoint.

Unlike the hardcoded OpenAIProviderSession (which reads model metadata from the
Llm enum's OPENAI_MODEL_CONFIG), this subclass overrides stream_turn so the
concrete model_id is sent verbatim to the provider.
"""

from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from agent.providers.base import EventSink, ProviderTurn
from agent.providers.openai import (
    OpenAIProviderSession,
    OpenAIResponsesParseState,
    _build_provider_turn,
    _convert_message_to_responses_input,
    parse_event,
    serialize_openai_tools,
)
from agent.tools import CanonicalToolDefinition
from agent.variant_config import VariantModelConfig
from costs.token_usage import TokenUsage
from fs_logging.agent_runs import AgentRunRecorder
from fs_logging.prompt_reports import PromptReportLogger
from llm import Llm


class OpenAICompatibleProviderSession(OpenAIProviderSession):
    """OpenAIProviderSession with a free-form model id.

    Overrides stream_turn to bypass the OPENAI_MODEL_CONFIG lookup. Tool
    result appending, usage tracking, and close() reuse the parent
    implementation (those code paths don't depend on the enum; only the
    cost-pricing lookup at close() falls back to None for unknown models,
    which is the desired behavior).
    """

    def __init__(
        self,
        client: AsyncOpenAI,
        model_id: str,
        reasoning_effort: Optional[str],
        prompt_messages: List[ChatCompletionMessageParam],
        tools: List[Dict[str, Any]],
        recorder: Optional[AgentRunRecorder] = None,
    ):
        # Deliberately skip OpenAIProviderSession.__init__ to avoid the
        # OPENAI_MODEL_CONFIG lookup.
        self._client = client
        self._model_id = model_id
        self._reasoning_effort = reasoning_effort
        # Placeholder enum to satisfy the inherited `_model` attribute's type;
        # nothing downstream reads it because we override stream_turn and
        # don't call the parent's close()/total_cost_usd() lookups.
        self._model = Llm.GPT_5_5_LOW
        self._tools = tools
        self._total_usage = TokenUsage()
        self._recorder = recorder
        self._prompt_report_logger = PromptReportLogger(
            provider="openai-compatible",
            model=self._model,
            api_model_name=model_id,
        )
        self._input_items: List[Dict[str, Any]] = [
            _convert_message_to_responses_input(message, image_detail="high")
            for message in prompt_messages
        ]

    async def stream_turn(self, on_event: EventSink) -> ProviderTurn:
        params: Dict[str, Any] = {
            "model": self._model_id,
            "input": self._input_items,
            "tools": self._tools,
            "tool_choice": "auto",
            "stream": True,
            "max_output_tokens": 50000,
        }
        if self._reasoning_effort:
            params["reasoning"] = {
                "effort": self._reasoning_effort,
                "summary": "auto",
            }

        self._prompt_report_logger.record_request(params)
        if self._recorder is not None:
            self._recorder.record_llm_request(
                "openai-compatible", self._model_id, params
            )

        state = OpenAIResponsesParseState()
        stream = await self._client.responses.create(**params)  # type: ignore
        async for event in stream:  # type: ignore
            await parse_event(event, state, on_event)

        if state.turn_usage is not None:
            self._prompt_report_logger.record_usage(state.turn_usage)
            self._total_usage.accumulate(state.turn_usage)

        turn = _build_provider_turn(state)
        if self._recorder is not None:
            self._recorder.record_llm_response(
                turn.assistant_text, turn.tool_calls, state.turn_usage
            )
        return turn

    def total_cost_usd(self) -> Optional[float]:
        # Unknown model id — no pricing entry. Returning None is correct; the
        # caller treats None as "unpriced / not bounded".
        return None


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
        tools=serialize_openai_tools(tools),
        recorder=recorder,
    )
