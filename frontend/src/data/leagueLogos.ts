// Competition crests, keyed by league slug (stable) with a name fallback for
// endpoints that only carry the display name.
//
// Source: media.api-sports.io league artwork (competition trademarks; used here
// for a local, non-commercial analyst dashboard).  Match by slug when we have
// one, otherwise by the normalized display name.

export function normLeague(name: string): string {
  return name
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

export const LEAGUE_LOGOS_BY_SLUG: Record<string, string> = {
  "england-premier-league": "/leagues/england-premier-league.png",
  "spain-la-liga": "/leagues/spain-la-liga.png",
  "germany-bundesliga": "/leagues/germany-bundesliga.png",
  "italy-serie-a": "/leagues/italy-serie-a.png",
  "france-ligue-1": "/leagues/france-ligue-1.png",
  "europe-uefa-champions-league": "/leagues/europe-uefa-champions-league.png",
  "europe-uefa-europa-league": "/leagues/europe-uefa-europa-league.png",
  "europe-uefa-conference-league": "/leagues/europe-uefa-conference-league.png",
};

export const LEAGUE_LOGOS_BY_NAME: Record<string, string> = {
  "premier league": "/leagues/england-premier-league.png",
  "la liga": "/leagues/spain-la-liga.png",
  "bundesliga": "/leagues/germany-bundesliga.png",
  "serie a": "/leagues/italy-serie-a.png",
  "ligue 1": "/leagues/france-ligue-1.png",
  "uefa champions league": "/leagues/europe-uefa-champions-league.png",
  "champions league": "/leagues/europe-uefa-champions-league.png",
  "uefa europa league": "/leagues/europe-uefa-europa-league.png",
  "europa league": "/leagues/europe-uefa-europa-league.png",
  "uefa conference league": "/leagues/europe-uefa-conference-league.png",
  "conference league": "/leagues/europe-uefa-conference-league.png",
};

export function leagueCrest(league: {
  slug?: string | null;
  name?: string | null;
}): string | undefined {
  if (league.slug && LEAGUE_LOGOS_BY_SLUG[league.slug]) {
    return LEAGUE_LOGOS_BY_SLUG[league.slug];
  }
  if (league.name) {
    const byName = LEAGUE_LOGOS_BY_NAME[normLeague(league.name)];
    if (byName) return byName;
  }
  return undefined;
}
