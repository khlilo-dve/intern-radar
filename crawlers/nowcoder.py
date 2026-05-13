"""牛客实习广场直爬 — 无需 RSSHub，直接抓取页面。"""
from __future__ import annotations

import logging
import re

import requests
from bs4 import BeautifulSoup

from crawlers.base import CrawlerAdapter, RawJob

log = logging.getLogger(__name__)

_NOWCODER_URL = "https://www.nowcoder.com/job/center/"

# 牛客职位类型代码
_TYPE_MAP = {
    "研发": "1", "测试": "2", "数据": "3", "算法": "4",
    "前端": "5", "产品": "6", "运营": "7",
}

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 intern-radar/2.0"
)


class NowcoderCrawler(CrawlerAdapter):
    """直接抓取牛客实习广场。"""

    name = "nowcoder"

    def crawl(self, keyword: str = "", city: str = "北京", **kwargs) -> list[RawJob]:
        job_type = kwargs.get("job_type", "产品")  # 默认产品岗
        latest_week = kwargs.get("latest_week", True)
        type_code = _TYPE_MAP.get(job_type, "6")

        params = {"recruitType": "1"}  # 1=实习广场
        if city:
            params["city"] = city
        if type_code:
            params["type"] = type_code
        params["order"] = "1"  # 最新发布
        if latest_week:
            params["latest"] = "true"

        log.info("牛客实习广场: city=%s type=%s", city, job_type)

        resp = None
        for attempt in range(3):
            try:
                resp = requests.get(
                    _NOWCODER_URL, params=params,
                    headers={"User-Agent": _UA},
                    timeout=30,
                )
                resp.raise_for_status()
                break
            except requests.RequestException as e:
                log.warning("牛客请求失败 (attempt %d/3): %s", attempt + 1, e)
                if attempt < 2:
                    import time
                    time.sleep(2)
        if resp is None:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select("div.job-card-item")
        log.info("牛客返回 %d 个职位卡片", len(cards))

        jobs = []
        for card in cards:
            job = self._parse_card(card, city)
            if job:
                jobs.append(job)

        # 按 keyword 过滤（如果指定）— 匹配标题或公司名
        if keyword:
            kw = keyword.lower()
            jobs = [j for j in jobs
                    if kw in j.title.lower()
                    or kw in j.company.lower()
                    or any(kw in t.lower() for t in j.tags)]

        log.info("牛客筛选后 %d 条", len(jobs))
        return jobs

    def _parse_card(self, card, default_city: str) -> RawJob | None:
        # 标题
        title_el = card.select_one("span.job-name")
        if not title_el:
            return None
        title = title_el.get_text(strip=True)

        # 链接
        link_el = card.select_one("a.job-message-boxs")
        url = link_el["href"] if link_el and link_el.get("href") else ""
        if url and not url.startswith("http"):
            url = "https://www.nowcoder.com" + url

        # 薪资
        salary_el = card.select_one("span.job-salary")
        salary = salary_el.get_text(strip=True) if salary_el else ""

        # 公司名（在 .company-right 中，取第一段文本）
        company = ""
        company_right = card.select_one(".company-right")
        if company_right:
            # 只取第一个 a 标签的文本（公司名链接）
            comp_link = company_right.select_one("a")
            if comp_link:
                company = comp_link.get_text(strip=True)
            else:
                full_text = company_right.get_text(strip=True)
                company = re.sub(r"(游戏|金融|教育|电商|医疗|互联网|企业服务|生活|社交|安全|大数据|AI|人工智能|企业|其他|零售|地产|消费|汽车|物流|广告|媒体|旅游|餐饮|健康|体育|游戏|音乐|影视|动漫|硬件|通信|半导体|云计算|物联网|区块链|AR|VR|机器人).*$", "", full_text)
                company = re.sub(r"\d+[-–]\d+人.*$", "", company).strip()

        # 城市
        city = default_city
        for item in card.select(".job-info-item"):
            text = item.get_text(strip=True)
            if any(c in text for c in ["北京", "上海", "广州", "深圳", "杭州", "成都", "南京", "武汉"]):
                city = text
                break

        return RawJob(
            title=title,
            company=company,
            url=url,
            source="nowcoder",
            city=city,
            salary=salary,
            tags=[],
            jd_text="",
        )
