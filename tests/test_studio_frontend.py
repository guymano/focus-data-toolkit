"""Focus Data Toolkit Studio front end (frontend/app.js): no stale progress, result or detection.

The Studio follows one conversion at a time and shows it under the source it converted. A
small Node harness loads app.js with stub DOM, fetch and EventSource objects and replays the
races a browser can hit:

* a second conversion starts while the first runs: the first one's late events and result
  must not replace the second one's view;
* another source is chosen while a conversion runs: its result must name its own source;
* a detection answers after the source changed: neither its result nor its error is shown.

Node ships with the CI runners, so the test never skips there; elsewhere it skips when Node
is not installed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from importlib.resources import as_file, files
from pathlib import Path

import pytest

HARNESS = r"""
"use strict";
const fs = require("fs");
const vm = require("vm");

class ClassList {
  constructor() { this.names = new Set(); }
  add(n) { this.names.add(n); }
  remove(n) { this.names.delete(n); }
  contains(n) { return this.names.has(n); }
  toggle(n, force) {
    if (force === undefined ? !this.names.has(n) : force) this.names.add(n); else this.names.delete(n);
  }
}
class Element {
  constructor(tag) {
    this.tag = tag; this.textContent = ""; this.value = ""; this.disabled = false;
    this.classList = new ClassList(); this.style = {}; this.dataset = {}; this.files = [];
    this.children = []; this.html = ""; this.listeners = {};
  }
  get innerHTML() { return this.html; }
  set innerHTML(html) { this.html = html; this.children = []; }
  get options() { return this.children.filter((c) => c.tag === "option"); }
  appendChild(child) { this.children.push(child); return child; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  querySelectorAll() { return []; }
}
const elements = new Map();
const document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, new Element("div"));
    return elements.get(id);
  },
  createElement(tag) { return new Element(tag); },
  querySelectorAll() { return []; },
};

let jobs = 0;
const held = [];
const holding = new Set();
function response(data, ok) {
  return {
    ok, status: ok ? 200 : 400, headers: { get: () => "application/json" },
    json: async () => data, text: async () => JSON.stringify(data),
  };
}
function route(path, opts) {
  if (path === "/api/config") {
    return { root: "/r", max_upload_bytes: 1, max_generate_rows: 10, providers: ["aws"],
             focus_versions: ["1.3"], output_formats: ["csv"] };
  }
  if (path.startsWith("/api/files")) return { path: "", entries: [] };
  if (path === "/api/jobs") return { job_id: "job" + (++jobs) };
  const result = path.match(/^\/api\/jobs\/([^/]+)\/result$/);
  if (result) {
    return { status: "succeeded", files: [{ name: result[1] + ".csv", is_dir: false }],
             datasets: { "Cost and Usage": { status: "PRODUCED", row_count: 1 } } };
  }
  if (path === "/api/detect") {
    const source = JSON.parse(opts.body).path;
    if (source === "bad.csv") return { error: "unreadable source" };
    return { dataset: "Cost and Usage", detected_version: source, confidence: "HIGH", score: 1 };
  }
  throw new Error("unexpected request " + path);
}
async function fetch(path, opts = {}) {
  const data = route(path, opts);
  const answer = () => response(data, !data.error);
  if ([...holding].some((prefix) => path.startsWith(prefix))) {
    return new Promise((resolve) => held.push(() => resolve(answer())));
  }
  return answer();
}
const streams = [];
class EventSource {
  constructor(url) {
    this.url = url; this.closed = false; this.listeners = {}; this.onmessage = null;
    this.onerror = null; streams.push(this);
  }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  close() { this.closed = true; }
  // Deliver an event even after close(), as a callback already queued would be.
  emit(type, data) {
    const event = { data: JSON.stringify(data || {}) };
    if (type === "message") { if (this.onmessage) this.onmessage(event); }
    else if (type === "error") { if (this.onerror) this.onerror(event); }
    else (this.listeners[type] || []).forEach((fn) => fn(event));
  }
}
const alerts = [];
const context = vm.createContext({
  document, fetch, EventSource, URLSearchParams, location: { search: "?token=t" },
  alert: (message) => alerts.push(message), console, setTimeout,
});
for (const id of ["resultCard", "progressCard", "detectOut"]) {
  document.getElementById(id).classList.add("hidden");
}
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context, { filename: "app.js" });

const $ = (id) => document.getElementById(id);
const choose = (path) => vm.runInContext(`setSource({ path: "${path}" }, "${path}")`, context);
const visible = (id) => !$(id).classList.contains("hidden");
const settle = async () => {
  for (let i = 0; i < 20; i++) await new Promise((resolve) => setImmediate(resolve));
};
const failures = [];
const check = (condition, message) => { if (!condition) failures.push(message); };

(async () => {
  await settle();

  // 1. A second conversion while the first runs.
  choose("a.csv");
  await $("convertBtn").onclick(); await settle();
  const first = streams[streams.length - 1];
  await $("convertBtn").onclick(); await settle();
  const second = streams[streams.length - 1];
  check(first.closed, "the first conversion's stream is closed when a second one starts");
  first.emit("message", { phase: "WRITING", completed: 9, total: 9, unit: "rows", fraction: 1 });
  first.emit("done");
  first.emit("error");
  await settle();
  check(!visible("resultCard"), "a superseded conversion's result is not shown");
  check(visible("progressCard"), "the followed conversion's progress stays visible");
  check($("progressText").textContent === "starting…", "a superseded conversion's progress is ignored");
  second.emit("done"); await settle();
  check(visible("resultCard"), "the followed conversion's result is shown");
  check($("previewFile").options.map((o) => o.value).join() === "job2.csv",
        "the preview lists the followed conversion's files");
  check($("resultSource").textContent === "source: a.csv", "the result names its source");

  // 2. Another source chosen while a conversion runs.
  choose("b.csv");
  check(!visible("resultCard"), "choosing another source hides the previous result");
  await $("convertBtn").onclick(); await settle();
  const third = streams[streams.length - 1];
  choose("c.csv");
  check($("progressSource").textContent === "source: b.csv", "a running conversion names its source");
  third.emit("done"); await settle();
  check(visible("resultCard"), "the running conversion's result is shown when it ends");
  check($("resultSource").textContent === "source: b.csv",
        "a result is shown under the source it converted, not the chosen one");

  // 3. A detection answered after the source changed, success and failure.
  holding.add("/api/detect");
  choose("d.csv");
  const detecting = $("detectBtn").onclick(); await settle();
  choose("e.csv");
  held.shift()(); await detecting; await settle();
  check(!visible("detectOut") && $("detectOut").textContent === "",
        "a previous source's detection is not shown");
  choose("bad.csv");
  const failing = $("detectBtn").onclick(); await settle();
  choose("f.csv");
  held.shift()(); await failing; await settle();
  check(alerts.length === 0, "a previous source's detection error is not shown");

  if (failures.length) {
    console.error(failures.join("\n"));
    process.exit(1);
  }
  console.log("ok");
})().catch((error) => { console.error(error); process.exit(1); });
"""


def test_studio_front_end_ignores_stale_jobs_and_sources(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            pytest.fail("Node is required to test the Studio front end on CI")
        pytest.skip("Node is not installed")
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    resource = files("focus_data_toolkit.studio").joinpath("frontend", "app.js")
    with as_file(resource) as app_js:
        result = subprocess.run(
            [node, str(harness), str(app_js)],
            capture_output=True, text=True, encoding="utf-8", timeout=120, check=False,
        )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "ok"
