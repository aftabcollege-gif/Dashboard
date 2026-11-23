type Row = Record<string, any>;

export const DEMO_FUNCTION_CATALOG = [
  { name: "SUM", category: "aggregate", signature: "SUM(column)", example: "SUM(revenue)", description_fa: "مجموع مقادیر عددی ستون در هر گروه یا کل داده‌ها.", is_aggregate: true, requires_min_args: 1, max_args: 1 },
  { name: "AVG", category: "aggregate", signature: "AVG(column)", example: "AVG(revenue)", description_fa: "میانگین حسابی مقادیر غیرخالی ستون.", is_aggregate: true, requires_min_args: 1, max_args: 1 },
  { name: "COUNT", category: "aggregate", signature: "COUNT(column)", example: "COUNT(id)", description_fa: "تعداد رکوردهای غیرخالی.", is_aggregate: true, requires_min_args: 1, max_args: 1 },
  { name: "COUNT_DISTINCT", category: "aggregate", signature: "COUNT_DISTINCT(column)", example: "COUNT_DISTINCT(region)", description_fa: "تعداد مقادیر یکتا و متمایز.", is_aggregate: true, requires_min_args: 1, max_args: 1 },
  { name: "MIN", category: "aggregate", signature: "MIN(column)", example: "MIN(revenue)", description_fa: "کمترین مقدار ستون.", is_aggregate: true, requires_min_args: 1, max_args: 1 },
  { name: "MAX", category: "aggregate", signature: "MAX(column)", example: "MAX(revenue)", description_fa: "بیشترین مقدار ستون.", is_aggregate: true, requires_min_args: 1, max_args: 1 },
  { name: "ROUND", category: "math", signature: "ROUND(value, decimals)", example: "ROUND(revenue / NULLIF(quantity, 0), 2)", description_fa: "گرد کردن عدد به تعداد اعشار دلخواه.", is_aggregate: false, requires_min_args: 1, max_args: 2 },
  { name: "ABS", category: "math", signature: "ABS(value)", example: "ABS(profit)", description_fa: "قدر مطلق مقدار عددی.", is_aggregate: false, requires_min_args: 1, max_args: 1 },
  { name: "NULLIF", category: "conditional", signature: "NULLIF(a, b)", example: "revenue / NULLIF(quantity, 0)", description_fa: "در صورت برابری با صفر مقدار NULL برمی‌گرداند تا خطای تقسیم بر صفر رخ ندهد.", is_aggregate: false, requires_min_args: 2, max_args: 2 },
  { name: "COALESCE", category: "conditional", signature: "COALESCE(a, b, ...)", example: "COALESCE(discount, 0)", description_fa: "اولین مقدار غیرخالی را برمی‌گرداند.", is_aggregate: false, requires_min_args: 2, max_args: 8 },
  { name: "JALALI_DATE", category: "date", signature: "JALALI_DATE(date_col)", example: "JALALI_DATE(order_date)", description_fa: "تبدیل تاریخ میلادی به تاریخ شمسی (YYYY/MM/DD).", is_aggregate: false, requires_min_args: 1, max_args: 1 },
  { name: "JALALI_YEAR", category: "date", signature: "JALALI_YEAR(date_col)", example: "JALALI_YEAR(order_date)", description_fa: "استخراج سال شمسی (مثلاً 1404).", is_aggregate: false, requires_min_args: 1, max_args: 1 },
  { name: "JALALI_MONTH", category: "date", signature: "JALALI_MONTH(date_col)", example: "JALALI_MONTH(order_date)", description_fa: "استخراج شماره ماه شمسی (۱ تا ۱۲).", is_aggregate: false, requires_min_args: 1, max_args: 1 },
  { name: "LOWER", category: "string", signature: "LOWER(text)", example: "LOWER(region)", description_fa: "تبدیل متن به حروف کوچک.", is_aggregate: false, requires_min_args: 1, max_args: 1 },
  { name: "TRIM", category: "string", signature: "TRIM(text)", example: "TRIM(region)", description_fa: "حذف فاصله‌های اضافه ابتدا و انتهای متن.", is_aggregate: false, requires_min_args: 1, max_args: 1 },
];

export const DEMO_ANALYSIS_CATALOG = [
  { id: "kpi", name_fa: "شاخص‌های کلیدی عملکرد (KPI)", category: "summary", recommended_charts: ["kpi", "gauge"], min_measures: 1, requires_time: false },
  { id: "distribution", name_fa: "توزیع و فراوانی داده‌ها", category: "distribution", recommended_charts: ["histogram", "bar", "boxplot"], min_measures: 1, requires_time: false },
  { id: "trend", name_fa: "روند تغییرات در طول زمان", category: "time", recommended_charts: ["line", "area"], min_measures: 1, requires_time: true },
  { id: "change", name_fa: "تغییرات دوره‌ای و نوسان", category: "time", recommended_charts: ["waterfall", "bar", "line"], min_measures: 1, requires_time: true },
  { id: "period_comparison", name_fa: "مقایسه بین دوره‌های زمانی", category: "time", recommended_charts: ["combo", "bar", "line"], min_measures: 1, requires_time: true },
  { id: "org_unit_comparison", name_fa: "مقایسه واحدها، مناطق یا گروه‌ها", category: "comparison", recommended_charts: ["bar", "horizontal_bar", "stacked_bar"], min_measures: 1, requires_time: false },
  { id: "ranking", name_fa: "رتبه‌بندی برترین‌ها و ضعیف‌ترین‌ها", category: "ranking", recommended_charts: ["horizontal_bar", "bar", "table"], min_measures: 1, requires_time: false },
  { id: "share", name_fa: "سهم از کل (درصدی)", category: "composition", recommended_charts: ["donut", "pie", "horizontal_bar"], min_measures: 1, requires_time: false },
  { id: "composition_ratio", name_fa: "ترکیب زیرمجموعه‌ها و نسبت‌ها", category: "composition", recommended_charts: ["stacked_bar", "treemap", "stacked_bar_100"], min_measures: 1, requires_time: false },
  { id: "correlation", name_fa: "همبستگی بین دو شاخص عددی", category: "statistical", recommended_charts: ["scatter", "bubble"], min_measures: 2, requires_time: false },
  { id: "outlier_detection", name_fa: "شناسایی داده‌های پرت (Outliers)", category: "quality", recommended_charts: ["boxplot", "scatter", "table"], min_measures: 1, requires_time: false },
  { id: "missing_data_audit", name_fa: "ممیزی داده‌های گمشده و کیفیت ستون‌ها", category: "quality", recommended_charts: ["horizontal_bar", "table"], min_measures: 0, requires_time: false },
  { id: "growth_decline", name_fa: "رشد و افت شاخص‌ها", category: "time", recommended_charts: ["area", "line", "waterfall"], min_measures: 1, requires_time: true },
  { id: "performance", name_fa: "ارزیابی عملکرد کلی و جزئیات", category: "summary", recommended_charts: ["combo", "table", "pivot_table"], min_measures: 1, requires_time: false },
  { id: "target_vs_actual", name_fa: "مقایسه عملکرد واقعی در برابر هدف", category: "target", recommended_charts: ["gauge", "progress", "combo"], min_measures: 1, requires_time: false },
];

const FORBIDDEN_TOKENS = /\b(select|insert|update|delete|drop|alter|create|attach|copy|pragma|read_csv|read_parquet|getenv|system)\b|;|--/i;

function safeEvalNumericExpr(expr: string, row: Row): number | null {
  // Simple arithmetic recursive descent supporting numbers, column identifiers, (), +, -, *, /
  const tokens: string[] = [];
  const re = /\s*([A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|[()+\-*/])\s*/g;
  let idx = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(expr)) !== null) {
    if (m.index !== idx) return null;
    tokens.push(m[1]);
    idx = re.lastIndex;
  }
  if (idx !== expr.length || tokens.length === 0) return null;

  let pos = 0;
  const parseExpr = (): number | null => {
    let val = parseTerm();
    if (val === null) return null;
    while (pos < tokens.length && (tokens[pos] === "+" || tokens[pos] === "-")) {
      const op = tokens[pos++];
      const rhs = parseTerm();
      if (rhs === null) return null;
      val = op === "+" ? val + rhs : val - rhs;
    }
    return val;
  };
  const parseTerm = (): number | null => {
    let val = parseFactor();
    if (val === null) return null;
    while (pos < tokens.length && (tokens[pos] === "*" || tokens[pos] === "/")) {
      const op = tokens[pos++];
      const rhs = parseFactor();
      if (rhs === null) return null;
      if (op === "/") {
        if (rhs === 0) return null;
        val = val / rhs;
      } else {
        val = val * rhs;
      }
    }
    return val;
  };
  const parseFactor = (): number | null => {
    const tk = tokens[pos++];
    if (!tk) return null;
    if (tk === "(") {
      const inner = parseExpr();
      if (tokens[pos++] !== ")") return null;
      return inner;
    }
    if (tk === "-") {
      const f = parseFactor();
      return f === null ? null : -f;
    }
    if (/^\d+(?:\.\d+)?$/.test(tk)) return Number(tk);
    if (/^[A-Za-z_][A-Za-z0-9_]*$/.test(tk)) {
      const raw = row[tk];
      if (raw === null || raw === undefined || raw === "") return null;
      const n = Number(raw);
      return Number.isNaN(n) ? null : n;
    }
    return null;
  };
  const out = parseExpr();
  return pos === tokens.length ? out : null;
}

export function handleDemoDescriptiveBuilder(
  method: string,
  segments: string[],
  body: any,
  deps: {
    datasets: any[];
    themes: any[];
    getRowsForDataset: (dsId: string) => { rows: Row[]; columns: string[] };
    executeVisual: (dsId: string, spec: any) => { columns: string[]; rows: Row[]; row_count: number };
  }
) {
  const sub = segments.slice(1).join("/");

  if (method === "GET" && sub === "catalog") {
    return { status: 200, body: { functions: DEMO_FUNCTION_CATALOG, analyses: DEMO_ANALYSIS_CATALOG } };
  }

  if (method === "GET" && sub === "datasets") {
    const out = deps.datasets.map((ds) => {
      const { rows } = deps.getRowsForDataset(ds.id);
      return {
        id: ds.id,
        name: ds.name,
        description: ds.description || "",
        schema_type: ds.schema_type || "fact",
        row_count: ds.row_count || rows.length,
        freshness_status: ds.freshness_status || "fresh",
        last_refreshed_at: ds.last_refreshed_at || new Date().toISOString(),
        has_security_restrictions: false,
        fields: (ds.fields || []).map((f: any, i: number) => ({
          id: f.id || `${ds.id}_f_${i}`,
          field_name: f.field_name,
          display_name: f.display_name || f.field_name,
          data_type: f.data_type || "string",
          semantic_role: f.semantic_role || "dimension",
          is_primary_key: Boolean(f.is_primary_key),
          is_calculated: Boolean(f.is_calculated),
          calculation_type: f.calculation_type || "physical",
          expression: f.expression || null,
          format_type: f.format_type || "number",
          unit: f.unit || "",
          description: f.description || "",
          version: 1,
          access_scope: "dataset",
        })),
        metrics: [],
      };
    });
    return { status: 200, body: { datasets: out } };
  }

  if (method === "GET" && sub === "models") {
    return { status: 200, body: [] };
  }
  if ((method === "POST" || method === "PUT") && sub.startsWith("models")) {
    return { status: 200, body: { id: `demo_model_${Date.now()}`, name: body?.name || "مدل دمو", version: 1 } };
  }

  if (method === "POST" && sub === "relationships/analyze") {
    const dsIds: string[] = Array.isArray(body?.dataset_ids) ? body.dataset_ids : [];
    const chosenDs = deps.datasets.filter((d) => dsIds.includes(d.id));
    const primary = chosenDs[0] || null;
    const adHoc: any[] = Array.isArray(body?.ad_hoc_joins) ? body.ad_hoc_joins : [];

    // Find shared column suggestions across datasets
    const connected = new Set<string>(primary ? [primary.id] : []);
    for (const j of adHoc) {
      connected.add(j.left_dataset_id);
      connected.add(j.right_dataset_id);
    }
    const disconnected = chosenDs.filter((d) => !connected.has(d.id)).map((d) => ({ id: d.id, name: d.name }));
    const suggestions: any[] = [];
    if (primary) {
      const pCols = new Set((primary.fields || []).map((f: any) => f.field_name));
      for (const other of chosenDs) {
        if (other.id === primary.id || connected.has(other.id)) continue;
        for (const f of other.fields || []) {
          if (pCols.has(f.field_name)) {
            suggestions.push({
              left_dataset_id: primary.id,
              left_dataset_name: primary.name,
              left_field: f.field_name,
              right_dataset_id: other.id,
              right_dataset_name: other.name,
              right_field: f.field_name,
              suggested_join_type: "left",
              suggested_cardinality: "many_to_one",
              confidence: 0.85,
            });
            break;
          }
        }
      }
    }
    return {
      status: 200,
      body: {
        primary_dataset_id: primary?.id || null,
        primary_dataset_name: primary?.name || null,
        ready_for_join: disconnected.length === 0,
        disconnected_datasets: disconnected,
        ambiguous_paths: [],
        resolved_joins: adHoc.map((j, idx) => ({
          id: `adhoc_${idx}`,
          left_dataset_id: j.left_dataset_id,
          left_dataset_name: chosenDs.find((d) => d.id === j.left_dataset_id)?.name || "Left",
          left_field: j.left_field,
          right_dataset_id: j.right_dataset_id,
          right_dataset_name: chosenDs.find((d) => d.id === j.right_dataset_id)?.name || "Right",
          right_field: j.right_field,
          join_type: j.join_type || "left",
          cardinality: j.cardinality || "many_to_one",
        })),
        available_relationships: [],
        suggestions,
        explosion_checks: [],
      },
    };
  }

  if (method === "POST" && (sub === "formulas/validate" || sub === "formulas/preview")) {
    const formula = String(body?.formula || "").trim();
    const dsIds: string[] = Array.isArray(body?.dataset_ids) ? body.dataset_ids : [];
    const primaryId = dsIds[0] || deps.datasets[0]?.id;
    if (!formula || FORBIDDEN_TOKENS.test(formula)) {
      return {
        status: 200,
        body: {
          valid: false,
          errors: ["عبارت واردشده شامل کلمات یا توابع غیرمجاز امنیتی است."],
          warnings: [],
          compiled_sql: "",
          output_data_type: "unknown",
          calculation_type: body?.calculation_type || "calculated_column",
          has_aggregate: false,
          referenced_fields: [],
          referenced_datasets: [],
          referenced_calculated_fields: [],
          referenced_metrics: [],
          rows: [],
          columns: [],
          row_count: 0,
          execution_ms: 0,
        },
      };
    }

    const { rows } = deps.getRowsForDataset(primaryId);
    const fname = String(body?.field_name || "calc_preview");
    const previewRows = rows.slice(0, 10).map((r) => ({
      ...r,
      [fname]: safeEvalNumericExpr(formula, r),
    }));
    const cols = previewRows[0] ? Object.keys(previewRows[0]) : [fname];

    return {
      status: 200,
      body: {
        valid: true,
        errors: [],
        warnings: [],
        compiled_sql: `(${formula}) AS "${fname}"`,
        output_data_type: "double",
        calculation_type: body?.calculation_type || "calculated_column",
        has_aggregate: /\b(SUM|AVG|COUNT|MIN|MAX)\s*\(/i.test(formula),
        referenced_fields: [],
        referenced_datasets: primaryId ? [primaryId] : [],
        referenced_calculated_fields: [],
        referenced_metrics: [],
        rows: previewRows,
        columns: cols,
        row_count: previewRows.length,
        execution_ms: 4.2,
      },
    };
  }

  if (method === "POST" && sub === "generate") {
    const dsIds: string[] = Array.isArray(body?.dataset_ids) ? body.dataset_ids : [];
    const primaryDs = deps.datasets.find((d) => dsIds.includes(d.id)) || deps.datasets[0];
    if (!primaryDs) return { status: 400, body: { detail: "No datasets found" } };

    const fields: any[] = primaryDs.fields || [];
    const measures = (Array.isArray(body?.measures) && body.measures.length > 0
      ? body.measures
      : fields.filter((f) => f.semantic_role === "measure").slice(0, 2).map((f) => ({ field_name: f.field_name, aggregation: "SUM" })));
    const mainMeasure = measures[0]?.field_name || fields.find((f) => f.semantic_role === "measure")?.field_name || "*";
    const mainAgg = measures[0]?.aggregation || "SUM";

    const dims = Array.isArray(body?.dimensions) && body.dimensions.length > 0
      ? body.dimensions
      : fields.filter((f) => f.semantic_role === "dimension").slice(0, 2).map((f) => f.field_name);
    const mainDim = dims[0] || fields[0]?.field_name || null;
    const timeDim = fields.find((f) => f.semantic_role === "time" || f.data_type === "date")?.field_name || null;

    const reqAnalyses: string[] = Array.isArray(body?.analysis_types) && body.analysis_types.length > 0
      ? body.analysis_types
      : ["kpi", "org_unit_comparison", "share", "performance"];

    const widgets: any[] = [];
    if (reqAnalyses.includes("kpi") || widgets.length === 0) {
      const kpiRes = deps.executeVisual(primaryDs.id, { y_field: mainMeasure, aggregation: mainAgg });
      const val = kpiRes.rows[0]?.value ?? 0;
      widgets.push({
        temp_id: `w_kpi_1`,
        analysis_type: "kpi",
        title: `مجموع ${mainMeasure}`,
        chart_type: "kpi",
        dataset_id: primaryDs.id,
        query_mode: "visual",
        visual_spec: { y_field: mainMeasure, aggregation: mainAgg },
        style: {},
        visualization: { show_legend: false, show_tooltip: true },
        layout: { x: 0, y: 0, w: 4, h: 3 },
        ...kpiRes,
        narrative_fa: `شاخص «${mainMeasure}» از منبع «${primaryDs.name}» برابر با ${Number(val).toLocaleString()} است.`,
        sources_used: [primaryDs.name],
      });
    }
    if (mainDim) {
      const barRes = deps.executeVisual(primaryDs.id, { x_field: mainDim, y_field: mainMeasure, aggregation: mainAgg, sort_by: "value", sort_dir: "desc", limit: 12 });
      const top = barRes.rows[0];
      widgets.push({
        temp_id: `w_bar_1`,
        analysis_type: "org_unit_comparison",
        title: `مقایسه ${mainMeasure} بر اساس ${mainDim}`,
        chart_type: "bar",
        dataset_id: primaryDs.id,
        query_mode: "visual",
        visual_spec: { x_field: mainDim, y_field: mainMeasure, aggregation: mainAgg, sort_by: "value", sort_dir: "desc", limit: 12 },
        style: {},
        visualization: { show_legend: true, show_tooltip: true },
        layout: { x: 0, y: 3, w: 6, h: 7 },
        ...barRes,
        narrative_fa: top
          ? `بیشترین مقدار در گروه «${top[mainDim]}» با مقدار ${Number(top.value || 0).toLocaleString()} ثبت شده است.`
          : "مقایسه دسته‌بندی آماده است.",
        sources_used: [primaryDs.name],
      });

      const pieRes = deps.executeVisual(primaryDs.id, { x_field: mainDim, y_field: mainMeasure, aggregation: mainAgg, sort_by: "value", sort_dir: "desc", limit: 8 });
      widgets.push({
        temp_id: `w_donut_1`,
        analysis_type: "share",
        title: `سهم ${mainDim} از ${mainMeasure}`,
        chart_type: "donut",
        dataset_id: primaryDs.id,
        query_mode: "visual",
        visual_spec: { x_field: mainDim, y_field: mainMeasure, aggregation: mainAgg, sort_by: "value", sort_dir: "desc", limit: 8 },
        style: {},
        visualization: { show_legend: true, show_tooltip: true },
        layout: { x: 6, y: 3, w: 6, h: 7 },
        ...pieRes,
        narrative_fa: `توزیع درصدی شاخص «${mainMeasure}» بین ${pieRes.row_count} دسته از منبع «${primaryDs.name}» محاسبه شده است.`,
        sources_used: [primaryDs.name],
      });
    }
    if (timeDim) {
      const lineRes = deps.executeVisual(primaryDs.id, { x_field: timeDim, y_field: mainMeasure, aggregation: mainAgg, sort_by: "dimension", sort_dir: "asc", limit: 60 });
      widgets.push({
        temp_id: `w_trend_1`,
        analysis_type: "trend",
        title: `روند زمانی ${mainMeasure}`,
        chart_type: "area",
        dataset_id: primaryDs.id,
        query_mode: "visual",
        visual_spec: { x_field: timeDim, y_field: mainMeasure, aggregation: mainAgg, sort_by: "dimension", sort_dir: "asc", limit: 60 },
        style: {},
        visualization: { show_legend: true, show_tooltip: true },
        layout: { x: 0, y: 10, w: 12, h: 7 },
        ...lineRes,
        narrative_fa: `روند تغییرات شاخص «${mainMeasure}» در طول ${lineRes.row_count} بازه زمانی استخراج شد.`,
        sources_used: [primaryDs.name],
      });
    }

    const tblRes = deps.executeVisual(primaryDs.id, { aggregation: "RAW", limit: 15 });
    widgets.push({
      temp_id: `w_tbl_1`,
      analysis_type: "performance",
      title: `جدول جزئیات داده‌های ${primaryDs.name}`,
      chart_type: "table",
      dataset_id: primaryDs.id,
      query_mode: "visual",
      visual_spec: { aggregation: "RAW", limit: 15 },
      style: {},
      visualization: { show_legend: false, show_tooltip: true },
      layout: { x: 0, y: timeDim ? 17 : 10, w: 12, h: 7 },
      ...tblRes,
      narrative_fa: `جدول جزئیات شامل ${tblRes.row_count} رکورد نمونه از منبع «${primaryDs.name}» نمایش داده شده است.`,
      sources_used: [primaryDs.name],
    });

    return {
      status: 200,
      body: {
        title: body?.title || "داشبورد تحلیل توصیفی",
        calendar_mode: body?.calendar_mode || "jalali",
        primary_dataset_id: primaryDs.id,
        dataset_ids: [primaryDs.id],
        source_names: [primaryDs.name],
        relationship_analysis: { primary_dataset_id: primaryDs.id, ready_for_join: true, disconnected_datasets: [], ambiguous_paths: [], resolved_joins: [], available_relationships: [], suggestions: [], explosion_checks: [] },
        has_security_restrictions: false,
        executive_summary_fa: `این داشبورد توصیفی از منبع «${primaryDs.name}» با ${widgets.length} ویجت تحلیلی و روایت متنی مستند تولید شده است.`,
        model_definition: { dataset_ids: [primaryDs.id], calendar_mode: body?.calendar_mode || "jalali" },
        filters: mainDim ? [{ name: mainDim, field_name: mainDim, dataset_id: primaryDs.id, filter_type: "multi_select", operator: "in" }] : [],
        widgets,
      },
    };
  }

  if (method === "POST" && sub === "preview") {
    const res = deps.executeVisual(body?.dataset_id, body?.visual_spec || {});
    return {
      status: 200,
      body: {
        ...res,
        narrative_fa: `به‌روزرسانی پیش‌نمایش ویجت «${body?.title || ""}» با ${res.row_count} رکورد انجام شد.`,
      },
    };
  }

  if (method === "POST" && sub === "save") {
    const dashId = `dash_desc_${Date.now()}`;
    const pageId = `${dashId}_p1`;
    const theme = deps.themes[0] || null;
    const widgets = (body?.widgets || []).map((w: any, idx: number) => ({
      id: `${dashId}_w_${idx + 1}`,
      dashboard_id: dashId,
      page_id: pageId,
      title: w.title,
      description: w.narrative_fa || "",
      chart_type: w.chart_type,
      dataset_id: w.dataset_id,
      layout: w.layout || { x: (idx % 2) * 6, y: (idx >> 1) * 7, w: 6, h: 7 },
      config: {
        style: w.style || {},
        visualization: w.visualization || { show_legend: true, show_tooltip: true },
        interactions: { cross_filter: true },
        advanced: { narrative_fa: w.narrative_fa || "", sources_used: w.sources_used || [], analysis_type: w.analysis_type || "" },
      },
      query: { query_mode: w.query_mode || "visual", sql_text: w.sql_text || null, visual_spec: w.visual_spec || {}, parameters: {} },
      drill_rule: { hierarchy: w.drill_hierarchy || [], level_rules: [], drill_through_page_id: null },
      widget_filters: [],
      permissions: { view: true, edit: true, delete: true, drill_down: true },
    }));
    const createdDashboard = {
      id: dashId,
      title: body?.title || "داشبورد تحلیل توصیفی",
      description: body?.description || "",
      is_public: false,
      embed_token: null,
      is_template: false,
      theme_id: theme?.id || null,
      theme,
      can_edit: true,
      can_manage: true,
      permissions: { view: true, edit: true, add_widget: true, delete_widget: true, share: true, manage: true, comment: true },
      pages: [{ id: pageId, title: "تحلیل توصیفی", page_order: 0, layout: { columns: 12, items: widgets.map((w: any) => ({ i: w.id, ...w.layout })) } }],
      widgets,
      filters: (body?.filters || []).map((f: any, idx: number) => ({
        id: `${dashId}_flt_${idx + 1}`,
        name: f.name || f.field_name,
        field_name: f.field_name,
        dataset_id: f.dataset_id,
        filter_type: f.filter_type || "multi_select",
        operator: f.operator || "in",
        scope: "dashboard",
        default_value: [],
      })),
    };
    return { status: 200, body: { id: dashId, title: createdDashboard.title, created_dashboard: createdDashboard } };
  }

  // GET /descriptive-builder/smart-kpis/{datasetId}
  if (method === "GET" && segments[1] === "smart-kpis" && segments[2]) {
    const ds = deps.datasets.find((d) => d.id === segments[2]) || deps.datasets[0];
    if (!ds) return { status: 404, body: { detail: "Dataset not found" } };
    const table = deps.getRowsForDataset(ds.id);
    const numFields = (ds.fields || []).filter((f: any) =>
      ["int", "integer", "float", "decimal", "numeric", "number"].includes(String(f.data_type || "").toLowerCase())
    );
    const smartKpis = numFields.slice(0, 6).map((f: any, idx: number) => {
      const vals = (table.rows || []).map((r) => Number(r[f.field_name])).filter((v) => !Number.isNaN(v));
      const sum = vals.reduce((a, b) => a + b, 0);
      const avg = vals.length ? sum / vals.length : 0;
      const isRatio = /rate|pct|percent|margin|ratio/i.test(f.field_name);
      const val = isRatio ? avg : sum;
      return {
        id: `demo_kpi_${f.field_name}`,
        slug: `${f.field_name}_${isRatio ? "avg" : "total"}`,
        name: `${isRatio ? "میانگین" : "مجموع"} ${f.display_name || f.field_name}`,
        formula: `${isRatio ? "AVG" : "SUM"}("${f.field_name}")`,
        format_type: /revenue|cost|profit|price|amount|budget/i.test(f.field_name) ? "currency" : isRatio ? "percentage" : "number",
        value: Math.round(val * 100) / 100,
        formatted_value: Math.round(val).toLocaleString("en-US"),
        category: idx === 0 ? "domain" : "aggregate",
        score: 94 - idx * 4,
        confidence: 0.92,
      };
    });
    return {
      status: 200,
      body: {
        dataset_id: ds.id,
        dataset_name: ds.name,
        domain: { primary_domain: "general_analytics", primary_domain_fa: "تحلیل جامع سازمانی", confidence: 0.85 },
        kpis: smartKpis,
        data_quality_kpis: [
          {
            id: "dq_completeness",
            name: "درصد تکمیل داده‌ها",
            formula: "100.0",
            value: 99.4,
            formatted_value: "99.4%",
          },
        ],
        recommended_pages: [],
      },
    };
  }

  return { status: 200, body: { status: "ok" } };
}
