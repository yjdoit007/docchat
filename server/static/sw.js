/* 문서 도우미 서비스 워커: 홈 화면 앱 설치용.
   - 화면 파일은 항상 서버에서 새로 받고(network-first), 서버에 닿지 못할 때만 저장해 둔 것을 쓴다 → 업데이트가 바로 반영됨
   - /api 요청(질문·답변·문서)은 절대 저장하지 않는다 (증빙: 항상 서버의 최신 원문만 보여 줌)
   - 서버에 닿지 못하면 '연결할 수 없어요' 안내 화면 */
var CACHE = "docchat-v1";
var SHELL = ["/", "/index.html", "/app.css", "/app.js", "/manifest.json",
  "/icons/icon-192.png", "/icons/icon-512.png", "/icons/apple-touch-icon.png", "/icons/favicon-32.png"];

self.addEventListener("install", function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) { return c.addAll(SHELL); }).then(function () { return self.skipWaiting(); }));
});

self.addEventListener("activate", function (e) {
  e.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k !== CACHE; }).map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});

var OFFLINE = '<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' +
  '<title>문서 도우미</title><style>body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;' +
  'font:16px/1.6 system-ui,sans-serif;background:#151A26;color:#E6EBF5;text-align:center;padding:24px}button{margin-top:16px;' +
  'font:inherit;padding:10px 20px;border-radius:10px;border:0;background:#E6EBF5;color:#151A26;font-weight:700}</style></head>' +
  '<body><div><h1 style="font-size:20px">서버에 연결할 수 없어요</h1><p>인터넷 연결을 확인하거나, 관리자에게 서버가 켜져 있는지 물어봐 주세요.</p>' +
  '<button onclick="location.reload()">다시 시도</button></div></body></html>';

self.addEventListener("fetch", function (e) {
  var req = e.request, url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin || url.pathname.indexOf("/api/") === 0) return;  // 서버에 그대로
  e.respondWith(fetch(req).then(function (res) {
    if (res.ok && SHELL.indexOf(url.pathname) >= 0) {
      var copy = res.clone();
      caches.open(CACHE).then(function (c) { c.put(req, copy); });
    }
    return res;
  }).catch(function () {
    return caches.match(req).then(function (hit) {
      if (hit) return hit;
      if (req.mode === "navigate") return new Response(OFFLINE, { headers: { "Content-Type": "text/html; charset=utf-8" } });
      return Response.error();
    });
  }));
});
