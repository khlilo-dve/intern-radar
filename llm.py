"""LLM 调用：走 OpenAI 兼容协议，代理的 base_url/key 从 env 注入。"""
from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
from pathlib import Path
from typing import Optional

from openai import OpenAI
from pydantic import ValidationError

from models import IntelRecord

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是"实习情报分析师"，为一名特定候选人扫描招聘信息。

候选人基准（对齐靶子）：
{baseline}

任务：从用户提供的原材料（网页正文 或 招聘截图）中提取结构化情报。

严格规则：
1. 只输出一个 JSON 对象，**不要用代码块包裹**，不要任何解释文字。
2. 字段与类型必须严格遵循：
   - Company: string — 公司名称
   - Business_Line: string — 所属业务线/产品线（不确定就填空字符串）
   - Hard_Tags: string[]（最多 5 个）— 核心硬性技能词汇，短词
   - Match_Score: integer 1-100 — 这个岗位对候选人的匹配度
   - Attack_Strategy: string ≤ 50 字 — 基于候选人底色，如何差异化包装经历降维打击
   - Critical_Gap: string ≤ 50 字 — 尖锐指出候选人目前完全没有的经历，必须立刻补齐的核心短板
3. Attack_Strategy 和 Critical_Gap 必须具体、犀利，禁止套话如"提升技能""多做项目"。
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
    """图片模式：招聘截图 → IntelRecord。"""
    model = model or os.environ.get("LLM_MODEL_VISION")
    if not model:
        raise RuntimeError("vision 未启用：LLM_MODEL_VISION 为空")

    image_path = Path(image_path)
    data_url = _to_data_url(image_path)
    system = SYSTEM_PROMPT.format(baseline=baseline.strip())
    content = [
        {"type": "text", "text": "原材料是一张招聘相关的截图，请仔细识别图中文字后输出 JSON。"},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]
    return _chat_and_parse(client, model, system, content, raw_input=f"[image] {image_path.name}")


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

def _chat_and_parse(client: OpenAI, model: str, system: str, user_content: list,
                    raw_input: Optional[str] = None,
                    source_url: Optional[str] = None) -> IntelRecord | dict:
    last_err: Exception | None = None
    for attempt in (1, 2):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.2,
                max_tokens=1024,
            )
            text = resp.choices[0].message.content or ""
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
        except (json.JSONDecodeError, ValidationError) as e:
            last_err = e
            log.warning("attempt %s parse failed: %s", attempt, e)
            continue
        except Exception as e:
            last_err = e
            log.warning("attempt %s LLM call failed: %s", attempt, e)
            continue
    raise RuntimeError(f"LLM parse failed after 2 attempts: {last_err}")


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip("` \n")
    # 找第一个 { ... 最后一个 } 之间的内容
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]
    return text


def _to_data_url(path: Path) -> str:
    raw = path.read_bytes()
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
