"""Build, test, and application-start pipeline."""

from __future__ import annotations

import shlex
import signal
import socket
import subprocess
import sys
import time
from typing import Optional, TypedDict

from backend.logger import get_logger
from backend.memory import store

log = get_logger(__name__)


class CheckpointResult(TypedDict):
    status: str        # "done" | "blocked" | "not_applicable"
    command: str
    stdout: str
    stderr: str
    exit_code: int


_MAX_OUTPUT = 4000  # chars kept per stdout/stderr


def _run(cmd: list[str], cwd: str) -> CheckpointResult:
    """Run a command, return a CheckpointResult."""
    log.debug("Running: %s (cwd=%s)", " ".join(cmd), cwd)
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        stdout = proc.stdout[-_MAX_OUTPUT:] if proc.stdout else ""
        stderr = proc.stderr[-_MAX_OUTPUT:] if proc.stderr else ""
        status = "done" if proc.returncode == 0 else "blocked"
        if proc.returncode != 0:
            log.warning("Command exited %d: %s", proc.returncode, " ".join(cmd))
        return CheckpointResult(
            status=status,
            command=" ".join(cmd),
            stdout=stdout,
            stderr=stderr,
            exit_code=proc.returncode,
        )
    except FileNotFoundError:
        log.error("Command not found: %s", cmd[0])
        return CheckpointResult(
            status="blocked",
            command=" ".join(cmd),
            stdout="",
            stderr=f"Command not found: {cmd[0]}",
            exit_code=-1,
        )
    except subprocess.TimeoutExpired:
        log.error("Command timed out (300s): %s", " ".join(cmd))
        return CheckpointResult(
            status="blocked",
            command=" ".join(cmd),
            stdout="",
            stderr="Command timed out after 300 seconds",
            exit_code=-1,
        )


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def install_deps(repo_dir: str, stack_profile: dict, job_id: int) -> CheckpointResult:
    """Install project dependencies based on detected stack."""
    langs = stack_profile.get("languages", [])
    pms = stack_profile.get("package_managers", [])
    import os
    from pathlib import Path

    root = Path(repo_dir)

    if "Python" in langs:
        if (root / "requirements.txt").exists():
            result = _run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], repo_dir)
        elif (root / "pyproject.toml").exists():
            result = _run([sys.executable, "-m", "pip", "install", "-e", "."], repo_dir)
        else:
            result = CheckpointResult(
                status="not_applicable", command="", stdout="", stderr="No Python deps file found.", exit_code=0
            )
    elif "JavaScript" in langs or "TypeScript" in langs:
        if "yarn" in pms and (root / "yarn.lock").exists():
            result = _run(["yarn", "install"], repo_dir)
        else:
            result = _run(["npm", "install"], repo_dir)
    elif "Java" in langs:
        if "maven" in stack_profile.get("build_system", ""):
            result = _run(["mvn", "dependency:resolve", "-q"], repo_dir)
        elif "gradle" in stack_profile.get("build_system", ""):
            result = _run(["./gradlew", "dependencies", "-q"], repo_dir)
        else:
            result = CheckpointResult(
                status="not_applicable", command="", stdout="", stderr="No Java dep install command found.", exit_code=0
            )
    elif "Rust" in langs:
        result = _run(["cargo", "fetch"], repo_dir)
    elif "Go" in langs:
        result = _run(["go", "mod", "download"], repo_dir)
    else:
        result = CheckpointResult(
            status="not_applicable", command="", stdout="", stderr="No supported package manager detected.", exit_code=0
        )

    store.set_checkpoint(job_id, "deps_installed", result["status"], {
        "command": result["command"],
        "stdout": result["stdout"][-500:],
        "stderr": result["stderr"][-500:],
    })
    return result


def build(repo_dir: str, stack_profile: dict, job_id: int) -> CheckpointResult:
    """Run the project build step."""
    from pathlib import Path
    root = Path(repo_dir)
    langs = stack_profile.get("languages", [])
    build_system = stack_profile.get("build_system", "unknown")

    if "JavaScript" in langs or "TypeScript" in langs:
        # Run npm run build if a build script exists
        pkg_json_path = root / "package.json"
        if pkg_json_path.exists():
            import json
            try:
                pkg = json.loads(pkg_json_path.read_text())
                if "build" in pkg.get("scripts", {}):
                    result = _run(["npm", "run", "build"], repo_dir)
                else:
                    result = CheckpointResult(
                        status="not_applicable", command="", stdout="", stderr="No build script in package.json.", exit_code=0
                    )
            except json.JSONDecodeError:
                result = CheckpointResult(
                    status="blocked", command="", stdout="", stderr="Could not parse package.json.", exit_code=-1
                )
        else:
            result = CheckpointResult(
                status="not_applicable", command="", stdout="", stderr="No package.json found.", exit_code=0
            )
    elif "Java" in langs:
        if "maven" in build_system:
            result = _run(["mvn", "package", "-DskipTests", "-q"], repo_dir)
        elif "gradle" in build_system:
            result = _run(["./gradlew", "build", "-x", "test"], repo_dir)
        else:
            result = CheckpointResult(
                status="not_applicable", command="", stdout="", stderr="No known Java build system.", exit_code=0
            )
    elif "Rust" in langs:
        result = _run(["cargo", "build"], repo_dir)
    elif "Go" in langs:
        result = _run(["go", "build", "./..."], repo_dir)
    elif "Python" in langs:
        # Python typically doesn't need a separate build step
        result = CheckpointResult(
            status="not_applicable", command="", stdout="", stderr="Python: no separate build step.", exit_code=0
        )
    else:
        result = CheckpointResult(
            status="not_applicable", command="", stdout="", stderr="No known build step for detected stack.", exit_code=0
        )

    store.set_checkpoint(job_id, "build_done", result["status"], {
        "command": result["command"],
        "stdout": result["stdout"][-500:],
        "stderr": result["stderr"][-500:],
    })
    return result


def run_tests(repo_dir: str, stack_profile: dict, job_id: int, checkpoint_name: str = "tests_run") -> CheckpointResult:
    """Run the project's test suite."""
    from pathlib import Path
    root = Path(repo_dir)
    langs = stack_profile.get("languages", [])
    test_frameworks = stack_profile.get("test_frameworks", [])

    if "Python" in langs:
        if "pytest" in test_frameworks or (root / "pytest.ini").exists() or (root / "setup.cfg").exists():
            result = _run([sys.executable, "-m", "pytest", "--tb=short", "-q"], repo_dir)
        else:
            result = _run([sys.executable, "-m", "unittest", "discover", "-q"], repo_dir)
    elif "JavaScript" in langs or "TypeScript" in langs:
        result = _run(["npm", "test", "--", "--passWithNoTests"], repo_dir)
    elif "Java" in langs:
        build_system = stack_profile.get("build_system", "")
        if "maven" in build_system:
            result = _run(["mvn", "test", "-q"], repo_dir)
        elif "gradle" in build_system:
            result = _run(["./gradlew", "test"], repo_dir)
        else:
            result = CheckpointResult(
                status="not_applicable", command="", stdout="", stderr="No Java test runner found.", exit_code=0
            )
    elif "Rust" in langs:
        result = _run(["cargo", "test"], repo_dir)
    elif "Go" in langs:
        result = _run(["go", "test", "./..."], repo_dir)
    else:
        result = CheckpointResult(
            status="not_applicable", command="", stdout="", stderr="No supported test runner detected.", exit_code=0
        )

    store.set_checkpoint(job_id, checkpoint_name, result["status"], {
        "command": result["command"],
        "stdout": result["stdout"][-500:],
        "stderr": result["stderr"][-500:],
        "exit_code": result["exit_code"],
    })
    return result


def _wait_for_port(port: int, timeout: float = 10.0) -> bool:
    """Poll localhost:port until it accepts a connection or timeout expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def _find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def start_app(repo_dir: str, stack_profile: dict, job_id: int) -> Optional[subprocess.Popen]:
    """
    Attempt to start the application in the background.
    Returns the Popen handle if successful, None otherwise.
    """
    from pathlib import Path
    root = Path(repo_dir)
    langs = stack_profile.get("languages", [])
    entry_points = stack_profile.get("entry_points", [])
    frameworks = stack_profile.get("frameworks", [])

    cmd: Optional[list[str]] = None
    port = 8080

    if "Python" in langs:
        if "Flask" in frameworks:
            ep = next((e for e in entry_points if e.endswith(".py")), None)
            if ep:
                cmd = [sys.executable, ep]
        elif "FastAPI" in frameworks:
            ep = next((e for e in entry_points if e.endswith(".py")), None)
            if ep:
                module = ep.replace(".py", "").replace("/", ".")
                cmd = [sys.executable, "-m", "uvicorn", f"{module}:app", "--port", str(port), "--host", "0.0.0.0"]
        elif "Django" in frameworks and (root / "manage.py").exists():
            cmd = [sys.executable, "manage.py", "runserver", f"0.0.0.0:{port}"]
        elif "Streamlit" in frameworks:
            ep = next((e for e in entry_points if e.endswith(".py")), None)
            if ep:
                cmd = [sys.executable, "-m", "streamlit", "run", ep, "--server.port", str(port)]
        elif entry_points:
            cmd = [sys.executable, entry_points[0]]
    elif "JavaScript" in langs or "TypeScript" in langs:
        cmd = ["npm", "start"]
    elif "Go" in langs:
        cmd = ["go", "run", "./..."]

    if cmd is None:
        store.set_checkpoint(job_id, "app_launched", "blocked", {
            "reason": "No known start command for detected stack",
        })
        return None

    try:
        log.info("Starting application: %s (cwd=%s)", " ".join(cmd), repo_dir)
        proc = subprocess.Popen(
            cmd,
            cwd=repo_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        bound = _wait_for_port(port, timeout=10.0)
        evidence: dict = {
            "command": " ".join(cmd),
            "pid": proc.pid,
            "port": port if bound else None,
        }
        status = "done" if bound else "blocked"
        store.set_checkpoint(job_id, "app_launched", status, evidence)
        if bound:
            log.info("Application bound to port %d (pid=%d)", port, proc.pid)
        else:
            log.warning("Application started (pid=%d) but did not bind port %d within 10s", proc.pid, port)
        return proc
    except Exception as exc:
        log.error("Failed to start application: %s", exc)
        store.set_checkpoint(job_id, "app_launched", "blocked", {"error": str(exc)})
        return None


def stop_app(proc: subprocess.Popen) -> None:
    """Gracefully stop the application process."""
    if proc is None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    except OSError:
        pass
