"""Focus Data Toolkit Studio (Lot C): security, job flow, cross-interface parity.

Driven entirely through the FastAPI TestClient (no browser, no live server). ``base_url`` is set
to the configured loopback authority so the Host guard passes exactly as a real browser would.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from focus_data_toolkit.convert import convert_files  # noqa: E402
from focus_data_toolkit.generators import get_generator  # noqa: E402
from focus_data_toolkit.studio import server  # noqa: E402
from focus_data_toolkit.studio.app import create_app  # noqa: E402
from focus_data_toolkit.studio.config import StudioConfig  # noqa: E402
from focus_data_toolkit.studio.jobs import JobManager  # noqa: E402
from focus_data_toolkit.studio.security import PathOutsideRoot, resolve_within_root  # noqa: E402

BASE = "http://127.0.0.1:8765"


def _client(tmp_path: Path, **overrides) -> tuple[TestClient, StudioConfig]:
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    (root / "cau.csv").write_bytes(get_generator("aws", "1.3").generate_csv_bytes(80, 5))
    config = StudioConfig(
        host="127.0.0.1", port=8765, root=root, work_dir=tmp_path / "work", **overrides
    )
    return TestClient(create_app(config), base_url=BASE), config


def _auth(config: StudioConfig) -> dict:
    return {"X-FDT-Token": config.token}


def _post_headers(config: StudioConfig) -> dict:
    return {"X-FDT-Token": config.token, "Origin": BASE}


def _run_job(client: TestClient, config: StudioConfig, **body) -> dict:
    resp = client.post("/api/jobs", headers=_post_headers(config), json=body)
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    for _ in range(200):
        status = client.get(f"/api/jobs/{job_id}", headers=_auth(config)).json()
        if status["status"] in ("succeeded", "failed", "cancelled"):
            break
        time.sleep(0.05)
    status["job_id"] = job_id
    return status


# --- security ---------------------------------------------------------------------------


def test_token_required(tmp_path):
    client, _config = _client(tmp_path)
    assert client.get("/api/health").status_code == 401


def test_health_with_token(tmp_path):
    client, config = _client(tmp_path)
    resp = client.get("/api/health", headers=_auth(config))
    assert resp.status_code == 200 and resp.json()["version"]


def test_bad_host_header_rejected(tmp_path):
    client, config = _client(tmp_path)
    resp = client.get("/api/health", headers={**_auth(config), "Host": "evil.example.com"})
    assert resp.status_code == 400  # anti DNS-rebinding


def test_post_requires_origin(tmp_path):
    client, config = _client(tmp_path)
    # token present but no Origin -> CSRF guard rejects the state-changing request
    assert client.post("/api/detect", headers=_auth(config), json={"path": "cau.csv"}).status_code == 403
    ok = client.post("/api/detect", headers=_post_headers(config), json={"path": "cau.csv"})
    assert ok.status_code == 200


def test_path_traversal_rejected(tmp_path):
    client, config = _client(tmp_path)
    assert client.get("/api/files?subpath=../..", headers=_auth(config)).status_code == 400


def test_resolve_within_root_unit(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    (root / "a.csv").write_text("x")
    assert resolve_within_root("a.csv", root) == (root / "a.csv").resolve()
    with pytest.raises(PathOutsideRoot):
        resolve_within_root("../secret", root)
    with pytest.raises(PathOutsideRoot):
        resolve_within_root("/etc/passwd", root)  # absolute inputs are refused outright


def test_source_file_confined_to_sources_dir(tmp_path):
    jm = JobManager(tmp_path / "jm", max_concurrency=1)
    source_id, _dir = jm.new_source_dir()
    # a well-formed (id, name) stays under the sources dir...
    resolved = jm.source_file(source_id, "cau.csv")
    assert resolved.name == "cau.csv"
    # ...and neither component can be used to climb out of it.
    with pytest.raises(PathOutsideRoot):
        jm.source_file(source_id, "../../etc/passwd")
    with pytest.raises(PathOutsideRoot):
        jm.source_file("../..", "passwd")


def test_run_refuses_non_loopback_without_allow_remote(capsys):
    rc = server.run(host="0.0.0.0", allow_remote=False, open_browser=False)
    assert rc == 2
    assert "allow-remote" in capsys.readouterr().err


def test_upload_cap_enforced(tmp_path):
    client, config = _client(tmp_path, max_upload_bytes=100)
    big = b"x" * 500
    resp = client.post(
        "/api/upload", headers=_post_headers(config), files={"file": ("big.csv", big, "text/csv")}
    )
    assert resp.status_code == 413


# --- detection + conversion flow --------------------------------------------------------


def test_detect(tmp_path):
    client, config = _client(tmp_path)
    resp = client.post("/api/detect", headers=_post_headers(config), json={"path": "cau.csv"})
    payload = resp.json()
    assert payload["dataset"] == "Cost and Usage" and payload["detected_version"] == "1.3"


def test_convert_job_succeeds_and_matches_cli(tmp_path):
    client, config = _client(tmp_path)
    status = _run_job(client, config, path="cau.csv", mode="synthetic", output_format="csv")
    assert status["status"] == "succeeded", status

    result = client.get(f"/api/jobs/{status['job_id']}/result", headers=_auth(config)).json()
    names = {f["name"] for f in result["files"]}
    assert "focus_1_4_manifest.json" in names and "SHA256SUMS" in names

    # Parity: the Studio output is byte-identical to a direct convert of the same input.
    ref = tmp_path / "ref"
    convert_files(str(config.root / "cau.csv"), str(ref), mode="synthetic")
    studio_sums = client.get(f"/api/jobs/{status['job_id']}/checksums", headers=_auth(config)).text
    assert studio_sums == (ref / "SHA256SUMS").read_text(encoding="utf-8")


def test_preview_is_bounded(tmp_path):
    client, config = _client(tmp_path)
    status = _run_job(client, config, path="cau.csv", mode="synthetic")
    resp = client.get(
        f"/api/jobs/{status['job_id']}/preview",
        headers=_auth(config),
        params={"file": "synthetic_focus_1_4_cost_and_usage.csv", "limit": 5},
    )
    page = resp.json()
    assert len(page["rows"]) <= 5 and page["columns"]


def test_diagnostics_csv_and_summary(tmp_path):
    client, config = _client(tmp_path)
    status = _run_job(client, config, path="cau.csv", mode="synthetic")
    jid = status["job_id"]
    csv_resp = client.get(f"/api/jobs/{jid}/diagnostics", headers=_auth(config), params={"format": "csv"})
    assert csv_resp.status_code == 200 and csv_resp.text.splitlines()[0].startswith("rule_id")
    html = client.get(f"/api/jobs/{jid}/summary.html", headers=_auth(config))
    assert html.status_code == 200 and "conversion summary" in html.text.lower()


def test_generate_then_convert(tmp_path):
    client, config = _client(tmp_path)
    gen = client.post(
        "/api/generate",
        headers=_post_headers(config),
        json={"provider": "aws", "focus_version": "1.3", "rows": 50, "seed": 5},
    ).json()
    status = _run_job(
        client, config,
        source_id=gen["source_id"], source_name=gen["source_name"], mode="synthetic",
    )
    assert status["status"] == "succeeded", status


def test_generate_rows_capped(tmp_path):
    client, config = _client(tmp_path, max_generate_rows=1000)
    resp = client.post(
        "/api/generate",
        headers=_post_headers(config),
        json={"provider": "aws", "focus_version": "1.3", "rows": 10_000, "seed": 5},
    )
    assert resp.status_code == 400


# --- job manager: queue + cancel --------------------------------------------------------


def test_config_and_files_listing(tmp_path):
    client, config = _client(tmp_path)
    cfg = client.get("/api/config", headers=_auth(config)).json()
    assert cfg["root"] == str(config.root) and "aws" in cfg["providers"]
    files = client.get("/api/files?subpath=", headers=_auth(config)).json()
    assert "cau.csv" in {e["name"] for e in files["entries"]}


def test_manifest_download_and_404s(tmp_path):
    client, config = _client(tmp_path)
    status = _run_job(client, config, path="cau.csv", mode="synthetic")
    jid = status["job_id"]
    manifest = client.get(f"/api/jobs/{jid}/manifest", headers=_auth(config))
    assert manifest.status_code == 200 and manifest.json()["target_version"] == "1.4"
    dl = client.get(
        f"/api/jobs/{jid}/download",
        headers=_auth(config),
        params={"file": "synthetic_focus_1_4_cost_and_usage.csv"},
    )
    assert dl.status_code == 200 and dl.content
    # traversal guard on downloads + unknown-job handling
    assert client.get(f"/api/jobs/{jid}/download", headers=_auth(config),
                      params={"file": "../../etc/passwd"}).status_code == 400
    assert client.get("/api/jobs/nope", headers=_auth(config)).status_code == 404
    assert client.post("/api/jobs/nope/cancel", headers=_post_headers(config)).status_code == 404


def test_cancel_endpoint_accepts_known_job(tmp_path):
    client, config = _client(tmp_path)
    status = _run_job(client, config, path="cau.csv", mode="synthetic")
    resp = client.post(f"/api/jobs/{status['job_id']}/cancel", headers=_post_headers(config))
    assert resp.status_code == 202


def test_jobmanager_cancel(tmp_path):
    jm = JobManager(tmp_path / "jm", max_concurrency=1)
    started = threading.Event()

    def run(job) -> None:
        started.set()
        while not job.cancel.wait(0.02):
            pass
        job.status = "cancelled"

    job = jm.submit_convert(run)
    assert started.wait(2.0)
    job.cancel.set()
    assert job.finished.wait(2.0)
    assert job.status == "cancelled"


def test_jobmanager_runs_sequentially(tmp_path):
    jm = JobManager(tmp_path / "jm", max_concurrency=1)
    order: list[str] = []
    lock = threading.Lock()

    def make(tag: str):
        def run(job) -> None:
            with lock:
                order.append(f"start-{tag}")
            time.sleep(0.05)
            with lock:
                order.append(f"end-{tag}")
            job.status = "succeeded"
        return run

    a = jm.submit_convert(make("a"))
    b = jm.submit_convert(make("b"))
    assert a.finished.wait(3.0) and b.finished.wait(3.0)
    # concurrency 1 => the two jobs never interleave.
    assert order in (
        ["start-a", "end-a", "start-b", "end-b"],
        ["start-b", "end-b", "start-a", "end-a"],
    ), order
