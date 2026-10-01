import importlib.util
import socket
import urllib.error
from pathlib import Path


def _load_worker_module():
    worker_path = Path(__file__).resolve().parents[1] / "scripts" / "agent_worker.py"
    spec = importlib.util.spec_from_file_location("agent_worker", worker_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_http_json_returns_599_on_urlerror(monkeypatch):
    worker = _load_worker_module()

    def fake_urlopen(*args, **kwargs):
        raise urllib.error.URLError("temporary DNS failure")

    monkeypatch.setattr(worker.urllib.request, "urlopen", fake_urlopen)

    status, body, raw = worker._http_json("GET", "https://example.invalid")

    assert status == 599
    assert body.get("reason") == "transport_error"
    assert "temporary DNS failure" in body.get("detail", "")
    assert "temporary DNS failure" in raw


def test_http_json_returns_599_on_socket_timeout(monkeypatch):
    worker = _load_worker_module()

    def fake_urlopen(*args, **kwargs):
        raise socket.timeout("timed out")

    monkeypatch.setattr(worker.urllib.request, "urlopen", fake_urlopen)

    status, body, raw = worker._http_json("GET", "https://example.invalid")

    assert status == 599
    assert body.get("reason") == "transport_error"
    assert "timed out" in body.get("detail", "")
    assert "timed out" in raw
