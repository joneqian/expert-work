# Helix sandbox image — "office" variant: Python 3.12 + office libraries +
# Chinese (CJK) fonts & locale, for the `exec_python` / file tools when an
# agent manifest sets `image_variant: office` (Stream OFFICE-1a / OFFICE-ADR-2).
#
# Why slim, not alpine (OFFICE-ADR-1): the office libs (pandas/numpy/Pillow/
# matplotlib) ship manylinux wheels that need glibc. On alpine's musl pip would
# compile them from source — a slow, fragile build. The minimal image stays
# alpine (infra/sandbox-image/Dockerfile, pure stdlib); office pays the larger
# Debian base because it must install binary wheels.
#
# Build context is `infra/` (the PARENT dir), NOT this directory, so we can
# COPY the single shared runner.py from the minimal image rather than
# duplicating that security-sensitive file (timeout + output-cap logic):
#
#     docker build -f infra/sandbox-image-office/Dockerfile -t <tag> infra/
#
# Hardening mirrors the minimal image (STREAM-F § 5.6): non-root uid 10000,
# pip removed after install, no shell entrypoint. The runtime flags
# (--read-only rootfs, --cap-drop=ALL, gVisor/runsc, tmpfs on /workspace,
# memory/pids/cpu caps, network=none) are applied at `docker run` by the F.3
# SandboxRuntimeProvider — kept out of the image so one image works under both
# runc (dev) and runsc (prod).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Read-only rootfs at runtime: point per-user cache/config dirs at the
    # tmpfs that F.3 mounts on /workspace, so matplotlib & friends can write.
    HOME=/workspace \
    MPLCONFIGDIR=/workspace/.mplconfig

# System deps (OFFICE-ADR-4 / route ① §5.4):
#   * CJK fonts + fontconfig (matplotlib/Pillow Chinese rendering) + zh_CN.UTF-8
#     locale; `fc-cache -f` bakes the font cache into the read-only rootfs.
#   * The system BINARIES the office skills shell out to — having the Python lib
#     is not enough: xlsx recalc / pptx thumbnail / docx accept-changes call
#     `soffice` (LibreOffice headless), pdf→image calls poppler, gif/video calls
#     ffmpeg. Install LibreOffice's no-GUI components only (writer/calc/impress
#     + core) to keep the image as small as this binary allows.
# No compiler toolchain — every Python dep still installs from a wheel.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-noto-cjk \
        fontconfig \
        locales \
        libreoffice-writer-nogui \
        libreoffice-calc-nogui \
        libreoffice-impress-nogui \
        poppler-utils \
        ffmpeg \
    && sed -i 's/^# *\(zh_CN.UTF-8 UTF-8\)/\1/' /etc/locale.gen \
    && locale-gen \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

# Set the CJK locale only AFTER locale-gen created it — declaring it earlier
# makes intervening build steps warn "cannot set LC_ALL". So str/bytes
# round-trips and library output default to UTF-8.
ENV LANG=zh_CN.UTF-8 \
    LC_ALL=zh_CN.UTF-8

# Office libraries (OFFICE-ADR-1). Pins live in requirements.txt.
COPY sandbox-image-office/requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Bake CJK-capable matplotlib defaults into the (read-only at runtime) rootfs
# so agent charts render Chinese without per-call font config (OFFICE-ADR-3).
COPY sandbox-image-office/mpl_cjk_rc.py /tmp/mpl_cjk_rc.py
RUN python /tmp/mpl_cjk_rc.py && rm /tmp/mpl_cjk_rc.py

# Drop pip / setuptools / wheel AFTER install: the sandbox has no runtime
# package-install path (egress is credential-proxy-only) so a present
# installer is pure attack surface. Imports of already-installed packages
# keep working — only the installer is gone.
RUN python -m pip uninstall -y pip setuptools wheel 2>/dev/null || true

# Non-root user, uid 10000 (matches the minimal image). `-m` creates the
# home dir; we then repoint HOME at /workspace (writable tmpfs at runtime).
RUN useradd -u 10000 -m -s /usr/sbin/nologin agent

# /workspace is the run root; a writable tmpfs is mounted over it at runtime.
# Created + owned here so WORKDIR resolves and the matplotlib config dir is
# writable even before (and without) the mount.
RUN mkdir -p /workspace/.mplconfig && chown -R agent:agent /workspace
WORKDIR /workspace

# Single source of truth — shared with the minimal image (build context = infra/).
COPY sandbox-image/runner.py /opt/runner.py
# Egress urllib shim (sandbox-egress §3.5) — shared with the minimal image; into
# the GLOBAL site-packages so `python -I -c` auto-imports it. No-op without
# HELIX_EGRESS_PROXY_AUTH. See sandbox-image/sitecustomize.py.
COPY sandbox-image/sitecustomize.py /usr/local/lib/python3.12/site-packages/sitecustomize.py

USER agent

# exec form — no shell process, no shell-injection surface.
ENTRYPOINT ["python", "/opt/runner.py"]
