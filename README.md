# Code-Scribe

> AI-powered documentation agent for GitHub repositories.

Code-Scribe is a Python-based AI agent that clones a GitHub repository, analyzes its structure and stack, and automatically generates two PDF deliverables:

- **End User Guide** — functional overview, installation, and usage instructions for end users
- **Developer / Code Guide** — architecture deep-dive, module reference, and contribution guide for developers

It also injects an in-app chat widget into target applications that have a web GUI, monitors the repository for changes, and keeps all documentation in sync automatically.

## Features

- FastAPI backend with real-time WebSocket log streaming
- Groq LLM integration (default: `openai/gpt-oss-120b`)
- Structured Python logging bridged to the WebSocket bus — every agent step is visible live in the UI
- WeasyPrint HTML → PDF rendering (A4, clean typography)
- APScheduler background polling for repository changes
- SQLite persistence — resumes from last completed checkpoint across stop/restart cycles
- Reusable knowledge store built from successful user chat interactions
- Evidence-based documentation — unverifiable sections are explicitly marked `[UNVERIFIED]`

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.11+ | Tested on Python 3.11 and 3.14 |
| `git` CLI | Must be in `PATH` for GitPython |
| Groq API key | <https://console.groq.com> |
| WeasyPrint system libs | See below |

### WeasyPrint system dependencies

**Ubuntu / Debian:**
```bash
sudo apt-get install libpango-1.0-0 libpangoft2-1.0-0 libcairo2
```

**macOS (Homebrew):**
```bash
brew install pango
```

**Fedora / RHEL:**
```bash
sudo dnf install pango cairo
```

---

## Quick Start

```bash
# 1. Clone and enter the project
git clone https://github.com/your-org/code-scribe.git
cd code-scribe

# 2. Install Python dependencies
pip install -e .

# 3. Configure environment variables
cp .env.example .env
# Edit .env and fill in at least GROQ_API_KEY

# 4. Start the server
code-scribe
# or via uvicorn directly:
# uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Then open [http://localhost:8000](http://localhost:8000) in your browser.

---

## Usage

1. Enter a public GitHub repository URL in the input field.
2. Click **Analyze**. The agent will:
   - Clone the repository
   - Detect the technology stack
   - Install dependencies, build, and run tests
   - Start the application (if possible)
   - Inspect the GUI (routes, views)
   - Generate the End User Guide and Developer Guide PDFs
   - Inject a chat widget into the target app (if it has a web GUI)
   - Re-run tests to confirm the injection didn't break anything
3. Download the generated PDFs from the **Generated Documents** section.
4. Use the **Ask** panel to ask questions about the repository — answers are grounded in the analysis.

---

## Configuration

All configuration is driven by environment variables. Copy `.env.example` to `.env` and fill in values:

| Variable | Default | Required | Description |
|---|---|---|---|
| `GROQ_API_KEY` | — | ✅ | Groq API key |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | | Groq model identifier |
| `POLL_INTERVAL_SECONDS` | `300` | | How often to poll for repo changes (seconds) |
| `WORKSPACE_DIR` | `./workspaces` | | Directory where repos are cloned |
| `OUTPUT_DIR` | `./outputs` | | Directory where PDFs and JSON are written |
| `DB_PATH` | `./code_scribe.db` | | SQLite database path |

---

## Project Structure

```
code-scribe/
├── backend/
│   ├── main.py                  # FastAPI app + lifespan (startup/shutdown)
│   ├── config.py                # Pydantic-settings configuration
│   ├── logger.py                # Structured logging — stderr + WebSocket bridge
│   ├── scheduler.py             # APScheduler background job setup
│   ├── api/
│   │   ├── routes.py            # REST endpoints
│   │   └── websocket.py         # WebSocket log bus + streaming endpoint
│   ├── agent/
│   │   ├── orchestrator.py      # 12-step workflow state machine
│   │   ├── repo.py              # Clone / diff / polling (GitPython)
│   │   ├── detector.py          # Stack detection (language, framework, GUI)
│   │   ├── builder.py           # Install deps, build, test, start app
│   │   ├── inspector.py         # GUI inspection and route extraction
│   │   ├── doc_generator.py     # LLM-driven section generation (concurrent)
│   │   ├── pdf_renderer.py      # WeasyPrint HTML → PDF
│   │   ├── chat_injector.py     # LLM chat widget generation + injection
│   │   ├── chat_handler.py      # Grounded Q&A with knowledge lookup
│   │   ├── knowledge_matcher.py # Token-overlap knowledge retrieval
│   │   ├── sync_monitor.py      # Diff-driven selective re-generation
│   │   ├── acceptance_check.py  # Acceptance criteria evaluator
│   │   └── llm.py               # Groq API client with retry + rate-limit logic
│   ├── memory/
│   │   ├── store.py             # SQLite read/write helpers
│   │   └── schema.sql           # DB schema (jobs, checkpoints, documents, knowledge)
│   └── templates/
│       └── doc_base.html        # A4 Jinja2 template for PDF rendering
├── frontend/
│   ├── index.html               # Single-page app shell
│   ├── app.js                   # Vanilla JS: form, WebSocket, chat, downloads
│   └── style.css                # Clean minimal styles
├── tests/
│   ├── conftest.py              # Shared fixtures (isolated DB, tmp_repo, job_id)
│   ├── test_acceptance_check.py
│   ├── test_builder.py
│   ├── test_chat_handler.py
│   ├── test_chat_injector.py
│   ├── test_detector.py
│   ├── test_doc_generator.py
│   ├── test_inspector.py
│   ├── test_knowledge_matcher.py
│   ├── test_llm.py
│   ├── test_main.py
│   ├── test_orchestrator.py
│   ├── test_orchestrator_extended.py
│   ├── test_repo.py
│   ├── test_routes.py
│   ├── test_scheduler.py
│   ├── test_store.py
│   ├── test_sync_monitor.py
│   ├── test_sync_monitor_extended.py
│   ├── test_websocket.py
│   └── test_websocket_extended.py
├── outputs/                     # Generated PDFs and summary JSON (gitignored)
├── workspaces/                  # Cloned repositories (gitignored)
├── pyproject.toml
├── .env.example
└── README.md
```

---

## Checkpoint Names

The orchestrator persists the following named checkpoints:

| Checkpoint | Step | Meaning |
|---|---|---|
| `repo_cloned` | 1 | Repository successfully cloned / updated |
| `stack_detected` | 2 | Technology stack identified |
| `deps_installed` | 3 | Dependencies installed |
| `build_done` | 4 | Build step executed |
| `tests_run` | 5 | Initial test run complete |
| `app_launched` | 6 | Application started (port bound) |
| `gui_inspected` | 7 | Routes and views extracted |
| `user_guide_generated` | 8 | End User Guide PDF written |
| `dev_guide_generated` | 9 | Developer Guide PDF written |
| `chat_view_injected` | 10 | Chat widget injected into target app |
| `tests_rerun` | 11 | Post-injection test run complete |
| `memory_saved` | 12 | Output schema JSON written |

If the server is stopped mid-run, the next startup automatically resumes from the last completed checkpoint.

---

## Outputs

All outputs land in `./outputs/` (or `OUTPUT_DIR`):

| File | Contents |
|---|---|
| `{job_id}_user_guide.pdf` | End User Guide |
| `{job_id}_dev_guide.pdf` | Developer / Code Guide |
| `{job_id}_summary.json` | Full output schema: stack profile, checkpoints, acceptance criteria, learning log |

---

## Running Tests

```bash
# Install dev dependencies (if not already installed via pip install -e .)
pip install -e .
pip install pytest pytest-cov pytest-asyncio anyio

# Run the full test suite
pytest

# Run with coverage report
pytest --cov=backend --cov-report=term-missing

# Run a single test file
pytest tests/test_orchestrator.py -v
```

The test suite uses an isolated in-memory SQLite database per test, so no real database is touched.
No Groq API key is required to run the tests — all LLM calls are mocked.

---

## Logging

Code-Scribe uses Python's standard `logging` module configured in [`backend/logger.py`](backend/logger.py).

- **DEBUG** — detailed per-command output (subprocess calls, LLM request parameters)
- **INFO** — one-line progress for each workflow step (visible in the browser UI via WebSocket)
- **WARNING** — recoverable problems (rate-limit back-off, app launch without port binding)
- **ERROR** — step failures recorded in the checkpoint table

Logs are written to **stderr** and forwarded to the **WebSocket log bus** at INFO and above, so every step appears live in the browser.

To increase verbosity during development:

```python
import logging
logging.getLogger("backend").setLevel(logging.DEBUG)
```

---

## License

MIT
