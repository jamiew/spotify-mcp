"""Tool decorator that logs timing and turns failures into model-visible errors."""

import functools
import inspect
import logging
import time
from collections.abc import Callable
from typing import Any

from spotipy import SpotifyException

from spotify_mcp.errors import ModelVisibleError, convert_spotify_error

logger = logging.getLogger(__name__)


def _model_visible(e: SpotifyException | ValueError) -> ModelVisibleError:
    if isinstance(e, SpotifyException):
        return convert_spotify_error(e)
    return ModelVisibleError(str(e))


def _log_success(tool_name: str, start: float) -> None:
    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info("Tool %s completed in %.1fms", tool_name, elapsed_ms)


def log_tool_execution[F: Callable[..., Any]](func: F) -> F:
    """Decorator for every tool: log timing, and surface failures to the model.

    Tools report bad arguments as ValueError and let spotipy's SpotifyException
    propagate. Both are re-raised as `ModelVisibleError` so the model reads the
    reason; MCPServer hides the text of anything else, and logs failures itself.

    Async tools are awaited inside the wrapper so timing brackets the real work.
    """
    tool_name = func.__name__

    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            logger.debug("Tool %s called with %s", tool_name, kwargs)
            start = time.perf_counter()
            try:
                result = await func(*args, **kwargs)
            except ModelVisibleError:
                raise
            except (SpotifyException, ValueError) as e:
                raise _model_visible(e) from e
            _log_success(tool_name, start)
            return result

        return async_wrapper  # type: ignore[return-value]

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        logger.debug("Tool %s called with %s", tool_name, kwargs)
        start = time.perf_counter()
        try:
            result = func(*args, **kwargs)
        except ModelVisibleError:
            raise
        except (SpotifyException, ValueError) as e:
            raise _model_visible(e) from e
        _log_success(tool_name, start)
        return result

    return wrapper  # type: ignore[return-value]
