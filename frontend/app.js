/**
 * Code-Scribe frontend — vanilla ES6 module.
 * No build step required; served by FastAPI StaticFiles.
 */

// ---------------------------------------------------------------------------
// DOM helpers
// ---------------------------------------------------------------------------
const $ = (id) => document.getElementById(id);

function show(id) { $(id).classList.remove("hidden"); }
function hide(id) { $(id).classList.add("hidden"); }

function setStatus(text, cls) {
  const badge = $("status-badge");
  badge.textContent = text;
  badge.className = `badge ${cls}`;
  show("status-badge");
}

function appendLog(line) {
  const pane = $("log-pane");
  const entry = document.createElement("div");
  entry.textContent = line;
  pane.appendChild(entry);
  pane.scrollTop = pane.scrollHeight;
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let currentJobId = null;
let ws = null;

// ---------------------------------------------------------------------------
// Job submission
// ---------------------------------------------------------------------------
async function submitJob(repoUrl) {
  try {
    const resp = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repo_url: repoUrl }),
    });
    if (!resp.ok) throw new Error(await resp.text());
    const data = await resp.json();
    currentJobId = data.job_id;
    setStatus("running", "running");
    show("log-section");
    connectLogs();
    pollJobStatus();
  } catch (err) {
    setStatus("error", "error");
    appendLog(`Error: ${err.message}`);
  }
}

// ---------------------------------------------------------------------------
// WebSocket log streaming
// ---------------------------------------------------------------------------
function connectLogs() {
  if (ws) ws.close();
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/ws/logs`);
  ws.onmessage = (e) => appendLog(e.data);
  ws.onerror = () => appendLog("[ws] Connection error");
  ws.onclose = () => appendLog("[ws] Disconnected");
}

// ---------------------------------------------------------------------------
// Poll job status until done / blocked
// ---------------------------------------------------------------------------
function pollJobStatus() {
  const interval = setInterval(async () => {
    const job = await loadCurrentJob();
    if (!job || job.status === "idle") return;
    setStatus(job.status, job.status);
    if (job.status === "done" || job.status === "blocked") {
      clearInterval(interval);
      if (job.id) loadDocuments(job.id);
      show("chat-section");
    }
  }, 3000);
}

// ---------------------------------------------------------------------------
// Load current job on page reload
// ---------------------------------------------------------------------------
async function loadCurrentJob() {
  try {
    const resp = await fetch("/api/jobs/current");
    if (!resp.ok) return null;
    const job = await resp.json();
    if (job.status === "idle") return job;
    currentJobId = job.id;
    setStatus(job.status, job.status);
    show("log-section");
    if (job.status === "running") {
      connectLogs();
      pollJobStatus();
    }
    if (job.id) loadDocuments(job.id);
    if (job.status === "done" || job.status === "blocked") show("chat-section");
    return job;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Documents
// ---------------------------------------------------------------------------
const DOC_LABELS = {
  user_guide: "End User Guide",
  dev_guide:  "Developer Guide",
};

async function loadDocuments(jobId) {
  try {
    const resp = await fetch(`/api/jobs/${jobId}/documents`);
    if (!resp.ok) return;
    const docs = await resp.json();
    const container = $("doc-links");
    container.innerHTML = "";
    if (!docs.length) {
      hide("docs-section");
      return;
    }
    docs.forEach((doc) => {
      const filename = doc.file_path.split("/").pop();
      const label = DOC_LABELS[doc.doc_type] ?? doc.doc_type;

      const row = document.createElement("div");
      row.className = "doc-row";
      row.dataset.docType = doc.doc_type;

      const link = document.createElement("a");
      link.href = `/api/documents/${encodeURIComponent(filename)}`;
      link.textContent = `📄 ${label}`;
      link.target = "_blank";
      link.className = "doc-link";

      const delBtn = document.createElement("button");
      delBtn.textContent = "Delete";
      delBtn.className = "doc-delete-btn";
      delBtn.setAttribute("aria-label", `Delete ${label}`);
      delBtn.addEventListener("click", () => deleteDocument(jobId, doc.doc_type, row));

      row.appendChild(link);
      row.appendChild(delBtn);
      container.appendChild(row);
    });
    show("docs-section");
  } catch (err) {
    console.error("loadDocuments error:", err);
  }
}

async function deleteDocument(jobId, docType, rowEl) {
  const label = DOC_LABELS[docType] ?? docType;
  if (!confirm(`Delete the ${label} PDF? This cannot be undone.`)) return;

  try {
    const resp = await fetch(`/api/jobs/${jobId}/documents/${encodeURIComponent(docType)}`, {
      method: "DELETE",
    });
    if (!resp.ok) {
      const msg = await resp.text();
      throw new Error(msg);
    }
    rowEl.remove();
    // Hide the section if there are no rows left
    if ($("doc-links").children.length === 0) {
      hide("docs-section");
    }
  } catch (err) {
    alert(`Could not delete document: ${err.message}`);
  }
}

// ---------------------------------------------------------------------------
// Chat
// ---------------------------------------------------------------------------
async function sendChat(message) {
  if (!currentJobId) {
    appendChatMessage("system", "No active job. Please analyze a repository first.");
    return;
  }
  appendChatMessage("user", message);
  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: currentJobId, message }),
    });
    if (!resp.ok) throw new Error(await resp.text());
    const data = await resp.json();
    appendChatMessage("agent", data.response);
  } catch (err) {
    appendChatMessage("system", `Error: ${err.message}`);
  }
}

function appendChatMessage(role, text) {
  const history = $("chat-history");
  const msg = document.createElement("div");
  msg.className = `chat-msg ${role}`;
  msg.textContent = text;
  history.appendChild(msg);
  history.scrollTop = history.scrollHeight;
}

// ---------------------------------------------------------------------------
// Event listeners
// ---------------------------------------------------------------------------
$("job-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const url = $("repo-url").value.trim();
  if (!url) return;
  $("log-pane").innerHTML = "";
  submitJob(url);
});

$("chat-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = $("chat-input");
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  sendChat(message);
});

// ---------------------------------------------------------------------------
// Init: restore state on page load
// ---------------------------------------------------------------------------
loadCurrentJob();
