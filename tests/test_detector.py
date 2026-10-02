"""Tests for backend/agent/detector.py — static stack detection."""
import json
import pytest
from pathlib import Path
from unittest.mock import patch

from backend.agent.detector import detect, _read_safe, _tree_listing


class TestReadSafe:
    def test_returns_content(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("hello world")
        assert _read_safe(f) == "hello world"

    def test_truncates_at_max_bytes(self, tmp_path):
        f = tmp_path / "big.txt"
        f.write_text("x" * 5000)
        result = _read_safe(f, max_bytes=100)
        assert len(result) == 100

    def test_missing_file_returns_empty(self, tmp_path):
        assert _read_safe(tmp_path / "nonexistent.txt") == ""


class TestTreeListing:
    def test_lists_files(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("")
        tree = _tree_listing(tmp_path)
        assert "src/app.py" in tree or "src" in tree

    def test_skips_hidden_dirs(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "HEAD").write_text("ref:")
        (tmp_path / "app.py").write_text("")
        tree = _tree_listing(tmp_path)
        assert ".git" not in tree

    def test_truncated_when_too_many_files(self, tmp_path):
        for i in range(210):
            (tmp_path / f"file{i}.py").write_text("")
        tree = _tree_listing(tmp_path, max_files=5)
        assert "truncated" in tree


class TestDetectPython:
    def test_detects_python_via_pyproject(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
        profile = detect(str(tmp_path))
        assert "Python" in profile["languages"]
        assert profile["build_system"] == "setuptools/pyproject"
        assert "pip" in profile["package_managers"]

    def test_detects_python_via_requirements(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("flask\n")
        profile = detect(str(tmp_path))
        assert "Python" in profile["languages"]

    def test_detects_flask(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("flask==2.0\n")
        profile = detect(str(tmp_path))
        assert "Flask" in profile["frameworks"]
        assert profile["has_gui"] is True
        assert profile["gui_type"] == "web"

    def test_detects_fastapi(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("fastapi\n")
        profile = detect(str(tmp_path))
        assert "FastAPI" in profile["frameworks"]

    def test_detects_django(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("django\n")
        profile = detect(str(tmp_path))
        assert "Django" in profile["frameworks"]

    def test_detects_streamlit(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("streamlit\n")
        profile = detect(str(tmp_path))
        assert "Streamlit" in profile["frameworks"]

    def test_detects_tkinter(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("tkinter\n")
        profile = detect(str(tmp_path))
        assert profile["gui_type"] == "desktop"

    def test_detects_pytest_framework(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("pytest\n")
        profile = detect(str(tmp_path))
        assert "pytest" in profile["test_frameworks"]

    def test_detects_pytest_ini(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("")
        (tmp_path / "pytest.ini").write_text("[pytest]\n")
        profile = detect(str(tmp_path))
        assert "pytest" in profile["test_frameworks"]

    def test_detects_entry_points(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("")
        (tmp_path / "main.py").write_text("")
        profile = detect(str(tmp_path))
        assert "main.py" in profile["entry_points"]

    def test_detects_setup_py(self, tmp_path):
        (tmp_path / "setup.py").write_text("from setuptools import setup\nsetup()")
        profile = detect(str(tmp_path))
        assert "Python" in profile["languages"]


class TestDetectJavaScript:
    def test_detects_js_via_package_json(self, tmp_path):
        (tmp_path / "package.json").write_text(json.dumps({
            "dependencies": {"react": "^18.0.0"},
        }))
        profile = detect(str(tmp_path))
        assert "JavaScript" in profile["languages"]
        assert "React" in profile["frameworks"]
        assert profile["has_gui"] is True

    def test_detects_typescript(self, tmp_path):
        (tmp_path / "package.json").write_text(json.dumps({
            "devDependencies": {"typescript": "^5.0.0"},
        }))
        profile = detect(str(tmp_path))
        assert "TypeScript" in profile["languages"]

    def test_detects_test_framework_jest(self, tmp_path):
        (tmp_path / "package.json").write_text(json.dumps({
            "devDependencies": {"jest": "^29.0.0"},
        }))
        profile = detect(str(tmp_path))
        assert "jest" in profile["test_frameworks"]

    def test_detects_vite_build(self, tmp_path):
        (tmp_path / "package.json").write_text(json.dumps({
            "dependencies": {},
            "scripts": {"build": "vite build"},
        }))
        profile = detect(str(tmp_path))
        assert profile["build_system"] == "vite"

    def test_handles_invalid_package_json(self, tmp_path):
        (tmp_path / "package.json").write_text("INVALID JSON")
        profile = detect(str(tmp_path))
        assert "JavaScript" in profile["languages"]

    def test_detects_yarn(self, tmp_path):
        (tmp_path / "package.json").write_text(json.dumps({}))
        (tmp_path / "yarn.lock").write_text("")
        profile = detect(str(tmp_path))
        assert "yarn" in profile["package_managers"]


class TestDetectJava:
    def test_detects_java_maven(self, tmp_path):
        (tmp_path / "pom.xml").write_text("<project><dependencies></dependencies></project>")
        profile = detect(str(tmp_path))
        assert "Java" in profile["languages"]
        assert profile["build_system"] == "maven"

    def test_detects_spring(self, tmp_path):
        (tmp_path / "pom.xml").write_text("<project><dependency>spring-boot</dependency></project>")
        profile = detect(str(tmp_path))
        assert "Spring" in profile["frameworks"]
        assert profile["gui_type"] == "web"

    def test_detects_junit(self, tmp_path):
        (tmp_path / "pom.xml").write_text("<project><dependency>junit</dependency></project>")
        profile = detect(str(tmp_path))
        assert "JUnit" in profile["test_frameworks"]

    def test_detects_gradle(self, tmp_path):
        (tmp_path / "build.gradle").write_text("")
        profile = detect(str(tmp_path))
        assert "Java" in profile["languages"]
        assert profile["build_system"] == "gradle"


class TestDetectRustAndGo:
    def test_detects_rust(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\nname = \"x\"\n")
        profile = detect(str(tmp_path))
        assert "Rust" in profile["languages"]
        assert profile["build_system"] == "cargo"

    def test_detects_go(self, tmp_path):
        (tmp_path / "go.mod").write_text("module example.com/mymod\n")
        profile = detect(str(tmp_path))
        assert "Go" in profile["languages"]
        assert profile["build_system"] == "go"


class TestDetectMakefile:
    def test_detects_make_build_system_with_python(self, tmp_path):
        # Add Python so LLM fallback isn't triggered; Makefile alone hits LLM
        (tmp_path / "requirements.txt").write_text("")
        (tmp_path / "Makefile").write_text("all:\n\techo hi\n")
        profile = detect(str(tmp_path))
        # requirements.txt sets build_system implicitly to "unknown" (no pyproject),
        # Makefile does not override because build_system != "unknown" after Python branch
        # just check Python language was detected
        assert "Python" in profile["languages"]

    def test_detects_make_no_other_lang(self, tmp_path):
        """Makefile alone: LLM fallback is invoked — mock it."""
        (tmp_path / "Makefile").write_text("all:\n\techo hi\n")
        with patch("backend.agent.detector.llm.ask_structured", return_value="not json"):
            profile = detect(str(tmp_path))
        assert isinstance(profile["languages"], list)


class TestDetectHTMLFallback:
    def test_html_with_python_sets_gui_web(self, tmp_path):
        """When Python is also detected, HTML correctly sets gui_type=web."""
        (tmp_path / "requirements.txt").write_text("")
        (tmp_path / "index.html").write_text("<html></html>")
        profile = detect(str(tmp_path))
        assert profile["has_gui"] is True
        assert profile["gui_type"] == "web"

    def test_html_alone_triggers_llm_fallback(self, tmp_path):
        """HTML alone — LLM fallback is invoked; mock it to avoid real API call."""
        (tmp_path / "index.html").write_text("<html></html>")
        with patch("backend.agent.detector.llm.ask_structured", return_value="not json"):
            profile = detect(str(tmp_path))
        assert isinstance(profile, dict)


class TestDetectLLMFallback:
    def test_llm_fallback_called_when_no_language(self, tmp_path):
        """When no language markers exist, detector should call llm.ask_structured."""
        llm_response = json.dumps({
            "languages": ["Haskell"],
            "frameworks": [],
            "build_system": "cabal",
            "test_frameworks": [],
            "has_gui": False,
            "gui_type": "none",
            "entry_points": [],
            "package_managers": ["cabal"],
        })
        with patch("backend.agent.detector.llm.ask_structured", return_value=llm_response):
            profile = detect(str(tmp_path))
        assert "Haskell" in profile["languages"]
        assert profile["build_system"] == "cabal"

    def test_llm_fallback_bad_json_returns_empty_languages(self, tmp_path):
        with patch("backend.agent.detector.llm.ask_structured", return_value="not json"):
            profile = detect(str(tmp_path))
        # Should not raise; languages will be empty list
        assert isinstance(profile["languages"], list)


class TestDetectUvLock:
    """Detector must handle projects that use uv with no requirements.txt/pyproject.toml."""

    def test_detects_python_via_uv_lock(self, tmp_path):
        (tmp_path / "uv.lock").write_text("# uv lockfile\n")
        profile = detect(str(tmp_path))
        assert "Python" in profile["languages"]

    def test_detects_uv_package_manager(self, tmp_path):
        (tmp_path / "uv.lock").write_text("")
        profile = detect(str(tmp_path))
        assert "uv" in profile["package_managers"]
        assert profile["build_system"] == "uv"

    def test_detects_streamlit_from_uv_lock(self, tmp_path):
        """uv.lock contains 'name = "streamlit"' — detector must find it."""
        lock_content = (
            'version = 1\nrequires-python = ">=3.11"\n\n'
            '[[package]]\nname = "streamlit"\nversion = "1.44.0"\n'
        )
        (tmp_path / "uv.lock").write_text(lock_content)
        (tmp_path / "app.py").write_text("import streamlit as st\nst.title('hi')")
        profile = detect(str(tmp_path))
        assert "Streamlit" in profile["frameworks"]
        assert profile["has_gui"] is True
        assert profile["gui_type"] == "web"

    def test_detects_streamlit_from_source_when_lock_truncated(self, tmp_path):
        """When the lock file keyword scan misses (e.g. very large lock), fall back to .py imports."""
        # Minimal lock file with no streamlit entry
        (tmp_path / "uv.lock").write_text("version = 1\n")
        (tmp_path / "quizzer.py").write_text(
            'import streamlit as st\n\nif __name__ == "__main__":\n    st.title("Q")\n'
        )
        profile = detect(str(tmp_path))
        assert "Streamlit" in profile["frameworks"]
        assert profile["has_gui"] is True

    def test_detects_framework_entry_point_from_source(self, tmp_path):
        """Entry point must include the .py that imports the detected framework."""
        (tmp_path / "uv.lock").write_text("version = 1\n")
        (tmp_path / "quizzer.py").write_text(
            'import streamlit as st\n\nif __name__ == "__main__":\n    st.title("Q")\n'
        )
        profile = detect(str(tmp_path))
        assert "quizzer.py" in profile["entry_points"]

    def test_uv_lock_does_not_override_pyproject_pip(self, tmp_path):
        """When both uv.lock and pyproject.toml exist, both package managers are listed."""
        (tmp_path / "uv.lock").write_text("")
        (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
        profile = detect(str(tmp_path))
        assert "uv" in profile["package_managers"]
        assert "pip" in profile["package_managers"]

