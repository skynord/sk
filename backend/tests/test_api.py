"""End-to-end tests for the HTTP API."""

from __future__ import annotations


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert "sample" in body["providers_enabled"]


def test_providers_list(client):
    providers = client.get("/api/providers").json()["providers"]
    names = {p["name"] for p in providers}
    assert {"football_data_co_uk", "sample", "manual", "api_football"} <= names
    sample = next(p for p in providers if p["name"] == "sample")
    assert sample["available"] is True


def test_sources(client):
    sources = client.get("/api/sources").json()["sources"]
    assert any(s["name"] == "sample" for s in sources)


def test_list_leagues(client):
    leagues = client.get("/api/leagues").json()["leagues"]
    assert len(leagues) >= 1
    assert any("SYNTHETIC" in (lg["country"] or "") for lg in leagues)


def test_list_matches_pagination(client):
    page = client.get("/api/matches", params={"limit": 5}).json()
    assert page["total"] >= 500
    assert len(page["matches"]) == 5
    first = page["matches"][0]
    assert first["home_team"]["name"] and first["away_team"]["name"]


def test_list_matches_filter_status(client):
    page = client.get("/api/matches", params={"status": "finished", "limit": 3}).json()
    assert all(m["status"] == "finished" for m in page["matches"])
    assert page["total"] >= 500


def test_match_detail(client):
    match_id = client.get("/api/matches", params={"limit": 1}).json()["matches"][0]["id"]
    detail = client.get(f"/api/matches/{match_id}").json()
    assert detail["id"] == match_id
    assert "stats" in detail and "odds" in detail


def test_match_detail_404(client):
    assert client.get("/api/matches/999999").status_code == 404


def test_analysis_returns_probabilities(client):
    # A mid-season match with enough prior history for training.
    match_id = client.get("/api/matches", params={"status": "finished", "limit": 1, "offset": 300}).json()["matches"][0]["id"]
    resp = client.post(f"/api/analysis/{match_id}", json={"monte_carlo_simulations": 2000, "persist": False})
    assert resp.status_code == 200
    body = resp.json()
    ox2 = body["markets"]["one_x_two"]
    assert abs(sum(ox2.values()) - 1.0) < 1e-6
    assert body["expected_goals"]["home"] > 0
    assert "confidence" in body


def test_analysis_with_manual_odds(client):
    match_id = client.get("/api/matches", params={"status": "finished", "limit": 1, "offset": 300}).json()["matches"][0]["id"]
    resp = client.post(
        f"/api/analysis/{match_id}",
        json={
            "monte_carlo_simulations": 2000,
            "persist": False,
            "manual_odds": [
                {"market": "1x2", "selection": "home", "decimal_odds": 1.5},
                {"market": "1x2", "selection": "draw", "decimal_odds": 4.2},
                {"market": "1x2", "selection": "away", "decimal_odds": 7.0},
            ],
        },
    )
    assert resp.status_code == 200
    value = resp.json()["value"]
    assert value["available"] is True
    assert len(value["assessments"]) == 3


def test_analysis_too_early_match_is_422(client):
    # The very first fixture of the season has no prior history to train on.
    first_id = min(m["id"] for m in client.get("/api/matches", params={"limit": 500}).json()["matches"])
    resp = client.post(f"/api/analysis/{first_id}", json={"persist": False})
    assert resp.status_code == 422


def test_create_manual_match(client):
    resp = client.post(
        "/api/matches/manual",
        json={
            "league": "Unit Test Cup",
            "country": "Testland",
            "home": "Alpha FC",
            "away": "Beta FC",
            "kickoff_utc": "2026-01-01T15:00:00Z",
            "home_stats": {"shots": 10, "xg": 1.4},
            "odds": [{"market": "1x2", "selection": "home", "decimal_odds": 2.1}],
        },
    )
    assert resp.status_code == 201
    match_id = resp.json()["match_id"]
    detail = client.get(f"/api/matches/{match_id}").json()
    assert detail["home_team"]["name"] == "Alpha FC"
    assert detail["odds"][0]["decimal_odds"] == 2.1


def test_value_assess(client):
    body = client.post(
        "/api/value/assess",
        json={"model_probability": 0.5, "decimal_odds": 2.2},
    ).json()
    assert body["is_positive_ev"] is True
    assert abs(body["expected_value"] - 0.1) < 1e-9


def test_model_registry(client):
    body = client.get("/api/models/registry").json()
    assert body["models"][0]["name"] == "dixon_coles"
    assert any(p["name"] == "sample" for p in body["providers"])


def test_backtest_endpoint(client):
    body = client.post(
        "/api/models/backtest",
        json={"league_id": 1, "min_train": 40, "retrain_every": 10},
    ).json()
    assert body["n_eval"] > 0
    markets = {m["market"]: m for m in body["markets"]}
    assert "1x2" in markets
    assert markets["1x2"]["brier"] > 0


def test_ingest_run_endpoint(client):
    body = client.post("/api/ingest/run", json={"provider": "sample"}).json()
    assert body["status"] == "success"
    runs = client.get("/api/ingest/runs").json()["runs"]
    assert any(r["provider"] == "sample" for r in runs)
