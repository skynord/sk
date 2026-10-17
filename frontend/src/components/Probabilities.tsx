import { pct } from "../lib/format";

const COLORS: Record<string, string> = {
  home: "var(--home)",
  draw: "var(--draw)",
  away: "var(--away)",
  yes: "var(--accent)",
  no: "var(--draw)",
  over: "var(--accent)",
  under: "var(--draw)",
};

export function OneXTwo({ probs }: { probs: Record<string, number> }) {
  return (
    <div className="ox2">
      {(["home", "draw", "away"] as const).map((key) => (
        <div key={key} className={`cell ${key}`}>
          <div className="name">{key[0].toUpperCase() + key.slice(1)}</div>
          <div className="prob">{pct(probs[key], 1)}</div>
        </div>
      ))}
    </div>
  );
}

export function ProbBars({
  entries,
}: {
  entries: Array<{ label: string; value: number; color?: string }>;
}) {
  return (
    <div>
      {entries.map((entry) => (
        <div className="bar-row" key={entry.label}>
          <div className="bar-label">{entry.label}</div>
          <div className="bar-wrap">
            <div className="bar">
              <span
                style={{
                  width: `${Math.max(0, Math.min(1, entry.value)) * 100}%`,
                  background: entry.color ?? COLORS[entry.label] ?? "var(--accent-2)",
                }}
              />
            </div>
          </div>
          <div className="bar-value">{pct(entry.value, 1)}</div>
        </div>
      ))}
    </div>
  );
}

export function TwoWayBar({
  label,
  over,
  under,
  overLabel = "Over",
  underLabel = "Under",
}: {
  label: string;
  over: number;
  under: number;
  overLabel?: string;
  underLabel?: string;
}) {
  return (
    <div className="bar-row">
      <div className="bar-label">{label}</div>
      <div className="bar-wrap">
        <div className="bar">
          <span style={{ width: `${over * 100}%`, background: "var(--accent)" }} title={`${overLabel} ${pct(over)}`} />
          <span style={{ width: `${under * 100}%`, background: "var(--draw)" }} title={`${underLabel} ${pct(under)}`} />
        </div>
      </div>
      <div className="bar-value">{pct(over, 0)}</div>
    </div>
  );
}
