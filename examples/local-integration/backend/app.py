"""Local acceptance fixture: an HTTP API backed by a real SQLite database."""

import argparse
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, required=True)
parser.add_argument("--database", required=True)
args = parser.parse_args()
with sqlite3.connect(args.database) as database:
    database.execute(
        "CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, text TEXT NOT NULL)"
    )


class Handler(BaseHTTPRequestHandler):
    def respond(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        with sqlite3.connect(args.database) as database:
            if path == "/health":
                database.execute("SELECT count(*) FROM notes").fetchone()
                self.respond(200, {"ready": True})
            elif path == "/notes":
                rows = database.execute("SELECT id, text FROM notes ORDER BY id").fetchall()
                self.respond(200, [{"id": row[0], "text": row[1]} for row in rows])
            else:
                self.respond(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/notes":
            self.respond(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 8192:
            self.respond(400, {"error": "invalid body length"})
            return
        try:
            text = json.loads(self.rfile.read(length)).get("text", "").strip()
        except (ValueError, AttributeError):
            self.respond(400, {"error": "invalid JSON"})
            return
        if not text:
            self.respond(400, {"error": "text is required"})
            return
        with sqlite3.connect(args.database) as database:
            row = database.execute("INSERT INTO notes(text) VALUES (?)", (text,))
            self.respond(201, {"id": row.lastrowid, "text": text})


ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
