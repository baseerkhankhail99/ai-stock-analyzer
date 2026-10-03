# Hugging Face Spaces setup

This repo can run as ONE Docker web service (Flask API + Dash dashboard) on a
free Hugging Face Space.

## 1. Space header

A Space reads its settings from the YAML block at the very top of the
`README.md` **in the Space repository**. Put this block at the top of the
`README.md` that you push to the Space (the GitHub README does not need it):

```yaml
---
title: AI Stock Analyzer
emoji: 📈
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---
```

`sdk: docker` and `app_port: 7860` are required. Spaces builds `Dockerfile`
from the repo root, so when pushing to the Space copy `Dockerfile.space` over
`Dockerfile` (the full-stack `Dockerfile` is meant for local/CI use):

```bash
cp Dockerfile.space Dockerfile
```

## 2. Secrets (Space → Settings → Variables and secrets)

| Name | Required | Notes |
|---|---|---|
| `DATABASE_URL` | recommended | Neon/Supabase connection string. Without it, a temporary SQLite file is used (data is lost on restart). |
| `SECRET_KEY` | yes | Long random string, e.g. `python -c "import secrets; print(secrets.token_hex(32))"` |
| `ALPHA_VANTAGE_API_KEY` | optional | |
| `FINNHUB_API_KEY` | optional | |
| `REDIS_URL` | optional | Not needed; an in-memory cache is used by default. |

Never commit real secrets to the repository.

See [DEPLOY_FREE.md](DEPLOY_FREE.md) for the full step-by-step guide.
