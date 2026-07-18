"""The Studio FastAPI application: security middleware + a thin API over the Core SDK.

Every route delegates to the same SDK the CLI uses — ``detect_focus_schema``, ``convert_files``,
the generators, ``open_row_source`` — so nothing here reimplements FOCUS logic and the outputs
match a CLI run byte-for-byte. The app is created via :func:`create_app` so it can be driven by a
test client without starting a server.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import io
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

from focus_data_toolkit import __version__
from focus_data_toolkit.studio.config import MAX_PREVIEW_LIMIT, StudioConfig
from focus_data_toolkit.studio.jobs import Job, JobManager
from focus_data_toolkit.studio.preview import sampled_page
from focus_data_toolkit.studio.security import (
    PathOutsideRoot,
    host_header_allowed,
    origin_allowed,
    resolve_within_root,
    token_matches,
)

_FRONTEND = Path(__file__).resolve().parent / "frontend"
_MANIFEST_NAME = "focus_1_4_manifest.json"
_CHECKSUMS_NAME = "SHA256SUMS"

# Error detail is logged server-side; API responses carry only generic, non-revealing messages
# (no exception text / stack info flows to the client).
_LOG = logging.getLogger("focus_data_toolkit.studio")


def _parquet_available() -> bool:
    try:
        import pyarrow  # noqa: F401

        return True
    except ModuleNotFoundError:
        return False


def create_app(config: StudioConfig, jobs: JobManager | None = None) -> FastAPI:
    app = FastAPI(title="Focus Data Toolkit Studio", docs_url=None, redoc_url=None, openapi_url=None)
    jm = jobs or JobManager(
        config.work_dir or config.root / ".fdt-studio-work",
        max_concurrency=config.max_concurrency,
        ttl_seconds=config.job_ttl_seconds,
    )
    app.state.jobs = jm

    @app.middleware("http")
    async def _guard(request: Request, call_next: Any) -> Response:
        # Loopback defense-in-depth (skipped for an explicit remote bind, which is token-gated):
        # validate Host (anti DNS-rebinding) and, for state-changing API calls, Origin (anti-CSRF).
        if not config.allow_remote:
            if not host_header_allowed(request.headers.get("host"), config.host, config.port):
                return JSONResponse({"error": "host not allowed"}, status_code=400)
        path = request.url.path
        is_api = path.startswith("/api/")
        if is_api and not config.allow_remote and request.method not in ("GET", "HEAD", "OPTIONS"):
            if not origin_allowed(request.headers.get("origin"), config.host, config.port):
                return JSONResponse({"error": "origin not allowed"}, status_code=403)
        # Token gates every API call (header for fetch, query param for EventSource/downloads).
        if is_api:
            token = request.headers.get("x-fdt-token") or request.query_params.get("token")
            if not token_matches(config.token, token):
                return JSONResponse({"error": "missing or invalid token"}, status_code=401)
        return await call_next(request)

    if _FRONTEND.is_dir():
        app.mount("/static", StaticFiles(directory=str(_FRONTEND)), name="static")

    # --- shell ---------------------------------------------------------------------------
    @app.get("/")
    async def index() -> Response:
        html = _FRONTEND / "index.html"
        if not html.is_file():
            return HTMLResponse("<h1>Studio frontend missing</h1>", status_code=500)
        return FileResponse(str(html))

    # --- metadata ------------------------------------------------------------------------
    @app.get("/api/health")
    async def health() -> Response:
        return JSONResponse({"version": __version__, "parquet": _parquet_available()})

    @app.get("/api/config")
    async def get_config() -> Response:
        from focus_data_toolkit.generators import FOCUS_VERSIONS, PROVIDERS

        return JSONResponse(
            {
                "root": str(config.root),
                "max_upload_bytes": config.max_upload_bytes,
                "max_generate_rows": config.max_generate_rows,
                "providers": list(PROVIDERS),
                "focus_versions": list(FOCUS_VERSIONS),
                "modes": ["strict", "synthetic"],
                "output_formats": ["csv", "parquet"] if _parquet_available() else ["csv"],
                "parquet": _parquet_available(),
            }
        )

    # --- browse the allowlisted root -----------------------------------------------------
    @app.get("/api/files")
    async def list_files(subpath: str = "") -> Response:
        try:
            target = resolve_within_root(subpath or ".", config.root)
        except PathOutsideRoot as exc:
            _LOG.warning("rejected file listing outside root: %s", exc)
            return JSONResponse({"error": "path is outside the allowed root"}, status_code=400)
        if not target.is_dir():
            return JSONResponse({"error": "not a directory"}, status_code=400)
        entries = []
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if child.name.startswith("."):
                continue
            try:
                size = child.stat().st_size if child.is_file() else None
            except OSError:
                size = None
            entries.append({"name": child.name, "is_dir": child.is_dir(), "size": size})
        rel = target.relative_to(config.root) if target != config.root else Path()
        return JSONResponse({"path": str(rel), "entries": entries})

    # --- detect --------------------------------------------------------------------------
    @app.post("/api/detect")
    async def detect(request: Request) -> Response:
        body = await request.json()
        try:
            source = _resolve_source(config, jm, body)
        except PathOutsideRoot as exc:
            _LOG.warning("rejected detect source: %s", exc)
            return JSONResponse({"error": "path is outside the allowed root"}, status_code=400)
        if source is None:
            return JSONResponse({"error": "provide a path or source"}, status_code=400)
        from focus_data_toolkit.io.records import MalformedRecordError
        from focus_data_toolkit.io.row_source import open_row_source
        from focus_data_toolkit.schema import detect_focus_schema

        try:
            with contextlib.closing(open_row_source(str(source))) as reader:
                header = reader.source_columns
            result = detect_focus_schema(header)
        except (MalformedRecordError, PathOutsideRoot, OSError) as exc:
            _LOG.warning("detect failed: %s", exc)
            return JSONResponse({"error": "could not read the source file"}, status_code=400)
        return JSONResponse(result.as_dict())

    # --- managed sources: upload + generate ----------------------------------------------
    @app.post("/api/upload")
    async def upload(file: UploadFile = File(...)) -> Response:
        source_id, dest_dir = jm.new_source_dir()
        name = Path(file.filename or "upload.csv").name
        target = dest_dir / name
        written = 0
        with open(target, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > config.max_upload_bytes:
                    out.close()
                    target.unlink(missing_ok=True)
                    return JSONResponse(
                        {"error": f"upload exceeds the {config.max_upload_bytes}-byte limit"},
                        status_code=413,
                    )
                out.write(chunk)
        return JSONResponse({"source_id": source_id, "source_name": name, "size": written})

    @app.post("/api/generate")
    async def generate(request: Request) -> Response:
        from starlette.concurrency import run_in_threadpool

        body = await request.json()
        provider = str(body.get("provider", "aws"))
        version = str(body.get("focus_version", "1.3"))
        seed = int(body.get("seed", 1202))
        rows = int(body.get("rows", 1000))
        if rows < 1 or rows > config.max_generate_rows:
            return JSONResponse(
                {"error": f"rows must be between 1 and {config.max_generate_rows} (Studio cap; "
                          "use the CLI/Runner for larger synthetic sets)"},
                status_code=400,
            )
        from focus_data_toolkit.generators import FOCUS_VERSIONS, PROVIDERS, get_generator

        if provider not in PROVIDERS or version not in FOCUS_VERSIONS:
            return JSONResponse({"error": "unknown provider or focus_version"}, status_code=400)

        source_id, dest_dir = jm.new_source_dir()
        suffix = version.replace(".", "_")

        def _emit() -> dict:
            module = get_generator(provider, version)
            cau_name = f"focus_{suffix}_cost_and_usage_{provider}.csv"
            (dest_dir / cau_name).write_bytes(module.generate_csv_bytes(rows, seed))
            names = [cau_name]
            if version == "1.3":
                cc_name = f"focus_{suffix}_contract_commitment_{provider}.csv"
                (dest_dir / cc_name).write_bytes(
                    module.generate_contract_commitment_csv_bytes(rows, seed)
                )
                names.append(cc_name)
            return {"cau": cau_name, "names": names}

        made = await run_in_threadpool(_emit)
        return JSONResponse(
            {"source_id": source_id, "source_name": made["cau"], "files": made["names"], "rows": rows}
        )

    # --- conversion jobs -----------------------------------------------------------------
    @app.post("/api/jobs")
    async def create_job(request: Request) -> Response:
        from focus_data_toolkit.convert import ConversionCancelled, OnExists
        from focus_data_toolkit.model.capabilities import CapabilityProfile
        from focus_data_toolkit.runtime import ResourceLimitError

        body = await request.json()
        try:
            source = _resolve_source(config, jm, body)
            contract = _resolve_source(config, jm, body, prefix="contract_")
        except PathOutsideRoot as exc:
            _LOG.warning("rejected job source outside root: %s", exc)
            return JSONResponse({"error": "source path is outside the allowed root"}, status_code=400)
        if source is None:
            return JSONResponse({"error": "provide a source (path or source_id)"}, status_code=400)
        mode = str(body.get("mode", "strict"))
        output_format = str(body.get("output_format", "csv"))
        supports = [str(s) for s in body.get("supports", [])]
        try:
            on_exists = OnExists(str(body.get("on_exists", "refuse")))
        except ValueError:
            return JSONResponse({"error": "invalid on_exists"}, status_code=400)
        caps = CapabilityProfile.of(*supports) if supports else None

        def run(job: Job) -> None:
            from focus_data_toolkit.convert import convert_files

            try:
                convert_files(
                    str(source),
                    str(job.out_dir),
                    contract_commitment=str(contract) if contract else None,
                    mode=mode,
                    output_format=output_format,
                    on_exists=on_exists,
                    capabilities=caps,
                    progress=lambda event: job.events.append(event.as_dict()),
                    cancel=job.cancel.is_set,
                )
                job.status = "succeeded"
            except ConversionCancelled:
                job.status = "cancelled"
            except ResourceLimitError as exc:
                job.status, job.error, job.error_code = "failed", exc.diagnostic.message, exc.diagnostic.code
            except Exception as exc:  # ConversionError / AtomicWriteError / MalformedRecord / ...
                job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"

        job = jm.submit_convert(run)
        return JSONResponse({"job_id": job.id}, status_code=202)

    @app.get("/api/jobs/{job_id}")
    async def job_status(job_id: str) -> Response:
        job = jm.get(job_id)
        if job is None:
            return JSONResponse({"error": "unknown job"}, status_code=404)
        return JSONResponse(job.summary())

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str) -> Response:
        if jm.get(job_id) is None:
            return JSONResponse({"error": "unknown job"}, status_code=404)

        async def stream() -> Any:
            cursor = 0
            while True:
                job = jm.get(job_id)
                if job is None:
                    break
                while cursor < len(job.events):
                    yield f"data: {json.dumps(job.events[cursor])}\n\n"
                    cursor += 1
                if job.done:
                    yield f"event: done\ndata: {json.dumps(job.summary())}\n\n"
                    break
                await asyncio.sleep(0.25)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.post("/api/jobs/{job_id}/cancel")
    async def job_cancel(job_id: str) -> Response:
        job = jm.get(job_id)
        if job is None:
            return JSONResponse({"error": "unknown job"}, status_code=404)
        job.cancel.set()
        return JSONResponse({"ok": True}, status_code=202)

    @app.get("/api/jobs/{job_id}/result")
    async def job_result(job_id: str) -> Response:
        job = jm.get(job_id)
        if job is None:
            return JSONResponse({"error": "unknown job"}, status_code=404)
        files: list[dict] = []
        manifest: dict | None = None
        if job.out_dir.is_dir():
            for child in sorted(job.out_dir.iterdir()):
                files.append(
                    {"name": child.name, "is_dir": child.is_dir(),
                     "size": child.stat().st_size if child.is_file() else None}
                )
            manifest = _read_manifest(job.out_dir)
        return JSONResponse(
            {
                "status": job.status,
                "error": job.error,
                "error_code": job.error_code,
                "files": files,
                "datasets": (manifest or {}).get("datasets"),
                "diagnostics": (manifest or {}).get("diagnostics", []),
                "assumptions_present": (manifest or {}).get("assumptions_present"),
            }
        )

    @app.get("/api/jobs/{job_id}/preview")
    async def job_preview(job_id: str, file: str, offset: int = 0, limit: int = 50) -> Response:
        try:
            path = jm.job_file(job_id, file)
        except (KeyError, PathOutsideRoot) as exc:
            _LOG.warning("rejected preview file: %s", exc)
            return JSONResponse({"error": "invalid file"}, status_code=400)
        if not path.exists():
            return JSONResponse({"error": "no such produced file"}, status_code=404)
        limit = max(1, min(limit, MAX_PREVIEW_LIMIT))
        from focus_data_toolkit.io.records import MalformedRecordError

        try:
            page = sampled_page(path, offset=offset, limit=limit)
        except (MalformedRecordError, OSError) as exc:
            _LOG.warning("preview failed: %s", exc)
            return JSONResponse({"error": "could not read the file"}, status_code=400)
        return JSONResponse(page)

    @app.get("/api/jobs/{job_id}/manifest")
    async def job_manifest(job_id: str) -> Response:
        job = jm.get(job_id)
        if job is None or not (job.out_dir / _MANIFEST_NAME).is_file():
            return JSONResponse({"error": "no manifest"}, status_code=404)
        return FileResponse(str(job.out_dir / _MANIFEST_NAME), media_type="application/json")

    @app.get("/api/jobs/{job_id}/checksums")
    async def job_checksums(job_id: str) -> Response:
        job = jm.get(job_id)
        if job is None or not (job.out_dir / _CHECKSUMS_NAME).is_file():
            return JSONResponse({"error": "no checksums"}, status_code=404)
        return PlainTextResponse((job.out_dir / _CHECKSUMS_NAME).read_text(encoding="utf-8"))

    @app.get("/api/jobs/{job_id}/diagnostics")
    async def job_diagnostics(job_id: str, format: str = "json") -> Response:
        job = jm.get(job_id)
        manifest = _read_manifest(job.out_dir) if job else None
        if manifest is None:
            return JSONResponse({"error": "no manifest"}, status_code=404)
        diags = manifest.get("diagnostics", [])
        if format == "csv":
            return PlainTextResponse(_diagnostics_csv(diags), media_type="text/csv")
        return JSONResponse(diags)

    @app.get("/api/jobs/{job_id}/download")
    async def job_download(job_id: str, file: str) -> Response:
        try:
            path = jm.job_file(job_id, file)
        except (KeyError, PathOutsideRoot) as exc:
            _LOG.warning("rejected download file: %s", exc)
            return JSONResponse({"error": "invalid file"}, status_code=400)
        if not path.is_file():
            return JSONResponse({"error": "not a downloadable file"}, status_code=404)
        return FileResponse(str(path), filename=path.name, media_type="application/octet-stream")

    @app.get("/api/jobs/{job_id}/summary.html")
    async def job_summary_html(job_id: str) -> Response:
        job = jm.get(job_id)
        manifest = _read_manifest(job.out_dir) if job else None
        if manifest is None:
            return HTMLResponse("<p>No result yet.</p>", status_code=404)
        return HTMLResponse(_summary_html(manifest))

    return app


# --- helpers ----------------------------------------------------------------------------
def _resolve_source(
    config: StudioConfig, jm: JobManager, body: dict, *, prefix: str = ""
) -> Path | None:
    """Resolve a source from a path (under root) or a managed (id, name) pair; None if absent."""
    path = body.get(f"{prefix}path")
    if path:
        return resolve_within_root(str(path), config.root)
    source_id = body.get(f"{prefix}source_id")
    source_name = body.get(f"{prefix}source_name")
    if source_id and source_name:
        return jm.source_file(str(source_id), str(source_name))
    return None


def _read_manifest(out_dir: Path) -> dict | None:
    manifest = out_dir / _MANIFEST_NAME
    if not manifest.is_file():
        return None
    try:
        return json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _diagnostics_csv(diags: list[dict]) -> str:
    columns = ["rule_id", "severity", "message", "dataset", "column", "line_number", "suggestion"]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for diag in diags:
        writer.writerow({key: diag.get(key, "") for key in columns})
    return buffer.getvalue()


def _summary_html(manifest: dict) -> str:
    import html

    rows = []
    for name, entry in (manifest.get("datasets") or {}).items():
        rows.append(
            f"<tr><td>{html.escape(name)}</td><td>{html.escape(str(entry.get('status')))}</td>"
            f"<td>{html.escape(str(entry.get('conformance')))}</td>"
            f"<td>{html.escape(str(entry.get('row_count', '')))}</td></tr>"
        )
    diags = manifest.get("diagnostics", [])
    return (
        "<!doctype html><meta charset='utf-8'><title>FOCUS conversion summary</title>"
        "<h1>FOCUS conversion summary</h1>"
        f"<p>source {html.escape(str(manifest.get('source_version')))} → "
        f"target {html.escape(str(manifest.get('target_version')))}, "
        f"mode {html.escape(str(manifest.get('mode')))}, "
        f"assumptions_present={html.escape(str(manifest.get('assumptions_present')))}.</p>"
        "<table border='1' cellpadding='4'><tr><th>Dataset</th><th>Status</th>"
        "<th>Conformance</th><th>Rows</th></tr>" + "".join(rows) + "</table>"
        f"<p>{len(diags)} diagnostic(s).</p>"
    )
