/* AgroNew — front-end (vanilla JS, sem build). */
(() => {
  'use strict';

  const $ = (s, el = document) => el.querySelector(s);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const STORE_KEY = 'agronew:v1';
  const CACHE_TTL = 5 * 60 * 1000;

  const FALLBACK_ACTS = [
    { id: 'plantar', label: 'Plantar', icon: '🌱' },
    { id: 'pulverizar', label: 'Pulverizar', icon: '💧' },
    { id: 'adubar', label: 'Adubar', icon: '🧪' },
    { id: 'colher', label: 'Colher', icon: '🌾' },
  ];

  const state = {
    lat: null, lon: null, name: '', areaHa: null, polygon: null,
    activity: null, data: null, dataKey: null, stale: null,
    config: { activities: FALLBACK_ACTS },
    reqId: 0, controller: null, restoring: false,
  };
  const cache = new Map();

  /* ---------------------------------------------------------------- mapa */
  const map = L.map('map', { zoomSnap: 0.5, zoomControl: false }).setView([-14.2, -51.9], 4);
  L.control.zoom({ position: 'topright', zoomInTitle: 'Aproximar', zoomOutTitle: 'Afastar' }).addTo(map);
  const satLayer = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
    maxZoom: 18, attribution: 'Imagens © Esri, Maxar, Earthstar Geographics',
  }).addTo(map);
  const streetLayer = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19, attribution: '© OpenStreetMap',
  });
  const fieldLayer = L.featureGroup().addTo(map);
  Object.assign(L.drawLocal.draw.handlers.polygon.tooltip, {
    start: 'Toque para marcar o primeiro canto.',
    cont: 'Toque para marcar o próximo canto.',
    end: 'Toque no primeiro ponto ou em Concluir para fechar.',
  });
  let marker = null;
  let drawer = null;
  let ignoreClickUntil = 0;

  $('#layerBtn').addEventListener('click', (e) => {
    const onStreets = map.hasLayer(streetLayer);
    if (onStreets) { map.removeLayer(streetLayer); satLayer.addTo(map); } else { map.removeLayer(satLayer); streetLayer.addTo(map); }
    e.currentTarget.setAttribute('aria-pressed', String(!onStreets));
    e.currentTarget.textContent = onStreets ? 'Ver mapa de ruas' : 'Ver satélite';
  });

  map.on('click', (e) => {
    if (drawer || Date.now() < ignoreClickUntil) return;
    setPoint(e.latlng, '');
  });

  function summary(text) { $('#fieldSummary').textContent = text; }
  function fmtHa(ha) { return ha.toLocaleString('pt-BR', { maximumFractionDigits: 1 }) + ' ha'; }
  function fmtCoord(lat, lon) { return `${lat.toFixed(4)}, ${lon.toFixed(4)}`; }

  function setPoint(latlng, name, { fly = false, zoom = 15 } = {}) {
    fieldLayer.clearLayers();
    state.polygon = null; state.areaHa = null;
    state.lat = latlng.lat; state.lon = latlng.lng; state.name = name || '';
    state.data = null; updateTags();
    marker = L.marker(latlng, { draggable: true, title: 'Arraste para ajustar' }).addTo(fieldLayer);
    marker.on('dragend', () => {
      const p = marker.getLatLng();
      state.lat = p.lat; state.lon = p.lng; state.name = ''; state.data = null; updateTags();
      updateSummary(); persist(); scheduleRefresh();
    });
    if (fly) map.flyTo(latlng, Math.max(map.getZoom(), zoom), { animate: !reduceMotion });
    afterLocation();
  }

  function polygonArea(latlngs) {
    try { return L.GeometryUtil.geodesicArea(latlngs) / 10000; } catch { return null; }
  }

  function setPolygon(latlngs, name) {
    fieldLayer.clearLayers();
    marker = null;
    const poly = L.polygon(latlngs, { color: '#facc15', weight: 3, fillOpacity: 0.18 }).addTo(fieldLayer);
    const lat = latlngs.reduce((a, p) => a + p.lat, 0) / latlngs.length;
    const lon = latlngs.reduce((a, p) => a + p.lng, 0) / latlngs.length;
    state.lat = lat; state.lon = lon;
    state.data = null; updateTags();
    state.polygon = latlngs.map((p) => [p.lat, p.lng]);
    state.areaHa = polygonArea(latlngs);
    if (name !== undefined) state.name = name;
    map.fitBounds(poly.getBounds(), { padding: [30, 30], animate: !reduceMotion });
    afterLocation();
  }

  function afterLocation() {
    updateSummary(); persist(); updateSteps(); scheduleRefresh(0);
    if (!state.restoring) { if (state.activity) closePanel(); else openPanel('act'); }
  }

  function updateSummary() {
    if (state.lat == null) return summary('Toque no mapa ou busque uma cidade.');
    const parts = [];
    parts.push(state.polygon ? `Área marcada: ${state.areaHa ? fmtHa(state.areaHa) : ''}`.trim() : 'Ponto marcado');
    parts.push(state.name || fmtCoord(state.lat, state.lon));
    if (state.areaHa > 5000) parts.push('Área muito grande: o clima vale para o centro, marque um talhão menor.');
    summary(parts.join(' · '));
  }

  /* ---------------------------------------------------------- desenhar área */
  const drawBar = $('#drawBar');
  function setMode(mode) {
    const draw = mode === 'draw';
    $('#modePoint').classList.toggle('is-active', !draw);
    $('#modePoint').setAttribute('aria-pressed', String(!draw));
    $('#modeDraw').classList.toggle('is-active', draw);
    $('#modeDraw').setAttribute('aria-pressed', String(draw));
  }
  function startDraw() {
    stopDraw();
    drawer = new L.Draw.Polygon(map, {
      allowIntersection: false, showArea: false,
      shapeOptions: { color: '#facc15', weight: 3, fillOpacity: 0.2 },
      drawError: { color: '#dc2626', message: 'Os lados não podem se cruzar.' },
    });
    drawer.enable();
    closePanel();
    document.body.classList.add('drawing');
    setMode('draw');
    drawBar.hidden = false;
    $('#drawDone').disabled = true;
    $('#drawHint').textContent = 'Toque nos cantos do talhão. Mínimo de 3 pontos.';
  }
  function stopDraw() {
    if (drawer) { try { drawer.disable(); } catch { /* já desligado */ } drawer = null; }
    drawBar.hidden = true;
    document.body.classList.remove('drawing');
    setMode('point');
  }
  map.on(L.Draw.Event.DRAWVERTEX, (e) => {
    const n = e.layers ? e.layers.getLayers().length : 0;
    $('#drawDone').disabled = n < 3;
    $('#drawHint').textContent = n < 3 ? `${n} ponto(s). Marque pelo menos 3 cantos.` : `${n} pontos. Toque em Concluir quando fechar o talhão.`;
  });
  map.on(L.Draw.Event.CREATED, (e) => {
    ignoreClickUntil = Date.now() + 500;
    const latlngs = e.layer.getLatLngs()[0];
    drawer = null; drawBar.hidden = true; document.body.classList.remove('drawing'); setMode('point');
    setPolygon(latlngs);
  });
  map.on(L.Draw.Event.DRAWSTOP, () => { drawer = null; drawBar.hidden = true; document.body.classList.remove('drawing'); setMode('point'); ignoreClickUntil = Date.now() + 500; });
  $('#modeDraw').addEventListener('click', startDraw);
  $('#modePoint').addEventListener('click', stopDraw);
  $('#drawUndo').addEventListener('click', () => drawer && drawer.deleteLastVertex());
  $('#drawDone').addEventListener('click', () => drawer && drawer.completeShape());
  $('#drawCancel').addEventListener('click', stopDraw);

  /* ------------------------------------------------------------------ busca */
  const q = $('#q'); const sug = $('#suggestions');
  let sugTimer = null; let sugResults = [];
  const COORD_RE = /^\s*(-?\d{1,2}(?:[.,]\d+)?)\s*[;,\s]\s*(-?\d{1,3}(?:[.,]\d+)?)\s*$/;

  function hideSug() { sug.hidden = true; sug.innerHTML = ''; q.setAttribute('aria-expanded', 'false'); }
  function placeLabel(r) { return r.admin1 ? `${r.name} - ${r.admin1}` : r.name; }
  function pickPlace(r) {
    hideSug();
    q.value = placeLabel(r);
    setPoint(L.latLng(r.lat, r.lon), placeLabel(r), { fly: true, zoom: 13 });
  }
  async function fetchPlaces(text) {
    const res = await fetch('/api/geocode?q=' + encodeURIComponent(text));
    if (!res.ok) throw new Error('geocode');
    return (await res.json()).results || [];
  }
  q.addEventListener('input', () => {
    clearTimeout(sugTimer);
    const v = q.value.trim();
    if (v.length < 3) return hideSug();
    sugTimer = setTimeout(async () => {
      try {
        sugResults = await fetchPlaces(v);
        if (!sugResults.length) return hideSug();
        sug.innerHTML = sugResults.map((r, i) => `<li role="option"><button type="button" data-i="${i}">${esc(r.name)}<small>${esc([r.admin1, r.country].filter(Boolean).join(' · '))}</small></button></li>`).join('');
        sug.hidden = false; q.setAttribute('aria-expanded', 'true');
      } catch { hideSug(); }
    }, 300);
  });
  sug.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-i]');
    if (b) pickPlace(sugResults[Number(b.dataset.i)]);
  });
  document.addEventListener('click', (e) => { if (!e.target.closest('.search')) hideSug(); });
  q.addEventListener('keydown', (e) => { if (e.key === 'Escape') hideSug(); });

  $('#searchForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const v = q.value.trim();
    if (!v) return;
    const m = v.match(COORD_RE); // aceita "-12.54, -55.72" digitado
    if (m) {
      const lat = parseFloat(m[1].replace(',', '.')); const lon = parseFloat(m[2].replace(',', '.'));
      if (Math.abs(lat) <= 90 && Math.abs(lon) <= 180) { hideSug(); return setPoint(L.latLng(lat, lon), '', { fly: true, zoom: 15 }); }
    }
    try {
      const results = sugResults.length && !sug.hidden ? sugResults : await fetchPlaces(v);
      if (!results.length) return summary('Não encontrei esse lugar. Tente "Cidade UF" ou toque no mapa.');
      pickPlace(results[0]);
    } catch { summary('Sem conexão para buscar. Toque no mapa para marcar o talhão.'); }
  });

  $('#locateBtn').addEventListener('click', () => {
    if (!navigator.geolocation) return summary('Seu aparelho não permite usar a localização.');
    summary('Buscando sua localização…');
    navigator.geolocation.getCurrentPosition(
      (pos) => setPoint(L.latLng(pos.coords.latitude, pos.coords.longitude), 'Minha localização', { fly: true, zoom: 16 }),
      (err) => summary(err.code === 1 ? 'Localização bloqueada. Libere nas configurações ou toque no mapa.' : 'Não consegui obter a localização. Toque no mapa.'),
      { enableHighAccuracy: true, timeout: 12000, maximumAge: 60000 },
    );
  });

  /* --------------------------------------------------------------- operações */
  function renderActs() {
    $('#acts').innerHTML = state.config.activities.map((a) => `
      <div class="act">
        <input type="radio" name="activity" id="act-${a.id}" value="${a.id}" ${state.activity === a.id ? 'checked' : ''}>
        <label for="act-${a.id}"><span class="ico" aria-hidden="true">${a.icon}</span><span>${esc(a.label)}</span><span class="tag" id="tag-${a.id}" aria-live="polite"></span></label>
      </div>`).join('');
    updateTags();
  }
  $('#acts').addEventListener('change', (e) => {
    if (e.target.name !== 'activity') return;
    state.activity = e.target.value;
    persist(); updateSteps();
    if (state.data) { render(); openPanel('result'); } else { scheduleRefresh(0); }
  });
  function updateTags() {
    for (const a of state.config.activities) {
      const tag = $('#tag-' + a.id);
      if (!tag) continue;
      const r = state.data && state.data.activities[a.id];
      tag.className = 'tag' + (r ? (r.go ? ' go' : ' no') : '');
      tag.textContent = r ? (r.go ? '✓ Pode' : '✕ Não') : '';
    }
  }

  function updateSteps() {
    $('#dock-local').dataset.done = String(state.lat != null);
    $('#dock-act').dataset.done = String(!!state.activity);
    $('#dock-result').dataset.done = String(!!(state.data && state.activity));
  }

  /* ------------------------------------------------------- menu flutuante */
  const panels = { local: $('#panel-local'), act: $('#panel-act'), result: $('#panel-result') };
  let openId = null;
  function openPanel(id) {
    openId = id;
    for (const [k, el] of Object.entries(panels)) {
      const on = k === id;
      el.classList.toggle('open', on);
      if (on) el.removeAttribute('inert'); else el.setAttribute('inert', '');
      $('#dock-' + k).setAttribute('aria-expanded', String(on));
    }
    document.body.classList.add('sheet-open');
  }
  function closePanel() {
    openId = null;
    for (const [k, el] of Object.entries(panels)) {
      el.classList.remove('open'); el.setAttribute('inert', '');
      $('#dock-' + k).setAttribute('aria-expanded', 'false');
    }
    document.body.classList.remove('sheet-open');
  }
  document.querySelectorAll('.dock-btn[data-panel]').forEach((b) =>
    b.addEventListener('click', () => (openId === b.dataset.panel ? closePanel() : openPanel(b.dataset.panel))));
  document.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', closePanel));
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && openId && !$('#helpDialog').open) closePanel(); });

  const pill = $('#verdictPill');
  function updatePill(r) {
    if (!r) { pill.hidden = true; return; }
    pill.className = 'verdict-pill ' + (r.go ? 'go' : 'no');
    pill.innerHTML = `<span aria-hidden="true">${r.go ? '✓' : '✕'}</span>${esc(r.headline)}`;
    pill.hidden = false;
  }
  pill.addEventListener('click', () => openPanel('result'));

  /* -------------------------------------------------------------- análise */
  let refreshTimer = null;
  function scheduleRefresh(delay = 350) {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(refresh, delay);
  }

  function keyOf() { return `${state.lat.toFixed(3)},${state.lon.toFixed(3)}`; }

  async function refresh() {
    if (state.lat == null) return renderEmpty();
    const key = keyOf();
    const hit = cache.get(key);
    if (hit && Date.now() - hit.t < CACHE_TTL) {
      state.data = hit.data; state.dataKey = key; state.stale = null;
      updateTags(); return render();
    }
    const id = ++state.reqId;
    if (state.controller) state.controller.abort();
    state.controller = new AbortController();
    if (state.activity) renderLoading(); else renderEmpty('Consultando clima e solo…');
    try {
      const url = `/api/overview?lat=${state.lat.toFixed(5)}&lon=${state.lon.toFixed(5)}&name=${encodeURIComponent(state.name || '')}`;
      const res = await fetch(url, { signal: state.controller.signal });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.error || 'Falha ao consultar os dados');
      if (id !== state.reqId) return;
      cache.set(key, { t: Date.now(), data: body });
      state.data = body; state.dataKey = key; state.stale = null;
      try { localStorage.setItem(STORE_KEY + ':last', JSON.stringify({ t: Date.now(), key, data: body })); } catch { /* sem storage */ }
      updateTags(); render(); if (!state.restoring && state.activity) openPanel('result');
    } catch (err) {
      if (err.name === 'AbortError' || id !== state.reqId) return;
      const last = readLast(key);
      if (last) { state.data = last.data; state.stale = last.t; updateTags(); render(); }
      else { state.data = null; updateTags(); renderError(err.message); }
    }
  }

  function readLast(key) {
    try { const v = JSON.parse(localStorage.getItem(STORE_KEY + ':last')); return v && v.key === key ? v : null; } catch { return null; }
  }

  /* ------------------------------------------------------------- renderização */
  const body = $('#resultBody');
  const STATUS_LABEL = { ok: 'Ok', warn: 'Atenção', fail: 'Impede', info: 'Info' };
  const STATUS_ICON = { ok: '✓', warn: '!', fail: '✕' };

  function renderEmpty(custom) {
    const msg = custom || (state.lat == null ? 'Marque o talhão no mapa para começar.' : 'Agora escolha o que quer fazer.');
    updatePill(null);
    body.innerHTML = `<div class="empty"><span class="big" aria-hidden="true">🌾</span><p><strong>${msg}</strong></p><p>A resposta — SIM ou NÃO — aparece aqui.</p></div>`;
  }
  function renderLoading() {
    updatePill(null);
    body.innerHTML = '<div class="skeleton" aria-busy="true"><p class="sr-only">Calculando…</p><div class="bar big"></div><div class="bar"></div><div class="grid2"><div class="bar"></div><div class="bar"></div><div class="bar"></div><div class="bar"></div></div></div>';
  }
  function renderError(msg) {
    updatePill(null);
    body.innerHTML = `<div class="error"><div class="big" aria-hidden="true">📡</div><p><strong>Não consegui buscar os dados agora.</strong></p><p class="muted">${esc(msg)}</p><button class="btn btn-primary" id="retryBtn" type="button">Tentar de novo</button></div>`;
    $('#retryBtn').addEventListener('click', () => scheduleRefresh(0));
    if (!state.restoring) openPanel('result');
  }

  function whenLine(r) {
    if (r.go) return `Boa janela ${r.window_now_open_ended ? 'por mais de' : 'por cerca de'} ${r.window_now_hours} h.`;
    return r.window ? `Próxima janela boa: ${r.window.label}` : 'Sem janela boa nas próximas 48 h.';
  }

  function timelineHtml(r) {
    const cells = r.timeline.map((t) => `<i class="${t.status}" title="${esc(t.time.slice(11, 13))}h"></i>`).join('');
    const hh = (k) => (r.timeline[k] ? r.timeline[k].time.slice(11, 13) + 'h' : '');
    return `<div class="timeline"><h4>Próximas 48 horas</h4>
      <div class="strip" role="img" aria-label="Faixa das próximas 48 horas: verde pode, amarelo atenção, vermelho não pode. Agora: ${r.go ? 'pode' : 'não pode'}.">${cells}</div>
      <div class="ticks" aria-hidden="true"><span>agora</span><span>${hh(12)}</span><span>${hh(24)}</span><span>${hh(36)}</span></div>
      <div class="legend-inline"><span><i class="dot ok"></i>Pode</span><span><i class="dot warn"></i>Atenção</span><span><i class="dot fail"></i>Não pode</span></div></div>`;
  }

  function satHtml(s) {
    if (!s || !s.datetime) return '<div class="sat"><span aria-hidden="true">🛰️</span><span>Sem imagem de satélite recente e limpa sobre este ponto.</span></div>';
    const d = new Date(s.datetime).toLocaleDateString('pt-BR');
    const img = s.thumbnail ? `<img src="${esc(s.thumbnail)}" alt="Miniatura da imagem Sentinel-2 de ${esc(d)}" loading="lazy" onerror="this.remove()">` : '<span aria-hidden="true">🛰️</span>';
    return `<div class="sat">${img}<span>Imagem de satélite mais recente: <b>${esc(d)}</b>${s.age_days != null ? ` (há ${s.age_days} dias)` : ''}${s.cloud_cover != null ? `, ${Math.round(s.cloud_cover)}% de nuvens` : ''}.</span></div>`;
  }

  function render() {
    const r = state.data && state.activity && state.data.activities[state.activity];
    if (!r) { updatePill(null); return renderEmpty(); }
    const stale = state.stale ? `<div class="banner" role="status">Sem conexão: mostrando dados salvos às ${new Date(state.stale).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}.</div>` : '';
    const cautions = r.cautions && r.cautions.length ? `<div class="caution"><strong>Atenção</strong><ul>${r.cautions.map((c) => `<li>${esc(c)}</li>`).join('')}</ul></div>` : '';
    const tiles = r.tiles.map((t) => `<div class="tile ${t.status}"><div class="lab"><span aria-hidden="true">${t.icon}</span>${esc(t.label)}</div><div class="val">${esc(t.value)}</div><div class="sub">${esc(t.sub)}</div><span class="st">${STATUS_LABEL[t.status] || ''}</span></div>`).join('');
    const checks = r.checks.map((c) => `<li><span class="ic ${c.status}" aria-hidden="true">${STATUS_ICON[c.status]}</span><span><b class="sr-only">${STATUS_LABEL[c.status]}: </b>${esc(c.text)}</span></li>`).join('');
    body.innerHTML = `${stale}
      <div class="verdict ${r.go ? 'go' : 'no'}" role="group" aria-label="Resposta">
        <div class="mark" aria-hidden="true">${r.go ? '✓' : '✕'}</div>
        <h3>${esc(r.headline)}</h3>
        <p class="why">${esc(r.reason)}</p>
        <p class="when">${esc(whenLine(r))}</p>
      </div>
      <p class="risk ${esc(r.risk.level)}"><span aria-hidden="true">${r.risk.level === 'baixo' ? '●' : r.risk.level === 'médio' ? '▲' : '■'}</span> Risco de condição ruim: ${esc(r.risk.level)}</p>
      ${cautions}
      <div class="tiles">${tiles}</div>
      ${timelineHtml(r)}
      <details class="why-box"><summary>Por que essa resposta?</summary><ul>${checks}</ul></details>
      ${satHtml(state.data.satellite)}
      <div class="actions">
        <button class="btn btn-primary btn-block" id="pdfBtn" type="button"><span aria-hidden="true">📄</span> Baixar relatório (PDF)</button>
      </div>`;
    $('#pdfBtn').addEventListener('click', (e) => downloadPdf(e.currentTarget));
    updatePill(r);
    updateSteps();
  }

  /* -------------------------------------------------------------------- PDF */
  async function downloadPdf(btn) {
    const original = btn.innerHTML;
    btn.disabled = true; btn.textContent = 'Gerando PDF…';
    try {
      const res = await fetch('/api/report.pdf', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lat: state.lat, lon: state.lon, activity: state.activity, area_ha: state.areaHa, name: state.name }),
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || 'Falha ao gerar o PDF');
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = `agronew-${state.activity}.pdf`;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      btn.textContent = '✓ PDF pronto';
      setTimeout(() => { btn.innerHTML = original; btn.disabled = false; }, 2200);
    } catch (err) {
      btn.innerHTML = original; btn.disabled = false;
      alert('Não foi possível gerar o PDF agora. ' + err.message);
    }
  }

  /* ------------------------------------------------------------------ ajuda */
  $('#helpBtn').addEventListener('click', () => $('#helpDialog').showModal());

  /* --------------------------------------------------------- persistência */
  function persist() {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify({
        lat: state.lat, lon: state.lon, name: state.name, areaHa: state.areaHa,
        polygon: state.polygon, activity: state.activity,
      }));
    } catch { /* sem storage */ }
  }
  function restore() {
    state.restoring = true;
    try {
      const s = JSON.parse(localStorage.getItem(STORE_KEY));
      if (!s || s.lat == null) return;
      state.activity = s.activity || null;
      if (s.polygon && s.polygon.length >= 3) {
        setPolygon(s.polygon.map((p) => L.latLng(p[0], p[1])), s.name || '');
      } else {
        setPoint(L.latLng(s.lat, s.lon), s.name || '', { fly: false });
        map.setView([s.lat, s.lon], 14);
      }
    } catch { /* ignora estado inválido */ }
    setTimeout(() => { state.restoring = false; }, 1500);
  }

  /* ----------------------------------------------------------------- início */
  async function init() {
    renderActs(); renderEmpty(); updateSteps();
    try {
      const res = await fetch('/api/config');
      if (res.ok) { state.config = { ...state.config, ...(await res.json()) }; renderActs(); }
    } catch { /* usa padrão */ }
    restore();
    if (state.lat == null) openPanel('local');
    if (state.activity) { const el = $('#act-' + state.activity); if (el) el.checked = true; }
    updateSteps();
    if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => {});
  }
  init();
})();
