"""Imagem de satélite recente (Sentinel-2 L2A) via Earth Search (Element 84).

Consulta o catálogo STAC público (sem chave) e devolve a cena mais recente com
pouca nuvem sobre o ponto. Serve para o produtor saber se há imagem recente e
limpa do talhão. Em caso de falha devolve None — o app segue sem satélite.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

STAC_SEARCH_URL = "https://earth-search.aws.element84.com/v1/search"
COLLECTION = "sentinel-2-l2a"
MAX_CLOUD = 40
LOOKBACK_DAYS = 30
TIMEOUT_S = 5


def parse_feature(feature: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    props = feature.get("properties") or {}
    assets = feature.get("assets") or {}
    dt = props.get("datetime")
    age_days = None
    if dt:
        try:
            when = datetime.fromisoformat(dt.replace("Z", "+00:00"))
            age_days = ((now or datetime.now(timezone.utc)) - when).days
        except ValueError:
            pass
    thumb = (assets.get("thumbnail") or {}).get("href")
    return {
        "id": feature.get("id"),
        "datetime": dt,
        "age_days": age_days,
        "cloud_cover": props.get("eo:cloud_cover"),
        "thumbnail": thumb,
        "source": "Sentinel-2 L2A · Earth Search (Element 84)",
    }


def latest_scene(lat: float, lon: float) -> dict[str, Any] | None:
    if os.environ.get("AGROSHIELD_DEMO") == "1":
        return {
            "id": "DEMO_SCENE",
            "datetime": "2026-11-13T13:41:00Z",
            "age_days": 4,
            "cloud_cover": 8.2,
            "thumbnail": None,
            "source": "Sentinel-2 L2A (demonstração)",
        }
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)
    body = {
        "collections": [COLLECTION],
        "intersects": {"type": "Point", "coordinates": [lon, lat]},
        "datetime": f"{start:%Y-%m-%dT%H:%M:%SZ}/{end:%Y-%m-%dT%H:%M:%SZ}",
        "query": {"eo:cloud_cover": {"lt": MAX_CLOUD}},
        "sortby": [{"field": "properties.datetime", "direction": "desc"}],
        "limit": 1,
    }
    try:
        resp = requests.post(STAC_SEARCH_URL, json=body, timeout=TIMEOUT_S)
        resp.raise_for_status()
        features = resp.json().get("features") or []
    except (requests.RequestException, ValueError):
        return None
    if not features:
        return None
    return parse_feature(features[0], end)
