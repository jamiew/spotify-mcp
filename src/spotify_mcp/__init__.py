import signal
import sys

from .fastmcp_server import mcp


def main() -> None:
    """Run the server over stdio."""
    # Exit quietly when the client disconnects or the user presses Ctrl+C.
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    try:
        mcp.run()
    except (BrokenPipeError, KeyboardInterrupt):
        sys.exit(0)


__all__ = ["main", "mcp"]
