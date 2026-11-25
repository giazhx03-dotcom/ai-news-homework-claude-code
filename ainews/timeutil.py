"""时间工具：统一 UTC 存储、Asia/Singapore 展示。（主控维护）"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Union

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    raise SystemExit("需要 Python 3.9+（zoneinfo）")

TZ_NAME = "Asia/Singapore"
SGT = ZoneInfo(TZ_NAME)


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def to_utc(dt: datetime) -> datetime:
    """aware -> UTC；naive 视为 UTC（调用方需自行保证）。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_utc(dt: Optional[datetime]) -> Optional[str]:
    """datetime -> '2026-11-26T01:00:00+00:00'；None -> None。"""
    if dt is None:
        return None
    return to_utc(dt).replace(microsecond=0).isoformat()


def parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return to_utc(dt)


def to_sgt(v: Union[datetime, str, None]) -> Optional[datetime]:
    if v is None:
        return None
    dt = parse_iso(v) if isinstance(v, str) else to_utc(v)
    return dt.astimezone(SGT) if dt else None


def fmt_sgt(v: Union[datetime, str, None], missing: str = "日期缺失") -> str:
    """展示用：'2026-11-26 09:00 SGT'。"""
    dt = to_sgt(v)
    return dt.strftime("%Y-%m-%d %H:%M SGT") if dt else missing


def sgt_date(v: Union[datetime, str, None] = None) -> str:
    """返回新加坡日期 'YYYY-MM-DD'（默认当前）。"""
    dt = to_sgt(v) if v is not None else now_utc().astimezone(SGT)
    return dt.strftime("%Y-%m-%d")


def parse_cli_time(s: str) -> datetime:
    """命令行时间：ISO 格式；不带时区时按 Asia/Singapore 解释。"""
    s = s.strip().replace("Z", "+00:00")
    if len(s) == 10:  # YYYY-MM-DD
        s += "T00:00:00"
    dt = datetime.fromisoformat(s.replace(" ", "T"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SGT)
    return to_utc(dt)
