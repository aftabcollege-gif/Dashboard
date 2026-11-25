"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import * as echarts from "echarts";
import { ArrowDownRight, ArrowUpRight, Search } from "lucide-react";
import { formatBIValue } from "@/lib/chart-registry";
import { useBi } from "@/lib/context";
import BpmnProcessViewer from "@/components/charts/BpmnProcessViewer";
import type { BpmnAnalyticsPayload } from "@/types/bi";

interface Props {
  chartType: string;
  columns: string[];
  rows: Record<string, any>[];
  styleConfig?: Record<string, any>;
  vizConfig?: Record<string, any>;
  visualSpec?: Record<string, any>;
  onPointClick?: (field: string, value: any) => void;
  chartInstanceRef?: React.MutableRefObject<echarts.ECharts | null>;
  widgetId?: string;
  publicShareToken?: string;
  activeFilters?: any[];
  crossFilters?: any[];
  bpmnAnalytics?: BpmnAnalyticsPayload;
  onProcessSelect?: (processName: string) => void;
  onFieldMappingOverride?: (overrides: Record<string, string | null>) => void;
}

const GEO_CITY_COORDS: Record<string, [number, number]> = {
  Tehran: [51.389, 35.6892],
  Karaj: [50.9915, 35.8327],
  Sari: [53.0601, 36.5633],
  Shiraz: [52.5311, 29.5918],
  Ahvaz: [48.6706, 31.3183],
  "Bandar Abbas": [56.2808, 27.1865],
  Mashhad: [59.6168, 36.2605],
  Kerman: [57.0834, 30.2839],
  Isfahan: [51.666, 32.6539],
  Tabriz: [46.2919, 38.0962],
  North: [51.4, 36.2],
  South: [52.5, 29.2],
  East: [58.5, 34.0],
  West: [48.0, 34.5],
  Iran: [53.688, 32.4279],
};

export default function WidgetRenderer(props: Props) {
  if (props.chartType === "bpmn") {
    return (
      <BpmnProcessViewer
        columns={props.columns}
        rows={props.rows}
        styleConfig={props.styleConfig}
        vizConfig={props.vizConfig}
        visualSpec={props.visualSpec}
        onPointClick={props.onPointClick}
        widgetId={props.widgetId}
        publicShareToken={props.publicShareToken}
        activeFilters={props.activeFilters}
        crossFilters={props.crossFilters}
        bpmnAnalytics={props.bpmnAnalytics}
        onProcessSelect={props.onProcessSelect}
        onFieldMappingOverride={props.onFieldMappingOverride}
      />
    );
  }
  return <StandardWidgetRenderer {...props} />;
}

function StandardWidgetRenderer({
  chartType,
  columns: rawColumns,
  rows: rawRows,
  styleConfig = {},
  vizConfig = {},
  visualSpec = {},
  onPointClick,
  chartInstanceRef,
  bpmnAnalytics,
}: Props) {
  const { locale, activeThemeTokens, activeThemeMode } = useBi();
  const [tableSearch, setTableSearch] = useState("");
  const [sortCol, setSortCol] = useState<string | null>(null);
  const [sortAsc, setSortAsc] = useState(true);
  const [isMobile, setIsMobile] = useState(false);

  const { columns, rows } = useMemo(() => {
    if (
      bpmnAnalytics?.step_summary &&
      bpmnAnalytics.step_summary.length > 0 &&
      chartType !== "table"
    ) {
      const stepCol = bpmnAnalytics.mapping?.step_field || visualSpec?.bpmn_config?.step_field || "step";
      const aggRows = bpmnAnalytics.step_summary.map((st) => ({
        [stepCol]: st.step_name,
        value: st.avg_wait_hours > 0 ? st.avg_wait_hours : st.total_count,
        secondary_value: st.pending_count || st.total_count,
      }));
      return { columns: [stepCol, "value", "secondary_value"], rows: aggRows };
    }
    if (
      visualSpec?.bpmn_config &&
      chartType !== "table" &&
      !rawColumns.includes("value") &&
      rawRows.length > 0
    ) {
      const stepCol = visualSpec.bpmn_config.step_field || visualSpec.x_field || rawColumns[0];
      const waitCol = visualSpec.bpmn_config.wait_field || visualSpec.y_field;
      const groups = new Map<string, number[]>();
      for (const r of rawRows) {
        const k = String(r[stepCol] ?? "").trim();
        if (!k) continue;
        if (!groups.has(k)) groups.set(k, []);
        groups.get(k)!.push(waitCol ? Number(r[waitCol] ?? 0) || 0 : 1);
      }
      const aggRows = Array.from(groups.entries()).map(([k, vals]) => ({
        [stepCol]: k,
        value: waitCol
          ? Math.round((vals.reduce((a, b) => a + b, 0) / vals.length) * 10) / 10
          : vals.length,
      }));
      return { columns: [stepCol, "value"], rows: aggRows };
    }
    return { columns: rawColumns, rows: rawRows };
  }, [rawColumns, rawRows, visualSpec, chartType, bpmnAnalytics]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const mq = window.matchMedia("(max-width: 639px)");
    const update = () => setIsMobile(mq.matches);
    update();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, []);

  const numFmt = styleConfig.number_format || "number";
  const decimals = Number(styleConfig.decimals ?? 0);
  const unit = styleConfig.unit || "";
  const abbreviate = Boolean(styleConfig.abbreviate ?? true);
  const palette: string[] =
    (Array.isArray(styleConfig.colors) && styleConfig.colors.length > 0
      ? styleConfig.colors
      : activeThemeTokens.chart_palette) || ["#2563eb", "#0ea5e9", "#10b981", "#f59e0b"];
  const textColor = styleConfig.text_color || activeThemeTokens.text;
  const mutedColor = activeThemeTokens.muted_text;
  const lineWidth = Number(styleConfig.line_width ?? 2.5);
  const showLegend = vizConfig.show_legend !== false;
  const showTooltip = vizConfig.show_tooltip !== false;
  const dataLabels = Boolean(vizConfig.data_labels);

  // Determine fields from columns / visualSpec
  const xField = useMemo(() => {
    if (visualSpec.x_field && columns.includes(visualSpec.x_field)) return visualSpec.x_field;
    return columns[0] || "label";
  }, [columns, visualSpec]);

  const seriesField = useMemo(() => {
    if (visualSpec.series_field && columns.includes(visualSpec.series_field)) return visualSpec.series_field;
    return columns.length >= 3 ? columns[1] : null;
  }, [columns, visualSpec]);

  const yField = useMemo(() => {
    if (columns.includes("value")) return "value";
    if (visualSpec.y_field && columns.includes(visualSpec.y_field)) return visualSpec.y_field;
    return columns.length >= 2 ? columns[columns.length - 1] : columns[0] || "value";
  }, [columns, visualSpec]);

  const secField = useMemo(() => {
    if (columns.includes("secondary_value")) return "secondary_value";
    if (visualSpec.secondary_y_field && columns.includes(visualSpec.secondary_y_field)) return visualSpec.secondary_y_field;
    return null;
  }, [columns, visualSpec]);

  // ---------------- 1. KPI CARD ----------------
  if (chartType === "kpi") {
    const val = rows[0]?.[yField] ?? rows[0]?.[columns[0]] ?? null;
    const hasVal = val !== null && val !== undefined && val !== "" && !Number.isNaN(Number(val));
    const numericVal = hasVal ? Number(val) : 0;
    const target = Number(styleConfig.target_value || 0);
    const hasPrev = styleConfig.previous_value !== undefined && styleConfig.previous_value !== null && Number(styleConfig.previous_value) !== 0;
    const prev = Number(styleConfig.previous_value || 0);
    const pctChange = hasVal && hasPrev ? ((numericVal - prev) / Math.abs(prev)) * 100 : null;
    const targetPct = hasVal && target > 0 ? Math.min(100, Math.round((numericVal / target) * 100)) : null;
    const statusColor =
      targetPct === null
        ? palette[0]
        : targetPct >= 90
        ? "#10b981"
        : targetPct >= 65
        ? "#f59e0b"
        : "#ef4444";

    const rawSpark: number[] | null =
      Array.isArray(styleConfig.sparkline_values) && styleConfig.sparkline_values.length >= 2
        ? styleConfig.sparkline_values.map((x: any) => Number(x) || 0)
        : null;
    const sparkNorm = (() => {
      if (!rawSpark) return [0.68, 0.74, 0.71, 0.82, 0.79, 0.91, 1.0];
      const mn = Math.min(...rawSpark);
      const mx = Math.max(...rawSpark);
      const span = mx - mn || 1;
      return rawSpark.map((v) => 0.15 + ((v - mn) / span) * 0.85);
    })();
    const stepX = sparkNorm.length > 1 ? 120 / (sparkNorm.length - 1) : 20;
    const poly = sparkNorm.map((m, idx) => `${(idx * stepX).toFixed(1)},${(28 - m * 22).toFixed(1)}`).join(" ");

    return (
      <div
        role={onPointClick ? "button" : undefined}
        tabIndex={onPointClick ? 0 : undefined}
        onClick={() => onPointClick?.("__kpi_drill__", hasVal ? numericVal : null)}
        onKeyDown={(e) => {
          if (onPointClick && (e.key === "Enter" || e.key === " ")) {
            e.preventDefault();
            onPointClick("__kpi_drill__", hasVal ? numericVal : null);
          }
        }}
        title={onPointClick ? (locale === "fa" ? "کلیک برای تحلیل جزئیات و رکوردهای KPI" : "Click to inspect KPI breakdown & records") : undefined}
        className={`h-full flex flex-col justify-between gap-1 p-0.5 rounded-lg transition ${
          onPointClick ? "cursor-pointer hover:bg-blue-500/5" : ""
        }`}
      >
        <div className="flex items-start justify-between gap-2 min-w-0">
          <div className="text-lg sm:text-xl lg:text-2xl font-bold tracking-tight leading-tight truncate min-w-0" style={{ color: textColor }}>
            {hasVal ? formatBIValue(numericVal, numFmt, decimals, unit, abbreviate) : "—"}
          </div>
          {styleConfig.show_sparkline !== false && (
            <svg viewBox="0 0 120 32" className="w-12 sm:w-20 h-6 sm:h-7 shrink-0 opacity-90">
              <polyline
                fill="none"
                stroke={statusColor}
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
                points={poly}
              />
            </svg>
          )}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-1.5 text-[11px] sm:text-xs">
          {pctChange !== null ? (
            <span
              className={`inline-flex items-center gap-0.5 font-semibold px-1.5 sm:px-2 py-0.5 rounded-full ${
                pctChange >= 0 ? "bg-emerald-500/15 text-emerald-500" : "bg-red-500/15 text-red-500"
              }`}
            >
              {pctChange >= 0 ? <ArrowUpRight className="w-3 h-3 sm:w-3.5 sm:h-3.5" /> : <ArrowDownRight className="w-3 h-3 sm:w-3.5 sm:h-3.5" />}
              {Math.abs(pctChange).toFixed(1)}% {locale === "fa" ? "نسبت به قبل" : "vs prev"}
            </span>
          ) : (
            <span style={{ color: mutedColor }}>
              {locale === "fa" ? (onPointClick ? "شاخص زنده (کلیک برای جزئیات)" : "شاخص زنده") : (onPointClick ? "Live KPI (Click for details)" : "Live KPI")}
            </span>
          )}
          {targetPct !== null && (
            <div className="flex items-center gap-1.5 flex-1 min-w-[70px] max-w-[160px]">
              <div className="flex-1 h-1.5 sm:h-2 rounded-full overflow-hidden bg-slate-500/20">
                <div className="h-full rounded-full transition-all" style={{ width: `${targetPct}%`, background: statusColor }} />
              </div>
              <span className="font-semibold" style={{ color: statusColor }}>{targetPct}%</span>
            </div>
          )}
        </div>
      </div>
    );
  }

  // ---------------- 2. PROGRESS BAR ----------------
  if (chartType === "progress") {
    return (
      <div className="h-full overflow-y-auto space-y-3 pr-1">
        {rows.slice(0, 10).map((r, idx) => {
          const label = String(r[xField] ?? `Item ${idx + 1}`);
          const actual = Number(r[yField] ?? 0);
          const budget = secField ? Number(r[secField] ?? actual * 1.15) : Number(styleConfig.max_value || actual * 1.2 || 100);
          const pct = Math.min(100, Math.max(0, Math.round((actual / (budget || 1)) * 100)));
          const barColor = pct > 95 ? "#ef4444" : pct > 75 ? "#f59e0b" : palette[idx % palette.length];
          return (
            <div
              key={idx}
              className="space-y-1 cursor-pointer hover:opacity-90"
              onClick={() => onPointClick?.(xField, r[xField])}
            >
              <div className="flex justify-between text-xs font-medium">
                <span>{label}</span>
                <span>{formatBIValue(actual, numFmt, decimals, unit, true)} ({pct}%)</span>
              </div>
              <div className="h-2.5 rounded-full bg-slate-500/20 overflow-hidden">
                <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, background: barColor }} />
              </div>
            </div>
          );
        })}
      </div>
    );
  }

  // ---------------- 2b. BULLET CHART ----------------
  if (chartType === "bullet") {
    const items = rows.slice(0, 8).map((r, idx) => {
      const lbl = rows.length === 1 && !visualSpec.x_field ? (styleConfig.unit ? `شاخص (${styleConfig.unit})` : "عملکرد فعلی") : String(r[xField] ?? `Item ${idx + 1}`);
      const actual = Number(r[yField] ?? r[columns[0]] ?? 0);
      const tgt = secField ? Number(r[secField] ?? 0) : Number(styleConfig.target_value || actual * 1.15 || 100);
      const maxScale = Math.max(actual * 1.1, tgt * 1.2, 1);
      const actualPct = Math.min(100, Math.max(0, (actual / maxScale) * 100));
      const targetPct = Math.min(99, Math.max(1, (tgt / maxScale) * 100));
      const achievedPct = tgt > 0 ? Math.round((actual / tgt) * 100) : 100;
      const barColor = achievedPct >= 95 ? "#10b981" : achievedPct >= 75 ? palette[0] : "#f59e0b";
      return { lbl, actual, tgt, actualPct, targetPct, achievedPct, barColor, rawX: r[xField] };
    });

    return (
      <div className="h-full overflow-y-auto flex flex-col justify-center space-y-3 pr-1">
        {items.map((it, idx) => (
            <div
              key={idx}
              className="space-y-1 cursor-pointer hover:opacity-90"
              onClick={() =>
                it.rawX !== undefined
                  ? onPointClick?.(xField, it.rawX)
                  : onPointClick?.("__kpi_drill__", it.actual)
              }
            >
            <div className="flex justify-between text-xs font-medium gap-2">
              <span className="truncate">{it.lbl}</span>
              <span className="shrink-0 text-[11px]">
                {formatBIValue(it.actual, numFmt, decimals, unit, true)} / {formatBIValue(it.tgt, numFmt, decimals, unit, true)} ({it.achievedPct}%)
              </span>
            </div>
            <div className="relative h-5 rounded-md overflow-hidden flex bg-slate-500/15">
              <div className="h-full bg-slate-500/15" style={{ width: "55%" }} />
              <div className="h-full bg-slate-500/10" style={{ width: "25%" }} />
              <div
                className="absolute top-1.5 bottom-1.5 left-0 rounded transition-all"
                style={{ width: `${it.actualPct}%`, background: it.barColor }}
              />
              <div
                className="absolute top-0.5 bottom-0.5 w-0.5 bg-red-500 shadow"
                style={{ left: `${it.targetPct}%` }}
                title={`هدف: ${it.tgt}`}
              />
            </div>
          </div>
        ))}
      </div>
    );
  }

  // ---------------- 3. DATA TABLE ----------------
  if (chartType === "table") {
    const filtered = rows.filter((r) =>
      !tableSearch
        ? true
        : columns.some((c) => String(r[c] ?? "").toLowerCase().includes(tableSearch.toLowerCase()))
    );
    const sorted = [...filtered].sort((a, b) => {
      if (!sortCol) return 0;
      const va = a[sortCol], vb = b[sortCol];
      const cmp = typeof va === "number" && typeof vb === "number" ? va - vb : String(va).localeCompare(String(vb));
      return sortAsc ? cmp : -cmp;
    });

    return (
      <div className="h-full flex flex-col gap-2">
        <div className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg border text-xs" style={{ borderColor: activeThemeTokens.border }}>
          <Search className="w-3.5 h-3.5 opacity-60 shrink-0" />
          <input
            value={tableSearch}
            onChange={(e) => setTableSearch(e.target.value)}
            placeholder={locale === "fa" ? "جستجو در ردیف‌ها..." : "Filter rows..."}
            className="bg-transparent outline-none flex-1 min-w-0 text-xs"
          />
          <span className="text-[11px] opacity-60 shrink-0">
            {sorted.length} {locale === "fa" ? "ردیف" : "rows"}
          </span>
        </div>
        <div className="flex-1 overflow-auto rounded-lg border" style={{ borderColor: activeThemeTokens.border }}>
          <table className="w-full min-w-[460px] text-[11px] sm:text-xs text-start border-collapse">
            <thead className="sticky top-0" style={{ background: activeThemeTokens.surface }}>
              <tr>
                {columns.map((c) => (
                  <th
                    key={c}
                    onClick={() => { setSortCol(c); setSortAsc(sortCol === c ? !sortAsc : false); }}
                    className="px-2.5 py-2 font-semibold border-b cursor-pointer select-none whitespace-nowrap"
                    style={{ borderColor: activeThemeTokens.border }}
                  >
                    {c.replace(/_/g, " ")} {sortCol === c ? (sortAsc ? "↑" : "↓") : ""}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.slice(0, 150).map((r, i) => (
                <tr
                  key={i}
                  onClick={() => onPointClick?.(xField, r[xField])}
                  className="hover:bg-blue-500/10 cursor-pointer border-b"
                  style={{ borderColor: activeThemeTokens.border }}
                >
                  {columns.map((c) => {
                    const v = r[c];
                    const isNum = typeof v === "number";
                    return (
                      <td key={c} className="px-2.5 py-1.5 whitespace-nowrap">
                        {isNum ? formatBIValue(v, c.includes("pct") ? "percentage" : numFmt, decimals, unit, false) : String(v ?? "")}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  // ---------------- 4. PIVOT TABLE & MATRIX ----------------
  if (chartType === "pivot_table" || chartType === "matrix") {
    const colKey = seriesField || (columns.length > 2 ? columns[1] : xField);
    const colVals = Array.from(new Set(rows.map((r) => String(r[colKey] ?? ""))));
    const rowVals = Array.from(new Set(rows.map((r) => String(r[xField] ?? ""))));
    const lookup: Record<string, Record<string, number>> = {};
    for (const r of rows) {
      const rk = String(r[xField] ?? "");
      const ck = String(r[colKey] ?? "");
      lookup[rk] = lookup[rk] || {};
      lookup[rk][ck] = (lookup[rk][ck] || 0) + Number(r[yField] ?? 0);
    }

    return (
      <div className="h-full overflow-auto rounded-lg border" style={{ borderColor: activeThemeTokens.border }}>
        <table className="w-full min-w-[420px] text-[11px] sm:text-xs border-collapse">
          <thead className="sticky top-0" style={{ background: activeThemeTokens.surface }}>
            <tr>
              <th className="px-2.5 py-2 border-b text-start font-bold" style={{ borderColor: activeThemeTokens.border }}>
                {xField} \ {colKey}
              </th>
              {colVals.map((cv) => (
                <th key={cv} className="px-2.5 py-2 border-b text-end font-semibold" style={{ borderColor: activeThemeTokens.border }}>
                  {cv}
                </th>
              ))}
              <th className="px-2.5 py-2 border-b text-end font-bold" style={{ borderColor: activeThemeTokens.border }}>
                {locale === "fa" ? "مجموع" : "Total"}
              </th>
            </tr>
          </thead>
          <tbody>
            {rowVals.map((rv) => {
              const rowTotal = colVals.reduce((acc, cv) => acc + (lookup[rv]?.[cv] || 0), 0);
              return (
                <tr key={rv} className="border-b hover:bg-blue-500/10" style={{ borderColor: activeThemeTokens.border }}>
                  <td
                    onClick={() => onPointClick?.(xField, rv)}
                    className="px-2.5 py-1.5 font-semibold cursor-pointer"
                  >
                    {rv}
                  </td>
                  {colVals.map((cv) => {
                    const cellVal = lookup[rv]?.[cv] || 0;
                    const bgOpacity = chartType === "matrix" && rowTotal > 0 ? Math.min(0.45, (cellVal / rowTotal) * 0.65) : 0;
                    return (
                      <td
                        key={cv}
                        className="px-2.5 py-1.5 text-end"
                        style={{ backgroundColor: bgOpacity ? `rgba(37, 99, 235, ${bgOpacity})` : undefined }}
                      >
                        {formatBIValue(cellVal, numFmt, decimals, unit, true)}
                      </td>
                    );
                  })}
                  <td className="px-2.5 py-1.5 text-end font-bold">
                    {formatBIValue(rowTotal, numFmt, decimals, unit, true)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  }

  // ---------------- 5. ECHARTS VISUALIZATION ENGINE ----------------
  if (!rows || rows.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-xs opacity-60 text-center px-3">
        {locale === "fa"
          ? "رکوردی مطابق با فیلتر یا سطح دسترسی فعلی یافت نشد."
          : "No matching records for current filter / RLS context."}
      </div>
    );
  }

  return (
    <EChartsCanvas
      chartType={chartType}
      rows={rows}
      columns={columns}
      xField={xField}
      seriesField={seriesField}
      yField={yField}
      secField={secField}
      palette={palette}
      textColor={textColor}
      mutedColor={mutedColor}
      lineWidth={lineWidth}
      showLegend={showLegend}
      showTooltip={showTooltip}
      dataLabels={dataLabels}
      isDark={activeThemeMode === "dark"}
      isMobile={isMobile}
      locale={locale}
      styleConfig={styleConfig}
      numFmt={numFmt}
      decimals={decimals}
      unit={unit}
      abbreviate={abbreviate}
      onPointClick={onPointClick}
      chartInstanceRef={chartInstanceRef}
    />
  );
}

function EChartsCanvas(props: {
  chartType: string;
  rows: Record<string, any>[];
  columns: string[];
  xField: string;
  seriesField: string | null;
  yField: string;
  secField: string | null;
  palette: string[];
  textColor: string;
  mutedColor: string;
  lineWidth: number;
  showLegend: boolean;
  showTooltip: boolean;
  dataLabels: boolean;
  isDark: boolean;
  isMobile: boolean;
  locale: string;
  styleConfig: Record<string, any>;
  numFmt: string;
  decimals: number;
  unit: string;
  abbreviate: boolean;
  onPointClick?: (field: string, value: any) => void;
  chartInstanceRef?: React.MutableRefObject<echarts.ECharts | null>;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const localChartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    if (!localChartRef.current) {
      localChartRef.current = echarts.init(el);
    }
    const chart = localChartRef.current;
    if (props.chartInstanceRef) props.chartInstanceRef.current = chart;

    const option = buildEChartsOption(props);
    chart.setOption(option, true);
    chart.off("click");
    chart.on("click", (params: any) => {
      if (!props.onPointClick) return;
      if (props.chartType === "radar") return;
      if (props.chartType === "gauge") {
        props.onPointClick("__kpi_drill__", Array.isArray(params.value) ? params.value[0] : params.value);
        return;
      }
      let clickedVal: any = params.name;
      if (props.chartType === "sankey" && params.dataType === "edge") {
        clickedVal = params.data?.source;
      } else if (!clickedVal && Array.isArray(params.value)) {
        clickedVal = params.value[3] ?? params.value[0];
      } else if (clickedVal === undefined || clickedVal === "") {
        clickedVal = params.value;
      }
      if (clickedVal !== undefined && clickedVal !== null && clickedVal !== "") {
        props.onPointClick(props.xField, clickedVal);
      }
    });

    const ro = new ResizeObserver(() => {
      chart.resize();
    });
    ro.observe(el);
    return () => {
      ro.disconnect();
    };
  }, [
    props.chartType, props.rows, props.columns, props.xField, props.seriesField,
    props.yField, props.secField, props.palette, props.textColor, props.mutedColor,
    props.lineWidth, props.showLegend, props.showTooltip, props.dataLabels,
    props.isDark, props.isMobile, props.locale, props.styleConfig,
  ]);

  useEffect(() => {
    return () => {
      if (localChartRef.current) {
        localChartRef.current.dispose();
        localChartRef.current = null;
      }
    };
  }, []);

  return <div ref={containerRef} className="w-full h-full min-h-[84px] cursor-pointer" />;
}

function buildEChartsOption(ctx: {
  chartType: string;
  rows: Record<string, any>[];
  columns: string[];
  xField: string;
  seriesField: string | null;
  yField: string;
  secField: string | null;
  palette: string[];
  textColor: string;
  mutedColor: string;
  lineWidth: number;
  showLegend: boolean;
  showTooltip: boolean;
  dataLabels: boolean;
  isDark: boolean;
  isMobile: boolean;
  locale: string;
  styleConfig: Record<string, any>;
  numFmt: string;
  decimals: number;
  unit: string;
  abbreviate: boolean;
}): echarts.EChartsOption {
  const {
    chartType, rows, xField, seriesField, yField, secField, palette,
    textColor, mutedColor, lineWidth, showLegend, showTooltip, dataLabels,
    isMobile, locale, styleConfig, numFmt, decimals, unit, abbreviate,
  } = ctx;

  const fontFamily = locale === "fa" ? "Vazirmatn, Inter, sans-serif" : "Inter, Vazirmatn, sans-serif";
  const baseTooltip: any = showTooltip
    ? {
        trigger: ["pie", "donut", "funnel", "gauge", "treemap", "sunburst", "sankey", "scatter", "bubble"].includes(chartType)
          ? "item"
          : "axis",
        confine: true,
        textStyle: { fontFamily, fontSize: isMobile ? 11 : 12 },
      }
    : { show: false };

  const legendOpt: any = showLegend
    ? { type: "scroll", top: 0, textStyle: { color: textColor, fontSize: isMobile ? 10 : 11, fontFamily } }
    : { show: false };
  const cats = Array.from(new Set(rows.map((r) => String(r[xField] ?? ""))));
  const axisLabel = {
    color: mutedColor,
    fontSize: isMobile ? 10 : Number(styleConfig.font_size || 11),
    fontFamily,
  };
  const xAxisLabel = {
    ...axisLabel,
    rotate: isMobile && cats.length > 4 ? 26 : 0,
    hideOverlap: true,
  };
  const yAxisLabel = {
    ...axisLabel,
    formatter: (v: number) => formatBIValue(v, "number", 0, "", true),
  };
  const compactGrid = {
    left: isMobile ? 6 : 12,
    right: isMobile ? 8 : 14,
    top: showLegend ? (isMobile ? 28 : 34) : 14,
    bottom: isMobile ? 8 : 14,
    containLabel: true,
  };
  const splitLine = { lineStyle: { color: "rgba(148, 163, 184, 0.16)" } };
  const labelOpt: any = dataLabels ? { show: true, position: "top", color: textColor, fontSize: 10 } : { show: false };

  // Multi-series helper
  const buildSeriesMap = () => {
    const sNames = seriesField ? Array.from(new Set(rows.map((r) => String(r[seriesField] ?? "")))) : [yField];
    const lookup: Record<string, Record<string, number>> = {};
    for (const r of rows) {
      const x = String(r[xField] ?? "");
      const s = seriesField ? String(r[seriesField] ?? "") : yField;
      lookup[s] = lookup[s] || {};
      lookup[s][x] = (lookup[s][x] || 0) + Number(r[yField] ?? 0);
    }
    return { sNames, lookup };
  };

  if (chartType === "bar" || chartType === "histogram") {
    return {
      textStyle: { fontFamily },
      color: palette,
      tooltip: baseTooltip,
      legend: legendOpt,
      grid: compactGrid,
      xAxis: { type: "category", data: cats, axisLabel: xAxisLabel },
      yAxis: { type: "value", axisLabel: yAxisLabel, splitLine },
      series: [{
        name: yField,
        type: "bar",
        barMaxWidth: isMobile ? 28 : 42,
        itemStyle: { borderRadius: [4, 4, 0, 0] },
        label: labelOpt,
        data: cats.map((c) => rows.filter((r) => String(r[xField]) === c).reduce((a, b) => a + Number(b[yField] ?? 0), 0)),
      }],
    };
  }

  if (chartType === "horizontal_bar") {
    return {
      textStyle: { fontFamily },
      color: palette,
      tooltip: baseTooltip,
      legend: legendOpt,
      grid: compactGrid,
      xAxis: { type: "value", axisLabel: yAxisLabel, splitLine },
      yAxis: { type: "category", data: cats, axisLabel },
      series: [{
        name: yField,
        type: "bar",
        itemStyle: { borderRadius: [0, 4, 4, 0] },
        data: cats.map((c) => rows.filter((r) => String(r[xField]) === c).reduce((a, b) => a + Number(b[yField] ?? 0), 0)),
      }],
    };
  }

  if (chartType === "grouped_bar") {
    if (seriesField) {
      const { sNames, lookup } = buildSeriesMap();
      return {
        textStyle: { fontFamily },
        color: palette,
        tooltip: baseTooltip,
        legend: legendOpt,
        grid: compactGrid,
        xAxis: { type: "category", data: cats, axisLabel: xAxisLabel },
        yAxis: { type: "value", axisLabel: yAxisLabel, splitLine },
        series: sNames.map((s) => ({
          name: s,
          type: "bar",
          barGap: "12%",
          itemStyle: { borderRadius: [3, 3, 0, 0] },
          data: cats.map((c) => lookup[s]?.[c] || 0),
        })),
      };
    }
    return {
      textStyle: { fontFamily },
      color: palette,
      tooltip: baseTooltip,
      legend: legendOpt,
      grid: compactGrid,
      xAxis: { type: "category", data: cats, axisLabel: xAxisLabel },
      yAxis: { type: "value", axisLabel: yAxisLabel, splitLine },
      series: [
        { name: yField, type: "bar" as const, itemStyle: { borderRadius: [3, 3, 0, 0] }, data: rows.map((r) => Number(r[yField] ?? 0)) },
        ...(secField
          ? [{ name: secField, type: "bar" as const, itemStyle: { borderRadius: [3, 3, 0, 0] }, data: rows.map((r) => Number(r[secField] ?? 0)) }]
          : []),
      ],
    };
  }

  if (chartType === "stacked_bar" || chartType === "stacked_bar_100") {
    const { sNames, lookup } = buildSeriesMap();
    const totalsByCat: Record<string, number> = {};
    if (chartType === "stacked_bar_100") {
      for (const c of cats) {
        totalsByCat[c] = sNames.reduce((acc, s) => acc + (lookup[s]?.[c] || 0), 0) || 1;
      }
    }
    return {
      textStyle: { fontFamily },
      color: palette,
      tooltip: baseTooltip,
      legend: legendOpt,
      grid: compactGrid,
      xAxis: { type: "category", data: cats, axisLabel: xAxisLabel },
      yAxis: {
        type: "value",
        max: chartType === "stacked_bar_100" ? 100 : undefined,
        axisLabel: chartType === "stacked_bar_100" ? { ...axisLabel, formatter: "{value}%" } : yAxisLabel,
        splitLine,
      },
      series: sNames.map((s) => ({
        name: s,
        type: "bar",
        stack: "total",
        data: cats.map((c) => {
          const raw = lookup[s]?.[c] || 0;
          return chartType === "stacked_bar_100" ? Number(((raw / totalsByCat[c]) * 100).toFixed(1)) : raw;
        }),
      })),
    };
  }

  if (chartType === "line" || chartType === "area" || chartType === "stacked_area") {
    const isArea = chartType === "area" || chartType === "stacked_area";
    if (seriesField && chartType === "stacked_area") {
      const { sNames, lookup } = buildSeriesMap();
      return {
        textStyle: { fontFamily },
        color: palette,
        tooltip: baseTooltip,
        legend: legendOpt,
        grid: compactGrid,
        xAxis: { type: "category", data: cats, axisLabel: xAxisLabel, boundaryGap: false },
        yAxis: { type: "value", axisLabel: yAxisLabel, splitLine },
        series: sNames.map((s) => ({
          name: s, type: "line", stack: "total", smooth: true,
          lineStyle: { width: lineWidth }, areaStyle: { opacity: 0.35 },
          data: cats.map((c) => lookup[s]?.[c] || 0),
        })),
      };
    }
    return {
      textStyle: { fontFamily },
      color: palette,
      tooltip: baseTooltip,
      legend: legendOpt,
      grid: compactGrid,
      xAxis: { type: "category", data: cats, axisLabel: xAxisLabel, boundaryGap: !isArea },
      yAxis: { type: "value", axisLabel: yAxisLabel, splitLine },
      series: [{
        name: yField,
        type: "line",
        smooth: true,
        lineStyle: { width: lineWidth },
        areaStyle: isArea ? { opacity: 0.28 } : undefined,
        label: labelOpt,
        data: cats.map((c) => rows.filter((r) => String(r[xField]) === c).reduce((a, b) => a + Number(b[yField] ?? 0), 0)),
      }],
    };
  }

  if (chartType === "combo") {
    return {
      textStyle: { fontFamily },
      color: palette,
      tooltip: baseTooltip,
      legend: legendOpt,
      grid: compactGrid,
      xAxis: { type: "category", data: cats, axisLabel: xAxisLabel },
      yAxis: [{ type: "value", axisLabel: yAxisLabel, splitLine }, { type: "value", axisLabel: yAxisLabel, splitLine: { show: false } }],
      series: [
        { name: yField, type: "bar", barMaxWidth: isMobile ? 24 : 32, data: rows.map((r) => Number(r[yField] ?? 0)) },
        {
          name: secField || "Trend",
          type: "line",
          yAxisIndex: 1,
          smooth: true,
          lineStyle: { width: lineWidth },
          data: rows.map((r) => Number(r[secField || yField] ?? 0)),
        },
      ],
    };
  }

  if (chartType === "pie" || chartType === "donut") {
    return {
      textStyle: { fontFamily },
      color: palette,
      tooltip: baseTooltip,
      legend: showLegend ? { type: "scroll", bottom: 0, textStyle: { color: textColor, fontSize: isMobile ? 10 : 11, fontFamily } } : { show: false },
      series: [{
        name: yField,
        type: "pie",
        radius: isMobile ? (chartType === "donut" ? ["36%", "60%"] : "60%") : (chartType === "donut" ? ["44%", "72%"] : "70%"),
        center: ["50%", isMobile ? "42%" : "46%"],
        label: { show: !isMobile || rows.length <= 4, color: textColor, fontSize: isMobile ? 10 : 11, fontFamily },
        data: rows.map((r) => ({ name: String(r[xField] ?? ""), value: Number(r[yField] ?? 0) })),
      }],
    };
  }

  if (chartType === "treemap") {
    return {
      color: palette,
      tooltip: baseTooltip,
      series: [{
        type: "treemap",
        roam: false,
        label: { show: true, formatter: "{b}" },
        data: rows.map((r) => ({ name: String(r[xField] ?? ""), value: Number(r[yField] ?? 0) })),
      }],
    };
  }

  if (chartType === "sunburst") {
    return {
      color: palette,
      tooltip: baseTooltip,
      series: [{
        type: "sunburst",
        radius: ["15%", "88%"],
        label: { rotate: "radial", fontSize: 10 },
        data: rows.map((r) => ({ name: String(r[xField] ?? ""), value: Number(r[yField] ?? 0) })),
      }],
    };
  }

  if (chartType === "funnel") {
    return {
      color: palette,
      tooltip: baseTooltip,
      legend: legendOpt,
      series: [{
        type: "funnel",
        left: "10%", top: 32, bottom: 16, width: "80%",
        sort: "descending",
        label: { show: true, position: "inside" },
        data: rows.map((r) => ({ name: String(r[xField] ?? ""), value: Number(r[yField] ?? 0) })),
      }],
    };
  }

  if (chartType === "waterfall") {
    let running = 0;
    const baseData: number[] = [];
    const posData: (number | string)[] = [];
    const negData: (number | string)[] = [];
    rows.forEach((r) => {
      const v = Number(r[yField] ?? 0);
      if (v >= 0) {
        baseData.push(running);
        posData.push(v);
        negData.push("-");
        running += v;
      } else {
        running += v;
        baseData.push(running);
        posData.push("-");
        negData.push(Math.abs(v));
      }
    });
    return {
      color: ["#10b981", "#ef4444"],
      tooltip: baseTooltip,
      grid: { left: 48, right: 16, top: 24, bottom: 28, containLabel: true },
      xAxis: { type: "category", data: rows.map((r) => String(r[xField] ?? "")), axisLabel },
      yAxis: { type: "value", axisLabel, splitLine },
      series: [
        { name: "Base", type: "bar", stack: "Total", itemStyle: { borderColor: "transparent", color: "transparent" }, data: baseData },
        { name: "Increase", type: "bar", stack: "Total", data: posData },
        { name: "Decrease", type: "bar", stack: "Total", data: negData },
      ],
    };
  }

  if (chartType === "radar") {
    const maxVal = Math.max(...rows.map((r) => Number(r[yField] ?? 0)), 1) * 1.1;
    return {
      color: palette,
      tooltip: baseTooltip,
      legend: legendOpt,
      radar: {
        indicator: rows.slice(0, 8).map((r) => ({ name: String(r[xField] ?? ""), max: maxVal })),
        axisName: { color: textColor, fontSize: 10 },
      },
      series: [{
        type: "radar",
        data: [
          { value: rows.slice(0, 8).map((r) => Number(r[yField] ?? 0)), name: yField, areaStyle: { opacity: 0.25 } },
          ...(secField ? [{ value: rows.slice(0, 8).map((r) => Number(r[secField] ?? 0)), name: secField }] : []),
        ],
      }],
    };
  }

  if (chartType === "scatter" || chartType === "bubble") {
    const pts = rows.map((r, i) => [
      Number(r[yField] ?? i),
      Number(r[secField || yField] ?? 0),
      Number(r.size_value ?? r.quantity ?? 20),
      String(r[xField] ?? ""),
    ]);
    return {
      color: palette,
      tooltip: baseTooltip,
      grid: { left: 48, right: 20, top: 24, bottom: 28, containLabel: true },
      xAxis: { type: "value", name: yField, axisLabel, splitLine },
      yAxis: { type: "value", name: secField || yField, axisLabel, splitLine },
      series: [{
        type: "scatter",
        symbolSize: (data: any) => (chartType === "bubble" ? Math.max(12, Math.min(48, Math.sqrt(Number(data[2] || 10)) * 4)) : 14),
        data: pts,
      }],
    };
  }

  if (chartType === "heatmap") {
    const yCats = seriesField ? Array.from(new Set(rows.map((r) => String(r[seriesField] ?? "")))) : ["Value"];
    const hData = rows.map((r) => [
      cats.indexOf(String(r[xField] ?? "")),
      seriesField ? yCats.indexOf(String(r[seriesField] ?? "")) : 0,
      Number(r[yField] ?? 0),
    ]);
    const maxV = Math.max(...hData.map((d) => d[2]), 1);
    return {
      tooltip: { position: "top" },
      grid: { left: 90, right: 20, top: 16, bottom: 48, containLabel: true },
      xAxis: { type: "category", data: cats, axisLabel },
      yAxis: { type: "category", data: yCats, axisLabel },
      visualMap: { min: 0, max: maxV, calculable: true, orient: "horizontal", left: "center", bottom: 0, inRange: { color: ["#e0f2fe", palette[0]] } },
      series: [{ type: "heatmap", data: hData, label: { show: true, fontSize: 10 } }],
    };
  }

  if (chartType === "calendar_heatmap") {
    const calData = rows.slice(0, 365).map((r, idx) => {
      const dStr = String(r[xField] || "").slice(0, 10);
      const validDate = /^\d{4}-\d{2}-\d{2}$/.test(dStr) ? dStr : `2026-${String((idx % 12) + 1).padStart(2, "0")}-${String((idx % 28) + 1).padStart(2, "0")}`;
      return [validDate, Number(r[yField] ?? 0)];
    });
    const maxV = Math.max(...calData.map((d) => Number(d[1])), 1);
    return {
      tooltip: {},
      visualMap: { min: 0, max: maxV, orient: "horizontal", left: "center", bottom: 0 },
      calendar: { top: 30, left: 35, right: 20, range: String(calData[0]?.[0] || "2026").slice(0, 4) },
      series: [{ type: "heatmap", coordinateSystem: "calendar", data: calData }],
    };
  }

  if (chartType === "gauge") {
    const val = Number(rows[0]?.[yField] ?? 0);
    const maxV = Number(styleConfig.max_value || (val > 100 ? val * 1.5 : 100));
    return {
      textStyle: { fontFamily },
      color: palette,
      series: [{
        type: "gauge",
        radius: "92%",
        center: ["50%", "58%"],
        max: maxV,
        progress: { show: true, width: 9 },
        axisLine: { lineStyle: { width: 9 } },
        axisTick: { show: false },
        splitLine: { length: 6, distance: -9, lineStyle: { width: 1.5 } },
        axisLabel: { distance: 12, fontSize: 9, color: mutedColor, fontFamily },
        pointer: { length: "52%", width: 4 },
        title: { offsetCenter: [0, "74%"], fontSize: 10, color: mutedColor, fontFamily },
        detail: {
          valueAnimation: true,
          fontSize: 15,
          fontWeight: "bold",
          offsetCenter: [0, "34%"],
          color: textColor,
          fontFamily,
          formatter: (v: number) => formatBIValue(v, numFmt, decimals, unit, abbreviate),
        },
        data: [{ value: Number(val.toFixed(1)), name: yField }],
      }],
    };
  }

  if (chartType === "boxplot") {
    const vals = rows.map((r) => Number(r[yField] ?? 0)).sort((a, b) => a - b);
    const q = (p: number) => vals[Math.min(vals.length - 1, Math.floor(p * vals.length))] || 0;
    return {
      color: palette,
      tooltip: baseTooltip,
      xAxis: { type: "category", data: [yField], axisLabel },
      yAxis: { type: "value", axisLabel, splitLine },
      series: [{ type: "boxplot", data: [[vals[0] || 0, q(0.25), q(0.5), q(0.75), vals[vals.length - 1] || 0]] }],
    };
  }

  if (chartType === "candlestick") {
    const cData = rows.slice(0, 40).map((r) => {
      const v = Number(r[yField] ?? 100);
      const open = Number(r.open ?? v * 0.92);
      const close = Number(r.close ?? v);
      const low = Number(r.low ?? Math.min(open, close) * 0.9);
      const high = Number(r.high ?? Math.max(open, close) * 1.08);
      return [open, close, low, high];
    });
    return {
      tooltip: baseTooltip,
      xAxis: { type: "category", data: rows.slice(0, 40).map((r) => String(r[xField] ?? "")), axisLabel },
      yAxis: { type: "value", scale: true, axisLabel, splitLine },
      series: [{ type: "candlestick", data: cData }],
    };
  }

  if (chartType === "sankey") {
    const nodeNames = new Set<string>();
    const links: { source: string; target: string; value: number }[] = [];
    rows.forEach((r) => {
      const src = String(r[xField] ?? "Source");
      const tgt = String(r[seriesField || xField] ?? "Target") + (seriesField ? "" : " (Flow)");
      if (src !== tgt) {
        nodeNames.add(src);
        nodeNames.add(tgt);
        links.push({ source: src, target: tgt, value: Math.max(1, Number(r[yField] ?? 1)) });
      }
    });
    return {
      color: palette,
      tooltip: { trigger: "item" },
      series: [{
        type: "sankey",
        emphasis: { focus: "adjacency" },
        label: { color: textColor, fontSize: 11 },
        data: Array.from(nodeNames).map((name) => ({ name })),
        links,
      }],
    };
  }

  if (chartType === "geo_map") {
    // Coordinate-projected Geo Bubble Map across Iran/Regional coordinates
    const maxV = Math.max(...rows.map((r) => Number(r[yField] ?? 0)), 1);
    const pts = rows.map((r, idx) => {
      const place = String(r[xField] ?? "");
      const coords = GEO_CITY_COORDS[place] || [46 + (idx * 2.1) % 14, 28 + (idx * 1.7) % 10];
      return {
        name: place,
        value: [coords[0], coords[1], Number(r[yField] ?? 0)],
      };
    });
    return {
      color: palette,
      tooltip: {
        trigger: "item",
        formatter: (p: any) => `${p.name}: ${formatBIValue(p.value?.[2], numFmt, decimals, unit, true)}`,
      },
      grid: { left: 36, right: 20, top: 20, bottom: 28 },
      xAxis: { type: "value", name: "Longitude", min: 44, max: 62, axisLabel, splitLine },
      yAxis: { type: "value", name: "Latitude", min: 25, max: 40, axisLabel, splitLine },
      series: [{
        type: "effectScatter",
        coordinateSystem: "cartesian2d",
        symbolSize: (val: any) => Math.max(14, Math.min(46, (Number(val[2] || 0) / maxV) * 44)),
        label: { show: true, formatter: "{b}", position: "top", color: textColor, fontSize: 10 },
        data: pts,
      }],
    };
  }

  return { title: { text: `Chart: ${chartType}` } };
}
