"""Clima e solo via Open-Meteo (https://open-meteo.com/en/docs).

Sem chave de API para uso não comercial. Para uso comercial, defina
OPEN_METEO_API_KEY (o app passa a usar customer-api.open-meteo.com).
"""
from __future__ import annotations

import math
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Any

import requests

HOURLY_VARS = [
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "precipitation_probability",
    "wind_speed_10m",
    "wind_gusts_10m",
    "soil_moisture_0_to_1cm",
    "soil_moisture_9_to_27cm",
    "soil_temperature_6cm",
    "et0_fao_evapotranspiration",
]
CURRENT_VARS = [
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "wind_speed_10m",
    "wind_gusts_10m",
]
PAST_DAYS = 2
FORECAST_DAYS = 4
CACHE_TTL_S = 600
TIMEOUT_S = (5, 9)  # (conexão, leitura) por tentativa
ATTEMPTS = 2

_cache: dict[tuple, tuple[float, dict]] = {}
_lock = threading.Lock()


class WeatherError(RuntimeError):
    """Falha ao obter dados de clima."""


def _base_url() -> tuple[str, dict[str, str]]:
    key = os.environ.get("OPEN_METEO_API_KEY")
    if key:
        return "https://customer-api.open-meteo.com/v1/forecast", {"apikey": key}
    return "https://api.open-meteo.com/v1/forecast", {}


def _index_now(times: list[str], current_time: str | None) -> int:
    """Índice da hora atual (maior horário <= agora local do ponto)."""
    ref = current_time or datetime.utcnow().strftime("%Y-%m-%dT%H:%M")
    ref_hour = ref[:13]  # YYYY-MM-DDTHH
    idx = 0
    for i, t in enumerate(times):
        if t[:13] <= ref_hour:
            idx = i
        else:
            break
    return idx


def normalize(raw: dict[str, Any]) -> dict[str, Any]:
    """Transforma a resposta do Open-Meteo em um dicionário estável."""
    hourly = raw.get("hourly") or {}
    times = hourly.get("time") or []
    if not times:
        raise WeatherError("Resposta sem dados horários")
    current = raw.get("current") or {}
    idx = _index_now(times, current.get("time"))
    out = {
        "lat": raw.get("latitude"),
        "lon": raw.get("longitude"),
        "elevation": raw.get("elevation"),
        "timezone": raw.get("timezone", "UTC"),
        "times": times,
        "now_index": idx,
        "current": {k: current.get(k) for k in CURRENT_VARS},
        "daily": raw.get("daily") or {},
    }
    for var in HOURLY_VARS:
        out[var] = hourly.get(var) or [None] * len(times)
    return out


def fetch(lat: float, lon: float) -> dict[str, Any]:
    """Busca e normaliza a previsão (com cache de 10 min por ponto)."""
    if os.environ.get("AGRONEW_DEMO") == "1":
        return demo_weather(lat, lon)

    key = (round(lat, 2), round(lon, 2))
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL_S:
            return hit[1]

    url, extra = _base_url()
    params = {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "hourly": ",".join(HOURLY_VARS),
        "current": ",".join(CURRENT_VARS),
        "daily": "precipitation_sum,temperature_2m_max,temperature_2m_min,wind_speed_10m_max",
        "wind_speed_unit": "kmh",
        "timezone": "auto",
        "past_days": PAST_DAYS,
        "forecast_days": FORECAST_DAYS,
        **extra,
    }
    last_exc: Exception | None = None
    data = None
    for attempt in range(ATTEMPTS):
        try:
            resp = requests.get(url, params=params, timeout=TIMEOUT_S)
            resp.raise_for_status()
            data = normalize(resp.json())
            break
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            if attempt + 1 < ATTEMPTS:
                time.sleep(0.5)
    if data is None:
        raise WeatherError(
            "O serviço de clima (Open-Meteo) demorou demais para responder. "
            "Isso costuma passar em instantes: toque em Tentar de novo."
        ) from last_exc

    with _lock:
        _cache[key] = (now, data)
        if len(_cache) > 500:  # evita crescer sem limite
            _cache.pop(next(iter(_cache)))
    return data


def demo_weather(lat: float, lon: float, scenario: str | None = None) -> dict[str, Any]:
    """Dados sintéticos e determinísticos para testes e modo demonstração."""
    scenario = scenario or os.environ.get("AGRONEW_DEMO_SCENARIO", "good")
    n = (PAST_DAYS + FORECAST_DAYS) * 24
    start = datetime(2026, 11, 15, 0, 0)
    times = [(start + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(n)]
    now_idx = PAST_DAYS * 24 + 9  # 09:00 do dia 3

    temp, rh, rain, prob, wind, gust, sm_s, sm_d, st, et0 = ([] for _ in range(10))
    for h in range(n):
        hod = h % 24
        temp.append(round(21 + 7 * math.sin((hod - 9) / 24 * 2 * math.pi), 1))
        rh.append(round(70 - 22 * math.sin((hod - 9) / 24 * 2 * math.pi)))
        r = 0.0
        p = 5
        w = 7.0 + 2 * math.sin(hod / 24 * 2 * math.pi)
        if scenario == "storm" and now_idx + 2 <= h <= now_idx + 5:
            r, p = 6.0, 90
            w = 24.0
        if scenario == "windy":
            w = 22.0
        rain.append(r)
        prob.append(p)
        wind.append(round(w, 1))
        gust.append(round(w * 1.5, 1))
        wet = scenario == "wet"
        sm_s.append(0.40 if wet else 0.22)
        sm_d.append(0.47 if wet else 0.31)
        st.append(round(24 + 3 * math.sin((hod - 10) / 24 * 2 * math.pi), 1))
        et0.append(round(max(0.0, 0.3 * math.sin((hod - 6) / 12 * math.pi)), 2))
    cur = {
        "time": times[now_idx],
        "temperature_2m": temp[now_idx],
        "relative_humidity_2m": rh[now_idx],
        "precipitation": rain[now_idx],
        "wind_speed_10m": wind[now_idx],
        "wind_gusts_10m": gust[now_idx],
    }
    return {
        "lat": lat,
        "lon": lon,
        "elevation": 400,
        "timezone": "America/Sao_Paulo",
        "times": times,
        "now_index": now_idx,
        "current": {k: cur.get(k) for k in CURRENT_VARS},
        "daily": {},
        "temperature_2m": temp,
        "relative_humidity_2m": rh,
        "precipitation": rain,
        "precipitation_probability": prob,
        "wind_speed_10m": wind,
        "wind_gusts_10m": gust,
        "soil_moisture_0_to_1cm": sm_s,
        "soil_moisture_9_to_27cm": sm_d,
        "soil_temperature_6cm": st,
        "et0_fao_evapotranspiration": et0,
    }


UF_TO_STATE = {
    "AC": "Acre", "AL": "Alagoas", "AP": "Amapá", "AM": "Amazonas", "BA": "Bahia",
    "CE": "Ceará", "DF": "Distrito Federal", "ES": "Espírito Santo", "GO": "Goiás",
    "MA": "Maranhão", "MT": "Mato Grosso", "MS": "Mato Grosso do Sul",
    "MG": "Minas Gerais", "PA": "Pará", "PB": "Paraíba", "PR": "Paraná",
    "PE": "Pernambuco", "PI": "Piauí", "RJ": "Rio de Janeiro",
    "RN": "Rio Grande do Norte", "RS": "Rio Grande do Sul", "RO": "Rondônia",
    "RR": "Roraima", "SC": "Santa Catarina", "SP": "São Paulo", "SE": "Sergipe",
    "TO": "Tocantins",
}


def _strip_accents(s: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()

STATE_TO_UF = {_strip_accents(v): k for k, v in UF_TO_STATE.items()}


def _split_uf(text: str) -> tuple[str, str | None]:
    """'Jequié BA' / 'Jequié - BA' / 'Jequié, BA' -> ('Jequié', 'BA')."""
    cleaned = text.replace(",", " ").replace(" - ", " ").replace("-", " ").split()
    if len(cleaned) > 1 and cleaned[-1].upper() in UF_TO_STATE:
        return " ".join(cleaned[:-1]), cleaned[-1].upper()
    return text.strip(), None


def _query_geocoder(name: str, count: int, country: str | None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"name": name, "count": count, "language": "pt", "format": "json"}
    if country:
        params["countryCode"] = country
    try:
        resp = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                            params=params, timeout=TIMEOUT_S)
        resp.raise_for_status()
        return resp.json().get("results") or []
    except (requests.RequestException, ValueError):
        return []


def geocode(text: str, count: int = 6) -> list[dict[str, Any]]:
    """Busca de cidades (Open-Meteo Geocoding). Brasil sempre vem primeiro.

    Aceita "Cidade UF" (a UF escolhe entre homônimas). Resultados de outros países só
    entram depois dos brasileiros, para "Jequié" não listar o povoado da Bolívia antes.
    """
    text = (text or "").strip()
    if len(text) < 2:
        return []
    name, uf = _split_uf(text)
    if os.environ.get("AGRONEW_DEMO") == "1":
        return [{"name": name.title(), "admin1": "MT", "uf": uf or "MT", "country": "Brasil",
                 "country_code": "BR", "lat": -12.5453, "lon": -55.7211}]
    br = _query_geocoder(name, 20, "BR")
    if uf:
        want = _strip_accents(UF_TO_STATE[uf])
        br = [r for r in br if _strip_accents(r.get("admin1") or "") == want] or br
    results = list(br)
    if len(results) < count:  # completa com o resto do mundo, depois dos brasileiros
        results += [r for r in _query_geocoder(name, 10, None) if r.get("country_code") != "BR"]
    items, seen = [], set()
    for r in results:
        key = (r.get("latitude") and round(r["latitude"], 2), r.get("longitude") and round(r["longitude"], 2))
        if key in seen:
            continue
        seen.add(key)
        is_br = r.get("country_code") == "BR"
        state_uf = STATE_TO_UF.get(_strip_accents(r.get("admin1") or "")) if is_br else None
        items.append({
            "name": r.get("name"),
            "admin1": state_uf or r.get("admin1"),
            "uf": state_uf,
            "country": r.get("country"),
            "country_code": r.get("country_code"),
            "lat": r.get("latitude"),
            "lon": r.get("longitude"),
        })
    return items[:count]


def resolve_place(text: str) -> dict[str, Any] | None:
    """Resolve 'Sorriso MT' / 'Lucas do Rio Verde' para o melhor resultado."""
    results = geocode(text, count=10)
    return results[0] if results else None
