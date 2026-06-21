import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)


def spawn_background(
    coro: Awaitable[Any],
    *,
    label: str,
    on_error: Callable[[Exception], Awaitable[None]] | None = None,
) -> asyncio.Task:
    """يشغّل مهمة في الخلفية دون حجب معالجات التيليجرام."""

    async def _wrapper():
        try:
            await coro
        except asyncio.CancelledError:
            logger.info("Background job cancelled: %s", label)
            raise
        except Exception as e:
            logger.exception("Background job failed (%s): %s", label, e)
            if on_error:
                await on_error(e)

    task = asyncio.create_task(_wrapper(), name=label)
    return task


async def progress_ticker(
    bot,
    chat_id: int,
    message_id: int,
    steps: list[str],
    *,
    interval: float = 15.0,
    stop_event: asyncio.Event,
):
    i = 0
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
            break
        except asyncio.TimeoutError:
            if stop_event.is_set():
                break
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=steps[i % len(steps)],
                )
                i += 1
            except Exception:
                pass
