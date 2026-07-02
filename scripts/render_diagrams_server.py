"""Serve the repo + accept POST /save to write rendered SVGs into docs/images/.
Used with render-excalidraw.html to regenerate the hand-drawn diagrams after
editing the .excalidraw sources:
  1. ./.venv/bin/python scripts/render_diagrams_server.py   (or python3)
  2. open http://127.0.0.1:8123/render-excalidraw.html
  3. wait for DONE — docs/images/*-hand.svg are rewritten
"""
import http.server
import pathlib
import urllib.parse

REPO = pathlib.Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "images"


class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(REPO), **kw)

    def do_POST(self):
        q = urllib.parse.urlparse(self.path)
        if q.path != "/save":
            self.send_response(404); self.end_headers(); return
        name = pathlib.Path(urllib.parse.parse_qs(q.query).get("name", ["out.svg"])[0]).name
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        (OUT / name).write_bytes(body)
        self.send_response(200); self.end_headers(); self.wfile.write(b"saved")
        print(f"saved {name} ({len(body)} bytes)")


if __name__ == "__main__":
    print("http://127.0.0.1:8123/render-excalidraw.html")
    http.server.HTTPServer(("127.0.0.1", 8123), H).serve_forever()
