"""
eNSP Topo Viewer - Local Server
启动后自动打开浏览器，拖入 .topo 文件后自动保存 topology.json
Claude 可直接读取 A:\ensp-AI\topology.json 获取拓扑信息
"""
import http.server
import json
import os
import sys
import webbrowser
import urllib.parse

PORT = 18080
DIR = os.path.dirname(os.path.abspath(__file__))
TOPO_JSON = os.path.join(DIR, "topology.json")


class TopoHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIR, **kwargs)

    def guess_type(self, path):
        t = super().guess_type(path)
        if t == "text/html":
            return "text/html; charset=utf-8"
        return t

    def do_POST(self):
        if self.path == "/api/topology":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
                with open(TOPO_JSON, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                self.send_json(200, {"ok": True, "path": TOPO_JSON})
                print(f"[SYNC] topology.json saved ({len(data.get('devices',[]))} devices, {len(data.get('links',[]))} links)")
            except Exception as e:
                self.send_json(500, {"ok": False, "error": str(e)})
        else:
            self.send_json(404, {"error": "not found"})

    def do_GET(self):
        if self.path == "/api/topology":
            if os.path.exists(TOPO_JSON):
                with open(TOPO_JSON, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.send_json(200, data)
            else:
                self.send_json(404, {"error": "no topology loaded yet"})
        else:
            super().do_GET()

    def send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        # quieter logging
        if "/api/" in (args[0] if args else ""):
            return
        super().log_message(fmt, *args)


def main():
    os.chdir(DIR)
    server = http.server.HTTPServer(("127.0.0.1", PORT), TopoHandler)
    url = f"http://127.0.0.1:{PORT}/topo-viewer.html"
    print(f"eNSP Topo Viewer server running at {url}")
    print(f"topology.json will be saved to: {TOPO_JSON}")
    print("Press Ctrl+C to stop.\n")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
        server.server_close()


if __name__ == "__main__":
    main()
