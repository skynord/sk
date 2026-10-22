import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { League, UpcomingPrediction, UpcomingPredictions } from "../api/types";
import { Card, ErrorBox, InfoBox, Loading, StatCard, WarnBox } from "../components/ui";
import { TeamCrest } from "../components/TeamCrest";
import { LeagueCrest } from "../components/LeagueCrest";
import { formatDateTime, pct, confidenceTone } from "../lib/format";

const SELECT_LABEL: Record<string, string> = {
  home: "Home win",
  draw: "Draw",
  away: "Away win",
};

function pickLabel(prediction: UpcomingPrediction): string {
  const selection = prediction.pick?.selection;
  if (!selection) return "–";
  if (selection === "home") return prediction.home_team;
  if (selection === "away") return prediction.away_team;
  return SELECT_LABEL[selection] ?? selection;
}

const POLL_MS = 2500;

type SortKey = "confidence" | "kickoff" | "competition";

const SORT_CHOICES: { key: SortKey; label: string; hint: string }[] = [
  { key: "confidence", label: "Confidence", hint: "Most confident first" },
  { key: "kickoff", label: "Kickoff", hint: "Soonest first" },
  { key: "competition", label: "Competition", hint: "Grouped by competition" },
];

const MIN_CONFIDENCE = [
  { value: 0, label: "Any confidence" },
  { value: 30, label: "Low and up (30+)" },
  { value: 48, label: "Moderate and up (48+)" },
  { value: 66, label: "High and up (66+)" },
  { value: 85, label: "Very high only (85+)" },
];

function confidenceOf(prediction: UpcomingPrediction): number {
  if (prediction.insufficient_data?.length) return -1;
  return typeof prediction.confidence_score === "number" ? prediction.confidence_score : -1;
}

export default function Predictions() {
  const [leagues, setLeagues] = useState<League[]>([]);
  const [data, setData] = useState<UpcomingPredictions | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [leagueId, setLeagueId] = useState<number | undefined>(undefined);
  const [days, setDays] = useState(14);
  const [includeLive, setIncludeLive] = useState(true);
  const [withFixtures, setWithFixtures] = useState<Set<string>>(new Set());
  const [sortBy, setSortBy] = useState<SortKey>("confidence");
  const [minConfidence, setMinConfidence] = useState(0);
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
          if (leagueId === undefined && res.status === "ready") {
            setWithFixtures(
              new Set(
                (res.predictions ?? [])
                  .map((p) => p.league)
                  .filter((name): name is string => Boolean(name)),
              ),
            );
          }
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

  const predictions = data?.predictions ?? [];
  const shown = useMemo(() => {
    const byConfidence = (a: UpcomingPrediction, b: UpcomingPrediction) =>
      confidenceOf(b) - confidenceOf(a) ||
      (b.pick?.probability ?? 0) - (a.pick?.probability ?? 0) ||
      a.kickoff_utc.localeCompare(b.kickoff_utc);
    const rows = predictions.filter((p) => confidenceOf(p) >= minConfidence);
    return [...rows].sort((a, b) => {
      if (sortBy === "confidence") return byConfidence(a, b);
      if (sortBy === "kickoff") {
        return a.kickoff_utc.localeCompare(b.kickoff_utc) || byConfidence(a, b);
      }
      return (
        (a.league ?? "").localeCompare(b.league ?? "") ||
        a.kickoff_utc.localeCompare(b.kickoff_utc) ||
        byConfidence(a, b)
      );
    });
  }, [predictions, sortBy, minConfidence]);

  const running = data?.status === "running";
  const selected = leagues.find((l) => l.id === leagueId);
  const fixturesKnown = withFixtures.size > 0;
  const withUpcoming = leagues.filter((l) => withFixtures.has(l.name));
  const historyOnly = leagues.filter((l) => !withFixtures.has(l.name) && l.match_count > 0);

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
              {fixturesKnown ? (
                <>
                  {withUpcoming.length > 0 && (
                    <optgroup label="With fixtures in the window">
                      {withUpcoming.map((l) => (
                        <option key={l.id} value={l.id}>
                          {l.name}
                        </option>
                      ))}
                    </optgroup>
                  )}
                  {historyOnly.length > 0 && (
                    <optgroup label="History only — no fixtures loaded">
                      {historyOnly.map((l) => (
                        <option key={l.id} value={l.id}>
                          {l.name} ({l.match_count} results)
                        </option>
                      ))}
                    </optgroup>
                  )}
                </>
              ) : (
                leagues.map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.name}
                  </option>
                ))
              )}
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

          {(data.predicted ?? 0) === 0 &&
            (selected && selected.match_count > 0 ? (
              <WarnBox>
                <strong>{selected.name}</strong> holds {selected.match_count} finished matches and
                no fixture in this window, so there is nothing to predict yet. Its results are all
                historical: the bot only predicts matches stored as{" "}
                <span className="mono">scheduled</span> or <span className="mono">live</span>, and no
                fixture list for its current season has been imported.{" "}
                <Link to={`/matches?league_id=${selected.id}`}>See its results</Link>.
              </WarnBox>
            ) : (
              <WarnBox>
                No upcoming fixtures were found in the window. The bot predicts fixtures that are
                already in the database (status <span className="mono">scheduled</span> or{" "}
                <span className="mono">live</span>). If every stored match is historical, import
                upcoming fixtures first, then run the bot again.
              </WarnBox>
            ))}

          {predictions.length > 0 && shown.length === 0 && (
            <WarnBox>
              No fixture reaches a confidence of {minConfidence}/100. The model is least sure about
              the matches with little history, so lower the minimum to see them again.
            </WarnBox>
          )}

          {shown.length > 0 && (
            <Card title="Predicted fixtures">
              <div className="pred-toolbar">
                <span className="muted small">Order by</span>
                <div className="pred-order" role="group" aria-label="Order the predicted fixtures">
                  {SORT_CHOICES.map((choice) => {
                    const active = sortBy === choice.key;
                    return (
                      <button
                        key={choice.key}
                        type="button"
                        className={`btn small${active ? "" : " secondary"}`}
                        aria-pressed={active}
                        title={choice.hint}
                        onClick={() => setSortBy(choice.key)}
                      >
                        {choice.label}
                        {active && <span aria-hidden="true">{choice.key === "confidence" ? " ↓" : " ↑"}</span>}
                      </button>
                    );
                  })}
                </div>
                <label className="pred-min">
                  <span className="muted small">At least</span>
                  <select
                    value={minConfidence}
                    onChange={(e) => setMinConfidence(Number(e.target.value))}
                  >
                    {MIN_CONFIDENCE.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
                <span className="faint small pred-count">
                  {shown.length === predictions.length
                    ? `${predictions.length} fixtures`
                    : `${shown.length} of ${predictions.length} fixtures`}
                </span>
              </div>

              <table className="fixtures-table">
                <thead>
                  <tr>
                    <th>
                      <button type="button" className="th-sort" onClick={() => setSortBy("kickoff")}>
                        Kickoff
                        <span className="faint small">
                          {sortBy === "kickoff" ? "↑" : ""}
                        </span>
                      </button>
                    </th>
                    <th>Competition</th>
                    <th>
                      Match <span className="faint small">(ML)</span>
                    </th>
                    <th>Pick</th>
                    <th className="num">Score</th>
                    <th className="num" title="Both teams to score">
                      BTTS
                    </th>
                    <th>
                      <button
                        type="button"
                        className="th-sort"
                        onClick={() => setSortBy("confidence")}
                      >
                        Confidence
                        <span className="faint small">
                          {sortBy === "confidence" ? "↓" : ""}
                        </span>
                      </button>
                    </th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((p) => {
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
                          {p.insufficient_data?.length ? (
                            <span
                              className="muted"
                              title={`No completed match for ${p.insufficient_data.join(", ")}: the model only holds a league-average prior, so no side is favoured.`}
                            >
                              sin datos
                            </span>
                          ) : p.pick ? (
                            <>
                              {pickLabel(p)} <strong>{pct(p.pick.probability, 0)}</strong>
                            </>
                          ) : (
                            <span className="faint">–</span>
                          )}
                        </td>
                        <td className="num">
                          {p.insufficient_data?.length ? (
                            <span className="faint">–</span>
                          ) : (
                            <>
                              <strong>{p.predicted_score.score}</strong>{" "}
                              <span className="faint">
                                {pct(p.predicted_score.probability, 0)}
                              </span>
                            </>
                          )}
                        </td>
                        <td
                          className="num"
                          title={
                            typeof btts === "number" && !p.insufficient_data?.length
                              ? `Both teams to score: ${pct(btts, 0)}`
                              : "No BTTS estimate"
                          }
                        >
                          {typeof btts === "number" && !p.insufficient_data?.length ? (
                            <span className={btts >= 0.5 ? "mono" : "muted"}>{pct(btts, 0)}</span>
                          ) : (
                            <span className="faint">–</span>
                          )}
                        </td>
                        <td>
                          {typeof p.confidence_score === "number" && !p.insufficient_data?.length ? (
                            <span
                              className={`conf-score conf-${confidenceTone(p.confidence)}`}
                              title={`Confidence ${p.confidence}: ${p.confidence_score}/100`}
                            >
                              {p.confidence_score}
                              <span className="conf-den">/100</span>
                            </span>
                          ) : (
                            <span className="faint">–</span>
                          )}
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
