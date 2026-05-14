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
    Leverage_Ratio: int = Field(..., ge=0, le=100, description="高杠杆投入率（越高越好）")
    AI_Leverage: int = Field(..., ge=0, le=100, description="AI/自动化杠杆空间")
    Asset_Sedimentation: int = Field(..., ge=0, le=100, description="资产沉淀度")

    Match_Score: int = Field(..., ge=0, le=100)
    Attack_Strategy: str = Field(..., max_length=2000)
    Critical_Gap: str = Field(..., max_length=2000)

    Status: Optional[str] = None
    Notes: Optional[str] = None

    Job_Title: Optional[str] = None
    City: Optional[str] = None
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
        if len(v) > 2000:
            v = v[:1998] + "…"
        return v

    def to_bitable_fields(self) -> dict:
        """转换为 Bitable record-upsert 所需的 fields 字典。"""
        fields: dict = {
            "公司": self.Company,
            "业务线": self.Business_Line,
            "硬标签": ", ".join(self.Hard_Tags),
            "红旗项": ", ".join(self.Red_Flags),
            "技术视野": self.Tech_Vision,
            "产品主导力": self.Product_Dominance,
            "高杠杆投入率": self.Leverage_Ratio,
            "AI杠杆": self.AI_Leverage,
            "资产沉淀度": self.Asset_Sedimentation,
            "综合匹配度": self.Match_Score,
            "打击策略": self.Attack_Strategy,
            "核心短板": self.Critical_Gap,
            "创建时间": int(datetime.now(tz=timezone.utc).timestamp() * 1000),
        }
        fields["投递状态"] = self.Status or "未投递"
        if self.Job_Title:
            fields["职位名称"] = self.Job_Title
        if self.City:
            fields["城市"] = self.City
        if self.Source_URL:
            fields["投递网址"] = self.Source_URL
        if self.Raw_Input:
            fields["原始输入"] = self.Raw_Input
        return fields


BITABLE_FIELD_SPEC = [
    {"field_name": "公司", "type": "text"},
    {"field_name": "业务线", "type": "text"},
    {
        "field_name": "投递状态",
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
    {"field_name": "硬标签", "type": "text"},
    {"field_name": "红旗项", "type": "text"},
    {"field_name": "技术视野", "type": "number"},
    {"field_name": "产品主导力", "type": "number"},
    {"field_name": "高杠杆投入率", "type": "number"},
    {"field_name": "AI杠杆", "type": "number"},
    {"field_name": "资产沉淀度", "type": "number"},
    {"field_name": "综合匹配度", "type": "number"},
    {"field_name": "打击策略", "type": "text"},
    {"field_name": "核心短板", "type": "text"},
    {"field_name": "备注", "type": "text"},
    {"field_name": "职位名称", "type": "text"},
    {"field_name": "城市", "type": "text"},
    {"field_name": "投递网址", "type": "text"},
    {"field_name": "创建时间", "type": "datetime"},
    {"field_name": "原始输入", "type": "text"},
]
