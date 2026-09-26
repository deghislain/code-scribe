"""Chat view injection: generate and inject a chat widget into the target application."""

from __future__ import annotations

import json
import re
from pathlib import Path

from backend.agent import llm
from backend.memory import store


# ---------------------------------------------------------------------------
# Chat widget code generation
# ---------------------------------------------------------------------------

def generate_chat_widget_code(stack_profile: dict, ui_map: dict) -> dict[str, str]:
    """
    Ask the LLM to produce the minimal chat widget component for the detected stack.
    Returns {filename: code_content} for each file to create.
    """
    frameworks = stack_profile.get("frameworks", [])
    routes = "\n".join(
        f"  {r['path']}: {r.get('description', '')}"
        for r in ui_map.get("routes", [])
    )

    stack_desc = ", ".join(frameworks) if frameworks else "unknown"

    if any(fw in frameworks for fw in ["React", "Next.js"]):
        prompt = (
            f"The application uses {stack_desc}.\n\n"
            "Generate a self-contained React functional component called ChatWidget. "
            "It should display a floating chat button (bottom-right corner). When clicked, "
            "it opens a chat panel with a message input and a scrollable history list. "
            "On submit, it POSTs {message, session_id} to /api/chat and displays the response.\n\n"
            f"Known routes for context:\n{routes}\n\n"
            "Return a single JSON object with one key 'ChatWidget.jsx' containing the full component code."
        )
    elif "Vue" in frameworks:
        prompt = (
            f"The application uses Vue.js.\n\n"
            "Generate a self-contained Vue 3 component called ChatWidget.vue. "
            "It should display a floating chat button. When clicked, opens a chat panel. "
            "On submit, POST {message, session_id} to /api/chat and show the response.\n\n"
            f"Known routes:\n{routes}\n\n"
            "Return a single JSON object with key 'ChatWidget.vue' containing the component code."
        )
    elif any(fw in frameworks for fw in ["Flask", "Django", "FastAPI"]):
        prompt = (
            f"The application uses {stack_desc} (Python web framework).\n\n"
            "Generate a self-contained HTML/CSS/JS chat widget to inject into a base template. "
            "The widget is a floating button (bottom-right) that opens a chat panel. "
            "On submit, POST JSON {message, session_id} to /api/chat. Display the response.\n\n"
            f"Known routes:\n{routes}\n\n"
            "Return a single JSON object with key 'chat_widget.html' containing the snippet."
        )
    else:
        prompt = (
            f"The application stack is: {stack_desc}.\n\n"
            "Generate a vanilla HTML/CSS/JavaScript chat widget snippet. "
            "Floating button bottom-right, opens panel, submits POST to /api/chat. "
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
        if any(fw in frameworks for fw in ["React", "Next.js"]):
            target_dir = root / "src" / "components"
        elif "Vue" in frameworks:
            target_dir = root / "src" / "components"
        else:
            target_dir = root / "templates"
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
    elif any(fw in frameworks for fw in ["Flask", "Django"]):
        candidates = list(root.rglob("base.html"))[:1] + list(root.rglob("layout.html"))[:1]
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
    Returns the relative path of the created/modified file, or None.
    """
    root = Path(repo_dir)
    frameworks = stack_profile.get("frameworks", [])

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
    Persists `chat_view_injected` checkpoint.
    """
    if not stack_profile.get("has_gui", False):
        store.set_checkpoint(job_id, "chat_view_injected", "not_applicable", {
            "reason": "No GUI detected"
        })
        return

    widget_files = generate_chat_widget_code(stack_profile, ui_map)
    modified = inject_into_app(repo_dir, widget_files, stack_profile)
    api_file = add_chat_api_endpoint(repo_dir, stack_profile)
    if api_file:
        modified.append(api_file)

    store.set_checkpoint(job_id, "chat_view_injected", "done", {
        "modified_files": modified,
        "widget_files": list(widget_files.keys()),
    })
