import rawDemoData from "@/lib/vercelDemoData.json";
import { parseSpreadsheetBuffer } from "@/lib/excelParser";
import { computeBpmnAnalytics, DEFAULT_PROCUREMENT_BPMN_GRAPH, inferBpmnFieldMapping, normalizeBpmnText } from "@/lib/bpmnParser";
import { handleDemoDescriptiveBuilder } from "@/lib/vercelDemoDescriptiveBuilder";
import {
  evaluateDemoKpiFormula,
  evaluateDemoStandardAndBusinessKpis,
  formatKpiVal,
} from "@/lib/vercelDemoKpi";
import { APP_VERSION, PREVIOUS_VERSION, RELEASE_NOTES, SCHEMA_VERSION } from "@/lib/version";

const demoData = rawDemoData as any;

type Row = Record<string, any>;

function getExtraDatasets(body?: any): any[] {
  if (Array.isArray(body?.demo_uploaded_datasets)) return body.demo_uploaded_datasets;
  if (Array.isArray(body?.demo_context?.uploaded_datasets)) return body.demo_context.uploaded_datasets;
  return [];
}

function getEffectiveDatasets(extraDatasets: any[] = []): any[] {
  const base: any[] = demoData.datasets || [];
  if (!extraDatasets.length) return base;
  const ids = new Set(extraDatasets.map((d) => d.id));
  return [...extraDatasets, ...base.filter((b) => !ids.has(b.id))];
}

function getTableForDataset(
  datasetId?: string | null,
  extraDatasets: any[] = []
): { tableName: string; rows: Row[]; columns: string[] } {
  const extraHit = extraDatasets.find((d: any) => d.id === datasetId);
  if (extraHit) {
    return {
      tableName: extraHit.warehouse_table || extraHit.id,
      rows: (extraHit.rows || []) as Row[],
      columns: (extraHit.columns || (extraHit.fields || []).map((f: any) => f.field_name)) as string[],
    };
  }
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
  rawMode = false,
  extraDatasets: any[] = []
) {
  const { rows: allRows, columns: tableCols } = getTableForDataset(datasetId, extraDatasets);
  const filtered = applyRlsAndFilters(allRows, username, filters);
  const agg = String(spec?.aggregation || "SUM").toUpperCase();
  const limit = Number(spec?.limit || (rawMode ? 1000 : 200));

  if (rawMode || agg === "RAW") {
    const cleanCols = tableCols.filter((c) => c !== "id" && !c.endsWith("_id"));
    const cols =
      Array.isArray(spec?.columns) && spec.columns.length > 0
        ? spec.columns
        : (cleanCols.length > 0 ? cleanCols : tableCols).slice(0, rawMode ? 24 : 10);
    const sliced = filtered.slice(0, rawMode ? Math.max(limit, 1000) : limit).map((r) => {
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
  const calMode = spec?.calendar_mode || "jalali";
  const msa = spec?.multi_source_agg || null;
  const rightRowsForMsa = msa?.right_dataset_id
    ? applyRlsAndFilters(getTableForDataset(msa.right_dataset_id, extraDatasets).rows, username, [])
    : undefined;

  if (!xField) {
    if (spec?.formula || msa) {
      const fVal = evaluateDemoKpiFormula(
        spec?.formula || "",
        filtered,
        calMode,
        msa,
        rightRowsForMsa
      );
      return {
        columns: ["value"],
        rows: [{ value: fVal !== null ? Math.round(fVal * 10000) / 10000 : null }],
        row_count: 1,
      };
    }
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
    if (spec?.formula) {
      const fVal = evaluateDemoKpiFormula(spec.formula, grp, calMode, null);
      row.value = fVal !== null ? Math.round(fVal * 100) / 100 : null;
    } else {
      row.value = Math.round(aggregateGroup(grp, yField, agg) * 100) / 100;
    }
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

function buildDemoBpmnPayload(
  widget: any,
  username: string | null,
  combinedFilters: any[],
  extraDatasets: any[] = [],
  bpmnProcessName: string | null = null,
  bpmnFieldOverrides: Record<string, string> | null = null
) {
  const tbl = getTableForDataset(widget.dataset_id, extraDatasets);
  const cols = tbl.columns || [];
  const baseRows = applyRlsAndFilters(tbl.rows || [], username, combinedFilters);
  const spec = widget.query?.visual_spec || {};
  const viz = widget.config?.visualization || {};
  const rawCfg = {
    ...(spec.bpmn_config || {}),
    ...(viz.bpmn_config || {}),
    ...(bpmnFieldOverrides || {}),
  };
  const mapping = inferBpmnFieldMapping(cols, rawCfg);
  const procCol = mapping.process_field;
  const processesMap = new Map<string, { total: number; steps: Set<string>; waits: number[] }>();
  if (procCol) {
    for (const r of baseRows) {
      const pName = String(r[procCol] ?? "").trim();
      if (!pName) continue;
      if (!processesMap.has(pName)) processesMap.set(pName, { total: 0, steps: new Set(), waits: [] });
      const cur = processesMap.get(pName)!;
      cur.total += 1;
      if (mapping.step_field && r[mapping.step_field]) cur.steps.add(String(r[mapping.step_field]).trim());
      if (mapping.wait_field) cur.waits.push(Number(r[mapping.wait_field] || 0) || 0);
    }
  }
  const processes = Array.from(processesMap.entries())
    .map(([process_name, info]) => ({
      process_name,
      total_count: info.total,
      step_count: info.steps.size,
      avg_wait_hours: info.waits.length
        ? Math.round((info.waits.reduce((a, b) => a + b, 0) / info.waits.length) * 10) / 10
        : 0,
    }))
    .sort((a, b) => b.total_count - a.total_count);

  let activeProcess: string | null = null;
  if (bpmnProcessName && bpmnProcessName !== "__ALL__" && bpmnProcessName !== "ALL") {
    activeProcess = bpmnProcessName;
  } else if (!bpmnProcessName && processes.length > 1) {
    activeProcess = processes[0].process_name;
  }
  const scopedRows =
    procCol && activeProcess
      ? baseRows.filter((r) => normalizeBpmnText(r[procCol]) === normalizeBpmnText(activeProcess))
      : baseRows;

  const computed = computeBpmnAnalytics(scopedRows, cols, rawCfg, "avg_wait");
  const stepList = Object.values(computed.stepMap);
  const assignees_by_step: Record<string, any[]> = {};
  for (const st of stepList) {
    assignees_by_step[st.stepKey] = st.assignees.map((a) => ({
      assigneeName: a.assigneeName,
      assigneeRole: a.assigneeRole,
      pendingCount: a.pendingCount,
      totalCount: a.totalCount,
      avgWaitHours: a.avgWaitHours,
      maxWaitHours: a.maxWaitHours,
      overdueCount: a.overdueCount,
    }));
  }

  return {
    mode: "sql_aggregated",
    total_source_rows: baseRows.length,
    active_source_rows: scopedRows.length,
    sampled_raw_rows: Math.min(scopedRows.length, 1500),
    processes: processes.map((p) => ({ ...p, pending_count: p.total_count })),
    active_process: activeProcess,
    mapping: {
      process_field: mapping.process_field || null,
      step_field: mapping.step_field,
      assignee_field: mapping.assignee_field || null,
      assignee_role_field: mapping.assignee_role_field || null,
      wait_field: mapping.wait_field || null,
      sla_field: mapping.sla_field || null,
      status_field: mapping.status_field || null,
      request_id_field: mapping.request_id_field || null,
      request_title_field: mapping.request_title_field || null,
      department_field: mapping.department_field || null,
      entered_at_field: mapping.entered_at_field || null,
    },
    available_columns: cols,
    step_summary: stepList.map((st, idx) => ({
      step_name: st.stepKey,
      step_order: idx + 1,
      total_count: st.totalCount,
      pending_count: st.pendingCount,
      completed_count: st.completedCount,
      avg_wait_hours: st.avgWaitHours,
      max_wait_hours: st.maxWaitHours,
      sla_hours: st.slaHours,
      overdue_count: st.overdueCount,
      assignee_count: st.assignees.length,
    })),
    transitions: [],
    assignees_by_step,
  };
}

function executeDemoBpmnStepDrill(
  widget: any,
  username: string | null,
  body: any,
  extraDatasets: any[] = []
) {
  const combinedFilters = [
    ...(body?.active_filters || []),
    ...(body?.cross_filters || []).filter((c: any) => c?.source_widget_id !== widget.id),
  ];
  const tbl = getTableForDataset(widget.dataset_id, extraDatasets);
  const cols = tbl.columns || [];
  let rows = applyRlsAndFilters(tbl.rows || [], username, combinedFilters);
  const spec = widget.query?.visual_spec || {};
  const viz = widget.config?.visualization || {};
  const rawCfg = {
    ...(spec.bpmn_config || {}),
    ...(viz.bpmn_config || {}),
    ...(body?.field_overrides || {}),
  };
  const mapping = inferBpmnFieldMapping(cols, rawCfg);
  if (mapping.process_field && body?.process_name && body.process_name !== "__ALL__") {
    const pNorm = normalizeBpmnText(body.process_name);
    rows = rows.filter((r) => normalizeBpmnText(r[mapping.process_field!]) === pNorm);
  }
  const computed = computeBpmnAnalytics(rows, cols, rawCfg, "avg_wait");
  const targetNorm = normalizeBpmnText(body?.step_name);
  const stepStats = Object.values(computed.stepMap).find(
    (s) => normalizeBpmnText(s.stepKey) === targetNorm || normalizeBpmnText(s.stepLabel) === targetNorm
  );
  if (!stepStats) {
    return {
      step_name: body?.step_name || "",
      process_name: body?.process_name || null,
      available_columns: cols,
      step_summary: {
        step_key: body?.step_name || "",
        total_count: 0,
        pending_count: 0,
        completed_count: 0,
        avg_wait_hours: 0,
        max_wait_hours: 0,
        sla_hours: 48,
        overdue_count: 0,
      },
      assignees: [],
      items: [],
      total_items: 0,
      limit: Number(body?.limit || 80),
      offset: Number(body?.offset || 0),
    };
  }
  let items = body?.pending_only && stepStats.pendingItems.length > 0 ? stepStats.pendingItems : stepStats.items;
  if (body?.assignee_name) {
    const aNorm = normalizeBpmnText(body.assignee_name);
    items = items.filter((it) => normalizeBpmnText(it.assigneeName) === aNorm);
  }
  if (body?.search) {
    const qNorm = normalizeBpmnText(body.search);
    items = items.filter(
      (it) =>
        normalizeBpmnText(it.requestId).includes(qNorm) ||
        normalizeBpmnText(it.requestTitle).includes(qNorm) ||
        normalizeBpmnText(it.assigneeName).includes(qNorm) ||
        normalizeBpmnText(it.department).includes(qNorm)
    );
  }
  const limit = Math.max(1, Math.min(Number(body?.limit || 80), 200));
  const offset = Math.max(0, Number(body?.offset || 0));
  return {
    step_name: stepStats.stepKey,
    process_name: body?.process_name || null,
    available_columns: cols,
    step_summary: {
      step_key: stepStats.stepKey,
      step_label: stepStats.stepLabel,
      total_count: stepStats.totalCount,
      pending_count: stepStats.pendingCount,
      completed_count: stepStats.completedCount,
      avg_wait_hours: stepStats.avgWaitHours,
      max_wait_hours: stepStats.maxWaitHours,
      sla_hours: stepStats.slaHours,
      overdue_count: stepStats.overdueCount,
    },
    assignees: stepStats.assignees.map((a) => ({
      assigneeName: a.assigneeName,
      assigneeRole: a.assigneeRole,
      pendingCount: a.pendingCount,
      totalCount: a.totalCount,
      avgWaitHours: a.avgWaitHours,
      maxWaitHours: a.maxWaitHours,
      overdueCount: a.overdueCount,
    })),
    items: items.slice(offset, offset + limit),
    total_items: items.length,
    limit,
    offset,
  };
}

function executeWidgetForDemo(
  widget: any,
  username: string | null,
  activeFilters: any[] = [],
  crossFilters: any[] = [],
  drillStack: any[] = [],
  extraDatasets: any[] = [],
  bpmnProcessName: string | null = null,
  bpmnFieldOverrides: Record<string, string> | null = null
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
  const isBpmn = effectiveChartType === "bpmn";
  const res = executeVisualSpec(widget.dataset_id, spec, username, combinedFilters, effectiveDim, isRaw || isBpmn, extraDatasets);
  const bpmnPayload = isBpmn
    ? buildDemoBpmnPayload(widget, username, combinedFilters, extraDatasets, bpmnProcessName, bpmnFieldOverrides)
    : undefined;

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
    ...(bpmnPayload ? { bpmn_analytics: bpmnPayload } : {}),
  };
}

function executeSimpleDemoSql(sqlText: string, username: string | null, extraDatasets: any[] = []) {
  const text = (sqlText || "").trim();
  const fromMatch = text.match(/\bFROM\s+([A-Za-z0-9_]+)/i);
  const tableName = fromMatch ? fromMatch[1] : "wh_enterprise_sales";
  const extraMatch = extraDatasets.find((d: any) => d.warehouse_table === tableName || d.id === tableName);
  const tbl = extraMatch
    ? { rows: extraMatch.rows || [], columns: extraMatch.columns || (extraMatch.fields || []).map((f: any) => f.field_name) }
    : demoData.tables?.[tableName];
  if (!tbl) {
    return { status: 400, body: { detail: `Demo table '${tableName}' not found.` } };
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
    [["bpmn", "گلوگاه", "گردش کار", "نقشه فرآیند", "نقشه فرایند", "نمودار فرآیند", "نمودار فرایند", "کارتابل فرآیند", "فرآیند خرید", "فرایند خرید"], "bpmn"],
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

function inferDemoBpmnConfig(ds: any, colsInfo: any[]) {
  if (!colsInfo?.length) return null;
  const hasExplicitStep = colsInfo.some((c: any) => {
    const hay = `${c.name || ""} ${c.label || ""}`.toLowerCase();
    return ["step_name", "step", "stage", "activity", "task", "گام", "مرحله", "فعالیت"].some((k) => hay.includes(k));
  });
  if (!hasExplicitStep && !String(ds?.warehouse_table || "").toLowerCase().includes("bpmn")) {
    return null;
  }
  const descriptors = colsInfo.map((c: any) => ({ name: String(c.name), label: String(c.label || c.name) }));
  const mapping = inferBpmnFieldMapping(descriptors);
  if (!mapping.step_field || (!mapping.wait_field && !mapping.assignee_field)) return null;
  const isSeedBpmn = String(ds?.warehouse_table || "").toLowerCase().includes("bpmn");
  return {
    ...mapping,
    heatmap_enabled: true,
    heatmap_metric: "avg_wait",
    process_name: ds?.name || "فرآیند سازمانی",
    ...(isSeedBpmn
      ? {
          nodes: DEFAULT_PROCUREMENT_BPMN_GRAPH.nodes,
          edges: DEFAULT_PROCUREMENT_BPMN_GRAPH.edges,
        }
      : {}),
  };
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
  if (ds?.fields?.length && (!demoData.tables?.[ds.warehouse_table] || ds.is_uploaded_excel)) {
    const colsInfo = ds.fields.map((f: any) => ({
      name: String(f.field_name),
      label: normalizeFaText(f.display_name || String(f.field_name).replace(/_/g, " ")),
      role: f.semantic_role || (["integer", "float"].includes(f.data_type) ? "measure" : f.data_type === "date" ? "time" : "dimension"),
    }));
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
  const fieldMetaMap = new Map<string, any>();
  for (const f of ds.fields || []) {
    if (f?.field_name) fieldMetaMap.set(f.field_name, f);
  }
  const colsInfo = (tbl.columns || []).map((name: string) => {
    const fmeta = fieldMetaMap.get(name);
    const isNum =
      fmeta?.semantic_role === "measure" ||
      (typeof sample[name] === "number" && !name.endsWith("_id") && !["year", "month", "quarter", "day"].includes(name));
    const role =
      fmeta?.semantic_role ||
      (isNum ? "measure" : ["year", "quarter", "month", "day", "date"].includes(name) ? "time" : "dimension");
    return { name, label: fmeta?.display_name || name.replace(/_/g, " "), role };
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
    "حوزه پروژه": ["project_type", "حوزه"],
    "تعداد پروژه": ["project_count", "تعداد پروژه"],
    "کار انجام شده": ["completed_tasks", "انجام"],
    "امتیاز عملکرد": ["performance_score", "امتیاز"],
    "امتیاز": ["performance_score", "score", "امتیاز"],
    "مبلغ قرارداد": ["contract_value", "مبلغ قرارداد"],
    "عملکرد": ["performance_score", "performance", "score", "revenue"],
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
    "گام": ["step_name", "step", "stage", "گام", "مرحله"],
    "مرحله": ["step_name", "step", "stage", "گام", "مرحله"],
    "فرآیند": ["process_name", "step_name", "فرآیند"],
    "فرایند": ["process_name", "step_name", "فرآیند"],
    "زمان انتظار": ["wait_hours", "wait_time", "wait", "انتظار"],
    "انتظار": ["wait_hours", "wait_time", "wait", "انتظار"],
    "تاخیر": ["wait_hours", "delay", "تاخیر"],
    "کارتابل": ["assignee_name", "assignee", "کارتابل", "مسئول"],
    "مسئول": ["assignee_name", "assignee", "owner", "مسئول"],
    "وضعیت": ["status", "state", "وضعیت"],
    "درخواست": ["request_code", "request_id", "درخواست"],
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
  const extraDatasets = getExtraDatasets(body);
  const datasets: any[] = getEffectiveDatasets(extraDatasets);

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
        "فرآیند": ["bpmn", "process", "فرآیند"],
        "فرایند": ["bpmn", "process", "فرآیند"],
        "bpmn": ["bpmn", "process", "فرآیند"],
        "گلوگاه": ["bpmn", "process", "فرآیند"],
        "کارتابل": ["bpmn", "process", "فرآیند"],
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
      else if (!dimMention) chosenDs = matchDemoDatasetFromText(datasets, semanticLow);
    }
  }

  const chartType = clar.chart_type || extractChartTypeFromText(semanticLow);
  let agg: string | null = clar.aggregation || null;
  if (!agg) {
    if (["میانگین", "متوسط", "avg", "average", "mean"].some((k) => semanticLow.includes(k))) agg = "AVG";
    else if (["بیشترین", "حداکثر", "ماکزیمم", "max", "highest"].some((k) => semanticLow.includes(k))) agg = "MAX";
    else if (["کمترین", "حداقل", "مینیمم", "min", "lowest"].some((k) => semanticLow.includes(k))) agg = "MIN";
    else if (["مجموع", "جمع", "سرجمع", "sum", "total"].some((k) => semanticLow.includes(k))) agg = "SUM";
    else if (["شمارش", "تعداد رکورد", "تعداد کل", "چند تا", "count", "how many"].some((k) => semanticLow.includes(k))) agg = "COUNT";
    else if (
      semanticLow.includes("تعداد") &&
      !["تعداد پروژه", "تعداد کار", "تعداد قرارداد"].some((cp) => semanticLow.includes(cp))
    ) {
      agg = "COUNT";
    }
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
  const inferredBpmn = inferDemoBpmnConfig(chosenDs, colsInfo);

  if (!chartType) {
    questions.push({
      key: "chart_type",
      question: "نوع ویجت / نمودار مشخص نیست. چه نوع نمایشی بسازم؟",
      options: [
        { value: "bar", label: "نمودار میله‌ای (Bar)" },
        { value: "bpmn", label: "فرآیند BPMN و کشف گلوگاه‌ها" },
        { value: "kpi", label: "کارت شاخص عددی (KPI)" },
        { value: "line", label: "نمودار خطی (Line)" },
        { value: "pie", label: "نمودار دایره‌ای (Pie)" },
        { value: "table", label: "جدول داده (Table)" },
      ],
    });
  }

  let xField: string | null = clar.x_field || null;
  if (xField === "__none__") xField = null;
  else if (!xField && chartType === "bpmn") {
    xField = inferredBpmn?.step_field || dimCols[0] || null;
  } else if (!xField && chartType !== "kpi" && chartType !== "gauge") {
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
  if (chartType === "bpmn" && !yField) {
    yField = inferredBpmn?.wait_field || numCols[0] || "*";
    if (yField !== "*" && !agg) agg = "AVG";
  }
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
            const isAvg = ["score", "pct", "rate", "ratio", "margin"].some((k) => nc.toLowerCase().includes(k));
            return isAvg
              ? { value: `AVG:${nc}`, label: `میانگین ${lbl}`, hint: `AVG(${nc})` }
              : { value: nc, label: `مجموع ${lbl}`, hint: `SUM(${nc})` };
          }),
        ],
      });
    }
  }

  if (yField === "__COUNT__") {
    yField = "*";
    agg = "COUNT";
  } else if (typeof yField === "string" && yField.startsWith("AVG:")) {
    yField = yField.slice(4);
    agg = "AVG";
  } else if (typeof yField === "string" && yField.startsWith("SUM:")) {
    yField = yField.slice(4);
    agg = "SUM";
  } else if (yField && !agg) {
    if (yField === "*") agg = "COUNT";
    else if (["score", "pct", "rate", "ratio", "margin"].some((k) => yField!.toLowerCase().includes(k))) agg = "AVG";
    else agg = "SUM";
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
  const xLbl = xField ? (colsInfo.find((c: any) => c.name === xField)?.label || xField) : "";
  const yLbl = effY === "*" ? "تعداد" : (colsInfo.find((c: any) => c.name === effY)?.label || effY);
  const finalTitle =
    extractedTitle ||
    (xField ? `${yLbl} بر اساس ${xLbl}` : `${effAgg} ${yLbl}`);

  const visualSpec: any = { y_field: effY, aggregation: effAgg, limit: 100 };
  if (xField && effChart !== "kpi" && effChart !== "gauge") visualSpec.x_field = xField;
  if (effChart === "bpmn") {
    visualSpec.bpmn_config = {
      ...(inferredBpmn || {}),
      ...(xField && !inferredBpmn?.step_field ? { step_field: xField } : {}),
      ...(effY !== "*" && !inferredBpmn?.wait_field ? { wait_field: effY } : {}),
      heatmap_enabled: true,
      heatmap_metric: "avg_wait",
      process_name: finalTitle,
    };
  }
  const isCurr = ["rev", "cost", "profit", "price", "amount", "salary", "budget"].some((k) => effY.toLowerCase().includes(k));
  const styleCfg = { number_format: isCurr && effAgg !== "COUNT" ? "currency" : "number" };
  const vizCfg = { show_legend: true, show_tooltip: true, data_labels: false };
  const previewData = executeVisualSpec(chosenDs.id, visualSpec, username, [], null, effChart === "bpmn", extraDatasets);

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
      layout: {
        x: 0,
        y: nextY,
        w: effChart === "bpmn" ? 12 : effChart === "kpi" || effChart === "gauge" ? 4 : 6,
        h: effChart === "bpmn" ? 10 : effChart === "kpi" || effChart === "gauge" ? 3 : 7,
      },
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

const DEMO_CHART_LABELS_FA: Record<string, string> = {
  bar: "نمودار میله‌ای",
  horizontal_bar: "نمودار میله‌ای افقی",
  stacked_bar: "نمودار ستونی انباشته",
  line: "نمودار خطی (روندی)",
  area: "نمودار مساحتی",
  pie: "نمودار دایره‌ای",
  donut: "نمودار حلقوی",
  kpi: "کارت عدد خلاصه",
  gauge: "عقربه شاخص",
  table: "جدول اطلاعات",
  pivot_table: "جدول مقایسه‌ای",
  bpmn: "فرآیند BPMN و گلوگاه‌ها",
};

function friendlyDemoDatasetLabel(ds: any): [string, string] {
  const wt = String(ds.warehouse_table || "").toLowerCase();
  const nm = String(ds.name || "").toLowerCase();
  if (wt.includes("bpmn") || nm.includes("bpmn") || nm.includes("فرآیند")) {
    return ["فرآیندهای BPMN و کارتابل درخواست‌ها (کشف گلوگاه)", `${ds.row_count || 67} رویداد فرآیندی`];
  }
  if (wt.includes("contractor") || ds.name === "تعداد") {
    return ["گزارش عملکرد پیمانکاران و پروژه‌ها (تعداد)", `${ds.row_count || 12} رکورد آماده`];
  }
  if (wt.includes("sales") || nm.includes("sales")) {
    return ["فروش، درآمد و سود مناطق", `${ds.row_count || 1200} سفارش`];
  }
  if (wt.includes("expense") || nm.includes("expense")) {
    return ["هزینه‌ها و بودجه بخش‌ها", `${ds.row_count || 450} سند مالی`];
  }
  if (wt.includes("hr") || wt.includes("workforce")) {
    return ["اطلاعات پرسنل و منابع انسانی", `${ds.row_count || 8} پرسنل`];
  }
  return [ds.name || "منبع داده", `${ds.row_count || 0} ردیف اطلاعات`];
}

function matchDemoDatasetFromText(datasets: any[], textLow: string): any | null {
  if (!datasets.length || !textLow) return null;
  for (const d of datasets) {
    if (textLow.includes(String(d.name || "").toLowerCase()) || textLow.includes(String(d.warehouse_table || "").toLowerCase())) {
      return d;
    }
  }
  const synGroups: Array<[string[], string[]]> = [
    [["bpmn", "گلوگاه", "کارتابل", "فرآیند", "فرایند", "انتظار", "گردش کار", "process"], ["bpmn", "process", "فرآیند"]],
    [["پیمانکار", "تعداد پروژه", "پروژه", "contractor"], ["contractor", "تعداد"]],
    [["فروش", "درآمد", "سود", "سفارش", "محصول", "مناطق", "منطقه", "sales", "revenue"], ["sales", "revenue"]],
    [["هزینه", "بودجه", "خرج", "مالی", "expense", "budget", "cost"], ["expense", "cost", "budget"]],
    [["پرسنل", "کارمند", "کارکنان", "حقوق", "منابع انسانی", "نیروی انسانی", "hr", "employee"], ["hr", "workforce"]],
  ];
  for (const [kws, targets] of synGroups) {
    if (kws.some((k) => textLow.includes(k))) {
      const hit = datasets.find((d) => {
        const dn = String(d.name || "").toLowerCase();
        const wt = String(d.warehouse_table || "").toLowerCase();
        return targets.some((tg) => dn.includes(tg) || wt.includes(tg));
      });
      if (hit) return hit;
    }
  }
  return null;
}

function resolveDemoThemeFromText(textLow: string): any | null {
  const themes: any[] = demoData.themes || [];
  if (!themes.length || !textLow) return null;
  for (const th of themes) {
    if (th.id === textLow || textLow.includes(String(th.slug || "").toLowerCase()) || textLow.includes(String(th.name || "").toLowerCase())) {
      return th;
    }
  }
  const themeHints: Array<[string[], string[]]> = [
    [["شیشه", "glass", "بلور", "شیک"], ["glass"]],
    [["نئون", "neon", "درخشان", "رنگی"], ["neon"]],
    [["تیره", "مشکی", "دارک", "dark", "تم شب", "حالت شب"], ["dark"]],
    [["اداری", "رسمی", "سازمانی", "corporate", "آبی"], ["corporate"]],
    [["مینیمال", "ساده", "minimal"], ["minimal"]],
    [["روشن", "سفید", "تم روز", "حالت روز", "light"], ["light", "corporate"]],
    [["متریال", "material"], ["material"]],
  ];
  for (const [kws, slugs] of themeHints) {
    if (kws.some((k) => textLow.includes(k))) {
      for (const slug of slugs) {
        const hit = themes.find((th) => String(th.slug || "").toLowerCase().includes(slug) || String(th.name || "").toLowerCase().includes(slug));
        if (hit) return hit;
      }
    }
  }
  return null;
}

function buildDemoFullDashboard(ds: any, title: string, layoutPreset: string, theme: any | null) {
  const { colsInfo, numCols, dimCols } = getDemoDatasetSchema(ds);
  const labelMap = new Map<string, string>(colsInfo.map((c: any) => [c.name, c.label]));
  const lbl = (col?: string | null) => (!col || col === "*" ? "رکوردها" : labelMap.get(col) || col.replace(/_/g, " "));

  const prefDims = [
    "level",
    "status_code",
    "service",
    "method",
    "logger",
    "contractor_name",
    "region",
    "project_type",
    "category",
    "department",
    "product",
    "channel",
    "city",
  ].filter((c) => dimCols.includes(c));
  const restDims = dimCols.filter((c: string) => !prefDims.includes(c) && !c.endsWith("_code") && c !== "line_number");
  const orderedDims = [...prefDims, ...restDims];
  const primaryDim = orderedDims[0] || null;
  const secondaryDim = orderedDims[1] || primaryDim;
  const timeCol = colsInfo.find((c: any) => c.role === "time")?.name || null;

  const prefNums = [
    "project_count",
    "revenue",
    "profit",
    "actual_amount",
    "performance_score",
    "contract_value",
    "salary",
    "latency_ms",
    "duration_ms",
    "response_bytes",
  ].filter((c) => numCols.includes(c));
  const restNums = numCols.filter((c: string) => !prefNums.includes(c) && c !== "line_number" && c !== "status_code");
  const orderedNums = [...prefNums, ...restNums];
  const primaryNum = orderedNums[0] || "*";
  const secondaryNum = orderedNums[1] || primaryNum;

  const aggFor = (col: string) => {
    if (col === "*") return "COUNT";
    if (["score", "pct", "rate", "ratio", "margin", "latency", "duration"].some((k) => col.toLowerCase().includes(k))) return "AVG";
    return "SUM";
  };
  const pAgg = aggFor(primaryNum);
  const sAgg = aggFor(secondaryNum);
  const isCurrP = ["rev", "cost", "profit", "price", "amount", "salary", "budget", "value"].some((k) => primaryNum.toLowerCase().includes(k));

  const dashId = `dash_ai_${Date.now()}_${Math.floor(Math.random() * 1000)}`;
  const pageId = `page_${dashId}_1`;
  const specs: any[] = [];

  if (layoutPreset === "executive_full" || layoutPreset === "kpi_and_pie") {
    specs.push({
      title: `تعداد کل ${primaryDim ? lbl(primaryDim) : "اطلاعات"}`,
      chart_type: "kpi",
      layout: { x: 0, y: 0, w: 4, h: 3 },
      visual_spec: { y_field: "*", aggregation: "COUNT" },
      style: { number_format: "number" },
    });
    if (primaryNum !== "*") {
      specs.push({
        title: `${pAgg === "AVG" ? "میانگین" : "مجموع"} ${lbl(primaryNum)}`,
        chart_type: "kpi",
        layout: { x: 4, y: 0, w: 4, h: 3 },
        visual_spec: { y_field: primaryNum, aggregation: pAgg },
        style: { number_format: isCurrP && pAgg !== "COUNT" ? "currency" : "number" },
      });
    }
    if (secondaryNum !== primaryNum && secondaryNum !== "*") {
      specs.push({
        title: `${sAgg === "AVG" ? "میانگین" : "مجموع"} ${lbl(secondaryNum)}`,
        chart_type: "kpi",
        layout: { x: 8, y: 0, w: 4, h: 3 },
        visual_spec: { y_field: secondaryNum, aggregation: sAgg },
        style: { number_format: "number" },
      });
    }
  }

  let chartTopY = specs.length ? 3 : 0;
  const bpmnInferred = inferDemoBpmnConfig(ds, colsInfo);
  if (bpmnInferred) {
    const wf = bpmnInferred.wait_field || primaryNum;
    const sf = bpmnInferred.step_field || primaryDim;
    specs.push({
      title: `نقشه فرآیند BPMN و گلوگاه‌ها — ${ds.name}`,
      chart_type: "bpmn",
      layout: { x: 0, y: chartTopY, w: 12, h: 10 },
      visual_spec: {
        x_field: sf,
        y_field: wf,
        aggregation: wf === "*" ? "COUNT" : "AVG",
        bpmn_config: { ...bpmnInferred, process_name: title, heatmap_enabled: true },
      },
      style: { number_format: "number" },
    });
    chartTopY += 10;
  }
  if (primaryDim && (layoutPreset === "executive_full" || layoutPreset === "charts_only")) {
    specs.push({
      title: `مقایسه ${lbl(primaryNum)} بر اساس ${lbl(primaryDim)}`,
      chart_type: "bar",
      layout: { x: 0, y: chartTopY, w: 6, h: 7 },
      visual_spec: { x_field: primaryDim, y_field: primaryNum, aggregation: pAgg, limit: 50 },
      style: { number_format: isCurrP && pAgg !== "COUNT" ? "currency" : "number" },
    });
  }
  if (secondaryDim && (layoutPreset === "executive_full" || layoutPreset === "kpi_and_pie")) {
    specs.push({
      title: `سهم بر اساس ${lbl(secondaryDim)}`,
      chart_type: layoutPreset === "kpi_and_pie" ? "pie" : "donut",
      layout: { x: primaryDim && layoutPreset === "executive_full" ? 6 : 0, y: chartTopY, w: 6, h: 7 },
      visual_spec: { x_field: secondaryDim, y_field: primaryNum, aggregation: pAgg, limit: 30 },
      style: { number_format: "number" },
    });
  }
  if (layoutPreset === "charts_only" && (timeCol || secondaryDim)) {
    const xt = timeCol || secondaryDim;
    specs.push({
      title: `روند ${lbl(secondaryNum)} بر اساس ${lbl(xt)}`,
      chart_type: "line",
      layout: { x: 6, y: chartTopY, w: 6, h: 7 },
      visual_spec: { x_field: xt, y_field: secondaryNum, aggregation: sAgg, limit: 50 },
      style: { number_format: "number" },
    });
  }
  if (layoutPreset === "executive_full" && primaryDim) {
    specs.push({
      title: `جدول جزئیات ${lbl(primaryDim)}`,
      chart_type: "table",
      layout: { x: 0, y: chartTopY + 7, w: 12, h: 7 },
      visual_spec: {
        x_field: primaryDim,
        y_field: primaryNum,
        secondary_y_field: secondaryNum !== primaryNum ? secondaryNum : undefined,
        aggregation: pAgg,
        limit: 50,
      },
      style: { number_format: "number" },
    });
  }

  const widgets = specs.map((sp, idx) => ({
    id: `w_${dashId}_${idx + 1}`,
    dashboard_id: dashId,
    page_id: pageId,
    title: sp.title,
    description: `ساخته‌شده با دستیار فارسی`,
    chart_type: sp.chart_type,
    dataset_id: ds.id,
    dataset_freshness: { status: "fresh", last_refreshed_at: new Date().toISOString(), row_count: ds.row_count || 0, data_version: 1 },
    layout: sp.layout,
    config: {
      style: sp.style || {},
      visualization: { show_legend: true, show_tooltip: true, data_labels: false },
      interactions: { cross_filter: true },
      advanced: { ai_generated: true },
    },
    query: { query_mode: "visual", sql_text: null, visual_spec: sp.visual_spec, parameters: {} },
    drill_rule: { hierarchy: [], level_rules: [], drill_through_page_id: null },
    widget_filters: [],
    permissions: { view: true, edit: true, delete: true, drill_down: true },
  }));

  const filterCol = ["region", "project_type", "department", "category", "year"].find((c) => dimCols.includes(c)) || primaryDim;
  const filters = filterCol
    ? [
        {
          id: `flt_${dashId}_1`,
          name: lbl(filterCol),
          field_name: filterCol,
          dataset_id: ds.id,
          filter_type: "multi_select",
          operator: "in",
          scope: "dashboard",
          default_value: [],
        },
      ]
    : [];

  const effTheme = theme || (demoData.themes || [])[0] || null;
  const createdDashDetail = {
    id: dashId,
    title,
    description: `ساخته‌شده با دستیار هوشمند فارسی از داده‌های «${ds.name}»`,
    is_public: false,
    embed_token: null,
    is_template: false,
    theme_id: effTheme?.id || null,
    theme: effTheme,
    can_edit: true,
    can_manage: true,
    permissions: { view: true, edit: true, add_widget: true, delete_widget: true, share: true, manage: true, comment: true },
    pages: [{ id: pageId, title: "نمای کلی", page_order: 0, layout: { columns: 12, items: widgets.map((w) => ({ i: w.id, ...w.layout })) } }],
    widgets,
    filters,
  };

  return {
    status: "dashboard_created",
    action: "create_dashboard",
    message_fa: `داشبورد «${title}» با ${widgets.length} گزارش و نمودار آماده با موفقیت ساخته شد!`,
    created_dashboard: {
      ...createdDashDetail,
      theme_name: effTheme?.name || "پیش‌فرض",
      dataset_id: ds.id,
      dataset_name: ds.name,
      widget_count: widgets.length,
    },
  };
}

function handleCreateDashboardDemo(body: any) {
  const clar = { ...(body?.clarifications || {}) };
  const freeReply = normalizeFaText(String(clar.free_text || "").trim());
  const norm = normalizeFaText(String(body?.prompt || "").trim());
  const combinedLow = normalizeFaText(`${norm} ${freeReply}`).toLowerCase();
  const extraDatasets = getExtraDatasets(body);
  const datasets: any[] = getEffectiveDatasets(extraDatasets);
  const themes: any[] = demoData.themes || [];

  let dashTitle: string | null = clar.title || null;
  if (!dashTitle) {
    const mt = norm.match(
      /(?:با\s+عنوان|تحت\s+عنوان|به\s+نام|با\s+نام|اسمش\s+رو\s+بذار|عنوان)\s+["«']?([^"»'\n]+?)["»']?(?=\s*(?:بساز|ایجاد|تولید|درست|$))/i
    );
    if (mt) dashTitle = mt[1].replace(/^[،,.«»"'\s]+|[،,.«»"'\s]+$/g, "");
  }

  let chosenDs: any = null;
  if (clar.dataset_id) chosenDs = datasets.find((d) => d.id === clar.dataset_id) || null;
  if (!chosenDs) chosenDs = matchDemoDatasetFromText(datasets, combinedLow);

  let chosenTheme: any = null;
  if (clar.theme_id) {
    chosenTheme = clar.theme_id === "__default__" ? themes[0] || null : themes.find((t) => t.id === clar.theme_id) || null;
  }
  if (!chosenTheme) chosenTheme = resolveDemoThemeFromText(combinedLow);

  let layoutPreset: string | null = clar.layout_preset || null;
  if (!layoutPreset) {
    if (["کامل", "همه", "مدیریتی", "جامع", "executive", "full"].some((k) => combinedLow.includes(k))) layoutPreset = "executive_full";
    else if (["روند", "خطی", "فقط نمودار", "charts"].some((k) => combinedLow.includes(k))) layoutPreset = "charts_only";
    else if (["خلاصه", "سهم", "دایره", "سریع", "کارت"].some((k) => combinedLow.includes(k))) layoutPreset = "kpi_and_pie";
  }

  const questions: any[] = [];
  if (!chosenDs) {
    questions.push({
      key: "dataset_id",
      question: "این داشبورد قرار است گزارش‌های کدام بخش یا موضوع را نشان دهد؟",
      options: datasets.map((d) => {
        const [lbl, hint] = friendlyDemoDatasetLabel(d);
        return { value: d.id, label: lbl, hint };
      }),
    });
  }
  if (!layoutPreset) {
    questions.push({
      key: "layout_preset",
      question: "دوست دارید چه نوع گزارش‌هایی داخل داشبورد ساخته شود؟",
      options: [
        { value: "executive_full", label: "بسته کامل مدیریتی (توصیه‌شده)", hint: "کارت‌های خلاصه آمار + نمودار میله‌ای + دایره‌ای + جدول جزئیات" },
        { value: "charts_only", label: "نمودارهای مقایسه‌ای و روندی", hint: "نمودار میله‌ای و خطی برای مقایسه و بررسی روند" },
        { value: "kpi_and_pie", label: "نمای سریع و خلاصه", hint: "اعداد کلیدی و نمودار سهم دایره‌ای" },
      ],
    });
  }
  if (!chosenTheme && themes.length) {
    const themeFa: Record<string, string> = {
      light: "روشن و تمیز",
      dark: "تیره و مدرن",
      corporate: "اداری و رسمی (آبی)",
      glass: "شیشه‌ای و حرفه‌ای",
      neon: "مدرن و پرکنتراست",
      minimal: "ساده و مینیمال",
      material: "مدرن متریال",
    };
    questions.push({
      key: "theme_id",
      question: "ظاهر و رنگ داشبورد چطور باشد؟",
      options: themes.slice(0, 5).map((th) => ({ value: th.id, label: themeFa[th.slug] || th.name })),
    });
  }

  if (questions.length) {
    return {
      status: "needs_clarification",
      action: "create_dashboard",
      message_fa: "برای اینکه داشبورد دقیقاً مطابق سلیقه شما ساخته شود، لطفاً به این موارد پاسخ دهید:",
      questions,
      parsed: {
        mode: "create_dashboard",
        dataset_id: chosenDs?.id || null,
        layout_preset: layoutPreset,
        theme_id: chosenTheme?.id || null,
        title: dashTitle,
      },
    };
  }

  if (!dashTitle) {
    const [friendly] = friendlyDemoDatasetLabel(chosenDs);
    dashTitle = `داشبورد ${friendly.split("(")[0].trim()}`;
  }
  return buildDemoFullDashboard(chosenDs, dashTitle, layoutPreset || "executive_full", chosenTheme);
}

function handleEditDashboardOrWidgetDemo(body: any, username: string | null) {
  const clar = { ...(body?.clarifications || {}) };
  const freeReply = normalizeFaText(String(clar.free_text || "").trim());
  const norm = normalizeFaText(String(body?.prompt || "").trim());
  const combinedText = normalizeFaText(`${norm} ${freeReply}`).trim();
  const combinedLow = combinedText.toLowerCase();

  // Merge built-in demo dashboards + client-side created dashboards from demo_context
  const customDashes: any[] = Array.isArray(body?.demo_context?.custom_dashboards) ? body.demo_context.custom_dashboards : [];
  const allDetailsMap = new Map<string, any>();
  for (const d of customDashes) {
    if (d?.id) allDetailsMap.set(d.id, d);
  }
  for (const [k, v] of Object.entries(demoData.dashboards_detail || {})) {
    if (!allDetailsMap.has(k)) allDetailsMap.set(k, v);
  }
  const allDashboards = Array.from(allDetailsMap.values());

  const reqDashId = clar.dashboard_id || body?.dashboard_id;
  let targetDash: any = reqDashId ? allDetailsMap.get(reqDashId) || null : null;
  if (!targetDash) {
    targetDash = allDashboards.find((d) => combinedLow.includes(String(d.title || "").toLowerCase())) || null;
  }
  if (!targetDash && allDashboards.length === 1) targetDash = allDashboards[0];

  if (!targetDash) {
    return {
      status: "needs_clarification",
      action: "edit",
      message_fa: "لطفاً مشخص کنید کدام داشبورد را می‌خواهید تغییر دهید:",
      questions: [
        {
          key: "dashboard_id",
          question: "کدام داشبورد را ویرایش کنیم؟",
          options: allDashboards.slice(0, 8).map((d) => ({ value: d.id, label: d.title })),
        },
      ],
      parsed: { mode: "edit" },
    };
  }

  // Merge client-side widget edits / deletions passed in demo_context if available
  const ctxWidgets: any[] = Array.isArray(body?.demo_context?.widgets)
    ? body.demo_context.widgets
    : targetDash.widgets || [];
  const widgets = ctxWidgets;

  const targetScope = clar.target_widget_id || body?.widget_id;
  let targetWidget: any = targetScope && targetScope !== "__dashboard__" ? widgets.find((w) => w.id === targetScope) || null : null;

  if (!targetWidget && targetScope !== "__dashboard__") {
    targetWidget = widgets.find((w) => combinedLow.includes(String(w.title || "").toLowerCase())) || null;
    if (!targetWidget && widgets.length) {
      if (["اول", "نخست"].some((k) => combinedLow.includes(k))) targetWidget = widgets[0];
      else if (combinedLow.includes("دوم") && widgets.length >= 2) targetWidget = widgets[1];
      else if (combinedLow.includes("سوم") && widgets.length >= 3) targetWidget = widgets[2];
      else if (combinedLow.includes("آخر")) targetWidget = widgets[widgets.length - 1];
    }
  }

  const isDashLevel =
    targetScope === "__dashboard__" ||
    (!targetWidget &&
      (["تم داشبورد", "تم ", "ظاهر داشبورد", "ظاهرش", "رنگ داشبورد", "قالب داشبورد", "تیره کن", "روشن کن", "شیشه ای کن"].some((k) =>
        combinedLow.includes(k)
      ) ||
        ["عنوان داشبورد", "اسم داشبورد", "نام داشبورد"].some((k) => combinedLow.includes(k)) ||
        (combinedLow.includes("فیلتر") && ["اضافه", "بذار", "قرار بده"].some((k) => combinedLow.includes(k)))));

  if (isDashLevel) {
    const themes: any[] = demoData.themes || [];
    let resolvedTheme = clar.theme_id ? themes.find((t) => t.id === clar.theme_id) || null : null;
    if (!resolvedTheme) resolvedTheme = resolveDemoThemeFromText(combinedLow);
    if (resolvedTheme) {
      return {
        status: "dashboard_updated",
        action: "edit_dashboard",
        message_fa: `ظاهر داشبورد «${targetDash.title}» با موفقیت به «${resolvedTheme.name}» تغییر یافت.`,
        updated_dashboard: {
          id: targetDash.id,
          title: targetDash.title,
          theme_id: resolvedTheme.id,
          theme_name: resolvedTheme.name,
        },
      };
    }

    const mTitle = combinedText.match(
      /(?:عنوان\s+داشبورد|اسم\s+داشبورد|نام\s+داشبورد|عنوانش|اسمش|نامش)\s*(?:را|رو)?\s*(?:تغییر\s+بده\s+به|عوض\s+کن\s+به|تغییر\s+بده|عوض\s+کن|بگذار|بذار|بکن|به)\s*["«']?([^"»'\n]+?)["»']?\s*$/i
    );
    const newDTitle = clar.new_title || (mTitle ? mTitle[1].replace(/^[،,.«»"'\s]+|[،,.«»"'\s]+$/g, "") : null);
    if (newDTitle && newDTitle.length >= 2 && !["عوض کن", "تغییر بده"].some((k) => newDTitle.includes(k))) {
      return {
        status: "dashboard_updated",
        action: "edit_dashboard",
        message_fa: `نام داشبورد با موفقیت به «${newDTitle}» تغییر یافت.`,
        updated_dashboard: { id: targetDash.id, title: newDTitle, theme_id: targetDash.theme_id },
      };
    }

    if (combinedLow.includes("فیلتر")) {
      const datasets: any[] = getEffectiveDatasets(getExtraDatasets(body));
      for (const w of widgets) {
        const dObj = datasets.find((d) => d.id === w.dataset_id);
        if (dObj) {
          const { colsInfo } = getDemoDatasetSchema(dObj);
          const col = matchDemoColumn(combinedLow, colsInfo, "dim");
          if (col) {
            const lbl = colsInfo.find((c: any) => c.name === col)?.label || col;
            const newFilter = {
              id: `flt_${Date.now()}`,
              name: lbl,
              field_name: col,
              dataset_id: dObj.id,
              filter_type: "multi_select",
              operator: "in",
              scope: "dashboard",
              default_value: [],
            };
            return {
              status: "dashboard_updated",
              action: "edit_dashboard",
              message_fa: `فیلتر «${lbl}» به داشبورد «${targetDash.title}» اضافه شد.`,
              updated_dashboard: { id: targetDash.id, title: targetDash.title, add_filter: newFilter },
            };
          }
        }
      }
    }

    return {
      status: "needs_clarification",
      action: "edit",
      message_fa: `چه تغییری روی تنظیمات کلی داشبورد «${targetDash.title}» انجام شود؟`,
      questions: [
        {
          key: "theme_id",
          question: "می‌توانید یکی از ظاهرهای زیر را انتخاب کنید یا نام جدید داشبورد را بنویسید:",
          options: themes.slice(0, 5).map((th) => ({ value: th.id, label: `تغییر ظاهر به: ${th.name}` })),
        },
      ],
      parsed: { mode: "edit", dashboard_id: targetDash.id, target_widget_id: "__dashboard__" },
    };
  }

  if (!targetWidget) {
    const opts: any[] = [{ value: "__dashboard__", label: "تغییر کلی داشبورد (نام، رنگ/تم، یا فیلتر)" }];
    for (const w of widgets.slice(0, 9)) {
      opts.push({ value: w.id, label: w.title, hint: DEMO_CHART_LABELS_FA[w.chart_type] || w.chart_type });
    }
    return {
      status: "needs_clarification",
      action: "edit",
      message_fa: `در داشبورد «${targetDash.title}»، کدام بخش یا نمودار را می‌خواهید تغییر دهید؟`,
      questions: [{ key: "target_widget_id", question: "مورد نظر را انتخاب کنید:", options: opts }],
      parsed: { mode: "edit", dashboard_id: targetDash.id },
    };
  }

  const editAction = clar.edit_action;
  let newChartType: string | null = clar.chart_type || extractChartTypeFromText(combinedLow);
  if (typeof editAction === "string" && editAction.startsWith("chart:")) {
    newChartType = editAction.split(":")[1];
  }
  const isDelete = editAction === "delete" || ["حذف", "پاک کن", "بردار", "delete", "remove"].some((k) => combinedLow.includes(k));
  if (isDelete) {
    return {
      status: "widget_deleted",
      action: "delete_widget",
      message_fa: `نمودار «${targetWidget.title}» با موفقیت از داشبورد حذف شد.`,
      deleted_widget_id: targetWidget.id,
      dashboard_id: targetDash.id,
    };
  }

  let newWTitle: string | null = clar.new_title || null;
  if (!newWTitle) {
    const mwt = combinedText.match(
      /(?:عنوان|اسم|نام)ش?\s*(?:ویجت|نمودار|کارت)?\s*(?:را|رو)?\s*(?:تغییر\s+بده\s+به|عوض\s+کن\s+به|تغییر\s+بده|عوض\s+کن|بگذار|بذار|بکن|به)\s*["«']?([^"»'\n]+?)["»']?(?=\s*(?:و\s+(?:نوعش|تبدیل)|$))/i
    );
    if (mwt) {
      const cand = mwt[1].replace(/^[،,.«»"'\s]+|[،,.«»"'\s]+$/g, "");
      if (cand.length >= 2 && !["عوض کن", "تغییر بده", "نمودار را"].some((k) => cand.includes(k))) {
        newWTitle = cand;
      }
    }
  }

  const extraDatasets = getExtraDatasets(body);
  const datasets: any[] = getEffectiveDatasets(extraDatasets);
  const dsObj = datasets.find((d) => d.id === targetWidget.dataset_id) || null;
  let newX: string | null = null;
  let newY: string | null = null;
  let newAgg: string | null = null;
  if (dsObj) {
    const { colsInfo } = getDemoDatasetSchema(dsObj);
    const mDim = combinedText.match(/(?:بر\s+اساس|به\s+تفکیک|بر\s+حسب|روی\s+محور)\s+["«']?([^"»'\n]+?)["»']?(?=\s*(?:نشان|نمایش|تبدیل|کن|بده|$))/i);
    if (mDim) newX = matchDemoColumn(mDim[1], colsInfo, "dim");
    const mHit = matchDemoColumn(combinedLow, colsInfo, "measure");
    if (mHit && mHit !== targetWidget.query?.visual_spec?.y_field) {
      newY = mHit;
      if (["میانگین", "avg"].some((k) => combinedLow.includes(k)) || ["score", "pct", "rate"].some((k) => mHit.toLowerCase().includes(k))) {
        newAgg = "AVG";
      } else if (["تعداد رکورد", "شمارش", "count"].some((k) => combinedLow.includes(k))) {
        newAgg = "COUNT";
        newY = "*";
      } else {
        newAgg = "SUM";
      }
    }
  }

  if (!newChartType && !newWTitle && !newX && !newY) {
    return {
      status: "needs_clarification",
      action: "edit",
      message_fa: `چه تغییری می‌خواهید روی «${targetWidget.title}» انجام شود؟`,
      questions: [
        {
          key: "edit_action",
          question: "می‌توانید یکی از گزینه‌ها را بزنید یا تغییر دلخواه خود را به فارسی تایپ کنید (مثلاً: عنوانش را بگذار آمار جدید):",
          options: [
            { value: "chart:bar", label: "تبدیل به نمودار میله‌ای" },
            { value: "chart:line", label: "تبدیل به نمودار خطی (روندی)" },
            { value: "chart:pie", label: "تبدیل به نمودار دایره‌ای" },
            { value: "chart:table", label: "نمایش به صورت جدول" },
            { value: "chart:kpi", label: "نمایش به شکل کارت عدد خلاصه" },
            { value: "delete", label: "حذف این نمودار از داشبورد" },
          ],
        },
      ],
      parsed: { mode: "edit", dashboard_id: targetDash.id, target_widget_id: targetWidget.id },
    };
  }

  const effChart = newChartType || targetWidget.chart_type;
  const effTitle = newWTitle || targetWidget.title;
  const baseVisualSpec: any = {
    ...(targetWidget.query?.visual_spec || {}),
    ...(newX ? { x_field: newX } : {}),
    ...(newY ? { y_field: newY } : {}),
    ...(newAgg ? { aggregation: newAgg } : {}),
  };
  if (effChart === "bpmn" && !baseVisualSpec.bpmn_config && dsObj) {
    const { colsInfo: cInfoE } = getDemoDatasetSchema(dsObj);
    const infE = inferDemoBpmnConfig(dsObj, cInfoE);
    if (infE) baseVisualSpec.bpmn_config = { ...infE, heatmap_enabled: true };
  }
  const visualSpec = baseVisualSpec;
  const previewData = dsObj ? executeVisualSpec(dsObj.id, visualSpec, username, [], null, effChart === "bpmn", extraDatasets) : null;

  return {
    status: "widget_updated",
    action: "edit_widget",
    message_fa: `«${effTitle}» با موفقیت به‌روزرسانی شد (${DEMO_CHART_LABELS_FA[effChart] || effChart}).`,
    widget: {
      id: targetWidget.id,
      dashboard_id: targetDash.id,
      page_id: targetWidget.page_id,
      title: effTitle,
      chart_type: effChart,
      dataset_id: targetWidget.dataset_id,
      visual_spec: visualSpec,
    },
    widget_spec: {
      title: effTitle,
      chart_type: effChart,
      visual_spec: visualSpec,
      style: {},
      visualization: { show_legend: true, show_tooltip: true },
    },
    preview_data: previewData,
  };
}

function handleAssistantDemo(body: any, username: string | null) {
  const clar = { ...(body?.clarifications || {}) };
  let effMode = clar.mode || (body?.mode && body.mode !== "auto" ? body.mode : null);
  const normLow = normalizeFaText(String(body?.prompt || "")).toLowerCase();

  if (!effMode) {
    const isEditKw = [
      "ویرایش", "تغییر", "عوض کن", "تبدیل کن", "تبدیل به", "بکن ", "حذف", "پاک کن", "بردار",
      "اسمش رو", "تم داشبورد", "ظاهر داشبورد", "رنگ داشبورد", "فیلتر", "تیره کن", "روشن کن",
    ].some((k) => normLow.includes(k));
    const hasDash = ["داشبورد", "dashboard", "صفحه گزارش"].some((k) => normLow.includes(k));
    const hasWidget = ["ویجت", "نمودار", "کارت", "جدول", "شاخص", "kpi", "chart", "widget", "bpmn", "گلوگاه", "گردش کار"].some((k) => normLow.includes(k));
    const isQa = ["چقدر", "کدام", "چند ", "میانگین", "مجموع", "جمع ", "بیشترین", "کمترین", "چرا", "؟", "?"].some((k) => normLow.includes(k));

    if (body?.widget_id || isEditKw) effMode = "edit";
    else if (hasDash && !hasWidget) effMode = "create_dashboard";
    else if (isQa && !hasWidget) effMode = "qa";
    else if (hasWidget || ["از روی منبع", "بر اساس", "به تفکیک"].some((k) => normLow.includes(k))) effMode = "add_widget";
  }

  if (!effMode) {
    return {
      status: "needs_clarification",
      action: "choose_mode",
      message_fa: "خوش آمدید! می‌خواهید با هم چه کاری انجام دهیم؟",
      questions: [
        {
          key: "mode",
          question: "یکی از کارهای زیر را انتخاب کنید:",
          options: [
            { value: "qa", label: "پرسش و تحلیل مستقیم از داده‌ها (Q&A)", hint: "پاسخ محاسبه‌شده با منبع واقعی" },
            { value: "create_dashboard", label: "ساخت یک داشبورد کامل جدید", hint: "با چند نمودار و کارت خلاصه آماده" },
            { value: "add_widget", label: "افزودن یک نمودار یا عدد آماری جدید", hint: "به داشبورد فعلی یا دلخواه" },
            { value: "edit", label: "ویرایش داشبورد یا نمودارهای موجود", hint: "تغییر رنگ/ظاهر، نام، نوع نمودار یا حذف" },
          ],
        },
      ],
      parsed: {},
    };
  }

  if (effMode === "qa") {
    const wRes: any = handleTextToWidgetDemo(body, username);
    if (wRes.status === "needs_clarification") return wRes;
    const rows = wRes.preview_data?.rows || [];
    const cols = wRes.preview_data?.columns || [];
    return {
      status: "qa_answered",
      action: "qa",
      message_fa: rows.length
        ? `پاسخ محاسبه‌شده از داده‌ها (${rows.length} نتیجه): ${cols.slice(0, 2).map((c: string) => `${c}: ${rows[0][c]}`).join(" · ")}`
        : "داده‌ای یافت نشد.",
      generated_sql: wRes.generated_sql || "-- Preview Demo SQL",
      recommended_chart_type: wRes.widget_spec?.chart_type || "bar",
      source_datasets: ["Demo Dataset"],
      confidence: 0.9,
      preview_data: wRes.preview_data,
    };
  }
  if (effMode === "create_dashboard") return handleCreateDashboardDemo(body);
  if (effMode === "edit") return handleEditDashboardOrWidgetDemo(body, username);
  return { action: "add_widget", ...handleTextToWidgetDemo(body, username) };
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
  if (method === "POST" && segments[0] === "dashboards" && segments[1] === "public" && segments[2] && segments[3] === "widgets" && segments[4] && segments[5] === "bpmn-drill") {
    const token = segments[2];
    const detMatch = Object.values(demoData.dashboards_detail || {}).find(
      (d: any) => d.is_public && d.embed_token === token
    ) as any;
    if (!detMatch) return { status: 404, body: { detail: "Public dashboard not found" } };
    const w = (detMatch.widgets || []).find((x: any) => x.id === segments[4]);
    if (!w) return { status: 404, body: { detail: "Widget not found" } };
    return { status: 200, body: executeDemoBpmnStepDrill(w, null, body, getExtraDatasets(body)) };
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
      widgets[w.id] = executeWidgetForDemo(
        w,
        null,
        body?.active_filters || [],
        body?.cross_filters || [],
        dStack,
        [],
        body?.bpmn_process_map?.[w.id] || null,
        body?.bpmn_field_overrides_map?.[w.id] || null
      );
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

  const extraDatasets = getExtraDatasets(body);

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
    const customDashes: any[] = Array.isArray(body?.demo_context?.custom_dashboards) ? body.demo_context.custom_dashboards : [];
    const det = customDashes.find((d) => d?.id === segments[1]) || findDashboard(segments[1]);
    if (!det) return { status: 404, body: { detail: "Dashboard not found" } };
    const widgets: Record<string, any> = {};
    const pageId = body?.page_id;
    for (const w of det.widgets || []) {
      if (pageId && w.page_id !== pageId) continue;
      const dStack = body?.drill_states?.[w.id] || [];
      widgets[w.id] = executeWidgetForDemo(
        w,
        username,
        body?.active_filters || [],
        body?.cross_filters || [],
        dStack,
        extraDatasets,
        body?.bpmn_process_map?.[w.id] || null,
        body?.bpmn_field_overrides_map?.[w.id] || null
      );
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
    const customDashes: any[] = Array.isArray(body?.demo_context?.custom_dashboards) ? body.demo_context.custom_dashboards : [];
    let foundWidget: any = null;
    for (const d of customDashes) {
      const hit = (d?.widgets || []).find((x: any) => x.id === segments[2]);
      if (hit) { foundWidget = hit; break; }
    }
    if (!foundWidget) {
      foundWidget = findWidget(segments[2])?.widget || null;
    }
    if (!foundWidget) return { status: 404, body: { detail: "Widget not found" } };
    return {
      status: 200,
      body: executeWidgetForDemo(
        foundWidget,
        username,
        body?.active_filters || [],
        body?.cross_filters || [],
        body?.drill_stack || [],
        extraDatasets,
        body?.bpmn_process_name || null,
        body?.bpmn_field_overrides || null
      ),
    };
  }
  if (method === "POST" && segments[0] === "dashboards" && segments[1] === "widgets" && segments[3] === "bpmn-drill") {
    const customDashes: any[] = Array.isArray(body?.demo_context?.custom_dashboards) ? body.demo_context.custom_dashboards : [];
    let foundWidget: any = null;
    for (const d of customDashes) {
      const hit = (d?.widgets || []).find((x: any) => x.id === segments[2]);
      if (hit) { foundWidget = hit; break; }
    }
    if (!foundWidget) {
      foundWidget = findWidget(segments[2])?.widget || null;
    }
    if (!foundWidget) return { status: 404, body: { detail: "Widget not found" } };
    return { status: 200, body: executeDemoBpmnStepDrill(foundWidget, username, body, extraDatasets) };
  }
  if (method === "POST" && segments[0] === "dashboards" && segments[1] === "widgets" && segments[3] === "drill-details") {
    const customDashes: any[] = Array.isArray(body?.demo_context?.custom_dashboards) ? body.demo_context.custom_dashboards : [];
    let foundWidget: any = null;
    for (const d of customDashes) {
      const hit = (d?.widgets || []).find((x: any) => x.id === segments[2]);
      if (hit) { foundWidget = hit; break; }
    }
    if (!foundWidget) {
      foundWidget = findWidget(segments[2])?.widget || null;
    }
    if (!foundWidget) return { status: 404, body: { detail: "Widget not found" } };
    const dsId = foundWidget.dataset_id;
    const dsObj = getEffectiveDatasets(extraDatasets).find((d: any) => d.id === dsId);
    const { dimCols } = dsObj ? getDemoDatasetSchema(dsObj) : { dimCols: [] };
    const hier = (foundWidget.drill_rule?.hierarchy || []).map((h: any) => h?.field).filter(Boolean);
    const availableDims = Array.from(new Set([...hier, ...dimCols])).filter(Boolean) as string[];
    const chosenDim = body?.breakdown_dimension && availableDims.includes(body.breakdown_dimension)
      ? body.breakdown_dimension
      : (availableDims[0] || null);

    const combinedFilters = [
      ...(body?.active_filters || []),
      ...(body?.cross_filters || []).filter((c: any) => c?.source_widget_id !== foundWidget.id),
    ];
    const limit = Math.max(10, Math.min(Number(body?.limit || 100), 200));
    const rawRes = executeVisualSpec(dsId, { ...(foundWidget.query?.visual_spec || {}), limit }, username, combinedFilters, null, true, extraDatasets);
    const bdRes = chosenDim && !foundWidget.query?.visual_spec?.multi_source_agg
      ? executeVisualSpec(dsId, { ...(foundWidget.query?.visual_spec || {}), limit: 25 }, username, combinedFilters, chosenDim, false, extraDatasets)
      : { columns: [], rows: [] };
    return {
      status: 200,
      body: {
        widget_id: foundWidget.id,
        title: foundWidget.title,
        formula: foundWidget.query?.visual_spec?.formula || null,
        drill_through_page_id: foundWidget.drill_rule?.drill_through_page_id || null,
        available_dimensions: availableDims,
        breakdown_dimension: chosenDim,
        breakdown_columns: bdRes.columns,
        breakdown_rows: bdRes.rows,
        columns: rawRes.columns,
        rows: rawRes.rows,
        row_count: rawRes.row_count,
      },
    };
  }
  if (method === "POST" && segments[0] === "dashboards" && segments[1] === "widgets" && segments[3] === "switch-chart") {
    const found = findWidget(segments[2]);
    const targetType = body?.target_chart_type || found?.widget?.chart_type || "bar";
    if (!found) {
      return { status: 200, body: { id: segments[2], chart_type: targetType } };
    }
    found.widget.chart_type = targetType;
    if (targetType === "bpmn" && found.widget.dataset_id) {
      const dsObj = getEffectiveDatasets(extraDatasets).find((d: any) => d.id === found.widget.dataset_id);
      if (dsObj && !found.widget.query?.visual_spec?.bpmn_config) {
        const { colsInfo } = getDemoDatasetSchema(dsObj);
        const inf = inferDemoBpmnConfig(dsObj, colsInfo);
        if (inf) {
          found.widget.query = found.widget.query || {};
          found.widget.query.visual_spec = { ...(found.widget.query.visual_spec || {}), bpmn_config: inf };
        }
      }
    }
    return { status: 200, body: { ...found.widget, chart_type: targetType } };
  }
  if (method === "POST" && segments[0] === "dashboards" && segments[2] === "widgets") {
    const wid = `dw_${Date.now()}_${Math.floor(Math.random() * 1000)}`;
    return {
      status: 200,
      body: {
        id: wid,
        dashboard_id: segments[1],
        page_id: body?.page_id || "page-1",
        title: body?.title || "New Widget",
        chart_type: body?.chart_type || "bar",
        dataset_id: body?.dataset_id || null,
        layout: {
          x: body?.layout_x ?? 0,
          y: body?.layout_y ?? 99,
          w: body?.layout_w ?? (body?.chart_type === "bpmn" ? 12 : 6),
          h: body?.layout_h ?? (body?.chart_type === "bpmn" ? 10 : 7),
        },
        visual_spec: body?.visual_spec || {},
      },
    };
  }
  if (method === "POST" && path === "/queries/convert-to-widget") {
    const wid = `qw_${Date.now()}_${Math.floor(Math.random() * 1000)}`;
    return {
      status: 200,
      body: { widget_id: wid, title: body?.title || "Query Widget", chart_type: body?.chart_type || "bar" },
    };
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
  if (method === "GET" && segments[0] === "dashboards" && segments[2] === "named-layouts") {
    return { status: 200, body: [] };
  }
  if (method === "GET" && path === "/actions") {
    return { status: 200, body: [] };
  }
  if (method === "GET" && segments[0] === "dashboards" && segments[2] === "comments") {
    return { status: 200, body: [{ id: "c1", widget_id: null, author_name: "System Administrator", content: "Q4 executive KPIs synced with warehouse.", created_at: "2026-11-16T09:00:00Z" }] };
  }
  if (method === "POST" && segments[0] === "dashboards" && segments[2] === "share") {
    const det = findDashboard(segments[1]);
    return { status: 200, body: { embed_token: det?.embed_token || "_TQsvwNqM9Bt93IWvC5Vjr6RC3hkVi-A", is_public: true } };
  }

  // Datasets & Semantic Layer
  if (method === "GET" && path === "/about") {
    return {
      status: 200,
      body: {
        name: "Enterprise BI Platform",
        version: APP_VERSION,
        previous_version: PREVIOUS_VERSION,
        schema_version: SCHEMA_VERSION,
        releases: RELEASE_NOTES,
      },
    };
  }
  if (method === "GET" && path === "/jobs") {
    return { status: 200, body: [] };
  }
  if (method === "GET" && segments[0] === "jobs" && segments[1]) {
    return { status: 200, body: { id: segments[1], status: "completed", progress_pct: 100, stage_label: "تکمیل‌شده" } };
  }
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
  if (method === "GET" && segments[0] === "datasets" && segments[2] === "analytics-diagnostics") {
    return {
      status: 200,
      body: {
        dataset_id: segments[1],
        duplicate_rows: 0,
        incomplete_columns: [],
        outliers: [],
        correlations: [],
        period_comparison: null,
        trend_anomalies: [],
        row_accounting: { input_rows: 100, processed_rows: 100, valid_rows: 100, invalid_rows: 0 },
      },
    };
  }
  if (method === "GET" && segments[0] === "datasets" && segments[2] === "preview") {
    const limit = Number(searchParams.get("limit") || 30);
    const { rows, columns } = getTableForDataset(segments[1], extraDatasets);
    const visible = applyRlsAndFilters(rows, username, []).slice(0, limit);
    return { status: 200, body: { columns, rows: visible, row_count: visible.length } };
  }
  if (method === "POST" && segments[0] === "datasets" && segments[2] === "filter-options") {
    const { rows } = getTableForDataset(segments[1], extraDatasets);
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

  // Data Sources & Excel/CSV File Ingestion
  if (method === "POST" && (path === "/data-sources/files/preview" || path === "/data-sources/files/import")) {
    const f = body?.__file;
    if (!f?.buffer) {
      return { status: 400, body: { detail: "No file uploaded" } };
    }
    let colRenames: Record<string, string> = {};
    let typeOverrides: Record<string, string> = {};
    try {
      if (body.column_renames_json) colRenames = JSON.parse(String(body.column_renames_json));
    } catch { /* ignore */ }
    try {
      if (body.type_overrides_json) typeOverrides = JSON.parse(String(body.type_overrides_json));
    } catch { /* ignore */ }
    const parsed = await parseSpreadsheetBuffer(f.buffer, f.name, {
      sheetName: body.sheet_name || undefined,
      hasHeader: String(body.has_header ?? "true") !== "false",
      nullStrategy: body.null_strategy || "keep",
      deduplicate: String(body.deduplicate ?? "false") === "true",
      columnRenames: colRenames,
      typeOverrides: typeOverrides,
    });
    if (path === "/data-sources/files/preview") {
      return {
        status: 200,
        body: {
          sheets: parsed.sheets,
          sheet_summaries: (parsed.sheets || []).map((s: string) => ({ sheet_name: s, estimated_rows: parsed.total_rows })),
          selected_sheet: parsed.selected_sheet,
          total_rows: parsed.total_rows,
          duplicate_count: parsed.duplicate_count,
          columns: parsed.columns,
          preview_rows: parsed.preview_rows,
          row_accounting: {
            input_rows: parsed.total_rows,
            processed_rows: parsed.total_rows,
            valid_rows: parsed.total_rows,
            invalid_rows: 0,
            warnings: [],
          },
        },
      };
    }
    const dsName = String(body.dataset_name || f.name.replace(/\.[^.]+$/, "")).trim() || "Excel Dataset";
    const dsId = `ds_xl_${Date.now()}_${Math.floor(Math.random() * 1000)}`;
    const whTable = `wh_upload_${Date.now()}`;
    return {
      status: 200,
      body: {
        id: dsId,
        dataset_id: dsId,
        name: dsName,
        warehouse_table: whTable,
        row_count: parsed.total_rows,
        columns_count: parsed.columns.length,
        columns: parsed.columns.map((c) => c.field_name),
        fields: parsed.columns.map((c, idx) => ({
          id: `${dsId}_f_${idx + 1}`,
          field_name: c.field_name,
          display_name: c.display_name,
          data_type: c.data_type,
          semantic_role: c.semantic_role,
          nullable: c.nullable,
        })),
        rows: parsed.all_rows,
        created_at: new Date().toISOString(),
      },
    };
  }
  if (method === "GET" && path === "/data-sources/composite-datasets") {
    return { status: 200, body: [] };
  }
  if (method === "POST" && path === "/data-sources/composite-datasets") {
    return { status: 200, body: { composite_id: `comp_${Date.now()}`, total_rows: 0, status: "completed" } };
  }
  if (method === "GET" && path === "/data-sources") {
    return { status: 200, body: demoData.data_sources || [] };
  }
  if (method === "GET" && path === "/data-sources/supported-types") {
    const base = demoData.supported_types || { databases: [], files: [] };
    const mergedFiles = Array.from(
      new Set([...(base.files || []), "xlsx", "xls", "csv", "tsv", "json", "jsonl", "ndjson", "log", "txt", "xml"])
    );
    return { status: 200, body: { ...base, files: mergedFiles } };
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
    const res = executeVisualSpec(body?.dataset_id, body?.visual_spec || {}, username, body?.filters || [], null, false, extraDatasets);
    return { status: 200, body: { ...res, execution_ms: 4.8, generated_sql: "-- Preview Visual Query" } };
  }
  if (method === "POST" && path === "/queries/execute-sql") {
    return executeSimpleDemoSql(body?.sql_text || "", username, extraDatasets);
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
  if (method === "GET" && segments[0] === "rbac" && segments[1] === "users" && segments[3] === "effective-permissions") {
    const u = (demoData.rbac_users || []).find((x: any) => x.id === segments[2]);
    return {
      status: 200,
      body: {
        user_id: segments[2],
        username: u?.username || "demo",
        roles: u?.roles || [],
        effective_permissions: [{ resource_type: "*", resource_id: "*", action: "*", effect: "allow", source: "demo" }],
        has_admin_wildcard: true,
      },
    };
  }
  if (method === "GET" && path === "/rbac/roles") {
    return { status: 200, body: demoData.rbac_roles || { roles: [], catalog: [] } };
  }
  if (method === "GET" && path === "/rbac/groups") {
    return { status: 200, body: demoData.rbac_groups || [] };
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
  // Intelligent KPI & Semantic Class Discovery (Demo Preview Parity)
  if (method === "GET" && path === "/ai/semantic-classes") {
    return {
      status: 200,
      body: [
        { id: "Additive Measure", name_fa: "سنجه جمع‌پذیر", policy: "additive", default_agg: "SUM", sum_allowed: true },
        { id: "Semi-additive Measure", name_fa: "سنجه نیمه‌جمع‌پذیر", policy: "semi_additive", default_agg: "LAST_VALUE", sum_allowed: false },
        { id: "Non-additive Measure", name_fa: "سنجه غیرجمع‌پذیر", policy: "non_additive", default_agg: "AVG", sum_allowed: false },
        { id: "Count Measure", name_fa: "سنجه شمارشی", policy: "count", default_agg: "SUM", sum_allowed: true },
        { id: "Ratio", name_fa: "نسبت", policy: "non_additive", default_agg: "AVG", sum_allowed: false },
        { id: "Percentage", name_fa: "درصد / نرخ", policy: "non_additive", default_agg: "AVG", sum_allowed: false },
        { id: "Currency", name_fa: "مبلغ مالی", policy: "additive", default_agg: "SUM", sum_allowed: true },
        { id: "Duration", name_fa: "مدت زمان / تاخیر", policy: "duration", default_agg: "AVG", sum_allowed: true },
        { id: "Date/Time", name_fa: "تاریخ و زمان", policy: "temporal", default_agg: "NONE", sum_allowed: false },
        { id: "Identifier", name_fa: "شناسه / کد یکتا", policy: "identifier", default_agg: "COUNT_DISTINCT", sum_allowed: false },
        { id: "Category", name_fa: "دسته‌بندی", policy: "dimension", default_agg: "COUNT_DISTINCT", sum_allowed: false },
        { id: "Status", name_fa: "وضعیت", policy: "dimension", default_agg: "COUNT_DISTINCT", sum_allowed: false },
        { id: "Geographic", name_fa: "موقعیت جغرافیایی", policy: "dimension", default_agg: "COUNT_DISTINCT", sum_allowed: false },
        { id: "Boolean", name_fa: "بولی / دودویی", policy: "boolean", default_agg: "COUNT", sum_allowed: false },
        { id: "Target", name_fa: "هدف / بودجه", policy: "additive", default_agg: "SUM", sum_allowed: true },
        { id: "Actual", name_fa: "عملکرد واقعی", policy: "additive", default_agg: "SUM", sum_allowed: true },
        { id: "Quantity", name_fa: "تعداد / مقدار", policy: "additive", default_agg: "SUM", sum_allowed: true },
        { id: "Unit Price", name_fa: "قیمت واحد / فی", policy: "non_additive", default_agg: "AVG", sum_allowed: false },
        { id: "Cost", name_fa: "هزینه", policy: "additive", default_agg: "SUM", sum_allowed: true },
        { id: "Revenue", name_fa: "فروش / درآمد", policy: "additive", default_agg: "SUM", sum_allowed: true },
        { id: "Profit", name_fa: "سود", policy: "additive", default_agg: "SUM", sum_allowed: true },
        { id: "Balance", name_fa: "مانده / موجودی لحظه‌ای", policy: "semi_additive", default_agg: "LAST_VALUE", sum_allowed: false },
      ],
    };
  }
  if (method === "GET" && segments[0] === "ai" && segments[1] === "recommend" && segments[2]) {
    const allDs = getEffectiveDatasets(extraDatasets);
    const ds = allDs.find((d) => d.id === segments[2]) || allDs[0];
    if (!ds) return { status: 404, body: { detail: "Dataset not found" } };
    const table = getTableForDataset(ds.id, extraDatasets);
    const rows = table.rows || [];
    const fields = ds.fields || [];
    const fieldClassifications = fields.map((f: any) => {
      const fn = String(f.field_name || "").toLowerCase();
      const dt = String(f.data_type || "string").toLowerCase();
      const isNum = ["int", "integer", "float", "decimal", "numeric", "number"].includes(dt);
      let semanticClass = isNum ? "Additive Measure" : "Category";
      let sumAllowed = isNum;
      let defAgg = isNum ? "SUM" : "COUNT";
      if (/(revenue|sales|income)/.test(fn)) semanticClass = "Revenue";
      else if (/(cost|expense)/.test(fn)) semanticClass = "Cost";
      else if (/(profit|margin)/.test(fn) && !/pct|rate/.test(fn)) semanticClass = "Profit";
      else if (/(pct|percent|rate|ratio)/.test(fn) && isNum) {
        semanticClass = "Percentage";
        sumAllowed = false;
        defAgg = "AVG";
      } else if (/(date|time|created)/.test(fn) || dt.includes("date")) {
        semanticClass = "Date/Time";
      } else if (/(region|city|province|country|state)/.test(fn)) {
        semanticClass = "Geographic";
      } else if (/(_id|^id$|code)/.test(fn)) {
        semanticClass = "Identifier";
        sumAllowed = false;
        defAgg = "COUNT_DISTINCT";
      }
      const numVals = isNum ? rows.map((r: any) => Number(r[f.field_name])).filter((v: number) => !Number.isNaN(v)) : [];
      const sum = numVals.reduce((a: number, b: number) => a + b, 0);
      const avg = numVals.length ? Math.round((sum / numVals.length) * 100) / 100 : undefined;
      return {
        field_name: f.field_name,
        display_name: f.display_name || f.field_name,
        data_type: isNum ? "numeric" : dt.includes("date") ? "date" : "categorical",
        raw_type: f.data_type,
        semantic_class: semanticClass,
        confidence: 0.9,
        needs_user_confirmation: false,
        default_aggregation: defAgg,
        sum_allowed: sumAllowed,
        stats: {
          avg,
          median: avg,
          stddev: avg ? Math.round(avg * 0.25 * 100) / 100 : undefined,
          null_pct: 0,
          distinct_count: new Set(rows.map((r: any) => r[f.field_name])).size,
          iqr_outlier_count: 0,
        },
      };
    });
    const calMode = searchParams.get("calendar_mode") || "jalali";
    const evalStd = evaluateDemoStandardAndBusinessKpis(ds, rows, calMode, {});
    const numCls = fieldClassifications.filter((f: any) => f.data_type === "numeric" && f.semantic_class !== "Identifier");
    const extraKpis = numCls.slice(0, 6).map((f: any, idx: number) => {
      const vals = rows.map((r: any) => Number(r[f.field_name])).filter((v: number) => !Number.isNaN(v));
      const sum = vals.reduce((a: number, b: number) => a + b, 0);
      const val = f.sum_allowed ? sum : vals.length ? sum / vals.length : 0;
      const fmt = ["Revenue", "Cost", "Profit", "Currency"].includes(f.semantic_class)
        ? "currency"
        : f.semantic_class === "Percentage"
        ? "percentage"
        : "number";
      return {
        id: `demo_${f.field_name}`,
        slug: `${f.field_name}_${f.default_aggregation.toLowerCase()}`,
        name: `${f.sum_allowed ? "مجموع" : "میانگین"} ${f.display_name}`,
        formula: `${f.default_aggregation}("${f.field_name}")`,
        base_fields: [f.field_name],
        format_type: fmt,
        display_format: fmt,
        unit: fmt === "percentage" ? "%" : "",
        value: Math.round(val * 100) / 100,
        formatted_value: formatKpiVal(val, fmt, fmt === "percentage" ? "%" : ""),
        category: idx === 0 ? "domain" : "aggregate",
        score: 88 - idx * 4,
        confidence: 0.92,
        status: "suggested",
        computability_status: "computable",
        computability_status_fa: "قابل محاسبه",
        source: ds.name,
        source_datasets: [ds.name],
        comparison_basis: "کل دوره داده‌ها",
        limitations: [],
      };
    });
    const stdSigs = new Set(evalStd.computableStandard.map((k) => k.formula.toLowerCase().replace(/\s+/g, "")));
    const filteredExtra = extraKpis.filter((k: any) => !stdSigs.has(k.formula.toLowerCase().replace(/\s+/g, "")));
    const allBiz = [...evalStd.computableStandard, ...filteredExtra];
    return {
      status: 200,
      body: {
        dataset_id: ds.id,
        dataset_name: ds.name,
        domain_detection: {
          primary_domain: "sales_ecommerce",
          primary_domain_fa: "فروش و عملکرد سازمانی",
          confidence: 0.88,
          evidence_fa: `شناسایی خودکار بر اساس ستون‌های ${fields.slice(0, 4).map((x: any) => x.field_name).join("، ")}`,
        },
        deep_profile: {
          row_count: rows.length || ds.row_count || 0,
          field_count: fields.length,
          duplicate_row_count: 0,
          completeness_pct: 99.6,
          is_approximate: false,
        },
        field_classifications: fieldClassifications,
        kpis: {
          standard: evalStd.standard,
          selected: allBiz.slice(0, 5),
          suggested: allBiz.slice(5),
          needs_confirmation: [],
          needs_data: evalStd.needsData,
          data_quality: [
            {
              id: "dq_completeness",
              name: "درصد تکمیل داده‌ها",
              formula: "100.0",
              formatted_value: "99.6%",
            },
          ],
          uncomputable: evalStd.uncomputable,
          all_business: allBiz,
          all_candidates: [...evalStd.standard, ...filteredExtra],
        },
        recommended_pages: [
          { title: "۱. خلاصه مدیریتی و KPIها", page_key: "executive_summary", widgets_count: 6, chart_types: ["kpi", "bullet", "grouped_bar", "bar", "pie"] },
          { title: "۲. تحلیل روند و زمان", page_key: "trends", widgets_count: 3, chart_types: ["line", "area", "calendar_heatmap"] },
          { title: "۳. مقایسه و تفکیک ابعادی", page_key: "breakdown", widgets_count: 3, chart_types: ["grouped_bar", "treemap"] },
          { title: "۴. توزیع و شاخص‌های آماری", page_key: "statistical", widgets_count: 2, chart_types: ["histogram", "scatter"] },
          { title: "۵. جزئیات رکوردها و کیفیت داده", page_key: "details", widgets_count: 2, chart_types: ["table", "kpi"] },
        ],
        suggested_widgets: allBiz.map((k: any) => ({ title: k.name, chart_type: "kpi" })),
      },
    };
  }
  if (method === "POST" && segments[0] === "ai" && segments[1] === "datasets" && segments[3] === "field-overrides") {
    return { status: 200, body: { status: "ok", updated: Array.isArray(body?.overrides) ? body.overrides.length : 0 } };
  }
  if (method === "POST" && segments[0] === "ai" && segments[1] === "datasets" && segments[3] === "kpis" && segments[4] === "preview") {
    const allDs = getEffectiveDatasets(extraDatasets);
    const ds = allDs.find((d) => d.id === segments[2]) || allDs[0];
    const table = ds ? getTableForDataset(ds.id, extraDatasets) : { rows: [] };
    const rows = table.rows || [];
    const formula = String(body?.formula || "");
    const msa = body?.multi_source_agg || null;
    const rightRows = msa?.right_dataset_id ? getTableForDataset(msa.right_dataset_id, extraDatasets).rows : undefined;
    const val = evaluateDemoKpiFormula(formula, rows, body?.calendar_mode || "jalali", msa, rightRows);
    const fmt = body?.format_type || "number";
    return {
      status: 200,
      body: {
        valid: true,
        name: body?.name || "Custom KPI",
        formula,
        value: val !== null ? Math.round(val * 10000) / 10000 : null,
        formatted_value: formatKpiVal(val, fmt, body?.unit || (fmt === "percentage" ? "%" : "")),
        target_value: body?.target_value,
        warnings: [],
      },
    };
  }
  if (method === "POST" && segments[0] === "ai" && segments[1] === "datasets" && segments[3] === "kpis" && segments[4] === "register") {
    return { status: 200, body: { status: "ok", count: Array.isArray(body?.kpis) ? body.kpis.length : 0 } };
  }
  if (method === "POST" && path === "/ai/generate-dashboard") {
    const hasConfirmed = Array.isArray(body?.confirmed_kpis) && body.confirmed_kpis.length > 0;
    const hasSelIds = Array.isArray(body?.selected_kpi_ids) && body.selected_kpi_ids.length > 0;
    const isAuto = Boolean(body?.auto_generate_kpis);
    if (!hasConfirmed && !hasSelIds && !isAuto) {
      return {
        status: 400,
        body: {
          detail: "انتخاب و تأیید KPIها پیش از تولید داشبورد الزامی است. لطفاً حداقل یک KPI را تأیید کنید.",
        },
      };
    }

    const allDs = getEffectiveDatasets(extraDatasets);
    const targetDs = allDs.find((d) => d.id === body?.dataset_id) || allDs[0];
    if (targetDs) {
      const [friendly] = friendlyDemoDatasetLabel(targetDs);
      const title = body?.title || `داشبورد ${friendly.split("(")[0].trim()}`;
      const built = buildDemoFullDashboard(targetDs, title, "executive_full", body?.theme_id || null);
      const dash: any = built.created_dashboard;
      if (body?.dashboard_id) {
        dash.id = String(body.dashboard_id);
      }
      const dsRows = getTableForDataset(targetDs.id, extraDatasets).rows || [];
      const calMode = body?.calendar_mode || "jalali";
      const stdEval = evaluateDemoStandardAndBusinessKpis(targetDs, dsRows, calMode, body?.target_values || {});

      // Resolve final confirmed KPI list
      let finalKpis: any[] = [];
      if (hasConfirmed) {
        finalKpis = body.confirmed_kpis;
      } else if (hasSelIds) {
        const byId = new Map<string, any>(stdEval.computableStandard.map((k) => [k.id, k]));
        for (const ck of body.custom_kpis || []) byId.set(ck.id, ck);
        finalKpis = body.selected_kpi_ids.map((id: string) => byId.get(id)).filter(Boolean);
      }
      if (!finalKpis.length && isAuto) {
        finalKpis = stdEval.computableStandard.slice(0, 4);
      }

      // Add hidden drill-through Details page
      const detPageId = `page_${dash.id}_details`;
      const mainPageId = dash.pages[0]?.id;
      const nonKpiWidgets = (dash.widgets || []).filter((w: any) => w.chart_type !== "kpi" && w.chart_type !== "table");
      const rawTableWidget = (dash.widgets || []).find((w: any) => w.chart_type === "table") || {
        id: `w_${dash.id}_raw`,
        dashboard_id: dash.id,
        page_id: detPageId,
        title: `جزئیات رکوردهای ${targetDs.name}`,
        description: "رکوردهای خام جهت تحلیل عمیق (Drill-Through)",
        chart_type: "table",
        dataset_id: targetDs.id,
        layout: { x: 0, y: 0, w: 12, h: 10 },
        config: { style: {}, visualization: {}, interactions: { cross_filter: false }, advanced: { ai_generated: true } },
        query: { query_mode: "visual", sql_text: null, visual_spec: { aggregation: "RAW", limit: 200 }, parameters: {} },
        drill_rule: { hierarchy: [], level_rules: [], drill_through_page_id: null },
        widget_filters: [],
        permissions: { view: true, edit: true, delete: true, drill_down: true },
      };
      rawTableWidget.page_id = detPageId;
      rawTableWidget.layout = { x: 0, y: 0, w: 12, h: 10 };

      // Build confirmed KPI widgets cleanly at top of Page 1
      const { dimCols } = getDemoDatasetSchema(targetDs);
      const hier = dimCols.slice(0, 2).map((f: string) => ({ field: f }));
      const kpiWidgets = finalKpis.slice(0, 9).map((k: any, idx: number) => {
        const fmt = k.display_format || k.format_type || "number";
        const useGauge = Boolean(k.target_value && Number(k.target_value) > 0 && idx === 0 && (fmt === "percentage" || fmt === "percent" || k.chart_type === "gauge"));
        const col = idx % 3;
        const row = Math.floor(idx / 3);
        return {
          id: `w_${dash.id}_kpi_${idx + 1}`,
          dashboard_id: dash.id,
          page_id: mainPageId,
          title: k.name,
          description: k.formula || "",
          chart_type: useGauge ? "gauge" : "kpi",
          dataset_id: targetDs.id,
          dataset_freshness: { status: "fresh", last_refreshed_at: new Date().toISOString(), row_count: targetDs.row_count || 0, data_version: 1 },
          layout: { x: col * 4, y: row * 4, w: 4, h: useGauge ? 5 : 4 },
          config: {
            style: {
              number_format: fmt === "percentage" ? "percent" : fmt,
              unit: k.unit || "",
              target_value: k.target_value ?? undefined,
              previous_value: k.previous_value ?? undefined,
            },
            visualization: { show_legend: false, show_tooltip: true },
            interactions: { cross_filter: false, drill_down: true },
            advanced: { ai_generated: true, formula: k.formula, kpi_id: k.id, sources_used: k.source_datasets || [targetDs.name] },
          },
          query: {
            query_mode: "visual",
            sql_text: null,
            visual_spec: {
              formula: k.formula,
              calendar_mode: calMode,
              multi_source_agg: k.multi_source_agg || undefined,
              pre_agg_grain: k.pre_agg_grain || undefined,
              time_field: k.time_field || undefined,
            },
            parameters: {},
          },
          drill_rule: { hierarchy: hier, level_rules: [], drill_through_page_id: detPageId },
          widget_filters: [],
          permissions: { view: true, edit: true, delete: true, drill_down: true },
        };
      });

      const kpiRowsCount = kpiWidgets.length > 0 ? Math.ceil(kpiWidgets.length / 3) * 4 + 1 : 0;
      const shiftedCharts = nonKpiWidgets.map((w: any) => ({
        ...w,
        dashboard_id: dash.id,
        layout: { ...w.layout, y: (w.layout?.y || 0) + kpiRowsCount },
        drill_rule: { ...(w.drill_rule || { hierarchy: hier, level_rules: [] }), drill_through_page_id: detPageId },
      }));

      dash.widgets = [...kpiWidgets, ...shiftedCharts, rawTableWidget];
      dash.pages = [
        { ...dash.pages[0], is_drill_through: false },
        { id: detPageId, title: "جزئیات رکوردها و تحلیل", page_order: 1, is_drill_through: true, layout_settings: { columns_count: 12, row_height: 42, gap_px: 16, grid_enabled: true } },
      ];
      dash.settings = {
        ai_generated: true,
        kpi_config: {
          dataset_id: targetDs.id,
          confirmed_kpis: finalKpis,
          selected_kpi_ids: finalKpis.map((k: any) => k.id),
          calendar_mode: calMode,
          template_mode: body?.template_mode || "executive",
          detail_level: body?.detail_level || "standard",
        },
      };

      return {
        status: 200,
        body: {
          dashboard_id: dash.id,
          title: dash.title,
          pages_count: dash.pages.length,
          widgets_count: dash.widgets.length,
          confirmed_kpis_count: finalKpis.length,
          created_dashboard: dash,
        },
      };
    }
    const first = demoData.dashboards_list?.[0];
    return { status: 200, body: { dashboard_id: first?.id, title: first?.title || "Executive Dashboard" } };
  }
  if (method === "POST" && path === "/ai/text-to-widget") {
    return { status: 200, body: handleTextToWidgetDemo(body, username) };
  }
  if (method === "POST" && path === "/ai/assistant") {
    return { status: 200, body: handleAssistantDemo(body, username) };
  }

  // Descriptive Dashboard Builder
  if (segments[0] === "descriptive-builder") {
    return handleDemoDescriptiveBuilder(method, segments, body, {
      datasets: getEffectiveDatasets(extraDatasets),
      themes: demoData.themes || [],
      getRowsForDataset: (dsId: string) => getTableForDataset(dsId, extraDatasets),
      executeVisual: (dsId: string, spec: any) =>
        executeVisualSpec(dsId, spec, username, [], null, String(spec?.aggregation || "").toUpperCase() === "RAW", extraDatasets),
    });
  }

  // Process Intelligence (Vercel Preview Demo over real sample rows)
  if (segments[0] === "process-intelligence") {
    if (segments[1] === "bpmn-layout") {
      return { status: 200, body: { node_positions: body?.node_positions || {}, zoom: body?.zoom || 1 } };
    }
    if (segments[1] === "datasets" && segments[2]) {
      const dsId = segments[2];
      if (segments[3] === "bottleneck-rules") {
        if (method === "GET") return { status: 200, body: { presets: [], rules: [] } };
        return { status: 200, body: { id: "demo_rule", ...(body || {}) } };
      }
      const tbl = getTableForDataset(dsId, extraDatasets);
      const cols = (tbl.columns || []).map((c) => ({ field_name: c, display_name: c, type: "string" }));
      const bpmnInf = inferBpmnFieldMapping(tbl.columns || []);
      const inferCol = (pats: string[]) => tbl.columns.find((c) => pats.some((p) => c.toLowerCase().includes(p))) || null;
      const mapping = {
        case_id: bpmnInf.request_id_field || inferCol(["request_code", "case_id", "request_id", "order_id"]),
        activity: bpmnInf.step_field || inferCol(["step_name", "activity", "stage"]),
        timestamp: bpmnInf.entered_at_field || inferCol(["event_time", "timestamp", "started_at", "order_date", "date"]),
        wait_hours: bpmnInf.wait_field || inferCol(["wait_hours", "wait"]),
        sla_hours: bpmnInf.sla_field || inferCol(["sla_hours", "sla"]),
        actor: bpmnInf.assignee_field || inferCol(["assignee", "actor", "sales_rep"]),
        department: bpmnInf.department_field || inferCol(["department", "region"]),
        status: bpmnInf.status_field || inferCol(["status"]),
        process_name: bpmnInf.process_field || null,
      };
      if (segments[3] === "processes") {
        const allRows = applyRlsAndFilters(tbl.rows, username, []);
        const pCol = mapping.process_name;
        const pMap = new Map<string, number>();
        if (pCol) {
          for (const r of allRows) {
            const pv = String(r[pCol] || "").trim();
            if (pv) pMap.set(pv, (pMap.get(pv) || 0) + 1);
          }
        }
        return {
          status: 200,
          body: {
            dataset_id: dsId,
            process_field: pCol,
            total_rows: allRows.length,
            processes: Array.from(pMap.entries())
              .map(([process_name, total_count]) => ({ process_name, total_count }))
              .sort((a, b) => b.total_count - a.total_count),
          },
        };
      }
      if (segments[3] === "mapping") {
        return {
          status: 200,
          body: {
            dataset_id: dsId,
            mapping,
            confidences: Object.fromEntries(Object.entries(mapping).map(([k, v]) => [k, v ? 0.9 : 0])),
            reference_sequence: [],
            user_confirmed: method === "POST",
            needs_confirmation: method !== "POST",
            available_columns: cols,
          },
        };
      }
      if (segments[3] === "analyze") {
        let rows = applyRlsAndFilters(tbl.rows, username, []);
        if (mapping.process_name && body?.process_name) {
          const pn = normalizeBpmnText(body.process_name);
          rows = rows.filter((r) => normalizeBpmnText(r[mapping.process_name!]) === pn);
        }
        if (!mapping.case_id || !mapping.activity) {
          return {
            status: 200,
            body: { status: "needs_mapping", mapping_info: { mapping, available_columns: cols }, error_fa: "ستون‌های Case ID و Activity در این دیتاست یافت نشد." },
          };
        }
        const byCase = new Map<string, any[]>();
        for (const r of rows) {
          const cid = String(r[mapping.case_id] || "").trim();
          if (!cid) continue;
          if (!byCase.has(cid)) byCase.set(cid, []);
          byCase.get(cid)!.push(r);
        }
        const byAct = new Map<string, { cnt: number; waits: number[]; breaches: number }>();
        const varMap = new Map<string, number>();
        let reworkCases = 0;
        let slaBreachCases = 0;
        for (const [, evs] of byCase) {
          const acts = evs.map((e) => String(e[mapping.activity!] || ""));
          if (new Set(acts).size < acts.length) reworkCases++;
          const vk = acts.join(" -> ");
          varMap.set(vk, (varMap.get(vk) || 0) + 1);
          let cBreach = false;
          for (const e of evs) {
            const act = String(e[mapping.activity!] || "");
            const cur = byAct.get(act) || { cnt: 0, waits: [], breaches: 0 };
            cur.cnt++;
            const w = mapping.wait_hours ? Number(e[mapping.wait_hours] || 0) : 0;
            const s = mapping.sla_hours ? Number(e[mapping.sla_hours] || 0) : 0;
            if (mapping.wait_hours) cur.waits.push(w);
            if (mapping.sla_hours && w > s) { cur.breaches++; cBreach = true; }
            byAct.set(act, cur);
          }
          if (cBreach) slaBreachCases++;
        }
        const bottlenecks = Array.from(byAct.entries()).map(([activity, st]) => {
          const avgW = st.waits.length ? Number((st.waits.reduce((a, b) => a + b, 0) / st.waits.length).toFixed(2)) : null;
          return {
            activity,
            case_count: st.cnt,
            event_count: st.cnt,
            avg_wait_hours: avgW,
            rework_events: 0,
            rework_rate_pct: 0,
            sla_breach_pct: Number(((st.breaches * 100) / Math.max(st.cnt, 1)).toFixed(1)),
            composite_bottleneck_score: Number(Math.min(100, (avgW || 0) * 4 + st.cnt * 1.5).toFixed(1)),
          };
        }).sort((a, b) => b.composite_bottleneck_score - a.composite_bottleneck_score);
        const totalCases = byCase.size || 1;
        const variants = Array.from(varMap.entries()).sort((a, b) => b[1] - a[1]).slice(0, 15).map(([k, v], i) => ({
          rank: i + 1,
          variant_key: k,
          steps: k.split(" -> "),
          case_count: v,
          share_pct: Number(((v * 100) / totalCases).toFixed(1)),
          avg_cycle_hours: null,
          rework_rate_pct: 0,
          sla_breach_pct: 0,
          skipped_steps: [],
          is_deviant: i > 0,
        }));
        return {
          status: 200,
          body: {
            status: "ok",
            dataset_id: dsId,
            mapping_info: { mapping, available_columns: cols },
            summary: {
              total_events_analyzed: rows.length,
              total_cases: byCase.size,
              unique_activities: byAct.size,
              unique_variants: varMap.size,
              avg_cycle_hours: null,
              median_cycle_hours: null,
              avg_wait_hours: bottlenecks[0]?.avg_wait_hours ?? null,
              avg_processing_hours: null,
              processing_time_note_fa: "[پیش‌نمایش ابری] نیازمند هر دو ستون start_time و end_time.",
              wait_time_basis_fa: "ستون wait_hours (داده نمونه پیش‌نمایش)",
              rework_case_count: reworkCases,
              rework_rate_pct: Number(((reworkCases * 100) / totalCases).toFixed(1)),
              self_loop_case_count: 0,
              sla_evaluated_cases: byCase.size,
              sla_breach_cases: slaBreachCases,
              sla_breach_rate_pct: Number(((slaBreachCases * 100) / totalCases).toFixed(1)),
            },
            data_quality: { warnings_fa: ["پیش‌نمایش ابری بر پایه رکوردهای نمونه؛ برای ذخیره نگاشت و اقدامات اصلاحی به FastAPI متصل شوید."] },
            bottlenecks,
            variants,
            hypotheses: [],
          },
        };
      }
      if (segments[3] === "cases") {
        const rows = applyRlsAndFilters(tbl.rows, username, []);
        const actF = searchParams.get("activity") || "";
        if (segments[4]) {
          const cid = decodeURIComponent(segments[4]);
          const evs = rows.filter((r) => mapping.case_id && String(r[mapping.case_id]) === cid);
          return {
            status: 200,
            body: {
              case_id: cid,
              events: evs.map((e, i) => ({
                seq: i + 1,
                activity: mapping.activity ? e[mapping.activity] : "",
                timestamp: mapping.timestamp ? e[mapping.timestamp] : null,
                actor: mapping.actor ? e[mapping.actor] : null,
                department: mapping.department ? e[mapping.department] : null,
                wait_hours: mapping.wait_hours ? e[mapping.wait_hours] : null,
              })),
            },
          };
        }
        const matched = rows.filter((r) => !actF || (mapping.activity && String(r[mapping.activity]) === actF)).slice(0, 40);
        return {
          status: 200,
          body: {
            total: matched.length,
            cases: matched.map((r, i) => ({
              case_id: mapping.case_id ? String(r[mapping.case_id]) : `CASE-${i + 1}`,
              event_count: 1,
              wait_hours: mapping.wait_hours ? Number(r[mapping.wait_hours] || 0) : null,
              has_rework: false,
              sla_breached: Boolean(mapping.sla_hours && mapping.wait_hours && Number(r[mapping.wait_hours]) > Number(r[mapping.sla_hours])),
            })),
          },
        };
      }
      if (segments[3] === "actions") {
        return { status: 200, body: [] };
      }
    }
  }

  // Security Log Intelligence (Honest Preview Demo status)
  if (segments[0] === "security-intel") {
    if (segments[1] === "connectors") {
      return {
        status: 200,
        body: {
          connectors: [
            { id: "syslog_security_jsonl", name_fa: "جمع‌آورنده بومی لاگ امنیتی (JSONL / Syslog / Key-Value)", status_fa: "پیاده‌سازی‌شده و آزمایش‌شده (نیازمند سرور Backend)", tested: true, requirements_fa: "در محیط کامل FastAPI فعال است." },
            { id: "defender_edr", name_fa: "Microsoft Defender for Endpoint (EDR)", status_fa: "طرح‌شده — نیازمند زیرساخت و اعتبارنامه", tested: false, requirements_fa: "نیازمند اعتبارنامه Graph API." },
            { id: "fortinet_fortigate", name_fa: "فایروال Fortinet FortiGate", status_fa: "طرح‌شده — نیازمند زیرساخت و اعتبارنامه", tested: false, requirements_fa: "نیازمند Syslog / API فایروال." },
          ],
        },
      };
    }
    if (segments[1] === "overview") {
      return {
        status: 200,
        body: {
          window_hours: 720,
          total_events: 0,
          action_breakdown: { Detect: 0, Block: 0, Quarantine: 0, Clean: 0, Failed: 0, Allow: 0 },
          interpretation_notes_fa: [
            "[پیش‌نمایش ابری]: برای دریافت و ذخیره‌سازی واقعی لاگ‌های امنیتی و هشدارهای آماری، BACKEND_INTERNAL_URL را به سرور FastAPI متصل کنید.",
            "نبود رویداد در پیش‌نمایش به‌معنای سلامت امنیتی نیست.",
          ],
        },
      };
    }
    if (segments[1] === "events") return { status: 200, body: { total: 0, can_view_raw: false, events: [] } };
    if (segments[1] === "classification-rules") {
      return { status: 200, body: { categories: [], rules: [] } };
    }
    if (
      segments[1] === "unparsed" ||
      segments[1] === "collectors" ||
      segments[1] === "alerts" ||
      segments[1] === "incidents" ||
      segments[1] === "vulnerabilities" ||
      segments[1] === "exposures"
    ) {
      return { status: 200, body: [] };
    }
  }

  if (segments[0] === "correlation" && segments[1] === "analyze") {
    return { status: 200, body: { status: "no_security_events", error_fa: "در پیش‌نمایش ابری رویداد امنیتی ذخیره نمی‌شود؛ برای همبستگی زنده به سرور FastAPI متصل شوید." } };
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
