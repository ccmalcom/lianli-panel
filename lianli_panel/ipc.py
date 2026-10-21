"""Transport for the lianli daemon's newline-delimited JSON IPC socket.

One call per connection: send a single JSON line, half-close, read until EOF.
"""
from __future__ import annotations

import errno
import json
import socket
from typing import Any

DEFAULT_SOCK = "/run/lianli/lianli-daemon.sock"

# Daemon 1.2+ refuses any write that is not wrapped in a Guarded envelope naming
# its version, IPC protocol and instance; reads still pass bare. Mirrors
# IpcRequest::is_read_only in lianli-shared/src/ipc.rs. A method missing here
# is treated as a write, which is the safe direction: a guarded read still works.
READ_ONLY = frozenset({
    "ListDevices", "GetConfig", "CheckMediaAccess", "GetTelemetry",
    "GetRgbCapabilities", "GetZoneColors", "ListRgbPresets", "GetWirelessOperation",
    "ListSensors", "SuggestSensorSources", "ListPwmHeaders", "GetLcdTemplates",
    "RenderTemplatePreview", "RenderSensorPreview", "Ping", "GetDaemonInfo",
    "GetInstallationHealth", "GetChannel", "GetMergeLightingConfig",
    "ListDeviceProfiles", "GetPixelCleanStatus", "GetStartupImageStatus",
    "GetPixelCleanPreparation", "ListStateBackups", "GetCatalogInstallStatus",
    "GetCatalogStorage", "GetManagedMediaStorage", "StartManagedMediaReview",
    "GetManagedMediaReview", "StartCatalogReview", "GetCatalogReview",
    "PreviewStateBackup",
})
PROTOCOL_VERSION = 1


class DaemonError(Exception):
    """The daemon answered, and the answer was an error."""


class DaemonDown(DaemonError):
    """The socket is absent — the daemon is not running."""


class DaemonRefused(DaemonError):
    """The socket exists but will not accept a connection from this process."""


class Client:
    def __init__(self, sock_path: str = DEFAULT_SOCK, timeout: float = 30.0) -> None:
        self.sock_path = sock_path
        self.timeout = timeout

    def call(self, method: str, params: dict | None = None) -> Any:
        req: dict[str, Any] = {"method": method}
        if params is not None:
            req["params"] = params
        if method not in READ_ONLY:
            req = self._guarded(req)
        return self._send(req)

    def _guarded(self, req: dict[str, Any]) -> dict[str, Any]:
        # Fetched per write: instance_id changes whenever the daemon restarts.
        try:
            info = self._send({"method": "GetDaemonInfo"})
        except (DaemonDown, DaemonRefused):
            raise
        except DaemonError:
            return req  # pre-1.2 daemon: no GetDaemonInfo, no guard either
        if "guarded_writes" not in (info.get("capabilities") or []):
            return req
        if info.get("protocol_version") != PROTOCOL_VERSION:
            raise DaemonError(
                f"daemon {info.get('version')} speaks IPC protocol "
                f"{info.get('protocol_version')}; this client only knows {PROTOCOL_VERSION}")
        guard = {"client_version": info["version"],
                 "protocol_version": PROTOCOL_VERSION,
                 "instance_id": info["instance_id"]}
        return {"method": "Guarded", "params": {"guard": guard, "request": req}}

    def _send(self, req: dict[str, Any]) -> Any:
        method = req["method"]
        raw = self._roundtrip(json.dumps(req) + "\n")
        try:
            reply = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DaemonError(f"unparseable reply to {method}: {raw[:200]!r}") from exc
        if reply.get("status") != "ok":
            raise DaemonError(reply.get("message", f"{method} failed"))
        return reply.get("data")

    def _roundtrip(self, payload: str) -> str:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        try:
            s.connect(self.sock_path)
        except FileNotFoundError as exc:
            raise DaemonDown(f"no socket at {self.sock_path}") from exc
        except ConnectionRefusedError as exc:
            raise DaemonDown(f"socket at {self.sock_path} refused") from exc
        except PermissionError as exc:
            # Seen inside sandboxes: mode 0666 but connect() still returns EPERM.
            raise DaemonRefused(f"not permitted to connect to {self.sock_path}") from exc
        except OSError as exc:
            if exc.errno in (errno.ENOENT, errno.ECONNREFUSED):
                raise DaemonDown(str(exc)) from exc
            raise
        try:
            s.sendall(payload.encode())
            s.shutdown(socket.SHUT_WR)
            chunks = []
            while True:
                chunk = s.recv(1 << 16)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks).decode()
        finally:
            s.close()
