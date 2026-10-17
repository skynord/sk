import { pct } from "../lib/format";

export function DistributionChart({
  data,
  height = 140,
}: {
  data: Array<{ label: string; value: number; highlight?: boolean; title?: string }>;
  height?: number;
}) {
  const max = Math.max(...data.map((d) => d.value), 0.0001);
  return (
    <div style={{ display: "flex", alignItems: "flex-end", gap: 6, height, paddingTop: 8 }}>
      {data.map((d) => (
        <div
          key={d.label}
          style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", height: "100%" }}
          title={d.title ?? `${d.label}: ${pct(d.value)}`}
        >
          <div style={{ flex: 1, width: "100%", display: "flex", alignItems: "flex-end" }}>
            <div
              style={{
                width: "100%",
                height: `${(d.value / max) * 100}%`,
                background: d.highlight ? "var(--accent)" : "var(--accent-2)",
                borderRadius: "4px 4px 0 0",
                opacity: d.highlight ? 1 : 0.75,
                minHeight: 2,
              }}
            />
          </div>
          <div style={{ fontSize: 10, color: "var(--text-faint)", marginTop: 4 }}>{d.label}</div>
          <div style={{ fontSize: 10, color: "var(--text-dim)" }}>{pct(d.value, 0)}</div>
        </div>
      ))}
    </div>
  );
}

export function ReliabilityChart({
  bins,
}: {
  bins: Array<{ mean_predicted: number | null; observed_frequency: number | null; n: number }>;
}) {
  const size = 240;
  const pad = 26;
  const points = bins.filter((b) => b.n > 0 && b.mean_predicted !== null && b.observed_frequency !== null);
  const maxN = Math.max(...points.map((p) => p.n), 1);
  const scale = (value: number) => pad + value * (size - 2 * pad);

  return (
    <svg width="100%" viewBox={`0 0 ${size} ${size}`} style={{ maxWidth: 320 }}>
      <rect x={pad} y={pad} width={size - 2 * pad} height={size - 2 * pad} fill="none" stroke="var(--border)" />
      <line x1={pad} y1={size - pad} x2={size - pad} y2={pad} stroke="var(--text-faint)" strokeDasharray="4 4" />
      {points.map((p, i) => (
        <circle
          key={i}
          cx={scale(p.mean_predicted as number)}
          cy={size - scale(p.observed_frequency as number)}
          r={3 + 5 * Math.sqrt((p.n as number) / maxN)}
          fill="var(--accent-2)"
          opacity={0.65}
        />
      ))}
      <text x={size / 2} y={size - 6} textAnchor="middle" fontSize="10" fill="var(--text-faint)">
        predicted probability
      </text>
      <text
        x={12}
        y={size / 2}
        textAnchor="middle"
        fontSize="10"
        fill="var(--text-faint)"
        transform={`rotate(-90 12 ${size / 2})`}
      >
        observed frequency
      </text>
    </svg>
  );
}
