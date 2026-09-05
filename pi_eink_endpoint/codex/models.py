"""Normalize App Server quota responses without inventing missing limits."""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import math
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Window:
    remaining_percent: float | None
    resets_at: datetime | None


@dataclass(frozen=True)
class Quota:
    five_hour: Window | None
    weekly: Window | None
    available_resets: int | None
    fetched_at: datetime
    stale: bool = False

    def mark_stale(self):
        return replace(self, stale=True)


def normalize_quota(response: dict, *, fetched_at: datetime | None = None,
                    timezone_name: str = "Asia/Tokyo") -> Quota:
    zone = ZoneInfo(timezone_name)
    fetched_at = fetched_at or datetime.now(timezone.utc)
    if fetched_at.tzinfo is None:
        raise ValueError("fetched_at must be timezone-aware")
    buckets = response.get("rateLimitsByLimitId")
    snapshot = (buckets.get("codex") if buckets is not None
                else response.get("rateLimits")) or {}
    # A legacy response can also explicitly identify a different model bucket.
    if snapshot.get("limitId") not in (None, "codex"):
        snapshot = {}
    windows = {}
    for key in ("primary", "secondary"):
        raw = snapshot.get(key) or {}
        duration = raw.get("windowDurationMins")
        if duration not in (300, 10080):
            continue
        used = raw.get("usedPercent")
        remaining = None
        if type(used) in (int, float) and math.isfinite(used):
            remaining = max(0.0, min(100.0, 100.0 - used))
        reset = raw.get("resetsAt")
        reset_at = None
        if type(reset) in (int, float) and math.isfinite(reset):
            try:
                reset_at = datetime.fromtimestamp(reset, zone)
            except (ValueError, OverflowError, OSError):
                pass
        windows[duration] = Window(remaining, reset_at)
    count = (response.get("rateLimitResetCredits") or {}).get("availableCount")
    if type(count) is not int or count < 0:
        count = None
    return Quota(windows.get(300), windows.get(10080), count,
                 fetched_at.astimezone(zone))
