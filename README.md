# AgroNew

**Posso plantar, pulverizar, adubar ou colher hoje?** Marque o talhão no mapa e receba um **SIM ou NÃO** claro, com o motivo em uma frase, a próxima janela boa, relatório em PDF e resposta por SMS.

Python (Flask) + front-end estático, pronto para a **Vercel**.

## O que ele faz

- **Mapa de satélite em tela cheia** (Esri World Imagery) com **menu flutuante de vidro** (Local, Operação, Resposta, Ajuda): toque para marcar um ponto, desenhe a área do talhão, busque a cidade, digite coordenadas ou use o GPS. O selo SIM/NÃO fica visível sobre o mapa mesmo com o menu fechado.
- **Decisão para 4 operações** (plantar, pulverizar, adubar, colher) com regras em `agronew/rules.py`:
  vento, rajadas, chuva nas próximas horas, umidade e temperatura do solo, temperatura e umidade do ar.
- **Resposta objetiva**: SIM/NÃO, motivo, risco (baixo/médio/alto), faixa de 48 h (verde/amarelo/vermelho) e próxima janela boa.
- **Dados**: [Open-Meteo](https://open-meteo.com/en/docs) (clima + umidade/temperatura do solo) e **Sentinel-2** via [Earth Search](https://github.com/Element84/earth-search) (cena de satélite mais recente com pouca nuvem).
- **PDF** limpo de 1 página (`POST /api/report.pdf`).
- **SMS para quem está sem internet** (sem botão no app): o produtor manda `PULVERIZAR Sorriso MT` (ou `COLHER -12.54 -55.72`) para o número do projeto e recebe a decisão de volta em até 160 caracteres, sem acentos (webhook `/api/sms/inbound`, Twilio, opcional).
- **Funciona com sinal fraco**: service worker guarda o app; a última resposta fica salva no aparelho.

## Estrutura

```
app.py                 API Flask (rotas /api/*) e servidor local
agronew/
  weather.py           Open-Meteo (clima/solo), geocodificação, cache
  satellite.py         Sentinel-2 (STAC Earth Search)
  rules.py             Motor de decisão e limites (LIMITS)
  sms.py               Texto curto e comandos de SMS recebido
  report.py            PDF (reportlab)
  geo.py               Área e centroide
public/                Front-end (HTML/CSS/JS, Leaflet vendorizado)
tests/                 pytest
vercel.json            Config da função e cache
```

## Rodar localmente

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest                       # testes (usam dados sintéticos)
python app.py                # http://localhost:3000
```

Sem internet para as APIs? `AGRONEW_DEMO=1 python app.py` usa dados sintéticos
(`AGRONEW_DEMO_SCENARIO=good|storm|windy|wet`). **Nunca ative em produção.**

## Publicar na Vercel

1. Importe o repositório na Vercel (detecta Flask em `app.py`; `public/` vai para a CDN).
2. (Opcional) configure as variáveis de `.env.example` em *Settings → Environment Variables*.
3. Para SMS recebido: no Twilio, aponte o webhook *A message comes in* do número para
   `https://SEU-APP.vercel.app/api/sms/inbound` (POST) e defina `PUBLIC_BASE_URL` (a assinatura `X-Twilio-Signature` é validada).


## Leis de UX aplicadas

| Lei | Onde aparece |
|---|---|
| **Hick** (menos opções, decisão mais rápida) | Só 4 operações; uma única resposta; detalhes escondidos em "Por que essa resposta?" |
| **Fitts** (alvos grandes e ao alcance) | Todos os alvos ≥ 48 px; menu flutuante na parte de baixo (zona do polegar); desenho do talhão com botões grandes, não a barra minúscula do Leaflet |
| **Doherty** (resposta < 400 ms) | Esqueleto de carregamento imediato; as 4 operações vêm numa chamada só, então trocar de operação é instantâneo; cache no servidor, na CDN e no aparelho |
| **Von Restorff** (o que importa se destaca) | Veredito grande, verde ou vermelho, com ícone ✓/✕, nunca só cor |
| **Miller** (blocos pequenos) | 4 indicadores (chuva, vento, solo, temperatura), 4 botões de menu, motivo em 1–2 frases |
| **Jakob** (padrões conhecidos) | Busca + GPS + mapa de satélite, como nos apps de mapa; radio buttons nativos |
| **Postel** (seja liberal ao receber) | Aceita cidade, "Cidade UF", coordenadas, GPS, desenho; SMS com ou sem acento, vírgula ou ponto |
| **Tesler** (o sistema absorve a complexidade) | Limites agronômicos, unidades e janelas calculados pelo motor, não pelo produtor |
| **Proximidade e região comum** | Cada etapa em seu cartão; cada indicador com valor, contexto e situação juntos |
| **Zeigarnik / efeito do gradiente de meta** | Botões do menu com ✓ nos passos concluídos (Local, Operação, Resposta) |
| **Pico-fim** | A jornada termina com a ação útil: baixar o PDF |
| **Estética-usabilidade** | Visual limpo, alto contraste (≥ 4,5:1), tipografia legível ao sol |
| **Acessibilidade** | Rótulos, `aria-live`, foco visível, `prefers-reduced-motion`, não depende só de cor |

## Limites e honestidade dos dados

- Clima e solo são **modelos numéricos (~10 km)** e valem para o **centro** da área marcada. A umidade do solo é uma estimativa de modelo, não uma medição de sensor.
- O satélite entra como **imagem de fundo** e como **a cena Sentinel-2 mais recente com pouca nuvem**. O app **ainda não calcula NDVI** sobre o talhão (próximo passo natural: ler as bandas B04/B08 do COG por requisição de faixa).
- Os limites em `rules.LIMITS` são referências gerais de campo (por exemplo, pulverizar com vento de 3 a 10 km/h, abaixo de ~30 °C e umidade do ar acima de ~55%). Ajuste por cultura, solo e produto; bula e agrônomo têm a palavra final.
- Uso comercial do Open-Meteo exige chave (`OPEN_METEO_API_KEY`). Os mapas Esri e OpenStreetMap exigem atribuição (já exibida) e têm termos de uso próprios.
