# Chengmo Websites — merchant-pipeline (Meta Hackathon 2026 submission)

> **Fictional demo brands only — not real merchants.** Every merchant shown in the demo and docs (Lone Star Window Tinting · Marigold & Mop · Wagtail & Whisk · Post & Paling · Maple Street Bakehouse) is an invented demonstration brand. None is a real customer, no real performance data (reply rates, revenue, conversion) is claimed, and no real merchant name, email address, or phone number appears anywhere in this repo. The live showroom: https://chengmo.top/demo/hackathon-demo/

---

# merchant-pipeline v0.1

小商户自动化 agent 骨架：**research → demo site → outreach → follow-up + score**。
整条流水线是一个 async generator，逐个 yield 事件；每次运行的完整事件流落盘到
`runs/<runId>.jsonl`，产物（demo 站、邮件草稿、调研笔记）落盘到 `runs/<runId>/artifacts/`。

```
python -m pipeline --merchant "店名" --city "城市" [--dry-run]

        ┌─ step 0 research ─┐  ┌─ step 1 site ─┐  ┌─ step 2 email ─┐  ┌─ step 3 score ─┐
事件流:  pipeline:start → step:start → research:item×N → site:block×5 → site:ready
        → email:draft×2 → score:factor×4 → score:total → score:rationale
        → step:done×4 → pipeline:done        （任何失败 → pipeline:error）

LLM:    OpenAI 兼容接口（唯一供应商），base_url/model/key 全部读环境变量
工具:   web_search(query) / fetch_page(url) / write_file(path, content)
        + 每步一个 submit_* 结构化提交工具（tool calling 驱动，非纯文本提示）
```

## 安装

- Python **3.10+**（Windows / macOS / Linux 均可）
- `--dry-run` **零第三方依赖**（纯标准库即可跑通全流程，不需要 key）
- 真跑才需要安装 SDK：

```bash
pip install -r requirements.txt   # 只含 openai>=1.40
```

## 跑 dry-run（自测，不调 API）

```bash
python -m pipeline --merchant "老王煎饼铺" --city "杭州" --dry-run
```

dry-run 不联网、不调 LLM。它用 `ScriptedChat` 把"模型"换成内置剧本，但
tool-calling 循环、工具执行、提交校验走的是与真跑**完全相同**的代码路径：
`web_search`/`fetch_page` 命中 `pipeline/mock.py` 的 mock 数据，`write_file`
真实落盘。输出示例（节选）：

```
pipeline:start  runId=20261024-112523-1af8ea  merchant=老王煎饼铺  startedAt=2026-10-24T04:25:23+00:00
step:start      step=0  (research)
research:item   [0] rating   ok  大众点评评分 4.6/5，同类目靠前  src=https://mock.dianping.example/shop/10086
...
site:ready      url=file:///.../runs/20261024-112523-1af8ea/artifacts/site.html  bytes=4510  html=<4510 bytes，完整内容见 jsonl>
score:total     total=84  band=contact-now  verdict=高优先线索：口碑扎实、没有独立站、联系方式齐全，建议 24 小时内首次联系
pipeline:done   elapsedMs=22  score=84
# 事件流已写入 runs\20261024-112523-1af8ea.jsonl
```

> 控制台里 `site:ready` 的 html 字段只显示摘要避免刷屏；**jsonl 里是全量 HTML**。

## 接真 key 真跑

三个环境变量（没有硬编码、没有默认 key）：

| 环境变量 | 含义 | 默认值 |
|---|---|---|
| `MODEL_API_KEY` | API key（必填，dry-run 除外） | 无 |
| `META_BASE_URL` | OpenAI 兼容接口地址 | `https://api.meta.ai/v1` |
| `META_MODEL` | 模型名 | `muse-spark-1.3` |

Git Bash：

```bash
export META_BASE_URL="https://api.meta.ai/v1"     # 或你的兼容端点
export META_MODEL="muse-spark-1.3"
export MODEL_API_KEY="sk-..."                      # 只放环境变量，别写进代码
python -m pipeline --merchant "老王煎饼铺" --city "杭州"
```

PowerShell：

```powershell
$env:META_BASE_URL="https://api.meta.ai/v1"
$env:META_MODEL="muse-spark-1.3"
$env:MODEL_API_KEY="sk-..."
python -m pipeline --merchant "老王煎饼铺" --city "杭州"
```

可选调优：`PIPELINE_HTTP_TIMEOUT`（网络超时秒数，默认 90；site/email 单次生成实测 20~30s，设 20 会把正常生成误判成超时）、
`PIPELINE_MAX_TOOL_ROUNDS`（每步最大工具轮数，默认 8）。

真跑时工具会真实联网：`web_search` 走 DuckDuckGo HTML 端点（无需额外搜索 key），
`fetch_page` 直接 GET 目标页并抽取正文纯文本，`write_file` 仍被沙箱限制在本次
运行目录内。工具失败不会炸流水线——错误以 `TOOL_ERROR: ...` 文本喂回模型自行重试。

## 事件契约

事件名与字段一个字不能改（`pipeline/events.py` 里有强校验，缺字段/拼错直接抛错）：

| 事件 | 载荷字段 |
|---|---|
| `pipeline:start` | `runId, merchant, startedAt` |
| `step:start` | `step`（0=research 1=site 2=email 3=followup+score） |
| `research:item` | `index, kind, text, ok, warn, sourceUrl`（kind ∈ rating/reviews/website/years/contact/other） |
| `site:block` | `key`（nav/hero/trust/services/footer） |
| `site:ready` | `url, bytes, html`（html 只出现在这里） |
| `email:draft` | `kind`（first/followup）`, to, subject, body` |
| `score:factor` | `index, label, points, max` + `evidence`（功能要求：每条判据必须带证据字符串） |
| `score:total` | `total, verdict, band`（band ∈ contact-now/maybe/skip） |
| `score:rationale` | `text` |
| `llm:usage` | `step, model, promptTokens, completionTokens, reasoningTokens, totalTokens, durationMs, toolCalls, textOnly`（每次 LLM HTTP 调用一行，用于算钱与判断“只回文本不调工具”） |
| `step:done` | `step, durationMs` |
| `pipeline:done` | `elapsedMs, score` |
| `pipeline:error` | `step, message, recoverable` |

jsonl 每行格式：`{"ts": ..., "event": "...", "data": {...}}`。

打分规则：四条判据固定满分 30/30/20/20（口碑与评价 / 独立站缺口 / 经营稳定性 /
可触达性）；`band` 由代码按总分占比计算（≥70% → `contact-now`，≥40% → `maybe`，
否则 `skip`），不信任模型自报。

## 产物

```
runs/
├── 20261024-112523-1af8ea.jsonl        # 完整事件流（每行一个事件）
└── 20261024-112523-1af8ea/
    └── artifacts/
        ├── site.html                   # 单文件 demo 站：零外部请求、系统字体、3 档价格
        ├── emails/first.txt            # 首触邮件
        ├── emails/followup.txt         # 跟进邮件
        └── notes/research-summary.md   # 调研摘要（模型经 write_file 写入）
```

## 代码结构

```
pipeline/
├── __main__.py   # CLI 入口：参数解析、控制台渲染、写 runs/<runId>.jsonl
├── run.py        # 主编排 async generator（四步 + 错误降级 + 兜底 net）
├── agent.py      # tool-calling 循环 + OpenAIChat(真跑) / ScriptedChat(dry-run)
├── tools.py      # web_search / fetch_page / write_file（含 OpenAI 工具 schema 与沙箱）
├── steps.py      # 四步的系统提示词、submit_* schema、校验/规范化（强制纯文本）
├── site.py       # 单文件 HTML 渲染（内容统一 escape，无外部资源）
├── scoring.py    # band 计算（contact-now / maybe / skip）
├── mock.py       # dry-run 内置数据 + 每步 tool-calling 剧本
├── events.py     # 事件名契约 + jsonl 落盘
└── config.py     # 环境变量读取（META_BASE_URL / META_MODEL / MODEL_API_KEY）
```

## 设计说明（对照硬约束）

- **单一 LLM 供应商**：只有 `agent.py:OpenAIChat` 一个模型入口，OpenAI 兼容协议，
  base_url/model/key 全部来自环境变量；代码里没有任何 key。
- **必须 tool calling**：每个步骤都要求模型通过 `web_search`/`fetch_page`/`write_file`
  完成工作，并最终调用 `submit_research`/`submit_site`/`submit_emails`/`submit_score`
  提交结构化结果；直接回文本会被 nudge 一次，仍不改则报 `pipeline:error`。
- **纯文本字段**：所有进入事件的文本在校验层用正则拒绝 HTML 片段；HTML 只存在于
  `site:ready.html`，且由确定性模板渲染、内容统一 `html.escape`。
- **失败必报**：工具失败 → `TOOL_ERROR` 文本回喂模型重试；步骤失败 →
  `pipeline:error(step, message, recoverable=true)` 并降级继续；致命失败（缺 key、
  API 不通、内部 bug）→ `pipeline:error(recoverable=false)` 终止。所有网络调用带
  超时、工具轮数有上限，不会静默卡死。

## 已知限制（v0.1）

- 真跑的 `web_search` 用 DuckDuckGo HTML 端点，可能被限流或改版；返回 0 结果时模型会
  收到 `NO_RESULTS` 提示并换关键词重试，仍失败则该步降级为 `pipeline:error`。
- 真跑效果依赖你接入的模型对 function calling 的支持质量。
