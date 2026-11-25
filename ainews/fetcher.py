"""RSS / Atom 抓取与解析（子Agent A 负责）。仅用标准库。

接口见 docs/CONTRACT.md 第 2 节：
- fetch_feed(source, timeout) -> FetchResult（绝不抛异常）
- parse_feed(data) -> (items, invalid_count)（纯函数）
- filter_by_window(items, start, end) -> (kept, stats)
"""
from __future__ import annotations

import gzip
import html
import re
import socket
import urllib.error
import urllib.request
import zlib
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

from ainews.timeutil import iso_utc, now_utc, parse_iso

USER_AGENT = "ai-news-homework/1.0 (+RSS reader)"
MAX_BYTES = 10 * 1024 * 1024  # 拒绝超过 10MB 的响应（压缩前与解压后都检查）
SUMMARY_MAX = 500

NS_ATOM = "http://www.w3.org/2005/Atom"
NS_CONTENT = "http://purl.org/rss/1.0/modules/content/"
NS_DC = "http://purl.org/dc/elements/1.1/"
NS_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
NS_RSS10 = "http://purl.org/rss/1.0/"


class FetchError(Exception):
    pass


# ---------------------------------------------------------------- 文本工具

_TAG_RE = re.compile(r"<[^>]*>", re.S)
_DROP_BLOCK_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.S | re.I)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_WS_RE = re.compile(r"\s+")


def _clean_text(s: Optional[str]) -> str:
    """去 HTML 标签（script/style 连内容一起删）、解码实体、压缩空白。"""
    if not s:
        return ""
    s = _COMMENT_RE.sub(" ", s)
    s = _DROP_BLOCK_RE.sub(" ", s)
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    # 实体解码后可能出现新的标签文本（如 &lt;script&gt;），再清一次
    s = _DROP_BLOCK_RE.sub(" ", s)
    s = _TAG_RE.sub(" ", s)
    s = s.replace(" ", " ")
    return _WS_RE.sub(" ", s).strip()


def _truncate(s: str, n: int = SUMMARY_MAX) -> str:
    return s if len(s) <= n else s[:n] + "…"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _text(el: Optional[ET.Element]) -> Optional[str]:
    if el is None:
        return None
    t = "".join(el.itertext())
    t = t.strip()
    return t or None


def _find(el: ET.Element, *names: str) -> Optional[ET.Element]:
    """按 '{ns}tag' 或 'tag' 依次查找直接子元素。"""
    for name in names:
        found = el.find(name)
        if found is not None:
            return found
    return None


# ---------------------------------------------------------------- 日期

def parse_date(raw: Optional[str]) -> Tuple[Optional[str], str]:
    """返回 (ISO UTC 字符串或 None, date_status)。

    支持 RFC 822/1123（email.utils）与 ISO 8601（含 Z、毫秒、无时区）。
    无时区一律按 UTC 解释。
    """
    if raw is None or not raw.strip():
        return None, "missing"
    s = raw.strip()
    dt: Optional[datetime] = None
    # RFC 822
    try:
        dt = parsedate_to_datetime(s)
    except (TypeError, ValueError, IndexError):
        dt = None
    if dt is None:
        dt = _parse_iso8601(s)
    if dt is None:
        return None, "unparseable"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        return iso_utc(dt), "ok"
    except (OverflowError, ValueError):
        return None, "unparseable"


_ISO_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})"
    r"(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:[.,](\d+))?)?)?"
    r"\s*(Z|[+-]\d{2}:?\d{2})?$",
    re.I,
)


def _parse_iso8601(s: str) -> Optional[datetime]:
    m = _ISO_RE.match(s)
    if not m:
        return None
    y, mo, d, hh, mi, ss, frac, tz = m.groups()
    try:
        micro = int((frac or "0")[:6].ljust(6, "0"))
        dt = datetime(int(y), int(mo), int(d), int(hh or 0), int(mi or 0), int(ss or 0), micro)
    except ValueError:
        return None
    if tz:
        if tz.upper() == "Z":
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            sign = 1 if tz[0] == "+" else -1
            digits = tz[1:].replace(":", "")
            try:
                off = timedelta(hours=int(digits[:2]), minutes=int(digits[2:4]))
                dt = dt.replace(tzinfo=timezone(sign * off))
            except ValueError:
                return None
    return dt


# ---------------------------------------------------------------- 解析

def _make_item(guid, title, link, summary_html, published_raw) -> Dict:
    published_at, status = parse_date(published_raw)
    return {
        "guid": guid.strip() if guid and guid.strip() else None,
        "title": _clean_text(title),
        "link": link.strip() if link and link.strip() else None,
        "summary": _truncate(_clean_text(summary_html)),
        "published_raw": published_raw.strip() if published_raw and published_raw.strip() else None,
        "published_at": published_at,
        "date_status": status,
    }


def _is_valid(item: Dict) -> bool:
    if not item["link"] and not item["guid"]:
        return False
    if not item["title"] and not item["link"]:
        return False
    return True


def _rss_item(it: ET.Element) -> Dict:
    title = _text(_find(it, "title", "{%s}title" % NS_RSS10))
    link = _text(_find(it, "link", "{%s}link" % NS_RSS10))
    guid = _text(it.find("guid"))
    if guid is None:
        about = it.get("{%s}about" % NS_RDF)
        guid = about
    summary = _text(_find(it, "description", "{%s}description" % NS_RSS10))
    if not summary:
        summary = _text(it.find("{%s}encoded" % NS_CONTENT))
    published = _text(_find(it, "pubDate", "{%s}date" % NS_DC))
    return _make_item(guid, title, link, summary, published)


def _atom_link(entry: ET.Element) -> Optional[str]:
    links = entry.findall("{%s}link" % NS_ATOM)
    for ln in links:
        if ln.get("rel", "alternate") == "alternate" and ln.get("href"):
            return ln.get("href")
    for ln in links:
        if ln.get("href"):
            return ln.get("href")
    return None


def _atom_entry(e: ET.Element) -> Dict:
    a = "{%s}" % NS_ATOM
    title = _text(e.find(a + "title"))
    guid = _text(e.find(a + "id"))
    link = _atom_link(e)
    summary = _text(e.find(a + "summary")) or _text(e.find(a + "content"))
    published = (
        _text(e.find(a + "published"))
        or _text(e.find(a + "updated"))
        or _text(e.find("{%s}date" % NS_DC))
    )
    return _make_item(guid, title, link, summary, published)


def parse_feed(data: bytes) -> Tuple[List[Dict], int]:
    """解析 RSS 2.0 / RSS 1.0(RDF) / Atom。XML 格式错误时抛 ET.ParseError / ValueError。

    （fetch_feed 会捕获并转为 ok=False。）
    """
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("parse_feed 需要 bytes")
    if len(data) > MAX_BYTES:
        raise ValueError("响应超过 10MB")
    # 防御：拒绝带 DOCTYPE 的文档（避免实体扩展类攻击；正常 feed 不需要 DTD）
    head = bytes(data[:4096]).lower()
    if b"<!doctype" in head or b"<!entity" in head:
        raise ValueError("拒绝含 DOCTYPE/ENTITY 声明的 XML（可能是 HTML 页面或不安全的 XML）")
    root = ET.fromstring(bytes(data))
    tag = _local(root.tag)
    raw_items: List[Dict] = []
    if tag == "rss":
        channel = root.find("channel")
        elems = channel.findall("item") if channel is not None else root.findall(".//item")
        raw_items = [_rss_item(x) for x in elems]
    elif tag == "RDF":
        raw_items = [_rss_item(x) for x in root.findall("{%s}item" % NS_RSS10)]
    elif tag == "feed":
        raw_items = [_atom_entry(x) for x in root.findall("{%s}entry" % NS_ATOM)]
    else:
        raise ValueError("不是 RSS/Atom 文档（根元素 <%s>）" % tag)
    items, invalid = [], 0
    for it in raw_items:
        if _is_valid(it):
            items.append(it)
        else:
            invalid += 1
    return items, invalid


# ---------------------------------------------------------------- 抓取

def _read_limited(resp, limit: int = MAX_BYTES) -> bytes:
    length = resp.headers.get("Content-Length")
    if length and length.isdigit() and int(length) > limit:
        raise FetchError("响应过大（Content-Length=%s，超过 10MB）" % length)
    buf = resp.read(limit + 1)
    if len(buf) > limit:
        raise FetchError("响应过大（超过 10MB）")
    return buf


def _decompress(data: bytes, encoding: str) -> bytes:
    enc = (encoding or "").lower().strip()
    if enc in ("gzip", "x-gzip") or data[:2] == b"\x1f\x8b":
        d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    elif enc == "deflate":
        try:
            d = zlib.decompressobj()
            out = d.decompress(data, MAX_BYTES + 1)
        except zlib.error:
            d = zlib.decompressobj(-zlib.MAX_WBITS)
            out = d.decompress(data, MAX_BYTES + 1)
        if len(out) > MAX_BYTES:
            raise FetchError("解压后超过 10MB")
        return out
    else:
        return data
    out = d.decompress(data, MAX_BYTES + 1)
    if len(out) > MAX_BYTES:
        raise FetchError("解压后超过 10MB")
    return out


def fetch_feed(source: dict, timeout: float = 20.0) -> dict:
    result = {
        "source_id": (source or {}).get("id", ""),
        "ok": False,
        "error": None,
        "http_status": None,
        "fetched_at": iso_utc(now_utc()),
        "items": [],
        "invalid_count": 0,
    }
    try:
        url = (source or {}).get("rss_url") or ""
        if not url.lower().startswith(("http://", "https://")):
            result["error"] = "无效的 RSS 地址：%r" % url
            return result
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
                "Accept-Encoding": "gzip, deflate",
            },
        )
        # urllib 默认跟随 3xx 重定向（最多 10 次）
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            result["http_status"] = status
            if not (200 <= int(status) < 300):
                result["error"] = "HTTP %s" % status
                return result
            raw = _read_limited(resp)
            data = _decompress(raw, resp.headers.get("Content-Encoding", ""))
        items, invalid = parse_feed(data)
        result.update(ok=True, items=items, invalid_count=invalid)
    except urllib.error.HTTPError as e:
        result["http_status"] = e.code
        result["error"] = "HTTP %s %s" % (e.code, e.reason)
    except urllib.error.URLError as e:
        result["error"] = "网络错误：%s" % (e.reason,)
    except (socket.timeout, TimeoutError):
        result["error"] = "网络超时（%ss）" % timeout
    except ET.ParseError as e:
        result["error"] = "XML 解析失败：%s" % e
    except (FetchError, ValueError, zlib.error, EOFError, gzip.BadGzipFile) as e:
        result["error"] = "%s：%s" % (type(e).__name__, e)
    except Exception as e:  # 兜底：绝不抛异常
        result["error"] = "未知错误 %s：%s" % (type(e).__name__, e)
    if not result["ok"]:
        result["items"], result["invalid_count"] = [], 0
    return result


# ---------------------------------------------------------------- 时间窗口

def filter_by_window(items: List[dict], start: datetime, end: datetime) -> Tuple[List[dict], dict]:
    stats = {"total": len(items), "in_window": 0, "out_of_window": 0, "no_date_kept": 0}
    kept: List[dict] = []
    for it in items:
        dt = parse_iso(it.get("published_at")) if it.get("date_status") == "ok" else None
        if dt is None:
            stats["no_date_kept"] += 1
            kept.append(it)
        elif start <= dt <= end:
            stats["in_window"] += 1
            kept.append(it)
        else:
            stats["out_of_window"] += 1
    return kept, stats
