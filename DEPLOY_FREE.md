# Free deployment guide (Hugging Face Spaces + Neon)

You will get a public URL such as
`https://YOUR-USERNAME-ai-stock-analyzer.hf.space` serving the dashboard at
`/dashboard/` and the API at `/api/...`. The whole app is **one** web service.

> **Disclaimer:** All analysis and forecasts are informational only and are
> **not financial advice**. Forecasts can be wrong. Do not trade based on them.

## Step 1 – Create a free database (Neon)

1. Go to <https://neon.tech> and sign up (free plan).
2. Create a project, then open **Dashboard → Connect** and copy the
   **connection string** (it starts with `postgresql://` and ends with
   `?sslmode=require`).
3. Keep it private – this is your `DATABASE_URL`.

*Supabase alternative:* create a project at <https://supabase.com>, then
**Project Settings → Database → Connection string (URI)** and use that value
(replace `[YOUR-PASSWORD]`). Both `postgres://` and `postgresql://` work, and
`sslmode=require` is added automatically when missing.

## Step 2 – Create a Hugging Face Space

1. Sign up at <https://huggingface.co>.
2. Click **New → Space**. Choose a name, SDK **Docker**, hardware
   **CPU basic (free)**, visibility Public (or Private).
3. Open the Space → **Settings → Variables and secrets** and add secrets:
   - `DATABASE_URL` – the Neon/Supabase string from Step 1
   - `SECRET_KEY` – a long random string
   - optional data keys (see "Data providers" below): `FINNHUB_API_KEY`,
     `ALPHA_VANTAGE_API_KEY`, `COINGECKO_API_KEY`

## Step 3 – Push the code

The Space is a git repository. From a copy of this project:

```bash
git clone https://huggingface.co/spaces/YOUR-USERNAME/YOUR-SPACE space
cd space
# copy the project files in (without the .git folder), then:
cp Dockerfile.space Dockerfile
# put the YAML header from SPACES_README.md at the top of README.md
git add . && git commit -m "Deploy" && git push
```

(Use a Hugging Face access token with write permission as the password.)
The Space builds the image (a few minutes) and then starts.

## Step 4 – Open your app

`https://YOUR-USERNAME-YOUR-SPACE.hf.space/` redirects to the dashboard.
Health check: `https://YOUR-USERNAME-YOUR-SPACE.hf.space/api/health`.

## Data providers (multi-source fallback chain)

Yahoo blocks or rate-limits many shared cloud IPs (Render, Spaces), so Yahoo is
**not** the only source. Each asset tries providers in order and the first one
that answers wins (the answering `source` and its `as_of` time are shown on
every card):

| Asset | Chain |
|---|---|
| Crypto (`*-USD`) | CoinGecko (one call for the overview) → Binance public API (may be geo-blocked, fails gracefully) → Yahoo |
| Stocks, indices, commodities | Finnhub (if `FINNHUB_API_KEY`) → Stooq (keyless CSV) → Alpha Vantage (if `ALPHA_VANTAGE_API_KEY`, detail views only) → Yahoo chart → yfinance |

Optional environment variables (all can be left unset):

| Variable | Effect |
|---|---|
| `FINNHUB_API_KEY` | Enables Finnhub quotes for US stocks (candles are premium; the app falls back when Finnhub answers 403) |
| `ALPHA_VANTAGE_API_KEY` | Enables Alpha Vantage daily history for single-symbol detail views (free key: 25 calls/day, the app stops at 20) |
| `COINGECKO_API_KEY` | CoinGecko demo key, sent as the `x-cg-demo-api-key` header for higher rate limits |

Behaviour to know about:

- A provider that answers 403/429 (rate limited/blocked) is skipped for 5
  minutes (circuit breaker) so the ban is not extended.
- The overview is cached for 60 s and fetched concurrently (max 6 workers,
  about 12 s budget). History per symbol is fetched once (~2y daily), shared by
  the chart, forecast, technical, analytics and compare views, cached 15 min.
- The last good overview/history is kept in memory and in the database
  (`market_snapshots` table). If every provider fails, it is shown with
  "Showing data from X minutes ago — live source unavailable".
- Stooq data is end-of-day; crypto is delayed ~1-2 minutes; Yahoo up to 15
  minutes. CoinGecko history is daily closes (candles are approximated and
  volume comes from CoinGecko's daily totals).
- `GET /api/market/momentum` never returns 502: it answers 200 with
  `{"available": false, "message": ...}` when it cannot be computed.

### Checking the data sources

Open `https://YOUR-APP/api/health/data`. For AAPL (stock), BTC-USD (crypto),
GC=F (commodity) and ^GSPC (index) it tries every provider individually and
reports `ok`, `latency_ms` and the error reason per provider (cached for 60 s;
API keys are never included). Alpha Vantage is skipped to protect its daily
quota unless you add `?alphavantage=1`. The Settings (gear) panel shows the same
provider state, and the dashboard footer shows e.g.
`Prices: CoinGecko, Stooq • Updated 12:11 UTC`.

## Start command (Render)

The dashboard calls the services in-process, so a single worker with several
threads is enough and keeps the in-memory cache shared (good for the 512 MB
free tier):

```bash
gunicorn --bind 0.0.0.0:$PORT --workers 1 --worker-class gthread --threads 8 --timeout 120 "app:create_app()"
```

## Free-tier limits (be aware)

- **Sleeping:** free Spaces go to sleep when idle; the first visit afterwards
  takes a while to wake up.
- **Storage:** the Space disk is temporary. Neon's free plan has a small
  storage cap, and free databases may also suspend when idle (the first query
  after that is slower).
- **Data providers:** free sources are delayed or end-of-day, never guaranteed
  real-time (see "Data providers" below).
- **Forecasts:** the default forecast uses a scikit-learn ensemble. Prophet and
  TensorFlow/LSTM are optional/not installed in the slim deployment.

## Run locally on Windows without Docker

```bat
python -m venv venv
venv\Scripts\activate
pip install -r requirements-space.txt
python app.py
```

Open <http://127.0.0.1:7860/>. With no `DATABASE_URL` set, a local SQLite file
(`stock_analyzer.db`) is used and Redis is not needed. Copy `.env.template` to
`.env` to configure keys.
