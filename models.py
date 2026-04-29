from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class IntelRecord(BaseModel):
    """AI 输出的强类型情报 —— 字段名必须与 Bitable 列名对齐。"""

    Company: str = Field(..., min_length=1, max_length=200)
    Business_Line: str = Field(default="", max_length=200)
    Hard_Tags: List[str] = Field(default_factory=list)
    Match_Score: int = Field(..., ge=0, le=100)
    Attack_Strategy: str = Field(..., max_length=80)
    Critical_Gap: str = Field(..., max_length=80)

    Source_URL: Optional[str] = None
    Raw_Input: Optional[str] = None

    @field_validator("Hard_Tags")
    @classmethod
    def _tags_cap(cls, v: List[str]) -> List[str]:
        cleaned = [t.strip() for t in v if isinstance(t, str) and t.strip()]
        return cleaned[:5]

    @field_validator("Attack_Strategy", "Critical_Gap")
    @classmethod
    def _trim_50ish(cls, v: str) -> str:
        v = v.strip()
        if len(v) > 60:
            v = v[:58] + "…"
        return v

    def to_bitable_fields(self) -> dict:
        """转换为 Bitable record-upsert 所需的 fields 字典。"""
        fields: dict = {
            "Company": self.Company,
            "Business_Line": self.Business_Line,
            "Hard_Tags": ", ".join(self.Hard_Tags),
            "Match_Score": self.Match_Score,
            "Attack_Strategy": self.Attack_Strategy,
            "Critical_Gap": self.Critical_Gap,
            "Created_At": int(datetime.now(tz=timezone.utc).timestamp() * 1000),
        }
        if self.Source_URL:
            fields["Source_URL"] = self.Source_URL
        if self.Raw_Input:
            fields["Raw_Input"] = self.Raw_Input
        return fields


BITABLE_FIELD_SPEC = [
    {"field_name": "Company", "type": "text"},
    {"field_name": "Business_Line", "type": "text"},
    {"field_name": "Hard_Tags", "type": "text"},
    {"field_name": "Match_Score", "type": "number"},
    {"field_name": "Attack_Strategy", "type": "text"},
    {"field_name": "Critical_Gap", "type": "text"},
    {"field_name": "Source_URL", "type": "text"},
    {"field_name": "Created_At", "type": "datetime"},
    {"field_name": "Raw_Input", "type": "text"},
]
