// Small formatting helpers.

export function pct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "–";
  return `${(value * 100).toFixed(digits)}%`;
}

export function num(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "–";
  return value.toFixed(digits);
}

export function signedPct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "–";
  const sign = value > 0 ? "+" : "";
  return `${sign}${(value * 100).toFixed(digits)}%`;
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "–";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
  });
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "–";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function isSynthetic(country: string | null | undefined): boolean {
  return Boolean(country && country.toUpperCase().includes("SYNTHETIC"));
}

// Badge colour for a graded confidence level ("very low" … "very high").
export function confidenceClass(level: string): string {
  if (level === "very high" || level === "high") return "ok";
  if (level === "low") return "warn";
  if (level === "very low") return "bad";
  return "";
}

export function capitalize(value: string): string {
  return value.length === 0 ? value : value.charAt(0).toUpperCase() + value.slice(1);
}
