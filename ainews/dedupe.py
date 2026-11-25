"""去重：条目 ID 与规范化链接。（主控维护）"""
from __future__ import annotations

import hashlib
import re
from typing import List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# 常见追踪参数，规范化时移除
TRACKING_PARAMS = {
    "fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "spm", "from",
    "ref", "ref_src", "source", "cmpid", "share", "share_source", "igshid",
}


def normalize_url(url: Optional[str]) -> Optional[str]:
    """规范化链接：统一 https、小写主机、去 www、默认端口、片段、追踪参数，排序查询，去尾斜杠。"""
    if not url:
        return None
    url = url.strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return None
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    port = parts.port if parts.port not in (None, 80, 443) else None
    netloc = host + (f":{port}" if port else "")
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    query = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    ]
    query.sort()
    return urlunsplit(("https", netloc, path, urlencode(query), ""))


def dedupe_keys(source_id: str, guid: Optional[str], link: Optional[str]) -> List[str]:
    """返回该条目的全部去重键（任一键已存在即视为重复）。

    - guid:<来源>:<guid>  条目 ID 在来源内唯一
    - url:<规范化链接>      跨来源也能识别同一链接
    """
    keys = []
    if guid and guid.strip():
        keys.append(f"guid:{source_id}:{guid.strip()}")
    norm = normalize_url(link)
    if norm:
        keys.append(f"url:{norm}")
    return keys


def article_id(keys: List[str]) -> str:
    return hashlib.sha1(keys[0].encode("utf-8")).hexdigest()[:16]
