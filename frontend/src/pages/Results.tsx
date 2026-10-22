import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Card, ErrorBox, Loading } from "../components/ui";
import { formatDate } from "../lib/format";

interface ResultRow {
  match_id: number;
  kickoff_utc: string;
  home_team: string;
  away_team: string;
  league_id: number | null;
  score: { home: number; away: number };
  actual: { "1x2": string; btts: string; score: string };
  bot: {
    pick: { market: string; selection: string; probability: number } | null;
    pick_ok: boolean | null;
    btts_pick: string | null;
    btts_ok: boolean | null;
    correct_score: string | null;
    correct_score_ok: boolean | null;
    confidence: string | null;
    expected_goals: { home: number; away: number; total: number } | null;
  };
}

interface ResultsResp {
  count: number;
  days: number;
  stats: {
    "1x2": { hit: number; total: number };
    btts: { hit: number; total: number };
    correct_score: { hit: number; total: number };
  };
  results: ResultRow[];
}

function pct(n: number, d: number) {
  if (!d) return "—";
  return `${Math.round((n / d) * 100)}% (${n}/${d})`;
}

export default function Results() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<ResultsResp | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetch(`${import.meta.env.VITE_API_BASE ?? "/api"}/results?days=${days}&limit=100`)
      .then((r) => {
        if (!r.ok) throw new Error(r.statusText);
        return r.json();
      })
      .then((d) => setData(d))
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [days]);

  if (loading && !data) return <Loading label="Loading results." />;
  if (error) return <ErrorBox message={error} />;

  const stats = data?.stats;
  return (
    <div>
      <div className="page-head">
        <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 10 }}>
          <h1 style={{ marginBottom: 0 }}>Results</h1>
          <div className="row" style={{ gap: 8 }}>
            {[7, 14, 30, 60].map((d) => (
              <button
                key={d}
                className={`btn small ${days === d ? "" : "secondary"}`}
                onClick={() => setDays(d)}
              >
                {d}d
              </button>
            ))}
          </div>
        </div>
        <p className="faint" style={{ marginBottom: 0 }}>
          Finished matches compared to stored batch predictions
        </p>
      </div>

      {stats && (
        <div className="grid cols-3" style={{ marginBottom: 18 }}>
          <Card title="1X2 (pick)">
            <div style={{ fontSize: 28, fontWeight: 600 }}>{pct(stats["1x2"].hit, stats["1x2"].total)}</div>
            <div className="faint">Hits / tracked</div>
          </Card>
          <Card title="BTTS">
            <div style={{ fontSize: 28, fontWeight: 600 }}>{pct(stats.btts.hit, stats.btts.total)}</div>
            <div className="faint">Hits / tracked</div>
          </Card>
          <Card title="Correct Score">
            <div style={{ fontSize: 28, fontWeight: 600 }}>{pct(stats.correct_score.hit, stats.correct_score.total)}</div>
            <div className="faint">Hits / tracked</div>
          </Card>
        </div>
      )}

      <Card title={`Finished (${data?.count ?? 0})`}>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Match</th>
                <th>Score</th>
                <th>Bot pick</th>
                <th>1X2</th>
                <th>BTTS pick</th>
                <th>BTTS</th>
                <th>Correct score</th>
                <th>CS</th>
              </tr>
            </thead>
            <tbody>
              {(data?.results ?? []).map((r) => (
                <tr key={r.match_id}>
                  <td className="faint">{formatDate(r.kickoff_utc)}</td>
                  <td>
                    <Link to={`/matches/${r.match_id}`}>
                      {r.home_team} <span className="faint">vs</span> {r.away_team}
                    </Link>
                  </td>
                  <td>
                    <strong>
                      {r.score.home} - {r.score.away}
                    </strong>
                  </td>
                  <td>
                    {r.bot.pick ? (
                      <span>
                        {r.bot.pick.selection === "home" ? r.home_team.split(" ")[0] : r.bot.pick.selection === "away" ? r.away_team.split(" ")[0] : "Draw"} ({Math.round(r.bot.pick.probability * 100)}%)
                      </span>
                    ) : (
                      <span className="faint">—</span>
                    )}
                  </td>
                  <td>{r.bot.pick_ok === true ? "✓" : r.bot.pick_ok === false ? "✗" : "—"}</td>
                  <td>{r.bot.btts_pick ? r.bot.btts_pick.toUpperCase() : <span className="faint">—</span>}</td>
                  <td>{r.bot.btts_ok === true ? "✓" : r.bot.btts_ok === false ? "✗" : "—"}</td>
                  <td>{r.bot.correct_score ?? <span className="faint">—</span>}</td>
                  <td>{r.bot.correct_score_ok === true ? "✓" : r.bot.correct_score_ok === false ? "✗" : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
