"""单文件 demo 站渲染：零外部请求（无图片/CDN/外链字体）、系统字体、3 档价格。

所有区块内容都是纯文本，渲染时统一 html.escape，因此模型输出不可能注入标记。
"""
from __future__ import annotations

import html as _html
import hashlib
import re

# 公网 demo 基址（部署在 chengmo.top/demo/<slug>/）
DEMO_BASE = "https://chengmo.top/demo"


def _slug(part: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (part or "").lower()).strip("-")


def public_url(merchant: str, city: str = "") -> str:
    """demo 站的公网地址。🔴 绝不返回 file:/// 或本地路径（外发邮件里点得开才行）。"""
    slug = _slug(merchant)
    if not slug:
        # 中文店名过不了 ASCII slug ⇒ 用稳定短哈希，避免不同商户撞同一个 /demo/shop/
        slug = "shop-" + hashlib.sha1(f"{merchant}|{city}".encode("utf-8")).hexdigest()[:8]
    city_slug = _slug(city)
    if city_slug and city_slug not in slug:
        slug = f"{slug}-{city_slug}"
    return f"{DEMO_BASE}/{slug[:60]}/"

# 纯 CSS 常量（不进 f-string，避免大括号转义问题）
_CSS = """
  :root { --ink:#292524; --muted:#78716c; --amber:#d97706; --paper:#faf7f2; --card:#ffffff; --line:#e7e5e4; }
  * { box-sizing:border-box; margin:0; padding:0 }
  body { font-family: system-ui,-apple-system,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
         color:var(--ink); background:var(--paper); line-height:1.6 }
  header { position:sticky; top:0; z-index:1; background:rgba(250,247,242,.95); border-bottom:1px solid var(--line);
           display:flex; justify-content:space-between; align-items:center; padding:14px 24px }
  header .brand { font-weight:700; font-size:18px }
  header nav a { color:var(--ink); text-decoration:none; margin-left:18px; font-size:15px }
  header nav a:hover { color:var(--amber) }
  .hero { text-align:center; padding:72px 24px 56px; background:linear-gradient(180deg,#fdf6ec,var(--paper)) }
  .hero h1 { font-size:clamp(26px,5vw,40px); letter-spacing:1px }
  .hero p { color:var(--muted); margin:14px auto 26px; max-width:34em }
  .hero .cta { display:inline-block; background:var(--amber); color:#fff; padding:12px 28px;
               border-radius:999px; text-decoration:none; font-weight:600 }
  .trust, .services { padding:40px 24px; max-width:960px; margin:0 auto }
  .trust h2, .services h2 { font-size:20px; margin-bottom:14px }
  .trust ul { list-style:none; display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:12px }
  .trust li { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px 16px }
  .services { padding-bottom:56px }
  .tiers { display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:16px }
  .tier { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:22px; position:relative }
  .tier.hot { border-color:var(--amber); box-shadow:0 6px 18px rgba(217,119,6,.15) }
  .tier .badge { position:absolute; top:-10px; right:16px; background:var(--amber); color:#fff;
                 font-size:12px; padding:2px 10px; border-radius:999px }
  .tier h3 { font-size:16px; color:var(--muted) }
  .tier .price { font-size:28px; font-weight:700; margin:6px 0 12px }
  .tier ul { list-style:none }
  .tier li { padding-left:1.3em; position:relative }
  .tier li::before { content:"\\2713"; position:absolute; left:0; color:var(--amber) }
  footer { background:#292524; color:#d6d3d1; padding:36px 24px; text-align:center; font-size:14px }
  footer .line { margin:3px 0 }
  footer .fine { color:#a8a29e; margin-top:14px; font-size:12px }
"""


def render_site(merchant: str, city: str, blocks: dict) -> str:
    e = _html.escape
    nav, hero, trust = blocks["nav"], blocks["hero"], blocks["trust"]
    services, footer = blocks["services"], blocks["footer"]

    anchors = ("top", "trust", "services", "contact")
    nav_links = "".join(f'<a href="#{a}">{e(t)}</a>' for t, a in zip(nav["items"], anchors))
    trust_points = "".join(f"<li>{e(p)}</li>" for p in trust["points"])
    tiers = "".join(_tier_card(i, t) for i, t in enumerate(services["tiers"]))
    footer_lines = "".join(f'<p class="line">{e(l)}</p>' for l in footer["lines"])

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="merchant-pipeline 0.1 (demo)">
<title>{e(merchant)} · {e(city)}</title>
<style>{_CSS}</style>
</head>
<body>
<header>
  <div class="brand">{e(nav["brand"])}</div>
  <nav>{nav_links}</nav>
</header>
<section class="hero" id="top">
  <h1>{e(hero["headline"])}</h1>
  <p>{e(hero["subline"])}</p>
  <a class="cta" href="#services">{e(hero["cta"])}</a>
</section>
<section class="trust" id="trust">
  <h2>{e(trust["title"])}</h2>
  <ul>{trust_points}</ul>
</section>
<section class="services" id="services">
  <h2>{e(services["title"])}</h2>
  <div class="tiers">{tiers}</div>
</section>
<footer id="contact">
  {footer_lines}
  <p class="fine">本页面是自动生成的单文件 demo（零外部请求 · 系统字体）· {e(merchant)}（{e(city)}）</p>
</footer>
</body>
</html>
"""


def _tier_card(index: int, tier: dict) -> str:
    e = _html.escape
    hot = " hot" if index == 1 else ""
    badge = '<span class="badge">最受欢迎</span>' if index == 1 else ""
    features = "".join(f"<li>{e(x)}</li>" for x in tier["features"])
    return (
        f'<div class="tier{hot}">{badge}'
        f'<h3>{e(tier["name"])}</h3>'
        f'<div class="price">{e(tier["price"])}</div>'
        f"<ul>{features}</ul></div>"
    )
