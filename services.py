"""按原始请求关联服务档位；只补元数据，不增加或重算账本用量。"""
import json
import subprocess
import sys
from collections import defaultdict
from contextlib import closing
from datetime import datetime
from pathlib import Path


def normalize_tier(value):
    value = str(value or "").strip().lower()
    return {"priority": "fast", "default": "standard", "normal": "standard"}.get(value, value)


def service_for(row):
    for field, evidence in (("tier_served", "served"), ("_native_served", "served"),
                            ("tier_sent", "sent"), ("tier_asked", "requested"),
                            ("_native_requested", "session")):
        tier = normalize_tier(row.get(field))
        if tier and tier != "auto":
            return {"tier": tier, "evidence": evidence}
    return {"tier": "unknown", "evidence": "unknown"}


def second(value):
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except (ValueError, TypeError, AttributeError):
        return None


def session_lines(path):
    """流式读取普通及压缩会话，不在磁盘上展开原始内容。"""
    if path.suffix != ".zst":
        with path.open(encoding="utf-8") as source:
            yield from source
        return
    process = subprocess.Popen(["zstd", "--decompress", "--stdout", "--quiet", "--", str(path)],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, encoding="utf-8")
    try:
        yield from process.stdout
        # 解压器可能先输出部分内容再发现损坏；失败时丢弃该文件的整份档位结果。
        if process.wait() != 0:
            raise OSError("压缩会话解压失败")
    finally:
        process.stdout.close()
        # 会话不匹配或解码失败时也回收进程，避免管道阻塞和残留子进程。
        if process.poll() is None:
            process.kill()
        process.wait()


def read_services(path, session):
    responses, moments = {}, defaultdict(set)
    requested, active_turn = "", None
    turns = {}
    with closing(session_lines(path)) as source:
        for line in source:
            # 跳过消息正文，避免把用户引用的示例当作真实设置。
            try:
                item = json.loads(line)
            except ValueError:
                continue
            kind, payload = item.get("type"), item.get("payload") or {}
            if not isinstance(payload, dict):
                continue
            if kind == "session_meta":
                if payload.get("id") != session:
                    return {}, {}
                requested, active_turn, turns = "", None, {}
            elif kind == "turn_context":
                active_turn = payload.get("turn_id")
                if "service_tier" in payload:
                    requested = normalize_tier(payload["service_tier"])
                turns[active_turn] = requested
            elif kind == "event_msg" and payload.get("type") == "thread_settings_applied":
                if payload.get("thread_id", session) != session:
                    continue
                settings = payload.get("thread_settings") or {}
                if "service_tier" in settings:
                    requested = normalize_tier(settings["service_tier"])
                    # 桌面端在 turn_context 紧后记录该回合实际采用的设置。
                    turns[active_turn] = requested
            elif kind == "token_usage_record" or (kind == "event_msg" and payload.get("type") == "token_count"):
                if payload.get("thread_id", session) != session:
                    continue
                tier = turns.get(payload.get("turn_id", active_turn), requested)
                served = normalize_tier(payload.get("service_tier"))
                value = (tier, served)
                response = payload.get("response_id")
                if response:
                    # 同一响应若出现冲突，宁可保留未知也不猜测。
                    if response in responses and responses[response] != value:
                        responses[response] = ("", "")
                    else:
                        responses[response] = value
                at = second(item.get("timestamp"))
                if at is not None:
                    moments[at].add(value)
    return responses, moments


def enrich_services(rows, home=None):
    home = Path(home or Path.home())
    grouped = defaultdict(list)
    for row in rows:
        if row.get("agent") == "codex" and row.get("source") == "log" and row.get("session"):
            grouped[row["session"]].append(row)
    if not grouped:
        return
    paths = defaultdict(set)
    # 读取 Magpie 已发现的会话路径，同时覆盖尚未进入缓存的新会话与归档。
    try:
        files = json.loads((home / ".cache/magpie/sessions.json").read_text()).get("files", {})
        for filename, meta in files.items():
            # 压缩后缓存可能仍保留已移除的普通文件路径。
            if meta.get("id") in grouped and Path(filename).is_file():
                paths[meta["id"]].add(Path(filename))
    except (OSError, ValueError):
        pass
    codex_home = home / ".codex"
    for root in (codex_home / "sessions", codex_home / "archived_sessions"):
        for pattern in ("*.jsonl", "*.jsonl.zst"):
            for path in root.rglob(pattern):
                session = path.name.removesuffix(".zst").removesuffix(".jsonl")[-36:]
                if session in grouped:
                    paths[session].add(path)
    unreadable = 0
    for session, session_rows in grouped.items():
        values, times = defaultdict(set), defaultdict(set)
        for path in sorted(paths[session]):
            try:
                responses, moments = read_services(path, session)
            except (OSError, UnicodeError):
                unreadable += 1
                continue
            for key, value in responses.items():
                values[key].add(value)
            for key, value in moments.items():
                times[key].update(value)
        for row in session_rows:
            response = row.get("response_id")
            matches = values.get(response, set()) if response else times.get(second(row.get("time")), set())
            if len(matches) == 1:
                requested, served = next(iter(matches))
                row["_native_requested"], row["_native_served"] = requested, served
    if unreadable:
        print(f"档位采集有 {unreadable} 个会话文件无法读取，仅采用成功读取的档位依据。", file=sys.stderr)
