"""Modo SMS: texto curto (<= 160 caracteres, só ASCII) e comandos recebidos.

Resposta a SMS recebido de quem está sem internet, por exemplo
         PULVERIZAR Sorriso MT
         COLHER -12.54 -55.72
     e responder com a decisão (webhook /api/sms/inbound).
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

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


def twiml(message: str) -> str:
    esc = (message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    return f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{esc}</Message></Response>'


def activity_names() -> list[str]:
    return list(rules.ACTIVITIES)
