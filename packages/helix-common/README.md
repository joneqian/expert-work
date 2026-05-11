# helix-agent-common

Helix-Agent shared utilities package. Houses the project version constant; future
Stream A work fills in:

- `logging.py` — structured logger spec (落实 P0 #10)
- `telemetry.py` — OpenTelemetry trace + Prometheus metric helpers (落实 P0 #11、#12)
- `errors.py` — exception hierarchy
- `config_loader.py` — environment config loader

See [docs/architecture/03-MONOREPO-LAYOUT.md](../../docs/architecture/03-MONOREPO-LAYOUT.md).
