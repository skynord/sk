import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { LiveMatch, LiveResponse } from "../api/types";
import { Card, ErrorBox, InfoBox, Loading, StatCard, WarnBox } from "../components/ui";
import { TeamCrest } from "../components/TeamCrest";
import { LeagueCrest } from "../components/LeagueCrest";
import { formatDateTime } from "../lib/format";

function ScoreCell({ match }: { match: LiveMatch }) {
  if (!match.score) return <span className="muted">vs</span>;
  return (
    <span className="mono">
      {match.score.home}–{match.score.away}
      {match.ht_score && (
        <span className="faint">
          {" "}
          (HT {match.ht_score.home}–{match.ht_score.away})
        </span>
      )}
    </span>
  );
}

function MatchTable({
  rows,
  busy,
  onImport,
}: {
  rows: LiveMatch[];
  busy: string | null;
  onImport: (match: LiveMatch) => void;
}) {
  if (rows.length === 0) return <p className="muted">Nothing here right now.</p>;
  return (
    <table>
      <thead>
        <tr>
          <th>Kickoff</th>
          <th>Competition</th>
          <th>Match</th>
          <th className="num">Status</th>
          <th>Venue</th>
          <th className="num">Analysis</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((m) => (
          <tr key={m.provider_id}>
            <td className="muted">{formatDateTime(m.kickoff_utc)}</td>
            <td className="muted">
              <LeagueCrest name={m.league} size={16} />
              {m.league}
            </td>
            <td>
              <TeamCrest name={m.home_team} size={18} />
              {m.home_team} <ScoreCell match={m} />{" "}
              <TeamCrest name={m.away_team} size={18} />
              {m.away_team}
            </td>
            <td className="num">
              <span className={`badge ${m.status === "live" ? "ok" : "warn"}`}>{m.status}</span>
            </td>
            <td className="muted">{m.venue ?? "–"}</td>
            <td className="num">
              {m.local_match_id ? (
                <Link to={`/matches/${m.local_match_id}`} className="btn small secondary">
                  Analyse →
                </Link>
              ) : (
                <button
                  className="btn small"
                  disabled={busy === m.provider_id}
                  onClick={() => onImport(m)}
                >
                  {busy === m.provider_id ? "Importing…" : "Import & analyse"}
                </button>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function Live() {
  const navigate = useNavigate();
  const [data, setData] = useState<LiveResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [date, setDate] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [importError, setImportError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .live(date || undefined)
      .then(setData)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [date]);

  useEffect(() => {
    load();
  }, [load]);

  const handleImport = useCallback(
    (match: LiveMatch) => {
      setBusy(match.provider_id);
      setImportError(null);
      api
        .importFixture({
          reference: match.provider_id,
          provider: data?.provider ?? undefined,
          date: data?.date ?? undefined,
        })
        .then((result) => {
          if (result.match_id) navigate(`/matches/${result.match_id}`);
        })
        .catch((e: Error) => setImportError(e.message))
        .finally(() => setBusy(null));
    },
    [data?.provider, data?.date, navigate],
  );

  return (
    <div>
      <div className="page-head">
        <h1>Live &amp; fixtures</h1>
        <p>
          Live scores and today's fixtures pulled from a configured provider. You can import any
          fixture and analyse it — the app never places a bet.
        </p>
      </div>

      <Card title="Source">
        <div className="grid cols-4">
          <label>
            <div className="muted small">Date (UTC)</div>
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </label>
          <label>
            <div className="muted small">&nbsp;</div>
            <button onClick={load} disabled={loading}>
              {loading ? "Fetching…" : "Fetch"}
            </button>
          </label>
        </div>
      </Card>

      {error && <ErrorBox message={error} />}
      {importError && <ErrorBox message={`Could not import fixture: ${importError}`} />}
      {!data && !error && <Loading />}

      {data && !data.available && (
        <WarnBox>
          {data.reason ??
            "No live data provider is configured. Set an API key to fetch live scores."}
          <div className="small" style={{ marginTop: 8 }}>
            Add a free key from{" "}
            <a href="https://www.football-data.org/client/register" target="_blank" rel="noreferrer">
              football-data.org
            </a>{" "}
            (fixtures &amp; live) or{" "}
            <a href="https://dashboard.api-football.com" target="_blank" rel="noreferrer">
              api-football.com
            </a>{" "}
            (also xG and odds), then set <span className="mono">FA_API_FOOTBALL_KEY</span> or{" "}
            <span className="mono">FA_FOOTBALL_DATA_ORG_KEY</span>.
          </div>
        </WarnBox>
      )}

      {data && data.available && (
        <>
          <div className="grid cols-4" style={{ marginBottom: 18 }}>
            <StatCard label="Provider" value={data.provider ?? "–"} />
            <StatCard label="Live now" value={data.counts?.live ?? data.live.length} />
            <StatCard label="Upcoming" value={data.counts?.upcoming ?? data.upcoming.length} />
            <StatCard label="Date" value={data.date ?? "today"} />
          </div>

          {data.reason && <WarnBox>{data.reason}</WarnBox>}

          <Card title="Live now">
            <MatchTable rows={data.live} busy={busy} onImport={handleImport} />
          </Card>

          <Card title="Fixtures">
            <MatchTable rows={data.upcoming} busy={busy} onImport={handleImport} />
          </Card>

          <InfoBox>
            Press <strong>Import &amp; analyse</strong> to store a fixture (with its stats and odds)
            and open its analysis. Already-imported fixtures show <strong>Analyse →</strong>.
          </InfoBox>
        </>
      )}
    </div>
  );
}
