"""读取本机价格依据，并用逐条账本金额核对分档说明。"""
import json
import os
import math
from pathlib import Path
from services import service_for, normalize_tier

PARTS = ("input", "output", "cache_write", "cache_read")


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def normalize_price(raw):
    if not isinstance(raw, dict):
        return None
    if any(not isinstance(raw.get(k), (int, float)) or not math.isfinite(raw[k]) or raw[k] < 0 for k in PARTS):
        return None
    base = {k: raw[k] for k in PARTS}
    if raw.get("cache_write_1h", 0) > 0:
        base["cache_write_1h"] = raw["cache_write_1h"]
    tiers = []
    for tier in raw.get("tiers", []):
        spec = tier.get("tier", {})
        if spec.get("type", "context") != "context":
            continue
        above = spec.get("size", tier.get("above", 0))
        if not isinstance(above, (int, float)) or not math.isfinite(above) or above <= 0:
            continue
        values = {k: tier.get(k, base[k]) for k in PARTS}
        if any(not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in values.values()):
            continue
        if tier.get("cache_write_1h", 0) > 0:
            values["cache_write_1h"] = tier["cache_write_1h"]
        tiers.append({"above": above, **values})
    return {"base": base, "tiers": sorted(tiers, key=lambda t: t["above"])}


def flat_price(price, prompt):
    flat = price["base"]
    for tier in price["tiers"]:
        if prompt > tier["above"]:
            flat = tier
    return flat


def calculated_cost(price, counts, written_hour=0):
    flat = flat_price(price, counts["input"] + counts["cached"] + counts["written"])
    hour = min(max(written_hour, 0), counts["written"])
    return (counts["input"] * flat["input"] + counts["output"] * flat["output"]
            + counts["cached"] * flat["cache_read"] + (counts["written"] - hour) * flat["cache_write"]
            + hour * flat.get("cache_write_1h", flat["cache_write"])) / 1e6


class PriceBook:
    def __init__(self, home=None):
        self.home = Path(home or Path.home())
        config = Path(os.environ.get("XDG_CONFIG_HOME", str(self.home / ".config"))) if home is None else self.home / ".config"
        cache = Path(os.environ.get("XDG_CACHE_HOME", str(self.home / ".cache"))) if home is None else self.home / ".cache"
        self.catalog_file = cache / "magpie/models.json"
        self.catalog = read_json(self.catalog_file).get("openai", {}).get("models", {})
        self.overrides = read_json(config / "magpie/settings.json").get("modelPrices", {})
        self.available = read_json(cache / "magpie/models/codex.json").get("models", [])
        self.cache = {}
        self.service_prices = read_json(Path(__file__).with_name("service-prices.json"))

    def get(self, model, provider="codex"):
        key = (model, provider)
        if key in self.cache:
            return self.cache[key]
        entry = self.catalog.get(model, {})
        price, source = None, "暂无可核对价格"
        for name in [provider + "/" + model, provider + "/*", "*/" + model]:
            price = normalize_price(self.overrides.get(name))
            if price:
                source = "Magpie 自定义价格：" + name
                break
        if not price:
            price = normalize_price(entry.get("cost"))
            if price:
                source = "Magpie 本机 models.dev 价格表（OpenAI）"
        modes = []
        for name, mode in entry.get("experimental", {}).get("modes", {}).items():
            mode_price = normalize_price(mode.get("cost"))
            if mode_price:
                modes.append({"name": name, "price": mode_price})
        # 本机价目缺少快速模式长上下文档时，补入已核对的官方完整单价。
        for name, mode_price in self.service_prices.get("models", {}).get(model, {}).items():
            current = next((mode["price"] for mode in modes if mode["name"] == name), None)
            if current and (current["tiers"] or any(current["base"][part] != mode_price["base"][part] for part in PARTS)):
                # 本机更新后的价格优先，旧价格快照不能覆盖新价格或猜测新的长上下文价。
                continue
            modes = [mode for mode in modes if mode["name"] != name]
            modes.append({"name": name, "price": mode_price, "source": self.service_prices["source"]})
        result = {"id": model, "name": entry.get("name", model), "price": price,
                  "source": source, "modes": modes, "updated": entry.get("last_updated")}
        self.cache[key] = result
        return result

    def summary(self, models):
        names = list(dict.fromkeys([*models, *(m["ID"] for m in self.available if m.get("ID"))]))
        return {"models": [self.get(name) for name in names], "unit": "美元 / 百万词元",
                "catalogAt": self.catalog_file.stat().st_mtime if self.catalog_file.exists() else None}


def observe_price(model_stats, row, counts, cost, book):
    info = book.get(row.get("model", ""), row.get("provider", ""))
    if not info["price"] and row.get("served_model"):
        info = book.get(row["served_model"], row.get("provider", ""))
    service = service_for(row)
    mode = next((m for m in info["modes"] if normalize_tier(m["name"]) == service["tier"]), None)
    price = info["price"] if service["tier"] in ("standard", "unknown") else mode["price"] if mode else None
    source = info["source"] if service["tier"] in ("standard", "unknown") else (mode or {}).get("source", "Magpie 本机服务档位价格")
    prompt = counts["input"] + counts["cached"] + counts["written"]
    # 标准价明确有长上下文门槛、服务价却缺档时，不能自动把短上下文价套到长请求。
    levels = {t["above"] for p in (info["price"], price) if p for t in p["tiers"]}
    above = max((level for level in levels if prompt > level), default=0)
    # 多种有效单价分别列出，避免把原生会话与网关自定义价混成一种。
    key = json.dumps([price, service], sort_keys=True)
    buckets = model_stats.setdefault("tariffs", {})
    bucket = buckets.setdefault(key, {"price": price, "source": source, "service": service, "requests": 0,
                                      "matched": 0, "mismatched": 0, "longRequests": 0,
                                      "unknown": 0, "tiers": {}, "tierUsage": {}, "serviceTiers": {}})
    bucket["requests"] += 1
    bucket["serviceTiers"][service["tier"]] = bucket["serviceTiers"].get(service["tier"], 0) + 1
    if above:
        bucket["longRequests"] += 1
    bucket["tiers"][str(above)] = bucket["tiers"].get(str(above), 0) + 1
    # 分档保存各类词元，页面修改单价或关闭长上下文时可准确重算。
    usage = bucket["tierUsage"].setdefault(str(above), {"input": 0, "output": 0, "cached": 0,
                                                       "written": 0, "writtenHour": 0, "requests": 0})
    for field, value in counts.items():
        usage[field] += value
    hour = min(max(int(row.get("cache_write_1h_tokens") or 0), 0), counts["written"])
    usage["writtenHour"] += hour
    usage["requests"] += 1
    if not price or cost is None or (above and not any(t["above"] == above for t in price["tiers"])):
        bucket["unknown"] += 1
        return
    expected = calculated_cost(price, counts, hour)
    bucket["matched" if abs(expected - float(cost)) <= 0.000001 else "mismatched"] += 1
