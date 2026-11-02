/* 直接按分档词元和单价重算；不使用旧版全局或单模型倍率。 */
(() => {
  const PARTS = ["input", "output", "cache_write", "cache_read"];
  function amount(value) {
    if ((typeof value !== "number" && typeof value !== "string") || String(value).trim() === "") throw new Error("单价不能为空");
    const n = Number(value);
    if (!Number.isFinite(n) || n < 0 || n > 1000000) throw new Error("单价须为 0 到 1,000,000 之间的数字");
    return n;
  }
  function flat(raw) {
    const result = {};
    for (const part of PARTS) result[part] = amount(raw?.[part]);
    if (raw?.cache_write_1h != null && raw.cache_write_1h !== "") result.cache_write_1h = amount(raw.cache_write_1h);
    return result;
  }
  function normalizeSchedule(raw) {
    const tiers = (raw?.price?.tiers || []).map(t => {
      const above = Number(t.above);
      if (!Number.isSafeInteger(above) || above <= 0) throw new Error("长上下文门槛无效");
      return {above, ...flat(t)};
    }).sort((a,b) => a.above-b.above);
    if (new Set(tiers.map(t => t.above)).size !== tiers.length) throw new Error("长上下文门槛重复");
    return {longContext: raw?.longContext !== false, price: {base: flat(raw?.price?.base), tiers}};
  }
  function normalizeModel(raw) {
    const result = {...(raw?.price ? normalizeSchedule(raw) : {}), modes: {}};
    for (const [name, value] of Object.entries(raw?.modes || {})) result.modes[name] = normalizeSchedule(value);
    return result;
  }
  function normalize(settings) {
    const result = {schema: 3, models: {}};
    // 旧版倍率失效，不转成隐藏的单价乘数。
    if (![2,3].includes(settings?.schema)) return result;
    for (const [model, value] of Object.entries(settings.models || {})) result.models[model] = normalizeModel(value);
    return result;
  }
  function costAt(usage, price) {
    if (!price) return null;
    for (const part of PARTS) if (!Number.isFinite(price[part]) || price[part] < 0) return null;
    const hour = Math.min(Math.max(usage.writtenHour || 0,0),usage.written || 0);
    return (usage.input*price.input + usage.output*price.output + usage.cached*price.cache_read
      + (usage.written-hour)*price.cache_write + hour*(price.cache_write_1h ?? price.cache_write)) / 1e6;
  }
  function tariffCost(tariff, own) {
    if (!tariff.tierUsage) return null;
    const service = tariff.service?.tier || "unknown";
    const chosen = ["standard","unknown"].includes(service) ? own : own?.modes?.[service];
    const schedule = chosen?.price || tariff.price;
    let total = 0;
    for (const [level, usage] of Object.entries(tariff.tierUsage)) {
      const above = Number(level);
      const price = chosen?.longContext === false || above === 0 ? schedule?.base
        : schedule?.tiers?.find(t => t.above === above);
      const value = costAt(usage, price);
      if (value == null) return null;
      total += value;
    }
    return total;
  }
  function modelCost(model, settings) {
    if (!model?.tariffs?.length) return null;
    const own = settings.models[model.name];
    let total = 0, requests = 0;
    for (const tariff of model.tariffs) {
      const value = tariffCost(tariff, own);
      if (value == null) return null;
      total += value;
      requests += Object.values(tariff.tierUsage).reduce((sum,u) => sum + u.requests,0);
    }
    // 缺分档数据时显示未知，不能用部分样本冒充完整周期。
    return requests === model.requests && Number.isFinite(total) ? total : null;
  }
  function cost(actual, settings) {
    if (actual?.requests === 0 && actual.models?.length === 0) return 0;
    if (!actual?.models?.length) return null;
    const values = actual.models.map(m => modelCost(m,settings));
    return values.some(v => v == null) ? null : values.reduce((sum,v) => sum+v,0);
  }
  globalThis.quotaPricing = {PARTS, amount, flat, normalize, normalizeModel, costAt, tariffCost, modelCost, cost};
})();
