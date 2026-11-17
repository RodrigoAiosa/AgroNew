"""Utilidades geográficas simples (sem dependências pesadas)."""
from __future__ import annotations

import math
from typing import Iterable, Sequence

EARTH_RADIUS_M = 6_371_008.8


def valid_latlon(lat: float, lon: float) -> bool:
    return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0


def centroid(points: Sequence[Sequence[float]]) -> tuple[float, float]:
    """Centro (média dos vértices) de uma lista de [lat, lon]."""
    if not points:
        raise ValueError("polígono vazio")
    lat = sum(p[0] for p in points) / len(points)
    lon = sum(p[1] for p in points) / len(points)
    return lat, lon


def polygon_area_ha(points: Iterable[Sequence[float]]) -> float:
    """Área aproximada em hectares de um polígono [[lat, lon], ...].

    Usa projeção equiretangular local, suficiente para talhões (< alguns km²).
    """
    pts = [(float(p[0]), float(p[1])) for p in points]
    if len(pts) < 3:
        return 0.0
    lat0 = math.radians(sum(p[0] for p in pts) / len(pts))
    xy = [
        (
            math.radians(lon) * EARTH_RADIUS_M * math.cos(lat0),
            math.radians(lat) * EARTH_RADIUS_M,
        )
        for lat, lon in pts
    ]
    area = 0.0
    for i in range(len(xy)):
        x1, y1 = xy[i]
        x2, y2 = xy[(i + 1) % len(xy)]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0 / 10_000.0
