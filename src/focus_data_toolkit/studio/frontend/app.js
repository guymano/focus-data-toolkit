"use strict";
// Focus Data Toolkit Studio — minimal no-build client. The access token comes from the URL the
// user opened (?token=…) and is sent on every API call; the full file is never loaded here.

const TOKEN = new URLSearchParams(location.search).get("token") || "";
const $ = (id) => document.getElementById(id);
const state = { source: null, jobId: null, cwd: "", config: null };

function withToken(url) {
  return url + (url.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(TOKEN);
}
async function api(path, { method = "GET", json, form } = {}) {
  const opts = { method, headers: { "X-FDT-Token": TOKEN } };
  if (json !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(json); }
  if (form !== undefined) { opts.body = form; }
  const res = await fetch(path, opts);
  const data = res.headers.get("content-type")?.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) throw new Error((data && data.error) || ("HTTP " + res.status));
  return data;
}

function setSource(source, label) {
  state.source = source;
  const el = $("sourceLabel");
  el.textContent = "source: " + label;
  el.classList.remove("hidden");
  $("detectBtn").disabled = false;
  $("convertBtn").disabled = false;
}

// --- tabs ---
$("tabs").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-tab]");
  if (!btn) return;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b === btn));
  ["server", "upload", "generate"].forEach((t) => $("tab-" + t).classList.toggle("hidden", t !== btn.dataset.tab));
});

// --- server file browser ---
async function browse(subpath) {
  const data = await api("/api/files?subpath=" + encodeURIComponent(subpath || ""));
  state.cwd = data.path || "";
  const crumbs = $("crumbs");
  const parts = state.cwd ? state.cwd.split("/") : [];
  crumbs.innerHTML = `<a data-p="">root</a>` + parts.map((p, i) =>
    ` / <a data-p="${parts.slice(0, i + 1).join("/")}">${escapeHtml(p)}</a>`).join("");
  crumbs.querySelectorAll("a").forEach((a) => a.onclick = () => browse(a.dataset.p));
  const ul = $("browser");
  ul.innerHTML = "";
  for (const entry of data.entries) {
    const li = document.createElement("li");
    const rel = (state.cwd ? state.cwd + "/" : "") + entry.name;
    li.className = entry.is_dir ? "dir" : "file";
    li.innerHTML = `<span>${entry.is_dir ? "📁 " : "📄 "}${escapeHtml(entry.name)}</span>` +
      `<span class="muted">${entry.is_dir ? "" : fmtSize(entry.size)}</span>`;
    li.onclick = () => entry.is_dir ? browse(rel) : setSource({ path: rel }, rel);
    ul.appendChild(li);
  }
}

// --- upload ---
$("uploadBtn").onclick = async () => {
  const f = $("uploadFile").files[0];
  if (!f) return;
  const form = new FormData();
  form.append("file", f);
  try {
    const r = await api("/api/upload", { method: "POST", form });
    setSource({ source_id: r.source_id, source_name: r.source_name }, r.source_name + " (uploaded)");
  } catch (e) { alert(e.message); }
};

// --- generate ---
$("genBtn").onclick = async () => {
  try {
    const r = await api("/api/generate", { method: "POST", json: {
      provider: $("genProvider").value, focus_version: $("genVersion").value,
      rows: Number($("genRows").value), seed: Number($("genSeed").value),
    }});
    setSource({ source_id: r.source_id, source_name: r.source_name },
      r.source_name + ` (generated, ${r.rows} rows)`);
  } catch (e) { alert(e.message); }
};

// --- detect ---
$("detectBtn").onclick = async () => {
  try {
    const r = await api("/api/detect", { method: "POST", json: state.source });
    const out = $("detectOut");
    out.textContent = `${r.dataset || "?"} · FOCUS ${r.detected_version || "?"} · confidence ${r.confidence} (score ${r.score})`
      + (r.missing_columns?.length ? `\nmissing: ${r.missing_columns.join(", ")}` : "")
      + (r.unknown_columns?.length ? `\nunknown: ${r.unknown_columns.join(", ")}` : "");
    out.classList.remove("hidden");
  } catch (e) { alert(e.message); }
};

// --- convert + progress (SSE) ---
$("convertBtn").onclick = async () => {
  try {
    const body = Object.assign({}, state.source, {
      mode: $("mode").value, output_format: $("format").value, on_exists: $("onExists").value,
    });
    const r = await api("/api/jobs", { method: "POST", json: body });
    state.jobId = r.job_id;
    $("resultCard").classList.add("hidden");
    $("progressCard").classList.remove("hidden");
    $("barFill").style.width = "0";
    $("progressText").textContent = "starting…";
    streamProgress(r.job_id);
  } catch (e) { alert(e.message); }
};

function streamProgress(jobId) {
  const es = new EventSource(withToken(`/api/jobs/${jobId}/events`));
  es.onmessage = (ev) => {
    const p = JSON.parse(ev.data);
    const pct = p.fraction != null ? Math.round(p.fraction * 100) : null;
    $("barFill").style.width = (pct != null ? pct : 8) + "%";
    const total = p.total != null ? "/" + p.total.toLocaleString() : "";
    $("progressText").textContent =
      `${p.phase} · ${p.completed.toLocaleString()}${total} ${p.unit}` +
      (pct != null ? ` (${pct}%)` : "") + (p.message ? ` — ${p.message}` : "");
  };
  es.addEventListener("done", (ev) => {
    es.close();
    $("barFill").style.width = "100%";
    loadResult(jobId);
  });
  es.onerror = () => { es.close(); loadResult(jobId); };
}

$("cancelBtn").onclick = async () => {
  if (state.jobId) { try { await api(`/api/jobs/${state.jobId}/cancel`, { method: "POST" }); } catch (e) {} }
};

// --- results ---
async function loadResult(jobId) {
  const r = await api(`/api/jobs/${jobId}/result`);
  $("progressCard").classList.add("hidden");
  $("resultCard").classList.remove("hidden");
  const cls = r.status === "succeeded" ? "status-ok" : r.status === "cancelled" ? "status-warn" : "status-err";
  $("resultStatus").innerHTML = `<span class="${cls}">${r.status}</span>` +
    (r.error ? ` — ${escapeHtml(r.error)}` : "") +
    (r.assumptions_present ? ` · <span class="status-warn">contains ASSUMED (synthetic) values</span>` : "");

  const dt = $("datasets");
  if (r.datasets) {
    dt.innerHTML = "<tr><th>Dataset</th><th>Status</th><th>Conformance</th><th>Rows</th></tr>" +
      Object.entries(r.datasets).map(([n, e]) =>
        `<tr><td>${escapeHtml(n)}</td><td>${escapeHtml(e.status)}</td><td>${escapeHtml(e.conformance || "")}</td><td>${e.row_count ?? ""}</td></tr>`).join("");
  } else { dt.innerHTML = ""; }

  const dl = $("downloads");
  dl.innerHTML = "";
  const links = [];
  const dataFiles = (r.files || []).filter((f) => !f.is_dir);
  if (dataFiles.some((f) => f.name === "focus_1_4_manifest.json")) links.push(["manifest", `/api/jobs/${jobId}/manifest`]);
  if (dataFiles.some((f) => f.name === "SHA256SUMS")) links.push(["checksums", `/api/jobs/${jobId}/checksums`]);
  links.push(["diagnostics.json", `/api/jobs/${jobId}/diagnostics?format=json`]);
  links.push(["diagnostics.csv", `/api/jobs/${jobId}/diagnostics?format=csv`]);
  links.push(["summary.html", `/api/jobs/${jobId}/summary.html`]);
  for (const f of dataFiles) links.push([f.name, `/api/jobs/${jobId}/download?file=${encodeURIComponent(f.name)}`]);
  for (const [label, url] of links) {
    const a = document.createElement("a");
    a.href = withToken(url); a.textContent = "⬇ " + label; a.target = "_blank"; a.rel = "noopener";
    dl.appendChild(a);
  }

  const sel = $("previewFile");
  sel.innerHTML = "";
  for (const f of (r.files || [])) {
    if (/\.(csv|parquet)$/.test(f.name) || f.is_dir) {
      const o = document.createElement("option"); o.value = f.name; o.textContent = f.name; sel.appendChild(o);
    }
  }
}

$("previewBtn").onclick = async () => {
  const file = $("previewFile").value;
  if (!state.jobId || !file) return;
  try {
    const p = await api(`/api/jobs/${state.jobId}/preview?file=${encodeURIComponent(file)}&limit=50`);
    const cols = p.columns || [];
    const head = "<tr>" + cols.map((c) => `<th>${escapeHtml(c)}</th>`).join("") + "</tr>";
    const rows = (p.rows || []).map((row) =>
      "<tr>" + cols.map((c) => `<td>${escapeHtml(String(row[c] ?? ""))}</td>`).join("") + "</tr>").join("");
    $("previewTable").innerHTML = `<table>${head}${rows}</table>` +
      `<p class="muted">rows ${p.offset}–${p.offset + (p.rows || []).length} (sampled)</p>`;
  } catch (e) { alert(e.message); }
};

// --- helpers ---
function escapeHtml(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function fmtSize(n) { if (n == null) return ""; const u = ["B", "KB", "MB", "GB"]; let i = 0; while (n >= 1000 && i < u.length - 1) { n /= 1000; i++; } return n.toFixed(i ? 1 : 0) + " " + u[i]; }

// --- init ---
(async function init() {
  if (!TOKEN) { $("tokenWarn").classList.remove("hidden"); return; }
  try {
    const cfg = await api("/api/config");
    state.config = cfg;
    $("rootLabel").textContent = cfg.root;
    $("uploadCap").textContent = fmtSize(cfg.max_upload_bytes);
    $("genNote").textContent = `max ${cfg.max_generate_rows.toLocaleString()} rows in the Studio; use the CLI/Runner for larger sets.`;
    fill($("genProvider"), cfg.providers);
    fill($("genVersion"), cfg.focus_versions);
    fill($("format"), cfg.output_formats);
    await browse("");
  } catch (e) {
    $("tokenWarn").classList.remove("hidden");
    $("tokenWarn").innerHTML = "<strong>Cannot reach the Studio API.</strong> " + escapeHtml(e.message);
  }
})();
function fill(sel, values) { sel.innerHTML = ""; for (const v of values) { const o = document.createElement("option"); o.value = v; o.textContent = v; sel.appendChild(o); } }
