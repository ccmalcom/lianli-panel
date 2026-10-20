#!/usr/bin/env python3
"""Minimal client for the lianli daemon's newline-delimited JSON IPC socket.

Shipped to /usr/local/share/lianli-panel beside the poller and sensor scripts,
which import it as a flat module, so it cannot depend on the lianli_panel
package. The guard logic duplicates lianli_panel.ipc.Client on purpose.
"""
import json
import socket
import sys

SOCK = "/run/lianli/lianli-daemon.sock"

# Daemon 1.2+ refuses any write that is not wrapped in a Guarded envelope; reads
# still pass bare. Mirrors IpcRequest::is_read_only in lianli-shared/src/ipc.rs.
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


def _send(req, timeout):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(SOCK)
    s.sendall((json.dumps(req) + "\n").encode())
    s.shutdown(socket.SHUT_WR)
    buf = b""
    while True:
        chunk = s.recv(1 << 16)
        if not chunk:
            break
        buf += chunk
    s.close()
    return json.loads(buf.decode())


def _guarded(req, timeout):
    # Fetched per write: instance_id changes whenever the daemon restarts.
    info = _send({"method": "GetDaemonInfo"}, timeout)
    if info.get("status") != "ok":
        return req  # pre-1.2 daemon: no GetDaemonInfo, no guard either
    data = info.get("data") or {}
    if "guarded_writes" not in (data.get("capabilities") or []):
        return req
    if data.get("protocol_version") != PROTOCOL_VERSION:
        return None
    guard = {"client_version": data["version"],
             "protocol_version": PROTOCOL_VERSION,
             "instance_id": data["instance_id"]}
    return {"method": "Guarded", "params": {"guard": guard, "request": req}}


def call(method, params=None, timeout=30):
    req = {"method": method}
    if params:
        req["params"] = params
    if method not in READ_ONLY:
        req = _guarded(req, timeout)
        if req is None:
            return {"status": "error",
                    "message": f"daemon IPC protocol is not {PROTOCOL_VERSION}; refusing to write"}
    return _send(req, timeout)


if __name__ == "__main__":
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else None
    print(json.dumps(call(sys.argv[1], params))[:4000])
