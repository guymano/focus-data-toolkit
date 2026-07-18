# focus-data-toolkit Runner — a batch OCI image that wraps the `focus-toolkit` CLI.
#
# It adds no FOCUS logic: the entrypoint IS the CLI, so a container run is exactly a CLI run
# (same manifests, diagnostics, checksums, exit codes). Batch only — no HTTP server.
#
# The base image is pinned by digest (immutable). We start from Debian slim rather than
# distroless so PyArrow's native libraries, CA certificates and diagnostics work out of the
# box; hardening to distroless is a later, separately-validated step.
ARG BASE=python:3.12-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b

# --- builder: install into an isolated venv (with the [parquet] extra) --------------------
FROM ${BASE} AS builder
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore
WORKDIR /src
COPY . .
# The core is dependency-free; the image bundles [parquet] (PyArrow) so production conversions
# can read/write Parquet. Building into /opt/venv keeps the runtime layer free of build tools.
# PyArrow is pinned via constraints/runtime.txt (mirrors uv.lock) so a rebuild of the same
# release tag installs the same bytes.
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install -c constraints/runtime.txt ".[parquet]"

# --- runtime: minimal, non-root, no build tools ------------------------------------------
FROM ${BASE} AS runtime
ARG VERSION=0.0.0
ARG REVISION=unknown
LABEL org.opencontainers.image.title="focus-data-toolkit" \
      org.opencontainers.image.description="Batch Runner for the FOCUS data toolkit (focus-toolkit CLI)." \
      org.opencontainers.image.source="https://github.com/guymano/focus-data-toolkit" \
      org.opencontainers.image.licenses="MIT AND CC-BY-4.0" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}" \
    FOCUS_TOOLKIT_WORK_DIR=/work \
    TMPDIR=/work \
    PYTHONUNBUFFERED=1

# Non-root user. `/input` is intended to be mounted read-only; only `/work` (scratch) and
# `/output` (atomic staging + final files) are written, so the image runs fine with a
# read-only root filesystem (`docker run --read-only`).
RUN useradd --uid 65532 --user-group --create-home --shell /usr/sbin/nologin app \
 && mkdir -p /input /output /work \
 && chown -R app:app /output /work
USER app
WORKDIR /work
VOLUME ["/input", "/output", "/work"]

# Exec form: PID 1 is focus-toolkit itself, so SIGTERM from `docker stop` reaches it and
# triggers the cooperative cancel (clean unwind, exit 130, nothing partial published).
ENTRYPOINT ["focus-toolkit"]
CMD ["--help"]
