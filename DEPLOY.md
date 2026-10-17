# Deploying Sky (always-on, free)

The whole app runs as one Docker image: the Python API serves both the
API (`/api/...`) and the built React dashboard. Data lives in Supabase
Postgres, so nothing depends on a local PC staying on.

## What is already done

- Code is pushed to the private GitHub repo `skynord/sk` (branch `main`).
- The SQLite database was migrated to Supabase (all 13 tables, counts verified).
- `Dockerfile` builds the frontend and backend in two stages.
- `render.yaml` is a Render Blueprint for a free web service.

## Deploy on Render (free)

1. Sign in at https://render.com with GitHub.
2. **New +** → **Blueprint** → pick the `skynord/sk` repository → **Connect**.
3. Render reads `render.yaml` and shows the service `sky-football`.
4. When asked for **FA_DATABASE_URL**, paste the Supabase connection string
   (kept private — it is not stored in this repository; set it in Render's
   environment variables, or in a local `.env` that is gitignored):

   ```
   postgresql+psycopg://postgres.<PROJECT_REF>:<PASSWORD>@aws-0-eu-west-1.pooler.supabase.com:5432/postgres
   ```

5. Click **Apply / Create** and wait for the build (a few minutes).
6. The service gets a public URL, e.g. `https://sky-football.onrender.com`.
   Open it on any device — no PC needed.

### Notes

- Render's free web service sleeps after ~15 minutes idle. The next visit
  takes ~50 seconds to wake up, then it is fast again.
- No Postgres is created on Render: the database is Supabase.
- To update, push to `main`; Render rebuilds automatically.

## Local run (for reference)

```powershell
cd backend
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000 — the backend serves the built dashboard and API.

Rebuild the dashboard after frontend changes:

```powershell
cd frontend
npm run build
```

## Database

`FA_DATABASE_URL` defaults to SQLite (`data/football_analyst.db`). Set it to a
`postgresql+psycopg://` URL to use Postgres.

### Migrating SQLite → Postgres (already done once)

```python
from sqlalchemy import create_engine, inspect, text
from app.db.base import Base
from app.db import models  # noqa: F401 - registers the mappers

# drop_all + create_all on the Postgres engine, then copy table by table
# in Base.metadata.sorted_tables order.
```
