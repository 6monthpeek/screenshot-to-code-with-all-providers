import asyncio
import traceback
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional, TYPE_CHECKING, cast

from openai.types.chat import ChatCompletionMessageParam

from codegen.utils import (
    build_fallback_document,
    check_stack_compliance,
    extract_html_content,
)
from llm import Llm

from agent.providers.base import (
    ExecutedToolCall,
    ProviderSession,
    ProviderTurn,
    StreamEvent,
)
from agent.providers.factory import create_provider_session
from agent.state import AgentFileState, seed_file_state_from_messages
from agent.tools import (
    AgentToolRuntime,
    extract_content_from_args,
    extract_path_from_args,
    summarize_text,
    summarize_tool_input,
)
from config import GENERATION_MAX_COST_USD
from costs.token_usage import TokenUsage
from fs_logging.agent_runs import AgentRunRecorder
from prompts.policies import build_stack_repair_message

if TYPE_CHECKING:
    from agent.variant_config import VariantModelConfig


class EmptyOutputError(Exception):
    """Raised when a run finishes without producing any HTML.

    Some models (observed: gemini-3.6-flash) occasionally run asset tools
    and then stop without calling create_file. Treating that as success
    poisons evals: the run looks green, diff mode skips it forever, and
    the output file is empty. Raising makes it a normal, retryable failure.
    """

    def __init__(self) -> None:
        super().__init__("Generation finished without producing any output.")


class BudgetExceededError(Exception):
    """Raised when a single generation exceeds the spend ceiling.

    The message is shown verbatim to end users (variantError), so it must
    not contain cost figures; the exact spend is in the run record.
    """

    def __init__(self) -> None:
        super().__init__(
            "Generation stopped: this variant exceeded its resource limit."
        )


class AgentEngine:
    def __init__(
        self,
        send_message: Callable[
            [str, Optional[str], int, Optional[Dict[str, Any]], Optional[str]],
            Awaitable[None],
        ],
        variant_index: int,
        openai_api_key: Optional[str],
        openai_base_url: Optional[str],
        anthropic_api_key: Optional[str],
        gemini_api_key: Optional[str],
        replicate_api_key: Optional[str],
        should_generate_images: bool,
        should_extract_assets: bool = True,
        asset_base_url: str = "",
        initial_file_state: Optional[Dict[str, str]] = None,
        option_codes: Optional[List[str]] = None,
        recorder: Optional[AgentRunRecorder] = None,
        stack: Optional[str] = None,
    ):
        self.send_message = send_message
        self.variant_index = variant_index
        self.recorder = recorder
        self.stack = stack
        self.openai_api_key = openai_api_key
        self.openai_base_url = openai_base_url
        self.anthropic_api_key = anthropic_api_key
        self.gemini_api_key = gemini_api_key
        self.replicate_api_key = replicate_api_key
        self.should_generate_images = should_generate_images
        self.should_extract_assets = should_extract_assets

        self.file_state = AgentFileState()
        if initial_file_state and initial_file_state.get("content"):
            self.file_state.path = initial_file_state.get("path") or "index.html"
            self.file_state.content = initial_file_state["content"]

        self.tool_runtime = AgentToolRuntime(
            file_state=self.file_state,
            should_generate_images=should_generate_images,
            openai_api_key=openai_api_key,
            openai_base_url=openai_base_url,
            gemini_api_key=gemini_api_key,
            replicate_api_key=replicate_api_key,
            asset_base_url=asset_base_url,
            option_codes=option_codes,
        )
        self._tool_preview_lengths: Dict[str, int] = {}
        # Final assistant turn of the last completed loop; consumed by the
        # stack-repair pass to continue the session past a final answer.
        self._last_turn: Optional[ProviderTurn] = None
        # Cost/usage of the last run(); captured before the session closes so
        # the caller can surface per-variant spend (None when unpriced).
        self.last_cost_usd: Optional[float] = None
        self.last_token_usage: Optional[TokenUsage] = None

    def _build_session(
        self,
        model: Llm,
        prompt_messages: List[ChatCompletionMessageParam],
        variant_model_config: "VariantModelConfig | None" = None,
    ) -> ProviderSession:
        """Build a provider session for this variant.

        When variant_model_config is set, we honor the per-variant override
        (any provider family). Otherwise we fall back to the legacy
        key-based factory which inspects the global API keys.
        """
        from agent.tools import canonical_tool_definitions
        from agent.variant_config import VariantModelConfig
        from agent.providers.openai_compatible import create_openai_compatible_session
        from preview_screenshot import is_screenshot_preview_available
        from config import REPLICATE_API_KEY

        if variant_model_config is not None:
            canonical_tools = canonical_tool_definitions(
                image_generation_enabled=self.should_generate_images,
                image_editing_enabled=bool(
                    self.replicate_api_key or REPLICATE_API_KEY
                ),
                asset_extraction_enabled=(
                    self.should_extract_assets
                    and bool(self.tool_runtime.input_images)
                    and variant_model_config.family == "gemini"
                ),
                screenshot_enabled=is_screenshot_preview_available(),
            )
            if variant_model_config.family == "openai":
                return create_openai_compatible_session(
                    cfg=variant_model_config,
                    prompt_messages=prompt_messages,
                    tools=canonical_tools,
                    recorder=self.recorder,
                )
            # Anthropic / Gemini families go through the legacy factory using
            # the per-variant api_key; the Llm enum is a placeholder.
            return create_provider_session(
                model=model,
                prompt_messages=prompt_messages,
                should_generate_images=self.should_generate_images,
                openai_api_key=(
                    variant_model_config.api_key
                    if variant_model_config.family == "openai"
                    else self.openai_api_key
                ),
                openai_base_url=(
                    variant_model_config.base_url
                    if variant_model_config.family == "openai"
                    else self.openai_base_url
                ),
                anthropic_api_key=(
                    variant_model_config.api_key
                    if variant_model_config.family == "anthropic"
                    else self.anthropic_api_key
                ),
                gemini_api_key=(
                    variant_model_config.api_key
                    if variant_model_config.family == "gemini"
                    else self.gemini_api_key
                ),
                replicate_api_key=self.replicate_api_key,
                should_extract_assets=(
                    self.should_extract_assets
                    and bool(self.tool_runtime.input_images)
                ),
                recorder=self.recorder,
            )

        # Legacy path: no per-variant override.
        return create_provider_session(
            model=model,
            prompt_messages=prompt_messages,
            should_generate_images=self.should_generate_images,
            openai_api_key=self.openai_api_key,
            openai_base_url=self.openai_base_url,
            anthropic_api_key=self.anthropic_api_key,
            gemini_api_key=self.gemini_api_key,
            replicate_api_key=self.replicate_api_key,
            should_extract_assets=(
                self.should_extract_assets and bool(self.tool_runtime.input_images)
            ),
            recorder=self.recorder,
        )

    @staticmethod
    def _extract_input_images(
        prompt_messages: List[ChatCompletionMessageParam],
    ) -> List[str]:
        images: List[str] = []
        for message in prompt_messages:
            msg_dict = message if isinstance(message, dict) else (dict(message) if isinstance(message, (tuple, list)) else {})
            content = msg_dict.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                part_dict = part if isinstance(part, dict) else (dict(part) if isinstance(part, (tuple, list)) else {})
                if part_dict.get("type") != "image_url":
                    continue
                image_url = part_dict.get("image_url")
                img_url_dict = image_url if isinstance(image_url, dict) else (dict(image_url) if isinstance(image_url, (tuple, list)) else {})
                url = cast(object, img_url_dict.get("url"))
                if (
                    isinstance(url, str)
                    and url.startswith("data:image/")
                    and "," in url
                ):
                    images.append(url)
        return images

    def _next_event_id(self, prefix: str) -> str:
        return f"{prefix}-{self.variant_index}-{uuid.uuid4().hex[:8]}"

    async def _send(
        self,
        msg_type: str,
        value: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
        event_id: Optional[str] = None,
    ) -> None:
        await self.send_message(msg_type, value, self.variant_index, data, event_id)

    def _mark_preview_length(self, tool_event_id: Optional[str], length: int) -> None:
        if not tool_event_id:
            return
        current = self._tool_preview_lengths.get(tool_event_id, 0)
        if length > current:
            self._tool_preview_lengths[tool_event_id] = length

    async def _stream_code_preview(self, tool_event_id: Optional[str], content: str) -> None:
        if not tool_event_id or not content:
            return

        already_sent = self._tool_preview_lengths.get(tool_event_id, 0)
        total_len = len(content)
        if already_sent >= total_len:
            return

        max_chunks = 18
        min_step = 200
        step = max(min_step, total_len // max_chunks)
        start = already_sent if already_sent > 0 else 0

        for end in range(start + step, total_len, step):
            await self._send("setCode", content[:end])
            self._mark_preview_length(tool_event_id, end)
            await asyncio.sleep(0.01)

        await self._send("setCode", content)
        self._mark_preview_length(tool_event_id, total_len)
        if self.recorder is not None:
            self.recorder.record_set_code(total_len, "stream_preview")

    async def _handle_streamed_tool_delta(
        self,
        event: StreamEvent,
        started_tool_ids: set[str],
        streamed_lengths: Dict[str, int],
    ) -> None:
        if event.type != "tool_call_delta":
            return
        if event.tool_name != "create_file":
            return
        if not event.tool_call_id:
            return

        content = extract_content_from_args(event.tool_arguments)
        if content is None:
            return

        tool_event_id = event.tool_call_id
        if tool_event_id not in started_tool_ids:
            path = (
                extract_path_from_args(event.tool_arguments)
                or self.file_state.path
                or "index.html"
            )
            await self._send(
                "toolStart",
                data={
                    "name": "create_file",
                    "input": {
                        "path": path,
                        "contentLength": len(content),
                        "preview": summarize_text(content, 200),
                    },
                },
                event_id=tool_event_id,
            )
            started_tool_ids.add(tool_event_id)

        last_len = streamed_lengths.get(tool_event_id, 0)
        if last_len == 0 and content:
            streamed_lengths[tool_event_id] = len(content)
            await self._send("setCode", content)
            self._mark_preview_length(tool_event_id, len(content))
        elif len(content) - last_len >= 40:
            streamed_lengths[tool_event_id] = len(content)
            await self._send("setCode", content)
            self._mark_preview_length(tool_event_id, len(content))

    async def _run_with_session(self, session: ProviderSession) -> str:
        max_steps = 30

        for _ in range(max_steps):
            assistant_event_id = self._next_event_id("assistant")
            thinking_event_id = self._next_event_id("thinking")
            started_tool_ids: set[str] = set()
            streamed_lengths: Dict[str, int] = {}

            async def on_event(event: StreamEvent) -> None:
                if self.recorder is not None:
                    if event.type == "assistant_delta":
                        stream_event_id = assistant_event_id
                    elif event.type == "thinking_delta":
                        stream_event_id = thinking_event_id
                    else:
                        stream_event_id = event.tool_call_id
                    self.recorder.record_stream_event(event, stream_event_id)

                if event.type == "assistant_delta":
                    if event.text:
                        await self._send(
                            "assistant",
                            event.text,
                            event_id=assistant_event_id,
                        )
                        # Stream HTML preview as the assistant types it into chat
                        if not self.file_state.content:
                            live_html = extract_html_content(event.text)
                            if live_html and ("<html" in live_html.lower() or "<!doctype" in live_html.lower()):
                                await self._send("setCode", live_html)
                    return

                if event.type == "thinking_delta":
                    if event.text:
                        await self._send(
                            "thinking",
                            event.text,
                            event_id=thinking_event_id,
                        )
                    return

                if event.type == "tool_call_delta":
                    await self._handle_streamed_tool_delta(
                        event,
                        started_tool_ids,
                        streamed_lengths,
                    )

            turn = await session.stream_turn(on_event)

            if not turn.tool_calls:
                self._last_turn = turn
                return await self._finalize_response(turn.assistant_text)

            # Abort only when the run would otherwise continue: a run that
            # just produced its final answer is already paid for. Unpriced
            # models return None and are not bounded.
            spent = session.total_cost_usd()
            if spent is not None and spent > GENERATION_MAX_COST_USD:
                print(
                    f"[BUDGET] Aborting variant {self.variant_index}: "
                    f"${spent:.2f} > ${GENERATION_MAX_COST_USD:.2f}"
                )
                raise BudgetExceededError()

            executed_tool_calls: List[ExecutedToolCall] = []
            for tool_call in turn.tool_calls:
                tool_event_id = tool_call.id or self._next_event_id("tool")
                if tool_event_id not in started_tool_ids:
                    await self._send(
                        "toolStart",
                        data={
                            "name": tool_call.name,
                            "input": summarize_tool_input(tool_call, self.file_state),
                        },
                        event_id=tool_event_id,
                    )

                if tool_call.name == "create_file":
                    content = extract_content_from_args(tool_call.arguments)
                    if content:
                        await self._stream_code_preview(tool_event_id, content)

                # Timing starts here, after the cosmetic preview stream, so
                # tool durations measure execution only.
                if self.recorder is not None:
                    self.recorder.record_tool_start(tool_event_id, tool_call)
                tool_result = await self.tool_runtime.execute(tool_call)
                if self.recorder is not None:
                    self.recorder.record_tool_end(
                        tool_event_id, tool_call, tool_result
                    )
                if tool_result.updated_content:
                    await self._send("setCode", tool_result.updated_content)
                    if self.recorder is not None:
                        self.recorder.record_set_code(
                            len(tool_result.updated_content), "tool_result"
                        )

                await self._send(
                    "toolResult",
                    data={
                        "name": tool_call.name,
                        "output": tool_result.summary,
                        "ok": tool_result.ok,
                    },
                    event_id=tool_event_id,
                )
                executed_tool_calls.append(
                    ExecutedToolCall(tool_call=tool_call, result=tool_result)
                )

            await session.append_tool_results(turn, executed_tool_calls)

        raise Exception("Agent exceeded max tool turns")

    async def run(
        self,
        model: Llm,
        prompt_messages: List[ChatCompletionMessageParam],
        variant_model_config: "VariantModelConfig | None" = None,
    ) -> str:
        self.tool_runtime.input_images = self._extract_input_images(prompt_messages)
        seed_file_state_from_messages(self.file_state, prompt_messages)

        if self.recorder is not None:
            self.recorder.record_run_start(model, prompt_messages)

        session = self._build_session(
            model=model,
            prompt_messages=prompt_messages,
            variant_model_config=variant_model_config,
        )
        try:
            try:
                result = await self._run_with_session(session)
            except EmptyOutputError:
                print(f"[RETRY] Variant {self.variant_index} produced empty output, retrying turn...")
                result = await self._run_with_session(session)

            if not result:
                raise EmptyOutputError()
            if not check_stack_compliance(result, self.stack):
                print(
                    f"[STACK] Variant {self.variant_index} output does not appear "
                    f"to follow the selected stack '{self.stack}'. Attempting repair."
                )
                repaired = await self._attempt_stack_repair(session)
                if repaired is not None:
                    result = repaired
            if self.recorder is not None:
                await self.recorder.record_run_end("completed", final_html=result)
            return result
        # BaseException so cancellation (client disconnect) still finalizes
        # the run record instead of leaving it stuck at "running".
        except BaseException as exc:
            if self.recorder is not None:
                await self.recorder.record_run_end(
                    "failed",
                    error="".join(
                        traceback.format_exception_only(type(exc), exc)
                    ).strip(),
                )
            raise
        finally:
            # Capture spend before closing: providers reset nothing on close,
            # but the session object is unreachable to callers after run().
            try:
                self.last_cost_usd = session.total_cost_usd()
                self.last_token_usage = session.total_usage()
            except Exception:
                pass
            await session.close()

    async def _attempt_stack_repair(self, session: ProviderSession) -> Optional[str]:
        """One-shot follow-up turn asking the model to convert its output
        to the selected stack. Best effort: any failure keeps the original
        (non-compliant but working) output instead of failing the variant.
        """
        if not self.stack:
            return None
        try:
            await self._send("status", "Adjusting output to match the selected stack...")
            await session.append_user_turn(
                self._last_turn, build_stack_repair_message(self.stack)
            )
            repaired = await self._run_with_session(session)
        except Exception as exc:
            print(
                f"[STACK] Variant {self.variant_index} repair attempt failed: {exc}"
            )
            return None
        if repaired and check_stack_compliance(repaired, self.stack):
            print(f"[STACK] Variant {self.variant_index} repaired successfully.")
            return repaired
        print(
            f"[STACK] Variant {self.variant_index} repair did not produce a "
            f"compliant document; keeping the original output."
        )
        return None

    async def _finalize_response(self, assistant_text: str) -> str:
        if self.file_state.content:
            return self.file_state.content

        if not assistant_text or not assistant_text.strip():
            raise EmptyOutputError()

        html = extract_html_content(assistant_text)
        if not html or len(html.strip()) < 10:
            clean_text = assistant_text.strip()
            inner = (
                '  <div class="p-8 rounded-xl text-center max-w-md mx-auto">\n'
                f"    <h1>Generated Design</h1>\n    <p>{clean_text}</p>\n  </div>"
            )
            html = build_fallback_document(inner, self.stack)

        self.file_state.content = html
        await self._send("setCode", html)
        if self.recorder is not None:
            self.recorder.record_set_code(len(html), "finalize")

        return self.file_state.content
