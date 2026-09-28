"""Public live status page, served only while the Minecraft server is up.

Serves whatever snapshot the bot hands it: GET / (page) and GET /status.json.
The snapshot is already public-only data (see monitor.public_status); the page
renders it with textContent, so MOTD/player names can't inject HTML.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Server Status</title>
<style>
  :root { --bg:#f6f7f9; --card:#fff; --fg:#1d2026; --muted:#6b7280; --ok:#1f9d55; --bad:#d64545; --link:#2563eb; }
  @media (prefers-color-scheme: dark) { :root { --bg:#16181d; --card:#1f2228; --fg:#e6e8eb; --muted:#9aa1ab; --link:#7aa7ff; } }
  body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, sans-serif; }
  main { max-width:560px; margin:40px auto; padding:0 16px; }
  .card { background:var(--card); border-radius:12px; padding:20px 24px; box-shadow:0 1px 3px #0002; }
  h1 { margin:0 0 4px; font-size:22px; } h1 .dot { color:var(--ok); } .off h1 .dot { color:var(--bad); }
  #motd { color:var(--muted); font-style:italic; margin:0 0 12px; }
  dl { display:grid; grid-template-columns:auto 1fr; gap:6px 16px; margin:0; }
  dt { color:var(--muted); } dd { margin:0; overflow-wrap:anywhere; }
  a { color:var(--link); }
  footer { color:var(--muted); font-size:12px; margin-top:12px; }
</style></head>
<body><main><div class="card" id="card">
  <h1><span class="dot">&#9679;</span> <span id="title">Loading&hellip;</span></h1>
  <p id="motd"></p><dl id="rows"></dl>
  <footer id="updated"></footer>
</div></main>
<script>
async function load() {
  let s;
  try { s = await (await fetch('status.json', {cache: 'no-store'})).json(); }
  catch { s = {online: false}; }
  const card = document.getElementById('card'), rows = document.getElementById('rows');
  card.className = 'card' + (s.online ? '' : ' off');
  document.getElementById('title').textContent = (s.name || 'Server') + (s.online ? ' Online' : ' Offline');
  document.getElementById('motd').textContent = s.motd || '';
  const p = s.players;
  const items = [
    ['Uptime', s.uptime], ['Version', s.version],
    ['TPS', s.tps != null ? s.tps + (s.mspt != null ? ' (' + s.mspt + ' ms/tick)' : '') : null],
    ['Players', p ? p.online + '/' + p.max + (p.names.length ? ' \\u2014 ' + p.names.join(', ') : '') : null],
    ['Server IP', s.server_ip],
  ];
  rows.replaceChildren();
  for (const [k, v] of items) {
    if (v == null || v === '') continue;
    const dt = document.createElement('dt'), dd = document.createElement('dd');
    dt.textContent = k; dd.textContent = v; rows.append(dt, dd);
  }
  if (s.modpack_url && /^https?:\\/\\//.test(s.modpack_url)) {
    const dt = document.createElement('dt'), dd = document.createElement('dd'), a = document.createElement('a');
    dt.textContent = 'Modpack'; a.href = s.modpack_url; a.textContent = 'Download'; a.rel = 'noopener';
    dd.append(a); rows.append(dt, dd);
  }
  document.getElementById('updated').textContent =
    s.updated ? 'Updated ' + new Date(s.updated * 1000).toLocaleTimeString() : '';
}
load(); setInterval(load, 15000);
</script></body></html>
"""

CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
       "connect-src 'self'; img-src 'self'")


class StatusPage:
    def __init__(self, port: int, get_snapshot):
        self.port, self.get_snapshot, self.httpd = port, get_snapshot, None

    def start(self):
        if self.httpd or not self.port:
            return
        get = self.get_snapshot

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path = self.path.split("?")[0]
                if path == "/":
                    body, ctype = PAGE.encode(), "text/html; charset=utf-8"
                elif path == "/status.json":
                    body, ctype = json.dumps(get()).encode(), "application/json"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Security-Policy", CSP)
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass  # quiet console

        try:
            self.httpd = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        except OSError as e:
            print(f"status page: can't bind port {self.port}: {e}")
            return
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
