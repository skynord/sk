import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import type { BacktestResponse, League } from "../api/types";
import { Card, ErrorBox, Loading, WarnBox } from "../components/ui";
import { ReliabilityChart } from "../components/Charts";
import { num } from "../lib/format";

export default function Backtests() {
  const [leagues, setLeagues] = useState<League[]>([]);
  const [leagueId, setLeagueId] = useState<number | "">("");
  const [minTrain, setMinTrain] = useState(40);
  const [retrainEvery, setRetrainEvery] = useState(10);
  const [halflife, setHalflife] = useState(180);
  const [result, setResult] = useState<BacktestResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .leagues()
      .then((ls) => {
        setLeagues(ls);
        const withData = ls.find((l) => l.match_count > 60) ?? ls[0];
        if (withData) setLeagueId(withData.id);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  async function run() {
    if (leagueId === "") return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.backtest({
        league_id: Number(leagueId),
        min_train: minTrain,
        retrain_every: retrainEvery,
        halflife_days: halflife,
      });
      setResult(res);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : (e as Error).message);
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <div className="page-head">
        <h1>Model &amp; validation</h1>
        <p>
          Walk-forward backtesting trains only on matches played before each fixture, so there is no
          look-ahead leakage. Lower Brier and log loss are better; compare each to its base-rate
          baseline.
        </p>
      </div>

      {error && <ErrorBox message={error} />}

      <Card title="Run a backtest">
        <div className="row">
          <label>
            Competition{" "}
            <select value={leagueId} onChange={(e) => setLeagueId(Number(e.target.value))}>
              {leagues.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name} ({l.match_count})
                </option>
              ))}
            </select>
          </label>
          <label>
            Min train{" "}
            <input
              type="number"
              min={10}
              value={minTrain}
              onChange={(e) => setMinTrain(Number(e.target.value))}
              style={{ width: 90 }}
            />
          </label>
          <label>
            Retrain every{" "}
            <input
              type="number"
              min={1}
              value={retrainEvery}
              onChange={(e) => setRetrainEvery(Number(e.target.value))}
              style={{ width: 90 }}
            />
          </label>
          <label>
            Half-life (days){" "}
            <input
              type="number"
              min={7}
              value={halflife}
              onChange={(e) => setHalflife(Number(e.target.value))}
              style={{ width: 90 }}
            />
          </label>
          <button className="btn" onClick={run} disabled={loading || leagueId === ""}>
            {loading ? "Backtesting…" : "Run backtest"}
          </button>
        </div>
      </Card>

      {loading && <Loading label="Running walk-forward validation…" />}

      {result && !loading && (
        <>
          <div className="row" style={{ marginBottom: 14 }}>
            <span className="badge">Evaluated {result.n_eval} matches</span>
            <span className="badge">First {result.n_train} used for training</span>
          </div>
          {result.warnings.map((w, i) => (
            <WarnBox key={i}>{w}</WarnBox>
          ))}

          <Card title="Scoring rules vs baseline">
            <table>
              <thead>
                <tr>
                  <th>Market</th>
                  <th className="num">n</th>
                  <th className="num">Brier</th>
                  <th className="num">Baseline Brier</th>
                  <th className="num">Improvement</th>
                  <th className="num">Log loss</th>
                  <th className="num">Accuracy</th>
                </tr>
              </thead>
              <tbody>
                {result.markets.map((m) => (
                  <tr key={m.market}>
                    <td>{m.market}</td>
                    <td className="num">{m.n}</td>
                    <td className="num">{num(m.brier, 4)}</td>
                    <td className="num muted">{num(m.baseline_brier, 4)}</td>
                    <td className="num" style={{ color: m.improvement_brier > 0 ? "var(--accent)" : "var(--danger)" }}>
                      {num(m.improvement_brier, 4)}
                    </td>
                    <td className="num">{num(m.log_loss, 4)}</td>
                    <td className="num">{m.accuracy !== null ? `${(m.accuracy * 100).toFixed(1)}%` : "–"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>

          <div className="grid cols-2">
            {result.markets
              .filter((m) => m.reliability.some((b) => b.n > 0))
              .map((m) => (
                <Card key={m.market} title={`Reliability — ${m.market}`}>
                  <ReliabilityChart bins={m.reliability} />
                  <p className="faint" style={{ fontSize: 12, marginBottom: 0 }}>
                    Points near the diagonal are well calibrated; larger circles hold more matches.
                  </p>
                </Card>
              ))}
          </div>
        </>
      )}
    </div>
  );
}
