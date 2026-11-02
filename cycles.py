"""从官方快照恢复真实周期，不把提前重置当作固定日历周。"""
import math
from datetime import datetime


def timestamp(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def closed_cycles(history, account, window, current_start, span):
    points = history.get("codex|" + account, {}).get(window, [])
    grouped = {}
    for point in points:
        end = timestamp(point.get("resetsAt"))
        start = timestamp(point.get("start"))
        at = timestamp(point.get("at"))
        if start is None and end is not None:
            start = end - span
        if (start is None or end is None or at is None or not start < current_start
                or not 0 < end - start <= 32 * 86400 or current_start - start > 90 * 86400):
            continue
        grouped.setdefault((start, end), []).append((at, point.get("left")))
    groups = sorted(grouped)
    result = []
    for index, (start, scheduled_end) in enumerate(groups):
        next_start = groups[index + 1][0] if index + 1 < len(groups) else current_start
        end = min(scheduled_end, next_start, current_start)
        if end <= start:
            continue
        # 分母必须来自该周期的有效快照，不能跨到新周期取百分比。
        valid = [(at, left) for at, left in grouped[(start, scheduled_end)]
                 if start <= at < end and isinstance(left, (int, float))
                 and math.isfinite(left) and 0 <= left <= 100]
        latest = max(valid, default=None, key=lambda value: value[0])
        result.append({"start": start, "end": end, "scheduledEnd": scheduled_end,
                       "readAt": latest[0] if latest else None,
                       "usedPercent": 100 - latest[1] if latest else None,
                       "endedEarly": end < scheduled_end})
    return result


def comparison_candidates(history, account, window, current_start, span):
    cycles = closed_cycles(history, account, window, current_start, span)
    result = []
    for cycle in reversed(cycles):
        result.append(cycle)
        # 先把连续的0%候选包含在读取范围内，之后再用实际用量判断。
        if cycle["usedPercent"] != 0:
            break
    return result


def comparison_cycles(history, account, window, current_start, span, *, has_usage):
    merged = 0
    for cycle in comparison_candidates(history, account, window, current_start, span):
        if cycle["usedPercent"] == 0 and not has_usage(cycle["start"], cycle["end"]):
            # 重置后的零消耗起点校准属于新周期初始化，不构成一个已结束的使用周期。
            merged += 1
            continue
        return [{**cycle, "kind": "previous", "mergedZeroStarts": merged}]
    return []
