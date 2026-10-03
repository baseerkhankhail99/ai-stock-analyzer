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
   - optional: `ALPHA_VANTAGE_API_KEY`, `FINNHUB_API_KEY`

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

## Free-tier limits (be aware)

- **Sleeping:** free Spaces go to sleep when idle; the first visit afterwards
  takes a while to wake up.
- **Storage:** the Space disk is temporary. Neon's free plan has a small
  storage cap, and free databases may also suspend when idle (the first query
  after that is slower).
- **Data provider:** `yfinance` is an unofficial Yahoo Finance wrapper. It can
  be rate-limited or break without notice, so some symbols may return no data.
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
