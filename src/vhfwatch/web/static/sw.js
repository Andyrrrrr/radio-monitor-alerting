// Marine 1 service worker: shows a home-screen alert when the station sends
// one, and opens its record when tapped. Served at /sw.js so it covers the
// whole site. Kept tiny on purpose: no caching, no offline copy of the page
// (the page must always show live data, never a stale copy).
//
// iOS requires every push to show a notification, so there is no silent path.

self.addEventListener("install", function () { self.skipWaiting(); });
self.addEventListener("activate", function (event) { event.waitUntil(self.clients.claim()); });

self.addEventListener("push", function (event) {
  var data = {};
  try { data = event.data ? event.data.json() : {}; }
  catch (e) { data = { body: event.data ? event.data.text() : "" }; }
  event.waitUntil(self.registration.showNotification(data.title || "Marine 1", {
    body: data.body || "",
    tag: data.tag || undefined,       // an update replaces the earlier alert
    renotify: true,                   // ...and still rings
    data: { url: data.url || "/marine1" },
    icon: "/static/icon-192.png",
    badge: "/static/icon-192.png",
  }));
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  var url = (event.notification.data && event.notification.data.url) || "/marine1";
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (wins) {
    for (var i = 0; i < wins.length; i++) {
      if ("navigate" in wins[i]) { wins[i].navigate(url); return wins[i].focus(); }
    }
    return self.clients.openWindow(url);
  }));
});
