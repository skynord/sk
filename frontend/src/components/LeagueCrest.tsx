// A competition crest: the real badge when we ship one for that league,
// otherwise a deterministic monogram derived from the name.
//
// The bundled crests live in `src/data/leagueLogos.ts` (matched by slug, then by
// name).  When a league is not in that map — or its image fails to load — we
// fall back to a stable monogram so the same competition always renders
// something, offline, with no external logo service.

import { useState } from "react";
import { leagueCrest, normLeague } from "../data/leagueLogos";

function hashString(text: string): number {
  let hash = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function initials(name: string): string {
  const words = name
    .replace(/[^\p{L}\p{N}\s]/gu, " ")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[words.length - 1][0]).toUpperCase();
}

export function LeagueCrest({
  name,
  slug,
  size = 20,
}: {
  name: string;
  slug?: string | null;
  size?: number;
}) {
  const [failed, setFailed] = useState(false);
  const logo = leagueCrest({ slug, name });

  if (logo && !failed) {
    return (
      <span
        className="team-crest team-crest--logo"
        title={name}
        style={{ width: Math.round(size * 1.8), height: size }}
      >
        <img src={logo} alt="" loading="lazy" onError={() => setFailed(true)} />
      </span>
    );
  }

  const key = normLeague(name) || name;
  const shade = 44 + (hashString(key) % 5) * 7;
  return (
    <span
      className="team-crest"
      title={name}
      aria-hidden="true"
      style={{
        width: size,
        height: size,
        fontSize: Math.round(size * 0.42),
        background: `linear-gradient(155deg, #ffffff 0%, hsl(0 0% ${shade + 16}%) 55%, hsl(0 0% ${shade}%) 100%)`,
      }}
    >
      {initials(name)}
    </span>
  );
}
