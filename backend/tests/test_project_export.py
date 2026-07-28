"""Tests for the Vite project export (codegen/project_export.py + route)."""

import json
from io import BytesIO
from zipfile import ZipFile

import pytest

from codegen.project_export import build_project_files
from routes.export import ExportRequest, export_code

REACT_HTML = """<html>
  <head>
    <title>App</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/react@18.0.0/umd/react.development.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/react-dom@18.0.0/umd/react-dom.development.js"></script>
    <script src="https://unpkg.com/@babel/standalone@7.25.6/babel.min.js"></script>
  </head>
  <body>
    <div id="root"></div>
    <script type="text/babel">
      function App() {
        const [count, setCount] = React.useState(0);
        return <button className="p-4">{count}</button>;
      }
      ReactDOM.createRoot(document.getElementById("root")).render(<App />);
    </script>
  </body>
</html>"""

PLAIN_HTML = """<html>
  <head><script src="https://cdn.tailwindcss.com"></script></head>
  <body><h1 class="text-xl">Hello</h1></body>
</html>"""

MULTI_COMPONENT_HTML = """<html>
  <head>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/react@18.0.0/umd/react.development.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/react-dom@18.0.0/umd/react-dom.development.js"></script>
    <script src="https://unpkg.com/@babel/standalone@7.25.6/babel.min.js"></script>
  </head>
  <body>
    <div id="root"></div>
    <script type="text/babel">
      function Header() {
        return <header className="p-4">Site</header>;
      }
      function Hero({ title }) {
        return <section className="p-8">{title}</section>;
      }
      function App() {
        return (
          <div>
            <Header />
            <Hero title="Hi" />
          </div>
        );
      }
      ReactDOM.createRoot(document.getElementById("root")).render(<App />);
    </script>
  </body>
</html>"""


class TestStaticScaffold:
    def test_static_stack_keeps_index_html_untouched(self) -> None:
        files = build_project_files(PLAIN_HTML, "html_tailwind")

        assert files["index.html"] == PLAIN_HTML
        assert sorted(files) == [".gitignore", "README.md", "index.html", "package.json"]

    def test_package_json_is_valid_and_has_vite_only(self) -> None:
        files = build_project_files(PLAIN_HTML, "html_tailwind")
        package = json.loads(files["package.json"])

        assert package["scripts"] == {
            "dev": "vite",
            "build": "vite build",
            "preview": "vite preview",
        }
        assert "vite" in package["devDependencies"]
        assert "dependencies" not in package

    def test_readme_mentions_stack_display_name(self) -> None:
        files = build_project_files(PLAIN_HTML, "vue_tailwind")

        assert "Vue + Tailwind" in files["README.md"]
        assert "npm run dev" in files["README.md"]

    def test_unknown_or_missing_stack_falls_back_gracefully(self) -> None:
        assert "README.md" in build_project_files(PLAIN_HTML, None)
        assert "custom_stack" in build_project_files(PLAIN_HTML, "custom_stack")["README.md"]


class TestReactTransform:
    def test_react_stack_extracts_jsx_into_main_jsx(self) -> None:
        files = build_project_files(REACT_HTML, "react_tailwind")

        assert "src/main.jsx" in files
        main_jsx = files["src/main.jsx"]
        assert main_jsx.startswith('import React from "react";')
        assert 'import ReactDOM from "react-dom/client";' in main_jsx
        assert "function App()" in main_jsx
        assert "ReactDOM.createRoot" in main_jsx

    def test_react_index_html_swaps_cdn_runtime_for_module_entry(self) -> None:
        files = build_project_files(REACT_HTML, "react_tailwind")
        index_html = files["index.html"]

        assert 'src="/src/main.jsx"' in index_html
        assert 'type="module"' in index_html
        assert "text/babel" not in index_html
        assert "react.development.js" not in index_html
        assert "react-dom.development.js" not in index_html
        assert "babel.min.js" not in index_html
        # The Tailwind CDN script must survive the transform.
        assert "cdn.tailwindcss.com" in index_html
        assert '<div id="root">' in index_html

    def test_react_scaffold_includes_toolchain(self) -> None:
        files = build_project_files(REACT_HTML, "react_tailwind")
        package = json.loads(files["package.json"])

        assert package["dependencies"]["react"].startswith("^18")
        assert "@vitejs/plugin-react" in package["devDependencies"]
        assert "plugin-react" in files["vite.config.js"]

    def test_react_stack_without_babel_script_falls_back_to_static(self) -> None:
        files = build_project_files(PLAIN_HTML, "react_tailwind")

        assert "src/main.jsx" not in files
        assert "vite.config.js" not in files
        assert files["index.html"] == PLAIN_HTML
        package = json.loads(files["package.json"])
        assert "dependencies" not in package

    def test_external_babel_src_falls_back_to_static(self) -> None:
        html = (
            '<html><body><div id="root"></div>'
            '<script type="text/babel" src="app.jsx"></script></body></html>'
        )
        files = build_project_files(html, "react_tailwind")

        assert "src/main.jsx" not in files
        assert files["index.html"] == html


class TestComponentSplit:
    def test_multiple_components_split_into_component_files(self) -> None:
        files = build_project_files(MULTI_COMPONENT_HTML, "react_tailwind")

        assert "src/components/Header.jsx" in files
        assert "src/components/Hero.jsx" in files
        assert "src/components/App.jsx" in files

        header = files["src/components/Header.jsx"]
        assert header.startswith('import React from "react";')
        assert "export default function Header()" in header

        # App consumes the other components, so it imports them.
        app = files["src/components/App.jsx"]
        assert 'import Header from "./Header.jsx";' in app
        assert 'import Hero from "./Hero.jsx";' in app
        assert "export default function App()" in app

    def test_split_main_jsx_only_imports_referenced_components(self) -> None:
        files = build_project_files(MULTI_COMPONENT_HTML, "react_tailwind")
        main_jsx = files["src/main.jsx"]

        assert 'import App from "./components/App.jsx";' in main_jsx
        # Bootstrap only renders <App />, so the others are not imported here.
        assert 'import Header from' not in main_jsx
        assert 'import Hero from' not in main_jsx
        assert "ReactDOM.createRoot" in main_jsx
        assert "function App()" not in main_jsx

    def test_single_component_keeps_single_main_jsx(self) -> None:
        files = build_project_files(REACT_HTML, "react_tailwind")

        assert "src/main.jsx" in files
        assert not any(path.startswith("src/components/") for path in files)

    def test_arrow_components_fall_back_to_single_main_jsx(self) -> None:
        html = MULTI_COMPONENT_HTML.replace(
            "function Header() {", "const Header = () => {"
        ).replace(
            "function Hero({ title }) {", "const Hero = ({ title }) => {"
        ).replace("      }\n      function App", "      };\n      function App")
        files = build_project_files(html, "react_tailwind")

        # Only one `function` component parses -> no split, but the react
        # transform itself still succeeds.
        assert "src/main.jsx" in files
        assert not any(path.startswith("src/components/") for path in files)
        assert "const Header" in files["src/main.jsx"]

    def test_split_readme_mentions_component_files(self) -> None:
        files = build_project_files(MULTI_COMPONENT_HTML, "react_tailwind")

        assert "src/components/" in files["README.md"]


class TestExportRoute:
    @pytest.mark.asyncio
    async def test_project_format_returns_scaffolded_zip(self) -> None:
        response = await export_code(
            ExportRequest(code=PLAIN_HTML, format="project", stack="html_tailwind")
        )

        assert (
            response.headers["Content-Disposition"]
            == 'attachment; filename="screenshot-to-code-vite-project.zip"'
        )
        with ZipFile(BytesIO(response.body)) as archive:
            names = sorted(archive.namelist())
            assert names == [".gitignore", "README.md", "index.html", "package.json"]
            json.loads(archive.read("package.json"))

    @pytest.mark.asyncio
    async def test_single_format_is_unchanged_by_default(self) -> None:
        response = await export_code(ExportRequest(code=PLAIN_HTML))

        assert (
            response.headers["Content-Disposition"]
            == 'attachment; filename="screenshot-to-code-export.zip"'
        )
        with ZipFile(BytesIO(response.body)) as archive:
            assert archive.namelist() == ["index.html"]

    @pytest.mark.asyncio
    async def test_project_format_react_zip_contains_main_jsx(self) -> None:
        response = await export_code(
            ExportRequest(code=REACT_HTML, format="project", stack="react_tailwind")
        )

        with ZipFile(BytesIO(response.body)) as archive:
            names = archive.namelist()
            assert "src/main.jsx" in names
            assert "vite.config.js" in names
            main_jsx = archive.read("src/main.jsx").decode("utf-8")
            assert "function App()" in main_jsx
