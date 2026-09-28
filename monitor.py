import re
import socket
import struct
from dataclasses import dataclass

import psutil
from mcstatus import JavaServer


class RconError(Exception):
    pass


def rcon_command(host: str, port: int, password: str, command: str, timeout: float = 5.0) -> str:
    """Run one command over the Source RCON protocol and return the response."""
    def recv(sock):
        def read(n):
            buf = b""
            while len(buf) < n:
                chunk = sock.recv(n - len(buf))
                if not chunk:
                    raise RconError("connection closed")
                buf += chunk
            return buf
        (length,) = struct.unpack("<i", read(4))
        return struct.unpack("<ii", read(length)[:8])  # (req_id, ptype)

    def send(sock, ptype, body):
        pkt = struct.pack("<ii", 0, ptype) + body.encode("utf-8") + b"\x00\x00"
        sock.sendall(struct.pack("<i", len(pkt)) + pkt)

    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        send(sock, 3, password)          # auth
        while True:
            rid, ptype = recv(sock)
            if ptype == 2:                # auth response
                if rid == -1:
                    raise RconError("authentication failed")
                break
        send(sock, 2, command)           # exec
        recv(sock)
        return ""


def bridge_line(line: str):
    """Turn a server log line into a Discord message, or None if not relayable."""
    m = re.search(r"INFO\]: <([^>]{1,16})> (.+)$", line)
    if m:
        return f"**{m.group(1)}**: {m.group(2)}"
    m = re.search(r"INFO\]: (\w{1,16}) (joined|left) the game\b", line)
    if m:
        return f"{'➕' if m.group(2) == 'joined' else '➖'} **{m.group(1)}** {m.group(2)}"
    return None


def is_server_live(host: str, port: int, timeout: float = 3.0) -> bool:
    """True only if a real Minecraft server answers a status ping.

    Stronger than a bare TCP connect. A plain TCP accept also succeeds through
    a playit.gg tunnel edge or a lingering/half-open socket while the actual
    server is down, so a connect-based check reports a stopped server as still
    up and the "went down" alert never fires. A status ping needs the Minecraft
    handshake to complete, so it flips to False the moment the server really
    stops. Needs enable-status=true (the default).
    """
    try:
        JavaServer(host, port, timeout=timeout).status()
        return True
    except Exception:
        return False


def server_start_time(host: str, port: int) -> float | None:
    """Epoch start time of the local process listening on port, else None.

    Only works for a same-PC server. Returns None if the host isn't local or
    the listener can't be identified (then the caller falls back to first-seen
    time).
    """
    if host not in ("127.0.0.1", "localhost", "::1", ""):
        return None
    try:
        for c in psutil.net_connections(kind="inet"):
            if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == port and c.pid:
                return psutil.Process(c.pid).create_time()
    except (psutil.Error, OSError):
        return None
    return None


@dataclass
class ServerMonitor:
    """Tracks up/down state and reports transitions between polls."""

    up: bool = False
    started_at: float | None = None

    def prime(self, is_up: bool, now: float, start_time: float | None = None) -> None:
        """Adopt current state at startup without emitting an event."""
        self.up = is_up
        self.started_at = (start_time or now) if is_up else None

    def update(self, is_up: bool, now: float, start_time: float | None = None) -> str | None:
        """Feed the latest reachability; return 'started', 'stopped', or None."""
        if is_up and not self.up:
            self.up, self.started_at = True, start_time or now
            return "started"
        if not is_up and self.up:
            self.up, self.started_at = False, None
            return "stopped"
        return None

    def uptime(self, now: float) -> float | None:
        if self.up and self.started_at is not None:
            return now - self.started_at
        return None


def players_value(names, online: int, partial: bool = False) -> str:
    """Body text for the players embed field."""
    if online == 0:
        return "None"
    body = ", ".join(sorted(names)) if names else "(names unavailable)"
    if partial and names and len(names) < online:
        body += f"\n_showing {len(names)} of {online}_"
    return body


def format_duration(seconds: float) -> str:
    d, r = divmod(int(seconds), 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    return " ".join(f"{v}{u}" for v, u in ((d, "d"), (h, "h"), (m, "m"), (s, "s")) if v) or "0s"
