"""
Logging utilities for Spotify MCP server performance monitoring and debugging.
"""

import functools
import logging
import time
from collections.abc import Callable
from typing import Any

# Configure logging
logger = logging.getLogger(__name__)


def log_tool_execution[F: Callable[..., Any]](func: F) -> F:
    """Decorator to log tool execution with timing and parameters."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        tool_name = func.__name__
        start_time = time.time()

        # Log tool invocation with sanitized parameters
        sanitized_kwargs = {k: v for k, v in kwargs.items() if k != "password"}
        logger.info(
            f"🔧 Tool invoked: {tool_name}",
            extra={
                "tool_name": tool_name,
                "parameters": sanitized_kwargs,
                "timestamp": start_time,
            },
        )

        try:
            result = func(*args, **kwargs)
            execution_time = (time.time() - start_time) * 1000  # Convert to ms

            # Log successful completion with timing
            logger.info(
                f"✅ Tool completed: {tool_name} ({execution_time:.1f}ms)",
                extra={
                    "tool_name": tool_name,
                    "execution_time_ms": execution_time,
                    "success": True,
                },
            )

            return result

        except Exception as e:
            execution_time = (time.time() - start_time) * 1000

            # Log error with timing
            logger.error(
                f"❌ Tool failed: {tool_name} ({execution_time:.1f}ms) - {str(e)}",
                extra={
                    "tool_name": tool_name,
                    "execution_time_ms": execution_time,
                    "success": False,
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
            )

            raise

    return wrapper  # type: ignore


def log_pagination_info(
    operation: str, total: int, limit: int | None, offset: int
) -> None:
    """Log pagination information for debugging large dataset operations."""
    logger.info(
        f"📄 Pagination: {operation} - total:{total}, limit:{limit}, offset:{offset}",
        extra={
            "operation": operation,
            "pagination": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": limit is not None and (offset + limit) < total
                if limit
                else False,
            },
        },
    )
