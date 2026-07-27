from dataclasses import dataclass
from typing import Any, List

from openai.types.chat import ChatCompletionMessageParam

from codegen.utils import extract_html_content


@dataclass
class AgentFileState:
    path: str = "index.html"
    content: str = ""


def ensure_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _as_dict(msg: Any) -> dict[str, Any]:
    if isinstance(msg, dict):
        return msg
    if isinstance(msg, (tuple, list)):
        try:
            return dict(msg)
        except Exception:
            return {}
    if hasattr(msg, "items"):
        return dict(msg.items())
    return {}


def extract_text_content(message: ChatCompletionMessageParam) -> str:
    msg_dict = _as_dict(message)
    content = msg_dict.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for part in content:
            part_dict = _as_dict(part)
            if part_dict.get("type") == "text":
                return ensure_str(part_dict.get("text"))
    return ""


def seed_file_state_from_messages(
    file_state: AgentFileState,
    prompt_messages: List[ChatCompletionMessageParam],
) -> None:
    if file_state.content:
        return

    for message in reversed(prompt_messages):
        msg_dict = _as_dict(message)
        if msg_dict.get("role") != "assistant":
            continue
        raw_text = extract_text_content(message)
        if not raw_text:
            continue
        extracted = extract_html_content(raw_text)
        file_state.content = extracted or raw_text
        if not file_state.path:
            file_state.path = "index.html"
        return

    if not prompt_messages:
        return

    system_message = prompt_messages[0]
    sys_dict = _as_dict(system_message)
    if sys_dict.get("role") != "system":
        return

    system_text = extract_text_content(system_message)
    markers = [
        "Here is the code of the app:",
    ]
    for marker in markers:
        if marker not in system_text:
            continue
        raw_text = system_text.split(marker, 1)[1].strip()
        extracted = extract_html_content(raw_text)
        file_state.content = extracted or raw_text
        if not file_state.path:
            file_state.path = "index.html"
        return
