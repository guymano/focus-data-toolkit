# Studio (local web UI)

The **Studio** is the toolkit's human-facing interface: a local web app for people who don't want
to touch Python, `pip`, virtual environments or CLI flags. It never reimplements FOCUS logic — it
drives the same SDK the CLI and Runner use (`detect_focus_schema`, `convert_files`, the
generators, `open_row_source`), so its manifests, diagnostics and checksums are **identical** to a
CLI run.

```bash
pip install "focus-data-toolkit[studio]"   # or [studio-all] for Parquet
focus-toolkit ui                           # opens http://127.0.0.1:8765/?token=…
```

The command prints a URL containing a one-time token and opens it in your browser.

## What you can do

1. **Choose a source** — one of:
   - a file already under the allowlisted root (`--root`, default: the current directory) — the
     recommended path for large files;
   - a small/medium **upload** (streamed to disk, size-capped);
   - **generated** synthetic test data (AWS/Azure/GCP, FOCUS 1.2/1.3).
2. **Detect** the dataset / FOCUS version / confidence.
3. **Convert** (strict or synthetic; CSV or Parquet) with **live per-phase progress** and a
   working **Cancel** — cancelling publishes nothing.
4. **Review** — a **sampled, paginated** preview (the full file is never loaded into the backend or
   the browser), the per-dataset status/conformance table, and downloads: each produced dataset,
   `focus_1_4_manifest.json`, `SHA256SUMS`, diagnostics as JSON or CSV, and an HTML summary.

## Security model

Even a loopback server is reachable by a malicious page open in your browser, so the Studio layers
several defenses (all local, no external calls):

| Control | Behaviour |
|---|---|
| **Bind** | `127.0.0.1` by default; a non-loopback `--host` is refused unless `--allow-remote`. |
| **Token** | A fresh URL-safe token per start, required on every API request (header or query). |
| **Host header** | Validated against the loopback/bind allowlist — defeats DNS-rebinding. |
| **Origin** | Required and validated on state-changing (POST) requests — defeats CSRF. |
| **Root allowlist** | All file access is confined under `--root`; absolute paths, drive-relative/UNC paths and `..` traversal are rejected. |
| **Symlinks / junctions** | Each entry is resolved to its real target (symlink, Windows junction, reparse point); a link whose target escapes `--root` is refused. A link that stays inside `--root` is followed. |
| **Uploads** | Streamed to disk, capped (`--max-upload`, default 200 MB), never held fully in RAM. |

With `--allow-remote` the Host/Origin checks are relaxed (you can't predict a remote authority) and
the **token becomes the sole guard** — expose only on trusted networks.

## Limits (be honest about scale)

- **Generation is capped** in the Studio (default 100 000 rows) because the generators build the
  dataset in memory. For very large synthetic sets, use the CLI or the Runner.
- **One conversion at a time** by default (extra submissions queue), so two large jobs can't
  exhaust the machine.
- **Large real files** go through the bounded streaming path, but the Studio is single-node — for
  hundreds of GB prefer the Runner with fast local storage (see [runner.md](runner.md)).
- Job outputs and generated/uploaded sources live under a work directory and are swept on a TTL and
  at startup (interrupted atomic publishes are recovered).

## Flags

```
focus-toolkit ui \
  --host 127.0.0.1 \      # bind address (non-loopback needs --allow-remote)
  --port 8765 \
  --root . \              # directory the UI may read source files from
  --work-dir PATH \       # scratch/output (default: a fresh temp dir)
  --max-upload 200MB \
  --allow-remote \        # permit a non-loopback bind (token still required)
  --no-open-browser
```
