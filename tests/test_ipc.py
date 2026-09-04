import json
import socket
import threading

import pytest

from lianli_panel.ipc import Client, DaemonError, DaemonDown


def _serve(path, payload, captured):
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(1)

    def run():
        conn, _ = srv.accept()
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
        captured.append(json.loads(buf.decode()))
        conn.sendall(json.dumps(payload).encode())
        conn.close()
        srv.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def test_call_returns_data_payload(tmp_path):
    path = str(tmp_path / "s.sock")
    captured = []
    t = _serve(path, {"status": "ok", "data": [1, 2, 3]}, captured)
    assert Client(path).call("ListDevices") == [1, 2, 3]
    t.join(timeout=5)
    assert captured[0] == {"method": "ListDevices"}


def test_params_are_sent_when_given(tmp_path):
    path = str(tmp_path / "s.sock")
    captured = []
    t = _serve(path, {"status": "ok", "data": None}, captured)
    Client(path).call("Ping", {"a": 1})
    t.join(timeout=5)
    assert captured[0] == {"method": "Ping", "params": {"a": 1}}


def test_error_status_raises_with_message(tmp_path):
    path = str(tmp_path / "s.sock")
    t = _serve(path, {"status": "error", "message": "nope"}, [])
    with pytest.raises(DaemonError, match="nope"):
        Client(path).call("Bad")
    t.join(timeout=5)


def test_missing_socket_raises_daemon_down(tmp_path):
    with pytest.raises(DaemonDown):
        Client(str(tmp_path / "absent.sock")).call("Ping")
