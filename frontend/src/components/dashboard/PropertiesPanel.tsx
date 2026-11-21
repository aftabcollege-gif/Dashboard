"use client";

import React, { useState } from "react";
import { Download, Plus, Trash2, X } from "lucide-react";
import { apiDownload, apiFetch } from "@/lib/api";
import { CHART_REGISTRY } from "@/lib/chart-registry";
import { useBi } from "@/lib/context";
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
  const { t, locale, showToast } = useBi();
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

  // New Widget Filter form
  const [wfField, setWfField] = useState("");
  const [wfOp, setWfOp] = useState("eq");
  const [wfVal, setWfVal] = useState("");

  const activeDs = datasets.find((d) => d.id === datasetId);
  const dsFields = activeDs?.fields || [];

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
      showToast("Widget properties saved");
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
        showToast(`Switched to ${targetType}`);
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
      showToast("Widget filter added");
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
    { id: "data", label: t("dataTab") },
    { id: "viz", label: t("vizTab") },
    { id: "style", label: t("styleTab") },
    { id: "filters", label: t("filtersTab") },
    { id: "drill", label: t("interactionsTab") },
    { id: "advanced", label: t("advancedTab") },
  ] as const;

  return (
    <div
      className="w-full lg:w-96 shrink-0 bi-card p-4 flex flex-col max-h-[85vh] lg:h-[calc(100vh-140px)] lg:sticky lg:top-4 overflow-hidden"
    >
      <div className="flex items-center justify-between pb-3 border-b" style={{ borderColor: "var(--bi-border)" }}>
        <div>
          <h3 className="text-sm font-bold truncate max-w-[240px]">{widget.title}</h3>
          <p className="text-[11px]" style={{ color: "var(--bi-muted)" }}>
            Widget Properties · {widget.chart_type}
          </p>
        </div>
        <button onClick={onClose} className="p-1 rounded hover:opacity-70"><X className="w-4 h-4" /></button>
      </div>

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
              <label className="block font-semibold mb-1">Dataset</label>
              <select
                value={datasetId}
                onChange={(e) => setDatasetId(e.target.value)}
                className="w-full px-2.5 py-1.5 rounded border bg-transparent"
                style={{ borderColor: "var(--bi-border)" }}
              >
                <option value="">Select dataset...</option>
                {datasets.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
              </select>
            </div>
            <div>
              <label className="block font-semibold mb-1">Query Mode</label>
              <select
                value={queryMode}
                onChange={(e) => setQueryMode(e.target.value)}
                className="w-full px-2.5 py-1.5 rounded border bg-transparent"
                style={{ borderColor: "var(--bi-border)" }}
              >
                <option value="visual">Visual Aggregation Builder</option>
                <option value="sql">Custom SQL Query</option>
              </select>
            </div>

            {queryMode === "visual" ? (
              <>
                <div>
                  <label className="block font-semibold mb-1">X Axis / Category Dimension</label>
                  <select
                    value={visualSpec.x_field || ""}
                    onChange={(e) => setVisualSpec({ ...visualSpec, x_field: e.target.value })}
                    className="w-full px-2.5 py-1.5 rounded border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  >
                    <option value="">None (KPI / Scalar)</option>
                    {dsFields.map((f) => <option key={f.field_name} value={f.field_name}>{f.field_name} ({f.data_type})</option>)}
                  </select>
                </div>
                <div>
                  <label className="block font-semibold mb-1">Series / Legend Split (Optional)</label>
                  <select
                    value={visualSpec.series_field || ""}
                    onChange={(e) => setVisualSpec({ ...visualSpec, series_field: e.target.value || undefined })}
                    className="w-full px-2.5 py-1.5 rounded border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  >
                    <option value="">None</option>
                    {dsFields.map((f) => <option key={f.field_name} value={f.field_name}>{f.field_name}</option>)}
                  </select>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="block font-semibold mb-1">Y Measure</label>
                    <select
                      value={visualSpec.y_field || ""}
                      onChange={(e) => setVisualSpec({ ...visualSpec, y_field: e.target.value })}
                      className="w-full px-2 py-1.5 rounded border bg-transparent"
                      style={{ borderColor: "var(--bi-border)" }}
                    >
                      <option value="*">* (Rows)</option>
                      {dsFields.map((f) => <option key={f.field_name} value={f.field_name}>{f.field_name}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="block font-semibold mb-1">Aggregation / Formula</label>
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
                  <label className="block font-semibold mb-1">Secondary Y Measure (Combo/Scatter/Bubble)</label>
                  <select
                    value={visualSpec.secondary_y_field || ""}
                    onChange={(e) => setVisualSpec({ ...visualSpec, secondary_y_field: e.target.value || undefined })}
                    className="w-full px-2 py-1.5 rounded border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  >
                    <option value="">None</option>
                    {dsFields.map((f) => <option key={f.field_name} value={f.field_name}>{f.field_name}</option>)}
                  </select>
                </div>
              </>
            ) : (
              <div>
                <label className="block font-semibold mb-1">SQL Query</label>
                <textarea
                  rows={6}
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
              Click any chart type to convert live while preserving dataset, colors, and filters:
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
                    <div className="font-semibold truncate">{locale === "fa" ? ct.labelFa : ct.label}</div>
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
              <label className="block font-semibold mb-1">Widget Title</label>
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className="w-full px-2.5 py-1.5 rounded border bg-transparent"
                style={{ borderColor: "var(--bi-border)" }}
              />
            </div>
            <div>
              <label className="block font-semibold mb-1">Description</label>
              <input
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                className="w-full px-2.5 py-1.5 rounded border bg-transparent"
                style={{ borderColor: "var(--bi-border)" }}
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="block font-semibold mb-1">Background Color</label>
                <input
                  type="color"
                  value={style.background_color || "#ffffff"}
                  onChange={(e) => setStyle({ ...style, background_color: e.target.value })}
                  className="w-full h-8 rounded border bg-transparent"
                />
              </div>
              <div>
                <label className="block font-semibold mb-1">Text Color</label>
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
                <label className="block font-semibold mb-1">Number Format</label>
                <select
                  value={style.number_format || "number"}
                  onChange={(e) => setStyle({ ...style, number_format: e.target.value })}
                  className="w-full px-2 py-1.5 rounded border bg-transparent"
                  style={{ borderColor: "var(--bi-border)" }}
                >
                  <option value="number">Number</option>
                  <option value="currency">Currency ($)</option>
                  <option value="percentage">Percentage (%)</option>
                  <option value="custom">Custom Unit</option>
                </select>
              </div>
              <div>
                <label className="block font-semibold mb-1">Decimals</label>
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
                <label className="block font-semibold mb-1">Font Size (px)</label>
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
                <label className="block font-semibold mb-1">Corner Radius (px)</label>
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
                <label className="block font-semibold mb-1">Line Width</label>
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
                <label className="block font-semibold mb-1">Primary Series Color</label>
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
                <span>Show Legend</span>
              </label>
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={viz.show_tooltip !== false} onChange={(e) => setViz({ ...viz, show_tooltip: e.target.checked })} />
                <span>Show Tooltip</span>
              </label>
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={Boolean(viz.data_labels)} onChange={(e) => setViz({ ...viz, data_labels: e.target.checked })} />
                <span>Show Data Labels</span>
              </label>
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={style.abbreviate !== false} onChange={(e) => setStyle({ ...style, abbreviate: e.target.checked })} />
                <span>Abbreviate K / M Values</span>
              </label>
            </div>
          </>
        )}

        {tab === "filters" && (
          <div className="space-y-3">
            <p className="text-[11px]" style={{ color: "var(--bi-muted)" }}>
              Widget-isolated filters only affect this chart without changing other widgets.
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
                <option value="">Select field...</option>
                {dsFields.map((f) => <option key={f.field_name} value={f.field_name}>{f.field_name}</option>)}
              </select>
              <div className="grid grid-cols-2 gap-2">
                <select value={wfOp} onChange={(e) => setWfOp(e.target.value)} className="px-2 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                  <option value="eq">Equals</option>
                  <option value="neq">Not Equals</option>
                  <option value="gt">Greater Than</option>
                  <option value="lt">Less Than</option>
                  <option value="contains">Contains</option>
                  <option value="not_null">Not Null</option>
                </select>
                <input
                  value={wfVal}
                  onChange={(e) => setWfVal(e.target.value)}
                  placeholder="Value"
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
                <Plus className="w-3.5 h-3.5" /> Add Widget Filter
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
              <span>Enable Cross-Filtering on Click</span>
            </label>

            <div>
              <div className="flex items-center justify-between mb-1">
                <span className="font-semibold">Drill-Down Hierarchy</span>
                <button
                  type="button"
                  onClick={() => setHierarchy([...hierarchy, { field: dsFields[0]?.field_name || "year" }])}
                  className="text-blue-500 font-semibold"
                >
                  + Level
                </button>
              </div>
              <div className="space-y-1.5">
                {hierarchy.map((h, i) => (
                  <div key={i} className="flex items-center gap-1.5">
                    <span className="w-12 opacity-70">Lvl {i}:</span>
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
                      <option value="">Keep chart</option>
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
              <label className="block font-semibold mb-1">Drill-Through Target Page</label>
              <select
                value={drillThroughPage}
                onChange={(e) => setDrillThroughPage(e.target.value)}
                className="w-full px-2 py-1.5 rounded border bg-transparent"
                style={{ borderColor: "var(--bi-border)" }}
              >
                <option value="">None</option>
                {pages.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}
              </select>
            </div>
          </div>
        )}

        {tab === "advanced" && (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="block font-semibold mb-1">KPI Target Value</label>
                <input
                  type="number"
                  value={style.target_value ?? ""}
                  onChange={(e) => setStyle({ ...style, target_value: Number(e.target.value) })}
                  className="w-full px-2 py-1.5 rounded border bg-transparent"
                  style={{ borderColor: "var(--bi-border)" }}
                />
              </div>
              <div>
                <label className="block font-semibold mb-1">KPI Previous Value</label>
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
              <div><b>Freshness:</b> {widget.dataset_freshness?.status || "fresh"}</div>
              <div><b>Data Version:</b> v{widget.dataset_freshness?.data_version || 1}</div>
              <div><b>Total Dataset Rows:</b> {widget.dataset_freshness?.row_count || 0}</div>
            </div>
            <div className="space-y-1.5">
              <div className="font-semibold">Export Widget Data</div>
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
          Apply & Save Widget Settings
        </button>
      </div>
    </div>
  );
}
