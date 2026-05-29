"""
Logging utilities for Spotify MCP server performance monitoring and debugging.
"""

import asyncio
import functools
import logging
import time
from collections.abc import Callable
from typing import Any

# Configure logging
logger = logging.getLogger(__name__)


def _log_invocation(tool_name: str, kwargs: dict[str, Any], start_time: float) -> None:
    sanitized_kwargs = {k: v for k, v in kwargs.items() if k != "password"}
    logger.info(
        f"🔧 Tool invoked: {tool_name}",
        extra={
            "tool_name": tool_name,
            "parameters": sanitized_kwargs,
            "timestamp": start_time,
        },
    )


def _log_success(tool_name: str, start_time: float) -> None:
    execution_time = (time.time() - start_time) * 1000  # ms
    logger.info(
        f"✅ Tool completed: {tool_name} ({execution_time:.1f}ms)",
        extra={
            "tool_name": tool_name,
            "execution_time_ms": execution_time,
            "success": True,
        },
    )


def _log_failure(tool_name: str, start_time: float, e: Exception) -> None:
    execution_time = (time.time() - start_time) * 1000
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


def log_tool_execution[F: Callable[..., Any]](func: F) -> F:
    """Decorator to log tool execution with timing and parameters.

    Supports both sync and async tools; async tools are awaited inside the
    wrapper so timing brackets the real work rather than coroutine creation.
    """

    if asyncio.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            tool_name = func.__name__
            start_time = time.time()
            _log_invocation(tool_name, kwargs, start_time)
            try:
                result = await func(*args, **kwargs)
                _log_success(tool_name, start_time)
                return result
            except Exception as e:
                _log_failure(tool_name, start_time, e)
                raise

        return async_wrapper  # type: ignore

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        tool_name = func.__name__
        start_time = time.time()
        _log_invocation(tool_name, kwargs, start_time)
        try:
            result = func(*args, **kwargs)
            _log_success(tool_name, start_time)
            return result
        except Exception as e:
            _log_failure(tool_name, start_time, e)
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
