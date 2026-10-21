import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { League, UpcomingPrediction, UpcomingPredictions } from "../api/types";
import { Card, ErrorBox, InfoBox, Loading, StatCard, WarnBox } from "../components/ui";
import { TeamCrest } from "../components/TeamCrest";
import { LeagueCrest } from "../components/LeagueCrest";
import { formatDateTime, pct, confidenceClass } from "../lib/format";

const SELECT_LABEL: Record<string, string> = {
  home: "Home win",
  draw: "Draw",
  away: "Away win",
};

function pickLabel(prediction: UpcomingPrediction): string {
  const { selection } = prediction.pick;
  if (selection === "home") return prediction.home_team;
  if (selection === "away") return prediction.away_team;
  return SELECT_LABEL[selection] ?? selection;
}

const POLL_MS = 2500;

export default function Predictions() {
  const [leagues, setLeagues] = useState<League[]>([]);
  const [data, setData] = useState<UpcomingPredictions | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [leagueId, setLeagueId] = useState<number | undefined>(undefined);
  const [days, setDays] = useState(14);
  const [includeLive, setIncludeLive] = useState(true);
  const pollTimer = useRef<number | null>(null);

  const clearTimer = useCallback(() => {
    if (pollTimer.current !== null) {
      window.clearTimeout(pollTimer.current);
      pollTimer.current = null;
    }
  }, []);

  const run = useCallback(
    (force = false) => {
      clearTimer();
      setError(null);
      api
        .upcomingPredictions({
          league_id: leagueId,
          days,
          include_live: includeLive,
          refresh: force,
        })
        .then((res) => {
          setData(res);
          if (res.status === "running") {
            pollTimer.current = window.setTimeout(() => run(false), POLL_MS);
          }
        })
        .catch((e: Error) => setError(e.message));
    },
    [leagueId, days, includeLive, clearTimer],
  );

  useEffect(() => {
    api.leagues().then(setLeagues).catch(() => undefined);
  }, []);

  useEffect(() => {
    run(false);
    return clearTimer;
  }, [run, clearTimer]);

  const running = data?.status === "running";

  return (
    <div>
      <div className="page-head">
        <h1>Predictions</h1>
        <p>
          The prediction bot runs the model over upcoming fixtures and reports, for each one, the
          most likely outcome, a scoreline, and any value it finds against stored odds.
        </p>
      </div>

      <Card title="Bot settings">
        <div className="grid cols-4">
          <label>
            <div className="muted small">Competition</div>
            <select
              value={leagueId ?? ""}
              onChange={(e) => setLeagueId(e.target.value ? Number(e.target.value) : undefined)}
            >
              <option value="">All competitions</option>
              {leagues.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            <div className="muted small">Forward window (days)</div>
            <input
              type="number"
              min={1}
              max={120}
              value={days}
              onChange={(e) => setDays(Math.max(1, Number(e.target.value) || 1))}
            />
          </label>
          <label>
            <div className="muted small">Include in-play</div>
            <input
              type="checkbox"
              checked={includeLive}
              onChange={(e) => setIncludeLive(e.target.checked)}
            />
          </label>
          <label>
            <div className="muted small">&nbsp;</div>
            <button onClick={() => run(true)} disabled={running}>
              {running ? "Running…" : "Run bot"}
            </button>
          </label>
        </div>
      </Card>

      {error && <ErrorBox message={error} />}
      {!error && data?.status === "error" && <ErrorBox message={data.error ?? "The bot failed."} />}
      {running && (
        <Loading
          label="Running the prediction bot…"
          detail="Fitting one model per competition. On the free server this takes several minutes: leave the page open and the results appear as soon as the run finishes."
        />
      )}

      {data?.status === "ready" && data.source === "stored" && (
        <InfoBox>
          These are the predictions the background refresh computed
          {data.generated_at ? ` at ${formatDateTime(data.generated_at)}` : ""} for the next{" "}
          {data.stored_window_days ?? data.window_days} days. Press <strong>Run bot</strong> to
          recompute them now.
        </InfoBox>
      )}

      {data?.status === "ready" && (
        <>
          <div className="grid cols-4" style={{ marginBottom: 18 }}>
            <StatCard label="Fixtures considered" value={data.considered ?? 0} />
            <StatCard label="Predictions made" value={data.predicted ?? 0} />
            <StatCard label="Skipped (no history)" value={(data.skipped ?? []).length} />
            <StatCard
              label="Window"
              value={`${data.window_days ?? 0} days`}
              sub={data.generated_at ? formatDateTime(data.generated_at) : undefined}
            />
          </div>

          {(data.predicted ?? 0) === 0 && (
            <WarnBox>
              No upcoming fixtures were found in the window. The bot predicts fixtures that are
              already in the database (status <span className="mono">scheduled</span> or{" "}
              <span className="mono">live</span>). If every stored match is historical, import
              upcoming fixtures first, then run the bot again.
            </WarnBox>
          )}

          {(data.predictions ?? []).length > 0 && (
            <Card title="Predicted fixtures">
              <table className="fixtures-table">
                <thead>
                  <tr>
                    <th>Kickoff</th>
                    <th>Competition</th>
                    <th>
                      Match <span className="faint small">(ML)</span>
                    </th>
                    <th>Pick</th>
                    <th className="num">Score</th>
                    <th className="num" title="Both teams to score">
                      BTTS
                    </th>
                    <th>Confidence</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {(data.predictions ?? []).map((p) => {
                    const btts = p.probabilities.btts?.yes;
                    return (
                      <tr key={p.match_id}>
                        <td className="muted nowrap">{formatDateTime(p.kickoff_utc)}</td>
                        <td className="muted">
                          {p.league ? (
                            <>
                              <LeagueCrest name={p.league} size={16} />
                              {p.league}
                            </>
                          ) : (
                            "–"
                          )}
                        </td>
                        <td>
                          <div className="fixture-match">
                            <span className="crest-team">
                              <TeamCrest name={p.home_team} size={18} />
                              {p.home_team}
                            </span>
                            <span className="faint">vs</span>
                            <span className="crest-team">
                              <TeamCrest name={p.away_team} size={18} />
                              {p.away_team}
                            </span>
                          </div>
                        </td>
                        <td>
                          {pickLabel(p)} <strong>{pct(p.pick.probability, 0)}</strong>
                        </td>
                        <td className="num">
                          <strong>{p.predicted_score.score}</strong>{" "}
                          <span className="faint">{pct(p.predicted_score.probability, 0)}</span>
                        </td>
                        <td
                          className="num"
                          title={
                            typeof btts === "number"
                              ? `Both teams to score: ${pct(btts, 0)}`
                              : "No BTTS estimate"
                          }
                        >
                          {typeof btts === "number" ? (
                            <span className={btts >= 0.5 ? "mono" : "muted"}>{pct(btts, 0)}</span>
                          ) : (
                            <span className="faint">–</span>
                          )}
                        </td>
                        <td>
                          <span
                            className={`badge ${confidenceClass(p.confidence)}`}
                            title={
                              typeof p.confidence_score === "number"
                                ? `Confidence score ${p.confidence_score}/100`
                                : undefined
                            }
                          >
                            {p.confidence}
                            {typeof p.confidence_score === "number"
                              ? ` · ${p.confidence_score}`
                              : ""}
                          </span>
                        </td>
                        <td className="right">
                          <Link
                            className="btn small secondary analyse-btn"
                            to={`/matches/${p.match_id}`}
                            title={`Open the full analysis of ${p.home_team} vs ${p.away_team}`}
                          >
                            Analyse
                            <span aria-hidden="true">→</span>
                          </Link>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </Card>
          )}

          {(data.skipped ?? []).length > 0 && (
            <Card title="Skipped fixtures">
              <p className="muted small">
                These fixtures had too little stored history for the model to produce an honest
                estimate.
              </p>
              <table>
                <thead>
                  <tr>
                    <th>Match</th>
                    <th>Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {(data.skipped ?? []).map((s) => (
                    <tr key={s.match_id}>
                      <td>
                        <TeamCrest name={s.home_team} size={18} />
                        {s.home_team} <span className="faint">vs</span>{" "}
                        <TeamCrest name={s.away_team} size={18} />
                        {s.away_team}
                      </td>
                      <td className="muted">{s.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          )}

          <InfoBox>
            Predictions are model outputs from stored results, not guarantees. Value figures only
            appear when odds are available and never place a bet for you.
          </InfoBox>
        </>
      )}
    </div>
  );
}
