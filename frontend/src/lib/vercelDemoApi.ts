import rawDemoData from "@/lib/vercelDemoData.json";

const demoData = rawDemoData as any;

type Row = Record<string, any>;

function getTableForDataset(datasetId?: string | null): { tableName: string; rows: Row[]; columns: string[] } {
  const ds = (demoData.datasets || []).find((d: any) => d.id === datasetId);
  const tableName = ds?.warehouse_table || "wh_enterprise_sales";
  const tbl = demoData.tables?.[tableName] || demoData.tables?.wh_enterprise_sales;
  return {
    tableName,
    rows: (tbl?.rows || []) as Row[],
    columns: (tbl?.columns || []) as string[],
  };
}

function resolveDemoUser(authHeader: string | null): { username: string; account: any } | null {
  if (!authHeader || !authHeader.startsWith("Bearer ")) return null;
  const tok = authHeader.slice(7).trim();
  if (!tok.startsWith("demo:")) return null;
  const username = tok.slice(5);
  const account = demoData.demo_accounts?.[username];
  if (!account) return null;
  return { username, account };
}

function applyRlsAndFilters(
  rows: Row[],
  username: string | null,
  filters: any[] = []
): Row[] {
  let out = rows;
  const account = username ? demoData.demo_accounts?.[username] : null;
  const userRegion = account?.login_response?.user?.region;
  if (userRegion && userRegion !== "All") {
    out = out.filter((r) => !("region" in r) || r.region === userRegion);
  }

  for (const f of filters || []) {
    if (f?.is_active === false) continue;
    const field = f?.field_name;
    const val = f?.value;
    if (!field || val === undefined || val === null || val === "" || (Array.isArray(val) && val.length === 0)) {
      continue;
    }
    const op = String(f?.operator || "eq").toLowerCase();
    out = out.filter((row) => {
      const rv = row[field];
      if (op === "in") {
        const arr = Array.isArray(val) ? val : [val];
        return arr.map(String).includes(String(rv));
      }
      if (op === "neq") return String(rv) !== String(val);
      if (op === "contains") return String(rv ?? "").toLowerCase().includes(String(val).toLowerCase());
      if (op === "gt") return Number(rv) > Number(val);
      if (op === "gte") return Number(rv) >= Number(val);
      if (op === "lt") return Number(rv) < Number(val);
      if (op === "lte") return Number(rv) <= Number(val);
      if (op === "between" && Array.isArray(val) && val.length >= 2) {
        return String(rv) >= String(val[0]) && String(rv) <= String(val[1]);
      }
      return String(rv) === String(val);
    });
  }
  return out;
}

function aggregateGroup(items: Row[], field: string, agg: string): number {
  const op = (agg || "SUM").toUpperCase();
  if (op === "COUNT") return items.length;
  if (!field || field === "*") return items.length;
  const nums = items.map((r) => Number(r[field] ?? 0)).filter((n) => !Number.isNaN(n));
  if (nums.length === 0) return 0;
  if (op === "AVG") return nums.reduce((a, b) => a + b, 0) / nums.length;
  if (op === "MIN") return Math.min(...nums);
  if (op === "MAX") return Math.max(...nums);
  return nums.reduce((a, b) => a + b, 0);
}

function executeVisualSpec(
  datasetId: string | null,
  spec: any,
  username: string | null,
  filters: any[] = [],
  overrideDimension: string | null = null,
  rawMode = false
) {
  const { rows: allRows, columns: tableCols } = getTableForDataset(datasetId);
  const filtered = applyRlsAndFilters(allRows, username, filters);
  const agg = String(spec?.aggregation || "SUM").toUpperCase();
  const limit = Number(spec?.limit || 200);

  if (rawMode || agg === "RAW") {
    const cleanCols = tableCols.filter((c) => c !== "id" && !c.endsWith("_id"));
    const cols =
      Array.isArray(spec?.columns) && spec.columns.length > 0
        ? spec.columns
        : (cleanCols.length > 0 ? cleanCols : tableCols).slice(0, 10);
    const sliced = filtered.slice(0, limit).map((r) => {
      const item: Row = {};
      for (const c of cols) item[c] = r[c];
      return item;
    });
    return { columns: cols, rows: sliced, row_count: sliced.length };
  }

  const xField = overrideDimension || spec?.x_field || null;
  const seriesField = !overrideDimension ? (spec?.series_field || null) : null;
  const yField = spec?.y_field || "revenue";
  const secField = spec?.secondary_y_field || null;
  const sizeField = spec?.size_field || null;

  if (!xField) {
    const val = aggregateGroup(filtered, yField, agg);
    return {
      columns: ["value"],
      rows: [{ value: Math.round(val * 10000) / 10000 }],
      row_count: 1,
    };
  }

  const groups = new Map<string, Row[]>();
  for (const r of filtered) {
    const k = seriesField ? `${String(r[xField])}\u0000${String(r[seriesField])}` : String(r[xField]);
    const list = groups.get(k);
    if (list) list.push(r);
    else groups.set(k, [r]);
  }

  const outRows: Row[] = [];
  for (const grp of Array.from(groups.values())) {
    const sample = grp[0];
    const row: Row = { [xField]: sample[xField] };
    if (seriesField) row[seriesField] = sample[seriesField];
    row.value = Math.round(aggregateGroup(grp, yField, agg) * 100) / 100;
    if (secField) {
      row.secondary_value = Math.round(aggregateGroup(grp, secField, spec?.secondary_aggregation || agg) * 100) / 100;
    }
    if (sizeField) {
      row.size_value = Math.round(aggregateGroup(grp, sizeField, "SUM") * 100) / 100;
    }
    outRows.push(row);
  }

  const timeFields = new Set(["year", "quarter", "month", "day"]);
  if (timeFields.has(xField)) {
    outRows.sort((a, b) => String(a[xField]).localeCompare(String(b[xField]), undefined, { numeric: true }));
  } else {
    outRows.sort((a, b) => Number(b.value) - Number(a.value));
  }

  const sliced = outRows.slice(0, limit);
  const cols = [xField];
  if (seriesField) cols.push(seriesField);
  cols.push("value");
  if (secField) cols.push("secondary_value");
  if (sizeField) cols.push("size_value");

  return { columns: cols, rows: sliced, row_count: sliced.length };
}

function executeWidgetForDemo(
  widget: any,
  username: string | null,
  activeFilters: any[] = [],
  crossFilters: any[] = [],
  drillStack: any[] = []
) {
  const drillRule = widget.drill_rule;
  const hierarchy = drillRule?.hierarchy || [];
  const levelRules = drillRule?.level_rules || [];
  const level = drillStack.length;
  let effectiveDim: string | null = null;
  let effectiveChartType = widget.chart_type;
  let isRaw = false;

  if (hierarchy.length > 0 && level < hierarchy.length) {
    const step = hierarchy[level];
    effectiveDim = typeof step === "object" ? step.field : String(step);
  } else if (hierarchy.length > 0 && level >= hierarchy.length) {
    isRaw = true;
    effectiveChartType = "table";
  }
  for (const lr of levelRules) {
    if (Number(lr.level) === level) {
      if (lr.chart_type) effectiveChartType = lr.chart_type;
      if (lr.field) effectiveDim = lr.field;
    }
  }

  const breadcrumbs: any[] = [{ level: 0, label: "All", field: null, value: null }];
  const drillFilters: any[] = [];
  drillStack.forEach((d, idx) => {
    if (d?.field && d?.value !== undefined) {
      drillFilters.push({ field_name: d.field, operator: "eq", value: d.value, is_active: true });
      breadcrumbs.push({ level: idx + 1, label: `${d.field}: ${d.value}`, field: d.field, value: d.value });
    }
  });

  const combinedFilters = [
    ...(activeFilters || []),
    ...(crossFilters || []).filter((c) => c?.source_widget_id !== widget.id),
    ...drillFilters,
  ];

  const spec = widget.query?.visual_spec || {};
  const res = executeVisualSpec(widget.dataset_id, spec, username, combinedFilters, effectiveDim, isRaw);

  return {
    widget_id: widget.id,
    effective_chart_type: effectiveChartType,
    drill: {
      level,
      breadcrumbs,
      can_drill_down: level < hierarchy.length,
      effective_dimension: effectiveDim,
      drill_through_page_id: drillRule?.drill_through_page_id || null,
    },
    freshness: widget.dataset_freshness || { status: "fresh", data_version: 2 },
    columns: res.columns,
    rows: res.rows,
    row_count: res.row_count,
    execution_ms: 4.2,
    cache_hit: false,
    data_version: 2,
    generated_sql: "-- Preview Demo Snapshot Engine",
  };
}

function executeSimpleDemoSql(sqlText: string, username: string | null) {
  const text = (sqlText || "").trim();
  const fromMatch = text.match(/\bFROM\s+([A-Za-z0-9_]+)/i);
  const tableName = fromMatch ? fromMatch[1] : "wh_enterprise_sales";
  const tbl = demoData.tables?.[tableName];
  if (!tbl) {
    return { status: 400, body: { detail: `Demo table '${tableName}' not found. Available: wh_enterprise_sales, wh_enterprise_expenses, wh_hr_workforce` } };
  }

  let rows: Row[] = applyRlsAndFilters(tbl.rows || [], username, []);

  // Optional simple WHERE col = 'val'
  const whereMatch = text.match(/\bWHERE\s+([\s\S]+?)(?:\bGROUP\s+BY\b|\bORDER\s+BY\b|\bLIMIT\b|$)/i);
  if (whereMatch) {
    const cond = whereMatch[1].trim();
    const eqMatch = cond.match(/([A-Za-z0-9_]+)\s*=\s*['"]?([^'"\s]+)['"]?/i);
    if (eqMatch) {
      const [, col, val] = eqMatch;
      rows = rows.filter((r) => String(r[col]) === String(val));
    }
  }

  const limitMatch = text.match(/\bLIMIT\s+(\d+)/i);
  const limit = limitMatch ? Math.min(500, Number(limitMatch[1])) : 200;

  const groupMatch = text.match(/\bGROUP\s+BY\s+([\s\S]+?)(?:\bORDER\s+BY\b|\bLIMIT\b|$)/i);
  const selectMatch = text.match(/^SELECT\s+([\s\S]+?)\s+FROM\b/i);
  const selectExpr = selectMatch ? selectMatch[1].trim() : "*";

  if (groupMatch) {
    const groupCols = groupMatch[1].split(",").map((s) => s.trim().split(/\s+/)[0]).filter(Boolean);
    const selectParts = selectExpr.split(",").map((s) => s.trim()).filter(Boolean);

    const bucketMap = new Map<string, Row[]>();
    for (const r of rows) {
      const key = groupCols.map((c) => String(r[c])).join("\u0000");
      const list = bucketMap.get(key);
      if (list) list.push(r);
      else bucketMap.set(key, [r]);
    }

    const outRows: Row[] = [];
    for (const grp of Array.from(bucketMap.values())) {
      const rec: Row = {};
      for (const part of selectParts) {
        const aliasMatch = part.match(/^(.+?)\s+AS\s+([A-Za-z0-9_]+)$/i);
        const expr = aliasMatch ? aliasMatch[1].trim() : part;
        const alias = aliasMatch ? aliasMatch[2].trim() : expr.replace(/[^A-Za-z0-9_]/g, "_");
        const fnMatch = expr.match(/^(SUM|AVG|COUNT|MIN|MAX)\s*\(\s*([A-Za-z0-9_*]+)\s*\)$/i);
        if (fnMatch) {
          rec[alias] = Math.round(aggregateGroup(grp, fnMatch[2], fnMatch[1]) * 100) / 100;
        } else {
          rec[aliasMatch ? alias : expr] = grp[0]?.[expr];
        }
      }
      outRows.push(rec);
    }

    const orderMatch = text.match(/\bORDER\s+BY\s+([A-Za-z0-9_]+)(?:\s+(ASC|DESC))?/i);
    if (orderMatch) {
      const [, col, dir] = orderMatch;
      const desc = (dir || "ASC").toUpperCase() === "DESC";
      outRows.sort((a, b) => {
        const av = a[col];
        const bv = b[col];
        if (typeof av === "number" && typeof bv === "number") return desc ? bv - av : av - bv;
        return desc ? String(bv).localeCompare(String(av)) : String(av).localeCompare(String(bv));
      });
    }

    const sliced = outRows.slice(0, limit);
    const cols = sliced[0] ? Object.keys(sliced[0]) : groupCols;
    return {
      status: 200,
      body: {
        columns: cols,
        rows: sliced,
        row_count: sliced.length,
        execution_ms: 5.1,
        generated_sql: text,
        explain_plan: [`Preview Demo Scan on ${tableName} (${rows.length} rows after RLS)`, `Hash Aggregate by (${groupCols.join(", ")})`],
      },
    };
  }

  const sliced = rows.slice(0, limit);
  let cols = tbl.columns || (sliced[0] ? Object.keys(sliced[0]) : []);
  if (selectExpr !== "*") {
    const picked = selectExpr.split(",").map((s) => s.trim().split(/\s+AS\s+/i)[0].trim()).filter((c) => cols.includes(c));
    if (picked.length > 0) cols = picked;
  }
  const projected = sliced.map((r) => {
    const o: Row = {};
    for (const c of cols) o[c] = r[c];
    return o;
  });
  return {
    status: 200,
    body: {
      columns: cols,
      rows: projected,
      row_count: projected.length,
      execution_ms: 3.4,
      generated_sql: text,
      explain_plan: [`Preview Demo Scan on ${tableName} (${projected.length} rows returned)`],
    },
  };
}

function findDashboard(id: string) {
  if (demoData.dashboards_detail?.[id]) return demoData.dashboards_detail[id];
  if (id.startsWith("clone__")) {
    const parts = id.split("__");
    const srcId = parts[1];
    const src = demoData.dashboards_detail?.[srcId];
    if (src) {
      return {
        ...src,
        id,
        title: `${src.title} (Template Copy)`,
        is_template: false,
        permissions: { ...(src.permissions || {}), view: true, edit: true, add_widget: true, change_theme: true, export: true },
      };
    }
  }
  return null;
}

function findWidget(wid: string) {
  for (const det of Object.values(demoData.dashboards_detail || {}) as any[]) {
    const w = (det.widgets || []).find((x: any) => x.id === wid);
    if (w) return { dash: det, widget: w };
  }
  return null;
}

function toCsv(columns: string[], rows: Row[]) {
  const esc = (v: any) => {
    const s = String(v ?? "");
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [columns.join(","), ...rows.map((r) => columns.map((c) => esc(r[c])).join(","))].join("\n");
}

function normalizeFaText(text: string): string {
  return (text || "")
    .trim()
    .replace(/ي/g, "ی")
    .replace(/ى/g, "ی")
    .replace(/ك/g, "ک")
    .replace(/\u200c/g, " ")
    .replace(/[۰-۹]/g, (d) => String("۰۱۲۳۴۵۶۷۸۹".indexOf(d)))
    .replace(/\s+/g, " ");
}

function extractChartTypeFromText(normLow: string): string | null {
  const rules: [string[], string][] = [
    [["میله ای افقی", "نمودار افقی", "ستونی افقی", "horizontal bar", "horizontal_bar"], "horizontal_bar"],
    [["میله ای پشته", "ستونی پشته", "پشته ای", "stacked bar", "stacked_bar"], "stacked_bar"],
    [["میله ای", "ستونی", "نمودار میله", "ویجت میله", "bar chart", "bar"], "bar"],
    [["مساحتی پشته", "stacked area", "stacked_area"], "stacked_area"],
    [["مساحتی", "سطحی", "area chart", "area"], "area"],
    [["خطی", "روند", "نمودار خط", "line chart", "line"], "line"],
    [["دونات", "حلقوی", "donut"], "donut"],
    [["دایره ای", "پای", "نمودار دایره", "pie chart", "pie"], "pie"],
    [["گیج", "عقربه ای", "سرعت سنج", "gauge"], "gauge"],
    [["جدول محوری", "پیوت", "pivot"], "pivot_table"],
    [["جدول", "لیست", "table"], "table"],
    [["آبشاری", "waterfall"], "waterfall"],
    [["قیف", "فانل", "funnel"], "funnel"],
    [["رادار", "عنکبوتی", "radar"], "radar"],
    [["پراکندگی", "scatter"], "scatter"],
    [["حبابی", "bubble"], "bubble"],
    [["درختی", "treemap"], "treemap"],
    [["نقشه", "جغرافیا", "geo_map", "map"], "geo_map"],
    [["حرارتی", "هیت مپ", "heatmap"], "heatmap"],
    [["ترکیبی", "combo"], "combo"],
    [["کارت شاخص", "کارت عدد", "تک عددی", "شاخص کل", "کارت kpi", "kpi"], "kpi"],
  ];
  for (const [kws, ct] of rules) {
    if (kws.some((kw) => normLow.includes(kw))) return ct;
  }
  return null;
}

function getDemoDatasetSchema(ds: any) {
  const profile = demoData.dataset_profiles?.[ds.id];
  if (profile?.columns?.length) {
    const colsInfo = profile.columns.map((c: any) => {
      const name = String(c.field_name || c.column_name);
      const dtype = String(c.data_type || "").toLowerCase();
      const role =
        c.semantic_role ||
        ((dtype.includes("int") || dtype.includes("double") || dtype.includes("float") || dtype.includes("decimal")) &&
        !name.endsWith("_id") &&
        !["year", "month", "quarter", "day"].includes(name)
          ? "measure"
          : ["year", "quarter", "month", "day", "date"].includes(name)
          ? "time"
          : "dimension");
      return {
        name,
        label: normalizeFaText(c.display_name || name.replace(/_/g, " ")),
        role,
      };
    });
    return {
      colsInfo,
      numCols: colsInfo.filter((c: any) => c.role === "measure").map((c: any) => c.name),
      dimCols: colsInfo
        .filter((c: any) => c.role !== "measure" && !c.name.endsWith("_id") && c.name !== "id")
        .map((c: any) => c.name),
    };
  }
  const tbl = demoData.tables?.[ds.warehouse_table] || { columns: [], rows: [] };
  const sample = tbl.rows?.[0] || {};
  const colsInfo = (tbl.columns || []).map((name: string) => {
    const isNum = typeof sample[name] === "number" && !name.endsWith("_id") && !["year", "month", "quarter", "day"].includes(name);
    const role = isNum ? "measure" : ["year", "quarter", "month", "day", "date"].includes(name) ? "time" : "dimension";
    return { name, label: name.replace(/_/g, " "), role };
  });
  return {
    colsInfo,
    numCols: colsInfo.filter((c: any) => c.role === "measure").map((c: any) => c.name),
    dimCols: colsInfo
      .filter((c: any) => c.role !== "measure" && !c.name.endsWith("_id") && c.name !== "id")
      .map((c: any) => c.name),
  };
}

function matchDemoColumn(mention: string, colsInfo: any[], roleFilter?: "measure" | "dim"): string | null {
  const mNorm = normalizeFaText(mention).toLowerCase().replace(/^[«»"'،,.؟?\s]+|[«»"'،,.؟?\s]+$/g, "");
  if (!mNorm) return null;
  const candidates =
    roleFilter === "measure"
      ? colsInfo.filter((c) => c.role === "measure")
      : roleFilter === "dim"
      ? colsInfo.filter((c) => c.role !== "measure" && !c.name.endsWith("_id") && c.name !== "id")
      : colsInfo;

  for (const c of candidates) {
    const cn = c.name.toLowerCase();
    const cl = c.label.toLowerCase();
    if (mNorm === cn || mNorm === cn.replace(/_/g, " ") || mNorm === cl) return c.name;
  }
  for (const c of candidates) {
    const cn = c.name.toLowerCase().replace(/_/g, " ");
    const cl = c.label.toLowerCase();
    if (mNorm.length >= 2 && (mNorm.includes(cn) || cn.includes(mNorm) || mNorm.includes(cl) || cl.includes(mNorm))) {
      return c.name;
    }
  }
  const syn: Record<string, string[]> = {
    "فروش": ["revenue", "sales", "total_sales"],
    "درامد": ["revenue", "income", "sales"],
    "درآمد": ["revenue", "income", "sales"],
    "سود": ["profit", "net_profit", "margin"],
    "هزینه": ["cost", "expense", "amount", "budget"],
    "بودجه": ["budget", "planned"],
    "تعداد": ["quantity", "qty", "units", "count", "headcount", "employees"],
    "مقدار": ["quantity", "amount", "value"],
    "مبلغ": ["amount", "revenue", "cost", "price", "total"],
    "حقوق": ["salary", "payroll", "wage"],
    "پیمانکار": ["contractor", "vendor", "supplier", "partner", "پیمانکار"],
    "نام پیمانکار": ["contractor_name", "contractor", "vendor_name", "vendor", "supplier", "پیمانکار"],
    "منطقه": ["region", "area", "zone"],
    "استان": ["province", "state", "region"],
    "شهر": ["city"],
    "کشور": ["country"],
    "محصول": ["product", "item"],
    "دسته": ["category", "group"],
    "دپارتمان": ["department", "dept"],
    "بخش": ["department", "division", "unit"],
    "کانال": ["channel"],
    "سال": ["year"],
    "فصل": ["quarter"],
    "ماه": ["month", "month_name"],
    "روز": ["day"],
    "تاریخ": ["order_date", "transaction_date", "date"],
  };
  for (const [fk, list] of Object.entries(syn)) {
    if (mNorm.includes(fk) || fk.includes(mNorm)) {
      for (const kw of list) {
        const hit = candidates.find((c) => c.name.toLowerCase().includes(kw) || c.label.toLowerCase().includes(kw));
        if (hit) return hit.name;
      }
    }
  }
  return null;
}

function handleTextToWidgetDemo(body: any, username: string | null) {
  const clar = { ...(body?.clarifications || {}) };
  const rawPrompt = String(body?.prompt || "").trim();
  const freeReply = normalizeFaText(String(clar.free_text || "").trim());
  const norm = normalizeFaText(rawPrompt);
  const datasets: any[] = demoData.datasets || [];

  if (datasets.length === 0) {
    return {
      status: "needs_clarification",
      message_fa: "هیچ منبع داده (دیتاست) در سیستم ثبت نشده است.",
      questions: [],
      parsed: {},
    };
  }

  let extractedTitle: string | null = clar.title || null;
  let titlePhrase: string | null = null;
  if (!extractedTitle) {
    const mTitle = norm.match(
      /(?:تحت\s+عنوان|با\s+عنوان|به\s+نام|با\s+نام|عنوان\s*[:：]|title\s*[:：])\s*["«']?([^"»'\n]+?)["»']?(?=\s*(?:بساز|ایجاد\s*کن|تولید\s*کن|درست\s*کن|اضافه\s*کن|بگذار|نشان\s*بده|نمایش\s*بده|$))/i
    );
    if (mTitle) {
      titlePhrase = mTitle[0];
      extractedTitle = mTitle[1].replace(/^[،,.«»"'\s]+|[،,.«»"'\s]+$/g, "");
    }
  }

  let sourceMention: string | null = null;
  const mSrc = norm.match(
    /(?:از\s+روی|از|روی)\s+(?:منبع\s+داده|منبع|دیتاست|جدول|source|dataset|table)\s+["«']?(.+?)["»']?(?=\s+(?:یک|یه|ویجت|نمودار|کارت|شاخص|جدول|بر\s+اساس|به\s+تفکیک|بر\s+حسب|تحت\s+عنوان|با\s+عنوان|بساز|ایجاد|تولید|by|per|$))/i
  );
  if (mSrc) sourceMention = mSrc[1].replace(/^[،,.«»"'\s]+|[،,.«»"'\s]+$/g, "");

  let semanticBody = norm;
  if (mSrc) semanticBody = semanticBody.replace(mSrc[0], " ");
  if (titlePhrase) semanticBody = semanticBody.replace(titlePhrase, " ");
  else if (extractedTitle) semanticBody = semanticBody.replace(extractedTitle, " ");
  if (freeReply) semanticBody = `${semanticBody} ${freeReply}`.trim();
  const semanticLow = normalizeFaText(semanticBody).toLowerCase();

  let dimMention: string | null = null;
  const mDim = semanticBody.match(
    /(?:بر\s+اساس|به\s+تفکیک|بر\s+حسب|برای\s+هر|روی\s+محور|by|per)\s+["«']?(.+?)["»']?(?=\s+(?:بساز|ایجاد|تولید|درست|اضافه|نشان|نمایش|تحت\s+عنوان|با\s+عنوان|به\s+نام|با\s+نام|از\s+روی|از\s+منبع|title|$))/i
  );
  if (mDim) dimMention = mDim[1].replace(/^[،,.«»"'\s]+|[،,.«»"'\s]+$/g, "");

  let chosenDs: any = null;
  const dsOverride = clar.dataset_id || body?.dataset_id;
  if (dsOverride) chosenDs = datasets.find((d) => d.id === dsOverride) || null;

  let sourceNotFoundName: string | null = null;
  if (!chosenDs && sourceMention) {
    const smLow = sourceMention.toLowerCase();
    let hits = datasets.filter(
      (d) =>
        smLow === d.name.toLowerCase() ||
        smLow.includes(d.name.toLowerCase()) ||
        d.name.toLowerCase().includes(smLow) ||
        smLow.includes(d.warehouse_table.toLowerCase())
    );
    if (hits.length === 0) {
      const srcSyn: Record<string, string[]> = {
        "فروش": ["sales", "revenue"],
        "هزینه": ["expense", "cost", "budget"],
        "منابع انسانی": ["hr", "workforce", "employee"],
        "نیروی انسانی": ["hr", "workforce", "employee"],
        "کارکنان": ["hr", "workforce", "employee"],
      };
      for (const [k, tgs] of Object.entries(srcSyn)) {
        if (smLow.includes(k)) {
          hits = datasets.filter((d) =>
            tgs.some((tg) => d.name.toLowerCase().includes(tg) || d.warehouse_table.toLowerCase().includes(tg))
          );
          if (hits.length) break;
        }
      }
    }
    if (hits.length === 1) chosenDs = hits[0];
    else if (hits.length === 0) sourceNotFoundName = sourceMention;
  }

  if (!chosenDs && !sourceNotFoundName) {
    if (datasets.length === 1) chosenDs = datasets[0];
    else {
      const matches = datasets.filter((d) => {
        const { colsInfo } = getDemoDatasetSchema(d);
        return dimMention
          ? !!matchDemoColumn(dimMention, colsInfo, "dim")
          : !!matchDemoColumn(semanticLow, colsInfo);
      });
      if (matches.length === 1) chosenDs = matches[0];
    }
  }

  const chartType = clar.chart_type || extractChartTypeFromText(semanticLow);
  let agg: string | null = clar.aggregation || null;
  if (!agg) {
    if (["تعداد", "شمارش", "count", "how many"].some((k) => semanticLow.includes(k))) agg = "COUNT";
    else if (["میانگین", "متوسط", "avg", "average", "mean"].some((k) => semanticLow.includes(k))) agg = "AVG";
    else if (["بیشترین", "حداکثر", "ماکزیمم", "max", "highest"].some((k) => semanticLow.includes(k))) agg = "MAX";
    else if (["کمترین", "حداقل", "مینیمم", "min", "lowest"].some((k) => semanticLow.includes(k))) agg = "MIN";
    else if (["مجموع", "جمع", "کل", "سرجمع", "sum", "total"].some((k) => semanticLow.includes(k))) agg = "SUM";
  }

  const questions: any[] = [];
  if (!chosenDs) {
    questions.push({
      key: "dataset_id",
      question: sourceNotFoundName
        ? `منبع داده «${sourceNotFoundName}» در سیستم پیدا نشد. لطفاً مشخص کنید از کدام منبع داده استفاده کنم؟`
        : "چند منبع داده در سیستم وجود دارد؛ کدام منبع داده (دیتاست) را برای ساخت این ویجت انتخاب می‌کنید؟",
      options: datasets.map((d) => ({ value: d.id, label: d.name, hint: `${d.row_count} ردیف` })),
    });
    return {
      status: "needs_clarification",
      message_fa: "برای ساخت دقیق ویجت، ابتدا منبع داده را مشخص کنید:",
      questions,
      parsed: { title: extractedTitle, chart_type: chartType, source_mention: sourceMention, dim_mention: dimMention, aggregation: agg },
    };
  }

  const { colsInfo, numCols, dimCols } = getDemoDatasetSchema(chosenDs);

  if (!chartType) {
    questions.push({
      key: "chart_type",
      question: "نوع ویجت / نمودار مشخص نیست. چه نوع نمایشی بسازم؟",
      options: [
        { value: "bar", label: "نمودار میله‌ای (Bar)" },
        { value: "kpi", label: "کارت شاخص عددی (KPI)" },
        { value: "line", label: "نمودار خطی (Line)" },
        { value: "pie", label: "نمودار دایره‌ای (Pie)" },
        { value: "table", label: "جدول داده (Table)" },
      ],
    });
  }

  let xField: string | null = clar.x_field || null;
  if (xField === "__none__") xField = null;
  else if (!xField && chartType !== "kpi" && chartType !== "gauge") {
    if (dimMention) {
      xField = matchDemoColumn(dimMention, colsInfo, "dim") || matchDemoColumn(dimMention, colsInfo);
      if (!xField) {
        questions.push({
          key: "x_field",
          question: `فیلد «${dimMention}» در منبع «${chosenDs.name}» پیدا نشد. کدام ستون را برای دسته‌بندی (محور X) انتخاب می‌کنید؟`,
          options: colsInfo.filter((c: any) => dimCols.includes(c.name)).slice(0, 10).map((c: any) => ({ value: c.name, label: c.label, hint: c.name })),
        });
      }
    } else {
      xField = matchDemoColumn(semanticLow, colsInfo, "dim");
      if (!xField && dimCols.length > 0 && chartType !== "table") {
        questions.push({
          key: "x_field",
          question: `نمودار بر اساس کدام ستون از «${chosenDs.name}» گروه‌بندی شود؟`,
          options: colsInfo.filter((c: any) => dimCols.includes(c.name)).slice(0, 10).map((c: any) => ({ value: c.name, label: c.label, hint: c.name })),
        });
      }
    }
  }

  let yField: string | null = clar.y_field || null;
  if (!yField) {
    const probe = dimMention ? semanticLow.replace(dimMention.toLowerCase(), " ") : semanticLow;
    yField = matchDemoColumn(probe, colsInfo, "measure");
  }
  if (agg === "COUNT" && !yField) yField = "*";
  if (!yField) {
    if (numCols.length === 1 && agg && ["SUM", "AVG", "MAX", "MIN"].includes(agg)) {
      yField = numCols[0];
    } else if (numCols.length === 0) {
      agg = agg || "COUNT";
      yField = "*";
    } else {
      questions.push({
        key: "y_field",
        question: `کدام شاخص/ستون عددی از منبع «${chosenDs.name}» محاسبه شود؟`,
        options: [
          { value: "__COUNT__", label: "تعداد رکوردها (شمارش / COUNT)", hint: "COUNT(*)" },
          ...numCols.slice(0, 6).map((nc: string) => {
            const lbl = colsInfo.find((c: any) => c.name === nc)?.label || nc;
            return { value: nc, label: `مجموع ${lbl}`, hint: `SUM(${nc})` };
          }),
        ],
      });
    }
  }

  if (yField === "__COUNT__") {
    yField = "*";
    agg = "COUNT";
  } else if (yField && !agg) {
    agg = yField === "*" ? "COUNT" : "SUM";
  }

  if (questions.length > 0) {
    return {
      status: "needs_clarification",
      message_fa: "برای تکمیل و ساخت دقیق ویجت، لطفاً موارد زیر را مشخص کنید:",
      questions,
      parsed: {
        dataset_id: chosenDs.id,
        dataset_name: chosenDs.name,
        title: extractedTitle,
        chart_type: chartType,
        x_field: xField,
        y_field: yField,
        aggregation: agg,
      },
    };
  }

  const effChart = chartType || "bar";
  const effAgg = agg || (yField === "*" ? "COUNT" : "SUM");
  const effY = yField || "*";
  const finalTitle =
    extractedTitle ||
    (xField ? `${effY === "*" ? "تعداد" : effY} بر اساس ${xField}` : `${effAgg} ${effY === "*" ? "تعداد" : effY}`);

  const visualSpec: any = { y_field: effY, aggregation: effAgg, limit: 100 };
  if (xField && effChart !== "kpi" && effChart !== "gauge") visualSpec.x_field = xField;
  const isCurr = ["rev", "cost", "profit", "price", "amount", "salary", "budget"].some((k) => effY.toLowerCase().includes(k));
  const styleCfg = { number_format: isCurr && effAgg !== "COUNT" ? "currency" : "number" };
  const vizCfg = { show_legend: true, show_tooltip: true, data_labels: false };
  const previewData = executeVisualSpec(chosenDs.id, visualSpec, username, []);

  let createdWidget: any = null;
  if (body?.create_widget !== false && body?.dashboard_id) {
    const det = findDashboard(body.dashboard_id);
    const targetPageId = body.page_id || det?.pages?.[0]?.id || "page-1";
    const existing = (det?.widgets || []).filter((w: any) => w.page_id === targetPageId);
    const nextY = existing.reduce((mx: number, w: any) => Math.max(mx, Number(w.layout?.y || 0) + Number(w.layout?.h || 0)), 0);
    const wid = `aiw_${Date.now()}_${Math.floor(Math.random() * 1000)}`;
    createdWidget = {
      id: wid,
      dashboard_id: body.dashboard_id,
      page_id: targetPageId,
      title: finalTitle,
      description: `AI Text-to-Widget (${chosenDs.name})`,
      chart_type: effChart,
      dataset_id: chosenDs.id,
      dataset_freshness: { status: "fresh", last_refreshed_at: new Date().toISOString(), row_count: chosenDs.row_count || 0, data_version: 1 },
      layout: { x: 0, y: nextY, w: effChart === "kpi" || effChart === "gauge" ? 4 : 6, h: effChart === "kpi" || effChart === "gauge" ? 3 : 7 },
      config: { style: styleCfg, visualization: vizCfg, interactions: { cross_filter: true }, advanced: { ai_text_prompt: `${rawPrompt} ${freeReply}`.trim() } },
      query: { query_mode: "visual", sql_text: null, visual_spec: visualSpec, parameters: {} },
      drill_rule: { hierarchy: [], level_rules: [], drill_through_page_id: null },
      widget_filters: [],
      permissions: { view: true, edit: true, delete: true, drill_down: true },
    };
    if (det && Array.isArray(det.widgets)) {
      det.widgets.push(createdWidget);
    }
  }

  return {
    status: createdWidget ? "created" : "ready",
    message_fa: createdWidget
      ? `ویجت «${finalTitle}» از منبع «${chosenDs.name}» با موفقیت ساخته شد و به داشبورد اضافه گردید.`
      : `پیکربندی ویجت «${finalTitle}» از منبع «${chosenDs.name}» آماده شد.`,
    widget: createdWidget,
    widget_spec: {
      title: finalTitle,
      chart_type: effChart,
      dataset_id: chosenDs.id,
      dataset_name: chosenDs.name,
      visual_spec: visualSpec,
      style: styleCfg,
      visualization: vizCfg,
    },
    generated_sql: `SELECT ${xField ? `${xField}, ` : ""}${effAgg}(${effY}) AS value FROM ${chosenDs.warehouse_table}${xField ? ` GROUP BY ${xField}` : ""}`,
    preview_data: previewData,
  };
}

export async function handleVercelDemoApi(
  method: string,
  segments: string[],
  searchParams: URLSearchParams,
  body: any,
  authHeader: string | null
): Promise<{ status: number; body?: any; textBody?: string; contentType?: string }> {
  const path = "/" + segments.join("/");
  const auth = resolveDemoUser(authHeader);
  const username = auth?.username || null;

  // Health & Auth
  if (method === "GET" && path === "/health") {
    return { status: 200, body: { status: "healthy", api_version: "1.0.0-preview" } };
  }
  if (method === "GET" && path === "/auth/status") {
    return {
      status: 200,
      body: {
        needs_initial_setup: false,
        demo_mode: true,
        setup_token_required: false,
        environment: "vercel-preview-demo",
      },
    };
  }
  if (method === "POST" && path === "/auth/login") {
    const uname = String(body?.username || "").trim();
    const pwd = String(body?.password || "");
    const acc = demoData.demo_accounts?.[uname];
    if (!acc || acc.password !== pwd) {
      return { status: 401, body: { detail: "Invalid username or password. Try admin / Admin!2026." } };
    }
    const tok = `demo:${uname}`;
    return {
      status: 200,
      body: {
        ...acc.login_response,
        access_token: tok,
        refresh_token: tok,
        token_type: "bearer",
      },
    };
  }
  if (method === "GET" && path === "/auth/me") {
    if (!auth) return { status: 401, body: { detail: "Not authenticated" } };
    return { status: 200, body: auth.account.me };
  }
  if (method === "POST" && path === "/auth/logout") {
    return { status: 200, body: { status: "logged_out" } };
  }
  if (method === "POST" && path === "/auth/mfa/setup") {
    return { status: 200, body: { secret: "JBSWY3DPEHPK3PXP", provisioning_uri: "otpauth://totp/NexusBI:demo?secret=JBSWY3DPEHPK3PXP&issuer=NexusBI" } };
  }
  if (method === "POST" && path === "/auth/mfa/enable") {
    return { status: 200, body: { mfa_enabled: true } };
  }

  // Public embed dashboard (no auth required)
  if (method === "GET" && segments[0] === "dashboards" && segments[1] === "public" && segments[2] && segments.length === 3) {
    const token = segments[2];
    const detMatch = Object.values(demoData.dashboards_detail || {}).find(
      (d: any) => d.is_public && d.embed_token === token
    ) as any;
    const pub = demoData.public_dashboards?.[token];
    if (!detMatch && !pub) return { status: 404, body: { detail: "Public dashboard not found" } };
    if (detMatch) {
      return {
        status: 200,
        body: {
          id: detMatch.id,
          title: detMatch.title,
          description: detMatch.description,
          theme_id: detMatch.theme_id,
          theme: detMatch.theme || pub?.theme || null,
          themes: demoData.themes || [],
          pages: (detMatch.pages || []).map((p: any) => ({ id: p.id, title: p.title })),
          widgets: (detMatch.widgets || []).map((w: any) => {
            const res = executeWidgetForDemo(w, null, [], [], []);
            return {
              id: w.id,
              page_id: w.page_id,
              title: w.title,
              chart_type: w.chart_type,
              layout: w.layout,
              config: w.config,
              query: w.query,
              drill_rule: w.drill_rule,
              data: { columns: res.columns, rows: res.rows, row_count: res.row_count },
            };
          }),
        },
      };
    }
    return { status: 200, body: { ...pub, themes: demoData.themes || [] } };
  }
  if (method === "POST" && segments[0] === "dashboards" && segments[1] === "public" && segments[2] && segments[3] === "execute-batch") {
    const token = segments[2];
    const detMatch = Object.values(demoData.dashboards_detail || {}).find(
      (d: any) => d.is_public && d.embed_token === token
    ) as any;
    if (!detMatch) return { status: 404, body: { detail: "Public dashboard not found" } };
    const widgets: Record<string, any> = {};
    for (const w of detMatch.widgets || []) {
      const hierarchy = w.drill_rule?.hierarchy || [];
      const rawStack = body?.drill_states?.[w.id] || [];
      const dStack = hierarchy.length > 0 ? rawStack.slice(0, Math.max(0, hierarchy.length - 1)) : [];
      widgets[w.id] = executeWidgetForDemo(w, null, body?.active_filters || [], body?.cross_filters || [], dStack);
    }
    return {
      status: 200,
      body: {
        dashboard_id: detMatch.id,
        load_ms: 9.4,
        widgets,
      },
    };
  }

  // Themes (accessible on login too)
  if (method === "GET" && path === "/themes") {
    return { status: 200, body: demoData.themes || [] };
  }
  if (method === "GET" && segments[0] === "themes" && segments[2] === "export") {
    const th = (demoData.themes || []).find((t: any) => t.id === segments[1]);
    return { status: 200, body: th || {} };
  }

  if (!auth) {
    return { status: 401, body: { detail: "Authentication required" } };
  }

  // Dashboards
  if (method === "GET" && path === "/dashboards") {
    return { status: 200, body: demoData.dashboards_list || [] };
  }
  if (method === "GET" && path === "/dashboards/templates") {
    const tpls = (demoData.dashboards_list || []).filter((d: any) => d.is_template);
    return { status: 200, body: tpls };
  }
  if (method === "POST" && path === "/dashboards") {
    const firstId = demoData.dashboards_list?.[0]?.id || "e3026158-f84f-47ca-a775-e5f0ca337d33";
    const newId = `clone__${firstId}__${Date.now()}`;
    return { status: 200, body: { id: newId, title: body?.title || "New Dashboard" } };
  }
  if (method === "GET" && segments[0] === "dashboards" && segments.length === 2) {
    const det = findDashboard(segments[1]);
    return det ? { status: 200, body: det } : { status: 404, body: { detail: "Dashboard not found" } };
  }
  if (method === "PUT" && segments[0] === "dashboards" && segments.length === 2) {
    const det = findDashboard(segments[1]);
    if (!det) return { status: 404, body: { detail: "Dashboard not found" } };
    if (body?.theme_id) {
      const th = (demoData.themes || []).find((x: any) => x.id === body.theme_id);
      if (th) {
        det.theme_id = th.id;
        det.theme = { id: th.id, name: th.name, slug: th.slug, mode: th.mode, tokens: th.tokens };
        const listEntry = (demoData.dashboards_list || []).find((x: any) => x.id === det.id);
        if (listEntry) listEntry.theme_id = th.id;
      }
    }
    if (body?.title !== undefined) det.title = body.title;
    if (body?.description !== undefined) det.description = body.description;
    if (body?.is_public !== undefined) det.is_public = body.is_public;
    return { status: 200, body: { status: "updated", theme_id: det.theme_id, theme: det.theme } };
  }
  if (method === "POST" && segments[0] === "dashboards" && segments[2] === "clone-template") {
    const det = findDashboard(segments[1]);
    if (!det) return { status: 404, body: { detail: "Template not found" } };
    const srcId = det.id.startsWith("clone__") ? det.id.split("__")[1] : det.id;
    return { status: 200, body: { id: `clone__${srcId}__${Date.now()}`, title: `${det.title} (Copy)` } };
  }
  if (method === "POST" && segments[0] === "dashboards" && segments[2] === "execute-batch") {
    const det = findDashboard(segments[1]);
    if (!det) return { status: 404, body: { detail: "Dashboard not found" } };
    const widgets: Record<string, any> = {};
    const pageId = body?.page_id;
    for (const w of det.widgets || []) {
      if (pageId && w.page_id !== pageId) continue;
      const dStack = body?.drill_states?.[w.id] || [];
      widgets[w.id] = executeWidgetForDemo(w, username, body?.active_filters || [], body?.cross_filters || [], dStack);
    }
    return {
      status: 200,
      body: {
        dashboard_id: det.id,
        load_ms: 14.8,
        cache_hits: 0,
        cache_misses: Object.keys(widgets).length,
        widgets,
      },
    };
  }
  if (method === "POST" && segments[0] === "dashboards" && segments[1] === "widgets" && segments[3] === "data") {
    const found = findWidget(segments[2]);
    if (!found) return { status: 404, body: { detail: "Widget not found" } };
    return {
      status: 200,
      body: executeWidgetForDemo(
        found.widget,
        username,
        body?.active_filters || [],
        body?.cross_filters || [],
        body?.drill_stack || []
      ),
    };
  }
  if (method === "POST" && segments[0] === "dashboards" && segments[1] === "widgets" && segments[3] === "switch-chart") {
    const found = findWidget(segments[2]);
    if (!found) return { status: 404, body: { detail: "Widget not found" } };
    return { status: 200, body: { ...found.widget, chart_type: body?.target_chart_type || found.widget.chart_type } };
  }
  if (method === "GET" && segments[0] === "dashboards" && segments[2] === "state") {
    return { status: 200, body: { active_page_id: null, filter_state: {}, drill_state: {}, widget_overrides: {} } };
  }
  if (method === "PUT" && segments[0] === "dashboards" && segments[2] === "state") {
    return { status: 200, body: { status: "state_saved" } };
  }
  if (method === "GET" && segments[0] === "dashboards" && segments[2] === "versions") {
    return { status: 200, body: [{ id: "v1", version_number: 1, label: "Baseline Seeded Snapshot", created_at: "2026-11-16T08:30:00Z" }] };
  }
  if (method === "GET" && segments[0] === "dashboards" && segments[2] === "comments") {
    return { status: 200, body: [{ id: "c1", widget_id: null, author_name: "System Administrator", content: "Q4 executive KPIs synced with warehouse.", created_at: "2026-11-16T09:00:00Z" }] };
  }
  if (method === "POST" && segments[0] === "dashboards" && segments[2] === "share") {
    const det = findDashboard(segments[1]);
    return { status: 200, body: { embed_token: det?.embed_token || "_TQsvwNqM9Bt93IWvC5Vjr6RC3hkVi-A", is_public: true } };
  }

  // Datasets & Semantic Layer
  if (method === "GET" && path === "/datasets") {
    return { status: 200, body: demoData.datasets || [] };
  }
  if (method === "GET" && path === "/datasets/semantic-metrics") {
    return { status: 200, body: demoData.semantic_metrics || [] };
  }
  if (method === "GET" && path === "/datasets/relationships") {
    return { status: 200, body: demoData.relationships || [] };
  }
  if (method === "GET" && segments[0] === "datasets" && segments[2] === "profile") {
    return { status: 200, body: demoData.dataset_profiles?.[segments[1]] || { columns: [], row_count: 0 } };
  }
  if (method === "GET" && segments[0] === "datasets" && segments[2] === "lineage") {
    return { status: 200, body: demoData.dataset_lineage?.[segments[1]] || { nodes: [], edges: [] } };
  }
  if (method === "GET" && segments[0] === "datasets" && segments[2] === "preview") {
    const limit = Number(searchParams.get("limit") || 30);
    const { rows, columns } = getTableForDataset(segments[1]);
    const visible = applyRlsAndFilters(rows, username, []).slice(0, limit);
    return { status: 200, body: { columns, rows: visible, row_count: visible.length } };
  }
  if (method === "POST" && segments[0] === "datasets" && segments[2] === "filter-options") {
    const { rows } = getTableForDataset(segments[1]);
    const fromArray = Array.isArray(body?.parent_filters) ? body.parent_filters : [];
    const fromMap = Object.entries(body?.parent_values || {})
      .filter(([, v]) => v !== null && v !== undefined && (!Array.isArray(v) || v.length > 0))
      .map(([k, v]) => ({ field_name: k, operator: Array.isArray(v) ? "in" : "eq", value: v, is_active: true }));
    const visible = applyRlsAndFilters(rows, username, [...fromArray, ...fromMap]);
    const field = body?.field_name;
    const limit = Number(body?.limit || 500);
    const vals = Array.from(new Set(visible.map((r) => r[field]).filter((v) => v !== undefined && v !== null)))
      .sort((a, b) => String(a).localeCompare(String(b), undefined, { numeric: true }))
      .slice(0, limit);
    return { status: 200, body: { field_name: field, values: vals } };
  }

  // Data Sources
  if (method === "GET" && path === "/data-sources") {
    return { status: 200, body: demoData.data_sources || [] };
  }
  if (method === "GET" && path === "/data-sources/supported-types") {
    return { status: 200, body: demoData.supported_types || { databases: [], files: [] } };
  }
  if (method === "POST" && segments[0] === "data-sources" && segments[2] === "test") {
    return { status: 200, body: { ok: true, latency_ms: 4.1, message: "Preview ERP SQLite source reachable (demo snapshot)" } };
  }
  if (method === "GET" && segments[0] === "data-sources" && segments[2] === "schema") {
    return {
      status: 200,
      body: {
        source_id: segments[1],
        tables: [
          { name: "erp_orders", columns: ["order_id", "order_date", "region", "product", "revenue", "profit"] },
          { name: "erp_expenses", columns: ["expense_id", "department", "expense_category", "budget_amount", "actual_amount"] },
        ],
      },
    };
  }

  // Queries
  if (method === "GET" && path === "/queries/saved") {
    return { status: 200, body: demoData.queries_saved || [] };
  }
  if (method === "GET" && path === "/queries/history") {
    return { status: 200, body: demoData.queries_history || [] };
  }
  if (method === "POST" && path === "/queries/execute-visual") {
    const res = executeVisualSpec(body?.dataset_id, body?.visual_spec || {}, username, body?.filters || []);
    return { status: 200, body: { ...res, execution_ms: 4.8, generated_sql: "-- Preview Visual Query" } };
  }
  if (method === "POST" && path === "/queries/execute-sql") {
    return executeSimpleDemoSql(body?.sql_text || "", username);
  }
  if (method === "POST" && path === "/queries/format-sql") {
    return { status: 200, body: { formatted_sql: String(body?.sql_text || "").trim() } };
  }

  // ETL
  if (method === "GET" && path === "/etl/monitor") {
    return { status: 200, body: demoData.etl_monitor || { kpis: {}, pipelines: [], recent_runs: [], recent_errors: [], materialized_views: [] } };
  }
  if (method === "POST" && segments[0] === "etl" && segments[1] === "pipelines" && segments[3] === "run") {
    return { status: 200, body: { status: "success", rows_processed: 1200, duration_ms: 42 } };
  }

  // RBAC & Security
  if (method === "GET" && path === "/rbac/users") {
    return { status: 200, body: demoData.rbac_users || [] };
  }
  if (method === "GET" && path === "/rbac/roles") {
    return { status: 200, body: demoData.rbac_roles || { roles: [], catalog: {} } };
  }
  if (method === "GET" && path === "/rbac/permissions/matrix") {
    return { status: 200, body: demoData.rbac_matrix || { rules: [], dashboards: [], widgets: [] } };
  }
  if (method === "GET" && path === "/rbac/ad/connections") {
    return { status: 200, body: demoData.rbac_ad || { connections: [], sync_jobs: [] } };
  }
  if (method === "POST" && segments[0] === "rbac" && segments[1] === "ad" && segments[4] === "test") {
    return { status: 200, body: { ok: true, message: "Mock Enterprise AD reachable (Preview Demo)" } };
  }
  if (method === "GET" && segments[0] === "rbac" && segments[1] === "ad" && segments[4] === "search") {
    const q = (searchParams.get("q") || "").toLowerCase();
    const list = (demoData.rbac_users || []).filter((u: any) => u.auth_source === "active_directory" && (!q || u.username.toLowerCase().includes(q)));
    return { status: 200, body: list };
  }
  if (method === "GET" && path === "/rbac/policies") {
    return { status: 200, body: demoData.rbac_policies || [] };
  }

  // Monitoring & Audit
  if (method === "GET" && path === "/monitoring/performance") {
    return { status: 200, body: demoData.monitoring_perf || {} };
  }
  if (method === "GET" && path === "/monitoring/audit-logs") {
    const q = (searchParams.get("search") || "").toLowerCase();
    const logs = (demoData.monitoring_audit || []).filter((l: any) =>
      !q || JSON.stringify(l).toLowerCase().includes(q)
    );
    return { status: 200, body: logs };
  }
  if (method === "GET" && path === "/monitoring/audit-logs/export") {
    const logs = demoData.monitoring_audit || [];
    const cols = logs[0] ? Object.keys(logs[0]) : ["id", "action", "username", "status", "created_at"];
    return { status: 200, textBody: toCsv(cols, logs), contentType: "text/csv" };
  }
  if (method === "POST" && path === "/monitoring/cache/invalidate") {
    return { status: 200, body: { status: "invalidated", cleared_entries: 18 } };
  }

  // Exports
  if (method === "GET" && path === "/exports/scheduled-reports") {
    return { status: 200, body: [] };
  }
  if (method === "GET" && segments[0] === "exports" && segments[1] === "widgets" && segments[2] && segments[3] === "csv") {
    const found = findWidget(segments[2]);
    if (!found) return { status: 404, body: { detail: "Widget not found" } };
    const res = executeWidgetForDemo(found.widget, username);
    return { status: 200, textBody: toCsv(res.columns, res.rows), contentType: "text/csv" };
  }
  if (method === "GET" && segments[0] === "exports" && segments[1] === "dashboards" && segments[2] && segments[3] === "csv") {
    const det = findDashboard(segments[2]);
    if (!det) return { status: 404, body: { detail: "Dashboard not found" } };
    const firstTable = det.widgets?.find((w: any) => w.dataset_id) || det.widgets?.[0];
    const res = firstTable ? executeWidgetForDemo(firstTable, username) : { columns: [], rows: [] };
    return { status: 200, textBody: toCsv(res.columns, res.rows), contentType: "text/csv" };
  }

  // AI Analytics
  if (method === "POST" && path === "/ai/nlq") {
    const question = String(body?.question || "").toLowerCase();
    let xField = "region";
    let yField = "revenue";
    let chartType = "bar";
    if (question.includes("product")) xField = "product";
    else if (question.includes("channel")) xField = "channel";
    else if (question.includes("year") || question.includes("trend") || question.includes("روند")) { xField = "year"; chartType = "area"; }
    if (question.includes("profit") || question.includes("سود")) yField = "profit";
    const res = executeVisualSpec(body?.dataset_id || null, { x_field: xField, y_field: yField, aggregation: "SUM" }, username, []);
    return {
      status: 200,
      body: {
        question: body?.question,
        recommended_chart_type: chartType,
        generated_sql: `SELECT ${xField}, SUM(${yField}) AS value FROM wh_enterprise_sales GROUP BY ${xField}`,
        ...res,
      },
    };
  }
  if (method === "POST" && path === "/ai/insights") {
    const vals: number[] = Array.isArray(body?.values) ? body.values.map(Number) : [];
    const avg = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : 0;
    return {
      status: 200,
      body: {
        summary: `Analyzed ${vals.length} data points (mean ${Math.round(avg).toLocaleString()}).`,
        anomalies: [],
        forecast: vals.slice(-3).map((v, i) => ({ label: `Forecast +${i + 1}`, value: Math.round(v * 1.04) })),
      },
    };
  }
  if (method === "POST" && path === "/ai/generate-dashboard") {
    const first = demoData.dashboards_list?.[0];
    return { status: 200, body: { dashboard_id: first?.id, title: first?.title || "Executive Dashboard" } };
  }
  if (method === "POST" && path === "/ai/text-to-widget") {
    return { status: 200, body: handleTextToWidgetDemo(body, username) };
  }

  // Fallback for preview demo mutations (read-only preview note)
  if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
    return {
      status: 200,
      body: {
        status: "preview_demo_ok",
        message: "Demo Preview mode: changes are simulated in-session (connect BACKEND_INTERNAL_URL for persistent storage).",
      },
    };
  }

  return { status: 404, body: { detail: `Endpoint ${path} not available in preview demo` } };
}
