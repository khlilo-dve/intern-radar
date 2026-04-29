"""lark-cli 子进程封装 —— 所有飞书侧 I/O 都走这里，业务层不碰 subprocess。"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

LARK_CLI = shutil.which("lark-cli") or "lark-cli"


class LarkCliError(RuntimeError):
    def __init__(self, cmd: list[str], payload: Any):
        self.cmd = cmd
        self.payload = payload
        super().__init__(f"lark-cli {' '.join(cmd[:3])} failed: {payload}")


def _run(args: list[str], timeout: int = 60) -> dict:
    """同步跑一个 lark-cli 命令，解析 JSON 返回；失败抛异常。"""
    cmd = [LARK_CLI] + args
    log.debug("lark-cli exec: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise LarkCliError(cmd, {"error": "timeout", "stderr": e.stderr}) from e

    out = proc.stdout.strip()
    if not out:
        if proc.returncode != 0:
            raise LarkCliError(cmd, {"returncode": proc.returncode, "stderr": proc.stderr})
        return {}

    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        if proc.returncode != 0:
            raise LarkCliError(cmd, {"returncode": proc.returncode, "stderr": proc.stderr, "stdout": out})
        return {"raw": out}

    if isinstance(data, dict) and data.get("ok") is False:
        raise LarkCliError(cmd, data)
    return data


# ---------- Bitable bootstrap ----------

def base_create(name: str, time_zone: str = "Asia/Shanghai", as_identity: str = "user",
                folder_token: Optional[str] = None) -> dict:
    """创建一个新的多维表格 app，返回 {app_token, url, ...}。"""
    args = ["base", "+base-create", "--name", name, "--time-zone", time_zone, "--as", as_identity]
    if folder_token:
        args.extend(["--folder-token", folder_token])
    resp = _run(args)
    return _extract_data(resp)


def table_create(base_token: str, name: str, fields: list[dict], as_identity: str = "user") -> dict:
    """在指定 base 中建表 + 批量建字段。"""
    args = [
        "base", "+table-create",
        "--base-token", base_token,
        "--name", name,
        "--fields", json.dumps(fields, ensure_ascii=False),
        "--as", as_identity,
    ]
    resp = _run(args)
    return _extract_data(resp)


def field_list(base_token: str, table_id: str, as_identity: str = "user") -> list[dict]:
    args = ["base", "+field-list", "--base-token", base_token, "--table-id", table_id, "--as", as_identity]
    resp = _run(args)
    data = _extract_data(resp)
    if isinstance(data, dict) and "items" in data:
        return data["items"]
    if isinstance(data, list):
        return data
    return []


def field_create(base_token: str, table_id: str, field_spec: dict, as_identity: str = "user") -> dict:
    args = [
        "base", "+field-create",
        "--base-token", base_token,
        "--table-id", table_id,
        "--json", json.dumps(field_spec, ensure_ascii=False),
        "--as", as_identity,
    ]
    return _extract_data(_run(args))


# ---------- Bitable runtime ----------

def record_upsert(base_token: str, table_id: str, fields: dict, as_identity: str = "user") -> dict:
    """
    用 v1 bitable API 直调，绕过 lark-cli 1.0 的 base +record-upsert（它打向 v3 接口
    schema 对不上）。v1 语义：{"fields": {字段名: 值}} 扁平结构。
    WSL Clash 假 IP 偶发抖动，带 3 次短超时重试。
    """
    path = f"/open-apis/bitable/v1/apps/{base_token}/tables/{table_id}/records"
    payload = {"fields": fields}
    args = [
        "api", "POST", path,
        "--data", json.dumps(payload, ensure_ascii=False),
        "--as", as_identity,
    ]
    last_err: Exception | None = None
    for attempt in (1, 2, 3):
        try:
            return _extract_data(_run(args, timeout=20))
        except LarkCliError as e:
            last_err = e
            log.warning("record_upsert attempt %s failed: %s", attempt, e.payload)
            continue
    raise last_err  # type: ignore[misc]


# ---------- IM ----------

def download_resource(message_id: str, file_key: str, rtype: str, out_path: str | Path,
                      as_identity: str = "bot") -> Path:
    """下载消息附带的图片/文件；返回落地路径。lark-cli 要求 --output 必须相对路径。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # lark-cli 拒绝绝对路径 ("absolute paths are not allowed")。转成相对 CWD 的相对路径。
    cwd = Path.cwd().resolve()
    abs_out = out_path.resolve()
    try:
        rel_out = abs_out.relative_to(cwd)
    except ValueError:
        raise LarkCliError(
            [],
            {"error": "output_path_not_under_cwd", "cwd": str(cwd), "out": str(abs_out)},
        )

    args = [
        "im", "+messages-resources-download",
        "--message-id", message_id,
        "--file-key", file_key,
        "--type", rtype,
        "--output", str(rel_out),
        "--as", as_identity,
    ]
    last_err: Exception | None = None
    for attempt in (1, 2, 3):
        try:
            _run(args, timeout=25)
            if not abs_out.exists():
                raise LarkCliError(args, {"error": "download_ok_but_file_missing", "path": str(abs_out)})
            return abs_out
        except LarkCliError as e:
            last_err = e
            log.warning("download attempt %s failed: %s", attempt, e.payload)
            continue
    raise last_err  # type: ignore[misc]


def send_text(chat_id: Optional[str] = None, user_id: Optional[str] = None,
              text: str = "", as_identity: str = "bot") -> dict:
    if not (chat_id or user_id):
        raise ValueError("send_text requires chat_id or user_id")
    args = ["im", "+messages-send", "--text", text, "--as", as_identity]
    if chat_id:
        args.extend(["--chat-id", chat_id])
    else:
        args.extend(["--user-id", user_id])
    last_err: Exception | None = None
    for attempt in (1, 2, 3):
        try:
            return _extract_data(_run(args, timeout=20))
        except LarkCliError as e:
            last_err = e
            log.warning("send_text attempt %s failed: %s", attempt, e.payload)
            continue
    raise last_err  # type: ignore[misc]


# ---------- helpers ----------

def _extract_data(resp: Any) -> Any:
    """lark-cli 统一响应形如 {ok:true, data:{...}} 或裸 dict；两种都兼容。"""
    if isinstance(resp, dict):
        if "data" in resp and resp.get("ok") is not False:
            return resp["data"]
        return resp
    return resp
