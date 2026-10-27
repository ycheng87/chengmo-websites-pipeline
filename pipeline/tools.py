"""三个基础工具：web_search / fetch_page / write_file。

- dry-run：web_search、fetch_page 走内置 mock 数据，不联网。
- 真跑：web_search 走 DuckDuckGo HTML 端点（无需额外 key），fetch_page 直接
  GET 目标 URL 并抽取正文纯文本。
- write_file 永远受沙箱限制：只能写在本次运行的 artifacts 目录内。

工具执行的任何失败都以 "TOOL_ERROR: ..." 文本返回给模型（让它自行调整重试），
绝不抛出异常打断 pipeline。
"""
from __future__ import annotations

import asyncio
import html as _html
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .mock import MockSources

MAX_PAGE_CHARS = 4000
USER_AGENT = "Mozilla/5.0 (compatible; merchant-pipeline/0.1)"

WEB_SEARCH_DEFINITION = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "搜索网络并返回前若干条结果（标题、链接、摘要，纯文本）。用于调研商户口碑、是否有独立官网等。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
            },
            "required": ["query"],
        },
    },
}

FETCH_PAGE_DEFINITION = {
    "type": "function",
    "function": {
        "name": "fetch_page",
        "description": "抓取 URL 页面并返回正文纯文本（自动去除 HTML 标签），最长约 4000 字符。",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "http/https URL"},
            },
            "required": ["url"],
        },
    },
}

WRITE_FILE_DEFINITION = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": "把文本内容写入本次运行的工作目录（runs/<runId>/artifacts/ 下），路径必须是相对路径且不允许越界。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对路径，例如 notes/summary.md"},
                "content": {"type": "string", "description": "要写入的文本内容"},
            },
            "required": ["path", "content"],
        },
    },
}


class ToolError(Exception):
    """工具调用参数或执行层面的错误。"""


class ToolBox:
    def __init__(self, run_dir: Path, mock_sources: MockSources | None = None,
                 http_timeout_s: float = 20.0) -> None:
        self.run_dir = Path(run_dir).resolve()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._mock = mock_sources
        self._timeout = http_timeout_s

    @property
    def dry_run(self) -> bool:
        return self._mock is not None

    async def execute(self, name: str, args: dict[str, Any]) -> str:
        """执行工具；错误也写成 TOOL_ERROR 文本喂回模型，绝不炸 pipeline。"""
        try:
            if name == "web_search":
                return await self._web_search(self._arg(args, "query"))
            if name == "fetch_page":
                return await self._fetch_page(self._arg(args, "url"))
            if name == "write_file":
                return await self._write_file(
                    self._arg(args, "path"),
                    self._arg(args, "content", allow_empty=True),
                )
            raise ToolError(f"未知工具 {name!r}")
        except Exception as e:
            return f"TOOL_ERROR: {type(e).__name__}: {e}"

    # -- web_search ---------------------------------------------------------

    async def _web_search(self, query: str) -> str:
        if self.dry_run:
            return self._mock.search_answer(query)
        # 主力 DeepSeek 搜索 API（灵犀 10-27 H-02 返工令：DDG 在香港机基本不产出，
        # 2026-10-26 连真店 5 项也一项没出，DDG 降为辅助）。
        # 需要 SEARCH_API_KEY（DeepSeek 平台 key，不硬编码）；没配时才走 DDG。
        ds = await asyncio.to_thread(self._deepseek_search, query)
        if not ds.startswith("TOOL_ERROR") and not ds.startswith("NO_RESULTS"):
            return ds
        ds_note = f"DeepSeek 不行：{ds}"
        try:
            out = await asyncio.to_thread(self._duckduckgo_search, query)
            if not out.startswith("NO_RESULTS"):
                return f"[fallback(ds={ds_note})] {out}"
            ddg_note = "DDG 无结果"
        except Exception as e:  # noqa: BLE001
            out = ""
            ddg_note = f"DDG 失败({type(e).__name__})"
        return f"{ds_note}；{ddg_note}"

    def _deepseek_search(self, query: str) -> str:
        """DeepSeek 搜索 API 兜底（需要 SEARCH_API_KEY）。结构化返回 title/url/summary。"""
        key = os.environ.get("SEARCH_API_KEY", "").strip()
        if not key:
            return "NO_RESULTS: 未配置 SEARCH_API_KEY，无法兜底"
        payload = {"query": query, "count": 6, "need_content": False}
        req = urllib.request.Request(
            "https://api.deepseek.com/v1/tools/search",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", "ignore"))
        lines: list[str] = []
        for i, r in enumerate((body.get("results") or [])[:8], start=1):
            title = (r.get("title") or "").strip()
            url = (r.get("url") or "").strip()
            summary = re.sub(r"\s+", " ", (r.get("summary") or ""))[:220]
            if not url:
                continue
            lines.append(f"{i}. {title}\n   {url}\n   {summary}")
        if not lines:
            return "NO_RESULTS: DeepSeek 也没有返回可解析结果"
        return "\n".join(lines)

    def _duckduckgo_search(self, query: str) -> str:
        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            page = resp.read().decode("utf-8", errors="replace")

        items: list[tuple[str, str]] = []
        for m in re.finditer(r"<a([^>]*result__a[^>]*)>(.*?)</a>", page, re.S):
            href_m = re.search(r'href="([^"]+)"', m.group(1))
            if not href_m:
                continue
            items.append((self._strip_tags(m.group(2)), self._unwrap_ddg(href_m.group(1))))
        snippets = [self._strip_tags(m.group(2))
                    for m in re.finditer(r"<a([^>]*result__snippet[^>]*)>(.*?)</a>", page, re.S)]

        lines: list[str] = []
        seen: set[str] = set()
        for i, (title, link) in enumerate(items):
            if not link or link in seen:
                continue
            seen.add(link)
            snip = snippets[i] if i < len(snippets) else ""
            lines.append(f"{len(lines) + 1}. {title}\n   {link}\n   {snip}")
            if len(lines) >= 8:
                break
        if not lines:
            return "NO_RESULTS: 搜索引擎没有返回可解析的结果，请更换关键词后重试。"
        return "\n".join(lines)

    @staticmethod
    def _unwrap_ddg(href: str) -> str:
        # DuckDuckGo 的跳转链接形如 //duckduckgo.com/l/?uddg=<urlencoded>
        if "uddg=" in href:
            for kv in href.split("?", 1)[1].split("&"):
                if kv.startswith("uddg="):
                    return urllib.parse.unquote(kv[5:])
        if href.startswith("//"):
            href = "https:" + href
        return href

    # -- fetch_page ---------------------------------------------------------

    async def _fetch_page(self, url: str) -> str:
        if self.dry_run:
            page = self._mock.page(url)
            if page is None:
                raise ToolError(f"mock 数据中没有该页面: {url}")
            return page
        return await asyncio.to_thread(self._http_fetch_text, url)

    def _http_fetch_text(self, url: str) -> str:
        if not url.startswith(("http://", "https://")):
            raise ToolError("只支持 http/https URL")
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            raw = resp.read(2_000_000)
        charset = resp.headers.get_content_charset() or "utf-8"
        return self._html_to_text(raw.decode(charset, errors="replace"))[:MAX_PAGE_CHARS]

    @staticmethod
    def _html_to_text(page_html: str) -> str:
        page_html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", page_html)
        page_html = re.sub(r"(?i)<(br|/p|/div|/li|/h[1-6])>", "\n", page_html)
        text = re.sub(r"<[^>]+>", " ", page_html)
        text = _html.unescape(text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n", text)
        return text.strip()

    # -- write_file ---------------------------------------------------------

    async def _write_file(self, path: str, content: str) -> str:
        rel = Path(path.replace("\\", "/"))
        if rel.is_absolute() or ".." in rel.parts:
            raise ToolError("path 必须是工作目录内的相对路径（禁止绝对路径或 ..）")
        target = (self.run_dir / rel).resolve()
        if not target.is_relative_to(self.run_dir):
            raise ToolError("path 逃出了运行工作目录")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"OK: 已写入 {rel.as_posix()}（{len(content.encode('utf-8'))} bytes）"

    # -- misc ---------------------------------------------------------------

    @staticmethod
    def _arg(args: dict[str, Any], key: str, allow_empty: bool = False) -> str:
        value = args.get(key)
        if not isinstance(value, str):
            raise ToolError(f"参数 {key} 必须是字符串")
        if not allow_empty and not value.strip():
            raise ToolError(f"参数 {key} 不能为空")
        return value

    @staticmethod
    def _strip_tags(s: str) -> str:
        return _html.unescape(re.sub(r"<[^>]+>", "", s)).strip()
