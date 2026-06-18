"""LLM 调用：走 OpenAI 兼容协议，代理的 base_url/key 从 env 注入。"""
from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import re
import time
from pathlib import Path
from typing import Optional

from openai import OpenAI
from pydantic import ValidationError

from models import IntelRecord
from utils import retry, RetryExhausted

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是"实习情报分析师"，为一名特定候选人扫描招聘信息。

候选人基准（对齐靶子）：
{baseline}

任务：从用户提供的原材料（网页正文 或 招聘截图）中提取结构化情报，并按多维度评分体系打分。

严格规则：
1. 只输出一个纯粹的 JSON 对象。你的第一个输出字符必须是 {{，最后一个输出字符必须是 }}。禁止使用 Markdown 代码块包裹（即禁止出现 ```json），禁止任何前置或后置解释文字。
2. 字段与类型必须严格遵循：
   - Company: string — 公司名称
   - Job_Title: string — 职位名称（从内容中识别，如"AI产品实习生"）
   - City: string — 工作城市（如"北京"，不确定填空字符串）
   - Business_Line: string — 所属业务线/产品线（不确定填空字符串）
   - Hard_Tags: string[]（最多 5 个）— 核心硬性技能词汇
   - Red_Flags: string[]（最多 3 个）— 警报词汇。触发条件包括：(1) 纯线性劳动、极度内卷、无产品话语权的暗语；(2) JD 明确要求"熟练独立产出标准 MRD/商业分析报告"且作为首要 KPI。偏好"敏捷迭代"、"快速构建业务原型"等关键词。没有则为空数组。
   - Tech_Vision: integer 0-100 — 技术视野要求。评分标准：80+ 深刻理解大模型边界、上下文窗口限制及 Agentic Workflow 协同流；60-79 需要技术方案选型能力；40-59 仅需了解技术概念；<40 纯业务岗无技术要求
   - Product_Dominance: integer 0-100 — 产品业务主导权。评分标准：80+ 能独立主导核心业务模块的敏捷验证闭环，在产品迭代中享有高权重建议权；60-79 参与产品规划有话语权；40-59 执行层但有建议权；<40 纯执行无产品参与
   - Leverage_Ratio: integer 0-100 — 高杠杆投入率（越高越好）。评分标准：80+ 以策略/架构/决策为主；60-79 创作与执行各半；40-59 执行为主偶有规划；<30 纯重复性劳动
   - AI_Leverage: integer 0-100 — AI/自动化杠杆空间。评分标准：80+ 核心工作流可被 AI 显著加速，能用 AI 工具链解构复杂系统、搭建自动化工作流；60-79 部分环节可自动化；40-59 少量辅助场景；<40 纯人工密集型
   - Asset_Sedimentation: integer 0-100 — 资产沉淀度。评分标准：80+ 产出可完全沉淀为个人的方法论，或作为高含金量作品集公开展示；60-79 有部分可沉淀资产但需加工；40-59 产出难以复用；<40 纯消耗型工作
   - Match_Score: integer 1-100 — 综合匹配度。加权公式：Tech_Vision×0.15 + Product_Dominance×0.20 + Leverage_Ratio×0.20 + AI_Leverage×0.30 + Asset_Sedimentation×0.15。评分标尺：>85 必须包含高 AI 杠杆或产品主导权；<50 为需要避开的纯重复性劳动。若 Red_Flags 非空，分数不得超过 60。
   - Attack_Strategy: string — 差异化包装策略（详细版）。要求：
     (a) 用「岗位要求 → 候选人武器」的对照结构，逐条列出如何用候选人已有数字资产（如：工业级CLI工具开发经验、自动化Agent脚本搭建能力、AI原生交付力）降维打击该岗位的核心竞争力要求；
     (b) 给出具体的面试话术切入点（如："我用 Rust 重构了 XX 系统的热路径，延迟从 Xms 降到 Yms"这种颗粒度的表述方向）；
     (c) 指出候选人相对其他候选人的独特优势锚点。禁止笼统的"展示技术能力"类套话。
   - Critical_Gap: string — 核心短板分析（详细版）。要求：
     (a) 精准定位 JD 中明确要求但候选人完全空白的经历维度；
     (b) 说明该短板为何是致命的（如："该岗位 KPI 直接绑定 DAU 增长，但候选人从未做过 C 端留存优化，入职后第一个月就会暴露"）；
     (c) 给出最小成本补齐路径（如："用 2 周时间复刻一个 XX 类型的 side project，拿到真实数据后即可在面试中自证"）。禁止"提升沟通能力"类空泛建议。
3. Attack_Strategy 和 Critical_Gap 必须极度犀利务实，禁止出现"提升技能"、"多做项目"等无意义建议。
4. 如果原材料与招聘完全无关（日常聊天、无意义图片），输出：{{"__skip__": true, "reason": "<一句话说明>"}}。
"""


def make_client() -> OpenAI:
    base_url = os.environ.get("LLM_BASE_URL", "").rstrip("/")
    api_key = os.environ.get("LLM_API_KEY", "")
    if not base_url or not api_key:
        raise RuntimeError("LLM_BASE_URL / LLM_API_KEY 未配置，检查 .env")
    return OpenAI(base_url=base_url, api_key=api_key)


def vision_available() -> bool:
    return bool(os.environ.get("LLM_MODEL_VISION", "").strip())


_BOSS_CITY_CODES = {
    "北京": "101010100", "上海": "101020100", "广州": "101280100",
    "深圳": "101280600", "杭州": "101210100", "成都": "101270100",
    "南京": "101190100", "武汉": "101200100", "西安": "101110100",
    "苏州": "101190400", "长沙": "101250100", "厦门": "101230200",
    "天津": "101030100", "重庆": "101040100", "郑州": "101180100",
    "东莞": "101281600", "珠海": "101280700", "合肥": "101220100",
    "青岛": "101120200", "济南": "101120100", "福州": "101230100",
}


def build_search_url(company: str = "", job_title: str = "", city: str = "",
                     platform: str = "boss") -> str:
    """根据公司/职位/城市构造招聘平台搜索 URL。"""
    from urllib.parse import quote
    query = job_title or company
    if not query:
        return ""
    if platform == "boss":
        city_code = _BOSS_CITY_CODES.get(city, "101010100")
        return f"https://www.zhipin.com/web/geek/job?query={quote(query)}&city={city_code}"
    elif platform == "lagou":
        return f"https://www.lagou.com/wn/zhaopin?kd={quote(query)}&city={quote(city)}"
    return ""


def parse_text(client: OpenAI, baseline: str, raw_text: str, source_url: Optional[str] = None,
               model: Optional[str] = None) -> IntelRecord | dict:
    """文本模式：网页正文 → IntelRecord。返回 IntelRecord 或 {"__skip__":..} dict。"""
    model = model or os.environ.get("LLM_MODEL_TEXT")
    if not model:
        raise RuntimeError("LLM_MODEL_TEXT 未配置")

    system = SYSTEM_PROMPT.format(baseline=baseline.strip())
    user_prompt = "原材料（网页正文）：\n\n" + raw_text
    if source_url:
        user_prompt += f"\n\n来源 URL: {source_url}"

    return _chat_and_parse(client, model, system, [{"type": "text", "text": user_prompt}],
                           raw_input=raw_text[:500], source_url=source_url)


def parse_image(client: OpenAI, baseline: str, image_path: str | Path,
                model: Optional[str] = None) -> IntelRecord | dict:
    """图片模式：招聘截图 → 识别文字 → 文本分析 → IntelRecord。两步拆分，vision 只做 OCR。"""
    model = model or os.environ.get("LLM_MODEL_VISION")
    if not model:
        raise RuntimeError("vision 未启用：LLM_MODEL_VISION 为空")

    image_path = Path(image_path)
    data_url = _to_data_url(image_path)

    # Step 1: Vision 只提取文字，不做分析
    t0 = time.monotonic()
    resp = client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "请识别这张招聘截图中的所有文字，原样输出，不要分析、不要总结、不要省略。"},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }],
        temperature=0,
        max_tokens=2048,
    )
    extracted = (resp.choices[0].message.content or "").strip()
    t_vision = time.monotonic() - t0
    log.info("Vision OCR 完成: %.1fs, 提取 %d chars", t_vision, len(extracted))

    if not extracted:
        return {"__skip__": True, "reason": "图片中未识别到文字"}

    # Step 2: 用文本模型分析（复用 parse_text）
    return parse_text(client, baseline, extracted, source_url=None)


def self_check_vision(client: OpenAI) -> bool:
    """启动时跑一次廉价 vision 调用兜底 —— 失败就降级。"""
    model = os.environ.get("LLM_MODEL_VISION", "").strip()
    if not model:
        return False
    tiny_png_b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    try:
        client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "reply with OK"},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{tiny_png_b64}"}},
                ],
            }],
            max_tokens=10,
            temperature=0,
        )
        return True
    except Exception as e:
        log.warning("vision self-check failed: %s", e)
        return False


# ---------- internals ----------

@retry(max_retries=2, backoff_base=2.0, backoff_max=10.0)
def _chat_and_parse(client: OpenAI, model: str, system: str, user_content: list,
                    raw_input: Optional[str] = None,
                    source_url: Optional[str] = None) -> IntelRecord | dict:
    t0 = time.monotonic()
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
        temperature=0.2,
        max_tokens=4096,
    )
    elapsed = time.monotonic() - t0
    usage = resp.usage
    log.info(
        "LLM 调用完成: model=%s %.1fs in=%d out=%d total=%d",
        model, elapsed,
        usage.prompt_tokens if usage else 0,
        usage.completion_tokens if usage else 0,
        usage.total_tokens if usage else 0,
    )
    text = resp.choices[0].message.content or ""
    log.debug("LLM 原始响应: %s", text[:300])
    text = _strip_fences(text)
    data = json.loads(text)
    if isinstance(data, dict) and data.get("__skip__"):
        return data
    if isinstance(data, dict) and not str(data.get("Company") or "").strip():
        return {"__skip__": True, "reason": "LLM 未识别到公司名，大概率不是招聘信息"}
    if source_url:
        data.setdefault("Source_URL", source_url)
    if raw_input:
        data.setdefault("Raw_Input", raw_input)
    return IntelRecord(**data)


def _strip_fences(text: str) -> str:
    text = text.strip()
    # 去掉 ```json ... ``` 或 ``` ... ``` 围栏
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    # 提取最外层 { ... } JSON 对象
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]
    return text


def _to_data_url(path: Path) -> str:
    raw = path.read_bytes()
    log.debug("图片: path=%s, size=%d bytes", path, len(raw))
    mime = _sniff_image_mime(raw) or mimetypes.guess_type(path.name)[0] or "image/jpeg"
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _sniff_image_mime(data: bytes) -> Optional[str]:
    """按 magic bytes 识别真实 MIME，不信任文件扩展名。"""
    if len(data) < 12:
        return None
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None
