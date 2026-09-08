"""Local acceptance fixture: browser UI and a separate backend HTTP dependency."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, required=True)
parser.add_argument("--backend", required=True)
args = parser.parse_args()
# A dependency that must already be ready before this service starts.
with urlopen(f"{args.backend}/health", timeout=2) as response:
    assert response.status == 200

PAGE = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>本地集成功能测试</title><style>
body{font:16px system-ui;max-width:650px;margin:60px auto;padding:24px;
background:#f6f7f9;color:#18212b}
input,button{font:inherit;padding:12px;border:1px solid #bbc4ce;border-radius:6px}
button{background:#236754;color:white;cursor:pointer}li{padding:10px}#status{min-height:24px}
</style><h1>本地集成功能测试</h1><p>浏览器 → 前端服务 → 后端 API → SQLite</p>
<form><label>测试内容 <input name="text" required></label> <button>保存记录</button></form>
<p id="status" role="status"></p><ul id="notes"></ul>
<script>
async function refresh(){const r=await fetch('/api/notes');if(!r.ok)throw Error('查询失败');
const notes=await r.json();document.querySelector('#notes').replaceChildren(...notes.map(n=>{
const li=document.createElement('li');li.textContent=n.text;return li}));}
document.querySelector('form').onsubmit=async e=>{e.preventDefault();
const status=document.querySelector('#status');
try{const r=await fetch('/api/notes',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({text:new FormData(e.target).get('text')})});
if(!r.ok)throw Error('保存失败');await refresh();
status.textContent='已保存并从数据库重新读取';e.target.reset();}
catch(error){status.textContent=error.message;}};
refresh().catch(error=>document.querySelector('#status').textContent=error.message);
</script></html>""".encode()


class Handler(BaseHTTPRequestHandler):
    def proxy(self, path):
        length = int(self.headers.get("Content-Length", "0"))
        if length > 8192:
            self.send_error(413)
            return
        data = self.rfile.read(length) if self.command == "POST" else None
        request = Request(
            f"{args.backend}{path}",
            data=data,
            method=self.command,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=2) as response:
                body = response.read()
                self.send_response(response.status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        except (HTTPError, URLError, TimeoutError):
            self.send_error(503, "Backend unavailable")

    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers()
            self.wfile.write(PAGE)
        elif self.path == "/health":
            self.proxy("/health")
        elif self.path == "/api/notes":
            self.proxy("/notes")
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/api/notes":
            self.proxy("/notes")
        else:
            self.send_error(404)


ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
