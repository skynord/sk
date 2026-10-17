import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import type { DataSource, IngestResult, ProviderInfo } from "../api/types";
import { Card, ErrorBox, InfoBox, Loading } from "../components/ui";
import { formatDateTime } from "../lib/format";

export default function DataSources() {
  const [providers, setProviders] = useState<ProviderInfo[] | null>(null);
  const [sources, setSources] = useState<DataSource[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<IngestResult | null>(null);

  function refresh() {
    Promise.all([api.providers(), api.sources()])
      .then(([p, s]) => {
        setProviders(p);
        setSources(s);
      })
      .catch((e: Error) => setError(e.message));
  }

  useEffect(refresh, []);

  async function runIngest(name: string) {
    setBusy(name);
    setError(null);
    setResult(null);
    try {
      const res = await api.ingest(name);
      setResult(res);
      refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : (e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  if (error && !providers) return <ErrorBox message={error} />;
  if (!providers || !sources) return <Loading />;

  return (
    <div>
      <div className="page-head">
        <h1>Data sources</h1>
        <p>
          Providers are opt-in and respectful of each site's terms: football-data.co.uk CSVs are read
          from <span className="mono">data/raw</span> only when you put them there; HTML scraping is
          off by default; API providers need your own key.
        </p>
      </div>

      {error && <ErrorBox message={error} />}
      {result && (
        <InfoBox>
          <strong>{result.provider}</strong>: {result.status} — {result.matches_upserted} matches
          upserted from {result.records_in} records. {result.reason}
          {result.warnings.length > 0 && (
            <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
              {result.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          )}
        </InfoBox>
      )}

      <Card title="Providers">
        <table>
          <thead>
            <tr>
              <th>Provider</th>
              <th>Kind</th>
              <th>Capabilities</th>
              <th>Cost / licence</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {providers.map((p) => (
              <tr key={p.name}>
                <td className="mono">{p.name}</td>
                <td className="muted">{p.kind}</td>
                <td className="pill-list">
                  {p.capabilities.map((c) => (
                    <span key={c} className="badge">
                      {c}
                    </span>
                  ))}
                </td>
                <td className="muted" style={{ fontSize: 12 }}>
                  {p.cost_note}
                  {p.requires_key && <div className="faint">requires API key</div>}
                </td>
                <td>
                  <span className={`badge ${p.available ? "ok" : "warn"}`}>
                    {p.available ? "available" : "unavailable"}
                  </span>
                  <div className="faint" style={{ fontSize: 11 }}>
                    {p.reason}
                  </div>
                </td>
                <td className="right">
                  <button
                    className="btn secondary small"
                    disabled={!p.available || busy === p.name}
                    onClick={() => runIngest(p.name)}
                  >
                    {busy === p.name ? "Importing…" : "Import"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <Card title="Registered sources">
        <table>
          <thead>
            <tr>
              <th>Source</th>
              <th>Kind</th>
              <th>Enabled</th>
              <th>Status</th>
              <th>Last checked</th>
            </tr>
          </thead>
          <tbody>
            {sources.map((s) => (
              <tr key={s.name}>
                <td className="mono">{s.name}</td>
                <td className="muted">{s.kind}</td>
                <td>
                  <span className={`badge ${s.enabled ? "ok" : ""}`}>{s.enabled ? "yes" : "no"}</span>
                </td>
                <td>
                  <span className={`badge ${s.status === "available" ? "ok" : "warn"}`}>{s.status}</span>
                  <div className="faint" style={{ fontSize: 11 }}>
                    {s.message}
                  </div>
                </td>
                <td className="muted">{formatDateTime(s.last_checked_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="faint" style={{ fontSize: 12, marginBottom: 0 }}>
          To enable more providers set <span className="mono">FA_ENABLED_PROVIDERS</span> and provide
          keys in the backend <span className="mono">.env</span> (see .env.example).
        </p>
      </Card>
    </div>
  );
}
