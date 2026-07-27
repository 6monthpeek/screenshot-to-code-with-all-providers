from typing import cast

from openai.types.chat import ChatCompletionContentPartParam, ChatCompletionMessageParam

from prompts.prompt_types import PromptHistoryMessage

Prompt = list[ChatCompletionMessageParam]


def _wrap_assistant_file_content(content: str, path: str = "index.html") -> str:
    stripped = content.strip()
    if stripped.startswith("<file ") and stripped.endswith("</file>"):
        return stripped
    return f'<file path="{path}">\n{stripped}\n</file>'


def _as_dict(obj: object) -> dict[str, object]:
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, (tuple, list)):
        try:
            return dict(obj)
        except Exception:
            return {}
    if hasattr(obj, "items"):
        return dict(obj.items())  # type: ignore
    return {}


def build_history_message(item: PromptHistoryMessage) -> ChatCompletionMessageParam:
    item_dict = _as_dict(item)
    role = str(item_dict.get("role", "user"))
    raw_images = item_dict.get("images")
    raw_videos = item_dict.get("videos")
    image_urls = raw_images if isinstance(raw_images, list) else []
    video_urls = raw_videos if isinstance(raw_videos, list) else []
    media_urls = [*image_urls, *video_urls]
    text_val = str(item_dict.get("text", ""))

    if role == "user" and len(media_urls) > 0:
        user_content: list[ChatCompletionContentPartParam] = []

        for media_url in media_urls:
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": str(media_url), "detail": "high"},
                }
            )

        user_content.append(
            {
                "type": "text",
                "text": text_val,
            }
        )

        return cast(
            ChatCompletionMessageParam,
            {
                "role": role,
                "content": user_content,
            },
        )

    return cast(
        ChatCompletionMessageParam,
        {
            "role": role,
            "content": (
                _wrap_assistant_file_content(text_val)
                if role == "assistant"
                else text_val
            ),
        },
    )
