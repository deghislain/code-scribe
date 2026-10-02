"""Stack detection: language, framework, build system, GUI presence."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import TypedDict

from backend.agent import llm


class StackProfile(TypedDict):
    languages: list[str]
    frameworks: list[str]
    build_system: str
    test_frameworks: list[str]
    has_gui: bool
    gui_type: str          # "web" | "desktop" | "none"
    entry_points: list[str]
    package_managers: list[str]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_safe(path: Path, max_bytes: int = 4096) -> str:
    try:
        return path.read_text(errors="replace")[:max_bytes]
    except OSError:
        return ""


def _tree_listing(root: Path, max_files: int = 200) -> str:
    """Return a compact directory tree string."""
    lines: list[str] = []
    for i, p in enumerate(sorted(root.rglob("*"))):
        if i >= max_files:
            lines.append("... (truncated)")
            break
        # Skip hidden dirs / __pycache__ / node_modules
        parts = p.relative_to(root).parts
        if any(part.startswith(".") or part in {"__pycache__", "node_modules", ".git"} for part in parts):
            continue
        lines.append(str(p.relative_to(root)))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect(repo_dir: str) -> StackProfile:
    """Analyse *repo_dir* and return a StackProfile."""
    root = Path(repo_dir)

    languages: set[str] = set()
    frameworks: set[str] = set()
    test_frameworks: set[str] = set()
    package_managers: list[str] = []
    build_system = "unknown"
    entry_points: list[str] = []
    has_gui = False
    gui_type = "none"

    # --- Python detection ----------------------------------------------------
    _py_dep_files = [
        root / "pyproject.toml",
        root / "requirements.txt",
        root / "setup.py",
        root / "uv.lock",
        root / "Pipfile",
        root / "Pipfile.lock",
        root / "poetry.lock",
    ]
    if any(f.exists() for f in _py_dep_files):
        languages.add("Python")

        # Package manager / build system detection
        if (root / "uv.lock").exists():
            package_managers.append("uv")
            build_system = "uv"
        if (root / "pyproject.toml").exists():
            if "pip" not in package_managers:
                package_managers.append("pip")
            if build_system == "unknown":
                build_system = "setuptools/pyproject"
        elif (root / "requirements.txt").exists():
            if "pip" not in package_managers:
                package_managers.append("pip")
        if (root / "Pipfile").exists() and "pipenv" not in package_managers:
            package_managers.append("pipenv")

        # Python framework detection — scan all available dep/lock files
        reqs_text = (
            _read_safe(root / "requirements.txt")
            + _read_safe(root / "pyproject.toml")
            + _read_safe(root / "Pipfile")
            # uv.lock and poetry.lock list package names one per line; read a
            # generous slice so we don't miss packages in large lock files.
            + _read_safe(root / "uv.lock", max_bytes=65536)
            + _read_safe(root / "poetry.lock", max_bytes=65536)
        )

        _fw_keywords = [
            ("Flask", "flask"),
            ("Django", "django"),
            ("FastAPI", "fastapi"),
            ("Streamlit", "streamlit"),
            ("Tkinter", "tkinter"),
            ("PyQt", "pyqt"),
            ("wxPython", "wxpython"),
        ]
        for fw, kw in _fw_keywords:
            if kw.lower() in reqs_text.lower():
                frameworks.add(fw)
                if fw in {"Flask", "Django", "FastAPI", "Streamlit"}:
                    has_gui = True
                    gui_type = "web"
                elif fw in {"Tkinter", "PyQt", "wxPython"}:
                    has_gui = True
                    gui_type = "desktop"

        # Second-pass: if no framework found from dep files, scan .py source
        # imports at the repo root.  This catches projects like Quizer where
        # the only lockfile is uv.lock but the keyword appears under a long
        # [[package]] block that was truncated.
        if not frameworks:
            for py_file in sorted(root.glob("*.py"))[:10]:
                try:
                    src = py_file.read_text(errors="replace")
                except OSError:
                    continue
                for fw, kw in _fw_keywords:
                    if f"import {kw}" in src.lower() or f"from {kw}" in src.lower():
                        frameworks.add(fw)
                        if fw in {"Flask", "Django", "FastAPI", "Streamlit"}:
                            has_gui = True
                            gui_type = "web"
                        elif fw in {"Tkinter", "PyQt", "wxPython"}:
                            has_gui = True
                            gui_type = "desktop"

        # Python test frameworks
        for tf, kw in [("pytest", "pytest"), ("unittest", "unittest"), ("nose", "nose")]:
            if kw in reqs_text.lower():
                test_frameworks.add(tf)

        # Check for pytest.ini / setup.cfg
        for f in ["pytest.ini", "setup.cfg", "tox.ini"]:
            if (root / f).exists():
                test_frameworks.add("pytest")
                break

        # Entry points: check common patterns first
        for ep in ["app.py", "main.py", "manage.py", "run.py", "server.py"]:
            if (root / ep).exists():
                entry_points.append(ep)

        # Also include any root-level .py that imports a detected web framework
        # (e.g. quizzer.py importing streamlit in the Quizer project).
        detected_fw_kws = {kw for _, kw in _fw_keywords if _ in frameworks}
        for py_file in sorted(root.glob("*.py")):
            rel = py_file.name
            if rel in entry_points:
                continue
            try:
                src = py_file.read_text(errors="replace").lower()
            except OSError:
                continue
            if any(
                f"import {kw}" in src or f"from {kw}" in src
                for kw in detected_fw_kws
            ):
                entry_points.append(rel)

    # --- JavaScript / TypeScript detection -----------------------------------
    pkg_json_path = root / "package.json"
    if pkg_json_path.exists():
        languages.add("JavaScript")
        package_managers.append("npm")
        pkg_json = _read_safe(pkg_json_path)
        try:
            pkg = json.loads(pkg_json)
        except json.JSONDecodeError:
            pkg = {}

        all_deps = {
            **pkg.get("dependencies", {}),
            **pkg.get("devDependencies", {}),
        }
        dep_str = " ".join(all_deps.keys()).lower()

        if "typescript" in dep_str:
            languages.add("TypeScript")

        if "yarn.lock" in os.listdir(root):
            package_managers.append("yarn")

        for fw, kw in [
            ("React", "react"),
            ("Vue", "vue"),
            ("Angular", "@angular/core"),
            ("Svelte", "svelte"),
            ("Next.js", "next"),
            ("Express", "express"),
        ]:
            if kw.lower() in dep_str:
                frameworks.add(fw)

        # JS projects are always GUI
        has_gui = True
        gui_type = "web"

        for tf in ["jest", "mocha", "vitest", "jasmine", "karma"]:
            if tf in dep_str:
                test_frameworks.add(tf)

        # Build system
        scripts = pkg.get("scripts", {})
        if "build" in scripts:
            build_cmd = scripts["build"]
            if "vite" in build_cmd:
                build_system = "vite"
            elif "webpack" in build_cmd:
                build_system = "webpack"
            elif "tsc" in build_cmd:
                build_system = "tsc"
            else:
                build_system = "npm-script"

        if (root / "index.js").exists() or (root / "server.js").exists():
            entry_points.extend(["index.js", "server.js"])

    # --- Java detection ------------------------------------------------------
    if (root / "pom.xml").exists():
        languages.add("Java")
        frameworks.add("Maven")
        build_system = "maven"
        package_managers.append("mvn")
        pom_text = _read_safe(root / "pom.xml")
        if "javafx" in pom_text.lower():
            frameworks.add("JavaFX")
            has_gui = True
            gui_type = "desktop"
        if "spring" in pom_text.lower():
            frameworks.add("Spring")
            has_gui = True
            gui_type = "web"
        if "junit" in pom_text.lower():
            test_frameworks.add("JUnit")

    if (root / "build.gradle").exists() or (root / "build.gradle.kts").exists():
        languages.add("Java")
        build_system = "gradle"
        package_managers.append("gradle")

    # --- Rust detection ------------------------------------------------------
    if (root / "Cargo.toml").exists():
        languages.add("Rust")
        build_system = "cargo"
        package_managers.append("cargo")

    # --- Go detection --------------------------------------------------------
    if (root / "go.mod").exists():
        languages.add("Go")
        build_system = "go"
        package_managers.append("go")

    # --- Makefile ------------------------------------------------------------
    if (root / "Makefile").exists() and build_system == "unknown":
        build_system = "make"

    # --- Static HTML/template check (for GUI in any lang) --------------------
    html_files = list(root.glob("**/*.html"))
    if html_files and not has_gui:
        has_gui = True
        gui_type = "web"

    # --- Fallback: ask LLM if ambiguous -------------------------------------
    if not languages:
        tree = _tree_listing(root)
        prompt = (
            f"Here is the directory listing of a software project:\n\n{tree}\n\n"
            "Identify the primary programming language(s), frameworks, build system, "
            "test framework(s), and whether it has a GUI (web or desktop). "
            "Respond as JSON with keys: languages, frameworks, build_system, "
            "test_frameworks, has_gui, gui_type, entry_points, package_managers."
        )
        raw = llm.ask_structured(prompt, schema_hint=(
            '{"languages":["..."],"frameworks":["..."],"build_system":"...",'
            '"test_frameworks":["..."],"has_gui":true,"gui_type":"web|desktop|none",'
            '"entry_points":["..."],"package_managers":["..."]}'
        ))
        try:
            parsed = json.loads(raw)
            return StackProfile(
                languages=parsed.get("languages", []),
                frameworks=parsed.get("frameworks", []),
                build_system=parsed.get("build_system", "unknown"),
                test_frameworks=parsed.get("test_frameworks", []),
                has_gui=parsed.get("has_gui", False),
                gui_type=parsed.get("gui_type", "none"),
                entry_points=parsed.get("entry_points", []),
                package_managers=parsed.get("package_managers", []),
            )
        except (json.JSONDecodeError, KeyError):
            pass

    return StackProfile(
        languages=sorted(languages),
        frameworks=sorted(frameworks),
        build_system=build_system,
        test_frameworks=sorted(test_frameworks),
        has_gui=has_gui,
        gui_type=gui_type,
        entry_points=entry_points,
        package_managers=package_managers,
    )
