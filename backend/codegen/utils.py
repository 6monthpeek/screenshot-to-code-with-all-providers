import re


def extract_html_content(text: str) -> str:
    if not text:
        return ""

    file_match = re.search(
        r"<file\s+path=\"[^\"]+\">\s*(.*?)\s*</file>",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if file_match:
        return extract_html_content(file_match.group(1).strip())

    # Try to extract code inside ```html ... ``` blocks anywhere in the text
    code_block_match = re.search(
        r"```(?:html|xml)?\s*\n?(.*?)\n?```", text, re.DOTALL | re.IGNORECASE
    )
    if code_block_match:
        inner = code_block_match.group(1).strip()
        if "<html" in inner.lower() or "<div" in inner.lower() or "<!doctype" in inner.lower():
            return inner

    # Try to find DOCTYPE + html tags together
    match_with_doctype = re.search(
        r"(<!DOCTYPE\s+html[^>]*>.*?<html.*?>.*?</html>)", text, re.DOTALL | re.IGNORECASE
    )
    if match_with_doctype:
        return match_with_doctype.group(1)

    # Fall back to just <html> tags
    match = re.search(r"(<html.*?>.*?</html>)", text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1)

    # Fallback to any <div> or body elements if full html tag missing
    body_match = re.search(r"(<div.*?>.*?</div>)", text, re.DOTALL | re.IGNORECASE)
    if body_match and len(body_match.group(1)) > 50:
        return f"<!DOCTYPE html><html><head><script src=\"https://cdn.tailwindcss.com\"></script></head><body>{body_match.group(1)}</body></html>"

    return text.strip()
