"""Host-port availability helpers used by scripts/check_ports.py."""

import socket


def is_port_free(port: int) -> bool:
    """Return True if no process has bound the given TCP port on localhost.

    Uses a bind-probe so it catches any bound socket, not just active listeners.
    The probe socket is never put into the listen backlog, so it does not
    interfere with other processes.

    Args:
        port: TCP port number to probe on 127.0.0.1.

    Returns:
        True when the port is available for binding; False otherwise.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def find_free_port(preferred: int) -> int:
    """Return preferred if free, otherwise the next OS-assigned free port.

    Args:
        preferred: First port number to try.

    Returns:
        preferred when available; a fresh OS-assigned ephemeral port otherwise.
    """
    if is_port_free(preferred):
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port
