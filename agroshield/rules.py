"""Motor de decisão do AgroShield.

Transforma a previsão horária (clima + solo) em uma resposta objetiva —
SIM ou NÃO — para quatro operações: plantar, pulverizar, adubar e colher.

Os limites abaixo seguem recomendações técnicas comuns de campo (por exemplo,
pulverização com vento de 3 a 10 km/h, temperatura abaixo de ~30 °C e umidade
relativa acima de ~55%). Eles são um ponto de partida: ajuste em ``LIMITS``
conforme cultura, solo, produto e orientação do seu agrônomo.

Cada critério devolve um *status*:
  ok    — condição favorável
  warn  — atenção (não impede, mas aumenta o risco)
  fail  — impede a operação agora
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

ACTIVITIES: dict[str, dict[str, Any]] = {
    "plantar": {
        "label": "Plantar", "verb": "plantar", "noun": "plantio",
        "icon": "🌱", "neg": "plante", "min_hours": 6,
    },
    "pulverizar": {
        "label": "Pulverizar", "verb": "pulverizar", "noun": "pulverização",
        "icon": "💧", "neg": "pulverize", "min_hours": 3,
    },
    "adubar": {
        "label": "Adubar", "verb": "adubar", "noun": "adubação",
        "icon": "🧪", "neg": "adube", "min_hours": 4,
    },
    "colher": {
        "label": "Colher", "verb": "colher", "noun": "colheita",
        "icon": "🌾", "neg": "colha", "min_hours": 6,
    },
}

# Limites ajustáveis (unidades: km/h, mm, °C, %, m³/m³ -> %).
LIMITS = {
    "spray_wind": {"fail_high": 15, "warn_high": 10, "warn_low": 3},
    "spray_gust": {"fail_high": 30},
    "spray_rain_4h_mm": 0.5,
    "spray_prob_4h": 60,
    "spray_temp": {"fail_high": 32, "warn_high": 30, "warn_low": 10},
    "spray_rh": {"fail_low": 40, "warn_low": 55},
    "plant_soil_moist": {"fail_low": 15, "warn_low": 22, "warn_high": 42, "fail_high": 48},
    "plant_soil_temp": {"fail_low": 14, "warn_low": 18, "warn_high": 32, "fail_high": 36},
    "plant_rain_24h": {"fail_high": 30, "warn_high": 15},
    "fert_rain_24h": {"fail_high": 25, "warn_high": 10},
    "fert_soil_moist": {"fail_high": 45, "warn_low": 12},
    "fert_wind": {"fail_high": 20, "warn_high": 12},
    "fert_temp": {"warn_high": 32},
    "fert_dry_48h_mm": 2.0,
    "harv_rain_6h_mm": 0.5,
    "harv_surface_moist": {"fail_high": 38, "warn_high": 30},
    "harv_rh": {"fail_high": 90, "warn_high": 80},
    "harv_gust": {"fail_high": 50, "warn_high": 40},
}

SCAN_HOURS = 48
WEIGHT = {"fail": 40, "warn": 15}
_ORDER = {"ok": 0, "warn": 1, "fail": 2}
DOW_PT = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]


# --------------------------------------------------------------------------
# Acesso seguro a séries
# --------------------------------------------------------------------------
def _v(wx: dict, key: str, i: int) -> float | None:
    arr = wx.get(key) or []
    if 0 <= i < len(arr) and arr[i] is not None:
        return float(arr[i])
    return None


def _window_sum(wx: dict, key: str, a: int, b: int) -> float | None:
    """Soma de [a, b). None se a janela sai dos limites da previsão."""
    arr = wx.get(key) or []
    if a < 0 or b > len(arr) or b <= a:
        return None
    return float(sum(x for x in arr[a:b] if x is not None))


def _window_max(wx: dict, key: str, a: int, b: int) -> float | None:
    arr = wx.get(key) or []
    vals = [x for x in arr[max(a, 0):min(b, len(arr))] if x is not None]
    return float(max(vals)) if vals else None


def _fmt(x: float, nd: int = 0) -> str:
    s = f"{x:.{nd}f}"
    return s.replace(".", ",")


# --------------------------------------------------------------------------
# Construção de critérios
# --------------------------------------------------------------------------
def _check(cid: str, label: str, group: str, status: str, text: str, sms: str,
           value: float | None = None, unit: str = "") -> dict[str, Any]:
    return {"id": cid, "label": label, "group": group, "status": status,
            "text": text, "sms": sms, "value": value, "unit": unit}


def _band(v: float, lim: dict[str, float]) -> str:
    """Classifica v: fail_low / warn_low / ok / warn_high / fail_high."""
    if "fail_high" in lim and v > lim["fail_high"]:
        return "fail_high"
    if "fail_low" in lim and v < lim["fail_low"]:
        return "fail_low"
    if "warn_high" in lim and v > lim["warn_high"]:
        return "warn_high"
    if "warn_low" in lim and v < lim["warn_low"]:
        return "warn_low"
    return "ok"


def _banded(cid, label, group, v, lim, unit, nd, texts, smss) -> dict[str, Any]:
    band = _band(v, lim)
    status = "ok" if band == "ok" else band.split("_")[0]
    s = _fmt(v, nd)
    return _check(cid, label, group, status, texts[band].format(v=s),
                  smss[band].format(v=s), round(v, 2), unit)


# --------------------------------------------------------------------------
# Regras por operação (avaliam a hora i, olhando para frente quando preciso)
# --------------------------------------------------------------------------
def _spray(wx: dict, i: int) -> list[dict]:
    L = LIMITS
    out: list[dict] = []
    w = _v(wx, "wind_speed_10m", i)
    g = _v(wx, "wind_gusts_10m", i)
    if w is not None:
        c = _banded(
            "wind", "Vento", "wind", w, L["spray_wind"], "km/h", 0,
            {
                "fail_high": "Vento forte ({v} km/h): a calda derivaria para fora do alvo.",
                "warn_high": "Vento um pouco alto ({v} km/h): use gotas grossas e barra baixa.",
                "warn_low": "Quase sem vento ({v} km/h): risco de inversão térmica, a névoa fica suspensa.",
                "ok": "Vento adequado ({v} km/h).",
            },
            {"fail_high": "Vento {v}km/h", "warn_high": "Vento {v}km/h",
             "warn_low": "Calmaria {v}km/h", "ok": "Vento ok"},
        )
        if g is not None and g > L["spray_gust"]["fail_high"] and c["status"] != "fail":
            c.update(status="fail",
                     text=f"Rajadas de {_fmt(g)} km/h: a calda derivaria para fora do alvo.",
                     sms=f"Rajada {_fmt(g)}km/h")
        out.append(c)
    r = _window_sum(wx, "precipitation", i, i + 4)
    p = _window_max(wx, "precipitation_probability", i, i + 4)
    if r is not None:
        if r >= L["spray_rain_4h_mm"]:
            out.append(_check("rain", "Chuva", "rain", "fail",
                              f"Chuva de {_fmt(r, 1)} mm prevista nas próximas 4 h: lavaria o produto.",
                              f"Chuva {_fmt(r, 1)}mm/4h", round(r, 1), "mm"))
        elif p is not None and p >= L["spray_prob_4h"]:
            out.append(_check("rain", "Chuva", "rain", "warn",
                              f"Chance de chuva de {_fmt(p)}% nas próximas 4 h: confira o tempo de secagem do produto.",
                              f"Chance chuva {_fmt(p)}%", round(r, 1), "mm"))
        else:
            out.append(_check("rain", "Chuva", "rain", "ok",
                              "Sem chuva prevista nas próximas 4 h.", "Sem chuva",
                              round(r, 1), "mm"))
    t = _v(wx, "temperature_2m", i)
    if t is not None:
        out.append(_banded(
            "temp", "Temperatura", "temp", t, L["spray_temp"], "°C", 0,
            {
                "fail_high": "Muito quente ({v} °C): o produto evapora antes de chegar à planta.",
                "warn_high": "Calor chegando ({v} °C): prefira o início da manhã ou fim da tarde.",
                "warn_low": "Frio ({v} °C): a absorção do produto pode ser menor.",
                "ok": "Temperatura adequada ({v} °C).",
            },
            {"fail_high": "Calor {v}C", "warn_high": "Calor {v}C",
             "warn_low": "Frio {v}C", "ok": "Temp ok"},
        ))
    rh = _v(wx, "relative_humidity_2m", i)
    if rh is not None:
        out.append(_banded(
            "humidity", "Umidade do ar", "temp", rh, L["spray_rh"], "%", 0,
            {
                "fail_low": "Ar muito seco ({v}%): as gotas evaporam no caminho.",
                "warn_low": "Ar um pouco seco ({v}%): use gotas maiores.",
                "ok": "Umidade do ar adequada ({v}%).",
            },
            {"fail_low": "Ar seco {v}%", "warn_low": "Ar seco {v}%", "ok": "UR ok"},
        ))
    return out


def _plant(wx: dict, i: int) -> list[dict]:
    L = LIMITS
    out: list[dict] = []
    sm = _v(wx, "soil_moisture_9_to_27cm", i)
    if sm is not None:
        out.append(_banded(
            "soil", "Umidade do solo", "soil", sm * 100, L["plant_soil_moist"], "%", 0,
            {
                "fail_low": "Solo seco demais ({v}%): a semente não germina bem.",
                "warn_low": "Solo meio seco ({v}%): confira a previsão de chuva.",
                "warn_high": "Solo bem úmido ({v}%): cuidado com compactação.",
                "fail_high": "Solo encharcado ({v}%): risco de apodrecer a semente.",
                "ok": "Umidade do solo boa para plantar ({v}%).",
            },
            {"fail_low": "Solo seco {v}%", "warn_low": "Solo seco {v}%",
             "warn_high": "Solo umido {v}%", "fail_high": "Solo encharcado {v}%",
             "ok": "Solo ok"},
        ))
    st = _v(wx, "soil_temperature_6cm", i)
    if st is not None:
        out.append(_banded(
            "soil_temp", "Temperatura do solo", "soil", st, L["plant_soil_temp"], "°C", 0,
            {
                "fail_low": "Solo frio ({v} °C): germinação lenta e irregular.",
                "warn_low": "Solo ainda fresco ({v} °C): germinação pode demorar.",
                "warn_high": "Solo quente ({v} °C): atenção à emergência.",
                "fail_high": "Solo muito quente ({v} °C): estresse para a plântula.",
                "ok": "Temperatura do solo boa ({v} °C).",
            },
            {"fail_low": "Solo frio {v}C", "warn_low": "Solo frio {v}C",
             "warn_high": "Solo quente {v}C", "fail_high": "Solo quente {v}C",
             "ok": "Solo temp ok"},
        ))
    r = _window_sum(wx, "precipitation", i, i + 24)
    if r is not None:
        out.append(_banded(
            "rain", "Chuva", "rain", r, L["plant_rain_24h"], "mm", 0,
            {
                "fail_high": "Chuva forte prevista ({v} mm em 24 h): risco de erosão e de lavar a semente.",
                "warn_high": "Bastante chuva prevista ({v} mm em 24 h).",
                "ok": "Sem chuva forte prevista em 24 h ({v} mm).",
            },
            {"fail_high": "Chuva {v}mm/24h", "warn_high": "Chuva {v}mm/24h", "ok": "Chuva ok"},
        ))
    return out


def _fertilize(wx: dict, i: int) -> list[dict]:
    L = LIMITS
    out: list[dict] = []
    r = _window_sum(wx, "precipitation", i, i + 24)
    if r is not None:
        out.append(_banded(
            "rain", "Chuva", "rain", r, L["fert_rain_24h"], "mm", 0,
            {
                "fail_high": "Chuva forte prevista ({v} mm em 24 h): o adubo seria levado pela água.",
                "warn_high": "Chuva moderada prevista ({v} mm em 24 h): parte do adubo pode escorrer.",
                "ok": "Chuva prevista em 24 h: {v} mm.",
            },
            {"fail_high": "Chuva {v}mm/24h", "warn_high": "Chuva {v}mm/24h", "ok": "Chuva ok"},
        ))
        r48 = _window_sum(wx, "precipitation", i, i + 48)
        if r48 is not None and r48 < L["fert_dry_48h_mm"] and out[-1]["status"] == "ok":
            out[-1].update(status="warn",
                           text="Sem chuva prevista por 48 h: o adubo nitrogenado pode perder N para o ar.",
                           sms="Sem chuva 48h")
    sm = _v(wx, "soil_moisture_9_to_27cm", i)
    if sm is not None:
        out.append(_banded(
            "soil", "Umidade do solo", "soil", sm * 100, L["fert_soil_moist"], "%", 0,
            {
                "fail_high": "Solo encharcado ({v}%): o adubo escorre e se perde.",
                "warn_low": "Solo seco ({v}%): o adubo demora a ser aproveitado.",
                "ok": "Umidade do solo adequada ({v}%).",
            },
            {"fail_high": "Solo encharcado {v}%", "warn_low": "Solo seco {v}%", "ok": "Solo ok"},
        ))
    w = _v(wx, "wind_speed_10m", i)
    if w is not None:
        out.append(_banded(
            "wind", "Vento", "wind", w, L["fert_wind"], "km/h", 0,
            {
                "fail_high": "Vento forte ({v} km/h): o adubo a lanço se espalha de forma desigual.",
                "warn_high": "Vento moderado ({v} km/h): regule a distribuição.",
                "ok": "Vento adequado ({v} km/h).",
            },
            {"fail_high": "Vento {v}km/h", "warn_high": "Vento {v}km/h", "ok": "Vento ok"},
        ))
    t = _v(wx, "temperature_2m", i)
    if t is not None:
        out.append(_banded(
            "temp", "Temperatura", "temp", t, L["fert_temp"], "°C", 0,
            {
                "warn_high": "Calor ({v} °C): ureia perde mais nitrogênio por volatilização.",
                "ok": "Temperatura adequada ({v} °C).",
            },
            {"warn_high": "Calor {v}C", "ok": "Temp ok"},
        ))
    return out


def _harvest(wx: dict, i: int) -> list[dict]:
    L = LIMITS
    out: list[dict] = []
    r = _window_sum(wx, "precipitation", i, i + 6)
    p = _window_max(wx, "precipitation_probability", i, i + 6)
    if r is not None:
        if r >= L["harv_rain_6h_mm"]:
            out.append(_check("rain", "Chuva", "rain", "fail",
                              f"Chuva de {_fmt(r, 1)} mm prevista nas próximas 6 h: risco de perda e grão úmido.",
                              f"Chuva {_fmt(r, 1)}mm/6h", round(r, 1), "mm"))
        elif p is not None and p >= 70:
            out.append(_check("rain", "Chuva", "rain", "warn",
                              f"Chance de chuva de {_fmt(p)}% nas próximas 6 h: tenha plano para parar.",
                              f"Chance chuva {_fmt(p)}%", round(r, 1), "mm"))
        else:
            out.append(_check("rain", "Chuva", "rain", "ok",
                              "Sem chuva prevista nas próximas 6 h.", "Sem chuva",
                              round(r, 1), "mm"))
    ss = _v(wx, "soil_moisture_0_to_1cm", i)
    if ss is not None:
        out.append(_banded(
            "soil", "Umidade do solo", "soil", ss * 100, L["harv_surface_moist"], "%", 0,
            {
                "fail_high": "Solo muito molhado na superfície ({v}%): a máquina atola e compacta.",
                "warn_high": "Superfície ainda úmida ({v}%): cuidado com tráfego pesado.",
                "ok": "Solo firme para a máquina ({v}%).",
            },
            {"fail_high": "Solo molhado {v}%", "warn_high": "Solo umido {v}%", "ok": "Solo firme"},
        ))
    rh = _v(wx, "relative_humidity_2m", i)
    if rh is not None:
        out.append(_banded(
            "humidity", "Umidade do ar", "temp", rh, L["harv_rh"], "%", 0,
            {
                "fail_high": "Ar muito úmido ({v}%): grão entra úmido e a secagem custa mais.",
                "warn_high": "Ar úmido ({v}%): melhor esperar a umidade baixar.",
                "ok": "Umidade do ar adequada ({v}%).",
            },
            {"fail_high": "Ar umido {v}%", "warn_high": "Ar umido {v}%", "ok": "UR ok"},
        ))
    g = _v(wx, "wind_gusts_10m", i)
    if g is not None:
        out.append(_banded(
            "wind", "Vento", "wind", g, L["harv_gust"], "km/h", 0,
            {
                "fail_high": "Rajadas fortes ({v} km/h): perdas na plataforma e risco de acamamento.",
                "warn_high": "Rajadas moderadas ({v} km/h): acompanhe as perdas.",
                "ok": "Vento sem risco para a colheita.",
            },
            {"fail_high": "Rajada {v}km/h", "warn_high": "Rajada {v}km/h", "ok": "Vento ok"},
        ))
    return out


_RULES: dict[str, Callable[[dict, int], list[dict]]] = {
    "plantar": _plant,
    "pulverizar": _spray,
    "adubar": _fertilize,
    "colher": _harvest,
}


# --------------------------------------------------------------------------
# Análise completa
# --------------------------------------------------------------------------
def _worst(checks: list[dict]) -> str:
    return max((c["status"] for c in checks), key=_ORDER.__getitem__, default="ok")


def _score(checks: list[dict]) -> int:
    return min(100, sum(WEIGHT.get(c["status"], 0) for c in checks))


def risk_level(score: int) -> str:
    if score < 20:
        return "baixo"
    if score < 50:
        return "médio"
    return "alto"


def _when_label(times: list[str], idx: int, now_idx: int) -> str:
    """Rótulo curto: 'hoje 14h', 'amanhã 06h', 'qui 06h'."""
    t = datetime.fromisoformat(times[idx])
    n = datetime.fromisoformat(times[now_idx])
    days = (t.date() - n.date()).days
    if days <= 0:
        day = "hoje"
    elif days == 1:
        day = "amanhã"
    else:
        day = DOW_PT[t.weekday()][:3]
    return f"{day} {t.hour:02d}h"


def _range_label(times: list[str], a: int, b: int, now_idx: int, open_ended: bool) -> str:
    """'hoje 15h–17h', 'amanhã 06h–11h', 'hoje 15h até amanhã 02h' ou 'amanhã 06h em diante'."""
    start = _when_label(times, a, now_idx)
    if open_ended:
        return f"{start} em diante"
    ta, tb = datetime.fromisoformat(times[a]), datetime.fromisoformat(times[b])
    if ta.date() == tb.date():
        return f"{start}–{tb.hour:02d}h"
    return f"{start} até {_when_label(times, b, now_idx)}"


def _find_window(statuses: list[str], start: int, min_hours: int) -> tuple[int, int] | None:
    """Primeira sequência sem 'fail' com >= min_hours a partir de start (índices relativos)."""
    run_start = None
    for k in range(start, len(statuses) + 1):
        ok = k < len(statuses) and statuses[k] != "fail"
        if ok and run_start is None:
            run_start = k
        if not ok and run_start is not None:
            if k - run_start >= min_hours:
                return run_start, k
            run_start = None
    return None


def _tiles(wx: dict, checks: list[dict]) -> list[dict]:
    i = wx["now_index"]

    def status_for(group: str) -> str:
        return _worst([c for c in checks if c["group"] == group])

    def n(key: str) -> float | None:
        return _v(wx, key, i)

    r24 = _window_sum(wx, "precipitation", i, i + 24)
    r_past = _window_sum(wx, "precipitation", max(i - 48, 0), i)
    wind, gust = n("wind_speed_10m"), n("wind_gusts_10m")
    sm, ss, st = n("soil_moisture_9_to_27cm"), n("soil_moisture_0_to_1cm"), n("soil_temperature_6cm")
    t, rh = n("temperature_2m"), n("relative_humidity_2m")

    def val(x, unit, nd=0):
        return "—" if x is None else f"{_fmt(x, nd)} {unit}"

    return [
        {"id": "rain", "icon": "🌧️", "label": "Chuva nas próx. 24 h",
         "value": val(r24, "mm", 1),
         "sub": f"Últimas 48 h: {val(r_past, 'mm', 1)}", "status": status_for("rain")},
        {"id": "wind", "icon": "🌬️", "label": "Vento",
         "value": val(wind, "km/h"),
         "sub": f"Rajadas: {val(gust, 'km/h')}", "status": status_for("wind")},
        {"id": "soil", "icon": "🌍", "label": "Umidade do solo",
         "value": val(None if sm is None else sm * 100, "%"),
         "sub": f"Superfície {val(None if ss is None else ss * 100, '%')} · solo {val(st, '°C')}",
         "status": status_for("soil")},
        {"id": "temp", "icon": "🌡️", "label": "Temperatura",
         "value": val(t, "°C"),
         "sub": f"Umidade do ar: {val(rh, '%')}", "status": status_for("temp")},
    ]


def analyze(wx: dict[str, Any], activity: str) -> dict[str, Any]:
    """Avalia a operação agora e nas próximas 48 h."""
    if activity not in ACTIVITIES:
        raise ValueError(f"Operação desconhecida: {activity}")
    meta = ACTIVITIES[activity]
    rule = _RULES[activity]
    times: list[str] = wx["times"]
    i0: int = wx["now_index"]
    last = min(len(times) - 1, i0 + SCAN_HOURS - 1)

    per_hour = [rule(wx, i) for i in range(i0, last + 1)]
    statuses = [_worst(c) for c in per_hour]
    checks_now = per_hour[0]
    if not checks_now:
        raise ValueError("Dados insuficientes para decidir")
    min_h = meta["min_hours"]

    # Quanto tempo a condição boa dura a partir de agora
    run_len = 0
    for s in statuses:
        if s == "fail":
            break
        run_len += 1
    reaches_end = run_len == len(statuses)
    now_fails = statuses[0] == "fail"
    short_window = (not now_fails) and (not reaches_end) and run_len < min_h
    go = (not now_fails) and (not short_window)

    # Próxima janela boa
    search_from = 0 if not go else run_len
    nxt = _find_window(statuses, search_from, min_h) if not go else None
    window = None
    if nxt:
        a, b = nxt
        end_idx = min(i0 + b, len(times) - 1)
        open_ended = b >= len(statuses)
        window = {
            "start": times[i0 + a], "end": times[end_idx],
            "hours": b - a,
            "open_ended": open_ended,
            "label": _range_label(times, i0 + a, end_idx, i0, open_ended),
        }

    fails = [c for c in checks_now if c["status"] == "fail"]
    warns = [c for c in checks_now if c["status"] == "warn"]
    score = _score(checks_now)
    if short_window:
        score = max(score, 25)

    verb, noun = meta["verb"], meta["noun"]
    if go:
        headline = f"SIM, pode {verb} agora"
        good = [c["text"] for c in checks_now if c["status"] == "ok"][:2]
        dur = f"mais de {run_len} h" if reaches_end else f"cerca de {run_len} h"
        reason = " ".join(good) if good else "Condições favoráveis."
        reason += f" A janela boa deve durar {dur}."
    else:
        headline = f"NÃO, não {meta['neg']} agora"
        if fails:
            reason = " ".join(c["text"] for c in fails[:2])
        else:
            reason = (f"A janela boa dura só {run_len} h e a {noun} precisa de pelo menos {min_h} h "
                      f"seguidas.")
    cautions = [c["text"] for c in warns]

    # SMS: curto e sem acentos
    if fails:
        sms_reason = ", ".join(c["sms"] for c in fails[:2])
    elif short_window:
        sms_reason = f"Janela curta {run_len}h"
    else:
        sms_reason = ", ".join([c["sms"] for c in warns[:1]] or ["Condicoes boas"])

    timeline = [
        {"time": times[i0 + k], "status": s} for k, s in enumerate(statuses)
    ]
    return {
        "activity": activity,
        "activity_label": meta["label"],
        "go": go,
        "verdict": "SIM" if go else "NÃO",
        "headline": headline,
        "reason": reason,
        "cautions": cautions,
        "risk": {"score": score, "level": risk_level(score)},
        "window": window,
        "window_now_hours": run_len,
        "window_now_open_ended": reaches_end,
        "checks": checks_now,
        "tiles": _tiles(wx, checks_now),
        "timeline": timeline,
        "sms_reason": sms_reason,
        "now": times[i0],
    }
