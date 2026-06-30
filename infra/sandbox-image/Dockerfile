# Helix sandbox image — the single full toolchain for the `exec_python` / `bash`
# / `file_ops` tools: Python 3.12 + data/office/media libraries + the system
# binaries skills shell out to (LibreOffice headless / poppler / ffmpeg) + Node.js
# + CJK fonts & locale. (sandbox-image-consolidation: one image, no variants.)
#
# Why slim, not alpine: the data/office wheels (pandas/numpy/Pillow/matplotlib)
# ship manylinux/glibc wheels — on alpine's musl pip would compile from source.
# The full image must be Debian-based regardless, so there is no lighter base.
#
# Hardening per STREAM-F-DESIGN Mini-ADR F-5 / subsystem 14 § 5.6 — image-level
# only; the *runtime* flags (--read-only rootfs, --cap-drop=ALL,
# --security-opt=no-new-privileges, --pids-limit, --memory, --cpus, --network,
# tmpfs on /workspace + /tmp) are applied at `docker run` by the F.3
# SandboxRuntimeProvider, so one image works under both runc (dev) and
# runsc/gVisor (prod).
#
# Stream HX-10: the pinned seccomp profile lives beside this Dockerfile as
# `seccomp-profile.json` — NOT baked in (docker-out-of-docker means the host
# daemon launches the sandbox), passed to `docker run` via `--security-opt
# seccomp=`. Prod pre-places it + sets HELIX_SANDBOX_SECCOMP_PROFILE_PATH; dev
# leaves it unset (host default).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Read-only rootfs at runtime: point per-user cache/config dirs at the tmpfs
    # F.3 mounts on /workspace, so matplotlib & friends can write.
    HOME=/workspace \
    MPLCONFIGDIR=/workspace/.mplconfig

# System deps:
#   * CJK fonts + fontconfig (matplotlib/Pillow Chinese rendering) + zh_CN.UTF-8
#     locale; `fc-cache -f` bakes the font cache into the read-only rootfs.
#   * The binaries office skills shell out to — the Python lib alone is not
#     enough: xlsx recalc / pptx thumbnail / docx accept-changes call `soffice`
#     (LibreOffice headless), pdf→image calls poppler, gif/video calls ffmpeg.
#     LibreOffice no-GUI components only (writer/calc/impress + core).
#   * Node.js + npm (Debian apt — no NodeSource repo/key, keeps the install
#     surface minimal) so skill-bundled `.js` runs via the `bash` tool. Browser
#     automation (Chromium/Playwright) is intentionally NOT here — that belongs
#     in an MCP server, not the sandbox image.
#   * Pango / cairo / gdk-pixbuf — weasyprint's native rendering backend (the
#     Python wheel alone can't rasterise HTML+CSS → PDF). Pairs with the baked
#     noto-cjk fonts so generated PDF reports render Chinese.
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
        nodejs \
        npm \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libpangoft2-1.0-0 \
        libgdk-pixbuf-2.0-0 \
    && sed -i 's/^# *\(zh_CN.UTF-8 UTF-8\)/\1/' /etc/locale.gen \
    && locale-gen \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

# Set the CJK locale only AFTER locale-gen created it — declaring it earlier
# makes intervening build steps warn "cannot set LC_ALL". So str/bytes
# round-trips and library output default to UTF-8.
ENV LANG=zh_CN.UTF-8 \
    LC_ALL=zh_CN.UTF-8

# Python libraries (pins in requirements.txt) — these are the de-facto standard
# libs every agent gets (requests-via-stdlib-urllib + pandas/openpyxl/docx/pptx/
# pdf/Pillow/matplotlib/...).
COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Bake CJK-capable matplotlib defaults into the (read-only at runtime) rootfs so
# agent charts render Chinese without per-call font config.
COPY mpl_cjk_rc.py /tmp/mpl_cjk_rc.py
RUN python /tmp/mpl_cjk_rc.py && rm /tmp/mpl_cjk_rc.py

# pip is kept (NOT uninstalled): the baked requirements cannot cover every
# library an agent might need, so agents install the rest on demand via the
# `bash` / `exec_python` tools. Runtime installs are bounded — outbound traffic
# to PyPI only flows through the per-agent egress credential proxy
# (sandbox-egress §3.5) and is audited. The earlier posture (Mini-ADR F-2)
# removed pip as attack surface; the egress gate now bounds that risk while
# restoring on-demand installs.
#
# PIP_USER=1 — the runtime rootfs is read-only, so a *global* install would fail
# on /usr/local. Defaulting pip to --user lands packages in $HOME/.local
# (HOME=/workspace, a writable tmpfs at runtime) and the user-site is on
# sys.path automatically, so a bare `pip install X` just works for the agent.
# Set AFTER the build-time requirements install above so those stay global.
ENV PIP_USER=1

# Non-root user, uid 10000 (subsystem 14 § 5.6). `-m` creates the home dir; we
# then repoint HOME at /workspace (writable tmpfs at runtime). `nologin` shell —
# the container is never meant to be exec'd into interactively.
RUN useradd -u 10000 -m -s /usr/sbin/nologin agent

# /workspace is the run root; a writable tmpfs is mounted over it at runtime.
# Created + owned here so WORKDIR resolves and the matplotlib config dir is
# writable even before (and without) the mount.
RUN mkdir -p /workspace/.mplconfig && chown -R agent:agent /workspace
WORKDIR /workspace

COPY runner.py /opt/runner.py

# Egress urllib shim (sandbox-egress §3.5): placed in the GLOBAL site-packages
# so Python auto-imports it at startup — including under the runner's
# `python -I -c` (isolated mode still runs `site`). No-op unless the supervisor
# injects HELIX_EGRESS_PROXY_AUTH (egress on). Makes stdlib urllib send the
# proxy token on CONNECT like requests/httpx already do.
COPY sitecustomize.py /usr/local/lib/python3.12/site-packages/sitecustomize.py

USER agent

# exec form — no shell process, no shell-injection surface.
ENTRYPOINT ["python", "/opt/runner.py"]
