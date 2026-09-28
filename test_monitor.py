import socket

from monitor import (
    ServerMonitor,
    bridge_line,
    format_duration,
    is_server_live,
    players_value,
)


def test_bridge_line():
    chat = "[23:41:53] [Server thread/INFO]: <Notch> hello there"
    assert bridge_line(chat) == "**Notch**: hello there"
    joined = "[23:41:53] [Server thread/INFO]: Steve joined the game"
    assert bridge_line(joined) == "➕ **Steve** joined"
    left = "[23:41:53] [Server thread/INFO]: Steve left the game"
    assert bridge_line(left) == "➖ **Steve** left"
    # non-chat lines are ignored
    assert bridge_line("[23:41:53] [Server thread/INFO]: Saving the game") is None
    assert bridge_line("[23:41:53] [Worker/ERROR]: boom") is None


def test_transitions():
    m = ServerMonitor()
    assert m.update(False, 0) is None      # stays down, no event
    assert m.update(True, 100) == "started"
    assert m.update(True, 130) is None     # stays up, no event
    assert m.uptime(130) == 30
    assert m.update(False, 200) == "stopped"
    assert m.uptime(200) is None


def test_true_start_time():
    # bot boots at now=1000 but server actually started at 700
    m = ServerMonitor()
    m.prime(True, now=1000, start_time=700)
    assert m.uptime(1000) == 300  # real uptime, not 0
    # priming never emits, and an up-transition honors the OS start time
    m2 = ServerMonitor()
    assert m2.update(True, now=1000, start_time=700) == "started"
    assert m2.uptime(1000) == 300


def test_is_server_live_rejects_bare_socket():
    # A socket that accepts TCP but speaks no Minecraft: exactly what a playit.gg
    # tunnel edge (or a lingering socket) looks like after the server stops.
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    host, port = srv.getsockname()
    socket.create_connection((host, port), timeout=1.0).close()  # a bare connect succeeds...
    assert is_server_live(host, port, timeout=1.0) is False   # ping-based: correctly down
    srv.close()
    assert is_server_live(host, port, timeout=1.0) is False   # nothing there: down


def test_players_value():
    assert players_value([], 0) == "None"
    assert players_value(["Bob", "Al"], 2) == "Al, Bob"
    # status-sample fallback that only saw some of the online players
    assert "showing 1 of 3" in players_value(["Al"], 3, partial=True)


def test_format_duration():
    assert format_duration(0) == "0s"
    assert format_duration(65) == "1m 5s"
    assert format_duration(3600) == "1h"
    assert format_duration(90061) == "1d 1h 1m 1s"


if __name__ == "__main__":
    test_transitions()
    test_true_start_time()
    test_is_server_live_rejects_bare_socket()
    test_bridge_line()
    test_players_value()
    test_format_duration()
    print("ok")
