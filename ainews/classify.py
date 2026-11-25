"""规则筛选与分类（主控维护）。规则全部来自 config/rules.json。"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

CATEGORIES = ["模型与研究", "产品与工具", "行业与政策", "待确认"]
PENDING = "待确认"
DEFAULT_RULES = Path(__file__).resolve().parent.parent / "config" / "rules.json"


def _compile_term(term: str) -> Tuple[str, "re.Pattern"]:
    """拉丁词用“非字母数字”边界；中文按子串；* 结尾为前缀匹配。"""
    prefix = term.endswith("*")
    t = term[:-1] if prefix else term
    has_cjk = any("一" <= ch <= "鿿" for ch in t)
    esc = re.escape(t)
    if has_cjk:
        # 中文词中可能夹带英文（如“AI芯片”），英文端仍需边界
        left = r"(?<![A-Za-z0-9])" if t[0].isascii() and t[0].isalnum() else ""
        right = r"(?![A-Za-z0-9])" if t[-1].isascii() and t[-1].isalnum() else ""
        pat = left + esc + right
    else:
        pat = r"(?<![A-Za-z0-9])" + esc + (r"[A-Za-z0-9\-]*" if prefix else r"(?![A-Za-z0-9])")
    return t, re.compile(pat, re.IGNORECASE)


def _compile_group(group: dict) -> List[Tuple[str, "re.Pattern"]]:
    terms = []
    for lang in ("zh", "en"):
        terms.extend(group.get(lang, []))
    return [_compile_term(t) for t in terms]


class Rules:
    def __init__(self, data: dict):
        r = data["relevance"]
        self.core = _compile_group(r["core_terms"])
        self.context = _compile_group(r["context_terms"])
        self.entity = _compile_group(r.get("entity_terms", {}))
        self.off_topic = _compile_group(r["off_topic_terms"])
        self.incidental = _compile_group(r["incidental_terms"])
        self.ai_token = re.compile(r["ai_token"]["pattern"])  # 大小写敏感
        self.th_include = r["threshold_include"]
        self.th_pending = r["threshold_pending"]
        self.cats = {k: _compile_group(v) for k, v in data["categories"].items() if not k.startswith("_")}
        self.examples = data.get("examples", [])


@lru_cache(maxsize=4)
def load_rules(path: Optional[str] = None) -> Rules:
    p = Path(path) if path else DEFAULT_RULES
    return Rules(json.loads(p.read_text(encoding="utf-8")))


def _hits(text: str, terms) -> List[str]:
    return [name for name, pat in terms if pat.search(text)]


def assess(title: str, summary: str = "", rules: Optional[Rules] = None) -> Dict:
    """返回 {"relevance": "yes"|"maybe"|"no", "score": int, "category": str|None, "reason": str}。

    步骤：先判断 AI 相关性，再分类；无关条目 category=None。
    """
    rules = rules or load_rules()
    title = title or ""
    summary = summary or ""

    core_t = _hits(title, rules.core)
    core_s = [h for h in _hits(summary, rules.core) if h not in core_t]
    tok_t = bool(rules.ai_token.search(title))
    tok_s = bool(rules.ai_token.search(summary))
    ctx = set(_hits(title, rules.context)) | set(_hits(summary, rules.context))
    ent_t = _hits(title, rules.entity)
    ent_s = [h for h in _hits(summary, rules.entity) if h not in ent_t]

    score = 3 * len(core_t) + 2 * len(core_s)
    score += 3 if tok_t else (1 if tok_s else 0)
    score += min(2, len(ctx))
    score += 2 * len(ent_t) + len(ent_s)
    signals = core_t + core_s + (["AI"] if (tok_t or tok_s) else []) + ent_t + ent_s + sorted(ctx)

    off = _hits(title, rules.off_topic)
    incidental = _hits(title + " " + summary, rules.incidental)
    if off and not core_t:
        why = f"排除：标题属体育/娱乐/社会语境（{', '.join(off[:3])}）"
        if incidental:
            why += f"，仅提及 AI 使用（{', '.join(incidental[:2])}）"
        return {"relevance": "no", "score": score, "category": None, "reason": why}

    if score >= rules.th_include and (core_t or core_s or tok_t):
        relevance = "yes"
    elif score >= rules.th_pending and signals:
        relevance = "maybe"
    else:
        return {"relevance": "no", "score": score, "category": None,
                "reason": "排除：AI 信号不足" + (f"（{', '.join(signals[:4])}）" if signals else "")}

    sig = "命中：" + ", ".join(dict.fromkeys(signals[:6]))
    if relevance == "maybe":
        return {"relevance": "maybe", "score": score, "category": PENDING,
                "reason": sig + "；相关性较弱，待人工确认"}

    cat_scores = {}
    cat_hits = {}
    for cat, terms in rules.cats.items():
        ht = _hits(title, terms)
        hs = [h for h in _hits(summary, terms) if h not in ht]
        cat_scores[cat] = 2 * len(ht) + len(hs)
        cat_hits[cat] = ht + hs
    best = max(cat_scores.values()) if cat_scores else 0
    winners = [c for c, s in cat_scores.items() if s == best]
    if best >= 2 and len(winners) == 1:
        cat = winners[0]
        reason = f"{sig}；分类依据：{', '.join(cat_hits[cat][:5])}"
    else:
        cat = PENDING
        detail = "、".join(f"{c}{s}" for c, s in cat_scores.items())
        reason = f"{sig}；分类不明确（得分 {detail}）"
    return {"relevance": "yes", "score": score, "category": cat, "reason": reason}
