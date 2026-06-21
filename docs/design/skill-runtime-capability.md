# Skill Runtime Capability — what skills actually run, and how we widen the lane

Status: Proposed (design-first; implementation split into follow-up PRs)
Date: 2026-06-21
Related: [skill-github-import.md](./skill-github-import.md),
[skill-marketplace-ia.md](./skill-marketplace-ia.md),
[skill-authoring-ia.md](./skill-authoring-ia.md), Stream HX-10 (sandbox isolation)

## 1. Why this doc

Once GitHub import (#726/#734) made it trivial to pull arbitrary public skills
(e.g. `skills.sh`), the obvious next question is: **does an imported skill
actually run in helix?** Spot-checking two Vercel skills —
`vercel-labs/skills/find-skills` (a `npx skills` meta/installer) and
`vercel-labs/agent-browser` (Node + Playwright + Chromium) — both do **not**
run. That raises a fair concern: *if a large fraction of public skills are like
these, is the whole Skill module low-value?*

This doc answers that honestly: it pins down **what helix can and cannot run
today**, corrects the sampling bias in "most skills are like those two", and
specifies the changes that widen the valuable lane without breaking the security
model.

## 2. How helix runs a skill today (as-built)

- **Instructions.** A skill's `SKILL.md` body is injected into the agent system
  prompt — eagerly (`lazy_load=false`) or on demand via the `skill_view`
  tool. `<available-skills>` lists each skill + its file manifest.
  (`agent_factory._LoadedSkills`, `tools/skill_view.py`.)
- **Files are text, read on demand.** `skill_view(skill_name, path)` returns any
  bundled file (`reference/*.md`, `scripts/*.py`, …) as **text** to the model.
  Supporting files live in the DB (base64), gated by the U-21 drift + context
  re-scan. **They are never placed on a filesystem.**
- **Execution substrate.** helix has `exec_python` and `bash` tools that run in a
  gVisor sandbox via the Sandbox Supervisor (`acquire → exec(code) → release`,
  per call). The sandbox images are:
  - `infra/sandbox-image` — **Python 3.12 alpine, pure stdlib**, pip removed.
  - `infra/sandbox-image-office` — Python 3.12 slim + pandas/numpy/Pillow/
    matplotlib + CJK fonts (manifest `image_variant: office`).
- **Hard runtime constraints (by design, HX-10 / Stream F):**
  - **`network=none`** at `docker run`; egress is credential-proxy-only.
  - **No runtime package install** — pip uninstalled; no npm; read-only rootfs.
  - **No Node.js, no browser/Chromium** in either image.
  - Sandbox is **acquired per tool call** (ephemeral tmpfs `/workspace`) unless
    the agent has a persistent user workspace volume (J.15).
  - Supervisor API today: `acquire / exec(code) / release / destroy /
    read_workspace_file / reap`. **There is no "write file into the sandbox"
    API** — `exec` only takes a code string.

### Consequence

A skill that says *"run `python scripts/x.py`"* cannot do so out of the box: the
script exists only as DB text reachable via `skill_view`, not as a file on
`/workspace`. The agent would have to `skill_view` the script → write it to
`/workspace` via `bash`/file ops → execute it. Possible, but fragile and
unstated — most skills assume their files are on disk.

## 3. Capability taxonomy — what runs

| Skill class | Example | Runs in helix? |
| --- | --- | --- |
| **Instruction / knowledge** (SKILL.md + reference docs, no execution) | SOPs, API conventions, writing/brand rules, workflows | ✅ Fully. The primary design intent of Agent Skills. |
| **Python compute, stdlib** | data munging, text processing | ⚠️ Works via `exec_python`/`bash`, but scripts aren't on disk → friction (read→write→run). |
| **Python compute, needs libs** | Anthropic `pptx`/`docx`/`xlsx`/`pdf` (pandas/Pillow/…) | ⚠️ Libs only if in the `office` image; **and** scripts aren't on disk (same friction). |
| **Node / other-runtime** | `agent-browser`, anything `npx`/Node | ❌ No runtime; can't install (network=none, no npm). |
| **Network-dependent** | browser automation, scraping, arbitrary API calls | ❌ `network=none` + egress allowlist. |
| **Env-specific tooling** | `find-skills` (`npx skills` installer) | ❌ No local skills dir / npx substrate; its job (discover+install) is already helix's platform import. |

### The sampling-bias correction

"Most skills are like the two I saw" is overstated. The two are **Vercel
CLI-ecosystem** skills (Node-flavored because Vercel's `skills` tooling is Node).
The **canonical Agent Skills catalog (Anthropic's own)** — `pptx`, `docx`,
`xlsx`, `pdf`, plus many pure-knowledge skills — is **Python + instructions**.
The Agent Skills spec frames skills as *procedural knowledge with optional
helper scripts*, not as runtime plugins. So helix's lane (instructions + Python
compute) already covers the **bulk** of the canonical catalog — just not "any
`skills.sh` entry verbatim".

## 4. Architectural division: Skill vs MCP

helix is **MCP-client** by direction. The clean split:

- **Skill = knowledge + in-sandbox computation.** Portable content, no external
  state, no network. Lives in the DB, surfaced via `skill_view` and (proposed)
  materialized into the sandbox for Python execution.
- **MCP = capabilities that need an external runtime, network, or stateful
  service.** Browser, DB, GitHub, SaaS APIs. Process-isolated, governed by
  credentials + audit, network-capable.

Under this split, **`agent-browser` as a skill is a category error.** Browser
automation belongs to a **browser MCP server** (consumed like the GitHub/
Postgres MCPs), not a Node skill shoved into a Python, network-less sandbox.
Vercel blends the two because Claude Code treats everything as a local skill in
a Node env; helix need not copy that. "helix can't run a browser skill" is not a
weakness — it's the boundary doing its job.

## 5. Changes (widen the lane without breaking the model)

### 5.1 Auto-materialize skill supporting files into the sandbox — HIGHEST ROI

**Goal:** when a run executes `exec_python`/`bash`, the activated skills'
supporting files are present on `/workspace/skills/<skill_name>/…` so a skill's
`python scripts/x.py` works as authored.

**Design:**
- Add a Supervisor capability to **seed files at `acquire` time** (extend
  `acquire(...)` with an optional `seed_files: list[(path, bytes)]`, written into
  `/workspace` before the first `exec`). Files are small and already in memory at
  build time (`_LoadedSkills.resolved_versions[*].supporting_files`).
- The orchestrator passes the activated skills' supporting files (decoded,
  post drift/scan check — reuse the `skill_view` U-21 path so seeding can't
  bypass the scanner) under `skills/<name>/`. SKILL.md itself is also written so
  relative refs inside the skill resolve.
- Per-call acquire means seeding runs each call; that's fine (bytes are tiny).
  For persistent-workspace agents, seed idempotently (skip if content_hash
  matches what's already there).
- **Security:** seeded files pass the **same U-21 drift + context re-scan** as
  `skill_view`; a `[BLOCKED]` skill is not seeded. Still `network=none`,
  read-only rootfs except the `/workspace` tmpfs, non-root uid 10000. No new
  egress, no installer. Cap total seeded bytes (reuse the 5 MiB skill-package
  total) and entry count.
- **Scope:** Supervisor service + orchestrator. New protocol field on `acquire`;
  back-compat (empty list = today's behavior).

**Unlocks:** the entire Anthropic Python catalog (`pptx`/`docx`/`xlsx`/`pdf`)
runs as authored under the `office` image, plus any stdlib Python skill.

### 5.2 Import-time runtime-dependency detection + warning — LIGHT

**Goal:** don't let an operator import a skill that can't run and only find out
at runtime.

**Design:** during import parse (`parse_skill_zip` / `_ingest_*`), classify the
package and attach a non-blocking signal:
- `node` markers: `package.json`, `*.js`/`*.ts` as the primary scripts, `npx`/
  `node` in SKILL.md.
- `browser`/`network` markers: `playwright`, `puppeteer`, `chromium`, `fetch(`/
  `http` to arbitrary hosts in SKILL.md, `requirements` on networked libs.
- Surface a `runtime: { kind: "python" | "node" | "browser" | "unknown",
  runnable: bool, hint }` in the import response. The admin UI shows it:
  *"This skill needs Node/a browser — helix runs Python-only sandboxes; consider
  a browser/Node MCP server instead."*
- **Non-blocking** — knowledge value may still exist (the instructions are
  readable even if scripts won't run). Just set expectations.

### 5.3 Node sandbox variant — EVALUATION (not yet a commitment)

**Question:** add `infra/sandbox-image-node` (Node 20 + a pinned dep set) so
Node-script skills run?

**Assessment:**
- *Pro:* covers Node compute skills (no browser) that are otherwise dead.
- *Con / cost:* a third image to maintain + pin + scan; Node's dep model wants
  `npm install` which conflicts with the no-installer rule (so deps must be
  pre-baked, like the office image — only skills using the baked set run);
  doubles the "which image" surface in manifests.
- *Does not help the headline cases:* `agent-browser` still needs a browser +
  network, which a Node image alone doesn't provide and which the security model
  forbids → that stays MCP.
- **Recommendation: defer.** Do 5.1 + 5.2 first. Revisit a Node image only if,
  after auto-mount, real demand for **non-network Node compute** skills shows up.
  Browser/network Node skills are out of scope for sandboxing regardless → MCP.

## 6. Decision

1. Skill module value is **real but lane-bound**: instructions + Python compute
   (covers the bulk of the canonical catalog). Not a universal runner for any
   `skills.sh` entry.
2. **Ship 5.1 (auto-mount)** — the single biggest unlock; turns the Python
   catalog from "fiddly" to "works as authored".
3. **Ship 5.2 (import detection)** — set expectations at import time.
4. **Defer 5.3 (Node image)**; **route browser/network to MCP** (5.4 direction).
5. Document this taxonomy (this file) as the canonical answer to "will skill X
   run?".

## 7. Open questions

- Persistent-workspace seeding idempotency: key on per-skill content_hash; evict
  stale `skills/<name>/` dirs when a version changes mid-run? (Likely yes.)
- Should SKILL.md body injection note to the model that files are now on disk at
  `/workspace/skills/<name>/` (so it prefers running over re-reading via
  `skill_view`)? (Probably a one-line hint in the `<available-skills>` summary.)
- Seeded-file size/count caps vs the existing package caps — reuse or separate?
