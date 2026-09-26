"""GUI inspection: extract routes and UI structure from static source or running app."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TypedDict

import httpx

from backend.agent import llm
from backend.memory import store


class RouteInfo(TypedDict):
    path: str
    title: str
    description: str


class UIMap(TypedDict):
    routes: list[RouteInfo]
    base_url: str


# ---------------------------------------------------------------------------
# Static route extraction helpers
# ---------------------------------------------------------------------------

def _extract_flask_routes(repo_dir: str) -> list[str]:
    """Find @app.route(...) and @blueprint.route(...) patterns in Python files."""
    routes: list[str] = []
    for py_file in Path(repo_dir).rglob("*.py"):
        try:
            text = py_file.read_text(errors="replace")
        except OSError:
            continue
        for m in re.finditer(r'@\w+\.route\(["\']([^"\']+)["\']', text):
            routes.append(m.group(1))
    return routes


def _extract_fastapi_routes(repo_dir: str) -> list[str]:
    """Find @router.get/post/... and @app.get/post/... patterns."""
    routes: list[str] = []
    pattern = re.compile(r'@(?:router|app)\.\w+\(["\']([^"\']+)["\']')
    for py_file in Path(repo_dir).rglob("*.py"):
        try:
            text = py_file.read_text(errors="replace")
        except OSError:
            continue
        for m in pattern.finditer(text):
            routes.append(m.group(1))
    return routes


def _extract_django_routes(repo_dir: str) -> list[str]:
    """Parse path(...) / url(...) calls from urls.py files."""
    routes: list[str] = []
    pattern = re.compile(r'(?:path|url|re_path)\(["\']([^"\']+)["\']')
    for urls_file in Path(repo_dir).rglob("urls.py"):
        try:
            text = urls_file.read_text(errors="replace")
        except OSError:
            continue
        for m in pattern.finditer(text):
            routes.append("/" + m.group(1).lstrip("/"))
    return routes


def _extract_react_router_routes(repo_dir: str) -> list[str]:
    """Extract <Route path=...> and path: '...' entries from JS/JSX/TS/TSX files."""
    routes: list[str] = []
    pattern = re.compile(r'(?:path\s*[=:]\s*["\']([^"\']+)["\'])')
    for ext in ["*.jsx", "*.tsx", "*.js", "*.ts"]:
        for js_file in Path(repo_dir).rglob(ext):
            if "node_modules" in str(js_file):
                continue
            try:
                text = js_file.read_text(errors="replace")
            except OSError:
                continue
            for m in pattern.finditer(text):
                val = m.group(1)
                if val.startswith("/"):
                    routes.append(val)
    return routes


def extract_routes_static(repo_dir: str, stack_profile: dict) -> UIMap:
    """
    Statically extract routes from source code based on detected stack.
    Returns a UIMap with routes populated but descriptions empty (filled by LLM later).
    """
    frameworks = stack_profile.get("frameworks", [])
    routes: list[str] = []

    if "Flask" in frameworks:
        routes = _extract_flask_routes(repo_dir)
    elif "FastAPI" in frameworks:
        routes = _extract_fastapi_routes(repo_dir)
    elif "Django" in frameworks:
        routes = _extract_django_routes(repo_dir)
    elif any(fw in frameworks for fw in ["React", "Vue", "Angular", "Svelte", "Next.js"]):
        routes = _extract_react_router_routes(repo_dir)

    # Deduplicate
    seen: set[str] = set()
    route_infos: list[RouteInfo] = []
    for r in routes:
        if r not in seen:
            seen.add(r)
            route_infos.append(RouteInfo(path=r, title="", description=""))

    return UIMap(routes=route_infos, base_url="")


def extract_routes_runtime(base_url: str) -> UIMap:
    """
    Fetch the running app and crawl links up to 2 levels deep.
    Returns a UIMap with paths found.
    """
    visited: set[str] = set()
    route_infos: list[RouteInfo] = []
    origin = base_url.rstrip("/")

    def _crawl(url: str, depth: int) -> None:
        if depth > 2 or url in visited:
            return
        visited.add(url)
        try:
            resp = httpx.get(url, timeout=5, follow_redirects=True)
        except Exception:
            return
        path = url[len(origin):] or "/"
        title = ""
        m = re.search(r"<title[^>]*>([^<]+)</title>", resp.text, re.IGNORECASE)
        if m:
            title = m.group(1).strip()
        route_infos.append(RouteInfo(path=path, title=title, description=""))

        if depth < 2:
            for href in re.findall(r'href=["\']([^"\'#?]+)["\']', resp.text):
                if href.startswith("/") and not href.startswith("//"):
                    _crawl(origin + href, depth + 1)

    _crawl(base_url, 0)
    return UIMap(routes=route_infos, base_url=base_url)


def describe_ui_with_llm(
    repo_dir: str,
    stack_profile: dict,
    ui_map: UIMap,
) -> UIMap:
    """
    Ask the LLM to describe the purpose of each route/view in plain English.
    Returns an enriched UIMap with descriptions filled in.
    """
    if not ui_map["routes"]:
        return ui_map

    route_list = "\n".join(
        f"  {r['path']}" + (f" ({r['title']})" if r["title"] else "")
        for r in ui_map["routes"]
    )

    # Gather a small sample of source files for context
    repo_root = Path(repo_dir)
    snippets: list[str] = []
    for ext in ["*.py", "*.jsx", "*.tsx", "*.js", "*.html"]:
        for f in list(repo_root.rglob(ext))[:3]:
            if "node_modules" in str(f) or "__pycache__" in str(f):
                continue
            try:
                snippets.append(f"--- {f.relative_to(repo_root)} ---\n{f.read_text(errors='replace')[:500]}")
            except OSError:
                pass
    context = "\n\n".join(snippets[:5])

    prompt = (
        f"You are analyzing the UI of a {' / '.join(stack_profile.get('frameworks', ['unknown']))} application.\n\n"
        f"Routes / pages found:\n{route_list}\n\n"
        f"Source code snippets:\n{context}\n\n"
        "For each route, write a one-sentence plain-English description of what the user can do there. "
        "Respond as a JSON array: [{\"path\": \"...\", \"title\": \"...\", \"description\": \"...\"}]"
    )
    raw = llm.ask(prompt, max_tokens=2048)

    # Parse response
    try:
        import json
        # Strip markdown code fences if present
        cleaned = re.sub(r"```(?:json)?", "", raw).strip().strip("`")
        parsed = json.loads(cleaned)
        path_to_info = {item["path"]: item for item in parsed if isinstance(item, dict)}
        enriched: list[RouteInfo] = []
        for r in ui_map["routes"]:
            info = path_to_info.get(r["path"], {})
            enriched.append(RouteInfo(
                path=r["path"],
                title=info.get("title", r["title"]),
                description=info.get("description", ""),
            ))
        return UIMap(routes=enriched, base_url=ui_map["base_url"])
    except Exception:
        return ui_map


def inspect(
    repo_dir: str,
    stack_profile: dict,
    job_id: int,
    base_url: str = "",
) -> UIMap:
    """
    Full GUI inspection pipeline. Returns UIMap and persists checkpoint.
    If `has_gui` is False, returns empty UIMap immediately.
    """
    if not stack_profile.get("has_gui", False):
        store.set_checkpoint(job_id, "gui_inspected", "not_applicable", {"reason": "No GUI detected"})
        return UIMap(routes=[], base_url="")

    # Static analysis first
    ui_map = extract_routes_static(repo_dir, stack_profile)

    # Runtime crawl if app is running
    if base_url:
        runtime_map = extract_routes_runtime(base_url)
        # Merge: add runtime routes not already in static list
        existing_paths = {r["path"] for r in ui_map["routes"]}
        for r in runtime_map["routes"]:
            if r["path"] not in existing_paths:
                ui_map["routes"].append(r)
        ui_map = UIMap(routes=ui_map["routes"], base_url=base_url)

    # Enrich with LLM descriptions
    ui_map = describe_ui_with_llm(repo_dir, stack_profile, ui_map)

    store.set_checkpoint(job_id, "gui_inspected", "done", {
        "route_count": len(ui_map["routes"]),
        "routes": [r["path"] for r in ui_map["routes"]],
        "base_url": ui_map["base_url"],
    })
    return ui_map
