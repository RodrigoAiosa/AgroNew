/* Service worker mínimo: o app abre mesmo com sinal fraco.
   - Arquivos estáticos: cache primeiro (atualiza em segundo plano).
   - /api e tiles de mapa: sempre rede (o app já guarda a última resposta no aparelho). */
const CACHE = 'agronew-shell-v5';
const SHELL = [
  './', 'index.html', 'styles.css', 'app.js', 'icon.svg', 'manifest.webmanifest',
  'vendor/leaflet/leaflet.css', 'vendor/leaflet/leaflet.js',
  'vendor/leaflet-draw/leaflet.draw.css', 'vendor/leaflet-draw/leaflet.draw.js',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((cs) => {
    for (const c of cs) { if ('focus' in c) return c.focus(); }
    return self.clients.openWindow('./');
  }));
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api/')) return;
  e.respondWith(
    caches.match(e.request).then((hit) => {
      const net = fetch(e.request).then((res) => {
        if (res.ok) caches.open(CACHE).then((c) => c.put(e.request, res.clone()));
        return res;
      }).catch(() => hit);
      return hit || net;
    }),
  );
});
