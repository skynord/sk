# Sky

A self-hosted football **prediction & analysis** application. It ingests real
historical results, fits a **Dixon–Coles Poisson** model, and turns it into
honest, reproducible match probabilities and value estimates — shown in a
React dashboard, with a **prediction bot** that forecasts upcoming fixtures.

> It does **not** place bets and it does **not** invent data. Anything that
> cannot be computed from real inputs is reported as missing — never guessed.

---

## Why this exists

Most "prediction" apps hide their assumptions behind a single number. This one
is built the other way around:

- **Every probability is traceable** to the model, its parameters and the exact
  matches it trained on (see the *Model & provenance* panel on each analysis).
- **Uncertainty is explicit** — Monte-Carlo standard error, calibration curves,
  and a confidence level that drops when there is little history.
- **Missing inputs stay missing.** No xG? The xG row is empty and says so. No
  odds? There is no value table, only a note.
- **Synthetic data is labelled.** The `sample` provider generates made-up
  results for demos; every derived record is tagged `SYNTHETIC` and the UI warns
  about it.
- **Backtesting is walk-forward.** Each fixture is predicted using only matches
  played *before* it, so the reported scores contain no look-ahead leakage.

---

## Features

- **Match analysis** — 1X2, double chance, over/under (1.5–4.5), both teams to
  score, team totals, correct score, total-goals distribution, Asian handicap.
- **Expected goals** — attack/defence multipliers, home advantage, with an
  optional xG blend (weight is adjustable and off by default).
- **Monte-Carlo simulation** — an independent cross-check of the closed-form
  probabilities, with its own standard error.
- **Value analysis** — enter the decimal odds you are offered and get fair
  probability, edge and expected value per unit, with caveats.
- **Form & head-to-head** — last 5/10/15, home/away splits, scoring rates, rest
  days, and recent meetings.
- **Backtesting & calibration** — Brier score, log loss, accuracy, and
  reliability curves versus a base-rate baseline.
- **Provider abstraction** — one interface for several data sources, each with
  its licence and cost surfaced in the UI.
- **Prediction bot** — `app.cli predict` scores every upcoming fixture in a
  window (1X2, totals, BTTS, scoreline, expected goals) and flags value against
  any stored odds. Run it once or on a loop; it can persist each run.
- **Live & fixtures** — `/api/live` shows in-play scores and today's fixtures
  from a configured provider (read-only; it never places a bet).
- **Full REST API** — FastAPI with OpenAPI docs at `/api/docs`.

---

## Architecture

```
football-analyst/
├── backend/                FastAPI + SQLAlchemy + NumPy/SciPy
│   ├── app/
│   │   ├── config.py         typed settings (FA_* env vars)
│   │   ├── db/               ORM models, session, migrations-free init
│   │   ├── providers/        openfootball, football-data.co.uk, API-Football,
│   │   │                     Sportmonks, football-data.org, Besoccer (opt-in),
│   │   │                     manual, sample
│   │   ├── ingest/           provider DTO -> database upserts + provenance
│   │   ├── models/           poisson, dixon_coles, markets, simulate,
│   │   │                     ratings, xg, calibration, backtest
│   │   ├── analysis/         features, value, report, engine, bot
│   │   ├── api/routes/       health, leagues, matches, analysis, models, value, predictions, ingest
│   │   ├── schemas.py        request/response models
│       │   └── cli.py            initdb / sources / ingest / refresh-upcoming / enrich-stats / backtest / predict / seed-sample
│   └── tests/                pytest suite
├── data/                   SQLite DB, cache, and data/raw for manual CSVs
└── frontend/               React + TypeScript + Vite dashboard
    └── src/
        ├── api/              typed client + response types
        ├── components/       UI primitives, probability bars, charts
        └── pages/            Dashboard, Matches, MatchAnalysis, Predictions, Live, Backtests, DataSources
```

**Pipeline:** `provider → ingest (DTO → upsert + provenance) → feature building →
Dixon–Coles fit → markets → Monte-Carlo → report → API → dashboard`.

---

## Quickstart

### 1. Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# create tables and register data sources
.\.venv\Scripts\python.exe -m app.cli initdb

# load REAL results right now (public-domain openfootball, no key, no sign-up)
.\.venv\Scripts\python.exe -m app.cli ingest openfootball

# OPTIONAL: or load SYNTHETIC demo data instead (clearly labelled)
.\.venv\Scripts\python.exe -m app.cli seed-sample

# run the API
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

- API root: <http://127.0.0.1:8000/api/health>
- Interactive docs: <http://127.0.0.1:8000/api/docs>

### 1b. Run the prediction bot

```powershell
cd backend

# score every fixture in the next 7 days (one pass)
.\.venv\Scripts\python.exe -m app.cli predict --days 7

# bot mode: refresh every hour until you stop it
.\.venv\Scripts\python.exe -m app.cli predict --days 7 --loop 3600
```

The bot predicts fixtures already stored with status `scheduled` or `live`. To
get real upcoming/live fixtures in, add a provider key — see *Getting real data*.

### 1c. Backfill per-match statistics (xG, shots, corners)

Openfootball ships results only — no shots, corners or xG — so analysis of those
matches runs on actual goals alone. If you have an API-Football key, backfill
each stored match's statistics (including **expected goals / xG**) onto the rows
already in the database; the request count is one per match, so `--limit` keeps
a run inside a Free key's daily allowance and re-running resumes where it stopped:

```powershell
cd backend

# enrich the 80 most recent finished matches, then re-run tomorrow for the next 80
.\.venv\Scripts\python.exe -m app.cli enrich-stats api_football --limit 80

# or focus a single competition / season
.\.venv\Scripts\python.exe -m app.cli enrich-stats api_football --league-id 1 --season-label 2024
```

Once a competition's recent matches carry xG, the analysis page shows it and the
confidence grade no longer holds back for missing xG.

> **Free-plan data limits.** An API-Football Free key only reaches seasons
> **2022–2024** (and has no odds), so a current-season fixture cannot be given
> xG from it; that needs a paid plan. Odds likewise need a paid plan *or* the
> manual `football_data_co_uk` CSVs (below). The app says so plainly rather than
> inventing numbers.

### 2. Frontend

```powershell
cd frontend
npm install
npm run dev            # http://localhost:5173  (proxies /api to :8000)
```

The Vite dev server proxies `/api` to `http://127.0.0.1:8000`, so run the backend
first.

### 2b. Or serve the built UI from the backend (one process)

Build the dashboard once; the API then serves it itself (with SPA routing), so a
single `uvicorn` process gives the whole app:

```powershell
cd frontend
npm install
npm run build            # writes frontend/dist

cd ..\backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
# open http://127.0.0.1:8000  — the dashboard, served by the API
```

### 3. Or run both

```powershell
.\run.ps1              # starts backend and frontend together
```

---

## Getting real data

Nothing is bundled. You choose a source and, importantly, **respect its terms**.

| Provider | How to get data | Key needed | Notes |
|---|---|---|---|
| `openfootball` | `app.cli ingest openfootball` | no | **Real results, no sign-up.** Public domain; fixtures/results only (no stats or odds). |
| `football_data_co_uk` | Download the season CSV yourself into `data/raw/`, then run `ingest` | no | Free, but automated scraping is forbidden — **manual download only**. |
| `manual` | Maintain `data/raw/manual_*.json` | no | For your own spreadsheets / one-off matches. |
| `sample` | `app.cli seed-sample` | no | **SYNTHETIC** demo data. Never shown as real. |
| `api_football` | set `FA_API_FOOTBALL_KEY` | yes | Free tier ~100 req/day; paid plans higher. |
| `sportmonks` | set `FA_SPORTMONKS_TOKEN` | yes | Paid; free tier limited to a couple of leagues. |
| `football_data_org` | set `FA_FOOTBALL_DATA_ORG_KEY` | yes | Free tier with rate limits. |
| `besoccer` | set `FA_ENABLE_BESOCCER_SCRAPING=true` | no | **Off by default.** HTML scraping, robots.txt honoured. |

The easiest way to get real games in is **openfootball** — free, public-domain
result data with no key. Choose the competitions and seasons in `.env`:

```
FA_OPENFOOTBALL_LEAGUES=en.1,es.1,de.1,it.1,fr.1,nl.1,pt.1,be.1,at.1,tr.1,gr.1,sco.1,uefa.cl
FA_OPENFOOTBALL_SEASONS=2024-25,2025-26
```

The six smaller domestic leagues are there for the Champions League: without
them a Turkish, Greek, Portuguese, Belgian, Dutch, Austrian or Scottish club is
only a league-average prior, and "Galatasaray are strong" would be a guess.

A cup club's pool follows the matches it actually played, not the competition
that first filed it (a club imported through the Champions League keeps the cup
as its "home" league forever), and the short cup spelling is folded onto the
league spelling, so `Sporting CP` trains on its Primeira Liga results.

then:

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.cli ingest openfootball
```

**Upcoming and live fixtures** are not available from openfootball (it is a
results archive). To fetch fixtures, live scores and (with API-Football) odds,
set a key and point the app at it:

```
FA_API_FOOTBALL_KEY=...        # api-football.com  (fixtures, live, stats, odds)
FA_FOOTBALL_DATA_ORG_KEY=...   # football-data.org (fixtures; live on paid tier)
FA_LIVE_PROVIDER=api_football  # optional: force one provider
FA_HTTP_RATE_LIMIT_PER_MINUTE=8  # keep under the provider's per-minute limit
```

The **API-Football Free plan** allows **100 requests/day and 10/minute**, and
only seasons 2022–2024 and a ±1-day date window. Sky paces itself to the
per-minute limit and only calls per-match stats/odds for the fixtures it keeps,
so a free key is enough for real live scores and next-day predictions.

**One-time history** (gives the keyed provider's own team names something to
learn from — one request per league/season):

```powershell
cd backend
# Premier League=39, La Liga=140, Serie A=135, Bundesliga=78, Ligue 1=61
foreach ($s in 2022,2023,2024) {
  foreach ($l in 39,140,135,78,61) {
    .\.venv\Scripts\python.exe -m app.cli ingest api_football --league-id $l --season $s --no-details
  }
}
```

**Daily refresh** — one command imports today's fixtures **and odds** (the big
five and the Champions League), then runs the prediction bot over them:

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.cli refresh-upcoming
```

Override the day or leagues when you want, or import only fixtures:

```powershell
.\.venv\Scripts\python.exe -m app.cli refresh-upcoming --date 2026-10-16 --days 5
.\.venv\Scripts\python.exe -m app.cli refresh-upcoming --no-details --no-predict
```

The lower-level command is still there for other providers/dates. Omit
`--no-details` to pull odds (the value column then fills in):

```powershell
.\.venv\Scripts\python.exe -m app.cli ingest api_football --date 2026-10-16 `
  --leagues "Premier League|England,La Liga|Spain,Serie A|Italy,Bundesliga|Germany,Ligue 1|France"
```

The same thing is available over HTTP:

```
POST /api/ingest/run
{"provider": "api_football", "include_details": true,
 "params": {"date": "2026-10-16",
            "leagues": ["Premier League|England", "La Liga|Spain"]}}
```

Example — import a football-data.co.uk CSV you saved to `data/raw/E0.csv`:

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.cli ingest football_data_co_uk
```

### Legal / ethical notes

- Automated collection is **disabled by default**. `besoccer` scraping is opt-in
  and checks `robots.txt` before every request.
- Rate limiting and caching are built in to avoid hammering any host.
- Each provider records its own **licence and cost** note, shown on the *Data
  sources* page so you always know the terms you are operating under.
- This tool is for **analysis and research**. It does not place bets and is not
  betting advice.
- Proprietary/paid sites (e.g. FootyStats, Sky Sports) are **not** scraped: their
  terms disallow it. Live data comes only from official APIs you hold a key for.
- Live/odds data is fetched from the provider you configure and is **never**
  auto-staked; the value figures are for you to judge.

---

## The model

**Dixon–Coles** (1997): a bivariate Poisson for each team's goals, with a
low-score correction (`rho`), team-specific attack and defence multipliers, a
shared home-advantage term, and **exponential time decay** so recent matches
matter more (half-life configurable, default 180 days).

From the fitted goal distribution we derive every market exactly by summing the
score grid. Monte-Carlo simulation independently samples the same process as a
sanity check and to report a simulation standard error.

**Backtesting** re-fits the model in walk-forward fashion: it trains only on
matches strictly before each evaluated fixture, then scores the predictions
(Brier, log loss, accuracy) against a base-rate baseline and plots calibration.

**xG blending** is optional and defaults to off. When a source supplies xG you
can give it a weight (0–1); the report states clearly whether xG was used.

---

**Form does not move the probabilities.** Results already carry it, and
walk-forward backtests of the form term (points per game, last 5) came out
slightly *worse* than without it: Brier 0.58750 vs 0.58707 in England,
0.59242 vs 0.59231 in Spain, 0.60580 vs 0.60554 in France. It does move the
**confidence grade**: a pick whose side is in clearly better recent form gains
up to 4 points, one whose side is in clearly worse form loses 6, and the report
says which. Both sides need five recent matches for it to apply.

**No pick without evidence.** If either side has no completed match in the
training window its strength is only a league-average prior, so the prediction
bot publishes the market numbers but no pick, no value and no confidence score:
the Predictions page shows "sin datos" rather than dressing a guess up as a
favourite.
## API quick reference

All routes are under the `/api` prefix.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | service status, enabled providers |
| GET | `/api/providers` | provider capabilities, availability, cost |
| GET | `/api/sources` | registered data sources and status |
| GET | `/api/leagues` | competitions with match counts |
| GET | `/api/matches` | list/filter/paginate matches |
| GET | `/api/matches/{id}` | match detail (stats, odds) |
| POST | `/api/matches/manual` | create a match by hand |
| POST | `/api/analysis/{id}` | full analysis report for a match |
| GET | `/api/predictions/upcoming` | prediction bot: forecasts for upcoming fixtures |
| GET | `/api/live` | live scores & today's fixtures (needs a provider key) |
| POST | `/api/models/backtest` | run a walk-forward backtest |
| POST | `/api/ingest/run` | trigger a provider import |
| POST | `/api/value/assess` | expected value for one price |

`/api/predictions/upcoming` answers from the batch the background refresh stored
(`source: "stored"`, plus the time it was computed) whenever the requested
window fits inside it, and `refresh=true` fits the model again. Fitting one model
per competition is CPU-bound — minutes on a small free server — so the batch is
built in the background and served from the database instead of making every
visit pay for a fit.

Example:

```bash
curl -s http://127.0.0.1:8000/api/analysis/536 \
  -H "Content-Type: application/json" \
  -d '{"monte_carlo_simulations": 10000, "xg_weight": 0.0}'
```

---

## Testing

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q      # unit + integration tests
.\.venv\Scripts\python.exe -m ruff check .   # lint
```

Frontend:

```powershell
cd frontend
npm run typecheck
npm run build
```

---

## Configuration

All settings use the `FA_` prefix and can live in a `.env` file at the repo root
or in `backend/`. See [`.env.example`](.env.example) for the full list, including
database URL, provider keys, model defaults and CORS origins.

---

## Disclaimer

Football is inherently uncertain. Nothing here is a guarantee, and the tool is
not financial or betting advice. Use it to understand probabilities and value,
not to assume outcomes. Always gamble responsibly where permitted.
