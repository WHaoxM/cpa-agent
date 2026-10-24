"""Reconcile cached and durable copies of one session transcript."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..utils.logger import get_logger

logger = get_logger("cpa-agent.session-history")


def merge_history_sources(
    cached_messages: List[Dict[str, Any]],
    mysql_messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Merge Redis and MySQL history only where their order can be proven.

    MySQL is an append-only projection written after Redis. Stable message IDs
    identify overlap; source order supplies chronology. If the sources disagree
    or have unanchored interleaving, retain Redis order rather than guess.
    """

    def unique_sequence(messages: List[Dict[str, Any]]):
        result: List[Dict[str, Any]] = []
        seen = set()
        for message in messages:
            if not isinstance(message, dict):
                return list(messages), None
            message_id = message.get("message_id")
            if not isinstance(message_id, str) or not message_id:
                return list(messages), None
            if message_id in seen:
                continue
            seen.add(message_id)
            result.append(message)
        return result, [message["message_id"] for message in result]

    cached, cached_ids = unique_sequence(cached_messages)
    durable, durable_ids = unique_sequence(mysql_messages)
    if not cached_messages:
        return durable
    if not mysql_messages:
        return cached
    if cached_ids is None or durable_ids is None:
        logger.warning("Session history reconciliation skipped: message identity unavailable")
        return cached

    cached_by_id = {message["message_id"]: message for message in cached}
    cached_set = set(cached_ids)
    durable_set = set(durable_ids)
    common_cached = [message_id for message_id in cached_ids if message_id in durable_set]
    common_durable = [message_id for message_id in durable_ids if message_id in cached_set]

    if not common_cached:
        def timestamp_key(message: Dict[str, Any]) -> Optional[float]:
            value = message.get("timestamp")
            try:
                if isinstance(value, datetime):
                    parsed = value
                elif isinstance(value, str) and value:
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                else:
                    return None
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.timestamp()
            except (OverflowError, OSError, TypeError, ValueError):
                return None

        cached_start = timestamp_key(cached[0])
        cached_end = timestamp_key(cached[-1])
        durable_start = timestamp_key(durable[0])
        durable_end = timestamp_key(durable[-1])
        if (None not in (cached_start, durable_start, durable_end)
                and durable_end < cached_start):
            return durable + cached
        if (None not in (cached_start, cached_end, durable_start)
                and cached_end < durable_start):
            return cached + durable
        logger.warning("Session history reconciliation skipped: sources have no ordered overlap")
        return cached

    if common_cached != common_durable:
        logger.warning("Session history reconciliation skipped: shared message order conflicts")
        return cached

    merged: List[Dict[str, Any]] = []
    cached_cursor = 0
    durable_cursor = 0
    for message_id in common_cached:
        cached_index = cached_ids.index(message_id, cached_cursor)
        durable_index = durable_ids.index(message_id, durable_cursor)
        cached_segment = cached[cached_cursor:cached_index]
        durable_segment = durable[durable_cursor:durable_index]
        if cached_segment and durable_segment:
            logger.warning("Session history reconciliation skipped: unanchored message interleaving")
            return cached
        merged.extend(cached_segment or durable_segment)
        # Redis carries the current session metadata and is written first.
        merged.append(cached_by_id[message_id])
        cached_cursor = cached_index + 1
        durable_cursor = durable_index + 1

    cached_tail = cached[cached_cursor:]
    durable_tail = durable[durable_cursor:]
    if cached_tail and durable_tail:
        logger.warning("Session history reconciliation skipped: unanchored message tails")
        return cached
    merged.extend(cached_tail or durable_tail)
    return merged
