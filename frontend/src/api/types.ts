// Shared API types, mirroring the backend response shapes.

export interface TeamRef {
  id: number;
  name: string;
}

export interface MatchSummary {
  id: number;
  league_id: number;
  league: string;
  season: string | null;
  kickoff_utc: string;
  status: string;
  home_team: TeamRef;
  away_team: TeamRef;
  score: { home: number; away: number } | null;
  ht_score: { home: number; away: number } | null;
  venue: string | null;
  round: string | null;
}

export interface MatchList {
  total: number;
  limit: number;
  offset: number;
  matches: MatchSummary[];
}

export interface MatchDetail extends MatchSummary {
  stats: Array<{
    team_id: number;
    is_home: boolean;
    xg: number | null;
    shots: number | null;
    shots_on_target: number | null;
    corners: number | null;
    fouls: number | null;
    yellow_cards: number | null;
    red_cards: number | null;
    possession: number | null;
    source_provider: string;
  }>;
  odds: Array<{
    bookmaker: string;
    market: string;
    selection: string;
    decimal_odds: number;
    is_closing: boolean;
    line: number | null;
    source_provider: string;
    captured_at: string | null;
  }>;
}

export interface League {
  id: number;
  slug: string;
  name: string;
  country: string | null;
  match_count: number;
}

export interface ProviderInfo {
  name: string;
  kind: string;
  capabilities: string[];
  available: boolean;
  reason: string;
  cost_note: string;
  requires_key: boolean;
  license_note?: string;
  terms_url?: string | null;
  notes?: string;
  base_url?: string | null;
}

export interface DataSource {
  name: string;
  kind: string;
  base_url: string | null;
  requires_key: boolean;
  enabled: boolean;
  status: string;
  message: string;
  license_note: string;
  cost_note: string;
  last_checked_at: string | null;
}

export interface FormStats {
  n_matches: number;
  wins: number;
  draws: number;
  losses: number;
  goals_for: number;
  goals_against: number;
  avg_goals_for: number | null;
  avg_goals_against: number | null;
  clean_sheets: number;
  failed_to_score: number;
  btts_frequency: number | null;
  over_under: Record<string, number>;
  first_half_goals_for: number | null;
  second_half_goals_for: number | null;
  points_per_match: number | null;
  notes: string[];
}

export interface H2HMatch {
  date: string;
  is_home: boolean;
  opponent: string;
  goals_for: number;
  goals_against: number;
  result: "W" | "D" | "L";
}

export interface ValueAssessment {
  market: string;
  selection: string;
  bookmaker: string;
  model_probability: number;
  decimal_odds: number;
  implied_probability: number;
  market_fair_probability: number | null;
  expected_value: number;
  break_even_probability: number;
  edge: number;
  is_positive_ev: boolean;
  confidence: string;
}

export interface Finding {
  title: string;
  detail: string;
  evidence: string[];
}

export interface AnalysisOptions {
  monte_carlo_simulations?: number;
  xg_weight?: number;
  seed?: number | null;
  halflife_days?: number | null;
  lookback_days?: number;
  include_asian_handicap?: boolean;
  persist?: boolean;
  manual_odds?: Array<{
    market: string;
    selection: string;
    decimal_odds: number;
    bookmaker?: string;
  }>;
}

export interface AnalysisReport {
  match: {
    id: number;
    league: { id: number; name: string; slug: string; country: string | null };
    season: { id: number; label: string } | null;
    home_team: { id: number; name: string; slug: string };
    away_team: { id: number; name: string; slug: string };
    kickoff_utc: string;
    status: string;
    venue: string | null;
    round: string | null;
    data_retrieved_at: string;
  };
  provenance: {
    match_sources: Array<{
      provider: string;
      source_url: string;
      competition: string;
      season: string | null;
      retrieved_at: string;
      data_as_of: string;
      is_estimate: boolean;
    }>;
    home_team: { name: string; provider_refs: Record<string, string> };
    away_team: { name: string; provider_refs: Record<string, string> };
  };
  model: {
    name: string;
    version: string;
    params: {
      attack: Record<string, number>;
      defence: Record<string, number>;
      intercept: number;
      home_advantage: number;
      rho: number;
      halflife_days: number;
      n_matches: number;
      converged: boolean;
    };
    n_training_matches: number;
    training_start: string;
    training_end: string;
    converged: boolean;
    xg_weight: number;
    xg_used: boolean;
  };
  expected_goals: { home: number; away: number; total: number };
  predicted_score: { score: string; probability: number };
  markets: {
    one_x_two: Record<string, number>;
    double_chance: Record<string, number>;
    totals: Record<string, { over: number; under: number }>;
    btts: Record<string, number>;
    team_totals: Record<string, Record<string, { over: number; under: number }>>;
    correct_scores: Array<{ score: string; home_goals: number; away_goals: number; probability: number }>;
    total_goals_distribution: Array<{ goals: number; probability: number; is_open_ended?: boolean }>;
    asian_handicap: Record<string, { win: number; push: number; lose: number }> | null;
    half_time: {
      available: boolean;
      note?: string;
      expected_goals?: { home: number; away: number; total: number };
      one_x_two?: Record<string, number>;
      double_chance?: Record<string, number>;
      totals?: Record<string, { over: number; under: number }>;
      btts?: Record<string, number>;
      correct_scores?: Array<{ score: string; home_goals: number; away_goals: number; probability: number }>;
      based_on_matches?: number;
      goal_share?: { home: number; away: number };
    };
    fair_odds: {
      one_x_two: Record<string, number | null>;
      double_chance: Record<string, number | null>;
      totals: Record<string, { over: number | null; under: number | null }>;
      btts: Record<string, number | null>;
      team_totals: Record<string, Record<string, { over: number | null; under: number | null }>>;
      half_time?: {
        one_x_two: Record<string, number | null>;
        totals: Record<string, { over: number | null; under: number | null }>;
        btts: Record<string, number | null>;
      };
    };
  };
  simulation: {
    n_simulations: number;
    seed: number;
    one_x_two: Record<string, number>;
    mc_std_error: number;
  };
  form: {
    home: TeamFormBundle;
    away: TeamFormBundle;
  };
  head_to_head: H2HMatch[];
  ratings: {
    home: { attack_multiplier: number; defence_multiplier: number; net_strength: number };
    away: { attack_multiplier: number; defence_multiplier: number; net_strength: number };
    table: Array<{
      team: string;
      attack_multiplier: number;
      defence_multiplier: number;
      net_strength: number;
    }>;
    base_rates: { home: number; away: number };
  };
  findings: Finding[];
  opportunities: Array<{
    market: string;
    selection: string;
    label: string;
    probability: number;
    fair_odds: number | null;
  }>;
  limitations: string[];
  scope: {
    cross_competition: boolean;
    own_league_matches: number;
    peer_league_ids: number[];
    n_training_matches: number;
    note: string | null;
    model_home_team?: string;
    model_away_team?: string;
  };
  value: { available: boolean; note?: string; assessments?: ValueAssessment[]; caveats?: string[] };
  confidence: {
    level: string;
    score?: number;
    n_training_matches: number;
    monte_carlo_std_error: number;
    components?: Record<string, number>;
    notes: string[];
  };
  model_run_id: number | null;
}

export interface TeamFormBundle {
  last5: FormStats;
  last10: FormStats;
  last15: FormStats;
  splits: { home: FormStats; away: FormStats; overall: FormStats };
  schedule: { rest_days: number | null; matches_last_14_days: number };
  xg: { n_with_xg: number; xg_for_avg: number | null; xg_against_avg: number | null };
}

export interface BacktestMarket {
  market: string;
  n: number;
  brier: number;
  log_loss: number;
  accuracy: number | null;
  baseline_brier: number;
  baseline_log_loss: number;
  baseline_accuracy: number | null;
  improvement_brier: number;
  reliability: Array<{
    lower: number;
    upper: number;
    n: number;
    mean_predicted: number | null;
    observed_frequency: number | null;
  }>;
}

export interface BacktestResponse {
  model_run_id: number;
  league_id: number;
  league_name: string;
  n_train: number;
  n_eval: number;
  warnings: string[];
  backtest_ids: number[];
  markets: BacktestMarket[];
}

export interface IngestResult {
  provider: string;
  match_id?: number | null;
  available: boolean;
  reason: string;
  records_in: number;
  matches_upserted: number;
  status: string;
  warnings: string[];
}

export interface PredictionValue {
  market: string;
  selection: string;
  bookmaker: string;
  decimal_odds: number;
  expected_value: number;
  edge: number;
  is_positive_ev: boolean;
}

export interface UpcomingPrediction {
  match_id: number;
  kickoff_utc: string;
  status: string;
  league: string | null;
  home_team: string;
  away_team: string;
  probabilities: {
    one_x_two: Record<string, number>;
    double_chance: Record<string, number> | null;
    over_under_2_5: Record<string, number>;
    btts: Record<string, number>;
  };
  pick: { market: string; selection: string; probability: number };
  predicted_score: { score: string; probability: number };
  expected_goals: { home: number; away: number; total: number };
  best_value: PredictionValue | null;
  confidence: string;
  confidence_score?: number | null;
  n_training_matches: number;
}

export interface UpcomingPredictions {
  status: "idle" | "running" | "ready" | "error";
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
  generated_at?: string;
  window_days?: number;
  include_live?: boolean;
  league_id?: number | null;
  considered?: number;
  predicted?: number;
  skipped?: Array<{ match_id: number; home_team: string; away_team: string; reason: string }>;
  predictions?: UpcomingPrediction[];
}

export interface LiveMatch {
  provider_id: string;
  local_match_id: number | null;
  kickoff_utc: string;
  status: string;
  league: string;
  country: string | null;
  home_team: string;
  away_team: string;
  score: { home: number; away: number } | null;
  ht_score: { home: number; away: number } | null;
  venue: string | null;
}

export interface LiveResponse {
  available: boolean;
  provider: string | null;
  date: string | null;
  reason?: string;
  counts?: { live: number; upcoming: number; finished: number };
  live: LiveMatch[];
  upcoming: LiveMatch[];
}
