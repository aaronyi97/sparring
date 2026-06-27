> Status: experimental module. This repo is one mechanism inside my broader AI Workflow Diagnostics system. Start here instead: DoneTrace / Fusion Paradigm.

# Sparring

[中文说明](README.zh-CN.md)

Sparring is a local-first decision tool. Before a high-stakes call, it makes three heterogeneous AI families argue first, then has an AI coach use the real disagreements to push you to think it through, and finally walks you out with a decision memo.

It is not a group chat, not a voting machine, and not a tool that picks the answer for you. The point is not to collect three opinions and average them. The point is to surface where strong, independent reasoners actually disagree, force you to take your own position before any synthesis is revealed, and leave the final judgment with you.

## Status

This is v1, local-first, and finished as a working local build (milestones M1–M5). Treat it as an honest v1, not a polished product: it runs end-to-end with real models, it has 58 passing tests, and the deploy scripts are written but not exercised against a live host.

It is meant to be run on your own machine with your own model credentials. There is no hosted service.

## What It Does

The core loop is seven steps:

1. **Question gate** — Liberal admission. A pure factual lookup ("what's the capital of X") is bounced back for a rewrite; a real decision gets through.
2. **Heterogeneous fan-out** — Three AI families answer the same question in parallel and independently, with no knowledge of each other (`n_of_m = 2`, first responders are used so one slow model can't stall the round).
3. **Disagreement map** — Consensus, points of conflict, and an overall decision-ambiguity score, built from the independent answers.
4. **Coach** — A five-track Socratic coach uses the *real* disagreements to walk you through your own thinking, backed by an independent closure judge that guards against self-justification bias.
5. **Position card** — Before anything is revealed, you are forced to state your own position. This step cannot be skipped.
6. **Side-by-side reveal** — Your reasoning versus the synthesized view of all parties, diffed point by point.
7. **Decision memo** — An exportable, shareable record of the call you made and why.

Across sessions, an optional qualitative profile observes your decision patterns (opt-in consent, one-click delete).

## What It Does Not Do

- It does not pick the answer for you or cast a deciding vote.
- It is not a group chat between models, and it is not a popularity poll.
- It does not let the models see each other's answers before you take your own position.
- It does not run as a hosted, multi-tenant service or sync your data anywhere.
- It does not call models without your own credentials; missing endpoints fail closed rather than silently falling back.
- It is not a place for secrets, customer data, or anything you would not want leaving your machine.

## Quick Start

Requirements:

- Python 3.11 or newer.
- Node.js for the front end.
- Your own model credentials (eight endpoints; see `.env.example`).

Back end:

```bash
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # then fill in your own endpoints and keys
.venv/bin/python -m uvicorn sparring.api:app --port 8788
```

Front end (in a second terminal):

```bash
cd web && npm install && npm run dev   # proxies /api to 8788
```

Seed an invite code (used for login):

```bash
.venv/bin/python -m sparring.seed_invite 5 "first batch"
```

Tests, CLI run, and a real HTTP end-to-end check:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m sparring.cli "your real decision question"
.venv/bin/python tools/e2e_live.py     # requires the back end to be running
```

## Model Routing

The three default contributors are intentionally heterogeneous — one model each from three different families — so the disagreement is real and not three variations of the same house style. A separate model serves as coach, and lighter models handle the gate, the disagreement analysis, the closure judge, and the final synthesis.

To swap models, edit one line: the `roles` block in `config.yaml`. Every real model call is recorded in a cost ledger at `data/sparring.db::cost_ledger` (idempotent by request id), so you can see what each session actually cost.

## Architecture

- **Back end:** FastAPI with Server-Sent Events for streaming, on top of SQLite (WAL mode).
- **Front end:** React 19 with Tailwind CSS v4, built with Vite, talking to the back end over SSE with heartbeats and hard timeouts.
- **Auth and limits:** invite-code login (the client cannot self-report identity), a daily per-user quota, and the opt-in profile observer.
- **Single worker:** the back end keeps some state in process (a background-synthesis task registry), so v1 runs as a single uvicorn worker. Scaling out is a v2 topic — move sessions to Postgres and the synthesis tasks to a queue. See `deploy/` for the full picture (v1 deploy scripts are written but not run; bringing it online is on you).

## Contributing

Contributions should keep the project local-first and privacy-safe by default. Keep examples synthetic, and never commit secrets, a real `.env`, or private data. Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

## License

MIT. See [LICENSE](LICENSE).
