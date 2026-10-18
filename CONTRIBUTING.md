# Contributing

Issues and pull requests are welcome. For security reports, follow
[SECURITY.md](SECURITY.md) instead of opening a public issue.

## Development setup

```bash
uv sync --extra dev
uv run pytest
bash -n scripts/*.sh
docker compose --env-file .env.example config --quiet
```

Keep credentials out of fixtures, logs, screenshots, and commits. New public
routes should include an explicit authentication and threat-model review.
