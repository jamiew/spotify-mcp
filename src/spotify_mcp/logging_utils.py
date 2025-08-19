"""
Logging utilities for Spotify MCP server performance monitoring and debugging.
"""

import functools
import logging
import time
from typing import Any, Callable, TypeVar

# Configure logging
logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def log_tool_execution(func: F) -> F:
    """Decorator to log tool execution with timing and parameters."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        tool_name = func.__name__
        start_time = time.time()
        
        # Log tool invocation with sanitized parameters
        sanitized_kwargs = {k: v for k, v in kwargs.items() if k != 'password'}
        logger.info(
            f"🔧 Tool invoked: {tool_name}",
            extra={
                "tool_name": tool_name,
                "parameters": sanitized_kwargs,
                "timestamp": start_time,
            }
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
                }
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
                }
            )
            
            raise
    
    return wrapper  # type: ignore


def log_api_call(api_name: str, operation: str) -> Callable[[F], F]:
    """Decorator to log Spotify API calls with timing."""
    
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            start_time = time.time()
            
            logger.info(
                f"🌐 API call: {api_name}.{operation}",
                extra={
                    "api_name": api_name,
                    "operation": operation,
                    "timestamp": start_time,
                }
            )
            
            try:
                result = func(*args, **kwargs)
                execution_time = (time.time() - start_time) * 1000
                
                logger.info(
                    f"✅ API success: {api_name}.{operation} ({execution_time:.1f}ms)",
                    extra={
                        "api_name": api_name,
                        "operation": operation,
                        "execution_time_ms": execution_time,
                        "success": True,
                    }
                )
                
                return result
                
            except Exception as e:
                execution_time = (time.time() - start_time) * 1000
                
                logger.warning(
                    f"⚠️ API failed: {api_name}.{operation} ({execution_time:.1f}ms) - {str(e)}",
                    extra={
                        "api_name": api_name,
                        "operation": operation,
                        "execution_time_ms": execution_time,
                        "success": False,
                        "error": str(e),
                        "error_type": type(e).__name__,
                    }
                )
                
                raise
        
        return wrapper  # type: ignore
    
    return decorator


def log_pagination_info(operation: str, total: int, limit: int | None, offset: int) -> None:
    """Log pagination information for debugging large dataset operations."""
    logger.info(
        f"📄 Pagination: {operation} - total:{total}, limit:{limit}, offset:{offset}",
        extra={
            "operation": operation,
            "pagination": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": limit is not None and (offset + limit) < total if limit else False,
            }
        }
    )


def log_batch_operation(operation: str, batch_size: int, total_items: int) -> None:
    """Log batch operation information."""
    logger.info(
        f"📦 Batch operation: {operation} - processing {batch_size} items (total: {total_items})",
        extra={
            "operation": operation,
            "batch_size": batch_size,
            "total_items": total_items,
        }
    )