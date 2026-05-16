# Helix sandbox image — minimal Python 3.12 runner for the `exec_python` tool.
#
# Hardening per STREAM-F-DESIGN Mini-ADR F-5 / subsystem 14 § 5.6. This file
# covers the *image-level* items; the *runtime* flags (--read-only rootfs,
# --cap-drop=ALL, --security-opt=no-new-privileges, --pids-limit, --memory,
# --cpus, --network) are applied at `docker run` time by the Stream F.3
# SandboxRuntimeProvider — keeping them out of the image so one image works
# under both the dev (runc) and prod (runsc / gVisor) runtimes.
#
# Base = alpine, not slim (Mini-ADR F-13): the sandbox runs pure-stdlib
# Python with no package-install path (Mini-ADR F-2), so the ~150 MB Debian
# userland in `python:3.12-slim` is dead weight — alpine is ~50 MB.
FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Drop pip / setuptools / wheel: the sandbox has no package-install path
# (egress is credential-proxy-only, Mini-ADR F-2) so a present installer is
# nothing but attack surface.
RUN python -m pip uninstall -y pip setuptools wheel 2>/dev/null || true

# Non-root user, uid 10000 (subsystem 14 § 5.6). `nologin` shell — the
# container is never meant to be exec'd into interactively. busybox
# `adduser -D` (alpine) also creates the matching group.
RUN adduser -D -u 10000 -s /sbin/nologin agent

# `/workspace` is where submitted code runs; at runtime a writable tmpfs is
# mounted over it (F.3). Created here so WORKDIR resolves even before the
# mount.
RUN mkdir /workspace && chown agent:agent /workspace
WORKDIR /workspace

COPY runner.py /opt/runner.py

USER agent

# exec form — no shell process, no shell-injection surface.
ENTRYPOINT ["python", "/opt/runner.py"]
