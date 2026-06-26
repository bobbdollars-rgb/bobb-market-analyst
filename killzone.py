"""
killzone.py — ICT Kill Zone schedule (WIB / UTC+7)
Source: BOBB MARKET ANALYST v3.0 prompt, Step 7D

All times are WIB (UTC+7), matching Bobb's location (Pontianak).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional, List, Tuple


@dataclass
class KillZone:
    name: str
    start_wib: dt.time
    end_wib: dt.time
    priority: str   # "HIGH" or "NORMAL"


KILL_ZONES: List[KillZone] = [
    KillZone("Asian Session", dt.time(6, 0), dt.time(9, 0), "NORMAL"),
    KillZone("London Open KZ", dt.time(14, 0), dt.time(17, 0), "HIGH"),
    KillZone("London Mid", dt.time(17, 0), dt.time(19, 0), "NORMAL"),
    KillZone("NY Open KZ", dt.time(19, 30), dt.time(22, 0), "HIGH"),
    KillZone("NY Mid", dt.time(22, 0), dt.time(23, 59), "NORMAL"),
    KillZone("NY Close", dt.time(1, 0), dt.time(3, 0), "NORMAL"),
]

SILVER_BULLET_WINDOWS: List[KillZone] = [
    KillZone("Silver Bullet (London pre-market)", dt.time(3, 0), dt.time(4, 0), "HIGH"),
    KillZone("Silver Bullet (London-NY overlap)", dt.time(17, 0), dt.time(18, 0), "HIGH"),
    KillZone("Silver Bullet (NY mid-session)", dt.time(23, 0), dt.time(23, 59), "HIGH"),
]


def _in_window(t: dt.time, start: dt.time, end: dt.time) -> bool:
    if start <= end:
        return start <= t <= end
    return t >= start or t <= end   # window wraps past midnight


def current_kill_zone(now_wib: dt.datetime) -> Optional[KillZone]:
    t = now_wib.time()
    for kz in KILL_ZONES:
        if _in_window(t, kz.start_wib, kz.end_wib):
            return kz
    return None


def active_silver_bullet(now_wib: dt.datetime) -> Optional[KillZone]:
    t = now_wib.time()
    for sb in SILVER_BULLET_WINDOWS:
        if _in_window(t, sb.start_wib, sb.end_wib):
            return sb
    return None


def _combine_aware(base: dt.datetime, t: dt.time) -> dt.datetime:
    """Combines base's date with time t, preserving base's tzinfo."""
    naive = dt.datetime.combine(base.date(), t)
    return naive.replace(tzinfo=base.tzinfo) if base.tzinfo else naive


def next_kill_zone(now_wib: dt.datetime) -> Tuple[KillZone, float]:
    """Returns (KillZone, minutes_until) for the next upcoming zone (any priority)."""
    candidates = []
    for kz in KILL_ZONES:
        start_dt = _combine_aware(now_wib, kz.start_wib)
        if start_dt <= now_wib:
            start_dt += dt.timedelta(days=1)
        candidates.append((kz, (start_dt - now_wib).total_seconds() / 60))
    candidates.sort(key=lambda x: x[1])
    return candidates[0]


def next_high_priority_kill_zone(now_wib: dt.datetime) -> Tuple[KillZone, float]:
    candidates = []
    for kz in KILL_ZONES:
        if kz.priority != "HIGH":
            continue
        start_dt = _combine_aware(now_wib, kz.start_wib)
        if start_dt <= now_wib:
            start_dt += dt.timedelta(days=1)
        candidates.append((kz, (start_dt - now_wib).total_seconds() / 60))
    candidates.sort(key=lambda x: x[1])
    return candidates[0]


def is_high_priority_active(now_wib: dt.datetime) -> bool:
    kz = current_kill_zone(now_wib)
    sb = active_silver_bullet(now_wib)
    return (kz is not None and kz.priority == "HIGH") or sb is not None
