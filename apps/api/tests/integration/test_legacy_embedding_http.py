"""Real HTTP reproduction of llama.cpp BGE-M3 pooling=none on an unchanged server."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from sentrix.infrastructure.models.legacy import BgeSidecarEmbedder


@pytest.mark.parametrize("invalid", [None, "zero", "dimension", "nonfinite", "batch", "pooled"])
def test_native_bge_cls_pooling_and_failure_semantics(invalid):
    seen = []
    cls = [3.0, 4.0] + [0.0] * 1022
    if invalid == "zero":
        cls = [0.0] * 1024
    elif invalid == "dimension":
        cls = cls[:10]
    elif invalid == "nonfinite":
        cls[0] = float("inf")
    tokens = [cls, [100.0] * 1024]
    native = [{"index": 0, "embedding": cls if invalid == "pooled" else tokens}]
    if invalid == "batch":
        native *= 2

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((self.path, body))
            status, value = 200, native
            if self.path == "/embed":
                status, value = 404, {}
            elif self.path == "/v1/embeddings":
                status, value = (
                    400,
                    {
                        "error": {
                            "message": (
                                "Pooling type 'none' is not OAI compatible. "
                                "Please use a different pooling type"
                            )
                        }
                    },
                )
            payload = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        embedder = BgeSidecarEmbedder(f"http://127.0.0.1:{server.server_port}")
        result = embedder.embed_query("本地协议验证")
        if invalid is None:
            assert result == [0.6, 0.8] + [0.0] * 1022
            assert embedder.embed_query("第二条") == result
            assert [path for path, _ in seen] == ["/embed", "/v1/embeddings", "/embedding", "/embedding"]
            assert seen[2][1] == {"content": "本地协议验证"}
        else:
            assert result == []
            assert embedder.load_error
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
