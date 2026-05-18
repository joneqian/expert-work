# Eval harness — Stream G.4

A lightweight, Python-native prompt-evaluation harness — a prompt
regression guard. No promptfoo, no Node (Mini-ADR G-3 in
[STREAM-G-DESIGN](../../docs/streams/STREAM-G-DESIGN.md)).

## Run

```bash
python tools/eval/helix_eval.py tools/eval/datasets/example.yaml
```

Exits 0 when every case passes, 1 otherwise. The harness is also
exercised by `tools/eval/test_helix_eval.py` in the gating `Test
(pytest)` CI job.

## Eval-set format

An eval set is a YAML file — see [`datasets/example.yaml`](./datasets/example.yaml):

```yaml
name: my-eval-set
cases:
  - id: a-unique-id
    prompt: "The input sent to the model."
    mock_response: "Deterministic stand-in output (mock provider / CI)."
    assertions:
      - type: contains       # output contains the value
        value: "expected substring"
      - type: not_contains   # output does NOT contain the value
        value: "sk-"
      - type: regex          # re.search(value, output) matches
        value: '"status"\s*:\s*"ok"'
      - type: equals         # output equals the value exactly
        value: "exact text"
```

## Providers

`run_eval(eval_set, complete)` takes a pluggable provider —
`async def complete(prompt: str) -> str`.

- **`mock_provider(eval_set)`** (M0 / CI) — returns each case's
  `mock_response`. Deterministic, needs no LLM credentials.
- **Real LLM** — supply your own `complete` that calls an LLM. Real
  prompt evaluation needs API credentials, so it runs locally / in the
  M0→M1 Gate, not in CI.

## Scope (M0)

M0 is the harness skeleton: format + runner + assertions + an example
set. Deferred to M2-D: LLM-as-judge assertions, a real provider wired to
the orchestrator LLM stack, regression-gating, A/B. Eval **dataset**
organisation (golden / regression sets) is Stream G.5.
