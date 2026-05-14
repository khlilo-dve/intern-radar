"""主入口：启动前 bootstrap Bitable，然后守住 lark-cli event subscribe 的输出流。"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

import handlers
import larkcli
import llm
from config import AppConfig, load_config as _load_config, save_config as _save_config
from crawlers import BossCrawler, NowcoderCrawler, RSSHubCrawler
from models import BITABLE_FIELD_SPEC, IntelRecord

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"
ENV_PATH = ROOT / ".env"

log = logging.getLogger("intern-radar")


def setup_logging() -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def load_config() -> AppConfig:
    if not CONFIG_PATH.exists():
        sample = ROOT / "config.yaml.example"
        if sample.exists():
            CONFIG_PATH.write_text(sample.read_text(encoding="utf-8"), encoding="utf-8")
            log.info("config.yaml 不存在，已从 example 复制一份")
        else:
            raise FileNotFoundError("config.yaml 和 config.yaml.example 都找不到")
    return _load_config(CONFIG_PATH)


def save_config(cfg: AppConfig) -> None:
    _save_config(cfg, CONFIG_PATH)


def bootstrap_bitable(cfg: AppConfig) -> AppConfig:
    """确保 bitable 已建并返回带 app_token/table_id 的配置。"""
    write_as = cfg.record_write_as

    if cfg.is_bootstrapped():
        log.info("复用现有 bitable: %s / %s", cfg.bitable.app_token, cfg.bitable.table_id)
        _verify_fields(cfg.bitable.app_token, cfg.bitable.table_id, write_as)
        return cfg

    log.info("首次启动：创建 Bitable app=%s", cfg.bitable.base_name)
    base_resp = larkcli.base_create(name=cfg.bitable.base_name, as_identity=write_as)
    app_token = (
        base_resp.get("app", {}).get("app_token")
        or base_resp.get("base", {}).get("base_token")
        or base_resp.get("app_token")
        or base_resp.get("base_token")
    )
    if not app_token:
        raise RuntimeError(f"base-create 未返回 app_token: {base_resp}")
    log.info("app_token=%s", app_token)

    log.info("创建 table=%s + %d 字段", cfg.bitable.table_name, len(BITABLE_FIELD_SPEC))
    table_resp = larkcli.table_create(
        base_token=app_token,
        name=cfg.bitable.table_name,
        fields=BITABLE_FIELD_SPEC,
        as_identity=write_as,
    )
    table_id = (
        table_resp.get("table_id")
        or (table_resp.get("table") or {}).get("id")
        or (table_resp.get("table") or {}).get("table_id")
        or (table_resp.get("tables") or [{}])[0].get("table_id")
        or (table_resp.get("tables") or [{}])[0].get("id")
    )
    if not table_id:
        raise RuntimeError(f"table-create 未返回 table_id: {table_resp}")
    log.info("table_id=%s", table_id)

    cfg.bitable.app_token = app_token
    cfg.bitable.table_id = table_id
    save_config(cfg)
    log.info("已写回 config.yaml")
    return cfg


def _verify_fields(base_token: str, table_id: str, as_identity: str) -> None:
    try:
        existing = larkcli.field_list(base_token, table_id, as_identity=as_identity)
    except Exception as e:
        log.warning("字段校验失败（不中断）：%s", e)
        return
    want = {f["field_name"] for f in BITABLE_FIELD_SPEC}
    have = {f.get("field_name") for f in existing if isinstance(f, dict)}
    missing = want - have
    if missing:
        log.warning("Bitable 缺字段 %s —— 尝试自动补齐", missing)
        for spec in BITABLE_FIELD_SPEC:
            if spec["field_name"] in missing:
                try:
                    larkcli.field_create(base_token, table_id, spec, as_identity=as_identity)
                    log.info("已补字段 %s", spec["field_name"])
                except Exception as e:
                    log.error("补字段 %s 失败：%s", spec["field_name"], e)


# ---------- 爬虫入库前过滤规则 ----------

# 实习岗位识别词
_INTERNSHIP_WORDS = {"实习", "intern", "Intern", "实习生", "暑期实习", "日常实习", "寒假实习"}

# AI 杠杆高触发词 → 匹配则加权
_AI_LEVERAGE_BOOST = {
    "agent", "智能体", "workflow", "自动化工作流",
    "prompt", "提示词工程", "模型调优", "效果评测",
    "低代码", "no-code", "快速原型", "mvp",
    "效能提升", "内部工具", "internal tools",
    "大模型", "llm", "rag", "ai产品", "aigc",
}

# 人工密集型红旗词 → 匹配则直接跳过
_MANUAL_LABOR_REDFLAG = {
    "数据标注", "语料清洗", "人工审核",
    "信息录入", "文员辅助", "纯执行",
    "标准话术执行", "客服支持", "电话客服",
}

# 高容错/敏捷验证词 → 匹配则加权
_AGILE_BOOST = {
    "0到1", "从0到1", "早期核心成员", "ai初创", "startup",
    "敏捷迭代", "快速试错", "结果导向",
    "参与决策", "业务闭环", "直面用户",
}

# 低容错/螺丝钉红旗词 → 匹配则降权上限 60
_BUREAUCRACY_REDFLAG = {
    "独立撰写高质量prd", "独立撰写高质量mrd",
    "熟练使用axure", "熟练使用visio",
    "1-2年以上大厂产品实习",
    "协助上级完成日常事务", "流程化推进",
}


def _pre_filter_job(job) -> tuple[bool, str]:
    """爬虫入库前过滤。返回 (是否通过, 原因)。"""
    text = f"{job.title} {' '.join(job.tags)}".lower()

    # 1. 必须是实习岗
    if not any(w.lower() in text for w in _INTERNSHIP_WORDS):
        return False, "非实习岗位"

    # 2. 人工密集型红旗 → 直接跳过
    for flag in _MANUAL_LABOR_REDFLAG:
        if flag in text:
            return False, f"人工密集型红旗: {flag}"

    return True, ""


def _compute_keyword_boost(job) -> float:
    """根据关键词计算加权系数 (0.0 ~ 0.3)。"""
    text = f"{job.title} {' '.join(job.tags)}".lower()
    boost = 0.0

    for kw in _AI_LEVERAGE_BOOST:
        if kw in text:
            boost += 0.15
            break

    for kw in _AGILE_BOOST:
        if kw in text:
            boost += 0.10
            break

    return min(boost, 0.30)


def _has_bureaucracy_redflag(job) -> bool:
    """是否命中低容错/螺丝钉红旗。"""
    text = f"{job.title} {' '.join(job.tags)}".lower()
    return any(flag in text for flag in _BUREAUCRACY_REDFLAG)


def run_crawler_scan(ctx: handlers.Context, cfg: AppConfig) -> None:
    """定时爬虫扫描：抓取 → LLM 评分 → 高分入库 + 推送。"""
    crawler_cfg = cfg.crawler
    if not crawler_cfg.enabled:
        return

    log.info("=== 爬虫扫描开始 ===")
    baseline = ctx.baseline
    client = ctx.client
    threshold = crawler_cfg.score_threshold
    seen_urls: set[str] = set()

    for source in crawler_cfg.sources:
        if not source.enabled:
            continue
        try:
            if source.name == "boss":
                crawler = BossCrawler()
            elif source.name == "nowcoder":
                crawler = NowcoderCrawler()
            else:
                crawler = RSSHubCrawler(base_url=source.base_url)
            for feed in source.feeds:
                jobs = crawler.crawl(
                    keyword=feed.keyword,
                    city=feed.city,
                    platform=source.platform,
                )
                log.info("source=%s keyword=%s city=%s → %d 条原始职位",
                         source.name, feed.keyword, feed.city, len(jobs))

                for job in jobs:
                    if job.url in seen_urls:
                        continue
                    seen_urls.add(job.url)

                    # 入库前过滤：实习岗 + 红旗词拦截
                    passed, reason = _pre_filter_job(job)
                    if not passed:
                        log.debug("过滤跳过: %s — %s", job.title, reason)
                        continue

                    # 用 trafilatura 抓 JD 正文
                    body = job.jd_text
                    if not body and job.url:
                        try:
                            import trafilatura
                            html = trafilatura.fetch_url(job.url)
                            body = trafilatura.extract(html) if html else ""
                        except Exception as e:
                            log.warning("JD 抓取失败 %s: %s", job.url, e)
                            continue

                    if not body or len(body) < 50:
                        log.debug("JD 太短，跳过: %s", job.title)
                        continue

                    # LLM 评分
                    try:
                        result = llm.parse_text(client, baseline, body, source_url=job.url)
                    except Exception as e:
                        log.warning("LLM 评分失败 %s: %s", job.title, e)
                        continue

                    if isinstance(result, dict) and result.get("__skip__"):
                        continue
                    if not isinstance(result, IntelRecord):
                        continue

                    # 补充爬虫元数据
                    if not result.Job_Title:
                        result.Job_Title = job.title
                    if not result.City:
                        result.City = job.city
                    if not result.Source_URL:
                        result.Source_URL = job.url

                    # 关键词加权：AI 杠杆 + 敏捷验证
                    boost = _compute_keyword_boost(job)
                    if boost > 0:
                        old_score = result.Match_Score
                        result.Match_Score = min(100, int(result.Match_Score + boost * 100))
                        log.info("关键词加权: %s %d→%d (+%.0f%%)",
                                 result.Company, old_score, result.Match_Score, boost * 100)

                    # 螺丝钉红旗：分数上限 60
                    if _has_bureaucracy_redflag(job):
                        if result.Match_Score > 60:
                            log.info("螺丝钉降权: %s %d→60", result.Company, result.Match_Score)
                            result.Match_Score = 60

                    # 低分跳过
                    if result.Match_Score < threshold:
                        log.debug("低分跳过: %s match=%d", result.Company, result.Match_Score)
                        continue

                    # 入库
                    upsert_fields = result.to_bitable_fields()
                    try:
                        larkcli.record_upsert(
                            cfg.bitable.app_token, cfg.bitable.table_id,
                            fields=upsert_fields,
                            as_identity=cfg.record_write_as,
                        )
                    except Exception as e:
                        log.error("爬虫入库失败 %s: %s", result.Company, e)
                        continue

                    # 推送飞书（用 bot 身份推到指定群/个人）
                    chat_id = os.environ.get("NOTIFY_CHAT_ID", "")
                    if chat_id:
                        try:
                            handlers.notify_high_score(ctx, chat_id, result)
                        except Exception as e:
                            log.warning("飞书推送失败 %s: %s", result.Company, e)

                    log.info("✅ 已入库: %s — %s (match=%d)", result.Company, result.Job_Title, result.Match_Score)

        except Exception as e:
            log.error("数据源 %s 扫描异常: %s", source.name, e)

    log.info("=== 爬虫扫描结束 ===")


def run_event_loop(ctx: handlers.Context, cfg: AppConfig) -> None:
    etypes = cfg.event.types
    as_id = cfg.event.as_identity

    pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="handler")

    backoff = [5, 30, 120]
    attempt = 0
    while True:
        cmd = [
            "lark-cli", "event", "+subscribe",
            "--event-types", etypes,
            "--as", as_id,
            "--compact", "--quiet", "--force",
        ]
        log.info("启动事件订阅: %s", " ".join(cmd))
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)

        def _graceful(_sig, _frm):
            log.info("收到停止信号，关闭事件流")
            proc.terminate()
            pool.shutdown(wait=False)
            sys.exit(0)

        signal.signal(signal.SIGINT, _graceful)
        signal.signal(signal.SIGTERM, _graceful)

        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("跳过非 JSON 行: %s", line[:200])
                    continue
                pool.submit(handlers.handle_event, ctx, event)
        except KeyboardInterrupt:
            proc.terminate()
            pool.shutdown(wait=False)
            return

        code = proc.wait()
        stderr_tail = (proc.stderr.read() if proc.stderr else "")[-500:]
        log.error("event subscribe 退出 code=%s stderr=%s", code, stderr_tail)

        if attempt >= len(backoff):
            log.error("重启次数耗尽，放弃。检查飞书后台长连接状态 / 网络 / scope。")
            sys.exit(1)
        wait = backoff[attempt]
        attempt += 1
        log.info("%ss 后重连（第 %s 次）", wait, attempt)
        time.sleep(wait)


def main() -> None:
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH)
    setup_logging()

    cfg = load_config()
    cfg = bootstrap_bitable(cfg)

    baseline_path = ROOT / cfg.baseline_path
    if not baseline_path.exists():
        raise FileNotFoundError(f"候选人基准文件不存在：{baseline_path}")
    baseline = baseline_path.read_text(encoding="utf-8")

    client = llm.make_client()
    vision_ok = llm.self_check_vision(client)
    if vision_ok:
        log.info("vision 模型自检通过，图片流已启用")
    else:
        log.warning("vision 未启用/自检失败，图片会被降级回复")

    download_dir = ROOT / cfg.download_dir

    ctx = handlers.Context(
        client=client,
        baseline=baseline,
        base_token=cfg.bitable.app_token,
        table_id=cfg.bitable.table_id,
        download_dir=download_dir,
        record_write_as=cfg.record_write_as,
        reply_as=cfg.reply_as,
        vision_ok=vision_ok,
    )

    # 启动爬虫定时扫描
    if cfg.crawler.enabled:
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler = BackgroundScheduler()
        scheduler.add_job(
            run_crawler_scan, "cron",
            hour=cfg.crawler.schedule_hour,
            minute=cfg.crawler.schedule_minute,
            args=[ctx, cfg],
            id="crawler_scan",
        )
        scheduler.start()
        log.info("爬虫调度已启动: 每天 %02d:%02d 扫描",
                 cfg.crawler.schedule_hour, cfg.crawler.schedule_minute)

        # 启动时立即跑一次扫描
        threading.Thread(target=run_crawler_scan, args=(ctx, cfg), daemon=True).start()

    run_event_loop(ctx, cfg)


if __name__ == "__main__":
    main()
