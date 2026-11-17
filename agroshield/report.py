"""Relatório em PDF (reportlab, Python puro — roda na Vercel)."""
from __future__ import annotations

import io
from datetime import datetime
from typing import Any

from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate, Spacer,
                                Table, TableStyle)

GREEN = colors.HexColor("#14532d")
GO = colors.HexColor("#15803d")
NOGO = colors.HexColor("#b91c1c")
WARN = colors.HexColor("#b45309")
INK = colors.HexColor("#1f2937")
MUTED = colors.HexColor("#6b7280")
LINE = colors.HexColor("#d1d5db")
SOFT = colors.HexColor("#f3f4f6")

STATUS_TXT = {"ok": "OK", "warn": "ATENÇÃO", "fail": "IMPEDE"}
STATUS_COLOR = {"ok": GO, "warn": WARN, "fail": NOGO, "info": MUTED}


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()["BodyText"]
    return {
        "body": ParagraphStyle("body", parent=base, fontName="Helvetica", fontSize=10,
                               leading=14, textColor=INK),
        "small": ParagraphStyle("small", parent=base, fontName="Helvetica", fontSize=8,
                                leading=11, textColor=MUTED),
        "h2": ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold", fontSize=12,
                             leading=16, textColor=GREEN, spaceBefore=10, spaceAfter=4),
        "title": ParagraphStyle("title", parent=base, fontName="Helvetica-Bold",
                                fontSize=18, leading=22, textColor=colors.white),
        "sub": ParagraphStyle("sub", parent=base, fontName="Helvetica", fontSize=9,
                              leading=12, textColor=colors.HexColor("#dcfce7")),
        "verdict": ParagraphStyle("verdict", parent=base, fontName="Helvetica-Bold",
                                  fontSize=22, leading=26, textColor=colors.white),
        "verdict_sub": ParagraphStyle("verdict_sub", parent=base, fontName="Helvetica",
                                      fontSize=10.5, leading=15, textColor=colors.white),
        "cell": ParagraphStyle("cell", parent=base, fontName="Helvetica", fontSize=9,
                               leading=12, textColor=INK),
        "cellb": ParagraphStyle("cellb", parent=base, fontName="Helvetica-Bold",
                                fontSize=9, leading=12, textColor=INK),
    }


def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _timeline_drawing(timeline: list[dict], rain: list[float], width: float) -> Drawing:
    """Faixa de 48 h (verde/amarelo/vermelho) + barras de chuva."""
    n = max(len(timeline), 1)
    h_strip, h_rain, gap = 14, 34, 16
    d = Drawing(width, h_strip + gap + h_rain + 16)
    cell_w = width / n
    top = h_rain + gap
    palette = {"ok": GO, "warn": colors.HexColor("#f59e0b"), "fail": NOGO}
    for k, item in enumerate(timeline):
        d.add(Rect(k * cell_w, top, cell_w - 0.6, h_strip, fillColor=palette[item["status"]],
                   strokeColor=None))
    max_r = max(rain) if rain and max(rain) > 0 else 1.0
    for k, r in enumerate(rain[:n]):
        bh = (r / max_r) * (h_rain - 4)
        if bh > 0:
            d.add(Rect(k * cell_w, 2, cell_w - 0.6, bh,
                       fillColor=colors.HexColor("#2563eb"), strokeColor=None))
    d.add(Line(0, 2, width, 2, strokeColor=LINE, strokeWidth=0.5))
    for k in range(0, n, 6):
        label = timeline[k]["time"][11:13] + "h"
        d.add(String(k * cell_w, top + h_strip + 3, label, fontName="Helvetica",
                     fontSize=6.5, fillColor=MUTED))
    d.add(String(width - 2, 6 + (h_rain - 4), f"chuva máx. {max_r:.1f} mm/h",
                 fontName="Helvetica", fontSize=6.5, fillColor=MUTED, textAnchor="end"))
    return d


def build_pdf(*, result: dict[str, Any], wx: dict[str, Any], place: str | None,
              lat: float, lon: float, area_ha: float | None,
              satellite: dict[str, Any] | None) -> bytes:
    st = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
        topMargin=14 * mm, bottomMargin=16 * mm,
        title=f"AgroShield – {result['activity_label']}", author="AgroShield",
    )
    width = A4[0] - 32 * mm
    story: list = []

    # Cabeçalho
    stamp = datetime.fromisoformat(result["now"]).strftime("%d/%m/%Y %H:%M")
    head = Table(
        [[Paragraph("AgroShield", st["title"]),
          Paragraph(f"Relatório de decisão<br/>Dados de {stamp} (hora local do talhão)", st["sub"])]],
        colWidths=[width * 0.4, width * 0.6],
    )
    head.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), GREEN), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10), ("ALIGN", (1, 0), (1, 0), "RIGHT"),
    ]))
    story += [head, Spacer(1, 8)]

    # Local
    area_txt = f"{area_ha:,.1f} ha".replace(",", "X").replace(".", ",").replace("X", ".") if area_ha else "ponto marcado"
    meta = [
        [Paragraph("Operação", st["cellb"]), Paragraph(_esc(result["activity_label"]), st["cell"]),
         Paragraph("Local", st["cellb"]), Paragraph(_esc(place or "Área selecionada no mapa"), st["cell"])],
        [Paragraph("Coordenadas", st["cellb"]), Paragraph(f"{lat:.5f}, {lon:.5f}", st["cell"]),
         Paragraph("Área", st["cellb"]), Paragraph(area_txt, st["cell"])],
    ]
    mt = Table(meta, colWidths=[width * 0.16, width * 0.34, width * 0.14, width * 0.36])
    mt.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, LINE), ("BACKGROUND", (0, 0), (0, -1), SOFT),
        ("BACKGROUND", (2, 0), (2, -1), SOFT), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story += [mt, Spacer(1, 10)]

    # Veredito
    go = result["go"]
    window = result.get("window")
    win_line = ""
    if not go:
        win_line = (f"<br/>Próxima janela boa: <b>{_esc(window['label'])}</b>" if window
                    else "<br/>Sem janela boa nas próximas 48 h.")
    verdict = Table(
        [[Paragraph(_esc(result["headline"]), st["verdict"])],
         [Paragraph(_esc(result["reason"]) + win_line +
                    f"<br/>Risco de condição ruim: <b>{result['risk']['level'].upper()}</b>",
                    st["verdict_sub"])]],
        colWidths=[width],
    )
    verdict.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), GO if go else NOGO),
        ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (0, 0), 10), ("BOTTOMPADDING", (0, -1), (-1, -1), 10),
    ]))
    story += [verdict]
    if result["cautions"]:
        story += [Spacer(1, 6), Paragraph(
            "<b>Atenção:</b> " + " ".join(_esc(c) for c in result["cautions"]), st["body"])]

    # Indicadores
    story += [Paragraph("Condições agora", st["h2"])]
    rows = []
    for t in result["tiles"]:
        col = STATUS_COLOR.get(t["status"], MUTED)
        rows.append([
            Paragraph(_esc(t["label"]), st["cellb"]),
            Paragraph(f"<b>{_esc(t['value'])}</b>", st["cell"]),
            Paragraph(_esc(t["sub"]), st["cell"]),
            Paragraph(f'<font color="{col.hexval()}"><b>{STATUS_TXT.get(t["status"], "—")}</b></font>',
                      st["cell"]),
        ])
    it = Table(rows, colWidths=[width * 0.28, width * 0.17, width * 0.39, width * 0.16])
    it.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story += [it]

    # Por quê
    story += [Paragraph("Por que essa resposta", st["h2"])]
    crow = [[Paragraph("Critério", st["cellb"]), Paragraph("Situação", st["cellb"]),
             Paragraph("Explicação", st["cellb"])]]
    for c in result["checks"]:
        col = STATUS_COLOR[c["status"]]
        crow.append([
            Paragraph(_esc(c["label"]), st["cell"]),
            Paragraph(f'<font color="{col.hexval()}"><b>{STATUS_TXT[c["status"]]}</b></font>', st["cell"]),
            Paragraph(_esc(c["text"]), st["cell"]),
        ])
    ct = Table(crow, colWidths=[width * 0.22, width * 0.14, width * 0.64], repeatRows=1)
    ct.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, LINE), ("BACKGROUND", (0, 0), (-1, 0), SOFT),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story += [ct]

    # Próximas 48 h
    i0 = wx["now_index"]
    n = len(result["timeline"])
    rain = [float(x or 0) for x in wx["precipitation"][i0:i0 + n]]
    story += [KeepTogether([
        Paragraph("Próximas 48 horas", st["h2"]),
        _timeline_drawing(result["timeline"], rain, width),
        Paragraph("Faixa superior: verde = pode, amarelo = atenção, vermelho = não pode. "
                  "Barras azuis: chuva por hora.", st["small"]),
    ])]

    # Satélite
    story += [Paragraph("Imagem de satélite", st["h2"])]
    if satellite and satellite.get("datetime"):
        cloud = satellite.get("cloud_cover")
        age = satellite.get("age_days")
        txt = (f"Cena Sentinel-2 mais recente com pouca nuvem sobre o ponto: "
               f"{satellite['datetime'][:10]}"
               + (f" (há {age} dias)" if age is not None else "")
               + (f", nuvens {cloud:.0f}%" if cloud is not None else "") + ".")
    else:
        txt = "Não foi encontrada imagem Sentinel-2 recente com pouca nuvem sobre este ponto."
    story += [Paragraph(_esc(txt), st["body"])]

    # Rodapé
    story += [Spacer(1, 10), Paragraph(
        "Fontes: Open-Meteo (previsão e solo, modelo numérico de ~10 km de resolução), "
        "Sentinel-2 / Copernicus via Earth Search (Element 84). "
        "O AgroShield é um apoio à decisão: os valores são estimativas para o ponto central da área "
        "e os limites são referências gerais. Confirme as condições no campo e siga a bula do produto "
        "e a orientação do seu engenheiro agrônomo.", st["small"])]

    def footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(16 * mm, 9 * mm, "AgroShield · apoio à decisão no campo")
        canvas.drawRightString(A4[0] - 16 * mm, 9 * mm, f"Página {doc_.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
