"""爬虫适配器抽象基类。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RawJob:
    """爬虫抓取的原始职位信息。"""
    title: str
    company: str
    url: str
    source: str  # "boss" / "lagou" / "nowcoder" / ...
    city: str = ""
    salary: str = ""
    tags: list[str] = field(default_factory=list)
    jd_text: str = ""  # 职位描述正文（可后续用 trafilatura 补抓）
    published: str = ""


class CrawlerAdapter(ABC):
    """所有数据源适配器的基类。"""

    name: str = "base"

    @abstractmethod
    def crawl(self, keyword: str, city: str, **kwargs) -> list[RawJob]:
        """抓取符合条件的职位列表。"""
        ...
