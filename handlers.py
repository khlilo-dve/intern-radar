"""消息分发：按 message_type 路由到 text / image handler，统一回执。"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import larkcli
import llm
import web
from models import IntelRecord
from utils import RetryExhausted

_IMG_KEY_RE = re.compile(r"img_v\d+_[A-Za-z0-9_\-]+")

log = logging.getLogger(__name__)


@dataclass
class Context:
    client: Any  # OpenAI client
    baseline: str
    base_token: str
    table_id: str
    download_dir: Path
    record_write_as: str
    reply_as: str
    vision_ok: bool


def handle_event(ctx: Context, event: dict) -> None:
    etype = event.get("type") or event.get("event_type")
    if etype != "im.message.receive_v1":
        return
    msg = event.get("message") or {}
    mtype = event.get("message_type") or msg.get("message_type")
    chat_id = event.get("chat_id") or msg.get("chat_id")
    message_id = event.get("message_id") or event.get("id") or msg.get("message_id")
    content = event.get("content")
    if content is None:
        content = msg.get("content")
    if mtype == "image" and not content:
        # 最后一招：把整个 event 交给下游，让递归抽 key 到底
        content = event

    log.info("event: type=%s message_id=%s chat_id=%s", mtype, message_id, chat_id)
    if mtype == "image":
        log.warning("image event full dump: %s", json.dumps(event, ensure_ascii=False)[:800])
        log.warning("image content type=%s value=%r", type(content).__name__, content)

    try:
        if mtype == "text":
            _handle_text(ctx, chat_id, message_id, content)
        elif mtype == "image":
            _handle_image(ctx, chat_id, message_id, content)
        elif mtype == "post":
            _handle_post(ctx, chat_id, message_id, content)
        else:
            _reply(ctx, chat_id, f"⏭️ 暂不支持的消息类型：{mtype}")
    except RetryExhausted as e:
        log.error("重试耗尽: %s", e)
        _reply(ctx, chat_id, f"⚠️ 服务暂时不可用（重试 {e.attempts} 次后失败），请稍后再试")
    except Exception as e:
        log.exception("handler error")
        _reply(ctx, chat_id, f"❌ 处理失败：{_short(e)}")


def _handle_text(ctx: Context, chat_id: str, message_id: str, content: Any) -> None:
    text = _as_text(content)
    if not text:
        _reply(ctx, chat_id, "⚠️ 空文本，忽略")
        return

    urls = web.extract_all_urls(text)
    if not urls:
        _reply(ctx, chat_id, "⏭️ 没看到 URL，这条不像招聘情报，跳过")
        return

    for url in urls:
        _reply(ctx, chat_id, f"🛰️ 已抓到链接，开始解析…\n{url}")
        try:
            body = web.fetch_readable(url)
        except Exception as e:
            _reply(ctx, chat_id, f"❌ 网页抓取失败：{_short(e)}")
            continue
        result = llm.parse_text(ctx.client, ctx.baseline, body, source_url=url)
        _finalize(ctx, chat_id, result, raw_fallback=text[:500])


def _handle_image(ctx: Context, chat_id: str, message_id: str, content: Any) -> None:
    if not ctx.vision_ok:
        _reply(ctx, chat_id, "🖼️ 图片已收到，但 vision 模型未启用/代理不支持多模态 —— 先把招聘文字粘成 URL 或纯文本发我。")
        return

    file_key = _extract_image_key(content)
    if not file_key:
        _reply(ctx, chat_id, "❌ 图片消息中未找到 image_key，放弃")
        return

    ctx.download_dir.mkdir(parents=True, exist_ok=True)
    safe_name = f"{int(time.time())}_{file_key[-16:]}.img"
    local_path = ctx.download_dir / safe_name
    try:
        larkcli.download_resource(message_id, file_key, "image", local_path, as_identity="bot")
    except Exception as e:
        _reply(ctx, chat_id, f"❌ 图片下载失败：{_short(e)}")
        return

    _reply(ctx, chat_id, "🛰️ 图片已下载，进入视觉解析…")
    result = llm.parse_image(ctx.client, ctx.baseline, local_path)
    _finalize(ctx, chat_id, result, raw_fallback=f"[image] {safe_name}")


def _handle_post(ctx: Context, chat_id: str, message_id: str, content: Any) -> None:
    """处理富文本消息（post）：可同时包含文字 URL 和图片。"""
    # post content 格式: {"title": "...", "content": [[{tag, ...}, ...], ...]}
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            pass

    if not isinstance(content, dict):
        _reply(ctx, chat_id, "⚠️ 富文本解析失败")
        return

    text_parts = []
    image_keys = []
    for paragraph in content.get("content", []):
        if not isinstance(paragraph, list):
            continue
        for elem in paragraph:
            if not isinstance(elem, dict):
                continue
            tag = elem.get("tag", "")
            if tag == "text":
                text_parts.append(elem.get("text", ""))
            elif tag == "a":
                text_parts.append(elem.get("href", ""))
            elif tag == "img":
                key = elem.get("image_key", "")
                if key:
                    image_keys.append(key)

    combined_text = " ".join(text_parts)

    # 处理 URL
    urls = web.extract_all_urls(combined_text)
    for url in urls:
        _reply(ctx, chat_id, f"🛰️ 已抓到链接，开始解析…\n{url}")
        try:
            body = web.fetch_readable(url)
        except Exception as e:
            _reply(ctx, chat_id, f"❌ 网页抓取失败：{_short(e)}")
            continue
        result = llm.parse_text(ctx.client, ctx.baseline, body, source_url=url)
        _finalize(ctx, chat_id, result, raw_fallback=combined_text[:500])

    # 处理图片
    if image_keys:
        if not ctx.vision_ok:
            _reply(ctx, chat_id, "🖼️ 富文本中含图片，但 vision 未启用")
        else:
            for key in image_keys:
                ctx.download_dir.mkdir(parents=True, exist_ok=True)
                safe_name = f"{int(time.time())}_{key[-16:]}.img"
                local_path = ctx.download_dir / safe_name
                try:
                    larkcli.download_resource(message_id, key, "image", local_path, as_identity="bot")
                except Exception as e:
                    _reply(ctx, chat_id, f"❌ 图片下载失败：{_short(e)}")
                    continue
                _reply(ctx, chat_id, "🛰️ 图片已下载，进入视觉解析…")
                result = llm.parse_image(ctx.client, ctx.baseline, local_path)
                _finalize(ctx, chat_id, result, raw_fallback=f"[image] {safe_name}")

    if not urls and not image_keys:
        _reply(ctx, chat_id, "⏭️ 富文本中未找到可解析的内容")


def _finalize(ctx: Context, chat_id: str, result, raw_fallback: str) -> None:
    if isinstance(result, dict) and result.get("__skip__"):
        _reply(ctx, chat_id, f"⏭️ 跳过：{result.get('reason') or '与招聘无关'}")
        return
    if not isinstance(result, IntelRecord):
        _reply(ctx, chat_id, "❌ LLM 输出不符合 schema，放弃")
        return

    if not result.Raw_Input:
        result.Raw_Input = raw_fallback

    if _is_duplicate(ctx, result):
        _reply(ctx, chat_id, f"⏭️ 该情报已入库（{result.Company}），跳过重复")
        return

    # 截图模式没有 Source_URL，自动补搜索链接
    if not result.Source_URL and (result.Job_Title or result.Company):
        result.Source_URL = llm.build_search_url(
            company=result.Company,
            job_title=result.Job_Title or "",
            city=result.City or "",
        )

    upsert_fields = result.to_bitable_fields()
    try:
        larkcli.record_upsert(
            ctx.base_token, ctx.table_id,
            fields=upsert_fields,
            as_identity=ctx.record_write_as,
        )
    except Exception as e:
        log.error("record_upsert failed. fields=%s", json.dumps(upsert_fields, ensure_ascii=False, default=str))
        log.error("record_upsert full error: %r", e)
        _reply(ctx, chat_id, f"❌ 入库失败：{_short(e)}")
        return

    def _bar(score: int, invert: bool = False) -> str:
        s = (100 - score) if invert else score
        filled = round(s / 10)
        return "█" * filled + "░" * (10 - filled)

    score_lines = "\n".join([
        f"**🔧 技术视野**  `{_bar(result.Tech_Vision)}` **{result.Tech_Vision}**",
        f"**🎯 产品主导**  `{_bar(result.Product_Dominance)}` **{result.Product_Dominance}**",
        f"**⚡ 高杠杆投入率**  `{_bar(result.Leverage_Ratio)}` **{result.Leverage_Ratio}**",
        f"**🤖 AI 杠杆**   `{_bar(result.AI_Leverage)}` **{result.AI_Leverage}**",
        f"**🚀 资产沉淀度** `{_bar(result.Asset_Sedimentation)}` **{result.Asset_Sedimentation}**",
    ])
    flags_line = ""
    if result.Red_Flags:
        flags_line = f"\n🚩 **警报**：{' | '.join(result.Red_Flags)}"

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"✅ {result.Company}"},
            "template": "green" if result.Match_Score >= 70 else ("yellow" if result.Match_Score >= 50 else "red"),
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": f"业务线：{result.Business_Line or '—'}"}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": score_lines}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**📊 综合匹配**  `{_bar(result.Match_Score)}` **{result.Match_Score}**{flags_line}"}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**打击点**\n{result.Attack_Strategy}"}},
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**短板**\n{result.Critical_Gap}"}},
        ],
    }

    try:
        larkcli.send_card(chat_id, card, as_identity=ctx.reply_as)
    except Exception as e:
        log.warning("卡片发送失败，降级纯文本: %s", e)
        fallback = (
            f"✅ 已入库 | {result.Company}\n"
            f"匹配度：{result.Match_Score}\n"
            f"打击点：{result.Attack_Strategy}\n"
            f"短板：{result.Critical_Gap}"
        )
        _reply(ctx, chat_id, fallback)


# ---------- utilities ----------

def _as_text(content: Any) -> str:
    """compact 模式下 text 是字符串；raw 模式下是 {"text": "..."} 的 JSON 字符串。两种都收。"""
    if content is None:
        return ""
    if isinstance(content, str):
        s = content.strip()
        if s.startswith("{"):
            try:
                obj = json.loads(s)
                if isinstance(obj, dict) and "text" in obj:
                    return str(obj.get("text") or "")
            except json.JSONDecodeError:
                pass
        return s
    if isinstance(content, dict) and "text" in content:
        return str(content.get("text") or "")
    return str(content)


def _extract_image_key(content: Any) -> Optional[str]:
    """从任意层级/任意键名/任意包装里抠出 image_key。终极武器：正则扫 img_vN_ 格式。"""
    if isinstance(content, str):
        s = content.strip()
        if s.startswith("{"):
            try:
                hit = _walk_for_image_key(json.loads(s))
                if hit:
                    return hit
            except json.JSONDecodeError:
                pass
        m = _IMG_KEY_RE.search(s)
        if m:
            return m.group(0)
    return _walk_for_image_key(content)


def _walk_for_image_key(node: Any) -> Optional[str]:
    if isinstance(node, dict):
        for k in ("image_key", "file_key", "imageKey", "fileKey", "key"):
            v = node.get(k)
            if isinstance(v, str) and v.startswith("img_"):
                return v
        for v in node.values():
            hit = _walk_for_image_key(v)
            if hit:
                return hit
    elif isinstance(node, list):
        for v in node:
            hit = _walk_for_image_key(v)
            if hit:
                return hit
    elif isinstance(node, str):
        m = _IMG_KEY_RE.search(node)
        if m:
            return m.group(0)
    return None


def _is_duplicate(ctx: Context, record: IntelRecord) -> bool:
    """检查是否已存在相同情报。URL 模式按 Source_URL 去重，图片模式按 Company 去重。"""
    try:
        items = larkcli.record_list(ctx.base_token, ctx.table_id, as_identity=ctx.record_write_as)
    except Exception as e:
        log.warning("去重查询失败（不阻塞入库）: %s", e)
        return False

    for item in items:
        fields = item.get("fields") or {}
        # URL 去重
        if record.Source_URL and fields.get("投递网址") == record.Source_URL:
            return True
        # 图片模式：同公司 + 同 Raw_Input
        if not record.Source_URL and record.Company == fields.get("公司"):
            existing_raw = str(fields.get("原始输入") or "")
            if record.Raw_Input and existing_raw and record.Raw_Input == existing_raw:
                return True
    return False


def _reply(ctx: Context, chat_id: Optional[str], text: str) -> None:
    if not chat_id:
        log.warning("no chat_id, dropping reply: %s", text)
        return
    try:
        larkcli.send_text(chat_id=chat_id, text=text, as_identity=ctx.reply_as)
    except Exception as e:
        log.error("reply failed: %s", e)


def notify_high_score(ctx: Context, chat_id: str, result: IntelRecord) -> None:
    """爬虫发现高分岗位时，发送飞书卡片通知。"""
    def _bar(score: int, invert: bool = False) -> str:
        s = (100 - score) if invert else score
        filled = round(s / 10)
        return "█" * filled + "░" * (10 - filled)

    score_lines = "\n".join([
        f"**🔧 技术视野**  `{_bar(result.Tech_Vision)}` **{result.Tech_Vision}**",
        f"**🎯 产品主导**  `{_bar(result.Product_Dominance)}` **{result.Product_Dominance}**",
        f"**⚡ 高杠杆投入率**  `{_bar(result.Leverage_Ratio)}` **{result.Leverage_Ratio}**",
        f"**🤖 AI 杠杆**   `{_bar(result.AI_Leverage)}` **{result.AI_Leverage}**",
        f"**🚀 资产沉淀度** `{_bar(result.Asset_Sedimentation)}` **{result.Asset_Sedimentation}**",
    ])
    flags_line = ""
    if result.Red_Flags:
        flags_line = f"\n🚩 **警报**：{' | '.join(result.Red_Flags)}"
    url_line = f"\n🔗 [投递/查看详情]({result.Source_URL})" if result.Source_URL else ""

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"🎯 新发现: {result.Company} — {result.Job_Title or ''}"},
            "template": "green" if result.Match_Score >= 70 else ("yellow" if result.Match_Score >= 50 else "red"),
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": f"业务线：{result.Business_Line or '—'}　城市：{result.City or '—'}"}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": score_lines}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**📊 综合匹配**  `{_bar(result.Match_Score)}` **{result.Match_Score}**{flags_line}{url_line}"}},
        ],
    }
    try:
        larkcli.send_card(chat_id, card, as_identity=ctx.reply_as)
    except Exception as e:
        log.warning("高分推送卡片发送失败: %s", e)
        fallback = f"🎯 {result.Company} — {result.Job_Title or ''}\n匹配度：{result.Match_Score}\n{result.Source_URL or ''}"
        _reply(ctx, chat_id, fallback)


def _short(e: Exception) -> str:
    s = str(e)
    return s if len(s) <= 200 else s[:198] + "…"
