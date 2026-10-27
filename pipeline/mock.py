"""dry-run 内置 mock：数据源 + 每一步的 tool-calling 剧本。

dry-run 不访问网络、不调 LLM API。ScriptedChat（agent.py）按这里的剧本回放
带 tool_calls 的助手消息，工具执行走的是真实代码路径（web_search / fetch_page
命中 mock 数据，write_file 照常落盘），因此整个 tool-calling 循环在 dry-run
下是被真实执行的，只是"模型"换成了剧本。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MockSources:
    facts: dict[str, str]
    results: list[dict[str, str]]
    pages: dict[str, str]

    def search_answer(self, query: str) -> str:
        lines = [f"[mock web_search] query={query!r}，返回 {len(self.results)} 条结果："]
        for i, r in enumerate(self.results, 1):
            lines.append(f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet']}")
        return "\n".join(lines)

    def page(self, url: str) -> str | None:
        return self.pages.get(url)


def build_mock_sources(merchant: str, city: str) -> MockSources:
    facts = {
        "merchant": merchant,
        "city": city,
        "rating": "4.6",
        "reviews": "328",
        "years": "8",
        "opened": "2018",
        "phone": "0571-8888 6688",
        "email": "laowang@mockmail.example",
        "address": f"{city}市西湖区文三路 138 号",
        "dianping": "https://mock.dianping.example/shop/10086",
        "meituan": "https://mock.meituan.example/shop/10086",
        "article": "https://mock.mp.example/article/foodie-2024",
    }
    f = facts
    results = [
        {
            "title": f"{merchant}（{city}）点评主页 - 大众点评",
            "url": f["dianping"],
            "snippet": f"{merchant}，{f['address']}，评分 {f['rating']}，{f['reviews']} 条评价，人均 ¥18。",
        },
        {
            "title": f"{merchant} 外卖店 - 美团",
            "url": f["meituan"],
            "snippet": f"{merchant} 美团外卖，月售 1200+，招牌煎饼 ¥15 起。",
        },
        {
            "title": f"开了 {f['years']} 年的煎饼铺：{city} 街坊的早餐据点 - 本地美食号",
            "url": f["article"],
            "snippet": f"{merchant} 自 {f['opened']} 年在文三路摆摊起家，现已是老街坊的固定早餐点。",
        },
    ]
    pages = {
        f["dianping"]: (
            f"{merchant}（{city} · 文三路店）\n"
            f"地址：{f['address']}\n"
            f"电话：{f['phone']}\n"
            f"评分：{f['rating']}/5.0（{f['reviews']} 条评价）\n"
            "人均：¥18\n"
            "营业时间：06:30-20:00\n"
            f"商户邮箱：{f['email']}\n"
            f"经营起始：{f['opened']} 年\n"
            "热门标签：早餐 煎饼 老字号 老板热情"
        ),
        f["meituan"]: (
            f"{merchant}（外卖店）\n"
            "月售 1200+；评分 4.5\n"
            "菜单：原味煎饼 ¥9 / 加蛋加肠 ¥15 / 全家福 3 份装 ¥39\n"
            "起送 ¥20，配送费 ¥3\n"
            f"联系方式：{f['phone']}"
        ),
        f["article"]: (
            f"……{merchant} 是文三路一带的早餐名店。{f['opened']} 年王老板推车摆摊起家，"
            f"{f['years']} 年过去，攒下 {f['reviews']} 条点评、{f['rating']} 分的成绩。"
            "店里至今没有自己的网站，老顾客都靠平台页面找到他……"
        ),
    }
    return MockSources(facts=facts, results=results, pages=pages)


# ---------------------------------------------------------------------------
# 每一步的剧本：list[round]，每个 round 是 list[(tool_name, arguments)]，
# 最后一轮一定是 submit_*。
# ---------------------------------------------------------------------------


def research_script(m: MockSources) -> list[list[tuple[str, dict]]]:
    f = m.facts
    items = [
        {"kind": "rating", "text": f"大众点评评分 {f['rating']}/5，同类目靠前",
         "ok": True, "warn": "", "sourceUrl": f["dianping"]},
        {"kind": "reviews", "text": f"{f['reviews']} 条评价，近期以好评为主（标签：早餐、老字号、老板热情）",
         "ok": True, "warn": "", "sourceUrl": f["dianping"]},
        {"kind": "website", "text": "未发现独立官网：搜索前 3 条均为大众点评/美团店铺页与媒体稿",
         "ok": True, "warn": "无独立站是本次外联的核心切入点", "sourceUrl": f["dianping"]},
        {"kind": "years", "text": f"约 {f['opened']} 年开业，经营 {f['years']} 年（媒体稿与点评页一致）",
         "ok": True, "warn": "", "sourceUrl": f["article"]},
        {"kind": "contact", "text": f"电话 {f['phone']}；店铺邮箱 {f['email']}（点评页公示）",
         "ok": True, "warn": "", "sourceUrl": f["dianping"]},
    ]
    return [
        [("web_search", {"query": f"{f['merchant']}（{f['city']}） 评分 评价"}),
         ("web_search", {"query": f"{f['merchant']} {f['city']} 官网 官方网站"})],
        [("fetch_page", {"url": f["dianping"]})],
        [("submit_research", {"items": items})],
    ]


def site_script(m: MockSources, research_items: list[dict]) -> list[list[tuple[str, dict]]]:
    f = m.facts
    summary = "\n".join(f"- [{it['kind']}] {it['text']}" for it in research_items) or "- （无调研数据）"
    blocks = {
        "nav": {"brand": f["merchant"], "items": ["首页", "街坊口碑", "三档套餐", "找到我们"]},
        "hero": {
            "headline": f"排队 {f['years']} 年的{f['merchant']}，线上也能下单了",
            "subline": f"{f['address']} · 每天 06:30 现摊第一张饼",
            "cta": "看看三档套餐",
        },
        "trust": {
            "title": "街坊都认这口",
            "points": [
                f"大众点评 {f['rating']} 分 · {f['reviews']} 条评价",
                f"{f['opened']} 年开业，现摊现卖 {f['years']} 年",
                f"地址：{f['address']}",
            ],
        },
        "services": {
            "title": "三档套餐（demo 定价，可随时改）",
            "tiers": [
                {"name": "基础款", "price": "¥9", "features": ["原味煎饼", "现磨豆浆一杯"]},
                {"name": "招牌款", "price": "¥15", "features": ["加蛋加肠煎饼", "热豆浆一杯", "免排队预留"]},
                {"name": "全家福", "price": "¥39", "features": ["招牌款 × 3", "自制小菜一盒", "同城闪送"]},
            ],
        },
        "footer": {
            "lines": [
                f"地址：{f['address']}",
                f"电话（微信同号）：{f['phone']}",
                "营业时间：每天 06:30 - 20:00",
                f"© 2026 {f['merchant']} · 本页为 demo",
            ]
        },
    }
    return [
        [("write_file", {"path": "notes/research-summary.md",
                         "content": f"# {f['merchant']}（{f['city']}）调研摘要\n\n{summary}\n"})],
        [("submit_site", {"blocks": blocks})],
    ]


def email_script(m: MockSources, site_url: str) -> list[list[tuple[str, dict]]]:
    f = m.facts
    link = site_url or "（demo 链接待补）"
    first_body = (
        f"王老板您好：\n\n"
        f"我们是本地小商户建站小组。顺手查了一下："
        f"咱们店大众点评 {f['rating']} 分、{f['reviews']} 条评价，但搜不到自己的官网，老顾客只能去平台页找您。\n\n"
        f"我们照着店里情况做了个一页官网小样（手机直接打开、不用维护）：\n{link}\n"
        f"里面是三档套餐价格表。您看着顺眼，我可以按您的意思改一版正式的；不打算花钱也完全没关系，小样就当送您的。\n\n"
        f"祝生意兴隆！\n小陈 · 本地建站小组"
    )
    followup_body = (
        f"王老板您好，上周发您的官网小样不知道有没有空看（30 秒能划完）：\n{link}\n\n"
        "页面里那三档价格是先按常见规格摆的，您给个实际价位，我照着改。\n\n"
        "这周忙我就不再打扰了。回复 1，我把正式版方案和报价发您；回复 2，我就把您从名单里划掉，绝不纠缠。\n\n"
        "小陈"
    )
    return [[("submit_emails", {
        "first": {
            "to": f["email"],
            "subject": f"给「{f['merchant']}」做了个官网小样，30 秒能看完",
            "body": first_body,
        },
        "followup": {
            "to": f["email"],
            "subject": "Re: 官网小样 —— 上周发您的那份",
            "body": followup_body,
        },
    })]]


def score_script(m: MockSources, research_items: list[dict]) -> list[list[tuple[str, dict]]]:
    f = m.facts
    return [[("submit_score", {
        "factors": [
            {"label": "口碑与评价", "points": 24, "max": 30},
            {"label": "独立站缺口", "points": 27, "max": 30},
            {"label": "经营稳定性", "points": 16, "max": 20},
            {"label": "可触达性", "points": 17, "max": 20},
        ],
        "verdict": "高优先线索：口碑扎实、没有独立站、联系方式齐全，建议 24 小时内首次联系",
        "rationale": (
            f"口碑与评价 24/30：{f['rating']} 分、{f['reviews']} 条评价说明有稳定客源，线上形象有变现空间；"
            "独立站缺口 27/30：目前只有平台店铺页，这正是我们能提供的最大价值点；"
            f"经营稳定性 16/20：{f['opened']} 年开业、经营 {f['years']} 年，有付费能力也有长期合作可能；"
            f"可触达性 17/20：电话 {f['phone']} 与邮箱都能直连店主，触达成本低。"
            "综合 84/100 落在 contact-now 区间，首触邮件直接附 demo 链接即可。"
        ),
    })]]
