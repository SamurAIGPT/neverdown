"""Background async task that scans for stale jobs and triggers failover."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from .dispatcher import Dispatcher

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def failover_loop(dispatcher: Dispatcher, *, scan_interval_s: float) -> None:
    """Run forever: every scan_interval_s, find stale jobs and fail them over.

    A 'stale' job is one in status='submitted' whose deadline has passed without a
    callback. This typically means the provider is hung or the callback was lost.
    """
    while True:
        try:
            stale = await dispatcher.jobs.list_stale_submitted(now=_utcnow())
            for job in stale:
                logger.info(
                    "Failover: job %s on provider %s exceeded deadline", job.id, job.provider
                )
                try:
                    await dispatcher.handle_deadline_exceeded(job)
                except Exception:
                    logger.exception("handle_deadline_exceeded raised for job %s", job.id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("failover_loop iteration raised")

        await asyncio.sleep(scan_interval_s)
