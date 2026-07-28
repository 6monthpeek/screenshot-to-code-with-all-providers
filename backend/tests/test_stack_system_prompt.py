"""Tests for per-stack prompt construction and stack-aware fallbacks.

These lock in the fix for the "selected React + Tailwind but got plain
HTML" bug: the system prompt must contain only the selected stack's
instructions, the stack policy line must be explicit, and fallback
document shells must include the stack's CDN scripts.
"""

import pytest

from codegen.utils import build_fallback_document, check_stack_compliance
from prompts.pipeline import build_prompt_messages
from prompts.policies import build_selected_stack_policy
from prompts.prompt_types import Stack
from prompts.system_prompt import build_system_prompt

TAILWIND_CDN = "https://cdn.tailwindcss.com"
REACT_CDN = "react@18.0.0/umd/react.development.js"
BABEL_CDN = "@babel/standalone@7.25.6/babel.min.js"
VUE_CDN = "vue.global.js"
BOOTSTRAP_CDN = "bootstrap@5.3.2/dist/css/bootstrap.min.css"
IONIC_CDN = "@ionic/core/dist/ionic/ionic.esm.js"


class TestBuildSystemPrompt:
    def test_react_prompt_contains_only_react_stack(self) -> None:
        prompt = build_system_prompt("react_tailwind")
        assert "Selected stack: React + Tailwind (MANDATORY)" in prompt
        assert REACT_CDN in prompt
        assert BABEL_CDN in prompt
        assert TAILWIND_CDN in prompt
        # Competing framework instructions must not leak in.
        assert VUE_CDN not in prompt
        assert BOOTSTRAP_CDN not in prompt
        assert IONIC_CDN not in prompt

    def test_react_prompt_forbids_static_html(self) -> None:
        prompt = build_system_prompt("react_tailwind")
        assert "MUST be implemented as React function components" in prompt
        assert "Do NOT write the UI as plain static HTML" in prompt

    def test_html_css_prompt_has_no_frameworks(self) -> None:
        prompt = build_system_prompt("html_css")
        assert "Selected stack: HTML + CSS (MANDATORY)" in prompt
        assert TAILWIND_CDN not in prompt
        assert REACT_CDN not in prompt
        assert BOOTSTRAP_CDN not in prompt

    def test_vue_prompt_contains_vue_globals_example(self) -> None:
        prompt = build_system_prompt("vue_tailwind")
        assert VUE_CDN in prompt
        # The f-string escaping must produce literal Vue braces.
        assert "{{ message }}" in prompt
        assert "const { createApp, ref } = Vue" in prompt
        assert REACT_CDN not in prompt

    def test_ionic_prompt_omits_font_awesome(self) -> None:
        prompt = build_system_prompt("ionic_tailwind")
        assert IONIC_CDN in prompt
        assert "font-awesome" not in prompt.lower()

    def test_every_stack_builds_and_keeps_shared_sections(self) -> None:
        stacks: list[Stack] = [
            "html_css",
            "html_tailwind",
            "react_tailwind",
            "bootstrap",
            "ionic_tailwind",
            "vue_tailwind",
        ]
        for stack in stacks:
            prompt = build_system_prompt(stack)
            assert "You are a coding agent" in prompt
            assert "create_file" in prompt
            assert "# Targeted element edits" in prompt


class TestSelectedStackPolicy:
    def test_react_policy_is_explicit(self) -> None:
        policy = build_selected_stack_policy("react_tailwind")
        assert "Selected stack: React + Tailwind (react_tailwind)." in policy
        assert "NOT acceptable" in policy

    def test_html_css_policy_forbids_tailwind(self) -> None:
        policy = build_selected_stack_policy("html_css")
        assert "Do NOT use Tailwind" in policy


class TestFallbackDocument:
    def test_react_fallback_includes_react_cdns(self) -> None:
        doc = build_fallback_document("<div>content</div>", "react_tailwind")
        assert REACT_CDN in doc
        assert BABEL_CDN in doc
        assert TAILWIND_CDN in doc

    def test_html_css_fallback_has_no_tailwind(self) -> None:
        doc = build_fallback_document("<div>content</div>", "html_css")
        assert TAILWIND_CDN not in doc
        assert "<div>content</div>" in doc

    def test_unknown_stack_defaults_to_tailwind(self) -> None:
        doc = build_fallback_document("<div>content</div>", None)
        assert TAILWIND_CDN in doc


class TestStackCompliance:
    def test_plain_html_fails_react_compliance(self) -> None:
        plain = "<!DOCTYPE html><html><body><div>hi</div></body></html>"
        assert check_stack_compliance(plain, "react_tailwind") is False

    def test_react_output_passes_react_compliance(self) -> None:
        doc = build_fallback_document("<div id='root'></div>", "react_tailwind")
        assert check_stack_compliance(doc, "react_tailwind") is True

    def test_html_stacks_always_pass(self) -> None:
        plain = "<!DOCTYPE html><html><body><div>hi</div></body></html>"
        assert check_stack_compliance(plain, "html_tailwind") is True
        assert check_stack_compliance(plain, "html_css") is True
        assert check_stack_compliance(plain, None) is True


class TestPipelineUsesStackSpecificPrompt:
    @pytest.mark.asyncio
    async def test_create_image_prompt_for_react(self) -> None:
        messages = await build_prompt_messages(
            stack="react_tailwind",
            input_mode="image",
            generation_type="create",
            prompt={
                "text": "",
                "images": ["data:image/png;base64,abc"],
                "videos": [],
            },
            history=[],
        )
        system_content = messages[0].get("content")
        assert isinstance(system_content, str)
        assert "Selected stack: React + Tailwind (MANDATORY)" in system_content
        assert REACT_CDN in system_content
        assert VUE_CDN not in system_content

        user_content = messages[1].get("content")
        assert isinstance(user_content, list)
        text_part = next(
            part
            for part in user_content
            if isinstance(part, dict) and part.get("type") == "text"
        )
        user_text = text_part.get("text")
        assert isinstance(user_text, str)
        assert "Selected stack: React + Tailwind (react_tailwind)." in user_text

    @pytest.mark.asyncio
    async def test_update_from_history_prompt_for_vue(self) -> None:
        messages = await build_prompt_messages(
            stack="vue_tailwind",
            input_mode="image",
            generation_type="update",
            prompt={"text": "", "images": [], "videos": []},
            history=[
                {
                    "role": "assistant",
                    "text": "<html>Initial</html>",
                    "images": [],
                    "videos": [],
                },
                {
                    "role": "user",
                    "text": "Make it blue",
                    "images": [],
                    "videos": [],
                },
            ],
        )
        system_content = messages[0].get("content")
        assert isinstance(system_content, str)
        assert "Selected stack: Vue + Tailwind (MANDATORY)" in system_content
        assert VUE_CDN in system_content
        assert REACT_CDN not in system_content
