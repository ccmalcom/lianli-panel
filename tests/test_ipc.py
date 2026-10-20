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
        Client(path).call("GetConfig")
    t.join(timeout=5)


def test_missing_socket_raises_daemon_down(tmp_path):
    with pytest.raises(DaemonDown):
        Client(str(tmp_path / "absent.sock")).call("Ping")


def _serve_many(path, replies, captured):
    """Answer one request per connection, choosing the reply by method name."""
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(4)
    srv.settimeout(5)

    def run():
        try:
            while True:
                conn, _ = srv.accept()
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                req = json.loads(buf.decode())
                captured.append(req)
                conn.sendall(json.dumps(replies[req["method"]]).encode())
                conn.close()
        except OSError:
            pass
        finally:
            srv.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


GUARDED_DAEMON = {"status": "ok", "data": {
    "version": "1.2.2", "protocol_version": 1, "instance_id": "482-abc",
    "capabilities": ["guarded_writes"]}}


def test_writes_are_wrapped_in_the_daemons_guard(tmp_path):
    path = str(tmp_path / "s.sock")
    captured = []
    _serve_many(path, {"GetDaemonInfo": GUARDED_DAEMON,
                       "Guarded": {"status": "ok", "data": None}}, captured)
    Client(path).call("SetRgbEffect", {"device_id": "d", "zone": 0})
    assert captured[-1] == {"method": "Guarded", "params": {
        "guard": {"client_version": "1.2.2", "protocol_version": 1,
                  "instance_id": "482-abc"},
        "request": {"method": "SetRgbEffect",
                    "params": {"device_id": "d", "zone": 0}}}}


def test_reads_are_sent_unwrapped(tmp_path):
    path = str(tmp_path / "s.sock")
    captured = []
    _serve_many(path, {"ListDevices": {"status": "ok", "data": []}}, captured)
    Client(path).call("ListDevices")
    assert captured == [{"method": "ListDevices"}]


def test_writes_go_unwrapped_to_a_daemon_without_guards(tmp_path):
    path = str(tmp_path / "s.sock")
    captured = []
    old = {"status": "ok", "data": {"version": "0.8.8", "protocol_version": 1,
                                    "instance_id": "a", "capabilities": []}}
    _serve_many(path, {"GetDaemonInfo": old,
                       "SetRgbEffect": {"status": "ok", "data": None}}, captured)
    Client(path).call("SetRgbEffect", {"device_id": "d"})
    assert captured[-1] == {"method": "SetRgbEffect", "params": {"device_id": "d"}}


def test_unknown_protocol_refuses_to_write(tmp_path):
    path = str(tmp_path / "s.sock")
    newer = {"status": "ok", "data": dict(GUARDED_DAEMON["data"], protocol_version=2)}
    _serve_many(path, {"GetDaemonInfo": newer}, [])
    with pytest.raises(DaemonError, match="protocol 2"):
        Client(path).call("SetRgbEffect", {"device_id": "d"})


def test_writes_go_unwrapped_when_daemon_info_is_unknown(tmp_path):
    path = str(tmp_path / "s.sock")
    captured = []
    _serve_many(path, {"GetDaemonInfo": {"status": "error", "message": "unknown variant"},
                       "SetConfig": {"status": "ok", "data": None}}, captured)
    Client(path).call("SetConfig", {"config": {}})
    assert captured[-1] == {"method": "SetConfig", "params": {"config": {}}}
