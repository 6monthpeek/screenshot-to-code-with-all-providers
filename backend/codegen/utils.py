import re

# CDN includes used when we must synthesize a document shell around model
# output that was not delivered through create_file. Keyed by stack so the
# fallback never silently downgrades a framework stack to plain HTML.
_TAILWIND_SCRIPT = '<script src="https://cdn.tailwindcss.com"></script>'
_STACK_HEAD_INCLUDES: dict[str, str] = {
    "html_tailwind": _TAILWIND_SCRIPT,
    "html_css": "",
    "react_tailwind": (
        '<script src="https://cdn.jsdelivr.net/npm/react@18.0.0/umd/react.development.js"></script>\n'
        '  <script src="https://cdn.jsdelivr.net/npm/react-dom@18.0.0/umd/react-dom.development.js"></script>\n'
        '  <script src="https://unpkg.com/@babel/standalone@7.25.6/babel.min.js"></script>\n'
        f"  {_TAILWIND_SCRIPT}"
    ),
    "bootstrap": (
        '<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">'
    ),
    "vue_tailwind": (
        '<script src="https://registry.npmmirror.com/vue/3.3.11/files/dist/vue.global.js"></script>\n'
        f"  {_TAILWIND_SCRIPT}"
    ),
    "ionic_tailwind": (
        '<script type="module" src="https://cdn.jsdelivr.net/npm/@ionic/core/dist/ionic/ionic.esm.js"></script>\n'
        '  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@ionic/core/css/ionic.bundle.css" />\n'
        f"  {_TAILWIND_SCRIPT}"
    ),
}

# Case-insensitive markers that must appear somewhere in the generated file
# for the output to plausibly follow the selected stack.
_STACK_COMPLIANCE_MARKERS: dict[str, list[str]] = {
    "react_tailwind": ["react"],
    "vue_tailwind": ["vue"],
    "bootstrap": ["bootstrap"],
    "ionic_tailwind": ["ionic"],
}


def build_fallback_document(inner_content: str, stack: str | None = None) -> str:
    """Wrap loose content in a full HTML shell with stack-appropriate CDNs."""
    head_includes = _STACK_HEAD_INCLUDES.get(stack or "", _TAILWIND_SCRIPT)
    head_block = f"\n  {head_includes}" if head_includes else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">{head_block}
</head>
<body>
{inner_content}
</body>
</html>"""


def check_stack_compliance(html: str, stack: str | None) -> bool:
    """Best-effort check that the output uses the selected stack.

    Only framework stacks are checked (a Tailwind/plain-HTML output can't
    really be "non-compliant" in a detectable way). Used for logging and
    diagnostics, never to reject output.
    """
    if not stack or not html:
        return True
    markers = _STACK_COMPLIANCE_MARKERS.get(stack)
    if not markers:
        return True
    lowered = html.lower()
    return all(marker in lowered for marker in markers)


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
        return build_fallback_document(body_match.group(1))

    return text.strip()
