"""配置管理：pydantic 校验，替换裸 dict。"""
from __future__ import annotations

from typing import Optional

import yaml
from pydantic import BaseModel, Field


class BitableConfig(BaseModel):
    app_token: str = ""
    table_id: str = ""
    base_name: str = "实习情报雷达"
    table_name: str = "Intel"


class EventConfig(BaseModel):
    types: str = "im.message.receive_v1"
    as_identity: str = Field(default="bot", alias="as")

    model_config = {"populate_by_name": True}


class AppConfig(BaseModel):
    bitable: BitableConfig = Field(default_factory=BitableConfig)
    event: EventConfig = Field(default_factory=EventConfig)
    record_write_as: str = "user"
    reply_as: str = "bot"
    baseline_path: str = "candidate_baseline.md"
    download_dir: str = "downloads"

    def is_bootstrapped(self) -> bool:
        return bool(self.bitable.app_token and self.bitable.table_id)


def load_config(path) -> AppConfig:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return AppConfig(**raw)


def save_config(cfg: AppConfig, path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg.model_dump(by_alias=True, exclude_defaults=True), f, allow_unicode=True, sort_keys=False)
