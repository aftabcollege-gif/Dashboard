"use client";

import React, { useEffect, useState } from "react";
import { Download, Eye, Loader2, Plus, Trash2, X } from "lucide-react";
import WidgetRenderer from "@/components/charts/WidgetRenderer";
import { apiDownload, apiFetch } from "@/lib/api";
import { CHART_REGISTRY } from "@/lib/chart-registry";
import { useBi } from "@/lib/context";
import { saveDemoWidgetEdit } from "@/lib/demoStore";
import { parseBpmnXml } from "@/lib/bpmnParser";
import { DashboardWidget, DatasetInfo } from "@/types/bi";

interface Props {
  widget: DashboardWidget;
  datasets: DatasetInfo[];
  pages: { id: string; title: string }[];
  onClose: () => void;
  onUpdated: () => void;
  onChartSwitched?: (widgetId: string, chartType: string) => void;
}

export default function PropertiesPanel({ widget, datasets, pages, onClose, onUpdated, onChartSwitched }: Props) {
  const { t, locale, authStatus, showToast } = useBi();
  const isFa = locale === "fa";
  const [tab, setTab] = useState<"data" | "viz" | "style" | "filters" | "drill" | "advanced">("data");

  // Local editable drafts initialized from widget
  const [title, setTitle] = useState(widget.title);
  const [description, setDescription] = useState(widget.description || "");
  const [datasetId, setDatasetId] = useState(widget.dataset_id || "");
  const [queryMode, setQueryMode] = useState(widget.query.query_mode || "visual");
  const [sqlText, setSqlText] = useState(widget.query.sql_text || "");
  const [visualSpec, setVisualSpec] = useState<Record<string, any>>(widget.query.visual_spec || {});
  const [style, setStyle] = useState<Record<string, any>>(widget.config.style || {});
  const [viz, setViz] = useState<Record<string, any>>(widget.config.visualization || {});
  const [interactions, setInteractions] = useState<Record<string, any>>(widget.config.interactions || {});
  const [hierarchy, setHierarchy] = useState<{ field: string }[]>(widget.drill_rule.hierarchy || []);
  const [levelRules, setLevelRules] = useState<{ level: number; chart_type: string }[]>(
    widget.drill_rule.level_rules || []
  );
  const [drillThroughPage, setDrillThroughPage] = useState(widget.drill_rule.drill_through_page_id || "");

  // Live Visual Preview inside PropertiesPanel
  const [showLivePreview, setShowLivePreview] = useState(true);
  const [livePreviewLoading, setLivePreviewLoading] = useState(false);
  const [livePreviewData, setLivePreviewData] = useState<{ columns: string[]; rows: any[] } | null>(null);
  const [livePreviewError, setLivePreviewError] = useState<string | null>(null);

  // New Widget Filter form
  const [wfField, setWfField] = useState("");
  const [wfOp, setWfOp] = useState("eq");
  const [wfVal, setWfVal] = useState("");

  const activeDs = datasets.find((d) => d.id === datasetId);
  const dsFields = activeDs?.fields || [];

  useEffect(() => {
    if (!showLivePreview || !datasetId || queryMode !== "visual") {
      return;
    }
    const timer = setTimeout(() => {
      setLivePreviewLoading(true);
      setLivePreviewError(null);
      apiFetch<any>("/queries/execute-visual", {
        method: "POST",
        body: JSON.stringify({
          dataset_id: datasetId,
          visual_spec: visualSpec,
          filters: [],
        }),
      })
        .then((res) => {
          setLivePreviewData({ columns: res.columns || [], rows: res.rows || [] });
        })
        .catch((err: any) => {
          setLivePreviewError(err?.message || "Preview error");
        })
        .finally(() => setLivePreviewLoading(false));
    }, 350);
    return () => clearTimeout(timer);
  }, [showLivePreview, datasetId, queryMode, visualSpec]);

  const handleSave = async () => {
    try {
      await apiFetch(`/dashboards/widgets/${widget.id}`, {
        method: "PUT",
        body: JSON.stringify({
          title,
          description,
          dataset_id: datasetId || null,
          query_mode: queryMode,
          sql_text: sqlText || null,
          visual_spec_json: visualSpec,
          style_json: style,
          visualization_json: viz,
          interactions_json: interactions,
          drill_hierarchy: hierarchy,
          drill_rules: levelRules,
          drill_through_page_id: drillThroughPage || null,
        }),
      });
      if (typeof window !== "undefined" && authStatus?.environment === "vercel-preview-demo") {
        saveDemoWidgetEdit(widget.dashboard_id, widget.id, {
          title,
          visual_spec: visualSpec,
        });
      }
      showToast(isFa ? "تنظیمات ویجت ذخیره و اعمال شد" : "Widget properties saved");
      onUpdated();
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleSwitchChart = async (targetType: string) => {
    try {
      const res = await apiFetch(`/dashboards/widgets/${widget.id}/switch-chart`, {
        method: "POST",
        body: JSON.stringify({ target_chart_type: targetType }),
      });
      onChartSwitched?.(widget.id, targetType);
      if (res.warnings?.length) {
        showToast(res.warnings[0]);
      } else {
        showToast(isFa ? `نمودار به ${targetType} تغییر یافت` : `Switched to ${targetType}`);
      }
      onUpdated();
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleAddWidgetFilter = async () => {
    if (!wfField) return;
    try {
      await apiFetch(`/dashboards/widgets/${widget.id}/filters`, {
        method: "POST",
        body: JSON.stringify({
          field_name: wfField,
          filter_type: "dropdown",
          operator: wfOp,
          value: wfVal,
        }),
      });
      setWfVal("");
      showToast(isFa ? "فیلتر ویجت اضافه شد" : "Widget filter added");
      onUpdated();
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleDeleteWidgetFilter = async (id: string) => {
    await apiFetch(`/dashboards/widget-filters/${id}`, { method: "DELETE" });
    onUpdated();
  };

  const tabs = [
    { id: "data", label: isFa ? "۱. داده و فرمول" : t("dataTab") },
    { id: "viz", label: isFa ? "۲. نوع نمودار" : t("vizTab") },
    { id: "style", label: isFa ? "۳. ظاهر و رنگ" : t("styleTab") },
    { id: "filters", label: isFa ? "۴. فیلترها" : t("filtersTab") },
    { id: "drill", label: isFa ? "۵. دریل‌داون" : t("interactionsTab") },
    { id: "advanced", label: isFa ? "۶. پیشرفته" : t("advancedTab") },
  ] as const;

  return (
    <div
      dir={isFa ? "rtl" : "ltr"}
      className="w-full lg:w-[410px] shrink-0 bi-card p-4 flex flex-col max-h-[88vh] lg:h-[calc(100vh-130px)] lg:sticky lg:top-4 overflow-hidden"
    >
      <div className="flex items-center justify-between pb-3 border-b" style={{ borderColor: "var(--bi-border)" }}>
        <div className="min-w-0">
          <h3 className="text-sm font-bold truncate max-w-[260px]">{title || widget.title}</h3>
          <p className="text-[11px]" style={{ color: "var(--bi-muted)" }}>
            {isFa ? `ویرایشگر زنده ویجت · ${widget.chart_type}` : `Widget Properties · ${widget.chart_type}`}
          </p>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setShowLivePreview((p) => !p)}
            className={`px-2 py-1 rounded border text-[10px] font-semibold flex items-center gap-1 ${
              showLivePreview ? "bg-blue-500/15 border-blue-500/50 text-blue-500" : ""
            }`}
            style={showLivePreview ? undefined : { borderColor: "var(--bi-border)" }}
          >
            <Eye className="w-3 h-3" />
            {isFa ? "پیش‌نمایش زنده" : "Live Preview"}
          </button>
          <button onClick={onClose} className="p-1 rounded hover:opacity-70"><X className="w-4 h-4" /></button>
        </div>
      </div>

      {/* Live Visual Preview Box */}
      {showLivePreview && queryMode === "visual" && datasetId && (
        <div
          className="mt-2.5 rounded-xl border p-2 shrink-0"
          style={{ borderColor: "var(--bi-border)", background: "rgba(59,130,246,0.03)" }}
        >
          <div className="flex items-center justify-between text-[10px] mb-1 px-1" style={{ color: "var(--bi-muted)" }}>
            <span>{isFa ? "پیش‌نمایش لحظه‌ای با داده‌های واقعی" : "Live Preview"}</span>
            {livePreviewLoading && <Loader2 className="w-3 h-3 animate-spin text-blue-500" />}
          </div>
          {livePreviewError ? (
            <div className="text-[10px] text-amber-500 py-3 text-center">{livePreviewError}</div>
          ) : livePreviewData ? (
            <div className="h-36">
              <WidgetRenderer
                chartType={widget.chart_type}
                columns={livePreviewData.columns}
                rows={livePreviewData.rows}
                styleConfig={style}
                vizConfig={viz}
                visualSpec={visualSpec}
              />
            </div>
          ) : (
            <div className="h-24 flex items-center justify-center text-[10px] opacity-60">...</div>
          )}
        </div>
      )}

      {/* Tabs */}
      <div className="grid grid-cols-3 gap-1 py-2 border-b text-[11px]" style={{ borderColor: "var(--bi-border)" }}>
        {tabs.map((tb) => (
          <button
            key={tb.id}
            onClick={() => setTab(tb.id)}
            className="py-1.5 px-2 rounded-md font-semibold transition truncate"
            style={{
              background: tab === tb.id ? "var(--bi-primary)" : "transparent",
              color: tab === tb.id ? "#fff" : "var(--bi-muted)",
            }}
          >
            {tb.label}
          </button>
        ))}
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto py-3 space-y-3 text-xs">
        {tab === "data" && (
          <>
            <div>
              <label className="block font-semibold mb-1">{isFa ? "مجموعه‌داده مشترک (Dataset)" : "Dataset"}</label>
              <select
                value={datasetId}
                onChange={(e) => setDatasetId(e.target.value)}
                className="w-full px-2.5 py-1.5 rounded border bg-transparent"
                style={{ borderColor: "var(--bi-border)" }}
              >
                <option value="">{isFa ? "انتخاب مجموعه‌داده..." : "Select dataset..."}</option>
                {datasets.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
              </select>
            </div>
            <div>
              <label className="block font-semibold mb-1">{isFa ? "حالت کوئری" : "Query Mode"}</label>
              <select
                value={queryMode}
                onChange={(e) => setQueryMode(e.target.value)}
                className="w-full px-2.5 py-1.5 rounded border bg-transparent"
                style={{ borderColor: "var(--bi-border)" }}
              >
                <option value="visual">{isFa ? "سازنده دیداری (بدون کدنویسی)" : "Visual Aggregation Builder"}</option>
                <option value="sql">{isFa ? "کوئری سفارشی SQL" : "Custom SQL Query"}</option>
              </select>
            </div>

            {queryMode === "visual" ? (
              <>
                <div>
                  <label className="block font-semibold mb-1">{isFa ? "ستون محور X / دسته‌بندی (Dimension)" : "X Axis / Category Dimension"}</label>
                  <select
                    value={visualSpec.x_field || ""}
                    onChange={(e) => setVisualSpec({ ...visualSpec, x_field: e.target.value })}
                    className="w-full px-2.5 py-1.5 rounded border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  >
                    <option value="">{isFa ? "بدون دسته‌بندی (کارت KPI / عددی)" : "None (KPI / Scalar)"}</option>
                    {dsFields.map((f) => <option key={f.field_name} value={f.field_name}>{f.display_name || f.field_name} ({f.data_type})</option>)}
                  </select>
                </div>
                <div>
                  <label className="block font-semibold mb-1">{isFa ? "تفکیک سری / گروه رنگی (اختیاری)" : "Series / Legend Split (Optional)"}</label>
                  <select
                    value={visualSpec.series_field || ""}
                    onChange={(e) => setVisualSpec({ ...visualSpec, series_field: e.target.value || undefined })}
                    className="w-full px-2.5 py-1.5 rounded border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  >
                    <option value="">{isFa ? "ندارد" : "None"}</option>
                    {dsFields.map((f) => <option key={f.field_name} value={f.field_name}>{f.display_name || f.field_name}</option>)}
                  </select>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="block font-semibold mb-1">{isFa ? "ستون شاخص (Y)" : "Y Measure"}</label>
                    <select
                      value={visualSpec.y_field || ""}
                      onChange={(e) => setVisualSpec({ ...visualSpec, y_field: e.target.value })}
                      className="w-full px-2 py-1.5 rounded border bg-transparent"
                      style={{ borderColor: "var(--bi-border)" }}
                    >
                      <option value="*">{isFa ? "* (تعداد ردیف)" : "* (Rows)"}</option>
                      {dsFields.map((f) => <option key={f.field_name} value={f.field_name}>{f.display_name || f.field_name}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="block font-semibold mb-1">{isFa ? "تابع تجمیع / فرمول" : "Aggregation / Formula"}</label>
                    <select
                      value={visualSpec.aggregation || "SUM"}
                      onChange={(e) => setVisualSpec({ ...visualSpec, aggregation: e.target.value })}
                      className="w-full px-2 py-1.5 rounded border bg-transparent"
                      style={{ borderColor: "var(--bi-border)" }}
                    >
                      {["SUM", "AVG", "COUNT", "COUNT_DISTINCT", "MIN", "MAX", "MEDIAN", "RANK", "PERCENTAGE", "YOY", "MOM", "YTD", "MTD", "RUNNING_TOTAL", "RAW"].map((a) => (
                        <option key={a} value={a}>{a}</option>
                      ))}
                    </select>
                  </div>
                </div>
                <div>
                  <label className="block font-semibold mb-1">{isFa ? "شاخص دوم (نمودار ترکیبی / حبابی)" : "Secondary Y Measure"}</label>
                  <select
                    value={visualSpec.secondary_y_field || ""}
                    onChange={(e) => setVisualSpec({ ...visualSpec, secondary_y_field: e.target.value || undefined })}
                    className="w-full px-2 py-1.5 rounded border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  >
                    <option value="">{isFa ? "ندارد" : "None"}</option>
                    {dsFields.map((f) => <option key={f.field_name} value={f.field_name}>{f.display_name || f.field_name}</option>)}
                  </select>
                </div>

                {(widget.chart_type === "bpmn" || visualSpec.bpmn_config) && (
                  <div className="p-2.5 rounded-lg border space-y-2 bg-blue-500/5" style={{ borderColor: "var(--bi-border)" }}>
                    <div className="font-bold text-[11px]">
                      {isFa ? "تنظیمات BPMN و نقشه حرارتی" : "BPMN Process & Heatmap Settings"}
                    </div>
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={visualSpec.bpmn_config?.heatmap_enabled ?? true}
                        onChange={(e) =>
                          setVisualSpec({
                            ...visualSpec,
                            bpmn_config: { ...(visualSpec.bpmn_config || {}), heatmap_enabled: e.target.checked },
                          })
                        }
                      />
                      <span>{isFa ? "نمایش نقشه حرارتی زمان انتظار" : "Show Wait-Time Heatmap"}</span>
                    </label>
                    <div className="grid grid-cols-2 gap-1.5">
                      <div>
                        <label className="block text-[10px] font-semibold">{isFa ? "ستون مسئول کارتابل" : "Assignee Field"}</label>
                        <select
                          value={visualSpec.bpmn_config?.assignee_field || ""}
                          onChange={(e) =>
                            setVisualSpec({
                              ...visualSpec,
                              bpmn_config: { ...(visualSpec.bpmn_config || {}), assignee_field: e.target.value || undefined },
                            })
                          }
                          className="w-full px-1.5 py-1 rounded border bg-transparent text-[11px]"
                          style={{ borderColor: "var(--bi-border)" }}
                        >
                          <option value="">{isFa ? "خودکار" : "Auto"}</option>
                          {dsFields.map((f) => <option key={f.field_name} value={f.field_name}>{f.field_name}</option>)}
                        </select>
                      </div>
                      <div>
                        <label className="block text-[10px] font-semibold">{isFa ? "ستون وضعیت" : "Status Field"}</label>
                        <select
                          value={visualSpec.bpmn_config?.status_field || ""}
                          onChange={(e) =>
                            setVisualSpec({
                              ...visualSpec,
                              bpmn_config: { ...(visualSpec.bpmn_config || {}), status_field: e.target.value || undefined },
                            })
                          }
                          className="w-full px-1.5 py-1 rounded border bg-transparent text-[11px]"
                          style={{ borderColor: "var(--bi-border)" }}
                        >
                          <option value="">{isFa ? "خودکار" : "Auto"}</option>
                          {dsFields.map((f) => <option key={f.field_name} value={f.field_name}>{f.field_name}</option>)}
                        </select>
                      </div>
                    </div>
                    <div>
                      <label className="block text-[10px] font-semibold mb-1">
                        {isFa ? "بارگذاری فایل BPMN (.bpmn/.xml)" : "Upload BPMN Model (.bpmn/.xml)"}
                      </label>
                      <input
                        type="file"
                        accept=".bpmn,.xml"
                        onChange={async (ev) => {
                          const f = ev.target.files?.[0];
                          if (!f) return;
                          const xml = await f.text();
                          const parsed = parseBpmnXml(xml);
                          setVisualSpec({
                            ...visualSpec,
                            bpmn_config: {
                              ...(visualSpec.bpmn_config || {}),
                              process_name: parsed.processName,
                              bpmn_xml: xml,
                              nodes: parsed.nodes,
                              edges: parsed.edges,
                            },
                          });
                          showToast(`مدل BPMN با ${parsed.nodes.length} گام بارگذاری شد`);
                        }}
                        className="text-[10px]"
                      />
                    </div>
                  </div>
                )}
              </>
            ) : (
              <div>
                <label className="block font-semibold mb-1">{isFa ? "متن کوئری SQL (فقط SELECT)" : "SQL Query"}</label>
                <textarea
                  rows={6}
                  dir="ltr"
                  value={sqlText}
                  onChange={(e) => setSqlText(e.target.value)}
                  className="w-full px-2.5 py-1.5 rounded border bg-transparent font-mono text-[11px]"
                  style={{ borderColor: "var(--bi-border)" }}
                />
              </div>
            )}
          </>
        )}

        {tab === "viz" && (
          <div className="space-y-2">
            <p className="text-[11px]" style={{ color: "var(--bi-muted)" }}>
              {isFa
                ? "با کلیک روی هر نوع نمودار، نمایش ویجت با حفظ مجموعه داده و فیلترها تغییر می‌کند:"
                : "Click any chart type to convert live while preserving dataset, colors, and filters:"}
            </p>
            <div className="grid grid-cols-2 gap-1.5">
              {CHART_REGISTRY.map((ct) => {
                const active = widget.chart_type === ct.id;
                return (
                  <button
                    key={ct.id}
                    onClick={() => handleSwitchChart(ct.id)}
                    className="text-start px-2.5 py-2 rounded-lg border text-[11px] transition"
                    style={{
                      borderColor: active ? "var(--bi-primary)" : "var(--bi-border)",
                      background: active ? "var(--bi-primary)" : "transparent",
                      color: active ? "#fff" : "var(--bi-text)",
                    }}
                  >
                    <div className="font-semibold truncate">{isFa ? ct.labelFa : ct.label}</div>
                    <div className="text-[10px] opacity-70">{ct.category}</div>
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {tab === "style" && (
          <>
            <div>
              <label className="block font-semibold mb-1">{isFa ? "عنوان ویجت" : "Widget Title"}</label>
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className="w-full px-2.5 py-1.5 rounded border bg-transparent"
                style={{ borderColor: "var(--bi-border)" }}
              />
            </div>
            <div>
              <label className="block font-semibold mb-1">{isFa ? "توضیحات" : "Description"}</label>
              <input
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                className="w-full px-2.5 py-1.5 rounded border bg-transparent"
                style={{ borderColor: "var(--bi-border)" }}
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="block font-semibold mb-1">{isFa ? "رنگ پس‌زمینه" : "Background Color"}</label>
                <input
                  type="color"
                  value={style.background_color || "#ffffff"}
                  onChange={(e) => setStyle({ ...style, background_color: e.target.value })}
                  className="w-full h-8 rounded border bg-transparent"
                />
              </div>
              <div>
                <label className="block font-semibold mb-1">{isFa ? "رنگ متن" : "Text Color"}</label>
                <input
                  type="color"
                  value={style.text_color || "#0f172a"}
                  onChange={(e) => setStyle({ ...style, text_color: e.target.value })}
                  className="w-full h-8 rounded border bg-transparent"
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="block font-semibold mb-1">{isFa ? "فرمت اعداد" : "Number Format"}</label>
                <select
                  value={style.number_format || "number"}
                  onChange={(e) => setStyle({ ...style, number_format: e.target.value })}
                  className="w-full px-2 py-1.5 rounded border bg-transparent"
                  style={{ borderColor: "var(--bi-border)" }}
                >
                  <option value="number">{isFa ? "عددی" : "Number"}</option>
                  <option value="currency">{isFa ? "ارزی / مبلغ" : "Currency"}</option>
                  <option value="percentage">{isFa ? "درصد (%)" : "Percentage (%)"}</option>
                  <option value="custom">{isFa ? "واحد سفارشی" : "Custom Unit"}</option>
                </select>
              </div>
              <div>
                <label className="block font-semibold mb-1">{isFa ? "تعداد اعشار" : "Decimals"}</label>
                <input
                  type="number"
                  min={0}
                  max={4}
                  value={style.decimals ?? 0}
                  onChange={(e) => setStyle({ ...style, decimals: Number(e.target.value) })}
                  className="w-full px-2 py-1.5 rounded border bg-transparent"
                  style={{ borderColor: "var(--bi-border)" }}
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="block font-semibold mb-1">{isFa ? "اندازه فونت (px)" : "Font Size (px)"}</label>
                <input
                  type="number"
                  min={9}
                  max={22}
                  value={style.font_size ?? 11}
                  onChange={(e) => setStyle({ ...style, font_size: Number(e.target.value) })}
                  className="w-full px-2 py-1.5 rounded border bg-transparent"
                  style={{ borderColor: "var(--bi-border)" }}
                />
              </div>
              <div>
                <label className="block font-semibold mb-1">{isFa ? "گردی گوشه‌ها (px)" : "Corner Radius (px)"}</label>
                <input
                  type="number"
                  min={0}
                  max={32}
                  value={style.border_radius ?? 12}
                  onChange={(e) => setStyle({ ...style, border_radius: Number(e.target.value) })}
                  className="w-full px-2 py-1.5 rounded border bg-transparent"
                  style={{ borderColor: "var(--bi-border)" }}
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="block font-semibold mb-1">{isFa ? "ضخامت خط" : "Line Width"}</label>
                <input
                  type="number"
                  step={0.5}
                  min={1}
                  max={8}
                  value={style.line_width ?? 2.5}
                  onChange={(e) => setStyle({ ...style, line_width: Number(e.target.value) })}
                  className="w-full px-2 py-1.5 rounded border bg-transparent"
                  style={{ borderColor: "var(--bi-border)" }}
                />
              </div>
              <div>
                <label className="block font-semibold mb-1">{isFa ? "رنگ اصلی سری داده" : "Primary Series Color"}</label>
                <input
                  type="color"
                  value={style.colors?.[0] || "#2563eb"}
                  onChange={(e) => setStyle({ ...style, colors: [e.target.value, ...(style.colors?.slice(1) || ["#0ea5e9", "#10b981", "#f59e0b"])] })}
                  className="w-full h-8 rounded border bg-transparent"
                />
              </div>
            </div>
            <div className="space-y-1.5 pt-1">
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={viz.show_legend !== false} onChange={(e) => setViz({ ...viz, show_legend: e.target.checked })} />
                <span>{isFa ? "نمایش راهنما (Legend)" : "Show Legend"}</span>
              </label>
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={viz.show_tooltip !== false} onChange={(e) => setViz({ ...viz, show_tooltip: e.target.checked })} />
                <span>{isFa ? "نمایش راهنمای شناور (Tooltip)" : "Show Tooltip"}</span>
              </label>
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={Boolean(viz.data_labels)} onChange={(e) => setViz({ ...viz, data_labels: e.target.checked })} />
                <span>{isFa ? "نمایش برچسب مقادیر روی نمودار" : "Show Data Labels"}</span>
              </label>
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={style.abbreviate !== false} onChange={(e) => setStyle({ ...style, abbreviate: e.target.checked })} />
                <span>{isFa ? "اختصار اعداد بزرگ (K / M / B)" : "Abbreviate K / M Values"}</span>
              </label>
            </div>
          </>
        )}

        {tab === "filters" && (
          <div className="space-y-3">
            <p className="text-[11px]" style={{ color: "var(--bi-muted)" }}>
              {isFa
                ? "فیلترهای اختصاصی ویجت فقط روی همین نمودار اعمال می‌شوند."
                : "Widget-isolated filters only affect this chart without changing other widgets."}
            </p>
            {widget.widget_filters.map((wf) => (
              <div key={wf.id} className="flex items-center justify-between p-2 rounded border" style={{ borderColor: "var(--bi-border)" }}>
                <span><b>{wf.field_name}</b> {wf.operator} {JSON.stringify(wf.value?.value ?? wf.value)}</span>
                <button onClick={() => handleDeleteWidgetFilter(wf.id)} className="text-red-500"><Trash2 className="w-3.5 h-3.5" /></button>
              </div>
            ))}
            <div className="space-y-2 pt-2 border-t" style={{ borderColor: "var(--bi-border)" }}>
              <select
                value={wfField}
                onChange={(e) => setWfField(e.target.value)}
                className="w-full px-2 py-1.5 rounded border bg-transparent"
                style={{ borderColor: "var(--bi-border)" }}
              >
                <option value="">{isFa ? "انتخاب ستون..." : "Select field..."}</option>
                {dsFields.map((f) => <option key={f.field_name} value={f.field_name}>{f.field_name}</option>)}
              </select>
              <div className="grid grid-cols-2 gap-2">
                <select value={wfOp} onChange={(e) => setWfOp(e.target.value)} className="px-2 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                  <option value="eq">{isFa ? "مساوی (=)" : "Equals"}</option>
                  <option value="neq">{isFa ? "نامساوی (!=)" : "Not Equals"}</option>
                  <option value="gt">{isFa ? "بزرگ‌تر از (>)" : "Greater Than"}</option>
                  <option value="lt">{isFa ? "کوچک‌تر از (<)" : "Less Than"}</option>
                  <option value="contains">{isFa ? "شامل" : "Contains"}</option>
                  <option value="not_null">{isFa ? "خالی نباشد" : "Not Null"}</option>
                </select>
                <input
                  value={wfVal}
                  onChange={(e) => setWfVal(e.target.value)}
                  placeholder={isFa ? "مقدار" : "Value"}
                  className="px-2 py-1.5 rounded border bg-transparent"
                  style={{ borderColor: "var(--bi-border)" }}
                />
              </div>
              <button
                type="button"
                onClick={handleAddWidgetFilter}
                className="w-full py-1.5 rounded border font-semibold flex items-center justify-center gap-1"
                style={{ borderColor: "var(--bi-primary)", color: "var(--bi-primary)" }}
              >
                <Plus className="w-3.5 h-3.5" /> {isFa ? "افزودن فیلتر ویجت" : "Add Widget Filter"}
              </button>
            </div>
          </div>
        )}

        {tab === "drill" && (
          <div className="space-y-3">
            <label className="flex items-center gap-2 font-semibold">
              <input
                type="checkbox"
                checked={interactions.cross_filter !== false}
                onChange={(e) => setInteractions({ ...interactions, cross_filter: e.target.checked })}
              />
              <span>{isFa ? "فعال‌سازی Cross-Filter با کلیک روی نمودار" : "Enable Cross-Filtering on Click"}</span>
            </label>

            <div>
              <div className="flex items-center justify-between mb-1">
                <span className="font-semibold">{isFa ? "سلسله‌مراتب Drill-Down" : "Drill-Down Hierarchy"}</span>
                <button
                  type="button"
                  onClick={() => setHierarchy([...hierarchy, { field: dsFields[0]?.field_name || "year" }])}
                  className="text-blue-500 font-semibold"
                >
                  {isFa ? "+ افزودن سطح" : "+ Level"}
                </button>
              </div>
              <div className="space-y-1.5">
                {hierarchy.map((h, i) => (
                  <div key={i} className="flex items-center gap-1.5">
                    <span className="w-12 opacity-70">{isFa ? `سطح ${i}:` : `Lvl ${i}:`}</span>
                    <select
                      value={h.field}
                      onChange={(e) => {
                        const next = [...hierarchy];
                        next[i] = { field: e.target.value };
                        setHierarchy(next);
                      }}
                      className="flex-1 px-2 py-1 rounded border bg-transparent"
                      style={{ borderColor: "var(--bi-border)" }}
                    >
                      {dsFields.map((f) => <option key={f.field_name} value={f.field_name}>{f.field_name}</option>)}
                    </select>
                    <select
                      value={levelRules.find((r) => r.level === i)?.chart_type || ""}
                      onChange={(e) => {
                        const filtered = levelRules.filter((r) => r.level !== i);
                        if (e.target.value) filtered.push({ level: i, chart_type: e.target.value });
                        setLevelRules(filtered);
                      }}
                      className="w-24 px-1.5 py-1 rounded border bg-transparent"
                      style={{ borderColor: "var(--bi-border)" }}
                    >
                      <option value="">{isFa ? "حفظ نمودار" : "Keep chart"}</option>
                      {CHART_REGISTRY.map((c) => <option key={c.id} value={c.id}>{c.id}</option>)}
                    </select>
                    <button onClick={() => setHierarchy(hierarchy.filter((_, j) => j !== i))} className="text-red-500">
                      <X className="w-3.5 h-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            </div>

            <div>
              <label className="block font-semibold mb-1">{isFa ? "صفحه مقصد Drill-Through" : "Drill-Through Target Page"}</label>
              <select
                value={drillThroughPage}
                onChange={(e) => setDrillThroughPage(e.target.value)}
                className="w-full px-2 py-1.5 rounded border bg-transparent"
                style={{ borderColor: "var(--bi-border)" }}
              >
                <option value="">{isFa ? "ندارد" : "None"}</option>
                {pages.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}
              </select>
            </div>
          </div>
        )}

        {tab === "advanced" && (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="block font-semibold mb-1">{isFa ? "مقدار هدف (Target)" : "KPI Target Value"}</label>
                <input
                  type="number"
                  value={style.target_value ?? ""}
                  onChange={(e) => setStyle({ ...style, target_value: Number(e.target.value) })}
                  className="w-full px-2 py-1.5 rounded border bg-transparent"
                  style={{ borderColor: "var(--bi-border)" }}
                />
              </div>
              <div>
                <label className="block font-semibold mb-1">{isFa ? "مقدار دوره قبل" : "KPI Previous Value"}</label>
                <input
                  type="number"
                  value={style.previous_value ?? ""}
                  onChange={(e) => setStyle({ ...style, previous_value: Number(e.target.value) })}
                  className="w-full px-2 py-1.5 rounded border bg-transparent"
                  style={{ borderColor: "var(--bi-border)" }}
                />
              </div>
            </div>
            <div className="p-2.5 rounded border space-y-1 text-[11px]" style={{ borderColor: "var(--bi-border)" }}>
              <div><b>{isFa ? "وضعیت تازگی:" : "Freshness:"}</b> {widget.dataset_freshness?.status || "fresh"}</div>
              <div><b>{isFa ? "نسخه داده:" : "Data Version:"}</b> v{widget.dataset_freshness?.data_version || 1}</div>
              <div><b>{isFa ? "تعداد ردیف:" : "Total Rows:"}</b> {widget.dataset_freshness?.row_count || 0}</div>
            </div>
            <div className="space-y-1.5">
              <div className="font-semibold">{isFa ? "خروجی داده‌های ویجت" : "Export Widget Data"}</div>
              <div className="flex gap-2">
                {["csv", "xlsx", "pdf"].map((fmt) => (
                  <button
                    key={fmt}
                    type="button"
                    onClick={() => apiDownload(`/exports/widgets/${widget.id}/${fmt}`, `widget_${widget.id.slice(0, 6)}.${fmt}`)}
                    className="flex-1 py-1.5 rounded border text-xs font-semibold flex items-center justify-center gap-1"
                    style={{ borderColor: "var(--bi-border)" }}
                  >
                    <Download className="w-3 h-3" /> {fmt.toUpperCase()}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Save Button */}
      <div className="pt-3 border-t" style={{ borderColor: "var(--bi-border)" }}>
        <button
          onClick={handleSave}
          className="w-full py-2 rounded-lg text-xs font-bold text-white transition"
          style={{ background: "var(--bi-primary)" }}
        >
          {isFa ? "ذخیره و اعمال تغییرات ویجت" : "Apply & Save Widget Settings"}
        </button>
      </div>
    </div>
  );
}
