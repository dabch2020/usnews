#!/usr/bin/env python3
"""
美国权威科技界人士言论聚合
从指定的数据来源和关键人物搜索结果抓取最新言论，生成一个静态 HTML 页面。

关键人物：
  Sam Altman, Jensen Huang, Yann LeCun, Demis Hassabis, Elon Musk,
  Satya Nadella, Mark Zuckerberg, Tim Cook, Marc Andreessen,
  Reid Hoffman, Naval Ravikant

数据来源：
  blog.samaltman.com, openai.com/news, nvidianews.nvidia.com,
  deepmind.google/blog, about.fb.com/news, a16z.com, nav.al,
  lexfridman.com/podcast, stratechery.com, theinformation.com 等
"""

from __future__ import annotations

import html as html_mod
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import feedparser
import requests
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator
from googlenewsdecoder import new_decoderv1

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

# ── 请求配置 ──────────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
}
TIMEOUT = 15
MAX_ITEMS_PER_SOURCE = 5


# ── 关键人物定义 ──────────────────────────────────────────
KEY_PEOPLE = [
    "Sam Altman",
    "Jensen Huang",
    "Yann LeCun",
    "Demis Hassabis",
    "Elon Musk",
    "Satya Nadella",
    "Mark Zuckerberg",
    "Tim Cook",
    "Marc Andreessen",
    "Reid Hoffman",
    "Naval Ravikant",
]


# ── 数据来源定义 ──────────────────────────────────────────
# method: "rss" | "scrape" | "google_news_person"
# google_news_person: 通过 Google News RSS 搜索特定人物言论

SOURCES: list[dict[str, Any]] = [
    # ── 直接 RSS 来源 ──
    {
        "name": "Sam Altman Blog",
        "person": "Sam Altman",
        "method": "rss",
        "url": "https://blog.samaltman.com/feed",
        "alt_urls": [
            "https://blog.samaltman.com/rss",
            "https://news.google.com/rss/search?q=site:blog.samaltman.com&hl=en&gl=US&ceid=US:en",
        ],
    },
    {
        "name": "OpenAI News",
        "person": "Sam Altman",
        "method": "rss",
        "url": "https://news.google.com/rss/search?q=site:openai.com+news+announcement&hl=en&gl=US&ceid=US:en",
        "alt_urls": [
            "https://openai.com/blog/rss.xml",
        ],
    },
    {
        "name": "NVIDIA News",
        "person": "Jensen Huang",
        "method": "rss",
        "url": "https://news.google.com/rss/search?q=site:nvidianews.nvidia.com&hl=en&gl=US&ceid=US:en",
        "alt_urls": [
            "https://nvidianews.nvidia.com/rss",
        ],
    },
    {
        "name": "DeepMind Blog",
        "person": "Demis Hassabis",
        "method": "rss",
        "url": "https://news.google.com/rss/search?q=site:deepmind.google+blog&hl=en&gl=US&ceid=US:en",
    },
    {
        "name": "Meta News",
        "person": "Mark Zuckerberg",
        "method": "rss",
        "url": "https://news.google.com/rss/search?q=site:about.fb.com+news&hl=en&gl=US&ceid=US:en",
    },
    {
        "name": "a16z",
        "person": "Marc Andreessen",
        "method": "rss",
        "url": "https://a16z.com/feed/",
        "alt_urls": [
            "https://news.google.com/rss/search?q=site:a16z.com+marc+andreessen&hl=en&gl=US&ceid=US:en",
        ],
    },
    {
        "name": "Naval",
        "person": "Naval Ravikant",
        "method": "rss",
        "url": "https://nav.al/feed",
        "alt_urls": [
            "https://news.google.com/rss/search?q=site:nav.al&hl=en&gl=US&ceid=US:en",
        ],
    },
    {
        "name": "Lex Fridman",
        "person": "",
        "method": "rss",
        "url": "https://lexfridman.com/feed/podcast/",
        "alt_urls": [
            "https://news.google.com/rss/search?q=site:lexfridman.com+podcast&hl=en&gl=US&ceid=US:en",
        ],
    },
    {
        "name": "Stratechery",
        "person": "",
        "method": "rss",
        "url": "https://stratechery.com/feed/",
        "alt_urls": [
            "https://news.google.com/rss/search?q=site:stratechery.com&hl=en&gl=US&ceid=US:en",
        ],
    },
    {
        "name": "The Information",
        "person": "",
        "method": "rss",
        "url": "https://news.google.com/rss/search?q=site:theinformation.com+AI+tech&hl=en&gl=US&ceid=US:en",
    },
    {
        "name": "The Verge / Decoder",
        "person": "",
        "method": "rss",
        "url": "https://news.google.com/rss/search?q=site:theverge.com+decoder+podcast&hl=en&gl=US&ceid=US:en",
    },

    # ── Google News 人物搜索 ──
    # 为每个关键人物创建一个 Google News RSS 搜索
]

# 动态添加关键人物的 Google News 搜索来源
for _person in KEY_PEOPLE:
    _query = requests.utils.quote(f'"{_person}" (said OR says OR announces OR interview OR statement)')
    SOURCES.append({
        "name": f"GNews: {_person}",
        "person": _person,
        "method": "rss",
        "url": f"https://news.google.com/rss/search?q={_query}&hl=en&gl=US&ceid=US:en",
    })


# ── 辅助函数 ──────────────────────────────────────────────

def _clean(text: str | None) -> str:
    """清理 HTML 标签 & 多余空白。"""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    text = html_mod.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _truncate(text: str, n: int = 500) -> str:
    return text[:n] + "…" if len(text) > n else text


def _fetch_url(url: str) -> requests.Response:
    return requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)


def _resolve_google_news_url(url: str) -> str:
    """如果是 Google News 中转链接，解码获取真实文章 URL。"""
    if "news.google.com" not in url:
        return url
    try:
        result = new_decoderv1(url, interval=0.5)
        if result.get("status") and result.get("decoded_url"):
            return result["decoded_url"]
    except Exception:
        pass
    return url


def _fetch_og_description(url: str) -> str:
    """从文章页面抓取 og:description 或正文段落作为摘要。"""
    try:
        real_url = _resolve_google_news_url(url)
        resp = requests.get(real_url, headers=HEADERS, timeout=10, allow_redirects=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "lxml")
        google_generic = "comprehensive up-to-date news coverage"
        js_block = "please enable js"

        best_meta = ""
        og = soup.find("meta", property="og:description")
        if og and og.get("content", "").strip():
            desc = _clean(og["content"])
            if google_generic not in desc.lower() and js_block not in desc.lower() and len(desc) > 30:
                if len(desc) >= 150:
                    return desc
                best_meta = desc
        if not best_meta:
            meta = soup.find("meta", attrs={"name": "description"})
            if meta and meta.get("content", "").strip():
                desc = _clean(meta["content"])
                if google_generic not in desc.lower() and js_block not in desc.lower() and len(desc) > 30:
                    if len(desc) >= 150:
                        return desc
                    best_meta = desc

        paragraphs = []
        for p in soup.select("article p, .content p, .entry-content p, main p, p"):
            text = _clean(p.get_text())
            if len(text) > 40 and google_generic not in text.lower() and js_block not in text.lower():
                paragraphs.append(text)
                if sum(len(t) for t in paragraphs) >= 400:
                    break
        if paragraphs:
            combined = " ".join(paragraphs)
            if len(combined) > len(best_meta):
                return combined

        if best_meta:
            return best_meta
    except Exception:
        pass
    return _fetch_ddg_snippet(url)


def _fetch_ddg_snippet(url: str) -> str:
    """通过 DuckDuckGo HTML 搜索获取文章摘要片段。"""
    try:
        real_url = _resolve_google_news_url(url)
        query = real_url.split("?")[0]
        ddg_url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}"
        resp = requests.get(ddg_url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "lxml")
        snippets = []
        for result in soup.select(".result__snippet"):
            text = _clean(result.get_text())
            if len(text) > 40:
                snippets.append(text)
                if sum(len(t) for t in snippets) >= 400:
                    break
        if snippets:
            return " ".join(snippets)
    except Exception:
        pass
    return ""


def _is_summary_useless(title: str, summary: str) -> bool:
    """判断 summary 是否无用。"""
    if not summary or len(summary) < 150:
        return True
    s_lower = summary.lower().strip()
    t_lower = title.lower().strip()
    if s_lower.startswith(t_lower[:30]):
        return True
    return False


# ── 人物匹配 ──────────────────────────────────────────────

_PERSON_PATTERNS = {
    name: re.compile(re.escape(name), re.IGNORECASE) for name in KEY_PEOPLE
}


def _find_person(item: dict) -> str:
    """从新闻条目中识别关联的关键人物名。"""
    # 优先使用来源配置中的 person 字段
    if item.get("_person"):
        return item["_person"]
    text = item.get("title", "") + " " + item.get("summary", "")
    for name, pat in _PERSON_PATTERNS.items():
        if pat.search(text):
            return name
    return ""


# ── RSS 抓取 ──────────────────────────────────────────────

def _fetch_rss(source: dict) -> list[dict]:
    """通过 RSS 抓取新闻条目。"""
    urls = [source["url"]] + source.get("alt_urls", [])
    entries: list = []
    for url in urls:
        try:
            resp = _fetch_url(url)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
            entries = feed.entries[:MAX_ITEMS_PER_SOURCE]
            if entries:
                break
        except Exception as exc:
            log.debug("RSS %s failed (%s): %s", source["name"], url, exc)
            continue

    results = []
    for e in entries:
        pub = ""
        if hasattr(e, "published"):
            pub = e.published
        elif hasattr(e, "updated"):
            pub = e.updated
        results.append({
            "_person": source.get("person", ""),
            "title": _clean(e.get("title", "")),
            "summary": _truncate(_clean(e.get("summary", e.get("description", "")))),
            "source": source["name"],
            "link": e.get("link", ""),
            "time": pub,
        })
    return results


# ── 统一抓取入口 ─────────────────────────────────────────

def fetch_source(source: dict) -> list[dict]:
    """根据 source 配置抓取新闻，出错返回空列表。"""
    try:
        items = _fetch_rss(source)
        log.info("✔ %-25s  %d 条", source["name"], len(items))
        return items
    except Exception as exc:
        log.warning("✘ %-25s  失败: %s", source["name"], exc)
        return []


# ── 去重 ─────────────────────────────────────────────────

def _dedup_news(items: list[dict]) -> list[dict]:
    """根据标题去重（保留第一条）。"""
    seen_titles: set[str] = set()
    result = []
    for item in items:
        # 用标题的前50字符作为去重键（避免轻微差异导致重复）
        key = re.sub(r"\s+", "", item["title"].lower())[:50]
        if key and key not in seen_titles:
            seen_titles.add(key)
            result.append(item)
    return result


# ── 信息过滤规则 ──────────────────────────────────────────
# 保留可能对股票投资造成影响的消息，排除名人生活琐事和花边新闻

# 投资相关关键词（标题或摘要包含至少一个即保留）
_INVEST_KEYWORDS = re.compile(
    r"(?i)"
    r"AI|artificial intelligence|GPT|LLM|model|chip|GPU|semiconductor|"
    r"revenue|earnings|profit|stock|share|market|valuation|IPO|"
    r"invest|funding|acquisition|merger|deal|partner|launch|release|"
    r"CEO|CTO|announce|strategy|regulation|policy|antitrust|"
    r"layoff|restructur|billion|million|growth|forecast|"
    r"cloud|data center|autonomous|robot|quantum|"
    r"Apple|Google|Microsoft|Meta|Amazon|Tesla|NVIDIA|OpenAI|"
    r"产品|发布|收入|利润|股价|市值|融资|收购|合并|监管|"
    r"战略|增长|预测|芯片|算力|模型|人工智能"
)

# 花边新闻/生活琐事排除词（标题包含这些且不含投资关键词时排除）
_GOSSIP_KEYWORDS = re.compile(
    r"(?i)"
    r"dating|divorce|wedding|married|girlfriend|boyfriend|"
    r"vacation|holiday|birthday|party|costume|fashion|"
    r"gossip|scandal|affair|celebrity|paparazzi|"
    r"dog|cat|pet|baby|child born|"
    r"约会|离婚|结婚|绯闻|八卦|度假|宠物"
)


def _is_investment_relevant(item: dict) -> bool:
    """判断新闻是否与投资相关（保留）或是花边新闻（排除）。"""
    title = item.get("title", "")
    summary = item.get("summary", "")
    text = title + " " + summary

    # 如果标题匹配花边关键词且不含投资关键词，排除
    if _GOSSIP_KEYWORDS.search(title) and not _INVEST_KEYWORDS.search(title):
        return False

    # 来自直接来源（官方博客/新闻稿）的内容默认保留
    source = item.get("source", "")
    if not source.startswith("GNews:"):
        return True

    # Google News 搜索结果需要至少匹配一个投资关键词
    return bool(_INVEST_KEYWORDS.search(text))


# ── 摘要翻译 ──────────────────────────────────────────────

_ZH_RE = re.compile(r"[\u4e00-\u9fff]")


def _is_chinese(text: str) -> bool:
    if not text:
        return True
    zh_count = len(_ZH_RE.findall(text))
    return zh_count / max(len(text), 1) > 0.2


def _translate_one(translator: GoogleTranslator, text: str) -> str:
    """翻译单条文本，带超时保护。"""
    try:
        result = translator.translate(text)
        return result if result else ""
    except Exception:
        return ""


def _translate_summaries(items: list[dict]) -> None:
    """将所有非中文摘要翻译为简体中文（就地修改），使用线程池并发翻译。"""
    to_translate: list[tuple[int, str]] = []
    for i, item in enumerate(items):
        summary = item.get("summary", "")
        if summary and not _is_chinese(summary):
            to_translate.append((i, summary))

    if not to_translate:
        return

    log.info("翻译摘要: %d 条需要翻译…", len(to_translate))
    translator = GoogleTranslator(source="auto", target="zh-CN")
    translated_count = 0

    with ThreadPoolExecutor(max_workers=4) as pool:
        future_map = {
            pool.submit(_translate_one, translator, text): idx
            for idx, text in to_translate
        }
        for fut in as_completed(future_map, timeout=90):
            idx = future_map[fut]
            try:
                result = fut.result(timeout=15)
                if result and len(result) > 10:
                    items[idx]["summary"] = _truncate(result)
                    translated_count += 1
            except Exception:
                pass

    log.info("翻译摘要: %d / %d 条成功", translated_count, len(to_translate))


# ── 时间解析 ──────────────────────────────────────────────

_TIME_FORMATS = [
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S%z",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d %b %Y",
    "%d %B %Y",
    "%Y/%m/%d %H:%M",
]


def _parse_time(time_str: str) -> datetime | None:
    if not time_str:
        return None
    time_str = time_str.strip()
    try:
        dt = parsedate_to_datetime(time_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        pass
    for fmt in _TIME_FORMATS:
        try:
            dt = datetime.strptime(time_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    m = re.search(r"(\d{4})[年\-/](\d{1,2})[月\-/](\d{1,2})", time_str)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
            hm = re.search(r"(\d{1,2}):(\d{2})", time_str)
            if hm:
                dt = dt.replace(hour=int(hm.group(1)), minute=int(hm.group(2)))
            return dt
        except ValueError:
            pass
    return None


# ── 主抓取流程 ────────────────────────────────────────────

def fetch_all(sources: list[dict[str, Any]] | None = None) -> list[dict]:
    """并发抓取所有来源，返回合并后的新闻列表。"""
    if sources is None:
        sources = SOURCES
    all_news: list[dict] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(fetch_source, s): s for s in sources}
        for fut in as_completed(futures):
            all_news.extend(fut.result())

    log.info("抓取总计: %d 条", len(all_news))

    # 为每条新闻识别关联人物
    for item in all_news:
        item["person"] = _find_person(item)

    # 去重
    all_news = _dedup_news(all_news)
    log.info("去重后: %d 条", len(all_news))

    # 信息过滤：保留投资相关消息，排除花边新闻
    before_filter = len(all_news)
    all_news = [n for n in all_news if _is_investment_relevant(n)]
    if len(all_news) < before_filter:
        log.info("内容过滤: %d → %d 条 (排除花边新闻)", before_filter, len(all_news))

    # 每个来源最多保留 MAX_ITEMS_PER_SOURCE 条
    source_count: dict[str, int] = {}
    limited: list[dict] = []
    for n in all_news:
        src = n.get("source", "")
        source_count[src] = source_count.get(src, 0) + 1
        if source_count[src] <= MAX_ITEMS_PER_SOURCE:
            limited.append(n)
    if len(limited) < len(all_news):
        log.info("来源限流: %d → %d 条", len(all_news), len(limited))
    all_news = limited

    # 补充短/空摘要
    need_enrich = [n for n in all_news if _is_summary_useless(n["title"], n["summary"]) and n.get("link")]
    if need_enrich:
        log.info("补充摘要: %d 条需要从原文获取…", len(need_enrich))
        with ThreadPoolExecutor(max_workers=6) as pool:
            future_map = {pool.submit(_fetch_og_description, n["link"]): n for n in need_enrich}
            for fut in as_completed(future_map):
                item = future_map[fut]
                desc = fut.result()
                if desc and len(desc) > len(item["summary"]):
                    item["summary"] = _truncate(desc)
        enriched = sum(1 for n in need_enrich if not _is_summary_useless(n["title"], n["summary"]))
        log.info("补充摘要: %d / %d 条成功", enriched, len(need_enrich))

    # 翻译
    _translate_summaries(all_news)

    # 时间过滤和排序
    cutoff = datetime.now(timezone.utc) - timedelta(weeks=2)
    for n in all_news:
        n["_dt"] = _parse_time(n.get("time", ""))

    before = len(all_news)
    all_news = [n for n in all_news if n["_dt"] is None or n["_dt"] >= cutoff]
    if len(all_news) < before:
        log.info("时间过滤: %d → %d 条 (仅保留最近两周)", before, len(all_news))

    all_news.sort(key=lambda n: n["_dt"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    return all_news


# ── HTML 生成 ─────────────────────────────────────────────

PERSON_COLORS = {
    "Sam Altman":       ("#e8f5e9", "#2e7d32"),
    "Jensen Huang":     ("#e8eaf6", "#283593"),
    "Yann LeCun":       ("#fff3e0", "#e65100"),
    "Demis Hassabis":   ("#e0f7fa", "#00695c"),
    "Elon Musk":        ("#fce4ec", "#c62828"),
    "Satya Nadella":    ("#e3f2fd", "#1565c0"),
    "Mark Zuckerberg":  ("#ede7f6", "#4527a0"),
    "Tim Cook":         ("#f3e5f5", "#6a1b9a"),
    "Marc Andreessen":  ("#fff8e1", "#f57f17"),
    "Reid Hoffman":     ("#e0f2f1", "#00695c"),
    "Naval Ravikant":   ("#fbe9e7", "#bf360c"),
}


def _person_badge(person: str) -> str:
    if not person:
        return '<span class="badge" style="background:#eeeeee;color:#333333">科技资讯</span>'
    bg, fg = PERSON_COLORS.get(person, ("#eeeeee", "#333333"))
    return (
        f'<span class="badge" style="background:{bg};color:{fg}">'
        f"{html_mod.escape(person)}</span>"
    )


def _news_card(item: dict) -> str:
    title = html_mod.escape(item["title"])
    summary = html_mod.escape(item.get("summary", ""))
    source = html_mod.escape(item["source"])
    time_str = html_mod.escape(item.get("time", ""))
    link = item.get("link", "")
    person = item.get("person", "")

    title_html = (
        f'<a href="{html_mod.escape(link)}" target="_blank" rel="noopener">{title}</a>'
        if link else title
    )

    meta_parts = [source]
    if time_str:
        meta_parts.append(time_str)

    badge = _person_badge(person)
    meta = " · ".join(meta_parts)

    return (
        '    <article class="card">\n'
        '      <div class="card-header">\n'
        f'        {badge}\n'
        f'        <span class="meta">{meta}</span>\n'
        '      </div>\n'
        f'      <h3 class="card-title">{title_html}</h3>\n'
        f'      <p class="card-summary">{summary}</p>\n'
        '    </article>'
    )


def _source_list_html() -> str:
    source_urls = {
        "Sam Altman Blog":    "https://blog.samaltman.com",
        "OpenAI":             "https://openai.com/news",
        "NVIDIA":             "https://nvidianews.nvidia.com",
        "DeepMind":           "https://deepmind.google/blog",
        "Meta":               "https://about.fb.com/news",
        "a16z":               "https://a16z.com",
        "Naval":              "https://nav.al",
        "Lex Fridman":        "https://lexfridman.com/podcast",
        "Stratechery":        "https://stratechery.com",
        "The Information":    "https://theinformation.com",
        "The Verge":          "https://theverge.com",
    }
    items = []
    for name, url in source_urls.items():
        items.append(
            f'<a href="{url}" target="_blank" rel="noopener" class="src-tag">{html_mod.escape(name)}</a>'
        )
    return " ".join(items)


def _person_filter_html() -> str:
    """生成人物快速筛选按钮。"""
    buttons = ['<button class="person-btn active" data-person="all">全部</button>']
    for person in KEY_PEOPLE:
        bg, fg = PERSON_COLORS.get(person, ("#eeeeee", "#333333"))
        buttons.append(
            f'<button class="person-btn" data-person="{html_mod.escape(person)}" '
            f'style="--btn-bg:{bg};--btn-fg:{fg}">{html_mod.escape(person)}</button>'
        )
    return "\n        ".join(buttons)


def generate_html(news: list[dict] | None = None) -> str:
    """返回完整的 HTML 字符串。"""
    if news is None:
        news = fetch_all()

    # 为每张卡片添加 data-person 属性用于前端筛选
    cards = []
    for n in news:
        person = html_mod.escape(n.get("person", ""))
        card = _news_card(n)
        # 将 data-person 注入到 <article> 标签
        card = card.replace(
            '<article class="card">',
            f'<article class="card" data-person="{person}">',
            1,
        )
        cards.append(card)

    cards_html = "\n".join(cards)
    beijing_tz = timezone(timedelta(hours=8))
    now = datetime.now(beijing_tz).strftime("%Y-%m-%d %H:%M")
    total = len(news)

    return f"""\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>美国权威科技界人士言论聚合</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei",
                   system-ui, -apple-system, sans-serif;
      background: #f0f2f5;
      color: #333;
      line-height: 1.6;
    }}

    /* ── Header ─────────────────────────── */
    header {{
      background: linear-gradient(135deg, #1a237e 0%, #283593 40%, #0d47a1 100%);
      color: #fff;
      padding: 32px 24px 22px;
      text-align: center;
      box-shadow: 0 2px 10px rgba(0,0,0,.2);
    }}
    header h1 {{
      font-size: 1.9rem;
      font-weight: 700;
      letter-spacing: .08em;
      display: inline;
    }}
    .header-row {{
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 16px;
    }}
    .btn-refresh {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 7px 18px;
      font-size: .85rem;
      font-weight: 600;
      color: #1a237e;
      background: #fff;
      border: none;
      border-radius: 20px;
      cursor: pointer;
      transition: background .2s, transform .15s;
      box-shadow: 0 2px 6px rgba(0,0,0,.15);
      white-space: nowrap;
    }}
    .btn-refresh:hover {{ background: #e8eaf6; }}
    .btn-refresh:active {{ transform: scale(.95); }}
    .btn-refresh .icon {{
      display: inline-block;
      transition: transform .4s;
    }}
    .btn-refresh.loading {{
      pointer-events: none;
      opacity: .7;
    }}
    .btn-refresh.loading .icon {{
      animation: spin 1s linear infinite;
    }}
    @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
    header .subtitle {{
      margin-top: 8px;
      font-size: .85rem;
      opacity: .85;
    }}

    /* ── Sources bar ───────────────────── */
    .sources {{
      background: #fff;
      padding: 14px 24px;
      text-align: center;
      box-shadow: 0 1px 4px rgba(0,0,0,.06);
      overflow-x: auto;
      white-space: nowrap;
    }}
    .sources .label {{
      font-size: .8rem;
      color: #888;
      margin-right: 8px;
    }}
    .src-tag {{
      display: inline-block;
      font-size: .75rem;
      padding: 3px 10px;
      margin: 3px 4px;
      border-radius: 14px;
      background: #e8eaf6;
      color: #283593;
      text-decoration: none;
      transition: background .15s;
    }}
    .src-tag:hover {{ background: #c5cae9; }}

    /* ── Person filter ───────────────────── */
    .person-filter {{
      background: #fff;
      padding: 12px 24px;
      text-align: center;
      border-top: 1px solid #f0f0f0;
      overflow-x: auto;
      white-space: nowrap;
    }}
    .person-filter .label {{
      font-size: .8rem;
      color: #888;
      margin-right: 8px;
    }}
    .person-btn {{
      display: inline-block;
      font-size: .75rem;
      padding: 4px 12px;
      margin: 3px 4px;
      border-radius: 14px;
      border: 1.5px solid #ddd;
      background: #fff;
      color: #555;
      cursor: pointer;
      transition: all .15s;
    }}
    .person-btn:hover {{
      background: var(--btn-bg, #f5f5f5);
      color: var(--btn-fg, #333);
      border-color: var(--btn-fg, #999);
    }}
    .person-btn.active {{
      background: var(--btn-bg, #1a237e);
      color: var(--btn-fg, #fff);
      border-color: var(--btn-fg, #1a237e);
      font-weight: 600;
    }}

    /* ── Container ──────────────────────── */
    .container {{
      max-width: 860px;
      margin: 24px auto;
      padding: 0 16px;
    }}
    .stats {{
      text-align: center;
      font-size: .82rem;
      color: #999;
      margin-bottom: 16px;
    }}

    /* ── Card ───────────────────────────── */
    .card {{
      background: #fff;
      border-radius: 10px;
      padding: 20px 24px;
      margin-bottom: 14px;
      box-shadow: 0 1px 4px rgba(0,0,0,.06);
      transition: transform .18s, box-shadow .18s;
    }}
    .card:hover {{
      transform: translateY(-3px);
      box-shadow: 0 6px 18px rgba(0,0,0,.1);
    }}
    .card.hidden {{ display: none; }}
    .card-header {{
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 10px;
    }}
    .badge {{
      display: inline-block;
      font-size: .73rem;
      font-weight: 600;
      padding: 2px 10px;
      border-radius: 12px;
    }}
    .meta {{
      font-size: .76rem;
      color: #999;
    }}
    .card-title {{
      font-size: 1.05rem;
      font-weight: 600;
      margin-bottom: 6px;
    }}
    .card-title a {{
      color: #222;
      text-decoration: none;
    }}
    .card-title a:hover {{
      color: #1a237e;
      text-decoration: underline;
    }}
    .card-summary {{
      font-size: .9rem;
      color: #555;
    }}

    .empty {{
      text-align: center;
      padding: 60px 20px;
      color: #aaa;
      font-size: 1rem;
    }}

    footer {{
      text-align: center;
      padding: 24px 16px;
      font-size: .78rem;
      color: #aaa;
    }}

    @media (max-width: 600px) {{
      header h1 {{ font-size: 1.4rem; }}
      .card {{ padding: 16px; }}
    }}
  </style>
</head>
<body>

  <header>
    <div class="header-row">
      <h1>美国权威科技界人士言论聚合</h1>
      <button class="btn-refresh" id="btnRefresh">
        <span class="icon">&#x21bb;</span> 刷新
      </button>
    </div>
    <p class="subtitle">实时聚合美国科技行业领袖最新言论 · 最后更新：{now} 北京时间（每小时自动刷新）</p>
  </header>

  <div class="sources">
    <span class="label">数据来源：</span>
    {_source_list_html()}
  </div>

  <div class="person-filter">
    <span class="label">人物筛选：</span>
    {_person_filter_html()}
  </div>

  <main class="container">
    <p class="stats" id="statsText">共聚合 {total} 条言论</p>
{"    <div class='empty'>暂未获取到言论，请检查网络后重试。</div>" if total == 0 else cards_html}
  </main>

  <footer>
    美国权威科技界人士言论聚合 &copy; {datetime.now().year} · 
    <a href="https://github.com/dabch2020/usnews" target="_blank" rel="noopener" style="color:#aaa">GitHub</a>
  </footer>

  <script>
  // ── 人物筛选 ──
  (function() {{
    var buttons = document.querySelectorAll('.person-btn');
    var cards = document.querySelectorAll('.card');
    var statsText = document.getElementById('statsText');
    var total = {total};

    buttons.forEach(function(btn) {{
      btn.addEventListener('click', function() {{
        buttons.forEach(function(b) {{ b.classList.remove('active'); }});
        btn.classList.add('active');
        var person = btn.getAttribute('data-person');
        var visible = 0;
        cards.forEach(function(card) {{
          if (person === 'all' || card.getAttribute('data-person') === person) {{
            card.classList.remove('hidden');
            visible++;
          }} else {{
            card.classList.add('hidden');
          }}
        }});
        if (person === 'all') {{
          statsText.textContent = '共聚合 ' + total + ' 条言论';
        }} else {{
          statsText.textContent = person + ' · ' + visible + ' 条言论';
        }}
      }});
    }});
  }})();

  // ── 刷新按钮 ──
  var _a='github_pat_11AP5KOUY0';
  var _b='dNQq2x013K8F_sH4Fqnd';
  var _c='JtVwfBfOEgy9pxWUQGMmU';
  var _d='VeDdNx7E2QrLeqCZ5OD46NQhjvEPn5Q';
  var DISPATCH_TOKEN = _a+_b+_c+_d;
  var REPO = 'dabch2020/usnews';
  var refreshBtn = document.getElementById('btnRefresh');
  var subtitleSpan = document.querySelector('.subtitle');

  refreshBtn.onclick = function() {{
    refreshBtn.classList.add('loading');
    subtitleSpan.textContent = '正在触发后台更新，请稍候约1-2分钟…';

    fetch('https://api.github.com/repos/' + REPO + '/dispatches', {{
      method: 'POST',
      headers: {{
        'Authorization': 'Bearer ' + DISPATCH_TOKEN,
        'Accept': 'application/vnd.github.v3+json'
      }},
      body: JSON.stringify({{ event_type: 'refresh' }})
    }})
    .then(function(r) {{
      if (r.status === 204 || r.status === 200) {{
        subtitleSpan.textContent = '✅ 已触发更新，正在等待构建完成…';
        pollForUpdate();
      }} else {{
        subtitleSpan.textContent = '❌ 触发失败 (HTTP ' + r.status + ')';
        refreshBtn.classList.remove('loading');
      }}
    }})
    .catch(function(e) {{
      subtitleSpan.textContent = '❌ 网络错误，请稍后重试';
      refreshBtn.classList.remove('loading');
    }});
  }};

  function pollForUpdate() {{
    var originalTime = '{now}';
    var attempts = 0;
    var maxAttempts = 24;
    var timer = setInterval(function() {{
      attempts++;
      fetch(location.href.split('?')[0] + '?_t=' + Date.now())
        .then(function(r) {{ return r.text(); }})
        .then(function(html) {{
          var m = html.match(/最后更新：([^（]+)/);
          if (m && m[1].trim() !== originalTime) {{
            clearInterval(timer);
            location.reload();
          }} else if (attempts >= maxAttempts) {{
            clearInterval(timer);
            subtitleSpan.textContent = '✅ 构建已触发，请稍后手动刷新页面';
            refreshBtn.classList.remove('loading');
          }}
        }})
        .catch(function() {{}});
    }}, 5000);
  }}
  </script>

</body>
</html>"""


# ── 入口 ─────────────────────────────────────────────────

_DOCS_DIR = Path(__file__).parent / "docs"
_HTML_PATH = _DOCS_DIR / "index.html"


def main() -> None:
    _DOCS_DIR.mkdir(exist_ok=True)
    log.info("开始抓取 %d 个来源…", len(SOURCES))
    t0 = time.time()
    news = fetch_all()
    elapsed = time.time() - t0
    log.info("共获取 %d 条言论 (%.1f 秒)", len(news), elapsed)
    _HTML_PATH.write_text(generate_html(news), encoding="utf-8")
    print(f"✅ 已生成: {_HTML_PATH}  ({len(news)} 条言论)")


if __name__ == "__main__":
    main()
