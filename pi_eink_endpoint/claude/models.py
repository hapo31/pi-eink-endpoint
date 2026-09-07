"""Normalize Claude Code usage into the shared quota model."""

from datetime import datetime, timezone
import math
from zoneinfo import ZoneInfo

from pi_eink_endpoint.quota.models import Quota, Window


def normalize_quota(response, *, fetched_at=None, timezone_name="Asia/Tokyo"):
    zone = ZoneInfo(timezone_name)
    fetched_at = fetched_at or datetime.now(timezone.utc)

    def window(name):
        raw = response.get(name)
        if not isinstance(raw, dict):
            return None
        utilization = raw.get("utilization")
        remaining = None
        if type(utilization) in (int, float) and math.isfinite(utilization):
            remaining = max(0.0, min(100.0, 100.0 - utilization))
        reset = raw.get("resets_at")
        reset_at = None
        if isinstance(reset, str):
            try:
                reset_at = datetime.fromisoformat(reset.replace("Z", "+00:00")).astimezone(zone)
            except ValueError:
                pass
        return Window(remaining, reset_at)

    return Quota(window("five_hour"), window("seven_day"), None,
                 fetched_at.astimezone(zone))
