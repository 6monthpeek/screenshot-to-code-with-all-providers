from prompts.prompt_types import Stack

# Human-readable stack names shown to the model (and in logs).
STACK_DISPLAY_NAMES: dict[Stack, str] = {
    "html_css": "HTML + CSS",
    "html_tailwind": "HTML + Tailwind",
    "react_tailwind": "React + Tailwind",
    "bootstrap": "HTML + Bootstrap",
    "ionic_tailwind": "Ionic + Tailwind",
    "vue_tailwind": "Vue + Tailwind",
}

# One-sentence, non-negotiable output requirement per stack. This is the
# strongest per-request signal the model gets, so framework stacks state
# explicitly that plain static HTML is NOT acceptable.
_STACK_REQUIREMENTS: dict[Stack, str] = {
    "html_css": (
        "Write plain HTML, CSS and JavaScript only. Do NOT use Tailwind or any "
        "other CSS framework."
    ),
    "html_tailwind": "Write plain HTML styled with Tailwind utility classes.",
    "react_tailwind": (
        "The UI MUST be built as React function components written in JSX "
        "(className, not class) inside a <script type=\"text/babel\"> block and "
        "rendered with ReactDOM into a root div, styled with Tailwind utility "
        "classes. Plain static HTML markup in <body> is NOT acceptable for this "
        "stack."
    ),
    "bootstrap": (
        "Build the UI with Bootstrap 5 components and utility classes loaded "
        "from the Bootstrap CDN. Do NOT use Tailwind."
    ),
    "ionic_tailwind": (
        "Build the UI with Ionic web components (ion-* tags) styled with "
        "Tailwind utility classes."
    ),
    "vue_tailwind": (
        "The UI MUST be built as a Vue 3 app using the global CDN build "
        "(createApp / templates), styled with Tailwind utility classes. Plain "
        "static HTML markup without Vue is NOT acceptable for this stack."
    ),
}


def build_selected_stack_policy(stack: Stack) -> str:
    display_name = STACK_DISPLAY_NAMES.get(stack, stack)
    requirement = _STACK_REQUIREMENTS.get(stack, "")
    return (
        f"Selected stack: {display_name} ({stack}). {requirement} "
        "Follow the selected-stack section of your system instructions exactly."
    ).strip()


def build_stack_repair_message(stack: str) -> str:
    """Follow-up user message sent when the final output ignored the stack.

    Used by the agent engine's one-shot repair turn: the model already
    produced a working page, so the ask is a conversion, not a redesign.
    """
    display_name = STACK_DISPLAY_NAMES.get(stack, stack)  # type: ignore[arg-type]
    requirement = _STACK_REQUIREMENTS.get(stack, "")  # type: ignore[arg-type]
    return (
        f"The file you produced does not follow the selected stack: "
        f"{display_name} ({stack}). {requirement} "
        "Rewrite the existing file now by calling create_file with the full "
        "corrected document. Keep the visual design, layout, text and assets "
        "exactly the same - only change the implementation to match the "
        "selected stack."
    ).strip()


def build_user_image_policy(image_generation_enabled: bool) -> str:
    if image_generation_enabled:
        return (
            "Image generation is enabled for this request. Use generate_images for "
            "missing assets when needed."
        )

    return (
        "Image generation is disabled for this request. Do not call generate_images. "
        "Use provided media, CSS effects, or placeholder URLs (https://placehold.co)."
    )
