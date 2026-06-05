"""Unit tests for host-port availability helpers."""

import socket
from unittest.mock import MagicMock, patch

from api.port_utils import find_free_port, is_port_free


def _free_sock(port: int = 54321) -> MagicMock:
    """Return a mock socket that binds without error."""
    sock = MagicMock()
    sock.__enter__ = MagicMock(return_value=sock)
    sock.__exit__ = MagicMock(return_value=False)
    sock.getsockname.return_value = ("127.0.0.1", port)
    return sock


def _busy_sock() -> MagicMock:
    """Return a mock socket that raises OSError on bind."""
    sock = MagicMock()
    sock.__enter__ = MagicMock(return_value=sock)
    sock.__exit__ = MagicMock(return_value=False)
    sock.bind.side_effect = OSError("address already in use")
    return sock


def test_is_port_free_when_port_is_occupied() -> None:
    with patch("socket.socket", return_value=_busy_sock()):
        assert not is_port_free(1234)


def test_is_port_free_when_port_is_released() -> None:
    with patch("socket.socket", return_value=_free_sock()):
        assert is_port_free(1234)


def test_find_free_port_returns_preferred_when_available() -> None:
    with patch("socket.socket", return_value=_free_sock()):
        assert find_free_port(1234) == 1234


def test_find_free_port_returns_alternative_when_preferred_is_occupied() -> None:
    # is_port_free creates one socket (busy); find_free_port creates a second (free).
    with patch("socket.socket", side_effect=[_busy_sock(), _free_sock(54321)]):
        alt = find_free_port(1234)
        assert alt != 1234
        assert alt > 0
