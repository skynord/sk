import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { League, UpcomingPrediction, UpcomingPredictions } from "../api/types";
import { Card, ErrorBox, InfoBox, Loading, StatCard, WarnBox } from "../components/ui";
import { TeamCrest } from "../components/TeamCrest";
import { LeagueCrest } from "../components/LeagueCrest";
import { formatDateTime, num, pct, signedPct, confidenceClass } from "../lib/format";

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
          detail="Fitting one model per competition — the first run takes a couple of minutes; later runs are instant."
        />
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
              <table>
                <thead>
                  <tr>
                    <th>Kickoff</th>
                    <th>Competition</th>
                    <th>Match</th>
                    <th>Pick</th>
                    <th className="num">1 / X / 2</th>
                    <th className="num">Score</th>
                    <th className="num">xG</th>
                    <th>Value</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {(data.predictions ?? []).map((p) => {
                    const ox2 = p.probabilities.one_x_two;
                    return (
                      <tr key={p.match_id}>
                        <td className="muted">{formatDateTime(p.kickoff_utc)}</td>
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
                          <TeamCrest name={p.home_team} size={18} />
                          {p.home_team} <span className="faint">vs</span>{" "}
                          <TeamCrest name={p.away_team} size={18} />
                          {p.away_team}
                        </td>
                        <td>
                          {pickLabel(p)} <strong>{pct(p.pick.probability, 0)}</strong>{" "}
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
                        <td className="num mono">
                          {pct(ox2.home, 0)} / {pct(ox2.draw, 0)} / {pct(ox2.away, 0)}
                        </td>
                        <td className="num">
                          <strong>{p.predicted_score.score}</strong>{" "}
                          <span className="faint">{pct(p.predicted_score.probability, 0)}</span>
                        </td>
                        <td className="num">
                          <strong>
                            {num(p.expected_goals.home, 2)}–{num(p.expected_goals.away, 2)}
                          </strong>
                        </td>
                        <td>
                          {p.best_value ? (
                            <span className={p.best_value.is_positive_ev ? "mono" : "muted"}>
                              {p.best_value.market}:{p.best_value.selection} @{" "}
                              {num(p.best_value.decimal_odds, 2)}{" "}
                              <strong>{signedPct(p.best_value.expected_value)}</strong>
                            </span>
                          ) : (
                            <span className="faint">no odds</span>
                          )}
                        </td>
                        <td className="right">
                          <Link to={`/matches/${p.match_id}`}>Analyse →</Link>
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
