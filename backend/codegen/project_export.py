"""Build a runnable Vite project from a generated single-file HTML document.

Two tiers, both deterministic (no LLM calls):

1. Static scaffold (all stacks): the generated index.html is kept as-is and
   wrapped with a minimal Vite setup (package.json, README, .gitignore). The
   page keeps running off its CDN runtime, but the user gets a real project
   with a dev server and build step.

2. React transform (react_tailwind only, best effort): the inline
   <script type="text/babel"> JSX is extracted into src/main.jsx, the React
   UMD + Babel standalone CDN scripts are removed, and a proper
   @vitejs/plugin-react toolchain is scaffolded. When the JSX contains two or
   more top-level function components, each one is additionally split into
   its own src/components/<Name>.jsx file (component library mode). If the
   document does not match the expected shape at any step, we silently fall
   back to the previous tier so the export can never fail because of a
   transform.
"""

import json
import re
import textwrap

from bs4 import BeautifulSoup
from bs4.element import Tag

from prompts.policies import STACK_DISPLAY_NAMES

# CDN scripts that become redundant once Vite compiles the JSX itself.
# Matches the React / ReactDOM UMD builds and Babel standalone, but not
# the Tailwind CDN script (which the static page still needs).
_REACT_RUNTIME_SRC_RE = re.compile(r"/react(-dom)?[@/]|babel", re.IGNORECASE)

_GITIGNORE = """node_modules
dist
.DS_Store
*.local
"""

_VITE_CONFIG_REACT = """import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
});
"""

_MAIN_JSX_HEADER = """import React from "react";
import ReactDOM from "react-dom/client";

"""

# A top-level function component declaration in dedented JSX: starts at
# column 0 with a capitalized name. Arrow/const components are left in place
# (splitting them is riskier to terminate reliably line-by-line).
_COMPONENT_DECL_RE = re.compile(r"^function\s+([A-Z][A-Za-z0-9_]*)\s*\(")


def _split_top_level_components(
    jsx_code: str,
) -> tuple[list[tuple[str, str]], str] | None:
    """Split dedented JSX into ([(name, source)], remaining bootstrap code).

    Only column-0 `function Name(` declarations closed by a column-0 `}` are
    treated as components. Returns None when fewer than two components are
    found, a declaration never terminates, a chunk looks structurally
    unbalanced, or the remaining code references no component - the caller
    then keeps the single-file src/main.jsx.
    """
    lines = jsx_code.splitlines()
    components: list[tuple[str, str]] = []
    remaining: list[str] = []
    i = 0
    while i < len(lines):
        match = _COMPONENT_DECL_RE.match(lines[i])
        if match is None:
            remaining.append(lines[i])
            i += 1
            continue
        end = i + 1
        while end < len(lines) and lines[end].rstrip() != "}":
            end += 1
        if end == len(lines):
            return None
        chunk = "\n".join(lines[i : end + 1])
        # Cheap structural sanity check; a mismatch means the line-based
        # scan mis-detected the end of the function.
        if chunk.count("{") != chunk.count("}"):
            return None
        components.append((match.group(1), chunk))
        i = end + 1

    if len(components) < 2:
        return None
    tail = "\n".join(remaining).strip()
    if not tail:
        return None
    if not any(
        re.search(rf"\b{name}\b", tail) for name, _ in components
    ):
        # The bootstrap code uses none of the split components - something
        # about the parse is off, so keep everything in one file.
        return None
    return components, tail


def _build_component_files(
    components: list[tuple[str, str]], tail: str
) -> dict[str, str]:
    """Emit src/components/<Name>.jsx files plus a src/main.jsx that imports
    the components the bootstrap code references."""
    names = [name for name, _ in components]
    files: dict[str, str] = {}
    for name, chunk in components:
        imports = ['import React from "react";']
        for other in names:
            if other != name and re.search(rf"\b{other}\b", chunk):
                imports.append(f'import {other} from "./{other}.jsx";')
        files[f"src/components/{name}.jsx"] = (
            "\n".join(imports) + "\n\nexport default " + chunk + "\n"
        )

    main_imports = "".join(
        f'import {name} from "./components/{name}.jsx";\n'
        for name in names
        if re.search(rf"\b{name}\b", tail)
    )
    files["src/main.jsx"] = _MAIN_JSX_HEADER + main_imports + "\n" + tail + "\n"
    return files


def _display_name(stack: str | None) -> str:
    if stack is None:
        return "Generated page"
    return STACK_DISPLAY_NAMES.get(stack, stack)  # type: ignore[arg-type]


def _build_package_json(stack: str | None, react_transformed: bool) -> str:
    package: dict[str, object] = {
        "name": "screenshot-to-code-export",
        "private": True,
        "version": "0.0.0",
        "type": "module",
        "scripts": {
            "dev": "vite",
            "build": "vite build",
            "preview": "vite preview",
        },
        "devDependencies": {"vite": "^5.4.0"},
    }
    if react_transformed:
        package["dependencies"] = {
            "react": "^18.3.1",
            "react-dom": "^18.3.1",
        }
        package["devDependencies"] = {
            "@vitejs/plugin-react": "^4.3.0",
            "vite": "^5.4.0",
        }
    return json.dumps(package, indent=2) + "\n"


def _build_readme(
    stack: str | None, react_transformed: bool, component_split: bool = False
) -> str:
    display_name = _display_name(stack)
    if component_split:
        details = (
            "The generated JSX was split into one file per component under "
            "`src/components/`, with `src/main.jsx` importing them and "
            "mounting the app. Everything is compiled by Vite with "
            "`@vitejs/plugin-react`, so there is no in-browser Babel step. "
            "Tailwind still loads from its CDN script in `index.html`."
        )
    elif react_transformed:
        details = (
            "The generated JSX was extracted into `src/main.jsx` and is "
            "compiled by Vite with `@vitejs/plugin-react`, so there is no "
            "in-browser Babel step. Tailwind still loads from its CDN "
            "script in `index.html`."
        )
    else:
        details = (
            "The page in `index.html` runs exactly as generated and loads "
            "its framework runtime from CDN scripts, so no bundling of the "
            "page code happens - Vite provides the dev server and build "
            "pipeline around it."
        )
    return f"""# Screenshot to Code export ({display_name})

This project was exported from screenshot-to-code as a runnable Vite project.

{details}

## Getting started

```bash
npm install
npm run dev
```

Then open the printed local URL. `npm run build` produces a production build
in `dist/`, and `npm run preview` serves that build locally.

Downloaded images (if any) live in `assets/` and are referenced with
relative paths from `index.html`.
"""


def _try_react_transform(index_html: str) -> dict[str, str] | None:
    """Extract inline JSX into src/main.jsx. Returns None if the document
    does not match the expected react_tailwind shape."""
    soup = BeautifulSoup(index_html, "html.parser")

    babel_scripts: list[Tag] = []
    for script in soup.find_all("script"):
        if not isinstance(script, Tag):
            continue
        script_type = script.get("type")
        if isinstance(script_type, str) and script_type.strip().lower() == "text/babel":
            babel_scripts.append(script)

    if not babel_scripts:
        return None

    jsx_parts: list[str] = []
    for script in babel_scripts:
        # External JSX sources cannot be extracted deterministically.
        if script.get("src"):
            return None
        # Dedent so top-level declarations sit at column 0, which both the
        # emitted main.jsx and the component splitter rely on.
        jsx_parts.append(textwrap.dedent(script.get_text()))

    jsx_code = "\n\n".join(part.strip() for part in jsx_parts).strip()
    if not jsx_code:
        return None

    body = soup.body
    if body is None:
        return None

    for script in babel_scripts:
        script.decompose()

    for script in soup.find_all("script"):
        if not isinstance(script, Tag):
            continue
        src = script.get("src")
        if isinstance(src, str) and _REACT_RUNTIME_SRC_RE.search(src):
            script.decompose()

    entry = soup.new_tag("script", type="module", src="/src/main.jsx")
    body.append(entry)

    transformed: dict[str, str] = {"index.html": str(soup)}
    split = _split_top_level_components(jsx_code)
    if split is not None:
        components, tail = split
        transformed.update(_build_component_files(components, tail))
    else:
        transformed["src/main.jsx"] = _MAIN_JSX_HEADER + jsx_code + "\n"
    return transformed


def build_project_files(index_html: str, stack: str | None) -> dict[str, str]:
    """Return the full text-file map (path -> content) for a project export.

    Always includes index.html; binary assets are zipped separately by the
    export route.
    """
    files: dict[str, str] = {"index.html": index_html}
    react_transformed = False

    if stack == "react_tailwind":
        transformed = _try_react_transform(index_html)
        if transformed is not None:
            files = transformed
            files["vite.config.js"] = _VITE_CONFIG_REACT
            react_transformed = True

    component_split = any(path.startswith("src/components/") for path in files)
    files["package.json"] = _build_package_json(stack, react_transformed)
    files[".gitignore"] = _GITIGNORE
    files["README.md"] = _build_readme(stack, react_transformed, component_split)
    return files
