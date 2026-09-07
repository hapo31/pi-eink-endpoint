"""Provider-independent quota values displayed on the panel."""

from dataclasses import dataclass, replace
from datetime import datetime


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
