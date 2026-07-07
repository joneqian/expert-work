# Eval datasets — Stream G.5

Git-versioned eval sets for the [eval harness](../README.md) (Stream G.4).
Eval sets are plain YAML, reviewed in PRs like code — a prompt change
that regresses a set is caught in review / CI.

## Layout

```
datasets/
├── example.yaml      # format reference — the smoke set G.4 ships
├── golden/           # canonical "this is what good looks like" sets
└── regression/       # cases that previously broke — recurrence guards
```

- **golden/** — representative inputs + expected behaviour for an agent
  or capability. The bar a prompt/manifest must clear. One file per
  agent or area: `golden/<agent-or-area>.yaml`.
- **regression/** — a case is added here whenever a real bug is found:
  the input that broke + assertions pinning the fixed behaviour, so the
  bug can never silently return. `regression/<agent-or-area>.yaml`.
- **example.yaml** — not a real set; the format reference (`name:
  example-smoke`). Copy it as a starting point.

## Eval-set format

See [`../expert_work_eval.py`](../expert_work_eval.py) and [`../README.md`](../README.md)
for the full schema. In short — a `name` + a list of `cases`, each a
`prompt` + machine-checkable `assertions`
(`contains` / `not_contains` / `regex` / `equals`) + a `mock_response`
(the deterministic stand-in the mock provider returns).

## Running

```bash
python tools/eval/expert_work_eval.py tools/eval/datasets/golden/<set>.yaml
```

## Versioning conventions

- Eval sets live in git; changes go through PR review.
- Adding / loosening an assertion on a golden set is a deliberate,
  reviewed change to "what good looks like".
- Finding a bug → add a `regression/` case in the same PR as the fix.
- Keep `mock_response` honest: it should be a plausible real output, so
  the assertions are meaningful even before a real LLM provider runs.

## Out of scope (M0)

Automatic dataset promotion and feeding user feedback (Stream G.6) back
in as candidate cases are M2-D — subsystems/26 § "用户反馈循环".
