# Code-Scribe Agent — Implementation Plan

## Top-Level Overview

Build **Code-Scribe**, a Python-based expert AI software engineering agent that:
- Accepts a GitHub repository URL via a web GUI (FastAPI backend + React/HTML frontend)
- Analyzes the repo (structure, stack, build, tests, runtime behavior, GUI detection)
- Generates two PDF deliverables: an **End User Guide** and a **Developer/Code Guide**
- Injects a **chat view** into the target app's UI if it has a GUI
- Monitors the repo for changes and keeps documentation in sync
- Persists all work state to a local SQLite database across stop/restart cycles
- Learns from successful user chat interactions and stores reusable knowledge

**LLM:** Groq API (openai/gpt-oss-120b)  
**PDF engine:** WeasyPrint (HTML/CSS → PDF)  
**Persistence:** SQLite via `sqlite3` stdlib + JSON columns  
**Frontend:** Minimal React SPA (Vite) or plain HTML/JS — served statically by FastAPI  
**Monitoring:** APScheduler background job polling remote repo for new commits  

---

## Architecture Overview

```
code-scribe/
├── backend/
│   ├── main.py                  # FastAPI app entry point
│   ├── api/
│   │   ├── routes.py            # REST endpoints
│   │   └── websocket.py         # WebSocket for real-time log streaming
│   ├── agent/
│   │   ├── orchestrator.py      # Top-level workflow state machine
│   │   ├── repo.py              # Clone / diff / polling logic
│   │   ├── detector.py          # Stack detection (language, framework, GUI)
│   │   ├── builder.py           # Install deps, build, run tests, start app
│   │   ├── inspector.py         # GUI inspection, route/URL extraction
│   │   ├── doc_generator.py     # LLM-driven doc section generation
│   │   ├── pdf_renderer.py      # WeasyPrint HTML → PDF
│   │   ├── chat_injector.py     # LLM-driven chat widget code generation + injection
│   │   └── llm.py               # Groq API client wrapper
│   ├── memory/
│   │   ├── store.py             # SQLite read/write helpers
│   │   └── schema.sql           # DB schema
│   ├── scheduler.py             # APScheduler background polling setup
│   └── config.py                # Env vars / settings (GROQ_API_KEY, poll interval, etc.)
├── frontend/
│   ├── index.html               # Single-page app shell
│   ├── app.js                   # URL input, job status, log viewer, chat panel
│   └── style.css
├── outputs/                     # Generated PDFs and injected files land here
├── workspaces/                  # Cloned repos are checked out here
├── pyproject.toml               # Python project metadata + dependencies
├── .env.example                 # Template for required env vars
└── README.md
```

---

## Sub-Tasks

---

### Sub-Task 1 — Project Scaffold and Configuration

**Status:** `[ ] pending`

**Intent:**  
Establish the full directory layout, Python project metadata, dependency declarations, and environment configuration. This is the foundation every other sub-task builds on.

**Expected Outcomes:**
- `pyproject.toml` exists with all required dependencies declared
- `.env.example` documents every required environment variable
- `backend/config.py` loads settings from environment with sane defaults
- `outputs/` and `workspaces/` directories exist (gitignored contents)
- `README.md` contains project overview and quickstart instructions

**Todo List:**
1. Create top-level `pyproject.toml` with `[project]` metadata and `[project.dependencies]`:
   - `fastapi`, `uvicorn[standard]`
   - `groq` (official Groq Python SDK)
   - `weasyprint`
   - `gitpython`
   - `apscheduler`
   - `aiofiles`
   - `python-dotenv`
   - `jinja2` (HTML template rendering for PDF)
2. Create `.env.example` with keys: `GROQ_API_KEY`, `GROQ_MODEL`, `POLL_INTERVAL_SECONDS`, `WORKSPACE_DIR`, `OUTPUT_DIR`
3. Create `backend/config.py` — pydantic-settings `Settings` class loading from `.env`
4. Create `outputs/` and `workspaces/` with `.gitkeep` placeholder files
5. Create skeleton `README.md` with project description, setup, and run instructions
6. Create `backend/__init__.py` and all `__init__.py` files for sub-packages

**Relevant Context:**
- No existing files — full greenfield
- `GROQ_API_KEY` is a required secret; never hardcode it
- `WORKSPACE_DIR` defaults to `./workspaces`, `OUTPUT_DIR` defaults to `./outputs`

---

### Sub-Task 2 — SQLite Persistent Memory Layer

**Status:** `[ ] pending`

**Intent:**  
Implement the persistence layer that survives stop/restart cycles. All agent state, job progress, learned knowledge, and validated user flows are stored here.

**Expected Outcomes:**
- `backend/memory/schema.sql` defines all tables
- `backend/memory/store.py` provides typed read/write functions for every table
- The DB is auto-initialized on first startup
- Memory correctly round-trips: save state → restart process → load state → resume

**Todo List:**
1. Write `backend/memory/schema.sql` with tables:
   - `jobs` — `(id, repo_url, branch, commit_sha, status, created_at, updated_at)`
   - `checkpoints` — `(job_id, checkpoint_name, status, evidence, recorded_at)`
   - `documents` — `(job_id, doc_type, file_path, generated_at)`
   - `learned_knowledge` — `(id, job_id, question, answer, source_evidence, confidence, created_at)`
   - `user_interactions` — `(id, job_id, user_message, agent_response, validated, created_at)`
2. Write `backend/memory/store.py`:
   - `init_db()` — create tables if not exist, called at app startup
   - `upsert_job(repo_url, branch, commit_sha) -> job_id`
   - `get_active_job() -> dict | None`
   - `set_checkpoint(job_id, name, status, evidence)`
   - `get_checkpoints(job_id) -> list[dict]`
   - `save_document(job_id, doc_type, file_path)`
   - `save_learned_knowledge(job_id, question, answer, evidence)`
   - `save_user_interaction(job_id, user_message, agent_response, validated)`
   - `get_learned_knowledge(job_id) -> list[dict]`
3. Ensure all writes use transactions; reads use `row_factory = sqlite3.Row`
4. Write a small test script `backend/memory/test_store.py` to verify round-trip

**Relevant Context:**
- Use `sqlite3` stdlib — no ORM dependency
- DB file path comes from `config.py` (`DB_PATH`, defaults to `./code_scribe.db`)
- `checkpoints` table maps directly to the "TEST / CHECKPOINT REQUIREMENTS" in the spec

---

### Sub-Task 3 — Groq LLM Client Wrapper

**Status:** `[ ] pending`

**Intent:**  
Provide a single, reusable interface to the Groq API used by every agent component. Centralises model selection, prompt construction, retry logic, and token budgeting.

**Expected Outcomes:**
- `backend/agent/llm.py` exposes a clean `ask(prompt, system, max_tokens) -> str` function
- Model name is read from config (not hardcoded)
- Errors from Groq API surface as descriptive exceptions, not raw HTTP errors
- A simple smoke-test confirms the key and model work before the main workflow starts

**Todo List:**
1. Write `backend/agent/llm.py`:
   - Import `groq` SDK, read `GROQ_API_KEY` and `GROQ_MODEL` from config
   - `ask(prompt: str, system: str = "", max_tokens: int = 4096) -> str`
     - Builds `messages` list with optional system role
     - Calls `client.chat.completions.create()`
     - Returns `response.choices[0].message.content`
   - `ask_structured(prompt: str, system: str, schema_hint: str) -> str`
     - Appends schema hint to prompt, asks LLM to respond as JSON
     - Returns raw string (caller parses JSON)
2. Add exponential back-off retry (max 3 attempts) for rate-limit / transient errors
3. Write `backend/agent/llm_smoke_test.py` — calls `ask("Say OK")`, prints response

**Relevant Context:**
- Groq SDK: `from groq import Groq`; `client = Groq(api_key=...)`
- Models available on free tier: `openai/gpt-oss-120b`, `mixtral-8x7b-32768`
- Keep prompts modular — each agent component builds its own prompt, passes to `ask()`

---

### Sub-Task 4 — Repository Access and Stack Detection

**Status:** `[ ] pending`

**Intent:**  
Clone the target repo, read its structure, and detect the technology stack, build system, test framework, GUI presence, and entry points. This feeds every downstream step.

**Expected Outcomes:**
- `backend/agent/repo.py` can clone a repo, fetch latest commit SHA, and diff commits
- `backend/agent/detector.py` returns a structured `StackProfile` datadict
- GUI presence is determined by inspecting package manifests and source files
- Detection results are persisted as a `checkpoints` record

**Todo List:**
1. Write `backend/agent/repo.py`:
   - `clone_or_update(repo_url, dest_dir) -> str` — returns checked-out commit SHA
     - If dest_dir exists: `git fetch` + `git pull`; else: `git clone`
   - `get_current_sha(repo_dir) -> str`
   - `get_changed_files(repo_dir, old_sha, new_sha) -> list[str]`
   - `poll_for_changes(repo_url, repo_dir, last_known_sha) -> str | None`
     - Returns new SHA if remote HEAD changed, else None
2. Write `backend/agent/detector.py`:
   - Walk repo directory tree, read key files: `package.json`, `pyproject.toml`,
     `requirements.txt`, `Cargo.toml`, `pom.xml`, `build.gradle`, `Makefile`, etc.
   - Detect: primary language(s), frameworks, build system, test framework(s)
   - Detect GUI presence:
     - Python: check for `tkinter`, `PyQt`, `wxPython`, `streamlit`, `flask` templates, `react` deps
     - JS/TS: always GUI; check for `react`, `vue`, `angular`, `svelte`
     - Java: check for `javafx`, `swing`
     - Read `src/` and `templates/` for HTML/JSX files
   - Return `StackProfile` TypedDict:
     ```
     {
       "languages": [...],
       "frameworks": [...],
       "build_system": "...",
       "test_framework": [...],
       "has_gui": bool,
       "gui_type": "web|desktop|none",
       "entry_points": [...],
       "package_managers": [...]
     }
     ```
   - If ambiguous, ask LLM with directory listing as context
3. Persist detection result as `checkpoints` record `"stack_detection"` with evidence JSON

**Relevant Context:**
- Use `gitpython` (`git.Repo`) for all git operations
- Avoid shell `subprocess` unless gitpython cannot do it; if used, always pass `check=True`
- Detection must be evidence-based (file existence, not assumptions)

---

### Sub-Task 5 — Build, Test, and Runtime Execution

**Status:** `[ ] pending`

**Intent:**  
Install dependencies, build the application, run its tests, and start it (if feasible) to validate runtime behavior. All outcomes — success or blocker — are recorded in memory.

**Expected Outcomes:**
- `backend/agent/builder.py` executes the full build/test/run pipeline
- Each step produces a `checkpoints` record with `status` and `evidence` (stdout/stderr excerpt)
- Partial failures are recorded without stopping downstream deliverable generation
- Application process handle is kept alive for GUI inspection (Sub-Task 6)

**Todo List:**
1. Write `backend/agent/builder.py`:
   - `install_deps(repo_dir, stack_profile) -> CheckpointResult`
     - Python: `pip install -r requirements.txt` or `pip install -e .`
     - Node: `npm install` or `yarn install`
     - Others: detect and run appropriate package manager command
   - `build(repo_dir, stack_profile) -> CheckpointResult`
     - Run build command inferred from stack (e.g. `npm run build`, `mvn package`, `cargo build`)
     - If no build step detected, mark as `"not_applicable"`
   - `run_tests(repo_dir, stack_profile) -> CheckpointResult`
     - Python: `pytest` or `python -m unittest`
     - Node: `npm test`
     - Capture exit code, stdout, stderr; truncate to 4000 chars for storage
   - `start_app(repo_dir, stack_profile) -> subprocess.Popen | None`
     - Attempt to start the app in background; return process handle or None
     - Wait up to 10 seconds for app to bind a port (check via socket)
     - Record bound port in checkpoint evidence
2. Each `CheckpointResult` is a TypedDict: `{status, command, stdout, stderr, exit_code}`
3. All checkpoints persisted via `store.set_checkpoint()`
4. Add `stop_app(process)` — graceful terminate + SIGKILL fallback

**Relevant Context:**
- Use `subprocess.run()` for install/build/test; `subprocess.Popen()` for app start
- Never start the app as root; validate the command before executing
- If `start_app` fails, mark `"app_launch"` checkpoint as `"blocked"` with evidence and continue

---

### Sub-Task 6 — GUI Inspection and Route Extraction

**Status:** `[ ] pending`

**Intent:**  
If the app has a GUI, inspect it to extract routes, views, navigation paths, and component descriptions. This evidence grounds both the user guide and the chat widget.

**Expected Outcomes:**
- `backend/agent/inspector.py` extracts a `UIMap` from running web app or static source
- Routes, page titles, and key UI controls are documented
- Result persisted to memory for use by doc generator and chat injector

**Todo List:**
1. Write `backend/agent/inspector.py`:
   - `extract_routes_static(repo_dir, stack_profile) -> UIMap`
     - For React/Vue/Angular: parse router config files for route definitions
     - For Flask/Django: parse `urls.py`, `routes.py`, `app.py` for `@app.route`
     - For FastAPI: parse `router` declarations
   - `extract_routes_runtime(base_url: str) -> UIMap`
     - Fetch `base_url` with `httpx`; parse HTML for `<nav>`, `<a>`, form actions
     - Walk up to 2 levels of links within the same origin
   - `describe_ui_with_llm(repo_dir, stack_profile, routes) -> str`
     - Build prompt: directory listing + key component files + route list
     - Ask LLM to describe the purpose of each view in plain English
   - Returns `UIMap` TypedDict: `{routes: list[{path, title, description}], base_url: str}`
2. Persist `UIMap` as `checkpoints` record `"gui_inspection"`
3. If `has_gui` is False, skip entirely and return empty `UIMap`

**Relevant Context:**
- Use `httpx` (sync) for simple HTTP fetching; no headless browser required
- Static analysis is the primary path; runtime inspection is a best-effort enhancement
- The `base_url` comes from the port detected in Sub-Task 5 `start_app`

---

### Sub-Task 7 — Documentation Generation

**Status:** `[ ] pending`

**Intent:**  
Use the Groq LLM to generate all sections of both the End User Guide and the Developer/Code Guide, grounded in repo evidence. Render each to a styled PDF via WeasyPrint.

**Expected Outcomes:**
- `backend/agent/doc_generator.py` produces two fully structured Markdown/HTML documents
- `backend/agent/pdf_renderer.py` converts them to PDF files in `outputs/`
- Generated PDFs match the section structure defined in the spec exactly
- Unverified sections are explicitly marked `[UNVERIFIED]`
- Document paths persisted to `documents` table

**Todo List:**
1. Write `backend/agent/doc_generator.py`:
   - `generate_user_guide(job_id, stack_profile, ui_map, checkpoints) -> str` (returns HTML string)
     - Build a prompt per major section (13 sections from spec) using verified evidence
     - Section 1 (Title Page): populated from repo metadata, date, job_id
     - Sections 2–12: LLM-generated, each prompt includes relevant evidence snippets
     - Section 13 (Appendices): static template + LLM for keyboard shortcuts
     - Mark any section where evidence was unavailable with `[UNVERIFIED]`
   - `generate_dev_guide(job_id, stack_profile, checkpoints, repo_dir) -> str` (returns HTML)
     - Build prompts for each of the 14 sections from spec
     - Include: folder tree, key file contents (truncated), test output, build output
     - Mark gaps with `[UNVERIFIED]`
2. Write `backend/agent/pdf_renderer.py`:
   - `render_pdf(html_content: str, output_path: str) -> str`
     - Wrap HTML in a styled Jinja2 template (A4, clean typography)
     - Call `weasyprint.HTML(string=html_content).write_pdf(output_path)`
     - Return absolute path to written file
   - Create `backend/templates/doc_base.html` — A4 page, header/footer, code block styles
3. Persist output paths via `store.save_document()`
4. If `has_gui` is False, skip user guide generation entirely

**Relevant Context:**
- Evidence for prompts: repo README, detected stack, folder tree, build/test outputs, `UIMap`
- Keep each LLM prompt under ~3000 tokens; split large sections into subsections if needed
- WeasyPrint requires system libs (`libpango`, `libcairo`); note this in README

---

### Sub-Task 8 — Chat View Injection

**Status:** `[ ] pending`

**Intent:**  
When the target app has a GUI, use the LLM to generate a self-contained chat widget appropriate for the detected stack, inject it into the application's UI, and expose a `/chat` API endpoint on the target app's backend (or proxy through Code-Scribe).

**Expected Outcomes:**
- `backend/agent/chat_injector.py` generates and injects the chat widget
- The widget is integrated into the target app's existing layout, not bolted on externally
- A `/chat` API route answers user questions grounded in the generated documentation
- Checkpoint `"chat_view_injected"` recorded with list of modified files
- Injection is skipped with evidence if `has_gui` is False

**Todo List:**
1. Write `backend/agent/chat_injector.py`:
   - `generate_chat_widget_code(stack_profile, ui_map) -> dict[str, str]`
     - Prompt LLM with: detected stack, framework, existing UI patterns from source, `UIMap`
     - Ask LLM to produce the minimal chat widget component/template appropriate for the stack
     - Returns dict of `{filename: code_content}` for each file to create or modify
   - `inject_into_app(repo_dir, widget_files: dict[str, str], stack_profile)`
     - Write new widget files to repo
     - Identify layout/shell file (e.g. `App.jsx`, `base.html`, `index.html`)
     - Prompt LLM to produce a patch that wires the widget into the shell file
     - Apply the patch (write modified file)
   - `add_chat_api_endpoint(repo_dir, stack_profile)`
     - If target app is Python (Flask/FastAPI/Django): add `/chat` endpoint file
     - If target app is Node: add Express/Next.js route
     - Endpoint receives `{message, session_id}`, queries Code-Scribe's internal chat handler,
       returns `{response}`
2. Write `backend/agent/chat_handler.py`:
   - `answer_question(job_id, question: str) -> str`
     - Load learned knowledge for job from DB
     - Build context: UIMap, doc summaries, learned Q&A pairs
     - Call `llm.ask(prompt)` with grounded context
     - If answer is grounded: persist to `learned_knowledge`; if not: return "I cannot verify this"
3. Persist `"chat_view_injected"` checkpoint with modified file list as evidence

**Relevant Context:**
- LLM generates the widget code — do not hardcode a specific framework widget
- Chat endpoint in the target app calls back to Code-Scribe's `chat_handler` or is self-contained
- Always check `has_gui` before running any injection logic

---

### Sub-Task 9 — FastAPI Backend and WebSocket Log Streaming

**Status:** `[ ] pending`

**Intent:**  
Expose the agent's functionality via a REST API and stream real-time progress logs to the frontend over WebSocket. This is what the web GUI calls.

**Expected Outcomes:**
- `backend/main.py` starts FastAPI, mounts static frontend, inits DB, starts scheduler
- REST endpoints cover: submit job, get job status, get documents, chat, get checkpoints
- WebSocket endpoint streams agent log lines to connected frontend clients in real time
- Scheduler background task polls for repo changes every `POLL_INTERVAL_SECONDS`

**Todo List:**
1. Write `backend/api/routes.py`:
   - `POST /api/jobs` — accept `{repo_url}`, create job in DB, trigger orchestrator
   - `GET /api/jobs/current` — return current job state from DB
   - `GET /api/jobs/{job_id}/checkpoints` — return checkpoint list
   - `GET /api/jobs/{job_id}/documents` — return document file paths
   - `GET /api/documents/{filename}` — serve generated PDF file
   - `POST /api/chat` — `{job_id, message}` → call `chat_handler.answer_question()`
2. Write `backend/api/websocket.py`:
   - `WS /ws/logs` — broadcast log lines to all connected clients
   - Implement a simple in-memory `log_bus` (asyncio Queue) that agent steps write to
   - Agent steps call `log_bus.put(line)` as they run; WebSocket handler reads and broadcasts
3. Write `backend/scheduler.py`:
   - Use `APScheduler` `AsyncIOScheduler`
   - Every `POLL_INTERVAL_SECONDS`: call `repo.poll_for_changes()` for active job
   - If changes detected: trigger re-run of doc generation for affected sections
4. Write `backend/main.py`:
   - Create `FastAPI` app
   - Call `store.init_db()` on startup
   - Mount `frontend/` as `StaticFiles` at `/`
   - Include routers from `routes.py` and `websocket.py`
   - Start scheduler on startup, stop on shutdown

**Relevant Context:**
- Use `asyncio.create_task` to run the orchestrator without blocking the API
- Log bus is a module-level `asyncio.Queue` — not a DB table
- Scheduler must not start a second job if one is already running

---

### Sub-Task 10 — Orchestrator State Machine

**Status:** `[ ] pending`

**Intent:**  
Wire all agent components together into the top-level workflow defined in the spec. The orchestrator loads prior memory on startup, runs each step in order, persists state after each milestone, and resumes from the last completed checkpoint on restart.

**Expected Outcomes:**
- `backend/agent/orchestrator.py` implements the full 12-step workflow from the spec
- On restart, completed checkpoints are skipped; failed or pending ones are retried
- Every meaningful state transition is persisted before proceeding
- Final output schema is produced as a JSON summary file alongside the PDFs
- All acceptance criteria checkpoints are evaluated and recorded

**Todo List:**
1. Write `backend/agent/orchestrator.py`:
   - `run_job(job_id: int)` — top-level async function called by API
   - Load existing checkpoints for `job_id` from DB
   - Execute each workflow step only if its checkpoint is not already `"done"`:
     1. Clone/update repo → `repo.clone_or_update()`
     2. Detect stack → `detector.detect()`
     3. Read docs/config → feed into detection context
     4. Install deps → `builder.install_deps()`
     5. Build → `builder.build()`
     6. Run tests → `builder.run_tests()`
     7. Start app → `builder.start_app()`
     8. Inspect GUI → `inspector.extract_routes_*()`
     9. Generate docs → `doc_generator.generate_*()`
     10. Inject chat view → `chat_injector.inject_into_app()` (if GUI)
     11. Re-run tests → `builder.run_tests()`
     12. Persist memory + produce output schema JSON
   - After each step: call `store.set_checkpoint()`, emit log line to `log_bus`
   - On exception: record `"blocked"` checkpoint with traceback evidence; continue if safe
2. Write output schema JSON to `outputs/{job_id}_summary.json` matching spec structure
3. Add `resume_job(job_id)` — reload state and call `run_job()` skipping done steps
4. On process startup: call `store.get_active_job()` → if found, call `resume_job()`

**Relevant Context:**
- Checkpoint names must match exactly: `"repo_cloned"`, `"stack_detected"`, `"deps_installed"`,
  `"build_done"`, `"tests_run"`, `"app_launched"`, `"gui_inspected"`, `"user_guide_generated"`,
  `"dev_guide_generated"`, `"chat_view_injected"`, `"tests_rerun"`, `"memory_saved"`
- Never mark a checkpoint `"done"` unless the step actually succeeded
- The output summary JSON must follow the OUTPUT SCHEMA section of the spec exactly

---

### Sub-Task 11 — Web Frontend

**Status:** `[ ] pending`

**Intent:**  
Build a minimal but functional web frontend that lets the user submit a GitHub URL, watch real-time agent progress, download generated PDFs, and interact with the agent's chat interface.

**Expected Outcomes:**
- `frontend/index.html` + `frontend/app.js` + `frontend/style.css` work as a standalone SPA
- URL submission triggers `POST /api/jobs` and opens WebSocket for log streaming
- Log lines appear in real time in a scrolling terminal-style pane
- "Download User Guide" and "Download Developer Guide" buttons appear when docs are ready
- A chat panel allows the user to ask questions answered by `POST /api/chat`
- On page reload, the current job state is fetched from `GET /api/jobs/current`

**Todo List:**
1. Write `frontend/index.html`:
   - Form: GitHub URL input + "Analyze" button
   - Status badge (idle / running / done / blocked)
   - Scrolling log pane (WebSocket feed)
   - Documents section with download links
   - Chat panel (message input + response display)
2. Write `frontend/app.js` (vanilla JS, no build step required):
   - `submitJob(url)` — POST to `/api/jobs`, connect WebSocket
   - `connectLogs()` — open `WS /ws/logs`, append messages to log pane
   - `loadCurrentJob()` — GET `/api/jobs/current` on page load, restore state
   - `downloadDoc(filename)` — fetch `/api/documents/{filename}`
   - `sendChat(message)` — POST `/api/chat`, display response
3. Write `frontend/style.css` — clean, minimal, dark terminal pane + light form area

**Relevant Context:**
- No build toolchain for frontend — plain ES6 modules served by FastAPI `StaticFiles`
- WebSocket URL is relative: `ws://${location.host}/ws/logs`
- Frontend must handle the case where the agent is already running (page reload mid-job)

---

### Sub-Task 12 — Documentation Sync Monitoring

**Status:** `[ ] pending`

**Intent:**  
Implement the background monitoring loop that detects repository changes and triggers selective re-generation of affected documentation sections, keeping guides synchronized with the codebase.

**Expected Outcomes:**
- Scheduler polls remote repo every `POLL_INTERVAL_SECONDS`
- When a new commit is detected, changed files are diffed
- Only affected doc sections are regenerated (not the full document)
- Updated PDFs replace the previous ones in `outputs/`
- Memory is updated after each successful sync cycle

**Todo List:**
1. Write `backend/agent/sync_monitor.py`:
   - `sync_check(job_id: int)` — called by scheduler
     - Load job from DB; get `last_commit_sha`
     - Call `repo.poll_for_changes()`; if None, return
     - Call `repo.get_changed_files(old_sha, new_sha)`
     - Map changed files to affected doc sections:
       - `src/**` changes → Core Logic, API sections of dev guide
       - `README*` or `docs/**` → Introduction, Architecture sections
       - Config files → Setup and Environment section
       - `tests/**` → Testing section
     - Re-generate only affected sections via `doc_generator`
     - Re-render full PDF (WeasyPrint re-renders the whole doc; sections are just LLM calls)
     - Update `last_commit_sha` in `jobs` table
     - Persist `"sync_completed"` checkpoint with new SHA as evidence
2. Register `sync_check` with APScheduler in `backend/scheduler.py`
3. Emit a WebSocket log line when sync triggers and completes

**Relevant Context:**
- Only run `sync_check` if `get_active_job()` returns a job with status `"done"`
- Diff-to-section mapping can be LLM-assisted if the changed file set is ambiguous
- Scheduler interval comes from `config.POLL_INTERVAL_SECONDS` (default: 300)

---

### Sub-Task 13 — Learning and Knowledge Persistence

**Status:** `[ ] pending`

**Intent:**  
Implement the learning loop that captures reusable support knowledge from successful user chat interactions, stores it in the DB, and uses it to improve future responses.

**Expected Outcomes:**
- Successful chat answers are stored in `learned_knowledge` table
- Future chat questions are matched against stored knowledge before calling LLM
- Unverified user claims are not stored unless confirmed
- Learning state is included in the final memory log section of the output schema

**Todo List:**
1. Update `backend/agent/chat_handler.py`:
   - Before calling LLM: query `store.get_learned_knowledge(job_id)` and prepend top-5
     most relevant Q&A pairs (simple keyword match, no embeddings needed)
   - After a successful grounded answer: call `store.save_learned_knowledge()`
   - After an unverifiable answer: do NOT store; respond with "I cannot verify this from
     the codebase"
2. Write `backend/agent/knowledge_matcher.py`:
   - `find_relevant(question: str, knowledge: list[dict], top_k: int = 5) -> list[dict]`
     - Score each knowledge item: count overlapping tokens between question and stored question
     - Return top-k by score (no ML required)
3. Update output schema JSON (`orchestrator.py`) to include `memory_learning_log` section:
   - Count of learned knowledge items
   - Count of user interactions
   - Last learned question (anonymized)

**Relevant Context:**
- No vector DB or embeddings — simple token overlap is sufficient for MVP
- `validated` flag on `user_interactions` table: set to True only after human or LLM confirmation
- Knowledge grounding check: answer must cite a file path, route, or verified behavior

---

### Sub-Task 14 — README, Validation, and Final Integration Test

**Status:** `[ ] pending`

**Intent:**  
Write the final README with full setup/run instructions, verify all components integrate correctly end-to-end, and confirm the acceptance criteria checklist is fully covered.

**Expected Outcomes:**
- `README.md` contains complete setup, configuration, and run instructions
- `pyproject.toml` includes a `[project.scripts]` entry: `code-scribe = "backend.main:start"`
- A manual end-to-end smoke test against a real public GitHub repo passes all 15 checkpoints
- All acceptance criteria from the spec are mapped to implemented components

**Todo List:**
1. Write complete `README.md`:
   - Prerequisites: Python 3.11+, WeasyPrint system libs, Groq API key
   - Install: `pip install -e .`
   - Configure: copy `.env.example` to `.env`, fill in `GROQ_API_KEY`
   - Run: `uvicorn backend.main:app --reload`
   - Usage: open `http://localhost:8000`, enter GitHub URL
   - Output locations: `outputs/` for PDFs
2. Add `"start"` function to `backend/main.py` calling `uvicorn.run()`
3. Write `backend/agent/acceptance_check.py`:
   - `evaluate_acceptance_criteria(job_id) -> dict` — reads checkpoints and documents table,
     returns dict mapping each of the 13 acceptance criteria to `pass/fail/partial`
4. Include acceptance check output in the `summary.json` under a `"acceptance_criteria"` key
5. Document WeasyPrint system dependency installation commands for Ubuntu/Debian and macOS

**Relevant Context:**
- WeasyPrint on Ubuntu: `sudo apt-get install libpango-1.0-0 libpangoft2-1.0-0`
- WeasyPrint on macOS: `brew install pango`
- Acceptance criteria numbers map directly to the spec's ACCEPTANCE CRITERIA section

---

## Dependency Order

```
Sub-Task 1  (scaffold)
    └──> Sub-Task 2  (memory)
             └──> Sub-Task 3  (LLM client)
                      ├──> Sub-Task 4  (repo + detector)
                      │        └──> Sub-Task 5  (build/test/run)
                      │                  └──> Sub-Task 6  (GUI inspect)
                      │                            ├──> Sub-Task 7  (doc generation)
                      │                            └──> Sub-Task 8  (chat injection)
                      └──> Sub-Task 9  (FastAPI + WebSocket)
                               └──> Sub-Task 10 (orchestrator)
                                        ├──> Sub-Task 11 (frontend)
                                        ├──> Sub-Task 12 (sync monitor)
                                        └──> Sub-Task 13 (learning)
                                                   └──> Sub-Task 14 (README + integration)
```
