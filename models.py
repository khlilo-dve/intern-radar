from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class IntelRecord(BaseModel):
    """AI 输出的强类型情报 —— 字段名必须与 Bitable 列名对齐。"""

    Company: str = Field(..., min_length=1, max_length=200)
    Business_Line: str = Field(default="", max_length=200)
    Hard_Tags: List[str] = Field(default_factory=list)
    Red_Flags: List[str] = Field(default_factory=list)

    # 多维度评分（0-100）
    Tech_Vision: int = Field(..., ge=0, le=100, description="技术视野要求")
    Product_Dominance: int = Field(..., ge=0, le=100, description="产品业务主导权")
    Exec_Ratio: int = Field(..., ge=0, le=100, description="日常执行杂活比例（越低越好）")
    AI_Leverage: int = Field(..., ge=0, le=100, description="AI/自动化杠杆空间")
    Growth_Ceiling: int = Field(..., ge=0, le=100, description="成长天花板")

    Match_Score: int = Field(..., ge=0, le=100)
    Attack_Strategy: str = Field(..., max_length=500)
    Critical_Gap: str = Field(..., max_length=500)

    Status: Optional[str] = None
    Notes: Optional[str] = None

    Source_URL: Optional[str] = None
    Raw_Input: Optional[str] = None

    @field_validator("Hard_Tags", "Red_Flags")
    @classmethod
    def _tags_cap(cls, v: List[str]) -> List[str]:
        cleaned = [t.strip() for t in v if isinstance(t, str) and t.strip()]
        return cleaned[:5]

    @field_validator("Attack_Strategy", "Critical_Gap")
    @classmethod
    def _trim_long(cls, v: str) -> str:
        v = v.strip()
        if len(v) > 500:
            v = v[:498] + "…"
        return v

    def to_bitable_fields(self) -> dict:
        """转换为 Bitable record-upsert 所需的 fields 字典。"""
        fields: dict = {
            "Company": self.Company,
            "Business_Line": self.Business_Line,
            "Hard_Tags": ", ".join(self.Hard_Tags),
            "Red_Flags": ", ".join(self.Red_Flags),
            "Tech_Vision": self.Tech_Vision,
            "Product_Dominance": self.Product_Dominance,
            "Exec_Ratio": self.Exec_Ratio,
            "AI_Leverage": self.AI_Leverage,
            "Growth_Ceiling": self.Growth_Ceiling,
            "Match_Score": self.Match_Score,
            "Attack_Strategy": self.Attack_Strategy,
            "Critical_Gap": self.Critical_Gap,
            "Created_At": int(datetime.now(tz=timezone.utc).timestamp() * 1000),
        }
        fields["Status"] = self.Status or "未投递"
        if self.Source_URL:
            fields["Source_URL"] = self.Source_URL
        if self.Raw_Input:
            fields["Raw_Input"] = self.Raw_Input
        return fields


BITABLE_FIELD_SPEC = [
    {"field_name": "Company", "type": "text"},
    {"field_name": "Business_Line", "type": "text"},
    {"field_name": "Hard_Tags", "type": "text"},
    {"field_name": "Red_Flags", "type": "text"},
    {"field_name": "Tech_Vision", "type": "number"},
    {"field_name": "Product_Dominance", "type": "number"},
    {"field_name": "Exec_Ratio", "type": "number"},
    {"field_name": "AI_Leverage", "type": "number"},
    {"field_name": "Growth_Ceiling", "type": "number"},
    {"field_name": "Match_Score", "type": "number"},
    {"field_name": "Attack_Strategy", "type": "text"},
    {"field_name": "Critical_Gap", "type": "text"},
    {
        "field_name": "Status",
        "type": 3,
        "property": {
            "options": [
                {"name": "未投递"},
                {"name": "简历未通过"},
                {"name": "简历通过"},
                {"name": "一面通过"},
                {"name": "二面通过"},
                {"name": "三面通过"},
            ]
        },
    },
    {"field_name": "Notes", "type": "text"},
    {"field_name": "Source_URL", "type": "text"},
    {"field_name": "Created_At", "type": "datetime"},
    {"field_name": "Raw_Input", "type": "text"},
]
