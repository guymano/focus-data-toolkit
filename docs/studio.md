# Studio (local web UI)

The **Studio** is the toolkit's human-facing interface: a local web app for people who don't want
to touch Python, `pip`, virtual environments or CLI flags. It never reimplements FOCUS logic — it
drives the same SDK the CLI and Runner use (`detect_focus_schema`, `convert_files`, the
generators, `open_row_source`), so its manifests, diagnostics and checksums are **identical** to a
CLI run.

## Installation & first launch

New to Python? Pick your operating system and run the block **top to bottom** — each is
self-contained. Already comfortable? Skip to the [one-liner](#already-have-python-one-liner).

**Prerequisite (every system): Python 3.11 or newer.** Check with `py --version` (Windows) or
`python3 --version` (macOS/Linux). If it's missing or older, install it first (links below).

### Windows (PowerShell)

1. Install **Python 3.11+** from [python.org/downloads](https://www.python.org/downloads/) — on the
   first installer screen, tick **"Add python.exe to PATH"**. Reopen PowerShell, then check:
   ```powershell
   py --version
   ```
2. In the folder holding your FOCUS files (or any folder), set the toolkit up in an isolated
   environment and launch it:
   ```powershell
   py -3.11 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install "focus-data-toolkit[studio]"
   focus-toolkit ui
   ```
3. Your browser opens to `http://127.0.0.1:8765/?token=…`. Press **Ctrl-C** in PowerShell to stop.

> **If activation says "running scripts is disabled on this system"**, either allow scripts for this
> window only and re-run the activate line:
> ```powershell
> Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
> .\.venv\Scripts\Activate.ps1
> ```
> …or skip activation entirely and call the environment directly:
> ```powershell
> .\.venv\Scripts\python -m pip install "focus-data-toolkit[studio]"
> .\.venv\Scripts\focus-toolkit ui
> ```

### macOS (Terminal)

1. Install **Python 3.11+** from [python.org/downloads](https://www.python.org/downloads/) (or
   `brew install python@3.12`). Check:
   ```bash
   python3 --version
   ```
2. In the folder holding your FOCUS files:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install "focus-data-toolkit[studio]"
   focus-toolkit ui
   ```
3. Your browser opens to `http://127.0.0.1:8765/?token=…`. Press **Ctrl-C** to stop.

### Linux (Terminal)

1. Ensure **Python 3.11+** with the venv/pip modules. On Debian/Ubuntu:
   ```bash
   sudo apt install python3-venv python3-pip
   python3 --version
   ```
2. In the folder holding your FOCUS files:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install "focus-data-toolkit[studio]"
   focus-toolkit ui
   ```
3. Open the printed `http://127.0.0.1:8765/?token=…` URL. Press **Ctrl-C** to stop.

> On recent distros a system-wide `pip install` is refused ("externally-managed-environment"). The
> virtual environment above is exactly what avoids that — nothing is installed system-wide.

### Already have Python? One-liner

With **pipx** or **uv**, install the tool in one isolated step (any OS), then launch it:

```bash
pipx install "focus-data-toolkit[studio]"     # or:  uv tool install "focus-data-toolkit[studio]"
focus-toolkit ui
```

### Options you'll likely want

- **Parquet support:** install `"focus-data-toolkit[studio-all]"` instead of `[studio]`.
- **Point it at your data / change the port:**
  ```bash
  focus-toolkit ui --root /path/to/your/focus/files --port 8765
  ```
  (Windows: quote paths with spaces, e.g. `--root "C:\My Data\focus"`.)
- **Come back later:** re-activate the environment (`source .venv/bin/activate`, or
  `.\.venv\Scripts\Activate.ps1` on Windows) and run `focus-toolkit ui` again. A pipx/uv install
  stays on your PATH — just run `focus-toolkit ui`.

### Running it on a remote / headless machine (SSH)

The Studio binds to `127.0.0.1`, so it isn't reachable from outside by design. To use it from your
laptop, forward the port over SSH — your data stays on the server and the link is encrypted:

```bash
# on the server:
focus-toolkit ui --no-open-browser --root /path/to/data
# on your laptop (new terminal), then open the printed URL locally:
ssh -L 8765:127.0.0.1:8765 user@server
```

Prefer this tunnel over `--allow-remote` (which exposes the port and leaves the token as the only
guard — use it only on a trusted network).

### Troubleshooting

| Symptom | Fix |
|---|---|
| `focus-toolkit: command not found` | The environment isn't active — re-run the `activate` line, or call it directly (`.venv/bin/focus-toolkit`; Windows: `.\.venv\Scripts\focus-toolkit`). |
| Windows: *"running scripts is disabled"* | See the ExecutionPolicy note under **Windows** above. |
| Linux: *"externally-managed-environment"* | Use the virtual environment (or pipx) — don't `pip install` system-wide. |
| `no matches found: focus-data-toolkit[studio]` | Quote it: `"focus-data-toolkit[studio]"` (needed in zsh/macOS and PowerShell). |
| *"ui needs the studio extra"* / `ModuleNotFoundError: fastapi` | Installed without the extra — reinstall with `[studio]`. |
| `Address already in use` (port 8765) | Pick another port: `focus-toolkit ui --port 8766`. |

The launch command prints a URL containing a one-time token and opens it in your browser.

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
