"""Chat view injection: generate and inject a chat widget into the target application."""

from __future__ import annotations

import json
import re
from pathlib import Path

from backend.agent import llm
from backend.memory import store

_MAX_SNIPPET_CHARS = 500   # characters per source file included in the widget prompt
_MAX_SNIPPET_FILES = 5     # number of source files to sample for UI patterns

# Import guard: already present in the target file → do not inject twice
_STREAMLIT_WIDGET_GUARD = "# code-scribe: chat_widget"


def _collect_source_snippets(repo_dir: str, stack_profile: dict) -> str:
    """
    Return a compact block of source-file excerpts so the LLM can tailor
    the widget to match the app's existing conventions and styling.
    """
    root = Path(repo_dir)
    if not root.exists():
        return ""
    frameworks = stack_profile.get("frameworks", [])

    # Choose file extensions relevant to the detected stack
    if any(fw in frameworks for fw in ["React", "Next.js", "Vue", "Angular", "Svelte"]):
        exts = ["*.jsx", "*.tsx", "*.vue", "*.js", "*.ts"]
    elif any(fw in frameworks for fw in ["Flask", "Django", "FastAPI", "Streamlit"]):
        exts = ["*.html", "*.py"]
    else:
        exts = ["*.html", "*.js", "*.py"]

    snippets: list[str] = []
    seen: set[Path] = set()
    for ext in exts:
        for f in sorted(root.rglob(ext)):
            if f in seen:
                continue
            if any(skip in str(f) for skip in ("node_modules", "__pycache__", ".git")):
                continue
            seen.add(f)
            try:
                text = f.read_text(errors="replace")[:_MAX_SNIPPET_CHARS]
            except OSError:
                continue
            snippets.append(f"--- {f.relative_to(root)} ---\n{text}")
            if len(snippets) >= _MAX_SNIPPET_FILES:
                break
        if len(snippets) >= _MAX_SNIPPET_FILES:
            break

    return "\n\n".join(snippets)


# ---------------------------------------------------------------------------
# Streamlit-specific injection
# ---------------------------------------------------------------------------

# The canonical widget filename written into the target repo.
_STREAMLIT_WIDGET_FILENAME = "chat_widget_st.py"


def _is_gitignored(root: "Path", filename: str) -> bool:
    """
    Return True if *filename* appears to be covered by the repo's .gitignore.
    Uses a simple line-by-line substring match — sufficient for the common
    cases of ``.env`` and ``.env.scribe``.
    """
    gitignore = root / ".gitignore"
    if not gitignore.exists():
        return False
    try:
        for line in gitignore.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Match exact filename, wildcard *.env, or directory-relative paths
            if line == filename or line.endswith("/" + filename):
                return True
            if line.startswith("*") and filename.endswith(line.lstrip("*")):
                return True
    except OSError:
        pass
    return False

_STREAMLIT_WIDGET_TEMPLATE = (
"# code-scribe: chat_widget\n"
'"""\n'
"In-app chat widget for {app_name}.\n"
"Rendered as a Streamlit sidebar component.\n"
"Wired in by Code-Scribe — do not remove the guard comment above.\n"
"\n"
"Self-contained: calls the Groq LLM directly using the GROQ_API_KEY\n"
"environment variable. No Code-Scribe backend process is required at\n"
"runtime — only Streamlit and the groq package need to be installed.\n"
'"""\n'
"import os\n"
"import uuid\n"
"from pathlib import Path\n"
"import streamlit as st\n"
"\n"
"# ---------------------------------------------------------------------------\n"
"# .env loader — stdlib only, no python-dotenv required\n"
"# Tries both '.env' and '.env.scribe' so credentials injected into either\n"
"# file by Code-Scribe are picked up automatically.\n"
"# ---------------------------------------------------------------------------\n"
"\n"
"def _load_env_file(env_file):\n"
"    try:\n"
"        for raw in env_file.read_text(errors='replace').splitlines():\n"
"            line = raw.strip()\n"
"            if not line or line.startswith('#') or '=' not in line:\n"
"                continue\n"
"            k, _, v = line.partition('=')\n"
"            k = k.strip()\n"
"            v = v.strip()\n"
"            if v and v[0] in ('\"', \"'\") and v[0] == v[-1]:\n"
"                v = v[1:-1]\n"
"            if k and k not in os.environ:\n"
"                os.environ[k] = v\n"
"    except OSError:\n"
"        pass\n"
"\n"
"def _load_dotenv():\n"
"    directory = Path(__file__).parent\n"
"    for _ in range(6):\n"
"        for fname in ('.env', '.env.scribe'):\n"
"            env_file = directory / fname\n"
"            if env_file.is_file():\n"
"                _load_env_file(env_file)\n"
"        if (directory / '.env').is_file() or (directory / '.env.scribe').is_file():\n"
"            break\n"
"        parent = directory.parent\n"
"        if parent == directory:\n"
"            break\n"
"        directory = parent\n"
"\n"
"_load_dotenv()\n"
"\n"
"# ---------------------------------------------------------------------------\n"
"# Configuration\n"
"# ---------------------------------------------------------------------------\n"
"\n"
"_GROQ_API_KEY = os.environ.get('GROQ_API_KEY', '')\n"
"_GROQ_MODEL   = os.environ.get('GROQ_MODEL', 'llama-3.3-70b-versatile')\n"
"\n"
"_APP_NAME = '{app_name}'\n"
"_APP_CONTEXT = (\n"
"    'You are a helpful in-app assistant for {app_name}. '\n"
"    '{app_description} '\n"
"    'Guide users step-by-step through the app features. '\n"
"    'Keep answers short and concrete.'\n"
")\n"
"\n"
"# ---------------------------------------------------------------------------\n"
"# Source-context snapshot (loaded once per session)\n"
"# ---------------------------------------------------------------------------\n"
"_MAX_FILES  = 5\n"
"_MAX_CHARS  = 1500\n"
"_SKIP_DIRS  = {{'node_modules', '__pycache__', '.git', 'tests', 'test'}}\n"
"_WIDGET_DIR = Path(__file__).parent\n"
"\n"
"\n"
"def _load_source_context():\n"
"    snippets = []\n"
"    seen = set()\n"
"    for ext in ('*.py', '*.js', '*.ts', '*.jsx', '*.tsx'):\n"
"        for f in sorted(_WIDGET_DIR.rglob(ext)):\n"
"            if f in seen:\n"
"                continue\n"
"            if any(skip in f.parts for skip in _SKIP_DIRS):\n"
"                continue\n"
"            if f.name == Path(__file__).name:\n"
"                continue\n"
"            seen.add(f)\n"
"            try:\n"
"                text = f.read_text(errors='replace')[:_MAX_CHARS]\n"
"            except OSError:\n"
"                continue\n"
"            rel = f.relative_to(_WIDGET_DIR)\n"
"            snippets.append('=== ' + str(rel) + ' ===\\n' + text)\n"
"            if len(snippets) >= _MAX_FILES:\n"
"                break\n"
"        if len(snippets) >= _MAX_FILES:\n"
"            break\n"
"    return '\\n\\n'.join(snippets)\n"
"\n"
"\n"
"# ---------------------------------------------------------------------------\n"
"# LLM call\n"
"# ---------------------------------------------------------------------------\n"
"\n"
"def _ask_groq(messages):\n"
"    if not _GROQ_API_KEY:\n"
"        return (\n"
"            'GROQ_API_KEY is not set. '\n"
"            'Add it to your environment or a .env file and restart Streamlit.'\n"
"        )\n"
"    try:\n"
"        from groq import Groq\n"
"        client = Groq(api_key=_GROQ_API_KEY)\n"
"        response = client.chat.completions.create(\n"
"            model=_GROQ_MODEL,\n"
"            messages=messages,\n"
"            max_tokens=1024,\n"
"        )\n"
"        return response.choices[0].message.content or 'No response.'\n"
"    except Exception as exc:\n"
"        return 'LLM error: ' + str(exc)\n"
"\n"
"\n"
"# ---------------------------------------------------------------------------\n"
"# Widget renderer\n"
"# ---------------------------------------------------------------------------\n"
"\n"
"def render_chat_widget():\n"
"    with st.sidebar:\n"
"        st.markdown('### \U0001f4ac App Assistant')\n"
"        st.caption('Ask me anything about using this app.')\n"
"        if 'cs_chat_history' not in st.session_state:\n"
"            st.session_state['cs_chat_history'] = []\n"
"        if 'cs_session_id' not in st.session_state:\n"
"            st.session_state['cs_session_id'] = str(uuid.uuid4())\n"
"        if 'cs_source_context' not in st.session_state:\n"
"            st.session_state['cs_source_context'] = _load_source_context()\n"
"        if 'cs_pending_input' not in st.session_state:\n"
"            st.session_state['cs_pending_input'] = None\n"
"        if st.session_state['cs_pending_input'] is not None:\n"
"            user_input = st.session_state['cs_pending_input']\n"
"            st.session_state['cs_pending_input'] = None\n"
"            source_ctx = st.session_state['cs_source_context']\n"
"            system_content = _APP_CONTEXT\n"
"            if source_ctx:\n"
"                system_content += ('\\n\\nKey source files for reference:\\n\\n' + source_ctx)\n"
"            system_msg = {{'role': 'system', 'content': system_content}}\n"
"            prior = [{{'role': m['role'], 'content': m['content']}}\n"
"                     for m in st.session_state['cs_chat_history']]\n"
"            msgs = [system_msg] + prior + [{{'role': 'user', 'content': user_input}}]\n"
"            answer = _ask_groq(msgs)\n"
"            st.session_state['cs_chat_history'].append(\n"
"                {{'role': 'assistant', 'content': answer}}\n"
"            )\n"
"        for msg in st.session_state['cs_chat_history']:\n"
"            with st.chat_message(msg['role']):\n"
"                st.markdown(msg['content'])\n"
"        user_input = st.chat_input('How can I help?', key='cs_chat_input')\n"
"        if user_input:\n"
"            st.session_state['cs_chat_history'].append(\n"
"                {{'role': 'user', 'content': user_input}}\n"
"            )\n"
"            st.session_state['cs_pending_input'] = user_input\n"
"            st.rerun()\n"
)

_STREAMLIT_INJECT_CALL = "\n\n# code-scribe: chat_widget — wired by Code-Scribe\nfrom chat_widget_st import render_chat_widget\nrender_chat_widget()\n"


def _generate_streamlit_widget(
    repo_dir: str,
    stack_profile: dict,
    ui_map: dict,
) -> str:
    """
    Return the Python source code of the Streamlit chat widget module.
    Uses the LLM to produce an app-specific description, then fills the template.

    The widget is fully self-contained: it calls Groq directly at runtime using
    the GROQ_API_KEY environment variable.  No Code-Scribe process is needed
    once the widget has been injected.
    """
    source_context = _collect_source_snippets(repo_dir, stack_profile)
    routes = "\n".join(
        f"  {r['path']}: {r.get('description', '')}"
        for r in ui_map.get("routes", [])
    )

    repo_name = Path(repo_dir).name

    prompt = (
        f"You are analysing a Streamlit application called '{repo_name}'.\n\n"
        "Source code snippets:\n"
        f"{source_context}\n\n"
        f"Known routes / pages: {routes or 'none'}\n\n"
        "Write TWO things (return as JSON with keys 'app_name' and 'app_description'):\n"
        "1. app_name: the proper display name of the app (e.g. 'Quizer').\n"
        "2. app_description: 3–5 sentences describing what the app does and how to use it, "
        "suitable as in-app assistant context. Mention key user actions step by step."
    )
    raw = llm.ask(prompt, max_tokens=512)
    try:
        cleaned = re.sub(r"```(?:json)?", "", raw).strip().strip("`")
        meta = json.loads(cleaned)
        app_name = meta.get("app_name", repo_name)
        app_description = meta.get("app_description", "This app helps you complete tasks interactively.")
    except (json.JSONDecodeError, ValueError):
        app_name = repo_name
        app_description = "This app helps you complete tasks interactively."

    return _STREAMLIT_WIDGET_TEMPLATE.format(
        app_name=_sanitize_for_template(app_name),
        app_description=_sanitize_for_template(app_description),
    )


def _sanitize_for_template(text: str) -> str:
    """
    Ensure *text* is safe to embed inside a triple-quoted Python string.

    Replaces Unicode typographic punctuation with ASCII equivalents and strips
    any triple-quote sequences that would prematurely close the string literal.
    The LLM sometimes returns curly quotes, non-breaking hyphens, or em-dashes
    that are valid Unicode but look identical to regular ASCII in the output,
    yet cause SyntaxErrors when the generated file is later parsed by Python.
    """
    replacements = {
        "\u2018": "'",   # left single quotation mark
        "\u2019": "'",   # right single quotation mark / apostrophe
        "\u201c": '"',   # left double quotation mark
        "\u201d": '"',   # right double quotation mark
        "\u2011": "-",   # non-breaking hyphen
        "\u2013": "-",   # en dash
        "\u2014": "-",   # em dash
        "\u2026": "...", # horizontal ellipsis
        "\u00a0": " ",   # non-breaking space
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    # Triple-quotes inside a triple-quoted string would end it prematurely.
    text = text.replace('"""', "'''")
    return text


def _inject_streamlit(
    repo_dir: str,
    stack_profile: dict,
    ui_map: dict,
    job_id: int,
) -> list[str]:
    """
    Full Streamlit injection path.

    1. Write chat_widget_st.py to the repo root.
    2. Copy GROQ_API_KEY / GROQ_MODEL from Code-Scribe's .env into a .env
       file at the repo root so the widget can find them when the user runs
       `streamlit run` in a fresh terminal without exporting variables.
    3. Wire the render_chat_widget() call into the entry-point file,
       guarded by the import guard so it is idempotent.

    Returns list of modified/created files (relative to repo_dir).
    """
    import os as _os
    root = Path(repo_dir)
    modified: list[str] = []

    # Step 1 — write the self-contained widget module
    widget_code = _generate_streamlit_widget(repo_dir, stack_profile, ui_map)
    widget_path = root / _STREAMLIT_WIDGET_FILENAME
    widget_path.write_text(widget_code)
    modified.append(_STREAMLIT_WIDGET_FILENAME)

    # Step 1b — copy Groq credentials so the widget can load them without
    # requiring a shell export.
    # Security: if '.env' is not listed in the repo's .gitignore we write to
    # '.env.scribe' instead (and add it to .gitignore) to avoid accidental
    # credential leaks when the developer runs `git push`.
    groq_keys = {
        k: _os.environ[k]
        for k in ("GROQ_API_KEY", "GROQ_MODEL")
        if k in _os.environ
    }
    if groq_keys:
        if _is_gitignored(root, ".env"):
            target_env_name = ".env"
        else:
            target_env_name = ".env.scribe"
            # Ensure the safe filename is git-ignored
            gitignore_path = root / ".gitignore"
            try:
                existing_gi = gitignore_path.read_text(errors="replace") if gitignore_path.exists() else ""
                if ".env.scribe" not in existing_gi:
                    with gitignore_path.open("a") as gi:
                        if existing_gi and not existing_gi.endswith("\n"):
                            gi.write("\n")
                        gi.write(".env.scribe\n")
                    modified.append(".gitignore")
            except OSError:
                pass

        repo_env_path = root / target_env_name
        # Read existing content so we don't overwrite values the repo already has
        existing_lines: list[str] = []
        existing_keys: set[str] = set()
        if repo_env_path.exists():
            try:
                for raw in repo_env_path.read_text(errors="replace").splitlines():
                    existing_lines.append(raw)
                    if "=" in raw and not raw.strip().startswith("#"):
                        existing_keys.add(raw.split("=", 1)[0].strip())
            except OSError:
                pass
        new_lines = [f"{k}={v}" for k, v in groq_keys.items() if k not in existing_keys]
        if new_lines:
            with repo_env_path.open("a") as fh:
                if existing_lines and existing_lines[-1].strip():
                    fh.write("\n")  # ensure we start on a new line
                fh.write("# Added by Code-Scribe — required by the injected chat widget\n")
                fh.write("\n".join(new_lines) + "\n")
            modified.append(target_env_name)

    # Step 2 — find the entry-point and wire the render call into it.
    # If the file wraps all its logic in `if __name__ == "__main__":` (common
    # in scripts that double as Streamlit apps), appending at the end of the
    # file places the call *outside* that guard.  Streamlit never executes the
    # __main__ block, so both the app logic and the widget would be skipped.
    # In that case we insert the wiring call *inside* the guard block (indented)
    # so that `streamlit run` (top-level execution) sees it, while
    # `python <file>` also works because it enters __main__.
    entry = _find_streamlit_entry(root, stack_profile)
    if entry:
        entry_path = root / entry
        try:
            existing = entry_path.read_text(errors="replace")
        except OSError:
            existing = ""
        if _STREAMLIT_WIDGET_GUARD not in existing:
            if 'if __name__ == "__main__":' in existing:
                # Insert before the __main__ guard so the widget renders for
                # both `streamlit run` (skips __main__) and `python` (enters it).
                inject_top_level = (
                    "\n# code-scribe: chat_widget — wired by Code-Scribe\n"
                    "from chat_widget_st import render_chat_widget\n"
                    "render_chat_widget()\n"
                )
                insertion_point = existing.index('if __name__ == "__main__":')
                updated = existing[:insertion_point] + inject_top_level + existing[insertion_point:]
            else:
                updated = existing + _STREAMLIT_INJECT_CALL
            entry_path.write_text(updated)
            modified.append(entry)

    return modified


def _find_streamlit_entry(root: Path, stack_profile: dict) -> str | None:
    """
    Return relative path to the Streamlit main entry-point (.py file).

    Priority order:
    1. Common well-known entry-point names that exist on disk.
    2. Root-level .py that has ``if __name__ == "__main__":`` and imports streamlit
       — this is the file you pass to ``streamlit run``.
    3. First root-level .py that actively calls ``st.<something>``.
    4. First root-level .py that merely imports streamlit.

    The Code-Scribe-injected widget file (_STREAMLIT_WIDGET_FILENAME) is always
    excluded — it is a helper module that imports streamlit but is not an entry point.
    """
    # Priority 1 — canonical names (widget filename is never in this list)
    for name in ("app.py", "main.py", "streamlit_app.py", "run.py"):
        if (root / name).exists():
            return name

    # Priority 2 — file with __main__ guard + streamlit import
    for py_file in sorted(root.glob("*.py")):
        if py_file.name == _STREAMLIT_WIDGET_FILENAME:
            continue
        try:
            src = py_file.read_text(errors="replace")
        except OSError:
            continue
        src_lower = src.lower()
        if '__name__ == "__main__"' in src and "import streamlit" in src_lower:
            return py_file.name

    # Priority 3 — any root .py that imports AND calls st.*
    for py_file in sorted(root.glob("*.py")):
        if py_file.name == _STREAMLIT_WIDGET_FILENAME:
            continue
        try:
            src = py_file.read_text(errors="replace").lower()
        except OSError:
            continue
        if "import streamlit" in src and "st." in src:
            return py_file.name

    # Priority 4 — any root .py that merely imports streamlit
    for py_file in sorted(root.glob("*.py")):
        if py_file.name == _STREAMLIT_WIDGET_FILENAME:
            continue
        try:
            if "import streamlit" in py_file.read_text(errors="replace").lower():
                return py_file.name
        except OSError:
            continue

    return None


# ---------------------------------------------------------------------------
# Chat widget code generation (non-Streamlit stacks)
# ---------------------------------------------------------------------------

def generate_chat_widget_code(stack_profile: dict, ui_map: dict, repo_dir: str = "") -> dict[str, str]:
    """
    Ask the LLM to produce the minimal chat widget component for the detected stack.
    Returns {filename: code_content} for each file to create.
    Includes source snippets from the repo so the LLM can match existing UI patterns.

    Streamlit stacks are NOT handled here — use _inject_streamlit() instead.
    """
    frameworks = stack_profile.get("frameworks", [])
    routes = "\n".join(
        f"  {r['path']}: {r.get('description', '')}"
        for r in ui_map.get("routes", [])
    )

    stack_desc = ", ".join(frameworks) if frameworks else "unknown"
    source_context = _collect_source_snippets(repo_dir, stack_profile) if repo_dir else ""
    source_block = f"\nExisting UI source snippets for style reference:\n{source_context}\n" if source_context else ""

    if any(fw in frameworks for fw in ["React", "Next.js"]):
        prompt = (
            f"The application uses {stack_desc}.\n\n"
            "Generate a self-contained React functional component called ChatWidget. "
            "It should display a floating chat button (bottom-right corner). When clicked, "
            "it opens a chat panel with a message input and a scrollable history list. "
            "On submit, it POSTs {message, session_id} to /api/chat and displays the response.\n\n"
            f"Known routes for context:\n{routes}\n"
            f"{source_block}\n"
            "Return a single JSON object with one key 'ChatWidget.jsx' containing the full component code."
        )
    elif "Vue" in frameworks:
        prompt = (
            f"The application uses Vue.js.\n\n"
            "Generate a self-contained Vue 3 component called ChatWidget.vue. "
            "It should display a floating chat button. When clicked, opens a chat panel. "
            "On submit, POST {message, session_id} to /api/chat and show the response.\n\n"
            f"Known routes:\n{routes}\n"
            f"{source_block}\n"
            "Return a single JSON object with key 'ChatWidget.vue' containing the component code."
        )
    elif any(fw in frameworks for fw in ["Flask", "Django", "FastAPI"]):
        prompt = (
            f"The application uses {stack_desc} (Python web framework).\n\n"
            "Generate a self-contained HTML/CSS/JS chat widget to inject into a base template. "
            "The widget is a floating button (bottom-right) that opens a chat panel. "
            "On submit, POST JSON {message, session_id} to /api/chat. Display the response.\n\n"
            f"Known routes:\n{routes}\n"
            f"{source_block}\n"
            "Return a single JSON object with key 'chat_widget.html' containing the snippet."
        )
    else:
        prompt = (
            f"The application stack is: {stack_desc}.\n\n"
            "Generate a vanilla HTML/CSS/JavaScript chat widget snippet. "
            "Floating button bottom-right, opens panel, submits POST to /api/chat. "
            f"{source_block}\n"
            "Return a single JSON object with key 'chat_widget.html'."
        )

    raw = llm.ask(prompt, max_tokens=3000)

    # Parse the JSON response
    try:
        cleaned = re.sub(r"```(?:json)?", "", raw).strip().strip("`")
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        # Fallback: treat entire response as the widget code
        return {"chat_widget.html": raw}


def inject_into_app(
    repo_dir: str,
    widget_files: dict[str, str],
    stack_profile: dict,
) -> list[str]:
    """
    Write widget files to the repo and wire the widget into the shell/layout file.
    Returns list of modified/created file paths.
    """
    root = Path(repo_dir)
    modified: list[str] = []
    frameworks = stack_profile.get("frameworks", [])

    # Write each widget file
    for filename, code in widget_files.items():
        # Determine where to put it
        if any(fw in frameworks for fw in ["React", "Next.js", "Vue"]):
            target_dir = root / "src" / "components"
        else:
            # Flask/Django/FastAPI and generic: put alongside main Python files
            target_dir = root / "templates"
            # Only create templates/ for genuine web-template stacks
            if not any(fw in frameworks for fw in ["Flask", "Django", "FastAPI"]):
                target_dir = root
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename
        target.write_text(code)
        modified.append(str(target.relative_to(root)))

    # Find the shell/layout file
    shell_file = _find_shell_file(root, stack_profile)
    if shell_file and widget_files:
        widget_name = next(iter(widget_files.keys()))
        _wire_widget_into_shell(root, shell_file, widget_name, stack_profile)
        modified.append(shell_file)

    return modified


def _find_shell_file(root: Path, stack_profile: dict) -> str | None:
    """Return relative path to the main layout/shell file."""
    frameworks = stack_profile.get("frameworks", [])
    candidates: list[Path] = []

    if any(fw in frameworks for fw in ["React", "Next.js"]):
        candidates = [root / "src" / "App.jsx", root / "src" / "App.tsx", root / "src" / "App.js"]
    elif "Vue" in frameworks:
        candidates = [root / "src" / "App.vue"]
    elif any(fw in frameworks for fw in ["Flask", "Django", "FastAPI"]):
        candidates = list(root.rglob("base.html"))[:1] + list(root.rglob("layout.html"))[:1]
    elif "Streamlit" in frameworks:
        # For Streamlit the "shell" is the Python entry point
        entry = _find_streamlit_entry(root, stack_profile)
        return entry  # may be None
    else:
        candidates = [root / "index.html"]

    for c in candidates:
        if c.exists():
            return str(c.relative_to(root))
    return None


def _wire_widget_into_shell(
    root: Path,
    shell_rel: str,
    widget_filename: str,
    stack_profile: dict,
) -> None:
    """Ask LLM to produce a patch that wires the widget into the shell file, then apply it."""
    shell_path = root / shell_rel
    try:
        shell_content = shell_path.read_text(errors="replace")
    except OSError:
        return

    frameworks = ", ".join(stack_profile.get("frameworks", []))
    prompt = (
        f"The following is the content of '{shell_rel}' from a {frameworks} application:\n\n"
        f"```\n{shell_content[:3000]}\n```\n\n"
        f"I need to add a chat widget defined in '{widget_filename}' to this file. "
        "Produce the COMPLETE updated file content with the widget imported/included "
        "at the end of the body (or as a component import + usage for React/Vue). "
        "Return only the complete updated file content, no explanations."
    )
    updated = llm.ask(prompt, max_tokens=4096)

    # Strip markdown fences if present
    updated = re.sub(r"^```[^\n]*\n", "", updated).rstrip("`").strip()

    if updated and len(updated) > 50:
        shell_path.write_text(updated)


def add_chat_api_endpoint(repo_dir: str, stack_profile: dict) -> str | None:
    """
    Add a /chat endpoint to the target app if it uses a supported Python or Node framework.
    Streamlit apps are skipped — their widget talks directly to Code-Scribe's /api/chat.
    Returns the relative path of the created/modified file, or None.
    """
    root = Path(repo_dir)
    frameworks = stack_profile.get("frameworks", [])

    # Streamlit is self-contained: the widget calls Code-Scribe directly
    if "Streamlit" in frameworks:
        return None

    if "FastAPI" in frameworks:
        code = (
            '"""Chat API endpoint — Code-Scribe injected."""\n'
            "from fastapi import APIRouter\n"
            "from pydantic import BaseModel\n\n"
            "router = APIRouter()\n\n"
            "class ChatRequest(BaseModel):\n"
            "    message: str\n"
            "    session_id: str = ''\n\n"
            "@router.post('/api/chat')\n"
            "def chat(req: ChatRequest):\n"
            "    import httpx\n"
            "    resp = httpx.post('http://localhost:8000/api/chat',\n"
            "                      json={'message': req.message, 'session_id': req.session_id})\n"
            "    return resp.json()\n"
        )
        target = root / "chat_endpoint.py"
        target.write_text(code)
        return "chat_endpoint.py"

    elif "Flask" in frameworks:
        code = (
            '"""Chat blueprint — Code-Scribe injected."""\n'
            "from flask import Blueprint, request, jsonify\n"
            "import httpx\n\n"
            "chat_bp = Blueprint('chat', __name__)\n\n"
            "@chat_bp.route('/api/chat', methods=['POST'])\n"
            "def chat():\n"
            "    data = request.get_json()\n"
            "    resp = httpx.post('http://localhost:8000/api/chat', json=data)\n"
            "    return jsonify(resp.json())\n"
        )
        target = root / "chat_blueprint.py"
        target.write_text(code)
        return "chat_blueprint.py"

    elif "Django" in frameworks:
        code = (
            '"""Chat view — Code-Scribe injected."""\n'
            "from django.http import JsonResponse\n"
            "from django.views.decorators.csrf import csrf_exempt\n"
            "from django.views.decorators.http import require_POST\n"
            "import json\n"
            "import httpx\n\n"
            "@csrf_exempt\n"
            "@require_POST\n"
            "def chat(request):\n"
            "    data = json.loads(request.body)\n"
            "    resp = httpx.post('http://localhost:8000/api/chat', json=data)\n"
            "    return JsonResponse(resp.json())\n"
        )
        target = root / "chat_view.py"
        target.write_text(code)
        return "chat_view.py"

    elif any(fw in frameworks for fw in ["Express", "Next.js"]):
        if "Next.js" in frameworks:
            # Next.js API route under pages/api/
            code = (
                "// Chat API route — Code-Scribe injected\n"
                "export default async function handler(req, res) {\n"
                "  if (req.method !== 'POST') return res.status(405).end();\n"
                "  const { message, session_id } = req.body;\n"
                "  const upstream = await fetch('http://localhost:8000/api/chat', {\n"
                "    method: 'POST',\n"
                "    headers: { 'Content-Type': 'application/json' },\n"
                "    body: JSON.stringify({ message, session_id }),\n"
                "  });\n"
                "  const data = await upstream.json();\n"
                "  res.status(200).json(data);\n"
                "}\n"
            )
            api_dir = root / "pages" / "api"
            api_dir.mkdir(parents=True, exist_ok=True)
            target = api_dir / "chat.js"
            target.write_text(code)
            return "pages/api/chat.js"
        else:
            # Express route file
            code = (
                "// Chat route — Code-Scribe injected\n"
                "const express = require('express');\n"
                "const router = express.Router();\n\n"
                "router.post('/api/chat', async (req, res) => {\n"
                "  const { message, session_id } = req.body;\n"
                "  try {\n"
                "    const resp = await fetch('http://localhost:8000/api/chat', {\n"
                "      method: 'POST',\n"
                "      headers: { 'Content-Type': 'application/json' },\n"
                "      body: JSON.stringify({ message, session_id }),\n"
                "    });\n"
                "    res.json(await resp.json());\n"
                "  } catch (err) {\n"
                "    res.status(500).json({ error: err.message });\n"
                "  }\n"
                "});\n\n"
                "module.exports = router;\n"
            )
            target = root / "chat_route.js"
            target.write_text(code)
            return "chat_route.js"

    return None


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------

def inject(
    repo_dir: str,
    stack_profile: dict,
    ui_map: dict,
    job_id: int,
) -> None:
    """
    Full chat injection pipeline. Skipped if `has_gui` is False.
    Streamlit apps get a native sidebar widget; all other stacks get an LLM-generated
    HTML/JS/component widget injected into the layout shell.
    Persists `chat_view_injected` checkpoint.
    """
    if not stack_profile.get("has_gui", False):
        store.set_checkpoint(job_id, "chat_view_injected", "not_applicable", {
            "reason": "No GUI detected"
        })
        return

    frameworks = stack_profile.get("frameworks", [])

    if "Streamlit" in frameworks:
        # Native Streamlit sidebar path — no HTML template involved
        modified = _inject_streamlit(repo_dir, stack_profile, ui_map, job_id)
    else:
        widget_files = generate_chat_widget_code(stack_profile, ui_map, repo_dir)
        modified = inject_into_app(repo_dir, widget_files, stack_profile)
        api_file = add_chat_api_endpoint(repo_dir, stack_profile)
        if api_file:
            modified.append(api_file)

    store.set_checkpoint(job_id, "chat_view_injected", "done", {
        "modified_files": modified,
    })
