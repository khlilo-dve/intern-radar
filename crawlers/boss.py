"""Boss 直聘爬虫 — Playwright 渲染页面，绕过反爬。"""
from __future__ import annotations

import logging
import re
import time

from crawlers.base import CrawlerAdapter, RawJob

log = logging.getLogger(__name__)

_BOSS_CITY_CODES = {
    "北京": "101010100", "上海": "101020100", "广州": "101280100",
    "深圳": "101280600", "杭州": "101210100", "成都": "101270100",
    "南京": "101190100", "武汉": "101200100", "西安": "101110100",
    "苏州": "101190400", "长沙": "101250100", "厦门": "101230200",
}


class BossCrawler(CrawlerAdapter):
    """用 Playwright 抓取 Boss 直聘搜索结果。"""

    name = "boss"

    def crawl(self, keyword: str = "", city: str = "北京", **kwargs) -> list[RawJob]:
        city_code = _BOSS_CITY_CODES.get(city, "101010100")
        query = keyword or "AI产品实习"
        max_pages = kwargs.get("max_pages", 3)

        log.info("Boss 直聘: query=%s city=%s pages=%d", query, city, max_pages)

        all_jobs: list[RawJob] = []
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log.error("playwright 未安装: pip install playwright && playwright install chromium")
            return []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1920, "height": 1080},
                locale="zh-CN",
            )
            page = context.new_page()

            for pg in range(1, max_pages + 1):
                url = (
                    f"https://www.zhipin.com/web/geek/job"
                    f"?query={query}&city={city_code}&page={pg}"
                )
                log.info("打开: %s", url)

                try:
                    page.goto(url, wait_until="networkidle", timeout=30000)
                except Exception as e:
                    log.warning("页面加载超时: %s", e)
                    continue

                # 等待职位卡片出现
                try:
                    page.wait_for_selector(".job-card-wrapper", timeout=10000)
                except Exception:
                    log.warning("第 %d 页没有职位卡片，可能到末尾了", pg)
                    break

                # 滚动加载更多
                for _ in range(3):
                    page.evaluate("window.scrollBy(0, 800)")
                    time.sleep(0.5)

                # 提取职位数据
                cards = page.query_selector_all(".job-card-wrapper")
                log.info("第 %d 页: %d 个职位", pg, len(cards))

                for card in cards:
                    job = self._parse_card(card)
                    if job:
                        all_jobs.append(job)

                time.sleep(2)  # 页间间隔，避免被封

            browser.close()

        log.info("Boss 直聘共抓到 %d 条", len(all_jobs))
        return all_jobs

    def _parse_card(self, card) -> RawJob | None:
        try:
            # 职位名
            title_el = card.query_selector(".job-name")
            title = title_el.inner_text().strip() if title_el else ""
            if not title:
                return None

            # 职位链接
            link_el = card.query_selector("a.job-card-left")
            url = ""
            if link_el:
                href = link_el.get_attribute("href") or ""
                url = f"https://www.zhipin.com{href}" if href.startswith("/") else href

            # 公司名
            company_el = card.query_selector(".company-name a") or card.query_selector(".company-name")
            company = company_el.inner_text().strip() if company_el else ""

            # 薪资
            salary_el = card.query_selector(".salary")
            salary = salary_el.inner_text().strip() if salary_el else ""

            # 城市 + 经验 + 学历
            info_el = card.query_selector(".job-info .tag-list")
            city = ""
            if info_el:
                tags = info_el.query_selector_all("span")
                for tag in tags:
                    text = tag.inner_text().strip()
                    if any(c in text for c in ["北京", "上海", "广州", "深圳", "杭州"]):
                        city = text
                        break

            # 标签
            tags = []
            skills_el = card.query_selector(".job-info .tag-list")
            if skills_el:
                for span in skills_el.query_selector_all("span"):
                    t = span.inner_text().strip()
                    if t and t != city:
                        tags.append(t)

            return RawJob(
                title=title,
                company=company,
                url=url,
                source="boss",
                city=city,
                salary=salary,
                tags=tags[:5],
                jd_text="",
            )
        except Exception as e:
            log.debug("解析职位卡片失败: %s", e)
            return None
