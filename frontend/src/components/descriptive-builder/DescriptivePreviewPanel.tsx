"use client";

import React, { useState } from "react";
import {
  BarChart3,
  Code2,
  Database,
  FileText,
  RefreshCw,
  Sparkles,
  Trash2,
} from "lucide-react";
import WidgetRenderer from "@/components/charts/WidgetRenderer";
import { apiFetch } from "@/lib/api";
import { CHART_REGISTRY } from "@/lib/chart-registry";
import type { DescriptiveWidgetDraft, GenerateDashboardResponse } from "@/types/descriptiveBuilder";

interface Props {
  isFa: boolean;
  plan: GenerateDashboardResponse;
  onUpdateWidget: (tempId: string, patch: Partial<DescriptiveWidgetDraft>) => void;
  onRemoveWidget: (tempId: string) => void;
  showToast: (msg: string) => void;
}

export function DescriptivePreviewPanel({
  isFa,
  plan,
  onUpdateWidget,
  onRemoveWidget,
  showToast,
}: Props) {
  const [refreshingId, setRefreshingId] = useState<string | null>(null);
  const [sqlVisibleId, setSqlVisibleId] = useState<string | null>(null);

  const handleRefreshWidgetPreview = async (w: DescriptiveWidgetDraft, nextChart?: string) => {
    setRefreshingId(w.temp_id);
    const targetChart = nextChart || w.chart_type;
    try {
      const res = await apiFetch<any>("/descriptive-builder/preview", {
        method: "POST",
        body: JSON.stringify({
          dataset_id: w.dataset_id,
          title: w.title,
          chart_type: targetChart,
          analysis_type: w.analysis_type,
          query_mode: w.query_mode || "visual",
          sql_text: w.sql_text,
          visual_spec: w.visual_spec,
          filters: [],
          calendar_mode: plan.calendar_mode,
        }),
      });
      onUpdateWidget(w.temp_id, {
        chart_type: targetChart,
        columns: res.columns || w.columns,
        rows: res.rows || w.rows,
        row_count: res.row_count ?? w.row_count,
        generated_sql: res.generated_sql || w.generated_sql,
        narrative_fa: res.narrative_fa || w.narrative_fa,
        execution_ms: res.execution_ms,
        error: null,
      });
    } catch (err: any) {
      showToast(err.message || (isFa ? "خطا در به‌روزرسانی پیش‌نمایش" : "Preview refresh failed"));
    } finally {
      setRefreshingId(null);
    }
  };

  const colSpanClass = (wCols: number) => {
    if (wCols <= 3) return "lg:col-span-3 sm:col-span-6 col-span-12";
    if (wCols <= 4) return "lg:col-span-4 sm:col-span-6 col-span-12";
    if (wCols <= 6) return "lg:col-span-6 col-span-12";
    if (wCols <= 8) return "lg:col-span-8 col-span-12";
    return "col-span-12";
  };

  return (
    <div className="space-y-4">
      {/* Executive Summary Banner */}
      <div
        className="bi-card p-4 space-y-2 border-blue-500/40"
        style={{ background: "color-mix(in srgb, var(--bi-primary) 7%, var(--bi-card))" }}
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="font-bold text-sm flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-blue-500" />
            {isFa ? `خلاصه تحلیلی داشبورد: ${plan.title}` : `Executive Summary: ${plan.title}`}
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            {plan.source_names.map((src) => (
              <span
                key={src}
                className="text-[10px] px-2 py-0.5 rounded-full border font-semibold flex items-center gap-1"
                style={{ borderColor: "var(--bi-border)", background: "var(--bi-surface)" }}
              >
                <Database className="w-2.5 h-2.5 text-blue-500" /> {src}
              </span>
            ))}
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 font-semibold">
              {plan.widgets.length} {isFa ? "ویجت آماده" : "Widgets"}
            </span>
          </div>
        </div>
        <p className="text-xs leading-relaxed">{plan.executive_summary_fa}</p>
      </div>

      {/* Preview Grid */}
      <div className="grid grid-cols-12 gap-4">
        {plan.widgets.map((w) => {
          const isKpi = w.chart_type === "kpi";
          return (
            <div key={w.temp_id} className={`${colSpanClass(w.layout?.w || 6)} bi-card flex flex-col overflow-hidden`}>
              {/* Editable Header */}
              <div
                className="px-3.5 py-2.5 border-b flex flex-wrap items-center justify-between gap-2"
                style={{ borderColor: "var(--bi-border)" }}
              >
                <input
                  value={w.title}
                  onChange={(e) => onUpdateWidget(w.temp_id, { title: e.target.value })}
                  className="font-bold text-xs bg-transparent border-b border-transparent focus:border-blue-500 outline-none flex-1 min-w-[140px]"
                  aria-label="Widget Title"
                />
                <div className="flex items-center gap-1.5 shrink-0 flex-wrap">
                  <select
                    value={w.chart_type}
                    onChange={(e) => handleRefreshWidgetPreview(w, e.target.value)}
                    className="text-[10px] px-2 py-1 rounded border font-semibold"
                    style={{ background: "var(--bi-surface)", borderColor: "var(--bi-border)" }}
                    title={isFa ? "تغییر نوع نمودار" : "Switch Chart Type"}
                  >
                    {CHART_REGISTRY.map((ct) => (
                      <option key={ct.id} value={ct.id}>
                        {isFa ? ct.labelFa : ct.label}
                      </option>
                    ))}
                  </select>
                  <select
                    value={w.layout?.w || 6}
                    onChange={(e) =>
                      onUpdateWidget(w.temp_id, { layout: { ...w.layout, w: Number(e.target.value) } })
                    }
                    className="text-[10px] px-1.5 py-1 rounded border"
                    style={{ background: "var(--bi-surface)", borderColor: "var(--bi-border)" }}
                    title={isFa ? "عرض ویجت در شبکه" : "Grid Width"}
                  >
                    <option value={3}>1/4</option>
                    <option value={4}>1/3</option>
                    <option value={6}>1/2</option>
                    <option value={8}>2/3</option>
                    <option value={12}>100%</option>
                  </select>
                  {w.generated_sql && (
                    <button
                      type="button"
                      onClick={() => setSqlVisibleId((cur) => (cur === w.temp_id ? null : w.temp_id))}
                      className="p-1 rounded hover:bg-blue-500/10 text-blue-500"
                      title="SQL"
                    >
                      <Code2 className="w-3.5 h-3.5" />
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => handleRefreshWidgetPreview(w)}
                    className="p-1 rounded hover:bg-blue-500/10"
                    title={isFa ? "بازاجرا" : "Refresh"}
                  >
                    <RefreshCw className={`w-3.5 h-3.5 ${refreshingId === w.temp_id ? "animate-spin" : ""}`} />
                  </button>
                  <button
                    type="button"
                    onClick={() => onRemoveWidget(w.temp_id)}
                    className="p-1 rounded text-red-500 hover:bg-red-500/10"
                    title={isFa ? "حذف ویجت از پیش‌نویس" : "Remove Widget"}
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>

              {/* Optional SQL Inspector */}
              {sqlVisibleId === w.temp_id && w.generated_sql && (
                <div dir="ltr" className="px-3 py-2 bg-slate-950 text-sky-300 font-mono text-[10px] overflow-x-auto border-b" style={{ borderColor: "var(--bi-border)" }}>
                  {w.generated_sql}
                </div>
              )}

              {/* Chart Canvas */}
              <div className={`p-3 ${isKpi ? "h-32" : w.chart_type === "table" || w.chart_type === "pivot_table" ? "h-64" : "h-72"}`}>
                {w.error ? (
                  <div className="h-full flex items-center justify-center text-xs text-red-500 text-center">
                    {w.error}
                  </div>
                ) : (
                  <WidgetRenderer
                    chartType={w.chart_type}
                    columns={w.columns || []}
                    rows={w.rows || []}
                    styleConfig={w.style}
                    vizConfig={w.visualization}
                    visualSpec={w.visual_spec}
                  />
                )}
              </div>

              {/* Fact-grounded Persian Narrative & Source Provenance */}
              {w.narrative_fa && (
                <div
                  className="px-3.5 py-2 border-t text-[11px] leading-relaxed space-y-1"
                  style={{ borderColor: "var(--bi-border)", background: "color-mix(in srgb, var(--bi-surface) 80%, transparent)" }}
                >
                  <div className="flex items-start gap-1.5">
                    <FileText className="w-3.5 h-3.5 text-emerald-500 shrink-0 mt-0.5" />
                    <span className="text-[11px]">{w.narrative_fa}</span>
                  </div>
                  <div className="flex flex-wrap items-center justify-between gap-2 text-[10px]" style={{ color: "var(--bi-muted)" }}>
                    <span>
                      <BarChart3 className="w-3 h-3 inline me-1" />
                      {w.sources_used?.join(" + ")}
                    </span>
                    {w.execution_ms !== undefined && <span>{w.execution_ms} ms</span>}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
