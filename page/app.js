/* 页面仅加载插件自己的汇总文件，不发起网络数据查询。 */
const $ = (id) => document.getElementById(id);
const number = new Intl.NumberFormat("zh-CN", {maximumFractionDigits: 2});
const currency = new Intl.NumberFormat("zh-CN", {minimumFractionDigits: 2, maximumFractionDigits: 2});
const money = (v) => v == null ? "未知" : "$" + currency.format(v);
const compact = (v) => v == null ? "未知" : v >= 1e8 ? number.format(v / 1e8) + " 亿" : v >= 1e4 ? number.format(v / 1e4) + " 万" : number.format(v);
const time = (v) => v ? new Date(v).toLocaleString("zh-CN", {month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false}) : "未知";
const windowName = (v) => ({"7 days":"7 天额度", "5 hours":"5 小时额度"}[v] || v);
const Pricing = window.quotaPricing;
const serviceName = value => ({standard:"标准",fast:"快速（fast）",ultrafast:"极速（ultrafast）",unknown:"未记录"}[value] || value);
const evidenceName = value => ({served:"实际响应",sent:"发送设置",requested:"请求设置",session:"会话请求设置",unknown:"按标准价暂估"}[value] || value);
let savedPricing = Pricing.normalize(), lastSnapshot = null;
let viewLifecycle = null, reportedShows = 0, pausedRefreshes = 0, waitingRefreshes = 0, deferredRenders = 0, bridgeReady = false;
let optionWrites = Promise.resolve(), lastWriteAt = 0;
function updateOptions(change) {
  const task = optionWrites.catch(() => {}).then(async () => {
    // 宿主每秒只接受一次选项写入；诊断与价格保存共用串行队列。
    const wait = 1100 - (Date.now() - lastWriteAt);
    if (wait > 0) await new Promise(resolve => setTimeout(resolve, wait));
    const current = await window.magpie.options.get();
    const result = await window.magpie.options.set(change(current || {}));
    lastWriteAt = Date.now();
    return result;
  });
  optionWrites = task;
  return task;
}
// 用宿主支持的选项接口保存实际渲染回执，便于区分桌面握手失败和采集故障。
let readyAt = 0, healthWrites = 0, healthPending = false;
async function recordHealth(force = false) {
  if (!readyAt || !viewLifecycle?.isActive() || healthPending || (!force && healthWrites >= 2) || !document.querySelector(".card")) return;
  if (!force && healthWrites === 1 && Date.now() - readyAt < 12000) return;
  healthPending = true;
  try {
    await updateOptions(options => ({...options, pageHealth: {
      at: new Date().toISOString(), scheme: location.protocol,
      cards: document.querySelectorAll(".card").length, uptimeMs: Date.now() - readyAt, version: "1.6.0",
      lifecycle:{...viewLifecycle?.stats(),pausedRefreshes,waitingRefreshes,deferredRenders},
      layout: {width:document.documentElement.clientWidth,height:innerHeight,
        scrollWidth:document.documentElement.scrollWidth,visualScale:window.visualViewport?.scale || 1,
        valueFont:getComputedStyle(document.querySelector(".value")).fontSize,
        textAdjust:getComputedStyle(document.documentElement).webkitTextSizeAdjust,
        columns:getComputedStyle(document.querySelector(".metrics")).gridTemplateColumns}
    }}));
    healthWrites++;
    reportedShows = viewLifecycle?.stats().shows || 0;
  } catch { /* 诊断回执失败不影响额度显示。 */ }
  finally { healthPending = false; }
}
function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}
function metric(label, cost, tokens) {
  const box = element("div", "metric");
  box.append(element("div", "label", label), element("div", "value", money(cost)), element("div", "tokens", compact(tokens) + " 词元"));
  return box;
}
function serviceSummary(actual) {
  const section = element("div", "service-summary");
  for (const name of [...new Set(["standard","fast",...Object.keys(actual.services || {})])]) {
    const group = actual.services?.[name];
    const badge = element("span", name === "fast" ? "service-fast" : "", serviceName(name) + " " + number.format(group?.requests || 0) + " 条");
    section.append(badge);
  }
  const groups = Object.values(actual.services || {});
  const inferredFast = groups.filter(g => g.tier === "fast" || g.tier === "ultrafast")
    .reduce((sum,g) => sum + g.requests - (g.evidence?.served || 0),0);
  if (inferredFast) section.append(element("p", "price-note", number.format(inferredFast) + " 条加速记录按当时请求设置识别，服务端实际档位未记录。"));
  if (actual.services?.unknown?.requests) section.append(element("p", "price-note", "未记录档位的用量按标准价暂估，无法确认其中是否含快速请求。"));
  return section;
}
function historyComparison(cardData, opened = false) {
  const section = element("section", "history-comparison");section.setAttribute("aria-label", "上周期对比");
  const head = element("div", "history-heading");
  head.append(element("h3", "", "上周期对比"),element("span", "", "按当前单价与长上下文设置折算"));
  section.append(head);
  if (!cardData.history?.length) {section.append(element("p", "price-note", "暂无保留的上周期额度记录。"));return section;}
  const wrap = element("div", "table-scroll"), table = element("table", "history-table"), header = element("tr");
  for (const label of ["周期", "已用额度", "预估总额度", "官方已用"]) header.append(element("th", "", label));
  table.append(header);
  for (const previous of cardData.history) {
    const row = element("tr"), label = element("td");
    label.append(element("strong", "", "上个周期"),
      element("small", "", time(previous.startAt) + " → " + time(previous.endAt) + (previous.endedEarly ? " · 提前重置" : "")));
    if (previous.actual?.services) {
      const groups=previous.actual.services, unknown=groups.unknown?.requests || 0;
      label.append(element("small", "", "快速 " + number.format(groups.fast?.requests || 0) + " 条" +
        (unknown ? " · " + number.format(unknown) + " 条未记录档位，按标准价暂估" : "")));
    }
    const cost = Pricing.cost(previous.actual,savedPricing);
    const aligned = Pricing.cost(previous.estimateActual,savedPricing);
    const estimated = previous.estimate && aligned != null ? aligned * 100 / previous.usedPercent : null;
    const usedCell = element("td");usedCell.append(element("strong", "", money(cost)),element("small", "", compact(previous.actual?.tokens) + " 词元"));
    const estimatedCell = element("td");estimatedCell.append(element("strong", "", estimated == null ? "无法估算" : money(estimated)),element("small", "", previous.estimate ? compact(previous.estimate.tokens) + " 词元" : previous.usedPercent === 0 ? "历史快照为 0%" : "缺少对齐样本"));
    const percentCell = element("td", "", previous.usedPercent == null ? "未知" : number.format(previous.usedPercent) + "%");
    row.append(label,usedCell,estimatedCell,percentCell);table.append(row);
  }
  wrap.append(table);section.append(wrap);
  const evidence = element("details", "history-evidence");evidence.open = opened;evidence.append(element("summary", "", "历史估算依据"));
  for (const previous of cardData.history) evidence.append(element("p", "price-note", "上个周期：已用统计到 " + time(previous.endAt) + "；预估使用 " + time(previous.readAt) + " 的官方比例和同一时刻前的用量。" + (previous.reason || "") + (previous.earlySnapshot ? " 该快照早于周期结束，并非最终比例。" : "") + (previous.mergedZeroStarts ? " 重置后无用量的0%起点校准已并入当前周期。" : "")));
  section.append(evidence);return section;
}
function render(snapshot) {
  lastSnapshot = snapshot;
  if(!viewLifecycle?.isActive()){deferredRenders++;return;}
  const oldOpen = [...document.querySelectorAll("#cards .model-details")].map(n => n.open);
  const historyOpen = [...document.querySelectorAll("#cards .history-evidence")].map(n => n.open);
  $("cards").replaceChildren();
  const aged = Date.now() - Date.parse(snapshot.generatedAt) > 180000;
  $("status").textContent = aged ? "采集程序超过 3 分钟未更新，请检查本机服务" : "统计更新于 " + time(snapshot.generatedAt);
  if (snapshot.error || !snapshot.cards?.length) {
    $("cards").append(element("div", "empty", snapshot.error || "尚未读取到 Codex 账号额度，请先在 Magpie 用量页刷新。"));
    return;
  }
  snapshot.cards.forEach((c, index) => {
    const stale = aged || Date.now() - Date.parse(c.readAt) > 600000 || Date.parse(c.resetsAt) <= Date.now();
    const actual = c.actual, estimate = stale ? null : c.estimate;
    const actualCost = Pricing.cost(actual, savedPricing);
    const estimateCost = estimate && actualCost != null ? actualCost * 100 / c.usedPercent : null;
    const card = element("article", "card");
    const identity = element("div", "identity"), account = element("div", "account"), name = element("div");
    name.append(element("h2", "", "Codex"), element("p", "", c.account));
    account.append(element("div", "logo", "C"), name);
    identity.append(account, element("span", "badge", (c.plan || "订阅").toUpperCase() + " · " + windowName(c.window)));
    const label = element("div", "meter-label");
    label.append(element("strong", "", "剩余 " + (c.remainingPercent == null ? "未知" : number.format(c.remainingPercent) + "%")), element("span", "", "官方已用 " + (c.usedPercent == null ? "未知" : number.format(c.usedPercent) + "%")));
    const meter = element("div", "meter"), fill = element("i");
    meter.setAttribute("role", "progressbar"); meter.setAttribute("aria-label", "官方剩余百分比");
    meter.setAttribute("aria-valuemin", "0"); meter.setAttribute("aria-valuemax", "100");
    if (c.remainingPercent != null) meter.setAttribute("aria-valuenow", String(c.remainingPercent));
    fill.style.width = Math.max(0, Math.min(100, c.remainingPercent || 0)) + "%"; meter.append(fill);
    const cycle = element("div", "cycle");
    cycle.append(element("span", "", "周期开始 " + time(c.startAt)), element("span", "", "重置时间 " + time(c.resetsAt)));
    const metrics = element("div", "metrics");
    metrics.append(metric("本周期已用 · 样本折算", actualCost, actual?.tokens), metric("预估总额度 · 满额", estimateCost, estimate?.tokens), metric("预估剩余额度", estimateCost != null && actualCost != null ? Math.max(0, estimateCost - actualCost) : null, estimate?.tokens != null ? Math.max(0, estimate.tokens - actual.tokens) : null));
    card.append(identity, label, meter, cycle, metrics, historyComparison(c,historyOpen[index]));
    if (actual) {
      card.append(serviceSummary(actual));
      const line = element("div", "detail-line");
      for (const text of [number.format(actual.requests) + " 条用量记录", "非缓存输入 " + compact(actual.input), "缓存读取 " + compact(actual.cached), "缓存创建 " + compact(actual.written), "输出 " + compact(actual.output)]) line.append(element("span", "", text));
      card.append(line);
      const custom = actual.models.filter(m => savedPricing.models[m.name]);
      card.append(element("div", "detail-line", custom.length ? "计价：已应用 " + custom.length + " 个模型的自定义单价／长上下文设置" : "计价：默认单价与长上下文档位"));
    }
    const reason = stale ? "数据已过期，当前显示旧快照；等待刷新后恢复估算。" : c.reason;
    if (reason) card.append(element("div", "notice warning", reason));
    const warnings = c.warnings.filter(w => !w.startsWith("仅按本机") && (actualCost == null || !w.startsWith("部分模型缺少价格")));
    if (warnings.length) card.append(element("div", "notice", warnings.join(" ")));
    const balance = element("div", "balance");
    if (c.balance) { const item = element("span", "", "账号积分余额　"); item.append(element("strong", "", c.balance)); balance.append(item); }
    if (c.resetCards != null) balance.append(element("span", "", "可用重置卡　" + c.resetCards + " 张"));
    if (balance.childNodes.length) card.append(balance);
    if (actual) {
      const details = element("details", "model-details");details.open = oldOpen[index] || false;
      details.append(element("summary", "", "模型明细与统计时间"));
      const table = element("table"), head = element("tr");
      for (const text of ["模型", "服务模式", "识别依据", "记录", "词元", "折算费用"]) head.append(element("th", "", text));
      table.append(head);
      for (const m of actual.models) for (const tariff of m.tariffs || []) {
        const row = element("tr"), usage = Object.values(tariff.tierUsage || {});
        const tokens = usage.reduce((sum,u) => sum + u.input + u.cached + u.written + u.output,0);
        for (const text of [m.name,serviceName(tariff.service?.tier || "unknown"),evidenceName(tariff.service?.evidence || "unknown"),number.format(tariff.requests),compact(tokens),money(Pricing.tariffCost(tariff,savedPricing.models[m.name]))]) row.append(element("td", "", text));
        table.append(row);
      }
      const tableWrap = element("div", "table-scroll");tableWrap.append(table);
      details.append(tableWrap, element("p", "detail-line", "额度快照 " + time(c.readAt) + " · 最后用量 " + time(actual.lastAt)));
      if (actual.excludedFree) details.append(element("p", "detail-line", "已排除 " + actual.excludedFree + " 条赠送模型记录"));
      card.append(details);
    }
    $("cards").append(card);
  });
  updatePriceModels();
  recordHealth((viewLifecycle?.stats().shows || 0)>reportedShows);
}
function modelInfo(model) {
  return lastSnapshot?.pricing?.models?.find(m => m.id === model);
}
function modelSample(model) {
  return lastSnapshot?.cards?.map(c => c.actual?.models?.find(m => m.name === model)).find(Boolean);
}
const copy = value => JSON.parse(JSON.stringify(value));
const dirtyModels = new Set();
const dirtyModes = new Map();
const drafts = new Map();
function markDirty(model) {
  dirtyModels.add(model);
  if (!dirtyModes.has(model)) dirtyModes.set(model,new Set());
  dirtyModes.get(model).add($("mode-select").value);
}
function defaultModel(model) {
  const sample = modelSample(model), info = modelInfo(model);
  const price = copy(info?.price || sample?.tariffs?.find(t => ["standard","unknown"].includes(t.service?.tier || "unknown") && t.price)?.price || {base: Object.fromEntries(Pricing.PARTS.map(k => [k,""])),tiers:[]});
  for (const tariff of (sample?.tariffs || []).filter(t => ["standard","unknown"].includes(t.service?.tier || "unknown"))) for (const tier of tariff.price?.tiers || []) {
    if (!price.tiers.some(t => t.above === tier.above)) price.tiers.push(copy(tier));
  }
  price.tiers.sort((a,b) => a.above-b.above);
  return {longContext:true,price,modes:Object.fromEntries((info?.modes || []).map(m => [m.name,{longContext:true,price:copy(m.price)}]))};
}
function draftFor(model) {
  if (!drafts.has(model)) {
    const defaults = defaultModel(model), own = savedPricing.models[model];
    drafts.set(model, copy({...defaults,...own,modes:{...defaults.modes,...own?.modes}}));
  }
  return drafts.get(model);
}
function modeDraft(model) {
  const draft = draftFor(model), mode = $("mode-select").value;
  if (mode === "standard") return draft;
  if (!draft.modes[mode]) draft.modes[mode] = {longContext:true,price:{
    base:Object.fromEntries(Pricing.PARTS.map(k => [k,""])),
    tiers:draft.price.tiers.map(t => ({above:t.above,...Object.fromEntries(Pricing.PARTS.map(k => [k,""]))}))}};
  return draft.modes[mode];
}
function updatePriceModels() {
  const names = [...new Set([...(lastSnapshot?.pricing?.models || []).map(m => m.id), ...(lastSnapshot?.cards || []).flatMap(c => (c.actual?.models || []).map(m => m.name))])];
  const selector = $("model-select"), selected = selector.value;
  if ([...selector.options].map(o => o.value).join("\n") !== names.join("\n")) {
    selector.replaceChildren();
    for (const name of names) {const option = element("option", "", name);option.value = name;selector.append(option);}
    if (names.includes(selected)) selector.value = selected;
  }
  renderPriceView();
}
const partLabels = {input:"输入",output:"输出",cache_write:"缓存创建",cache_read:"缓存读取",cache_write_1h:"缓存创建·1小时"};
function priceTable(price, counts, draft = null, model = "") {
  const wrap = element("div", "table-scroll"), table = element("table", "price-table"), head = element("tr");
  const hasHour = [price.base,...price.tiers].some(p => p.cache_write_1h != null);
  const parts = [...Pricing.PARTS,...(hasHour ? ["cache_write_1h"] : [])];
  for (const label of ["计价档位",...parts.map(p => partLabels[p]),"本周期记录"]) head.append(element("th", "", label));
  table.append(head);
  for (const p of [price.base,...price.tiers]) {
    const row = element("tr"), rowName = p.above ? "输入 > " + number.format(p.above) : "标准";
    const disabled = !!p.above && draft?.longContext === false;
    if (disabled) row.className = "inactive-tier";
    row.append(element("td", "", rowName + (disabled ? "（停用）" : "")));
    for (const part of parts) {
      const cell = element("td");
      if (draft) {
        const input = element("input", "unit-price");
        input.type = "number";input.min = "0";input.max = "1000000";input.step = "any";
        input.id = "price-" + (p.above || "base") + "-" + part;
        input.value = p[part] ?? "";input.disabled = disabled;
        input.placeholder = part === "cache_write_1h" ? "同创建价" : "填写单价";
        input.setAttribute("aria-label", rowName + " " + partLabels[part] + "单价");
        input.addEventListener("input", () => {
          p[part] = input.value;markDirty(model);
          $("pricing-status").textContent = "有未保存的修改";
        });
        cell.append(input);
      } else cell.textContent = p[part] == null ? "未提供" : "$" + new Intl.NumberFormat("zh-CN",{maximumFractionDigits:6}).format(p[part]);
      row.append(cell);
    }
    row.append(element("td", "", counts ? number.format(counts[String(p.above || 0)] || 0) : "—"));
    table.append(row);
  }
  wrap.append(table);return wrap;
}
function renderPriceView(force = false) {
  if(!viewLifecycle?.isActive())return;
  const view = $("price-view"), model = $("model-select").value, mode = $("mode-select").value;
  if (!force && view.dataset.model === model && view.dataset.mode === mode && (dirtyModels.has(model) || document.activeElement?.classList.contains("unit-price"))) return;
  const referenceOpen = !!view.querySelector("details")?.open;
  view.replaceChildren();view.dataset.model = model;view.dataset.mode = mode;
  if (!model) {view.append(element("p", "notice", "等待采集模型价格。"));return;}
  if (!dirtyModels.has(model)) drafts.delete(model);
  const draft = modeDraft(model), info = modelInfo(model), sample = modelSample(model);
  $("long-context").checked = draft.longContext;
  $("long-context").disabled = draft.price.tiers.length === 0;
  const counts = {};
  for (const tariff of (sample?.tariffs || []).filter(t => (t.service?.tier === "unknown" ? "standard" : t.service?.tier || "standard") === mode)) for (const [key,value] of Object.entries(tariff.tiers || {})) counts[key] = (counts[key] || 0) + value;
  view.append(element("p", "price-caption", serviceName(mode) + " · 美元 / 百万词元 · 直接修改单价"));
  view.append(priceTable(draft.price, sample ? counts : null, draft, model));
  view.append(element("p", "price-note", draft.price.tiers.length
    ? (draft.longContext ? "已启用：超过门槛的整次请求使用长上下文价。" : "已关闭：此服务模式使用基础价，长上下文行暂不参与计算。") + " 修改后点击保存生效。"
    : "当前模型未配置长上下文加价档位。"));
  view.append(element("p", "price-note", "门槛 = 非缓存输入 + 缓存读取 + 缓存创建；不含输出。修改单价会重算此模式的周期金额，实际词元和官方额度比例保持原计量。"));
  if (sample) view.append(element("p", "price-note", "当前模型样本：输入 " + compact(sample.input) + " · 输出 " + compact(sample.output) + " · 缓存创建 " + compact(sample.written) + " · 缓存读取 " + compact(sample.cached) + " 词元。"));
  const refs = element("details", "mode-prices");refs.open = referenceOpen;
  refs.append(element("summary", "", "查看默认价格与服务参考"));
  refs.append(element("p", "price-source", info?.source || "本机默认价目不可用"));
  if (info?.price) refs.append(priceTable(info.price,null));
  for (const mode of info?.modes || []) {
    refs.append(element("p", "price-source", ({fast:"快速（fast）",ultrafast:"极速（ultrafast）"}[mode.name] || mode.name) + "服务参考价"),priceTable(mode.price,null));
  }
  refs.append(element("p", "price-note", "标准价来自本机 Magpie；快速与极速单独计价，已核对模型的长上下文服务价补自 OpenAI 官方价目。缺少服务单价或长上下文档时显示未知，可在对应模式中填写。"));
  view.append(refs);
}
async function savePricing(reset = false) {
  const model = $("model-select").value, mode = $("mode-select").value;
  if (!model) return;
  $("save-pricing").disabled = true;$("reset-pricing").disabled = true;
  try {
    const selected = modeDraft(model);
    const chosen = reset ? null : Pricing.normalizeModel({price:selected.price,longContext:selected.longContext});
    $("pricing-status").textContent = "正在保存…";
    const result = await updateOptions(options => {
      const pricing = Pricing.normalize(options.pricing);
      const own = pricing.models[model] || {modes:{}};
      if (mode === "standard") {
        if (reset) {delete own.price;delete own.longContext;}
        else {own.price = chosen.price;own.longContext = chosen.longContext;}
      } else if (reset) delete own.modes[mode];
      else own.modes[mode] = {price:chosen.price,longContext:chosen.longContext};
      if (!own.price && !Object.keys(own.modes).length) delete pricing.models[model];
      else pricing.models[model] = own;
      return {...options,pricing};
    });
    savedPricing = Pricing.normalize(result.pricing);
    dirtyModes.get(model)?.delete(mode);
    if (!dirtyModes.get(model)?.size) {dirtyModels.delete(model);dirtyModes.delete(model);drafts.delete(model);}
    else if (reset) {
      const defaults = defaultModel(model), draft = drafts.get(model);
      if (mode === "standard") {draft.price=defaults.price;draft.longContext=true;}
      else {delete draft.modes[mode];if (defaults.modes[mode]) draft.modes[mode]=defaults.modes[mode];}
    }
    if (lastSnapshot) render(lastSnapshot);
    renderPriceView(true);
    $("pricing-status").textContent = reset ? "已恢复此服务模式默认价" : "已保存此服务模式，周期金额已重算";
  } catch (error) {$("pricing-status").textContent = "未保存：" + error.message;}
  finally {$("save-pricing").disabled = false;$("reset-pricing").disabled = false;}
}
$("toggle-pricing").addEventListener("click", () => {
  const panel = $("pricing-panel");panel.hidden = !panel.hidden;
  $("toggle-pricing").setAttribute("aria-expanded", String(!panel.hidden));
});
$("save-pricing").addEventListener("click", () => savePricing());
$("reset-pricing").addEventListener("click", () => savePricing(true));
$("long-context").addEventListener("change", () => {
  const model = $("model-select").value;
  modeDraft(model).longContext = $("long-context").checked;markDirty(model);
  renderPriceView(true);$("pricing-status").textContent = "有未保存的修改";
});
$("model-select").addEventListener("change", () => {
  renderPriceView(true);
  $("pricing-status").textContent = dirtyModels.has($("model-select").value) ? "此模型有未保存的修改" : "";
});
$("mode-select").addEventListener("change", () => renderPriceView(true));
let loading = false;
function refresh() {
  if(!bridgeReady || !viewLifecycle?.isActive()){
    if(viewLifecycle?.stats().state==='hidden')pausedRefreshes++;else waitingRefreshes++;
    return;
  }
  if (loading) return;
  loading = true; $("refresh").disabled = true;
  const script = document.createElement("script");
  script.src = "snapshot.js?v=" + Date.now();
  const done = () => {loading = false;if(viewLifecycle?.isActive())$("refresh").disabled = false;script.remove();};
  script.onload = () => {try {render(window.quotaSnapshot);} finally {done();}};
  script.onerror = () => {if(viewLifecycle?.isActive())$("status").textContent = "尚无可用统计文件，请检查本机采集服务。";done();};
  document.head.append(script);
}
$("refresh").addEventListener("click", refresh);
viewLifecycle=window.quotaViewLifecycle.mount({onReady:()=>{if(bridgeReady)refresh();}});
setInterval(refresh,15000);
let resizeReport;
window.addEventListener("resize", () => {clearTimeout(resizeReport);resizeReport = setTimeout(() => recordHealth(true),500);});
if (window.magpie) window.magpie.ready.then(async () => {
  readyAt = Date.now();
  try {
    const options = await window.magpie.options.get();
    savedPricing = Pricing.normalize(options.pricing);
    if (options.pricing && options.pricing.schema !== 3) await updateOptions(current => ({...current,pricing:Pricing.normalize(current.pricing)}));
  } catch {$("pricing-status").textContent = "未能读取自定义价格，暂按默认价目展示。";}
  bridgeReady=true;refresh();
}).catch(() => {$("status").textContent = "请从 Magpie 的「额度详情」标签打开此页面。";});
