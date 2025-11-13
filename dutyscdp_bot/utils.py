from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


def seconds_until(target_time: time, timezone: str) -> float:
    tz = ZoneInfo(timezone)
    now = datetime.now(tz=tz)
    target_dt = now.replace(hour=target_time.hour, minute=target_time.minute, second=0, microsecond=0)
    if target_dt <= now:
        target_dt += timedelta(days=1)
    return (target_dt - now).total_seconds()
