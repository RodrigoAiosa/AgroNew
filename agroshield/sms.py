"""Modo SMS: texto curto (<= 160 caracteres, só ASCII) e comandos recebidos.

Dois usos:
  1. Enviar o resultado de uma análise para um celular (Twilio, opcional).
  2. Receber um SMS de quem está sem internet, por exemplo
         PULVERIZAR Sorriso MT
         COLHER -12.54 -55.72
     e responder com a decisão (webhook /api/sms/inbound).
"""
from __future__ import annotations

import os
import re
import threading
import time
import unicodedata
from typing import Any

import requests

from . import rules
from .geo import valid_latlon

SMS_LIMIT = 160

_ALIASES = {
    "PLANTAR": "plantar", "PLANTIO": "plantar", "SEMEAR": "plantar", "PLANTE": "plantar",
    "PULVERIZAR": "pulverizar", "PULVERIZACAO": "pulverizar", "APLICAR": "pulverizar",
    "SPRAY": "pulverizar", "PULVERIZE": "pulverizar",
    "ADUBAR": "adubar", "ADUBACAO": "adubar", "ADUBO": "adubar", "ADUBE": "adubar",
    "COLHER": "colher", "COLHEITA": "colher", "COLHA": "colher",
}
def ascii_only(text: str) -> str:
    """Remove acentos e tudo que não for ASCII imprimível (SMS GSM-7 simples)."""
    norm = unicodedata.normalize("NFKD", text)
    out = norm.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", out).strip()


def _window_text(result: dict[str, Any]) -> str:
    w = result.get("window")
    if result["go"]:
        return ""
    if not w:
        return "Sem janela boa em 48h"
    return "Prox janela: " + ascii_only(w["label"]).replace("–", "-")


def build_sms(result: dict[str, Any], place: str | None = None) -> str:
    """Monta o SMS em uma única mensagem de até 160 caracteres.

    Se não couber, descarta partes menos importantes (local, janela, risco),
    mantendo sempre: operação + SIM/NAO + motivo.
    """
    act = result["activity"].upper()
    verdict = "SIM" if result["go"] else "NAO"
    reason = ascii_only(result["sms_reason"])
    risk = "Risco " + ascii_only(result["risk"]["level"]).upper()
    window = _window_text(result)
    loc = ascii_only(place)[:18] if place else ""

    head = f"AgroShield {act}: {verdict}."
    options = [
        [head, reason + ".", window + "." if window else "", risk + ".", loc],
        [head, reason + ".", window + "." if window else "", risk + "."],
        [head, reason + ".", window + "." if window else ""],
        [head, reason + "."],
    ]
    for parts in options:
        text = " ".join(p for p in parts if p)
        if len(text) <= SMS_LIMIT:
            return text
    return (head + " " + reason)[:SMS_LIMIT]


# --------------------------------------------------------------------------
# Comandos recebidos
# --------------------------------------------------------------------------
HELP_TEXT = (
    "AgroShield: envie OPERACAO + LOCAL. Ex: PULVERIZAR Sorriso MT "
    "ou COLHER -12.54 -55.72. Operacoes: PLANTAR PULVERIZAR ADUBAR COLHER"
)


def _parse_coords(rest: str) -> tuple[float, float] | None:
    """Aceita '-12.54 -55.72', '-12.54,-55.72', '-12,54 -55,72' ou com ';'."""
    tokens = [t.strip(",") for t in re.split(r"[;\s]+", rest.strip()) if t.strip(",")]
    if len(tokens) == 1 and tokens[0].count(",") == 1:
        tokens = tokens[0].split(",")
    if len(tokens) != 2:
        return None
    try:
        lat, lon = (float(t.replace(",", ".")) for t in tokens)
    except ValueError:
        return None
    return (lat, lon) if valid_latlon(lat, lon) else None


def parse_command(body: str) -> dict[str, Any]:
    """Interpreta o SMS. Retorna {'help': True} ou {'activity','coords'|'place'}."""
    text = ascii_only(body or "").upper()
    if not text or text.split()[0] in {"AJUDA", "HELP", "?"}:
        return {"help": True}
    first, _, rest = text.partition(" ")
    activity = _ALIASES.get(first)
    if not activity:
        return {"error": "operacao"}
    rest = rest.strip()
    if not rest:
        return {"error": "local", "activity": activity}
    coords = _parse_coords(rest)
    if coords:
        return {"activity": activity, "coords": coords}
    return {"activity": activity, "place": rest.title()}


# --------------------------------------------------------------------------
# Envio (Twilio)
# --------------------------------------------------------------------------
_sent: dict[str, list[float]] = {}
_lock = threading.Lock()
RATE_WINDOW_S = 3600
RATE_MAX = 5


def sending_enabled() -> bool:
    return all(os.environ.get(k) for k in
               ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN")) and bool(
        os.environ.get("TWILIO_FROM") or os.environ.get("TWILIO_MESSAGING_SERVICE_SID"))


def normalize_phone(raw: str) -> str | None:
    """Converte para E.164. Sem '+', assume Brasil (+55)."""
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return None
    if (raw or "").strip().startswith("+"):
        return "+" + digits if 8 <= len(digits) <= 15 else None
    if digits.startswith("55") and len(digits) in (12, 13):
        return "+" + digits
    if len(digits) in (10, 11):
        return "+55" + digits
    return None


def rate_limited(key: str) -> bool:
    now = time.time()
    with _lock:
        hits = [t for t in _sent.get(key, []) if now - t < RATE_WINDOW_S]
        if len(hits) >= RATE_MAX:
            _sent[key] = hits
            return True
        hits.append(now)
        _sent[key] = hits
        if len(_sent) > 2000:
            _sent.pop(next(iter(_sent)))
    return False


def send_sms(to: str, body: str) -> dict[str, Any]:
    """Envia via Twilio REST. Levanta RuntimeError em falha."""
    sid = os.environ["TWILIO_ACCOUNT_SID"]
    token = os.environ["TWILIO_AUTH_TOKEN"]
    data = {"To": to, "Body": body}
    if os.environ.get("TWILIO_MESSAGING_SERVICE_SID"):
        data["MessagingServiceSid"] = os.environ["TWILIO_MESSAGING_SERVICE_SID"]
    else:
        data["From"] = os.environ["TWILIO_FROM"]
    try:
        resp = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
            data=data, auth=(sid, token), timeout=10,
        )
    except requests.RequestException as exc:
        raise RuntimeError("Falha de rede ao enviar o SMS") from exc
    if resp.status_code >= 300:
        raise RuntimeError(f"O provedor recusou o SMS (HTTP {resp.status_code})")
    return {"sid": resp.json().get("sid"), "status": resp.json().get("status")}


def twiml(message: str) -> str:
    esc = (message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    return f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{esc}</Message></Response>'


def activity_names() -> list[str]:
    return list(rules.ACTIVITIES)
