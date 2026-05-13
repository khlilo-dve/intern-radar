"""RSSHub 数据源适配器 — 通过 RSS 统一接入 Boss/拉钩/牛客等。"""
from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import quote

import feedparser
import requests

from crawlers.base import CrawlerAdapter, RawJob

log = logging.getLogger(__name__)

# Boss 直聘城市编码
_BOSS_CITY_MAP = {
    "北京": "101010100", "上海": "101020100", "广州": "101280100",
    "深圳": "101280600", "杭州": "101210100", "成都": "101270100",
    "南京": "101190100", "武汉": "101200100", "西安": "101110100",
    "苏州": "101190400", "长沙": "101250100", "厦门": "101230200",
    "天津": "101030100", "重庆": "101040100", "郑州": "101180100",
    "东莞": "101281600", "珠海": "101280700", "合肥": "101220100",
    "青岛": "101120200", "济南": "101120100", "福州": "101230100",
}


class RSSHubCrawler(CrawlerAdapter):
    """通过 RSSHub 获取招聘信息。"""

    name = "rsshub"

    def __init__(self, base_url: str = "https://rsshub.app"):
        self.base_url = base_url.rstrip("/")

    def crawl(self, keyword: str, city: str = "", **kwargs) -> list[RawJob]:
        platform = kwargs.get("platform", "boss")
        feeds = kwargs.get("feeds") or [{"keyword": keyword, "city": city}]

        all_jobs: list[RawJob] = []
        for feed_cfg in feeds:
            kw = feed_cfg.get("keyword", keyword)
            ct = feed_cfg.get("city", city)
            try:
                jobs = self._fetch_feed(platform, kw, ct)
                all_jobs.extend(jobs)
            except Exception as e:
                log.warning("RSSHub 抓取失败 platform=%s kw=%s city=%s: %s", platform, kw, ct, e)

        return all_jobs

    def _fetch_feed(self, platform: str, keyword: str, city: str) -> list[RawJob]:
        url = self._build_feed_url(platform, keyword, city)
        log.info("拉取 RSS: %s", url)

        resp = requests.get(url, timeout=30, headers={"User-Agent": "intern-radar/2.0"})
        resp.raise_for_status()

        feed = feedparser.parse(resp.text)
        if feed.bozo and not feed.entries:
            log.warning("RSS 解析异常: %s", feed.bozo_exception)
            return []

        jobs = []
        for entry in feed.entries:
            job = RawJob(
                title=entry.get("title", "").strip(),
                company=self._extract_company(entry),
                url=entry.get("link", ""),
                source=platform,
                city=city,
                salary=entry.get("salary", ""),
                tags=self._extract_tags(entry),
                jd_text=entry.get("summary", ""),
                published=entry.get("published", ""),
            )
            if job.title and job.url:
                jobs.append(job)

        log.info("platform=%s city=%s keyword=%s → %d 条", platform, city, keyword, len(jobs))
        return jobs

    def _build_feed_url(self, platform: str, keyword: str, city: str) -> str:
        if platform == "boss":
            city_code = _BOSS_CITY_MAP.get(city, "101010100")
            return f"{self.base_url}/zhipin/{city_code}/{quote(keyword)}"
        elif platform == "lagou":
            return f"{self.base_url}/lagou/{quote(city)}/{quote(keyword)}"
        elif platform == "nowcoder":
            return f"{self.base_url}/nowcoder/discuss/5"
        else:
            raise ValueError(f"不支持的平台: {platform}")

    def _extract_company(self, entry) -> str:
        """从 RSS entry 中提取公司名。"""
        # RSSHub 的 Boss 直聘 RSS 通常把公司名放在 author 或 description 中
        author = entry.get("author", "")
        if author:
            return author
        # fallback: 从 title 中提取（格式通常是 "职位名 - 公司名"）
        title = entry.get("title", "")
        if " - " in title:
            return title.rsplit(" - ", 1)[-1].strip()
        if "｜" in title:
            return title.rsplit("｜", 1)[-1].strip()
        return ""

    def _extract_tags(self, entry) -> list[str]:
        """从 RSS entry 中提取标签。"""
        tags = []
        for tag in entry.get("tags", []):
            term = tag.get("term", "")
            if term:
                tags.append(term)
        return tags[:5]
