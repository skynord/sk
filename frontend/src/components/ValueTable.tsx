import type { ValueAssessment } from "../api/types";
import { pct, signedPct } from "../lib/format";

export function ValueTable({
  assessments,
  caveats,
}: {
  assessments: ValueAssessment[];
  caveats?: string[];
}) {
  const sorted = [...assessments].sort((a, b) => b.expected_value - a.expected_value);
  return (
    <div>
      <table>
        <thead>
          <tr>
            <th>Selection</th>
            <th className="num">Model p</th>
            <th className="num">Odds</th>
            <th className="num">Fair p</th>
            <th className="num">Edge</th>
            <th className="num">EV / unit</th>
            <th>Confidence</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((a) => (
            <tr key={`${a.market}-${a.selection}-${a.bookmaker}`}>
              <td>
                {a.selection}{" "}
                <span className="faint mono" style={{ fontSize: 11 }}>
                  {a.bookmaker}
                </span>
              </td>
              <td className="num">{pct(a.model_probability)}</td>
              <td className="num">
                <strong>{a.decimal_odds.toFixed(2)}</strong>
              </td>
              <td className="num">{a.market_fair_probability !== null ? pct(a.market_fair_probability) : "–"}</td>
              <td className="num">{signedPct(a.edge)}</td>
              <td className="num" style={{ color: a.is_positive_ev ? "var(--accent)" : "var(--danger)" }}>
                <strong>{signedPct(a.expected_value)}</strong>
              </td>
              <td>
                <span className={`badge ${a.is_positive_ev ? "pos" : ""}`}>{a.confidence}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {caveats && caveats.length > 0 && (
        <ul className="muted" style={{ margin: "12px 0 0", paddingLeft: 18, fontSize: 12 }}>
          {caveats.map((c, i) => (
            <li key={i}>{c}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
