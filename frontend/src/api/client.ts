import type {
  AnalysisOptions,
  AnalysisReport,
  BacktestResponse,
  DataSource,
  IngestResult,
  League,
  LiveResponse,
  MatchDetail,
  MatchList,
  ProviderInfo,
  UpcomingPredictions,
} from "./types";

const BASE = import.meta.env.VITE_API_BASE ?? "/api";

const NETWORK_RETRIES = 6;
const NETWORK_RETRY_BASE_MS = 500;

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  for (let attempt = 0; ; attempt += 1) {
    let response: Response;
    try {
      response = await fetch(`${BASE}${path}`, {
        headers: { "Content-Type": "application/json" },
        ...init,
      });
    } catch (err) {
      // A refused connection means the request never reached the API, so
      // retrying is safe (nothing ran server-side). Covers the dev-server
      // startup race where the UI loads before the backend is listening.
      if (attempt < NETWORK_RETRIES) {
        await delay(NETWORK_RETRY_BASE_MS * (attempt + 1));
        continue;
      }
      throw new ApiError(
        `Can't reach the API at ${BASE}. Is the backend running on http://127.0.0.1:8000?`,
        0,
      );
    }
    if (!response.ok) {
      let detail = response.statusText;
      try {
        const body = await response.json();
        detail = body.detail ?? JSON.stringify(body);
      } catch {
        /* keep statusText */
      }
      throw new ApiError(detail, response.status);
    }
    return (await response.json()) as T;
  }
}

function query(params: Record<string, string | number | boolean | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const api = {
  health: () => request<{ status: string; providers_enabled: string[] }>("/health"),
  providers: () => request<{ providers: ProviderInfo[] }>("/providers").then((r) => r.providers),
  sources: () => request<{ sources: DataSource[] }>("/sources").then((r) => r.sources),

  leagues: () => request<{ leagues: League[] }>("/leagues").then((r) => r.leagues),

  matches: (params: {
    league_id?: number;
    status?: string;
    q?: string;
    on_date?: string;
    date_from?: string;
    date_to?: string;
    order?: string;
    limit?: number;
    offset?: number;
  }) => request<MatchList>(`/matches${query(params)}`),

  match: (id: number) => request<MatchDetail>(`/matches/${id}`),

  analysis: (id: number, options: AnalysisOptions) =>
    request<AnalysisReport>(`/analysis/${id}`, {
      method: "POST",
      body: JSON.stringify(options),
    }),

  backtest: (body: {
    league_id: number;
    min_train?: number;
    retrain_every?: number;
    halflife_days?: number;
    totals_line?: number;
  }) =>
    request<BacktestResponse>("/models/backtest", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  ingest: (provider: string) =>
    request<IngestResult>("/ingest/run", {
      method: "POST",
      body: JSON.stringify({ provider }),
    }),

  importFixture: (body: {
    reference: string;
    provider?: string;
    date?: string;
    include_details?: boolean;
  }) =>
    request<IngestResult>("/ingest/fixture", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  upcomingPredictions: (params: {
    league_id?: number;
    days?: number;
    limit?: number;
    include_live?: boolean;
  }) => request<UpcomingPredictions>(`/predictions/upcoming${query(params)}`),

  live: (date?: string) => request<LiveResponse>(`/live${query({ date })}`),
};
