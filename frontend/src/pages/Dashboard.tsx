import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { DataSource, League, MatchSummary, ProviderInfo } from "../api/types";
import { Card, ErrorBox, Loading, StatCard, WarnBox } from "../components/ui";
import { TeamCrest } from "../components/TeamCrest";
import { LeagueCrest } from "../components/LeagueCrest";
import { formatDateTime, isSynthetic } from "../lib/format";

export default function Dashboard() {
  const [leagues, setLeagues] = useState<League[] | null>(null);
  const [providers, setProviders] = useState<ProviderInfo[] | null>(null);
  const [sources, setSources] = useState<DataSource[] | null>(null);
  const [upcoming, setUpcoming] = useState<MatchSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api.leagues(),
      api.providers(),
      api.sources(),
      api.matches({ limit: 8, order: "kickoff_desc", status: "finished" }),
    ])
      .then(([lg, pr, src, matches]) => {
        setLeagues(lg);
        setProviders(pr);
        setSources(src);
        setUpcoming(matches.matches);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  if (error) return <ErrorBox message={error} />;
  if (!leagues || !providers || !sources || !upcoming) return <Loading />;

  const available = providers.filter((p) => p.available);
  const hasSynthetic = sources.some((s) => s.name === "sample" && s.status === "available");
  const totalMatches = leagues.reduce((sum, l) => sum + l.match_count, 0);

  return (
    <div>
      <div className="page-head">
        <h1>Dashboard</h1>
        <p>Overview of loaded competitions, data providers and the latest results.</p>
      </div>

      {totalMatches === 0 && (
        <WarnBox>
          No matches loaded yet. Import real results with{" "}
          <span className="mono">python -m app.cli ingest openfootball</span>, or enable a provider
          with an API key in <span className="mono">.env</span>.
        </WarnBox>
      )}
      {hasSynthetic && (
        <WarnBox>
          The <strong>sample</strong> provider is loaded with <strong>SYNTHETIC</strong> (generated)
          data for demonstration only. It is not real results — do not use it for real analysis.
        </WarnBox>
      )}

      <div className="grid cols-3" style={{ marginBottom: 18 }}>
        <StatCard label="Competitions" value={leagues.length} />
        <StatCard label="Matches stored" value={totalMatches} />
        <StatCard label="Providers available" value={`${available.length} / ${providers.length}`} />
      </div>

      <Card title="Competitions">
        {leagues.length === 0 ? (
          <p className="muted">No competitions yet.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Competition</th>
                <th>Country</th>
                <th className="num">Matches</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {leagues.map((l) => (
                <tr key={l.id}>
                  <td>
                    <LeagueCrest name={l.name} slug={l.slug} size={18} />
                    {l.name}
                  </td>
                  <td>
                    {isSynthetic(l.country) ? (
                      <span className="badge synthetic">{l.country}</span>
                    ) : (
                      <span className="muted">{l.country ?? "–"}</span>
                    )}
                  </td>
                  <td className="num">{l.match_count}</td>
                  <td className="right">
                    <Link to={`/matches?league_id=${l.id}`}>Matches →</Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title="Latest results">
        {upcoming.length === 0 ? (
          <p className="muted">No results yet.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Kickoff</th>
                <th>Match</th>
                <th className="num">Score</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {upcoming.map((m) => (
                <tr key={m.id}>
                  <td className="muted">{formatDateTime(m.kickoff_utc)}</td>
                  <td>
                    <div className="muted small" style={{ marginBottom: 2 }}>
                      <LeagueCrest name={m.league} size={14} />
                      {m.league}
                    </div>
                    <TeamCrest name={m.home_team.name} size={18} />
                    {m.home_team.name} <span className="faint">vs</span>{" "}
                    <TeamCrest name={m.away_team.name} size={18} />
                    {m.away_team.name}
                  </td>
                  <td className="num">
                    {m.score ? `${m.score.home}–${m.score.away}` : "–"}
                  </td>
                  <td className="right">
                    <Link to={`/matches/${m.id}`}>Analyse →</Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title="Provider status">
        <table>
          <thead>
            <tr>
              <th>Provider</th>
              <th>Kind</th>
              <th>Status</th>
              <th>Note</th>
            </tr>
          </thead>
          <tbody>
            {providers.map((p) => (
              <tr key={p.name}>
                <td className="mono">{p.name}</td>
                <td className="muted">{p.kind}</td>
                <td>
                  <span className={`badge ${p.available ? "ok" : "warn"}`}>
                    {p.available ? "available" : "unavailable"}
                  </span>
                </td>
                <td className="muted">{p.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
