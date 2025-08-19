import signal
import sys

from .fastmcp_server import mcp


def main() -> None:
    """Main entry point for the package."""
    # Handle SIGPIPE gracefully (when client disconnects)
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)

    try:
        mcp.run()
    except BrokenPipeError:
        # Handle broken pipe gracefully when client disconnects
        sys.exit(0)
    except KeyboardInterrupt:
        # Handle Ctrl+C gracefully
        sys.exit(0)


# Optionally expose other important items at package level
__all__ = ["main", "mcp"]
