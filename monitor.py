import socket
from dataclasses import dataclass


def is_server_up(host: str, port: int, timeout: float = 3.0) -> bool:
    """True if a TCP connection to host:port succeeds (server accepting players)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def server_start_time(host: str, port: int) -> float | None:
    """Epoch start time of the local process listening on port, else None.

    Only works for a same-PC server; needs psutil. Returns None if psutil is
    absent, the host isn't local, or the listener can't be identified (then the
    caller falls back to first-seen time).
    """
    if host not in ("127.0.0.1", "localhost", "::1", ""):
        return None
    try:
        import psutil
    except ImportError:
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


def format_players(names, online: int, maximum: int, partial: bool = False) -> str:
    if online == 0:
        return f"No players online (0/{maximum})"
    body = ", ".join(sorted(names)) if names else "(names unavailable)"
    if partial and names and len(names) < online:
        body += f"  _(showing {len(names)} of {online})_"
    return f"**{online}/{maximum} online:** {body}"


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    d, r = divmod(seconds, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    parts = []
    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    if s or not parts:
        parts.append(f"{s}s")
    return " ".join(parts)
