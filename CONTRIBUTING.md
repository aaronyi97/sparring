# Contributing

Thanks for helping improve Sparring.

The project is intentionally small: a local-first decision-sparring tool that makes three heterogeneous AI families argue, then has an AI coach use the real disagreements to help one person think through a high-stakes call. Please keep changes inside that boundary.

## Ground Rules

- Keep the default app local-first. The human keeps the final judgment; the tool surfaces disagreement and runs the coaching loop, it does not decide.
- Do not add a hosted multi-tenant service, cloud sync, or telemetry without an explicit product-boundary discussion.
- Keep model access credential-driven and fail-closed. Do not add silent fallbacks to a default provider.
- Use synthetic examples only.
- Do not include real local paths, private workspace names, customer data, private decision records, or secrets.

## Development Checks

Run the tests before submitting changes:

```bash
.venv/bin/python -m pytest -q
```

If a change affects the seven-step loop or a model role, update both `README.md` and `README.zh-CN.md` where relevant.

## Pull Request Checklist

- The change serves the local-first decision-sparring tool.
- Docs are updated if behavior or boundary changed.
- Examples and fixtures are synthetic.
- No sensitive data appears in code, docs, screenshots, or issue text.
- `.venv/bin/python -m pytest -q` passes.

## Do Not Commit Secrets

This tool talks to real models through your own endpoints, so secrets are easy to leak by accident. Before you push:

- Never commit API keys, tokens, or a real `.env`. Use `.env.example` as the only checked-in template, with placeholder values.
- Never commit the local database or `data/` contents.
- Never paste real decision records, private conversations, or customer data into code, tests, issues, or screenshots.

## Scope Discipline

Good contributions make the decision loop sharper: a cleaner disagreement map, a more honest coach, a better closure judge, a clearer reveal. Large additions such as cloud accounts, team administration, or a hosted service should start as a product-boundary proposal before code.
