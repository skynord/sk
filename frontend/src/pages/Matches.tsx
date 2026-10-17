import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { League, MatchList } from "../api/types";
import { Card, ErrorBox, Loading } from "../components/ui";
import { TeamCrest } from "../components/TeamCrest";
import { LeagueCrest } from "../components/LeagueCrest";
import { formatDate } from "../lib/format";

const PAGE_SIZE = 25;

const PERIODS = [
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
  { value: "next7", label: "Next 7 days" },
  { value: "all", label: "All time" },
] as const;

const PERIOD_LABELS: Record<string, string> = Object.fromEntries(
  PERIODS.map((p) => [p.value, p.label]),
);

function utcToday(): Date {
  const now = new Date();
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
}

function isoDay(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function addDays(d: Date, days: number): Date {
  const n = new Date(d);
  n.setUTCDate(n.getUTCDate() + days);
  return n;
}

export default function Matches() {
  const [params, setParams] = useSearchParams();
  const [leagues, setLeagues] = useState<League[]>([]);
  const [data, setData] = useState<MatchList | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const leagueId = params.get("league_id") ?? "";
  const status = params.get("status") ?? "";
  const q = params.get("q") ?? "";
  const onDate = params.get("on_date") ?? "";
  const period = params.get("period") ?? "7d";
  const offset = Number(params.get("offset") ?? 0);

  const range = useMemo(() => {
    if (onDate) {
      return { from: undefined, to: undefined, order: "kickoff_desc" };
    }
    const today = utcToday();
    switch (period) {
      case "30d":
        return {
          from: isoDay(addDays(today, -29)),
          to: isoDay(today),
          order: "kickoff_desc",
        };
      case "next7":
        return {
          from: isoDay(today),
          to: isoDay(addDays(today, 6)),
          order: "kickoff_asc",
        };
      case "all":
        return { from: undefined, to: undefined, order: "kickoff_desc" };
      case "7d":
      default:
        return {
          from: isoDay(addDays(today, -6)),
          to: isoDay(today),
          order: "kickoff_desc",
        };
    }
  }, [period, onDate]);

  useEffect(() => {
    api.leagues().then(setLeagues).catch(() => undefined);
  }, []);

  useEffect(() => {
    setLoading(true);
    api
      .matches({
        league_id: leagueId ? Number(leagueId) : undefined,
        status: status || undefined,
        q: q || undefined,
        on_date: onDate || undefined,
        date_from: range.from,
        date_to: range.to,
        order: range.order,
        limit: PAGE_SIZE,
        offset,
      })
      .then(setData)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [leagueId, status, q, onDate, offset, range]);

  const leagueName = useMemo(
    () => leagues.find((l) => String(l.id) === leagueId)?.name,
    [leagues, leagueId],
  );

  const periodLabel = onDate ? `on ${onDate}` : PERIOD_LABELS[period] ?? "Last 7 days";

  function update(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    next.delete("offset");
    setParams(next);
  }

  return (
    <div>
      <div className="page-head">
        <h1>Matches</h1>
        <p>
          {periodLabel}
          {leagueName ? ` · ${leagueName}` : " · all competitions"}
        </p>
      </div>

      <Card>
        <div className="row">
          <label>
            Period{" "}
            <select value={period} onChange={(e) => update("period", e.target.value)}>
              {PERIODS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Competition{" "}
            <select value={leagueId} onChange={(e) => update("league_id", e.target.value)}>
              <option value="">All</option>
              {leagues.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Status{" "}
            <select value={status} onChange={(e) => update("status", e.target.value)}>
              <option value="">Any</option>
              <option value="scheduled">Scheduled</option>
              <option value="finished">Finished</option>
              <option value="live">Live</option>
            </select>
          </label>
          <label>
            Date{" "}
            <input type="date" value={onDate} onChange={(e) => update("on_date", e.target.value)} />
          </label>
          <label>
            Team{" "}
            <input
              placeholder="search…"
              defaultValue={q}
              onKeyDown={(e) => {
                if (e.key === "Enter") update("q", (e.target as HTMLInputElement).value);
              }}
            />
          </label>
        </div>
      </Card>

      {error && <ErrorBox message={error} />}
      {loading && <Loading />}

      {data && !loading && (
        <Card title={`${data.total} matches`}>
          {data.matches.length === 0 ? (
            <p className="muted">No matches match these filters.</p>
          ) : (
            <div>
              {data.matches.map((m) => (
                <Link key={m.id} to={`/matches/${m.id}`} style={{ color: "inherit" }}>
                  <div className="match-row">
                    <div className="muted">
                      <LeagueCrest name={m.league} size={16} />
                      {formatDate(m.kickoff_utc)}
                    </div>
                    <div className="teams">
                      <TeamCrest name={m.home_team.name} size={20} />
                      {m.home_team.name}
                      <span className="vs">vs</span>
                      <TeamCrest name={m.away_team.name} size={20} />
                      {m.away_team.name}
                    </div>
                    <div className="right">
                      {m.status === "finished" && m.score ? (
                        <span className="mono">
                          {m.score.home}–{m.score.away}
                        </span>
                      ) : (
                        <span className="badge">{m.status}</span>
                      )}
                    </div>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </Card>
      )}

      {data && data.total > PAGE_SIZE && (
        <div className="row">
          <button
            className="btn secondary small"
            disabled={offset === 0}
            onClick={() => {
              const next = new URLSearchParams(params);
              next.set("offset", String(Math.max(0, offset - PAGE_SIZE)));
              setParams(next);
            }}
          >
            ← Previous
          </button>
          <span className="muted">
            {offset + 1}–{Math.min(offset + PAGE_SIZE, data.total)} of {data.total}
          </span>
          <button
            className="btn secondary small"
            disabled={offset + PAGE_SIZE >= data.total}
            onClick={() => {
              const next = new URLSearchParams(params);
              next.set("offset", String(offset + PAGE_SIZE));
              setParams(next);
            }}
          >
            Next →
          </button>
        </div>
      )}
    </div>
  );
}
