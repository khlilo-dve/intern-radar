"""URL 抓取：trafilatura 主力 + requests 兜底。只负责把网页变成纯文本。"""
from __future__ import annotations

import logging
import re
from typing import Optional

import requests
import trafilatura

log = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://[^\s一-鿿，。！？、；：（）【】\"'<>]+", re.UNICODE)

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 intern-radar/0.1"
)


def extract_url(text: str) -> Optional[str]:
    """从一段文本里抠出第一个 URL，去掉尾部常见中文标点。"""
    m = URL_RE.search(text or "")
    if not m:
        return None
    return m.group(0).rstrip(".,;:!?)】》")


def fetch_readable(url: str, timeout: int = 20) -> str:
    """抓网页并抽正文。先试 trafilatura，抽不到再退化到 requests + trafilatura.extract。"""
    html = trafilatura.fetch_url(url, no_ssl=False)
    if html:
        body = trafilatura.extract(
            html,
            include_tables=False,
            include_comments=False,
            favor_precision=True,
        )
        if body and body.strip():
            return _clip(body)

    # Fallback: requests + trafilatura 解析
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": UA}, allow_redirects=True)
        r.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"fetch failed: {e}") from e

    body = trafilatura.extract(r.text, include_tables=False, include_comments=False) or ""
    body = body.strip()
    if not body:
        text = re.sub(r"<[^>]+>", " ", r.text)
        body = re.sub(r"\s+", " ", text).strip()
    if not body:
        raise RuntimeError("no extractable content")
    return _clip(body)


def _clip(text: str, limit: int = 8000) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "\n…(truncated)"
