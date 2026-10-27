"""四个步骤的系统提示词、submit_* 工具 schema、以及提交校验/规范化函数。

校验函数同时是规范化函数：合法则返回规范化后的结构，非法则抛 ValueError
（agent 会把错误喂回模型让它重试）。所有文本字段在这里强制"纯文本、禁 HTML"。
"""
from __future__ import annotations

import json
import re
from typing import Any

from .scoring import band_for
from .tools import FETCH_PAGE_DEFINITION, WEB_SEARCH_DEFINITION, WRITE_FILE_DEFINITION

STEP_NAMES = {0: "research", 1: "site", 2: "email", 3: "followup+score"}

SYSTEM_PROMPT = (
    "你是「小商户增长代理」，负责为本地小商户做调研、生成单页官网 demo 文案、写外联邮件并给线索打分。\n"
    "硬规则：\n"
    "1. 必须通过调用工具完成工作，最终用 submit_* 工具一次性提交结构化结果；\n"
    "2. 所有提交的文本一律是纯文本中文，严禁 HTML 标签或代码片段；\n"
    "2b. 🔴 不许直接回复文本：每个步骤的最终产物必须用该步的 submit_* 工具提交，"
    "直接回文本等于步骤失败（会被要求重调工具，多次不调判失败）；\n"
    "3. 事实性内容必须来自工具返回的证据，不得编造；查不到就 ok=false 并在 warn 里写明；\n"
    "4. 🔴 禁止第一人称亲历叙述：不许写「路过 / 吃过 / 去过 / 用过 / 我买过 / 我们店附近」"
    "这类假装亲自到过、亲自体验过的表述，也不许虚构与店主的交集；"
    "所有文案只能基于商户公开信息（评分 / 评价 / 官网状态）和样页本身说话；\n"
    "5. 🔴 email:draft 正文里的 demo 链接必须 https:// 开头（形如 https://chengmo.top/demo/<slug>/），"
    "禁止出现 file:/// 、本地路径、或任何不带域名的相对路径；\n"
    "6. 🔴 禁止编造第三方案例、客户名与效果数据：不许写「别家店用了之后新客变多」「某客户反馈」"
    "这类社会证明；没有真实案例时不许暗示有，只能讲样页本身（价格、版式、怎么改）。"
)

RESEARCH_TOOLS = [WEB_SEARCH_DEFINITION, FETCH_PAGE_DEFINITION]
SITE_TOOLS = [WRITE_FILE_DEFINITION]
SITE_BLOCK_ORDER = ("nav", "hero", "trust", "services", "footer")
RESEARCH_KINDS = ("rating", "reviews", "website", "years", "contact", "other")

_TAG_RE = re.compile(r"<[a-zA-Z/!][^>]*>")


def ensure_plain_text(**fields: str) -> None:
    for key, value in fields.items():
        if value and _TAG_RE.search(value):
            raise ValueError(f"{key} 含 HTML 片段：文本字段必须纯文本")


def _s(value: Any, name: str = "字段") -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 必须是非空字符串")
    return value.strip()


def _int_in(value: Any, lo: int, hi: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} 必须是整数")
    if not lo <= value <= hi:
        raise ValueError(f"{name} 必须在 {lo}~{hi} 之间")
    return value


def _str_list(value: Any, lo: int, hi: int, name: str) -> list[str]:
    if not isinstance(value, list) or not lo <= len(value) <= hi:
        raise ValueError(f"{name} 必须是 {lo}~{hi} 个元素的字符串数组")
    return [_s(item, f"{name}[{i}]") for i, item in enumerate(value)]


def _research_json(research_items: list[dict]) -> str:
    if not research_items:
        return "（本次无调研数据：请使用中性写法，不要编造具体数字）"
    return json.dumps(research_items, ensure_ascii=False, indent=2)


# ===========================================================================
# step 0: research
# ===========================================================================

SUBMIT_RESEARCH = {
    "type": "function",
    "function": {
        "name": "submit_research",
        "description": "提交调研结论（一次性提交全部条目）。kind 覆盖 rating/reviews/website/years/contact，缺哪项就 ok=false。",
        "parameters": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string", "enum": list(RESEARCH_KINDS)},
                            "text": {"type": "string", "description": "纯文本结论，例如：大众点评评分 4.6/5（328 条评价）"},
                            "ok": {"type": "boolean"},
                            "warn": {"type": "string", "description": "ok=false 时写原因；否则空字符串"},
                            "sourceUrl": {"type": "string", "description": "证据来源 URL，没有则空字符串"},
                        },
                        "required": ["kind", "text", "ok", "warn", "sourceUrl"],
                    },
                }
            },
            "required": ["items"],
        },
    },
}


def research_task(merchant: str, city: str) -> str:
    return (
        f"调研目标商户：{merchant}（城市：{city}）。\n"
        "用 web_search 查 2~3 次（关键词：店名、店名+城市），再用 fetch_page 抓 1~2 个来源 URL 查明：\n"
        "1. rating：在点评/美团/地图等平台的评分；\n"
        "2. reviews：评价数量与近期口碑倾向；\n"
        "3. website：有没有独立官网（还是只有第三方平台店铺页）；\n"
        "4. years：经营年限（大约哪年开业）；\n"
        "5. contact：公开电话/邮箱。\n"
        "每条结论必须写 sourceUrl；查不到的项 ok=false 且 warn 写明尝试过什么。"
        "完成后调用 submit_research 一次性提交。"
    )


def validate_research(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("items 必须是非空数组")
    if len(items) > 8:
        raise ValueError("items 最多 8 条")
    out: list[dict[str, Any]] = []
    for i, raw in enumerate(items):
        if not isinstance(raw, dict):
            raise ValueError(f"items[{i}] 必须是对象")
        kind = raw.get("kind")
        if kind not in RESEARCH_KINDS:
            raise ValueError(f"items[{i}].kind 非法: {kind!r}")
        text = _s(raw.get("text"), f"items[{i}].text")
        ok = raw.get("ok")
        if not isinstance(ok, bool):
            raise ValueError(f"items[{i}].ok 必须是布尔值")
        warn = (raw.get("warn") or "").strip()
        source_url = (raw.get("sourceUrl") or "").strip()
        ensure_plain_text(**{f"items[{i}].text": text, f"items[{i}].warn": warn})
        if not ok and not warn:
            warn = "未查明"
        out.append({"index": len(out), "kind": kind, "text": text,
                    "ok": ok, "warn": warn, "sourceUrl": source_url})
    return out


# ===========================================================================
# step 1: site
# ===========================================================================

SUBMIT_SITE = {
    "type": "function",
    "function": {
        "name": "submit_site",
        "description": "提交单页官网 demo 的五个文案区块（纯文本）。services.tiers 必须恰好 3 档且每档有价格。",
        "parameters": {
            "type": "object",
            "properties": {
                "blocks": {
                    "type": "object",
                    "properties": {
                        "nav": {
                            "type": "object",
                            "properties": {
                                "brand": {"type": "string"},
                                "items": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 4},
                            },
                            "required": ["brand", "items"],
                        },
                        "hero": {
                            "type": "object",
                            "properties": {
                                "headline": {"type": "string"},
                                "subline": {"type": "string"},
                                "cta": {"type": "string"},
                            },
                            "required": ["headline", "subline", "cta"],
                        },
                        "trust": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "points": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 5},
                            },
                            "required": ["title", "points"],
                        },
                        "services": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "tiers": {
                                    "type": "array",
                                    "minItems": 3,
                                    "maxItems": 3,
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"},
                                            "price": {"type": "string", "description": "含货币符号，例如 ¥15"},
                                            "features": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 4},
                                        },
                                        "required": ["name", "price", "features"],
                                    },
                                },
                            },
                            "required": ["title", "tiers"],
                        },
                        "footer": {
                            "type": "object",
                            "properties": {
                                "lines": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 6},
                            },
                            "required": ["lines"],
                        },
                    },
                    "required": ["nav", "hero", "trust", "services", "footer"],
                }
            },
            "required": ["blocks"],
        },
    },
}


def site_task(merchant: str, city: str, research_items: list[dict]) -> str:
    return (
        f"为 {merchant}（{city}）生成单页官网 demo 的全部文案区块。调研数据（JSON）：\n"
        f"{_research_json(research_items)}\n"
        "要求：\n"
        "- blocks 恰好包含 nav/hero/trust/services/footer 五个键；\n"
        "- services.tiers 恰好 3 档，每档有 name、price（含货币符号）、2~4 条 features"
        "（商户自己的产品/套餐定价，基于调研合理设计）；\n"
        "- 全部纯文本中文，禁止 HTML；数字必须与调研一致，调研没有的用不含具体数字的写法；\n"
        "- 可以先调用 write_file 把草稿存到 notes/ 下。\n"
        "完成后调用 submit_site 提交。"
    )


def validate_site(payload: dict[str, Any]) -> dict[str, Any]:
    blocks = payload.get("blocks")
    if not isinstance(blocks, dict):
        raise ValueError("blocks 必须是对象")

    nav = blocks.get("nav")
    if not isinstance(nav, dict):
        raise ValueError("缺少 nav 区块")
    nav_items = _str_list(nav.get("items"), 2, 4, "nav.items")
    nav_out = {"brand": _s(nav.get("brand"), "nav.brand"), "items": nav_items}

    hero = blocks.get("hero")
    if not isinstance(hero, dict):
        raise ValueError("缺少 hero 区块")
    hero_out = {
        "headline": _s(hero.get("headline"), "hero.headline"),
        "subline": _s(hero.get("subline"), "hero.subline"),
        "cta": _s(hero.get("cta"), "hero.cta"),
    }

    trust = blocks.get("trust")
    if not isinstance(trust, dict):
        raise ValueError("缺少 trust 区块")
    trust_out = {
        "title": _s(trust.get("title"), "trust.title"),
        "points": _str_list(trust.get("points"), 2, 5, "trust.points"),
    }

    services = blocks.get("services")
    if not isinstance(services, dict):
        raise ValueError("缺少 services 区块")
    tiers_raw = services.get("tiers")
    if not isinstance(tiers_raw, list) or len(tiers_raw) != 3:
        raise ValueError("services.tiers 必须恰好 3 档（三档价格）")
    tiers = []
    for i, tier in enumerate(tiers_raw):
        if not isinstance(tier, dict):
            raise ValueError(f"tiers[{i}] 必须是对象")
        tiers.append({
            "name": _s(tier.get("name"), f"tiers[{i}].name"),
            "price": _s(tier.get("price"), f"tiers[{i}].price"),
            "features": _str_list(tier.get("features"), 2, 4, f"tiers[{i}].features"),
        })
    services_out = {"title": _s(services.get("title"), "services.title"), "tiers": tiers}

    footer = blocks.get("footer")
    if not isinstance(footer, dict):
        raise ValueError("缺少 footer 区块")
    footer_out = {"lines": _str_list(footer.get("lines"), 2, 6, "footer.lines")}

    ensure_plain_text(**{
        "nav.brand": nav_out["brand"], **{f"nav.items[{i}]": t for i, t in enumerate(nav_items)},
        "hero.headline": hero_out["headline"], "hero.subline": hero_out["subline"], "hero.cta": hero_out["cta"],
        "trust.title": trust_out["title"], **{f"trust.points[{i}]": p for i, p in enumerate(trust_out["points"])},
        "services.title": services_out["title"],
        **{f"tiers[{i}].price": t["price"] for i, t in enumerate(tiers)},
        **{f"footer.lines[{i}]": l for i, l in enumerate(footer_out["lines"])},
    })
    return {"nav": nav_out, "hero": hero_out, "trust": trust_out,
            "services": services_out, "footer": footer_out}


# ===========================================================================
# step 2: email
# ===========================================================================

SUBMIT_EMAILS = {
    "type": "function",
    "function": {
        "name": "submit_emails",
        "description": "提交两封外联邮件（首触 first + 跟进 followup），全部纯文本。",
        "parameters": {
            "type": "object",
            "properties": {
                "first": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "店主邮箱"},
                        "subject": {"type": "string"},
                        "body": {"type": "string"},
                    },
                    "required": ["to", "subject", "body"],
                },
                "followup": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string"},
                        "subject": {"type": "string"},
                        "body": {"type": "string"},
                    },
                    "required": ["to", "subject", "body"],
                },
            },
            "required": ["first", "followup"],
        },
    },
}


def email_task(merchant: str, city: str, research_items: list[dict], site_url: str) -> str:
    link = site_url or "（demo 链接待补）"
    return (
        f"给 {merchant}（{city}）的店主写两封外联邮件。调研数据（JSON）：\n"
        f"{_research_json(research_items)}\n"
        f"demo 站地址（写进正文）：{link}\n"
        "要求：\n"
        "- first（首触）：正文 100~260 字，口语化，开头提一个调研到的真实细节（评分/年限），"
        "再自然引出 demo 链接，结尾一个明确 CTA；\n"
        "- followup（跟进）：假定首触 5 天后无回复再发，正文 60~200 字，不施压，给对方退出选项；\n"
        "- to 必须用调研到的真实邮箱（research contact 项的 sourceUrl/文本里能对上的那个），"
        "查不到真实邮箱就不要编 placeholder，"
        "直接不提交（调 submit_emails 前 to 缺失视为校验失败）；\n"
        "- 全部纯文本中文，禁止 HTML 与营销腔。\n"
        "完成后调用 submit_emails 提交。"
    )


def validate_emails(payload: dict[str, Any]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for kind in ("first", "followup"):
        mail = payload.get(kind)
        if not isinstance(mail, dict):
            raise ValueError(f"缺少 {kind} 邮件")
        to = _s(mail.get("to"), f"{kind}.to")
        subject = _s(mail.get("subject"), f"{kind}.subject")
        body = _s(mail.get("body"), f"{kind}.body")
        if "@" not in to:
            raise ValueError(f"{kind}.to 必须是邮箱地址")
        if "unknown" in to.lower() or "example." in to.lower():
            raise ValueError(f"{kind}.to 是占位邮箱（{to}），不许发："
                             "调研没找到真实邮箱就停在待核实")
        if len(subject) > 80:
            raise ValueError(f"{kind}.subject 过长（>80 字符）")
        if not 30 <= len(body) <= 1200:
            raise ValueError(f"{kind}.body 长度需在 30~1200 字符之间")
        ensure_plain_text(**{f"{kind}.subject": subject, f"{kind}.body": body})
        out[kind] = {"kind": kind, "to": to, "subject": subject, "body": body}
    return out


# ===========================================================================
# step 3: followup + score
# ===========================================================================

SUBMIT_SCORE = {
    "type": "function",
    "function": {
        "name": "submit_score",
        "description": "提交线索评分：恰好 4 条判据，分数必须能对上调研数据。",
        "parameters": {
            "type": "object",
            "properties": {
                "factors": {
                    "type": "array",
                    "minItems": 4,
                    "maxItems": 4,
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "points": {"type": "integer"},
                            "max": {"type": "integer"},
                        },
                        "required": ["label", "points", "max"],
                    },
                },
                "verdict": {"type": "string", "description": "一句话结论"},
                "rationale": {"type": "string", "description": "2~3 句打分理由"},
            },
            "required": ["factors", "verdict", "rationale"],
        },
    },
}


def score_task(merchant: str, city: str, research_items: list[dict]) -> str:
    return (
        f"按固定四条判据给线索 {merchant}（{city}）打分。调研数据（JSON）：\n"
        f"{_research_json(research_items)}\n"
        "判据与满分（顺序固定）：\n"
        "1. 口碑与评价（max 30）：评分越高、评价越多分越高；\n"
        "2. 独立站缺口（max 30）：没有独立官网或官网很弱则高分（这正是我们的价值点）；\n"
        "3. 经营稳定性（max 20）：经营年限越长分越高；\n"
        "4. 可触达性（max 20）：有公开电话/邮箱、能直连决策人则高分。\n"
        "要求：factors 恰好 4 条且顺序如上；分数要能对上调研里的具体事实"
        "（不许写「根据调研」这类空话）；verdict 一句话；rationale 2~3 句。"
        "完成后调用 submit_score 提交。"
    )


def validate_score(payload: dict[str, Any]) -> dict[str, Any]:
    factors_raw = payload.get("factors")
    if not isinstance(factors_raw, list) or len(factors_raw) != 4:
        raise ValueError("factors 必须恰好 4 条判据")
    factors = []
    for i, raw in enumerate(factors_raw):
        if not isinstance(raw, dict):
            raise ValueError(f"factors[{i}] 必须是对象")
        label = _s(raw.get("label"), f"factors[{i}].label")
        points = _int_in(raw.get("points"), 0, 50, f"factors[{i}].points")
        max_points = _int_in(raw.get("max"), 1, 50, f"factors[{i}].max")
        if points > max_points:
            raise ValueError(f"factors[{i}].points 不能超过 max")
        ensure_plain_text(**{f"factors[{i}].label": label})
        factors.append({"index": i, "label": label, "points": points,
                        "max": max_points})
    verdict = _s(payload.get("verdict"), "verdict")
    rationale = _s(payload.get("rationale"), "rationale")
    if len(rationale) < 20:
        raise ValueError("rationale 至少 20 个字符")
    ensure_plain_text(verdict=verdict, rationale=rationale)
    total = sum(f["points"] for f in factors)
    max_total = sum(f["max"] for f in factors)
    return {
        "factors": factors,
        "total": total,
        "maxTotal": max_total,
        "band": band_for(total, max_total),
        "verdict": verdict,
        "rationale": rationale,
    }
