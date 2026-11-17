"""AgroShield — API Flask (Vercel) + servidor local do front-end.

Na Vercel, os arquivos de ``public/`` são servidos pela CDN e este módulo atende
apenas ``/api/*``. Localmente (``python app.py``), ele também serve ``public/``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from flask import Flask, Response, jsonify, request, send_from_directory

from agroshield import report, rules, satellite, sms, weather
from agroshield.geo import valid_latlon

ROOT = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(ROOT, "public")

app = Flask(__name__, static_folder=None)
app.json.ensure_ascii = False
app.json.sort_keys = False

_pool = ThreadPoolExecutor(max_workers=4)


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


@app.errorhandler(ApiError)
def _api_error(err: ApiError):
    return jsonify({"error": err.message}), err.status


@app.errorhandler(404)
def _not_found(_):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Rota não encontrada"}), 404
    return Response("Não encontrado", status=404)


@app.errorhandler(Exception)
def _unexpected(err: Exception):
    app.logger.exception("Erro inesperado")
    return jsonify({"error": "Erro interno. Tente novamente em instantes."}), 500


def _float(value: Any, name: str) -> float:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        raise ApiError(f"Parâmetro inválido: {name}")


def _location_args(src) -> tuple[float, float, str]:
    lat = _float(src.get("lat"), "lat")
    lon = _float(src.get("lon"), "lon")
    if not valid_latlon(lat, lon):
        raise ApiError("Coordenadas fora do intervalo válido")
    activity = (src.get("activity") or "").lower()
    if activity not in rules.ACTIVITIES:
        raise ApiError("Escolha uma operação: " + ", ".join(rules.ACTIVITIES))
    return lat, lon, activity


def compute(lat: float, lon: float, activity: str) -> dict[str, Any]:
    """Busca clima + satélite em paralelo e roda o motor de decisão."""
    f_wx = _pool.submit(weather.fetch, lat, lon)
    f_sat = _pool.submit(satellite.latest_scene, lat, lon)
    try:
        wx = f_wx.result()
    except weather.WeatherError as exc:
        raise ApiError(str(exc), 502)
    try:
        sat = f_sat.result()
    except Exception:  # satélite é opcional
        sat = None
    try:
        result = rules.analyze(wx, activity)
    except ValueError as exc:
        raise ApiError(str(exc), 422)
    return {"wx": wx, "sat": sat, "result": result}


def _place_label(r: dict[str, Any]) -> str:
    return f"{r.get('name')} - {r.get('admin1')}" if r.get("admin1") else str(r.get("name"))


# --------------------------------------------------------------------------
# Rotas
# --------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"ok": True, "demo": os.environ.get("AGROSHIELD_DEMO") == "1"}


@app.get("/api/config")
def config():
    return {
        "activities": [
            {"id": k, "label": v["label"], "icon": v["icon"]} for k, v in rules.ACTIVITIES.items()
        ],
        "sms_send_enabled": sms.sending_enabled(),
        "sms_number": os.environ.get("SMS_NUMBER", ""),
        "sms_limit": sms.SMS_LIMIT,
    }


@app.get("/api/geocode")
def geocode():
    q = (request.args.get("q") or "").strip()
    resp = jsonify({"results": weather.geocode(q) if len(q) >= 2 else []})
    resp.headers["Cache-Control"] = "public, s-maxage=3600"
    return resp


@app.get("/api/analyze")
def analyze():
    lat, lon, activity = _location_args(request.args)
    data = compute(lat, lon, activity)
    result, wx, sat = data["result"], data["wx"], data["sat"]
    place = (request.args.get("name") or "").strip()[:60] or None
    body = {
        **result,
        "location": {"lat": lat, "lon": lon, "elevation": wx.get("elevation"),
                     "timezone": wx.get("timezone")},
        "satellite": sat,
        "sms": sms.build_sms(result, place),
        "sources": ["Open-Meteo", "Sentinel-2 (Copernicus) via Earth Search"],
    }
    resp = jsonify(body)
    resp.headers["Cache-Control"] = "public, s-maxage=300, stale-while-revalidate=600"
    return resp


@app.get("/api/overview")
def overview():
    """Decisão para as 4 operações de uma vez (troca de operação instantânea no front)."""
    lat = _float(request.args.get("lat"), "lat")
    lon = _float(request.args.get("lon"), "lon")
    if not valid_latlon(lat, lon):
        raise ApiError("Coordenadas fora do intervalo válido")
    place = (request.args.get("name") or "").strip()[:60] or None
    f_wx = _pool.submit(weather.fetch, lat, lon)
    f_sat = _pool.submit(satellite.latest_scene, lat, lon)
    try:
        wx = f_wx.result()
    except weather.WeatherError as exc:
        raise ApiError(str(exc), 502)
    try:
        sat = f_sat.result()
    except Exception:
        sat = None
    activities = {}
    for act in rules.ACTIVITIES:
        try:
            res = rules.analyze(wx, act)
        except ValueError as exc:
            raise ApiError(str(exc), 422)
        activities[act] = {**res, "sms": sms.build_sms(res, place)}
    resp = jsonify({
        "location": {"lat": lat, "lon": lon, "elevation": wx.get("elevation"),
                     "timezone": wx.get("timezone")},
        "satellite": sat,
        "activities": activities,
        "sources": ["Open-Meteo", "Sentinel-2 (Copernicus) via Earth Search"],
    })
    resp.headers["Cache-Control"] = "public, s-maxage=300, stale-while-revalidate=600"
    return resp


@app.post("/api/report.pdf")
def report_pdf():
    payload = request.get_json(silent=True) or {}
    lat, lon, activity = _location_args(payload)
    area = payload.get("area_ha")
    area_ha = None
    if area not in (None, ""):
        area_ha = max(0.0, min(_float(area, "area_ha"), 1_000_000.0)) or None
    place = (str(payload.get("name") or "").strip()[:80]) or None
    data = compute(lat, lon, activity)
    pdf = report.build_pdf(
        result=data["result"], wx=data["wx"], place=place, lat=lat, lon=lon,
        area_ha=area_ha, satellite=data["sat"], sms_text=sms.build_sms(data["result"], place),
    )
    fname = f"agroshield-{activity}-{data['result']['now'][:10]}.pdf"
    return Response(pdf, mimetype="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{fname}"',
        "Cache-Control": "no-store",
    })


@app.post("/api/sms/send")
def sms_send():
    if not sms.sending_enabled():
        raise ApiError("O envio de SMS não está ativado neste servidor.", 503)
    payload = request.get_json(silent=True) or {}
    lat, lon, activity = _location_args(payload)
    phone = sms.normalize_phone(str(payload.get("phone") or ""))
    if not phone:
        raise ApiError("Número de celular inválido. Use DDD + número, ex.: 66 99999-1234.")
    ip = (request.headers.get("x-forwarded-for") or request.remote_addr or "?").split(",")[0].strip()
    if sms.rate_limited(f"ip:{ip}") or sms.rate_limited(f"to:{phone}"):
        raise ApiError("Muitos envios seguidos. Tente de novo em alguns minutos.", 429)
    place = (str(payload.get("name") or "").strip()[:40]) or None
    data = compute(lat, lon, activity)
    text = sms.build_sms(data["result"], place)  # texto sempre gerado no servidor
    try:
        info = sms.send_sms(phone, text)
    except RuntimeError as exc:
        raise ApiError(str(exc), 502)
    masked = phone[:3] + "•" * max(len(phone) - 7, 0) + phone[-4:]
    return {"sent": True, "to": masked, "text": text, **info}


def _twilio_signature_ok() -> bool:
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    if not token or os.environ.get("TWILIO_VALIDATE_SIGNATURE") == "0":
        return True
    sig = request.headers.get("X-Twilio-Signature", "")
    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    url = (base + request.path) if base else request.url
    params = request.form if request.method == "POST" else {}
    data = url + "".join(k + params[k] for k in sorted(params))
    digest = hmac.new(token.encode(), data.encode(), hashlib.sha1).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), sig)


def _answer_sms(body_text: str) -> str:
    cmd = sms.parse_command(body_text)
    if cmd.get("help"):
        return sms.HELP_TEXT
    if cmd.get("error") == "operacao":
        return "AgroShield: nao entendi. " + sms.HELP_TEXT
    if cmd.get("error") == "local":
        return "AgroShield: faltou o local. Ex: " + cmd["activity"].upper() + " Sorriso MT"
    if "coords" in cmd:
        lat, lon = cmd["coords"]
        place = None
    else:
        found = weather.resolve_place(cmd["place"])
        if not found:
            return "AgroShield: nao achei esse local. Envie CIDADE UF ou latitude e longitude."
        lat, lon, place = found["lat"], found["lon"], _place_label(found)
    try:
        data = compute(lat, lon, cmd["activity"])
    except ApiError:
        return "AgroShield: sem dados de clima agora. Tente de novo em alguns minutos."
    return sms.build_sms(data["result"], place)


@app.route("/api/sms/inbound", methods=["GET", "POST"])
def sms_inbound():
    """Webhook de SMS recebido (Twilio). Responde com TwiML ou texto puro."""
    if not _twilio_signature_ok():
        return Response("Assinatura inválida", status=403)
    src = request.form if request.method == "POST" else request.args
    answer = _answer_sms(src.get("Body") or src.get("body") or "")
    if request.args.get("format") == "text":
        return Response(answer, mimetype="text/plain; charset=utf-8")
    return Response(sms.twiml(answer), mimetype="text/xml")


# --------------------------------------------------------------------------
# Front-end no desenvolvimento local (na Vercel, public/ vem da CDN)
# --------------------------------------------------------------------------
if not os.environ.get("VERCEL"):
    @app.get("/", defaults={"path": "index.html"})
    @app.get("/<path:path>")
    def _static(path: str):
        return send_from_directory(PUBLIC, path)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 3000)), debug=True)
