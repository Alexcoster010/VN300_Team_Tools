"use strict";

const state = {
  settings: {},
  history: [],
  job: null,
  selectedEntry: null,
  selectedResult: null,
  priorJobStatus: null,
  pollTimer: null,
  toastTimer: null,
};

const $ = (id) => document.getElementById(id);
const terminalStatuses = new Set(["completed", "failed", "cancelled"]);

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  let data = {};
  try { data = await response.json(); } catch (_) { /* empty error response */ }
  if (!response.ok) throw new Error(data.error || `${response.status} ${response.statusText}`);
  return data;
}

function showToast(message, isError = false) {
  const toast = $("toast");
  toast.textContent = message;
  toast.className = `toast show${isError ? " error" : ""}`;
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => { toast.className = "toast"; }, 3500);
}

function setView(name) {
  const titles = {
    analysis: ["OFFLINE WORKSPACE", "Data analysis"],
    dashboard: ["LIVE TELEMETRY", "Pi dashboard"],
    results: ["GENERATED OUTPUT", "Analysis results"],
  };
  document.querySelectorAll(".nav-button").forEach((button) => button.classList.toggle("active", button.dataset.view === name));
  document.querySelectorAll(".view").forEach((view) => view.classList.remove("active"));
  $(`${name}View`).classList.add("active");
  $("viewEyebrow").textContent = titles[name][0];
  $("viewTitle").textContent = titles[name][1];
  if (name === "dashboard" && !$("piFrame").hasAttribute("src") && $("piUrl").value.trim()) checkPi(true);
}

function populateSettings(settings) {
  state.settings = settings;
  $("inputPath").value = settings.input_path || "";
  $("outputRoot").value = settings.output_root || "";
  $("piUrl").value = settings.pi_url || "http://raspberrypi.local:8080/";
  $("driverOrder").value = settings.driver_order || "";
  $("driverOffset").value = settings.driver_order_offset ?? 0;
  $("autoSectors").value = settings.auto_sectors ?? 3;
  $("sectorMinimum").value = settings.sector_report_min_seconds ?? 20;
  $("ggEnabled").checked = settings.gg_enabled !== false;
  $("includeAscii").checked = Boolean(settings.include_ascii);
  const mode = document.querySelector(`input[name="mode"][value="${settings.mode || "auto"}"]`);
  if (mode) mode.checked = true;
}

function formPayload() {
  return {
    input_path: $("inputPath").value.trim(),
    output_root: $("outputRoot").value.trim(),
    mode: document.querySelector('input[name="mode"]:checked').value,
    driver_order: $("driverOrder").value.trim(),
    driver_order_offset: Number($("driverOffset").value),
    auto_sectors: Number($("autoSectors").value),
    sector_report_min_seconds: Number($("sectorMinimum").value),
    gg_enabled: $("ggEnabled").checked,
    include_ascii: $("includeAscii").checked,
  };
}

function fileName(path) {
  if (!path) return "";
  const pieces = path.replaceAll("\\", "/").split("/").filter(Boolean);
  return pieces.at(-1) || path;
}

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function elapsedTime(value) {
  if (!value) return "";
  const seconds = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

function resultUrl(entry, result) {
  return `/results/${encodeURIComponent(entry.id)}/${encodeURIComponent(result.name)}`;
}

function renderJob(job) {
  state.job = job;
  const badge = $("jobBadge");
  const active = job && ["queued", "running", "cancelling"].includes(job.status);
  badge.className = `job-badge ${active ? "running" : (job?.status || "idle")}`;
  badge.querySelector("span:last-child").textContent = !job ? "No active analysis" :
    active ? `${job.status[0].toUpperCase()}${job.status.slice(1)} analysis` : `Last run ${job.status}`;

  $("runEmpty").classList.toggle("hidden", Boolean(job));
  $("runActive").classList.toggle("hidden", !job);
  $("cancelRun").classList.toggle("hidden", !active);
  $("startRun").disabled = active;
  $("startRun").textContent = active ? "Analysis running" : "Run analysis";
  if (!job) return;

  $("runStatus").textContent = `${job.status[0].toUpperCase()}${job.status.slice(1)}`;
  $("runDot").className = `status-dot ${active ? "running" : job.status}`;
  $("runTime").textContent = terminalStatuses.has(job.status) ? formatTime(job.completed_at) : elapsedTime(job.created_at);
  $("runInput").textContent = job.input_path;
  $("runInput").title = job.input_path;
  const log = $("runLog");
  const wasNearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 35;
  log.textContent = (job.log || []).join("\n") || "Starting analyzer...";
  if (wasNearBottom) log.scrollTop = log.scrollHeight;
  $("quickResults").replaceChildren(...(job.results || []).slice(0, 3).map((result) => {
    const link = document.createElement("a");
    link.className = "result-link";
    link.href = resultUrl(job, result);
    link.target = "_blank";
    link.rel = "noopener";
    link.append(document.createTextNode(result.label));
    const type = document.createElement("span");
    type.textContent = result.kind;
    link.append(type);
    link.addEventListener("click", (event) => {
      if (result.kind !== "html") return;
      event.preventDefault();
      selectResultEntry(job, result.name);
      setView("results");
    });
    return link;
  }));
}

async function refreshJob() {
  try {
    const data = await api("/api/job");
    const previous = state.job?.status;
    renderJob(data.job);
    if (data.job && terminalStatuses.has(data.job.status) && previous && !terminalStatuses.has(previous)) {
      await loadBootstrap(false);
      showToast(data.job.status === "completed" ? "Analysis completed." : `Analysis ${data.job.status}.`, data.job.status === "failed");
    }
  } catch (error) {
    showToast(error.message, true);
  }
}

function historyRunLabel(entry) {
  const input = fileName(entry.input_path);
  return input || `Run ${entry.id}`;
}

function renderHistory() {
  const list = $("historyList");
  list.replaceChildren();
  if (!state.history.length) {
    const empty = document.createElement("div");
    empty.className = "history-empty";
    empty.textContent = "No completed analyses";
    list.append(empty);
    return;
  }
  state.history.forEach((entry) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `history-item ${entry.status}${state.selectedEntry?.id === entry.id ? " active" : ""}`;
    const head = document.createElement("div");
    head.className = "history-item-head";
    const title = document.createElement("strong");
    title.textContent = entry.mode === "auto" ? "Auto timing" : entry.mode;
    const status = document.createElement("span");
    status.textContent = entry.status;
    head.append(title, status);
    const when = document.createElement("time");
    when.textContent = formatTime(entry.completed_at || entry.created_at);
    const source = document.createElement("span");
    source.textContent = historyRunLabel(entry);
    source.title = entry.input_path;
    button.append(head, when, source);
    button.addEventListener("click", () => selectResultEntry(entry));
    list.append(button);
  });
}

function selectResultEntry(entry, preferredName = "") {
  state.selectedEntry = entry;
  const results = entry.results || [];
  const preferred = results.find((item) => item.name === preferredName);
  const primary = preferred || results.find((item) => item.name === "report.html") || results.find((item) => item.kind === "html") || results[0];
  renderHistory();
  $("resultToolbar").classList.toggle("hidden", !primary);
  $("resultFiles").classList.toggle("hidden", !results.length);
  const files = results.map((result) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `result-file-button${primary?.name === result.name ? " active" : ""}`;
    button.textContent = result.label;
    button.addEventListener("click", () => openResult(entry, result));
    return button;
  });
  $("resultFiles").replaceChildren(...files);
  if (primary) openResult(entry, primary);
  else {
    state.selectedResult = null;
    $("resultFrame").removeAttribute("src");
    $("resultEmpty").classList.remove("hidden");
  }
}

function openResult(entry, result) {
  state.selectedEntry = entry;
  state.selectedResult = result;
  const url = resultUrl(entry, result);
  $("resultTitle").textContent = result.label;
  $("resultMeta").textContent = `${historyRunLabel(entry)} | ${formatTime(entry.completed_at || entry.created_at)}`;
  $("resultToolbar").classList.remove("hidden");
  $("resultFiles").querySelectorAll("button").forEach((button) => button.classList.toggle("active", button.textContent === result.label));
  if (result.kind === "html" || result.kind === "csv") {
    $("resultFrame").src = url;
    $("resultEmpty").classList.add("hidden");
  }
}

async function loadBootstrap(selectLatest = true) {
  const data = await api("/api/bootstrap");
  populateSettings(data.settings);
  state.history = data.history || [];
  renderJob(data.job);
  renderHistory();
  if (selectLatest && !state.selectedEntry && state.history.length) selectResultEntry(state.history[0]);
  else if (state.selectedEntry) {
    const updated = state.history.find((entry) => entry.id === state.selectedEntry.id);
    if (updated) selectResultEntry(updated, state.selectedResult?.name || "");
  }
}

async function selectFolder(inputId) {
  const input = $(inputId);
  const button = document.querySelector(`[data-browse="${inputId}"]`);
  button.disabled = true;
  try {
    const data = await api("/api/select-folder", { method: "POST", body: JSON.stringify({ initial: input.value }) });
    if (data.path) input.value = data.path;
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function startAnalysis(event) {
  event.preventDefault();
  $("formError").classList.add("hidden");
  try {
    const data = await api("/api/analysis/start", { method: "POST", body: JSON.stringify(formPayload()) });
    renderJob(data.job);
    showToast("Analysis started.");
  } catch (error) {
    $("formError").textContent = error.message;
    $("formError").classList.remove("hidden");
  }
}

async function cancelAnalysis() {
  try {
    const data = await api("/api/analysis/cancel", { method: "POST", body: "{}" });
    renderJob(data.job);
  } catch (error) {
    showToast(error.message, true);
  }
}

function setPiState(kind, text) {
  $("piConnection").className = `connection-state ${kind}`;
  $("piConnection").querySelector("span:last-child").textContent = text;
  $("sidebarPiDot").className = `status-dot ${kind === "online" ? "online" : (kind === "offline" ? "failed" : "neutral")}`;
  $("sidebarPiText").textContent = text;
}

async function checkPi(loadFrame = true) {
  const value = $("piUrl").value.trim();
  if (!value) return;
  setPiState("neutral", "Checking...");
  try {
    const saved = await api("/api/settings", { method: "POST", body: JSON.stringify({ pi_url: value }) });
    $("piUrl").value = saved.settings.pi_url;
    const data = await api(`/api/pi/check?url=${encodeURIComponent(saved.settings.pi_url)}`);
    setPiState("online", data.logging ? "Pi logging" : "Pi online");
    if (loadFrame) {
      $("piFrame").src = saved.settings.pi_url;
      $("piFrameEmpty").classList.add("hidden");
    }
  } catch (error) {
    setPiState("offline", "Pi offline");
    showToast(`Pi dashboard unavailable: ${error.message}`, true);
  }
}

document.querySelectorAll(".nav-button").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
document.querySelectorAll("[data-browse]").forEach((button) => button.addEventListener("click", () => selectFolder(button.dataset.browse)));
$("analysisForm").addEventListener("submit", startAnalysis);
$("cancelRun").addEventListener("click", cancelAnalysis);
$("checkPi").addEventListener("click", () => checkPi(true));
$("reloadPi").addEventListener("click", () => {
  const frame = $("piFrame");
  if (frame.src) frame.src = frame.src;
  else checkPi(true);
});
$("openPi").addEventListener("click", async () => {
  const url = $("piUrl").value.trim();
  if (url) window.open(url.includes("://") ? url : `http://${url}`, "_blank", "noopener");
});
$("openResultTab").addEventListener("click", () => {
  if (state.selectedEntry && state.selectedResult) window.open(resultUrl(state.selectedEntry, state.selectedResult), "_blank", "noopener");
});
$("openOutput").addEventListener("click", async () => {
  if (!state.selectedEntry) return;
  try {
    await api("/api/open-output", { method: "POST", body: JSON.stringify({ job_id: state.selectedEntry.id }) });
  } catch (error) {
    showToast(error.message, true);
  }
});

const requestedView = new URLSearchParams(window.location.search).get("view");
if (["analysis", "dashboard", "results"].includes(requestedView)) setView(requestedView);
loadBootstrap().then(() => {
  if (requestedView === "dashboard") checkPi(true);
}).catch((error) => showToast(error.message, true));
state.pollTimer = setInterval(refreshJob, 1000);
