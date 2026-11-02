#!/usr/bin/env python3
"""从 Magpie 原生账本读取统计，只向插件页面输出脱敏汇总。"""
import csv
import io
import json
import math
import os
import re
import subprocess
import shutil
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from pricing import PriceBook, observe_price
from cycles import comparison_cycles, comparison_candidates
from services import enrich_services, service_for

ROOT = Path(__file__).resolve().parent
MAGPIE = os.environ.get("MAGPIE_BIN") or shutil.which("magpie") or str(Path.home() / ".local/bin/magpie")
STALE_SECONDS = 600


def stamp(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value is not None else None


def span_seconds(window):
    span = window.get("span")
    if isinstance(span, (int, float)) and 0 < span <= 32 * 86400:
        return span
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(hours?|days?|weeks?)", window.get("name", ""), re.I)
    if match:
        return float(match[1]) * {"h": 3600, "d": 86400, "w": 604800}[match[2][0].lower()]
    return None


def mask_account(value):
    if "@" not in value:
        return "账号 " + value[:2] + "***"
    user, domain = value.split("@", 1)
    return user[:2] + "***@" + domain


def metric():
    return {"requests": 0, "input": 0, "cached": 0, "output": 0, "written": 0,
            "tokens": 0, "cost": Decimal(0), "missingPrices": set(), "models": {},
            "firstAt": None, "lastAt": None, "unattributed": 0, "excludedFree": 0,
            "services": {}}


def aggregate(rows, account, start, end, price_book=None, end_inclusive=True):
    result = metric()
    for row in rows:
        at = stamp(row.get("time"))
        if at is None or not start <= at <= end or (not end_inclusive and at == end):
            continue
        # 原生导出已合并网关和会话账本；仅接受明确归属的记录。
        gateway = row.get("provider") == "codex" and row.get("source") != "log"
        native = row.get("agent") == "codex" and row.get("source") == "log"
        if not (gateway or native):
            continue
        who = row.get("provider_account") if gateway else row.get("session_account")
        if not who:
            result["unattributed"] += 1
            continue
        if who != account:
            continue
        if native and row.get("session_official_login") != "true":
            result["unattributed"] += 1
            continue
        model = row.get("model") or row.get("served_model") or "未知模型"
        # 赠送模型不消耗此额度池，不能用于推算付费窗口。
        if model.split("@", 1)[0] == "gpt-reserve":
            result["excludedFree"] += 1
            continue
        counts = {k: int(row.get(field) or 0) for k, field in [
            ("input", "input_tokens"), ("cached", "cache_read_tokens"),
            ("output", "output_tokens"), ("written", "cache_write_tokens")]}
        if any(v < 0 for v in counts.values()):
            raise ValueError("账本含负词元计数")
        tokens = sum(counts.values())
        if not tokens:
            continue
        service = service_for(row)
        group = result["services"].setdefault(service["tier"], {"tier": service["tier"], "requests": 0,
            "tokens": 0, "evidence": {}})
        group["requests"] += 1
        group["tokens"] += tokens
        group["evidence"][service["evidence"]] = group["evidence"].get(service["evidence"], 0) + 1
        m = result["models"].setdefault(model, {"name": model, "requests": 0, "tokens": 0, "cost": Decimal(0), "priced": True,
                                             "input": 0, "cached": 0, "written": 0, "output": 0})
        result["requests"] += 1
        m["requests"] += 1
        m["tokens"] += tokens
        result["tokens"] += tokens
        for k, value in counts.items():
            result[k] += value
            m[k] += value
        result["firstAt"] = min(result["firstAt"] or at, at)
        result["lastAt"] = max(result["lastAt"] or at, at)
        try:
            cost = Decimal(row.get("cost_usd") or "NaN")
            if not cost.is_finite() or cost < 0:
                raise InvalidOperation
            result["cost"] += cost
            m["cost"] += cost
        except InvalidOperation:
            result["missingPrices"].add(model)
            m["priced"] = False
            cost = None
        if price_book:
            observe_price(m, row, counts, cost, price_book)
    result["cost"] = None if result["missingPrices"] else float(result["cost"])
    result["missingPrices"] = sorted(result["missingPrices"])
    result["models"] = sorted([
        {**m, "cost": float(m["cost"]) if m["priced"] else None, "tariffs": list(m.get("tariffs", {}).values())}
        for m in result["models"].values()], key=lambda m: m["tokens"], reverse=True)
    result["firstAt"], result["lastAt"] = iso(result["firstAt"]), iso(result["lastAt"])
    return result


def past_summary(cycle, rows, account, price_book):
    actual = aggregate(rows, account, cycle["start"], cycle["end"], price_book, end_inclusive=False)
    at, used = cycle["readAt"], cycle["usedPercent"]
    aligned = aggregate(rows, account, cycle["start"], at, price_book) if at is not None else None
    reason = None
    if at is None:
        reason = "缺少该周期的官方比例快照，无法推算总额度。"
    elif not used:
        reason = "历史快照已用为 0%，无法推算总额度。"
    elif not aligned["requests"]:
        reason = "历史快照前没有已关联账号的本机用量。"
    estimate = None if reason else {"tokens": round(aligned["tokens"] * 100 / used)}
    return {"kind": cycle["kind"], "startAt": iso(cycle["start"]), "endAt": iso(cycle["end"]),
            "scheduledResetsAt": iso(cycle["scheduledEnd"]), "endedEarly": cycle["endedEarly"],
            "readAt": iso(at), "usedPercent": used, "actual": actual, "estimateActual": aligned,
            "estimate": estimate, "reason": reason,
            "earlySnapshot": at is not None and cycle["end"] - at > STALE_SECONDS,
            "mergedZeroStarts": cycle.get("mergedZeroStarts", 0)}


def build_snapshot(quotas, rows, now, price_book=None, history=None):
    cards = []
    for quota in quotas:
        if quota.get("provider") != "codex" or not quota.get("user"):
            continue
        at = stamp(quota.get("readAt"))
        for window in quota.get("windows", []):
            reset = stamp(window.get("resetsAt"))
            span = span_seconds(window)
            start = reset - span if reset and span else None
            used = window.get("used")
            valid_pct = isinstance(used, (int, float)) and math.isfinite(used) and 0 <= used <= 100
            valid_range = start is not None and at is not None and start <= at < reset and span <= 32 * 86400
            actual = aggregate(rows, quota["user"], start, at, price_book) if valid_range else None
            reason = None
            if quota.get("error"):
                reason = "官方额度读取失败，请在 Magpie 用量页刷新。"
            elif not valid_range:
                reason = "周期或快照时间无效，等待官方额度刷新。"
            elif reset <= now:
                reason = "周期已重置，等待官方额度刷新。"
            elif now - at > STALE_SECONDS or at - now > 60:
                reason = "额度快照已过期，等待 Magpie 更新后估算。"
            elif not valid_pct or used == 0:
                reason = "官方已用比例为零或无效，暂不可估算。"
            elif not actual["requests"]:
                reason = "本周期没有已关联此账号的用量样本。"
            estimate = None
            if not reason:
                factor = 100 / used
                estimate = {"tokens": round(actual["tokens"] * factor),
                            "cost": round(actual["cost"] * factor, 2) if actual["cost"] is not None else None}
            warnings = ["仅按本机可读取、已关联账号的记录估算；其他设备或缺失记录可能使结果偏低。"]
            if actual and actual["unattributed"]:
                warnings.append(f"本周期另有 {actual['unattributed']} 条记录未能确认账号，未计入。")
            if actual and actual["missingPrices"]:
                warnings.append("部分模型缺少价格，费用与金额估算显示未知：" + "、".join(actual["missingPrices"]))
            past = comparison_cycles(history or {}, quota["user"], window.get("name", ""), start, span,
                has_usage=lambda begin, end: aggregate(rows, quota["user"], begin, end,
                    end_inclusive=False)["requests"] > 0) if start is not None else []
            cards.append({"account": mask_account(quota["user"]), "plan": quota.get("plan", ""),
                          "window": window.get("name", ""), "usedPercent": used if valid_pct else None,
                          "remainingPercent": 100 - used if valid_pct else None,
                          "startAt": iso(start), "resetsAt": iso(reset), "readAt": iso(at),
                          "balance": quota.get("balance", ""), "resetCards": quota.get("resets", {}).get("count"),
                          "actual": actual, "estimate": estimate, "reason": reason, "warnings": warnings,
                          "history": [past_summary(c, rows, quota["user"], price_book) for c in past]})
    samples = [c["actual"] for c in cards]
    samples.extend(p["actual"] for c in cards for p in c["history"])
    models = list(dict.fromkeys(m["name"] for sample in samples if sample for m in sample["models"]))
    return {"schema": 5, "generatedAt": iso(now), "cards": cards, "error": None,
            "pricing": price_book.summary(models) if price_book else {"models": []},
            "scope": "Magpie 原生去重账本 · 本机样本 · 同一官方快照时间"}


def run(*args):
    result = subprocess.run([str(MAGPIE), *args], capture_output=True, text=True, timeout=40, check=True)
    return result.stdout


def publish(snapshot):
    # 原子替换避免页面读到半份统计；不复制登录信息、完整账号或请求内容。
    target = ROOT / "page/snapshot.js"
    tmp = target.with_suffix(".tmp")
    tmp.write_text("window.quotaSnapshot = " + json.dumps(snapshot, ensure_ascii=False, allow_nan=False) + ";\n")
    tmp.chmod(0o600)
    os.replace(tmp, target)


def main():
    if "--installed-only" in sys.argv:
        config = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "magpie/plugins.json"
        try:
            entries = json.loads(config.read_text()).get("plugins", [])
        except (OSError, ValueError):
            return 0
        if not any(e.get("spec") == str(ROOT) and not e.get("off") for e in entries):
            return 0
    now = datetime.now(timezone.utc).timestamp()
    try:
        quotas = json.loads(run("quota", "codex", "--json"))
        history_file = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "magpie/quota-history.json"
        try:
            history = json.loads(history_file.read_text())
        except (OSError, ValueError):
            history = {}
        starts = []
        for q in quotas:
            for w in q.get("windows", []):
                reset, span = stamp(w.get("resetsAt")), span_seconds(w)
                if reset and span:
                    start = reset - span
                    starts.append(start)
                    starts.extend(c["start"] for c in comparison_candidates(history, q.get("user", ""), w.get("name", ""), start, span))
        days = max(8, math.ceil((now - min(starts or [now - 604800])) / 86400) + 2)
        rows = list(csv.DictReader(io.StringIO(run("usage", "--csv", "--days", str(days)))))
        if rows and not {"session_official_login", "session_account", "cost_usd"}.issubset(rows[0]):
            raise ValueError("账本字段不兼容")
        enrich_services(rows)
        snapshot = build_snapshot(quotas, rows, datetime.now(timezone.utc).timestamp(), PriceBook(), history)
        publish(snapshot)
        print(f"已更新 {len(snapshot['cards'])} 个额度窗口。")
    except Exception as exc:
        # 错误只报告类型，避免原生命令错误输出含账号或连接信息。
        publish({"schema": 1, "generatedAt": iso(now), "cards": [],
                 "error": "本机采集失败（" + type(exc).__name__ + "），下次自动重试。"})
        print("额度采集失败：" + type(exc).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
