"""主入口：启动前 bootstrap Bitable，然后守住 lark-cli event subscribe 的输出流。"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv

import handlers
import larkcli
import llm
from models import BITABLE_FIELD_SPEC

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


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        sample = ROOT / "config.yaml.example"
        if sample.exists():
            CONFIG_PATH.write_text(sample.read_text(encoding="utf-8"), encoding="utf-8")
            log.info("config.yaml 不存在，已从 example 复制一份")
        else:
            raise FileNotFoundError("config.yaml 和 config.yaml.example 都找不到")
    with CONFIG_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_config(cfg: dict) -> None:
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)


def bootstrap_bitable(cfg: dict) -> dict:
    """确保 bitable 已建并返回带 app_token/table_id 的配置。"""
    bt = cfg.setdefault("bitable", {})
    write_as = cfg.get("record_write_as", "user")

    if bt.get("app_token") and bt.get("table_id"):
        log.info("复用现有 bitable: %s / %s", bt["app_token"], bt["table_id"])
        _verify_fields(bt["app_token"], bt["table_id"], write_as)
        return cfg

    base_name = bt.get("base_name") or "实习情报雷达"
    table_name = bt.get("table_name") or "Intel"

    log.info("首次启动：创建 Bitable app=%s", base_name)
    base_resp = larkcli.base_create(name=base_name, as_identity=write_as)
    app_token = (
        base_resp.get("app", {}).get("app_token")
        or base_resp.get("base", {}).get("base_token")
        or base_resp.get("app_token")
        or base_resp.get("base_token")
    )
    if not app_token:
        raise RuntimeError(f"base-create 未返回 app_token: {base_resp}")
    log.info("app_token=%s", app_token)

    log.info("创建 table=%s + 9 字段", table_name)
    table_resp = larkcli.table_create(
        base_token=app_token,
        name=table_name,
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

    bt["app_token"] = app_token
    bt["table_id"] = table_id
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


def run_event_loop(ctx: handlers.Context, cfg: dict) -> None:
    event_cfg = cfg.get("event") or {}
    etypes = event_cfg.get("types") or "im.message.receive_v1"
    as_id = event_cfg.get("as") or "bot"

    backoff = [5, 30, 120]
    attempt = 0
    while True:
        cmd = [
            "lark-cli", "event", "+subscribe",
            "--event-types", etypes,
            "--as", as_id,
            "--compact", "--quiet",
        ]
        log.info("启动事件订阅: %s", " ".join(cmd))
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)

        def _graceful(_sig, _frm):
            log.info("收到停止信号，关闭事件流")
            proc.terminate()
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
                handlers.handle_event(ctx, event)
        except KeyboardInterrupt:
            proc.terminate()
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

    baseline_path = ROOT / (cfg.get("baseline_path") or "candidate_baseline.md")
    if not baseline_path.exists():
        raise FileNotFoundError(f"候选人基准文件不存在：{baseline_path}")
    baseline = baseline_path.read_text(encoding="utf-8")

    client = llm.make_client()
    vision_ok = llm.self_check_vision(client)
    if vision_ok:
        log.info("✅ vision 模型自检通过，图片流已启用")
    else:
        log.warning("⚠️ vision 未启用/自检失败，图片会被降级回复")

    download_dir = ROOT / (cfg.get("download_dir") or "downloads")

    ctx = handlers.Context(
        client=client,
        baseline=baseline,
        base_token=cfg["bitable"]["app_token"],
        table_id=cfg["bitable"]["table_id"],
        download_dir=download_dir,
        record_write_as=cfg.get("record_write_as", "user"),
        reply_as=cfg.get("reply_as", "bot"),
        vision_ok=vision_ok,
    )

    run_event_loop(ctx, cfg)


if __name__ == "__main__":
    main()
