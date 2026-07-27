import base64
import io
from typing import List, cast
from PIL import Image

from prompts.prompt_types import PromptHistoryMessage, UserTurnInput


def optimize_image_data_url(data_url: str, max_dimension: int = 1280) -> str:
    """Downscale and compress input base64 images so they don't overflow context windows."""
    if not data_url.startswith("data:image/") or "," not in data_url:
        return data_url

    try:
        header, encoded = data_url.split(",", 1)
        data = base64.b64decode(encoded)
        img = Image.open(io.BytesIO(data))

        w, h = img.size
        if max(w, h) > max_dimension:
            scale = max_dimension / max(w, h)
            new_size = (max(1, round(w * scale)), max(1, round(h * scale)))
            img = img.resize(new_size, Image.LANCZOS)

        has_alpha = img.mode in ("RGBA", "LA", "P") and (
            img.mode != "P" or "transparency" in img.info
        )
        buf = io.BytesIO()
        if has_alpha:
            img.save(buf, format="PNG", optimize=True)
            new_mime = "image/png"
        else:
            img.convert("RGB").save(buf, format="JPEG", quality=85, optimize=True)
            new_mime = "image/jpeg"

        new_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:{new_mime};base64,{new_b64}"
    except Exception:
        return data_url


def _to_string_list(value: object) -> List[str]:
    if not isinstance(value, list):
        return []
    raw_list = cast(List[object], value)
    return [optimize_image_data_url(item) for item in raw_list if isinstance(item, str)]


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


def parse_prompt_content(raw_prompt: object) -> UserTurnInput:
    prompt_dict = _as_dict(raw_prompt)
    text = prompt_dict.get("text")
    parsed: UserTurnInput = {
        "text": text if isinstance(text, str) else "",
        "images": _to_string_list(prompt_dict.get("images")),
        "videos": _to_string_list(prompt_dict.get("videos")),
    }

    full_text = prompt_dict.get("fullText")
    if isinstance(full_text, str) and full_text.strip():
        parsed["full_text"] = full_text

    return parsed


def parse_prompt_history(raw_history: object) -> List[PromptHistoryMessage]:
    if not isinstance(raw_history, list):
        return []

    history: List[PromptHistoryMessage] = []
    raw_items = cast(List[object], raw_history)
    for item in raw_items:
        item_dict = _as_dict(item)
        role_value = item_dict.get("role")
        if not isinstance(role_value, str) or role_value not in ("user", "assistant"):
            continue

        text = item_dict.get("text")
        history.append(
            {
                "role": role_value,
                "text": text if isinstance(text, str) else "",
                "images": _to_string_list(item_dict.get("images")),
                "videos": _to_string_list(item_dict.get("videos")),
            }
        )

    return history
