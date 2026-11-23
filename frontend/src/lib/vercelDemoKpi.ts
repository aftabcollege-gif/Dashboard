import type { KpiComputabilityStatus, KpiDefinition } from "@/types/bi";

type Row = Record<string, any>;

function safeNum(v: any): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

export function formatKpiVal(
  val: number | null,
  fmt: string = "number",
  unit: string = ""
): string {
  if (val === null || val === undefined || !Number.isFinite(val)) return "—";
  if (fmt === "percentage" || fmt === "percent") {
    return `${val.toLocaleString("en-US", { maximumFractionDigits: 2 })}%`;
  }
  if (fmt === "currency") {
    return `${Math.round(val).toLocaleString("en-US")}${unit ? ` ${unit}` : ""}`;
  }
  if (fmt === "count") {
    return `${Math.round(val).toLocaleString("en-US")}${unit ? ` ${unit}` : ""}`;
  }
  const rounded = Math.round(val * 100) / 100;
  return `${rounded.toLocaleString("en-US")}${unit ? ` ${unit}` : ""}`;
}

/** Simple deterministic Gregorian -> Jalali (Year, Month) index for calendar-aligned demo MoM */
function toJalaliYm(isoDateStr: string): { jy: number; jm: number } | null {
  const m = String(isoDateStr || "").match(/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})/);
  if (!m) return null;
  const gy = parseInt(m[1], 10);
  const gm = parseInt(m[2], 10);
  const gd = parseInt(m[3], 10);
  if (gy < 1900 || gm < 1 || gm > 12 || gd < 1 || gd > 31) return null;
  const gdm = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];
  const gy2 = gm > 2 ? gy + 1 : gy;
  let days =
    355666 +
    365 * gy +
    Math.floor((gy2 + 3) / 4) -
    Math.floor((gy2 + 99) / 100) +
    Math.floor((gy2 + 399) / 400) +
    gd +
    gdm[gm - 1];
  let jy = -1595 + 33 * Math.floor(days / 12053);
  days %= 12053;
  jy += 4 * Math.floor(days / 1461);
  days %= 1461;
  if (days > 365) {
    jy += Math.floor((days - 1) / 365);
    days = (days - 1) % 365;
  }
  const jm = days < 186 ? 1 + Math.floor(days / 31) : 7 + Math.floor((days - 186) / 30);
  return { jy, jm };
}

function getMonthIndex(rawDate: any, calendarMode: string): { idx: number; lbl: string } | null {
  if (!rawDate) return null;
  const s = String(rawDate).slice(0, 10);
  const m = s.match(/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})/);
  if (!m) return null;
  const y = parseInt(m[1], 10);
  const mo = parseInt(m[2], 10);
  if (calendarMode === "jalali" && y >= 1900) {
    const j = toJalaliYm(s);
    if (!j) return null;
    return { idx: j.jy * 12 + j.jm, lbl: `${j.jy}/${String(j.jm).padStart(2, "0")}` };
  }
  return { idx: y * 12 + mo, lbl: `${y}-${String(mo).padStart(2, "0")}` };
}

function matchField(
  fields: any[],
  pos: string[],
  neg: string[] = [],
  numericOnly = false,
  dateOnly = false
): any | null {
  let best: { score: number; f: any } | null = null;
  for (const f of fields) {
    const fn = String(f.field_name || "").toLowerCase();
    const dn = String(f.display_name || "").toLowerCase();
    const dt = String(f.data_type || "").toLowerCase();
    const isNum = ["int", "integer", "float", "double", "decimal", "numeric", "number"].includes(dt);
    const isDt = dt.includes("date") || /(date|time|created_at)/.test(fn);
    if (numericOnly && !isNum) continue;
    if (dateOnly && !isDt) continue;
    if (neg.some((n) => fn.includes(n) || dn.includes(n))) continue;
    let sc = 0;
    for (const p of pos) {
      if (fn === p || dn === p) sc = Math.max(sc, 100);
      else if (fn.includes(p) || dn.includes(p)) sc = Math.max(sc, 60);
    }
    if (sc > 0 && (!best || sc > best.score)) {
      best = { score: sc, f };
    }
  }
  return best?.f || null;
}

export function evalTwoStageAov(rows: Row[], amountCol: string, orderCol: string): {
  value: number | null;
  ordersCount: number;
  nullOrderRows: number;
  multiItemOrders: number;
} {
  const byOrder = new Map<string, { sum: number; count: number }>();
  let nullOrderRows = 0;
  for (const r of rows) {
    const ok = r[orderCol];
    if (ok === null || ok === undefined || ok === "") {
      nullOrderRows++;
      continue;
    }
    const key = String(ok);
    const amt = safeNum(r[amountCol]) ?? 0;
    const prev = byOrder.get(key) || { sum: 0, count: 0 };
    prev.sum += amt;
    prev.count += 1;
    byOrder.set(key, prev);
  }
  if (byOrder.size === 0) return { value: null, ordersCount: 0, nullOrderRows, multiItemOrders: 0 };
  let total = 0;
  let multiItemOrders = 0;
  for (const entry of Array.from(byOrder.values())) {
    total += entry.sum;
    if (entry.count > 1) multiItemOrders++;
  }
  return {
    value: total / byOrder.size,
    ordersCount: byOrder.size,
    nullOrderRows,
    multiItemOrders,
  };
}

export function evalCalendarMom(
  rows: Row[],
  measureCol: string,
  timeCol: string,
  calendarMode: string = "gregorian"
): {
  value: number | null;
  currLbl?: string;
  prevLbl?: string;
  currVal?: number;
  prevVal?: number;
  status: "ok" | "too_few_months" | "gapped" | "zero_prev";
} {
  const monthly = new Map<number, { lbl: string; sum: number }>();
  for (const r of rows) {
    const m = getMonthIndex(r[timeCol], calendarMode);
    if (!m) continue;
    const v = safeNum(r[measureCol]) ?? 0;
    const cur = monthly.get(m.idx) || { lbl: m.lbl, sum: 0 };
    cur.sum += v;
    monthly.set(m.idx, cur);
  }
  if (monthly.size < 2) return { value: null, status: "too_few_months" };
  const keys = Array.from(monthly.keys()).sort((a, b) => a - b);
  const currIdx = keys[keys.length - 1];
  const prevIdx = currIdx - 1;
  const curr = monthly.get(currIdx)!;
  const prev = monthly.get(prevIdx);
  if (!prev) {
    return { value: null, currLbl: curr.lbl, status: "gapped" };
  }
  if (prev.sum === 0) {
    return { value: null, currLbl: curr.lbl, prevLbl: prev.lbl, currVal: curr.sum, prevVal: 0, status: "zero_prev" };
  }
  return {
    value: ((curr.sum - prev.sum) * 100) / Math.abs(prev.sum),
    currLbl: curr.lbl,
    prevLbl: prev.lbl,
    currVal: curr.sum,
    prevVal: prev.sum,
    status: "ok",
  };
}

export function evalMultiSourceAgg(
  leftRows: Row[],
  rightRows: Row[],
  msa: Record<string, any>
): number | null {
  const lKey = String(msa.left_key || "");
  const rKey = String(msa.right_key || "");
  const lField = String(msa.left_field || "");
  const rField = String(msa.right_field || "");
  const lAgg = String(msa.left_agg || "SUM").toUpperCase();
  const rAgg = String(msa.right_agg || "SUM").toUpperCase();
  const jType = String(msa.join_type || "INNER").toUpperCase();

  const aggGroup = (items: Row[], fld: string, op: string): number => {
    if (op === "COUNT") return items.length;
    if (op === "COUNT_DISTINCT") return new Set(items.map((x) => x[fld]).filter((v) => v != null)).size;
    const nums = items.map((x) => safeNum(x[fld])).filter((v): v is number => v !== null);
    if (!nums.length) return 0;
    if (op === "AVG") return nums.reduce((a, b) => a + b, 0) / nums.length;
    if (op === "MIN") return Math.min(...nums);
    if (op === "MAX") return Math.max(...nums);
    return nums.reduce((a, b) => a + b, 0);
  };

  const lMap = new Map<string, Row[]>();
  for (const r of leftRows) {
    if (r[lKey] == null) continue;
    const k = String(r[lKey]);
    if (!lMap.has(k)) lMap.set(k, []);
    lMap.get(k)!.push(r);
  }
  const rMap = new Map<string, Row[]>();
  for (const r of rightRows) {
    if (r[rKey] == null) continue;
    const k = String(r[rKey]);
    if (!rMap.has(k)) rMap.set(k, []);
    rMap.get(k)!.push(r);
  }

  let lTotal = 0;
  let rTotal = 0;
  for (const [k, lGroup] of Array.from(lMap.entries())) {
    const rGroup = rMap.get(k);
    if (jType !== "LEFT" && !rGroup) continue;
    lTotal += aggGroup(lGroup, lField, lAgg);
    rTotal += rGroup ? aggGroup(rGroup, rField, rAgg) : 0;
  }

  const expr = String(msa.final_expr || "");
  if (expr.includes("_l_val") && expr.includes("_r_val") && expr.includes("/")) {
    if (rTotal === 0) return null;
    const mult = expr.includes("* 100") ? 100 : 1;
    if (expr.includes("-")) return ((lTotal - rTotal) * mult) / Math.abs(rTotal);
    return (lTotal * mult) / rTotal;
  }
  return lTotal;
}

export function evaluateDemoKpiFormula(
  formula: string,
  rows: Row[],
  calendarMode: string = "gregorian",
  multiSourceAgg?: Record<string, any> | null,
  rightRows?: Row[]
): number | null {
  if (multiSourceAgg && rightRows) {
    return evalMultiSourceAgg(rows, rightRows, multiSourceAgg);
  }
  const f = (formula || "").trim();
  const aovM = f.match(/^AOV\(\s*"?([A-Za-z0-9_]+)"?\s*,\s*"?([A-Za-z0-9_]+)"?\s*\)$/i);
  if (aovM) {
    return evalTwoStageAov(rows, aovM[1], aovM[2]).value;
  }
  const momM = f.match(/^MOM_CALENDAR\(\s*"?([A-Za-z0-9_]+)"?\s*,\s*"?([A-Za-z0-9_]+)"?\s*\)$/i);
  if (momM) {
    return evalCalendarMom(rows, momM[1], momM[2], calendarMode).value;
  }
  // Profit margin / ratio formulas: (SUM(a) - SUM(b)) * 100 / NULLIF(SUM(a), 0)
  const marginM = f.match(/SUM\(\s*"?([A-Za-z0-9_]+)"?\s*\)\s*,\s*0\)\s*-\s*COALESCE\(\s*SUM\(\s*"?([A-Za-z0-9_]+)"?\s*\)/i);
  if (marginM) {
    const s = rows.reduce((a, r) => a + (safeNum(r[marginM[1]]) ?? 0), 0);
    const c = rows.reduce((a, r) => a + (safeNum(r[marginM[2]]) ?? 0), 0);
    return s === 0 ? null : ((s - c) * 100) / s;
  }
  // Ratio SUM(a) / NULLIF(SUM(b), 0)
  const ratioM = f.match(/SUM\(\s*"?([A-Za-z0-9_]+)"?\s*\).*?\/\s*NULLIF\(\s*SUM\(\s*"?([A-Za-z0-9_]+)"?\s*\)/i);
  if (ratioM) {
    const a = rows.reduce((acc, r) => acc + (safeNum(r[ratioM[1]]) ?? 0), 0);
    const b = rows.reduce((acc, r) => acc + (safeNum(r[ratioM[2]]) ?? 0), 0);
    if (b === 0) return null;
    const mult = f.includes("* 100") ? 100 : 1;
    return (a * mult) / b;
  }
  const cdM = f.match(/COUNT\s*\(\s*DISTINCT\s+"?([A-Za-z0-9_]+)"?\s*\)/i);
  if (cdM) {
    return new Set(rows.map((r) => r[cdM[1]]).filter((v) => v !== null && v !== undefined && v !== "")).size;
  }
  const aggM = f.match(/(SUM|AVG|MIN|MAX|COUNT)\s*\(\s*"?([A-Za-z0-9_*]+)"?\s*\)/i);
  if (aggM) {
    const fn = aggM[1].toUpperCase();
    const col = aggM[2];
    if (fn === "COUNT" && col === "*") return rows.length;
    const nums = rows.map((r) => safeNum(r[col])).filter((v): v is number => v !== null);
    if (!nums.length) return fn === "SUM" ? 0 : null;
    if (fn === "COUNT") return nums.length;
    if (fn === "SUM") return nums.reduce((a, b) => a + b, 0);
    if (fn === "AVG") return nums.reduce((a, b) => a + b, 0) / nums.length;
    if (fn === "MIN") return Math.min(...nums);
    if (fn === "MAX") return Math.max(...nums);
  }
  return rows.length;
}

export function evaluateDemoStandardAndBusinessKpis(
  ds: any,
  rows: Row[],
  calendarMode: string = "jalali",
  targetValues: Record<string, number> = {}
) {
  const fields = ds.fields || [];
  const salesF = matchField(fields, ["total_sales", "sales_amount", "revenue", "sales", "amount", "فروش", "درآمد", "مبلغ"], ["cost", "target", "budget", "profit", "unit_price", "pct", "هزینه", "هدف", "سود"], true);
  const costF = matchField(fields, ["total_cost", "cost", "expense", "cogs", "بها", "هزینه"], ["target", "budget", "ratio", "pct"], true);
  const profitF = matchField(fields, ["net_profit", "gross_profit", "profit", "سود"], ["pct", "rate", "margin_pct"], true);
  const targetF = matchField(fields, ["target_revenue", "target_sales", "target_amount", "target", "budget", "هدف", "بودجه"], ["date", "rate", "pct"], true);
  const orderF = matchField(fields, ["order_id", "invoice_id", "tx_id", "transaction_id", "شماره سفارش", "شناسه سفارش"], ["customer", "product", "مشتری"]);
  const custF = matchField(fields, ["customer_id", "client_id", "buyer_id", "customer_name", "customer", "شناسه مشتری", "مشتری"], ["date"]);
  const timeF = matchField(fields, ["order_date", "tx_date", "sale_date", "created_at", "date", "تاریخ"], ["due", "delivery"], false, true);
  const delivF = matchField(fields, ["actual_delivery", "delivery_date", "delivered_at", "تاریخ تحویل"], [], false, true);
  const dueF = matchField(fields, ["due_date", "promised_date", "deadline", "تاریخ سررسید"], [], false, true);
  const delayDaysF = matchField(fields, ["delay_days", "late_days", "overdue_days", "روز تأخیر"], [], true);

  const stdKpis: KpiDefinition[] = [];
  const makeStd = (partial: Partial<KpiDefinition> & { id: string; name: string; formula: string }): KpiDefinition => {
    const status = partial.computability_status || "computable";
    const statusFa =
      status === "computable"
        ? "قابل محاسبه"
        : status === "computable_with_warning"
        ? "قابل محاسبه با هشدار"
        : status === "needs_data"
        ? "نیازمند تکمیل داده"
        : "غیرقابل محاسبه";
    return {
      slug: partial.id,
      is_standard: true,
      category: "standard",
      format_type: partial.display_format || "number",
      display_format: partial.display_format || "number",
      unit: partial.unit || "",
      priority: partial.priority || 1,
      comparison_mode: partial.comparison_mode || "none",
      source_datasets: [ds.name],
      source: ds.name,
      confidence: 0.94,
      score: status === "needs_data" ? 55 : status === "uncomputable" ? 20 : 95,
      computability_status: status,
      computability_status_fa: statusFa,
      formatted_value: formatKpiVal(partial.value ?? null, partial.display_format || "number", partial.unit || ""),
      ...partial,
    };
  };

  // 1. Total Sales
  if (!salesF) {
    stdKpis.push(makeStd({
      id: "std_total_sales",
      name: "مجموع فروش",
      formula: "COALESCE(SUM(<sales_amount>), 0)",
      display_format: "currency",
      unit: "ریال",
      computability_status: "needs_data",
      missing_fields_fa: ["فیلد مبلغ فروش / درآمد (Sales Amount)"],
      rejection_reason_fa: "ستون مبلغ فروش در این منبع داده یافت نشد.",
      priority: 1,
    }));
  } else {
    const fn = salesF.field_name;
    const total = rows.reduce((a, r) => a + (safeNum(r[fn]) ?? 0), 0);
    const nullCnt = rows.filter((r) => safeNum(r[fn]) === null).length;
    const warns = nullCnt > 0 ? [`${nullCnt} ردیف خالی (NULL) بدون خطا حذف شدند.`] : [];
    stdKpis.push(makeStd({
      id: "std_total_sales",
      name: "مجموع فروش",
      formula: `COALESCE(SUM("${fn}"), 0)`,
      base_fields: [fn],
      display_format: "currency",
      unit: "ریال",
      value: Math.round(total * 100) / 100,
      computability_status: warns.length ? "computable_with_warning" : "computable",
      warnings_fa: warns,
      priority: 1,
    }));
  }

  // 2. Profit Margin
  if (!salesF || (!costF && !profitF)) {
    stdKpis.push(makeStd({
      id: "std_profit_margin",
      name: "حاشیه سود",
      formula: "(COALESCE(SUM(<sales>), 0) - COALESCE(SUM(<cost>), 0)) * 100.0 / NULLIF(SUM(<sales>), 0)",
      display_format: "percentage",
      unit: "%",
      computability_status: "needs_data",
      missing_fields_fa: [!salesF ? "مبلغ فروش" : "", !costF && !profitF ? "فیلد هزینه یا سود" : ""].filter(Boolean),
      rejection_reason_fa: "برای محاسبه حاشیه سود به ستون فروش و هزینه/سود نیاز است.",
      priority: 2,
    }));
  } else {
    const sFn = salesF.field_name;
    const sSum = rows.reduce((a, r) => a + (safeNum(r[sFn]) ?? 0), 0);
    let pVal: number | null = null;
    let formula = "";
    const baseFields = [sFn];
    if (costF) {
      const cFn = costF.field_name;
      baseFields.push(cFn);
      const cSum = rows.reduce((a, r) => a + (safeNum(r[cFn]) ?? 0), 0);
      formula = `(COALESCE(SUM("${sFn}"), 0) - COALESCE(SUM("${cFn}"), 0)) * 100.0 / NULLIF(SUM("${sFn}"), 0)`;
      pVal = sSum !== 0 ? ((sSum - cSum) * 100) / sSum : null;
    } else if (profitF) {
      const pFn = profitF.field_name;
      baseFields.push(pFn);
      const prSum = rows.reduce((a, r) => a + (safeNum(r[pFn]) ?? 0), 0);
      formula = `COALESCE(SUM("${pFn}"), 0) * 100.0 / NULLIF(SUM("${sFn}"), 0)`;
      pVal = sSum !== 0 ? (prSum * 100) / sSum : null;
    }
    stdKpis.push(makeStd({
      id: "std_profit_margin",
      name: "حاشیه سود",
      formula,
      base_fields: baseFields,
      display_format: "percentage",
      unit: "%",
      value: pVal !== null ? Math.round(pVal * 100) / 100 : null,
      computability_status: pVal === null ? "uncomputable" : "computable",
      errors_fa: pVal === null ? ["مجموع فروش برابر صفر است؛ تقسیم بر صفر قابل محاسبه نیست."] : [],
      rejection_reason_fa: pVal === null ? "مجموع فروش برابر صفر است." : undefined,
      priority: 2,
    }));
  }

  // 3. Calendar-aligned MoM Growth
  if (!salesF || !timeF) {
    stdKpis.push(makeStd({
      id: "std_mom_growth",
      name: "رشد ماهانه",
      formula: "MOM_CALENDAR(<sales_amount>, <date_field>)",
      display_format: "percentage",
      unit: "%",
      computability_status: "needs_data",
      missing_fields_fa: ["فیلد مبلغ فروش", "ستون تاریخ / زمان"],
      rejection_reason_fa: "نیازمند ستون تاریخ معتبر و مبلغ فروش در منبع داده است.",
      priority: 3,
    }));
  } else {
    const sFn = salesF.field_name;
    const tFn = timeF.field_name;
    const mom = evalCalendarMom(rows, sFn, tFn, calendarMode);
    let st: KpiComputabilityStatus = "computable";
    const warns: string[] = [];
    const errs: string[] = [];
    const miss: string[] = [];
    if (mom.status === "too_few_months" || mom.status === "gapped") {
      st = "needs_data";
      miss.push("حداقل ۲ ماه تقویمی متوالی در داده‌ها");
      warns.push(mom.status === "gapped" ? "ماه قبل از آخرین ماه در داده‌ها وجود ندارد (شکاف تقویمی)." : "تعداد ماه‌های تقویمی کمتر از ۲ ماه است.");
    } else if (mom.status === "zero_prev") {
      st = "uncomputable";
      errs.push("مقدار فروش ماه قبل صفر است؛ تقسیم بر صفر رخ می‌دهد.");
    }
    stdKpis.push(makeStd({
      id: "std_mom_growth",
      name: "رشد ماهانه",
      formula: `MOM_CALENDAR("${sFn}", "${tFn}")`,
      base_fields: [sFn, tFn],
      time_field: tFn,
      display_format: "percentage",
      unit: "%",
      value: mom.value !== null ? Math.round(mom.value * 100) / 100 : null,
      previous_value: mom.prevVal ?? null,
      computability_status: st,
      warnings_fa: warns,
      errors_fa: errs,
      missing_fields_fa: miss,
      rejection_reason_fa: errs[0] || warns[0],
      comparison_mode: st === "computable" ? "previous_period" : "none",
      comparison_basis: mom.currLbl && mom.prevLbl ? `مقایسه ${mom.currLbl} با ${mom.prevLbl}` : undefined,
      priority: 3,
    }));
  }

  // 4. Two-stage Order-level AOV
  if (!salesF || !orderF) {
    stdKpis.push(makeStd({
      id: "std_aov",
      name: "میانگین مبلغ سفارش",
      formula: "AOV(<sales_amount>, <order_id>)",
      display_format: "currency",
      unit: "ریال",
      computability_status: "needs_data",
      missing_fields_fa: ["شناسه سفارش / فاکتور (Order ID)"],
      rejection_reason_fa: "برای تجمیع دومرحله‌ای سطح سفارش به شناسه سفارش نیاز است.",
      priority: 4,
    }));
  } else {
    const sFn = salesF.field_name;
    const oFn = orderF.field_name;
    const aov = evalTwoStageAov(rows, sFn, oFn);
    const warns = aov.multiItemOrders > 0 ? [`تجمیع دومرحله‌ای روی ${aov.ordersCount} سفارش یکتا انجام شد.`] : [];
    stdKpis.push(makeStd({
      id: "std_aov",
      name: "میانگین مبلغ سفارش",
      formula: `AOV("${sFn}", "${oFn}")`,
      base_fields: [sFn, oFn],
      pre_agg_grain: { grain_field: oFn, measure_field: sFn, grain_agg: "SUM" },
      display_format: "currency",
      unit: "ریال",
      value: aov.value !== null ? Math.round(aov.value * 100) / 100 : null,
      computability_status: aov.value === null ? "uncomputable" : warns.length ? "computable_with_warning" : "computable",
      warnings_fa: warns,
      errors_fa: aov.value === null ? ["هیچ شناسه سفارش معتبری یافت نشد."] : [],
      priority: 4,
    }));
  }

  // 5. Unique Customers
  if (!custF) {
    stdKpis.push(makeStd({
      id: "std_unique_customers",
      name: "تعداد مشتریان یکتا",
      formula: "COUNT(DISTINCT <customer_id>)",
      display_format: "count",
      unit: "مشتری",
      computability_status: "needs_data",
      missing_fields_fa: ["شناسه یا نام مشتری (Customer ID)"],
      rejection_reason_fa: "ستون شناسه مشتری یافت نشد.",
      priority: 5,
    }));
  } else {
    const cFn = custF.field_name;
    const uniq = new Set(rows.map((r) => r[cFn]).filter((v) => v !== null && v !== undefined && v !== "")).size;
    stdKpis.push(makeStd({
      id: "std_unique_customers",
      name: "تعداد مشتریان یکتا",
      formula: `COUNT(DISTINCT "${cFn}")`,
      base_fields: [cFn],
      display_format: "count",
      unit: "مشتری",
      value: uniq,
      computability_status: uniq > 0 ? "computable" : "uncomputable",
      priority: 5,
    }));
  }

  // 6. Target Achievement
  const manualTarget = safeNum(targetValues["std_target_achievement"] ?? targetValues["target"]);
  if (!salesF || (!targetF && !(manualTarget && manualTarget > 0))) {
    stdKpis.push(makeStd({
      id: "std_target_achievement",
      name: "درصد تحقق هدف",
      formula: "COALESCE(SUM(<sales>), 0) * 100.0 / NULLIF(SUM(<target>), 0)",
      base_fields: salesF ? [salesF.field_name] : [],
      display_format: "percentage",
      unit: "%",
      computability_status: "needs_data",
      missing_fields_fa: ["ستون هدف / بودجه (Target) یا ثبت مقدار هدف دستی"],
      rejection_reason_fa: "فیلد هدف در داده موجود نیست؛ می‌توانید هدف دستی وارد کنید.",
      priority: 6,
    }));
  } else {
    const sFn = salesF.field_name;
    const sSum = rows.reduce((a, r) => a + (safeNum(r[sFn]) ?? 0), 0);
    const tSum = targetF
      ? rows.reduce((a, r) => a + (safeNum(r[targetF.field_name]) ?? 0), 0)
      : (manualTarget || 0);
    const pct = tSum > 0 ? (sSum * 100) / tSum : null;
    stdKpis.push(makeStd({
      id: "std_target_achievement",
      name: "درصد تحقق هدف",
      formula: targetF
        ? `COALESCE(SUM("${sFn}"), 0) * 100.0 / NULLIF(SUM("${targetF.field_name}"), 0)`
        : `COALESCE(SUM("${sFn}"), 0) * 100.0 / NULLIF(${manualTarget}, 0)`,
      base_fields: targetF ? [sFn, targetF.field_name] : [sFn],
      display_format: "percentage",
      unit: "%",
      target_value: tSum,
      comparison_mode: "target",
      value: pct !== null ? Math.round(pct * 100) / 100 : null,
      computability_status: pct === null ? "uncomputable" : "computable",
      priority: 6,
    }));
  }

  // 7. Delay Rate
  if (delayDaysF) {
    const dFn = delayDaysF.field_name;
    const elig = rows.filter((r) => safeNum(r[dFn]) !== null && String(r.status || "").toLowerCase() !== "cancelled");
    const del = elig.filter((r) => (safeNum(r[dFn]) ?? 0) > 0);
    const pct = elig.length > 0 ? (del.length * 100) / elig.length : null;
    stdKpis.push(makeStd({
      id: "std_delay_rate",
      name: "نرخ تأخیر",
      formula: `SUM(CASE WHEN "${dFn}" > 0 THEN 1.0 ELSE 0.0 END) * 100.0 / NULLIF(COUNT("${dFn}"), 0)`,
      base_fields: [dFn],
      display_format: "percentage",
      unit: "%",
      value: pct !== null ? Math.round(pct * 100) / 100 : null,
      eligible_definition_fa: `رکوردهای دارای «${delayDaysF.display_name || dFn}» معتبر (بجز لغوشده‌ها)`,
      delayed_definition_fa: `موارد با «${delayDaysF.display_name || dFn}» بزرگ‌تر از صفر`,
      computability_status: pct === null ? "uncomputable" : "computable",
      priority: 7,
    }));
  } else if (delivF && dueF) {
    const afn = delivF.field_name;
    const dfn = dueF.field_name;
    const elig = rows.filter((r) => r[afn] && r[dfn]);
    const del = elig.filter((r) => String(r[afn]) > String(r[dfn]));
    const pct = elig.length > 0 ? (del.length * 100) / elig.length : null;
    stdKpis.push(makeStd({
      id: "std_delay_rate",
      name: "نرخ تأخیر",
      formula: `SUM(CASE WHEN "${afn}" > "${dfn}" THEN 1.0 ELSE 0.0 END) * 100.0 / NULLIF(COUNT(*), 0)`,
      base_fields: [afn, dfn],
      display_format: "percentage",
      unit: "%",
      value: pct !== null ? Math.round(pct * 100) / 100 : null,
      eligible_definition_fa: "رکوردهای دارای تاریخ تحویل و تاریخ سررسید",
      delayed_definition_fa: "تاریخ تحویل دیرتر از تاریخ سررسید",
      computability_status: pct === null ? "uncomputable" : "computable",
      priority: 7,
    }));
  } else {
    stdKpis.push(makeStd({
      id: "std_delay_rate",
      name: "نرخ تأخیر",
      formula: "SUM(CASE WHEN <delayed> THEN 1.0 ELSE 0.0 END) * 100.0 / NULLIF(SUM(CASE WHEN <eligible> THEN 1.0 ELSE 0.0 END), 0)",
      display_format: "percentage",
      unit: "%",
      computability_status: "needs_data",
      missing_fields_fa: ["ستون‌های تاریخ تحویل و سررسید یا روز تأخیر"],
      rejection_reason_fa: "ستون‌های زمان تحویل/سررسید یا تأخیر برای محاسبه این KPI موجود نیست.",
      priority: 7,
    }));
  }

  return {
    standard: stdKpis,
    computableStandard: stdKpis.filter((k) => k.computability_status === "computable" || k.computability_status === "computable_with_warning"),
    needsData: stdKpis.filter((k) => k.computability_status === "needs_data"),
    uncomputable: stdKpis.filter((k) => k.computability_status === "uncomputable"),
  };
}
