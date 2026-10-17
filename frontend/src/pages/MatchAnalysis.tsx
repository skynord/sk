import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { AnalysisOptions, AnalysisReport } from "../api/types";
import { Card, ErrorBox, Loading, StatCard, WarnBox } from "../components/ui";
import { TeamCrest } from "../components/TeamCrest";
import { LeagueCrest } from "../components/LeagueCrest";
import { OneXTwo, ProbBars, TwoWayBar } from "../components/Probabilities";
import { DistributionChart } from "../components/Charts";
import { FormPanel } from "../components/FormPanel";
import { ValueTable } from "../components/ValueTable";
import { formatDate, isSynthetic, num, pct, capitalize, confidenceClass } from "../lib/format";

interface OddsRow {
  selection: string;
  odds: string;
}

export default function MatchAnalysis() {
  const { id } = useParams();
  const matchId = Number(id);

  const [report, setReport] = useState<AnalysisReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [sims, setSims] = useState(10000);
  const [xgWeight, setXgWeight] = useState(0);
  const [halflife, setHalflife] = useState<number | "">("");
  const [odds, setOdds] = useState<OddsRow[]>([]);

  const run = useCallback(
    async (extra?: Partial<AnalysisOptions>) => {
      setLoading(true);
      setError(null);
      try {
        const manual = odds
          .filter((o) => o.odds && !Number.isNaN(Number(o.odds)) && Number(o.odds) > 1)
          .map((o) => ({ market: "1x2", selection: o.selection, decimal_odds: Number(o.odds) }));
        const options: AnalysisOptions = {
          monte_carlo_simulations: sims,
          xg_weight: xgWeight,
          halflife_days: halflife === "" ? null : Number(halflife),
          include_asian_handicap: true,
          persist: false,
          manual_odds: manual,
          ...extra,
        };
        const result = await api.analysis(matchId, options);
        setReport(result);
      } catch (e) {
        setError(e instanceof ApiError ? e.message : (e as Error).message);
      } finally {
        setLoading(false);
      }
    },
    [matchId, sims, xgWeight, halflife, odds],
  );

  useEffect(() => {
    api
      .match(matchId)
      .then((d) => {
        const existing = d.odds.filter((o) => o.market === "1x2");
        if (existing.length >= 3) {
          setOdds(
            ["home", "draw", "away"].map((sel) => ({
              selection: sel,
              odds: String(existing.find((o) => o.selection === sel)?.decimal_odds ?? ""),
            })),
          );
        } else {
          setOdds(["home", "draw", "away"].map((sel) => ({ selection: sel, odds: "" })));
        }
      })
      .catch((e: Error) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [matchId]);

  useEffect(() => {
    run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [matchId]);

  if (error && !report) return <ErrorBox message={error} />;
  if (!report) return <Loading label="Fitting model and simulating…" />;

  const { match, markets, expected_goals } = report;
  const synthetic = isSynthetic(match.league.country);

  return (
    <div>
      <div className="page-head">
        <div className="row">
          <h1 style={{ marginBottom: 0 }}>
            <TeamCrest name={match.home_team.name} size={26} />
            {match.home_team.name} <span className="faint">vs</span>{" "}
            <TeamCrest name={match.away_team.name} size={26} />
            {match.away_team.name}
          </h1>
          {synthetic ? (
            <span className="badge synthetic">SYNTHETIC DATA</span>
          ) : (
            <span className="badge">{match.status}</span>
          )}
        </div>
        <p>
          <LeagueCrest name={match.league.name} slug={match.league.slug} size={16} />
          {match.league.name}
          {match.season ? ` · ${match.season.label}` : ""} · {formatDate(match.kickoff_utc)}
          {match.round ? ` · ${match.round}` : ""}
        </p>
      </div>

      {synthetic && (
        <WarnBox>
          This match is from the synthetic sample dataset. Probabilities are real model output, but
          the underlying results are generated and must not be used for real decisions.
        </WarnBox>
      )}
      {report.scope?.cross_competition && (
        <WarnBox>
          {report.scope.note ??
            "This competition's own history is thin; a sibling competition was used."}{" "}
          Clubs were matched by name across providers
          {report.scope.model_home_team && report.scope.model_away_team
            ? ` (${report.scope.model_home_team} / ${report.scope.model_away_team}).`
            : "."}
        </WarnBox>
      )}
      {error && <ErrorBox message={error} />}

      <Card title="Model options">
        <div className="row">
          <label>
            Simulations{" "}
            <input
              type="number"
              min={1000}
              step={1000}
              value={sims}
              onChange={(e) => setSims(Number(e.target.value))}
              style={{ width: 110 }}
            />
          </label>
          <label>
            xG weight{" "}
            <input
              type="number"
              min={0}
              max={1}
              step={0.1}
              value={xgWeight}
              onChange={(e) => setXgWeight(Number(e.target.value))}
              style={{ width: 80 }}
            />
          </label>
          <label>
            Half-life (days){" "}
            <input
              type="number"
              min={7}
              placeholder="180"
              value={halflife}
              onChange={(e) => setHalflife(e.target.value === "" ? "" : Number(e.target.value))}
              style={{ width: 100 }}
            />
          </label>
          <button className="btn" onClick={() => run()} disabled={loading}>
            {loading ? "Running…" : "Run analysis"}
          </button>
        </div>
        <p className="faint" style={{ fontSize: 12, marginBottom: 0 }}>
          xG blending only applies when the provider supplies xG. Currently{" "}
          {report.model.xg_used ? "in use" : "not used"}.
        </p>
      </Card>

      <div className="grid cols-4" style={{ marginBottom: 18 }}>
        <StatCard label="Expected goals" value={<strong>{num(expected_goals.home)} – {num(expected_goals.away)}</strong>} sub={`total ${num(expected_goals.total)}`} />
        <StatCard
          label="Most likely score"
          value={<strong>{report.predicted_score.score}</strong>}
          sub={pct(report.predicted_score.probability)}
        />
        <StatCard
          label="Home win"
          value={<strong>{pct(markets.one_x_two.home)}</strong>}
          sub={`sim ${pct(report.simulation.one_x_two.home)}`}
        />
        <StatCard
          label="Confidence"
          value={capitalize(report.confidence.level)}
          sub={
            <>
              {typeof report.confidence.score === "number" && (
                <span className={`badge ${confidenceClass(report.confidence.level)}`}>
                  {report.confidence.score}/100
                </span>
              )}{" "}
              {report.confidence.n_training_matches} training matches
            </>
          }
        />
      </div>

      {report.confidence.notes.length > 0 && (
        <div className="alert info" style={{ marginTop: -4 }}>
          <strong>Confidence.</strong> {report.confidence.notes.join(" ")}
        </div>
      )}

      {report.opportunities.length > 0 && (
        <Card title="Best statistical opportunities">
          <p className="muted" style={{ marginTop: 0 }}>
            The model&apos;s highest-probability outcomes across the markets, with their
            margin-free fair price. This is a ranked reading of the model&apos;s own output —
            not a tip, and not a claim of an edge over any real bookmaker price.
          </p>
          <table>
            <thead>
              <tr>
                <th>Outcome</th>
                <th>Market</th>
                <th className="num">Probability</th>
                <th className="num">Fair odds</th>
              </tr>
            </thead>
            <tbody>
              {report.opportunities.map((o, i) => (
                <tr key={`${o.market}-${o.selection}-${i}`}>
                  <td>{o.label}</td>
                  <td className="faint mono" style={{ fontSize: 11 }}>
                    {o.market}
                  </td>
                  <td className="num">{pct(o.probability)}</td>
                  <td className="num">
                    <strong>{o.fair_odds !== null ? o.fair_odds.toFixed(2) : "–"}</strong>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      <div className="grid cols-2">
        <Card title="Result probabilities (1X2)">
          <OneXTwo probs={markets.one_x_two} />
          <div style={{ marginTop: 14 }}>
            <ProbBars
              entries={[
                { label: "Home", value: markets.one_x_two.home, color: "var(--home)" },
                { label: "Draw", value: markets.one_x_two.draw, color: "var(--draw)" },
                { label: "Away", value: markets.one_x_two.away, color: "var(--away)" },
              ]}
            />
          </div>
          <p className="faint" style={{ fontSize: 12 }}>
            Monte-Carlo standard error on home win: ±{num(report.confidence.monte_carlo_std_error, 3)}{" "}
            ({report.simulation.n_simulations.toLocaleString()} simulations).
          </p>
        </Card>

        <Card title="Double chance & both teams to score">
          <ProbBars
            entries={Object.entries(markets.double_chance).map(([k, v]) => ({
              label: k.replace(/_/g, " "),
              value: v,
            }))}
          />
          <h3 style={{ marginTop: 16 }}>Both teams to score</h3>
          <TwoWayBar label="BTTS" over={markets.btts.yes} under={markets.btts.no} overLabel="Yes" underLabel="No" />
        </Card>

        <Card title="Total goals">
          {Object.entries(markets.totals).map(([line, o]) => (
            <TwoWayBar key={line} label={`O/U ${line}`} over={o.over} under={o.under} />
          ))}
          <h3 style={{ marginTop: 16 }}>Total-goals distribution</h3>
          <DistributionChart
            data={markets.total_goals_distribution.map((d) => ({
              label: d.is_open_ended ? `${d.goals}+` : String(d.goals),
              value: d.probability,
            }))}
          />
        </Card>

        <Card title="Correct score (top 8)">
          <DistributionChart
            data={markets.correct_scores.map((s) => ({ label: s.score, value: s.probability }))}
          />
        </Card>

        <Card title={`Team totals — ${match.home_team.name}`}>
          {Object.entries(markets.team_totals.home).map(([line, o]) => (
            <TwoWayBar key={line} label={`Over ${line}`} over={o.over} under={o.under} />
          ))}
        </Card>

        <Card title={`Team totals — ${match.away_team.name}`}>
          {Object.entries(markets.team_totals.away).map(([line, o]) => (
            <TwoWayBar key={line} label={`Over ${line}`} over={o.over} under={o.under} />
          ))}
        </Card>

        <Card title="Half-time markets">
          {markets.half_time.available &&
          markets.half_time.one_x_two &&
          markets.half_time.totals &&
          markets.half_time.btts ? (
            <>
              <OneXTwo probs={markets.half_time.one_x_two} />
              <div style={{ marginTop: 14 }}>
                {Object.entries(markets.half_time.totals).map(([line, o]) => (
                  <TwoWayBar key={line} label={`HT O/U ${line}`} over={o.over} under={o.under} />
                ))}
              </div>
              <h3 style={{ marginTop: 16 }}>Both teams to score (HT)</h3>
              <TwoWayBar
                label="HT BTTS"
                over={markets.half_time.btts.yes}
                under={markets.half_time.btts.no}
                overLabel="Yes"
                underLabel="No"
              />
              {markets.half_time.note && (
                <p className="faint" style={{ fontSize: 12, marginBottom: 0 }}>
                  {markets.half_time.note}
                </p>
              )}
            </>
          ) : (
            <p className="muted">{markets.half_time.note ?? "Half-time markets unavailable."}</p>
          )}
        </Card>

        <Card title="Fair odds (margin-free)">
          <p className="muted" style={{ marginTop: 0 }}>
            Fair decimal odds for each model probability (1 ÷ probability). Compare against a
            real quoted price to judge it; they carry no bookmaker margin.
          </p>
          <h3>1X2</h3>
          <table>
            <tbody>
              {Object.entries(markets.fair_odds.one_x_two).map(([key, value]) => (
                <tr key={key}>
                  <td>{key[0].toUpperCase() + key.slice(1)}</td>
                  <td className="num">{value !== null ? value.toFixed(2) : "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <h3 style={{ marginTop: 14 }}>Over / Under</h3>
          <table>
            <thead>
              <tr>
                <th>Line</th>
                <th className="num">Over</th>
                <th className="num">Under</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(markets.fair_odds.totals).map(([line, o]) => (
                <tr key={line}>
                  <td className="mono">{line}</td>
                  <td className="num">{o.over !== null ? o.over.toFixed(2) : "–"}</td>
                  <td className="num">{o.under !== null ? o.under.toFixed(2) : "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <h3 style={{ marginTop: 14 }}>Both teams to score</h3>
          <table>
            <tbody>
              {Object.entries(markets.fair_odds.btts).map(([key, value]) => (
                <tr key={key}>
                  <td>{key[0].toUpperCase() + key.slice(1)}</td>
                  <td className="num">{value !== null ? value.toFixed(2) : "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>

      {markets.asian_handicap && (
        <Card title={`Asian handicap (${match.home_team.name} line)`}>
          <table>
            <thead>
              <tr>
                <th>Line</th>
                <th className="num">Win</th>
                <th className="num">Push</th>
                <th className="num">Lose</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(markets.asian_handicap).map(([line, o]) => (
                <tr key={line}>
                  <td className="mono">{line}</td>
                  <td className="num">{pct(o.win)}</td>
                  <td className="num">{pct(o.push)}</td>
                  <td className="num">{pct(o.lose)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      <Card title="Value analysis (your odds)">
        <p className="muted" style={{ marginTop: 0 }}>
          Enter the 1X2 decimal odds you are being offered. This only does arithmetic on the model's
          probabilities and your numbers — it never places a bet.
        </p>
        <div className="row" style={{ marginBottom: 12 }}>
          {odds.map((o, i) => (
            <label key={o.selection}>
              {o.selection[0].toUpperCase() + o.selection.slice(1)}{" "}
              <input
                type="number"
                step="0.01"
                min="1.01"
                placeholder="e.g. 2.10"
                value={o.odds}
                onChange={(e) => {
                  const next = [...odds];
                  next[i] = { ...o, odds: e.target.value };
                  setOdds(next);
                }}
                style={{ width: 96 }}
              />
            </label>
          ))}
          <button className="btn secondary small" onClick={() => run()} disabled={loading}>
            Recompute with odds
          </button>
        </div>
        {report.value.available && report.value.assessments ? (
          <ValueTable assessments={report.value.assessments} caveats={report.value.caveats} />
        ) : (
          <p className="muted">{report.value.note ?? "No odds supplied."}</p>
        )}
      </Card>

      <div className="grid cols-2">
        <Card title={`Form — ${match.home_team.name}`}>
          <FormPanel bundle={report.form.home} />
        </Card>
        <Card title={`Form — ${match.away_team.name}`}>
          <FormPanel bundle={report.form.away} />
        </Card>
      </div>

      <Card title="Head-to-head">
        {report.head_to_head.length === 0 ? (
          <p className="muted">No previous meetings recorded.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Venue</th>
                <th className="num">Score</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              {report.head_to_head.map((h, i) => (
                <tr key={i}>
                  <td className="muted">{formatDate(h.date)}</td>
                  <td>{h.is_home ? "Home" : "Away"}</td>
                  <td className="num">
                    {h.goals_for}–{h.goals_against}
                  </td>
                  <td>
                    <span className={`wdl`}>
                      <span className={h.result.toLowerCase()}>{h.result}</span>
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title="Findings">
        <div className="findings">
          {report.findings.map((f, i) => (
            <div className="finding" key={i}>
              <strong>{f.title}</strong>
              <div>{f.detail}</div>
              <div className="evidence">{f.evidence.join(" · ")}</div>
            </div>
          ))}
        </div>
      </Card>

      <div className="grid cols-2">
        <Card title="Attack / defence multipliers">
          <table>
            <thead>
              <tr>
                <th>Team</th>
                <th className="num">Attack</th>
                <th className="num">Defence</th>
                <th className="num">Net</th>
              </tr>
            </thead>
            <tbody>
              {report.ratings.table.map((t) => (
                <tr key={t.team}>
                  <td>{t.team}</td>
                  <td className="num">{num(t.attack_multiplier)}</td>
                  <td className="num">{num(t.defence_multiplier)}</td>
                  <td className="num">{num(t.net_strength)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="faint" style={{ fontSize: 12, marginBottom: 0 }}>
            Multipliers are relative to the competition average (&gt;1 attack is above average;
            &gt;1 defence concedes less).
          </p>
        </Card>

        <Card title="Model &amp; provenance">
          <table>
            <tbody>
              <tr>
                <td>Model</td>
                <td className="mono">
                  {report.model.name} v{report.model.version}
                </td>
              </tr>
              <tr>
                <td>Training matches</td>
                <td className="num">{report.model.n_training_matches}</td>
              </tr>
              <tr>
                <td>Training window</td>
                <td className="mono">
                  {formatDate(report.model.training_start)} → {formatDate(report.model.training_end)}
                </td>
              </tr>
              <tr>
                <td>Home advantage</td>
                <td className="num">{num(report.model.params.home_advantage, 3)} (log)</td>
              </tr>
              <tr>
                <td>Low-score correlation (rho)</td>
                <td className="num">{num(report.model.params.rho, 3)}</td>
              </tr>
              <tr>
                <td>Converged</td>
                <td>
                  <span className={`badge ${report.model.converged ? "ok" : "bad"}`}>
                    {report.model.converged ? "yes" : "no"}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
          <h3 style={{ marginTop: 14 }}>Sources</h3>
          {report.provenance.match_sources.map((s, i) => (
            <div key={i} className="row" style={{ fontSize: 12 }}>
              <span className="mono">{s.provider}</span>
              {s.is_estimate && <span className="badge synthetic">estimate</span>}
              <span className="faint mono">{s.source_url}</span>
            </div>
          ))}
        </Card>
      </div>

      {report.limitations.length > 0 && (
        <Card title="Limitations">
          <ul className="muted" style={{ margin: 0, paddingLeft: 18 }}>
            {report.limitations.map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
        </Card>
      )}

      <div className="row">
        <Link to="/matches" className="btn secondary small">
          ← Back to matches
        </Link>
      </div>
    </div>
  );
}
