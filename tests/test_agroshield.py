import os

os.environ["AGROSHIELD_DEMO"] = "1"

import pytest

from agroshield import geo, rules, satellite, sms, weather


def wx(scenario):
    return weather.demo_weather(-12.5, -55.7, scenario)


def test_good_day_allows_everything_but_flags_dry_spell():
    for act in rules.ACTIVITIES:
        r = rules.analyze(wx("good"), act)
        assert r["go"] and r["verdict"] == "SIM"


def test_storm_blocks_spray_and_harvest_and_gives_next_window():
    for act in ("pulverizar", "colher"):
        r = rules.analyze(wx("storm"), act)
        assert not r["go"] and "Chuva" in r["reason"]
        assert r["window"] and r["window"]["hours"] >= rules.ACTIVITIES[act]["min_hours"]


def test_short_window_is_a_no():
    r = rules.analyze(wx("storm"), "adubar")
    assert not r["go"] and "janela" in r["reason"].lower()


def test_wind_blocks_spray_and_fertilize_not_harvest():
    assert not rules.analyze(wx("windy"), "pulverizar")["go"]
    assert not rules.analyze(wx("windy"), "adubar")["go"]
    assert rules.analyze(wx("windy"), "colher")["go"]


def test_waterlogged_soil_blocks_fertilize_and_harvest():
    assert not rules.analyze(wx("wet"), "adubar")["go"]
    assert not rules.analyze(wx("wet"), "colher")["go"]


def test_unknown_activity():
    with pytest.raises(ValueError):
        rules.analyze(wx("good"), "voar")


def test_sms_is_ascii_and_fits_one_message():
    for sc in ("good", "storm", "windy", "wet"):
        for act in rules.ACTIVITIES:
            text = sms.build_sms(rules.analyze(wx(sc), act), "Sorriso - MT com um nome bem longo")
            assert len(text) <= 160 and text.isascii()
            assert text.startswith("AgroShield " + act.upper())


@pytest.mark.parametrize("body,expected", [
    ("pulverizar Sorriso MT", {"activity": "pulverizar", "place": "Sorriso Mt"}),
    ("COLHER -12.54 -55.72", {"activity": "colher", "coords": (-12.54, -55.72)}),
    ("colher -12,54 -55,72", {"activity": "colher", "coords": (-12.54, -55.72)}),
    ("adubação", {"error": "local", "activity": "adubar"}),
    ("xyz", {"error": "operacao"}),
])
def test_parse_command(body, expected):
    assert sms.parse_command(body) == expected


def test_polygon_area():
    # ~1 km x 1 km perto do equador = ~100 ha
    d = 0.009
    ha = geo.polygon_area_ha([[0, 0], [0, d], [d, d], [d, 0]])
    assert 95 < ha < 105


def test_satellite_parse():
    f = {"id": "X", "properties": {"datetime": "2026-11-10T13:00:00Z", "eo:cloud_cover": 5.5},
         "assets": {"thumbnail": {"href": "https://e/t.jpg"}}}
    out = satellite.parse_feature(f)
    assert out["cloud_cover"] == 5.5 and out["thumbnail"].endswith("t.jpg")


def test_open_meteo_normalize_picks_current_hour():
    raw = {"latitude": 1, "longitude": 2, "timezone": "UTC",
           "current": {"time": "2026-11-17T14:15", "temperature_2m": 20},
           "hourly": {"time": ["2026-11-17T13:00", "2026-11-17T14:00", "2026-11-17T15:00"],
                      "precipitation": [0, 1, 2]}}
    out = weather.normalize(raw)
    assert out["now_index"] == 1 and out["precipitation"] == [0, 1, 2]


@pytest.fixture
def client():
    from app import app
    return app.test_client()


def test_api_overview_and_validation(client):
    r = client.get("/api/overview?lat=-12.5&lon=-55.7")
    assert r.status_code == 200 and set(r.get_json()["activities"]) == set(rules.ACTIVITIES)
    assert client.get("/api/analyze?lat=999&lon=1&activity=colher").status_code == 400
    assert client.get("/api/analyze?lat=1&lon=1&activity=voar").status_code == 400


def test_api_pdf(client):
    r = client.post("/api/report.pdf", json={"lat": -12.5, "lon": -55.7, "activity": "plantar", "area_ha": 40})
    assert r.status_code == 200 and r.data.startswith(b"%PDF")


def test_inbound_sms(client):
    r = client.post("/api/sms/inbound", data={"Body": "PLANTAR -12.5 -55.7"})
    assert r.status_code == 200 and b"AgroShield PLANTAR" in r.data
    assert b"PULVERIZAR" in client.get("/api/sms/inbound?Body=ajuda&format=text").data


def test_sms_send_route_removed(client):
    assert client.post('/api/sms/send', json={}).status_code in (404, 405)
