"use client";

import React, { useEffect, useState } from "react";
import { Filter, Lock, Plus, RotateCcw, Sparkles, X } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { useBi } from "@/lib/context";
import { DashboardFilterDef, DatasetInfo } from "@/types/bi";

interface Props {
  dashboardId: string;
  pageId: string;
  filters: DashboardFilterDef[];
  datasets: DatasetInfo[];
  filterValues: Record<string, any>;
  onChangeValue: (filterId: string, val: any) => void;
  onResetAll: () => void;
  crossFilters: { field_name: string; operator: string; value: any; source_widget_id: string }[];
  onClearCrossFilter: (idx?: number) => void;
  editMode: boolean;
  onFilterAdded: () => void;
}

export default function GlobalFilterBar({
  dashboardId,
  pageId,
  filters,
  datasets,
  filterValues,
  onChangeValue,
  onResetAll,
  crossFilters,
  onClearCrossFilter,
  editMode,
  onFilterAdded,
}: Props) {
  const { locale, t, showToast } = useBi();
  const [optionsMap, setOptionsMap] = useState<Record<string, any[]>>({});
  const [showAdd, setShowAdd] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDatasetId, setNewDatasetId] = useState(datasets[0]?.id || "");
  const [newField, setNewField] = useState("");
  const [newScope, setNewScope] = useState<"global" | "dashboard">("global");
  const [newType, setNewType] = useState("multi_select");
  const [newOp, setNewOp] = useState("in");
  const [newParentId, setNewParentId] = useState("");
  const [newLocked, setNewLocked] = useState(false);

  const visibleFilters = filters.filter(
    (f) => f.scope === "global" || !f.page_id || f.page_id === pageId
  );

  // Fetch cascading filter options whenever parent filter values change
  useEffect(() => {
    let cancelled = false;
    async function loadOptions() {
      const nextMap: Record<string, any[]> = {};
      for (const f of visibleFilters) {
        if (!f.dataset_id) continue;
        if (["relative_date", "date_range", "numeric_range", "top_n", "text_search", "is_null", "not_null"].includes(f.filter_type)) {
          continue;
        }
        const parentFilters: any[] = [];
        if (f.parent_filter_id) {
          const parent = filters.find((pf) => pf.id === f.parent_filter_id);
          const pVal = parent ? filterValues[parent.id] : undefined;
          if (parent && pVal !== undefined && pVal !== "" && (!Array.isArray(pVal) || pVal.length > 0)) {
            parentFilters.push({
              field_name: parent.field_name,
              operator: parent.operator,
              value: pVal,
              is_active: true,
            });
          }
        }
        try {
          const res = await apiFetch<{ values: any[] }>(`/datasets/${f.dataset_id}/filter-options`, {
            method: "POST",
            body: JSON.stringify({ field_name: f.field_name, parent_filters: parentFilters, limit: 100 }),
          });
          nextMap[f.id] = res.values || [];
        } catch {
          nextMap[f.id] = [];
        }
      }
      if (!cancelled) setOptionsMap(nextMap);
    }
    loadOptions();
    return () => {
      cancelled = true;
    };
  }, [JSON.stringify(visibleFilters.map((f) => f.id)), JSON.stringify(filterValues)]);

  const handleAddFilter = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newName || !newField) return;
    try {
      await apiFetch(`/dashboards/${dashboardId}/filters`, {
        method: "POST",
        body: JSON.stringify({
          page_id: newScope === "dashboard" ? pageId : null,
          scope: newScope,
          name: newName,
          field_name: newField,
          dataset_id: newDatasetId || datasets[0]?.id,
          filter_type: newType,
          operator: newOp,
          parent_filter_id: newParentId || null,
          is_locked: newLocked,
        }),
      });
      setShowAdd(false);
      setNewName("");
      setNewParentId("");
      setNewLocked(false);
      showToast("Filter added");
      onFilterAdded();
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleDeleteFilter = async (fid: string) => {
    try {
      await apiFetch(`/dashboards/filters/${fid}`, { method: "DELETE" });
      onFilterAdded();
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const selectedDataset = datasets.find((d) => d.id === (newDatasetId || datasets[0]?.id));

  return (
    <div className="bi-card p-2.5 sm:p-3 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider opacity-75">
          <Filter className="w-3.5 h-3.5" />
          <span>{t("globalFilters")}</span>
        </div>

        {visibleFilters.map((f) => {
          const opts = optionsMap[f.id] || [];
          const val = filterValues[f.id];

          return (
            <div
              key={f.id}
              className="flex items-center justify-between sm:justify-start gap-1.5 px-2.5 py-1.5 sm:py-1 rounded-lg border text-xs flex-1 sm:flex-initial min-w-[140px]"
              style={{ borderColor: "var(--bi-border)", background: "var(--bi-surface)", color: "var(--bi-text)" }}
            >
              <span className="font-semibold opacity-80 shrink-0">
                {f.name}
                {f.scope === "dashboard" ? (locale === "fa" ? " (صفحه)" : " (Page)") : ""}:
              </span>
              {f.is_locked && <Lock className="w-3 h-3 text-amber-500 shrink-0" />}

              {f.filter_type === "relative_date" ? (
                <select
                  disabled={f.is_locked}
                  value={val || ""}
                  onChange={(e) => onChangeValue(f.id, e.target.value)}
                  style={{ background: "var(--bi-surface)", color: "var(--bi-text)" }}
                  className="outline-none font-medium min-w-0 flex-1 sm:flex-initial"
                >
                  <option value="">{locale === "fa" ? "همه زمان‌ها" : "All Time"}</option>
                  <option value="last_7_days">Last 7 Days</option>
                  <option value="last_30_days">Last 30 Days</option>
                  <option value="last_90_days">Last 90 Days</option>
                  <option value="last_365_days">Last 365 Days</option>
                  <option value="ytd">Year to Date (YTD)</option>
                  <option value="mtd">Month to Date (MTD)</option>
                </select>
              ) : f.filter_type === "date_range" ? (
                <div className="flex items-center gap-1">
                  <input
                    type="date"
                    disabled={f.is_locked}
                    value={val?.start || ""}
                    onChange={(e) => onChangeValue(f.id, { ...(val || {}), start: e.target.value })}
                    className="bg-transparent outline-none text-[11px]"
                  />
                  <span>–</span>
                  <input
                    type="date"
                    disabled={f.is_locked}
                    value={val?.end || ""}
                    onChange={(e) => onChangeValue(f.id, { ...(val || {}), end: e.target.value })}
                    className="bg-transparent outline-none text-[11px]"
                  />
                </div>
              ) : f.filter_type === "numeric_range" ? (
                <div className="flex items-center gap-1">
                  <input
                    type="number"
                    disabled={f.is_locked}
                    placeholder="Min"
                    value={val?.min ?? ""}
                    onChange={(e) => onChangeValue(f.id, { ...(val || {}), min: e.target.value ? Number(e.target.value) : "" })}
                    className="bg-transparent outline-none w-14 text-[11px]"
                  />
                  <span>–</span>
                  <input
                    type="number"
                    disabled={f.is_locked}
                    placeholder="Max"
                    value={val?.max ?? ""}
                    onChange={(e) => onChangeValue(f.id, { ...(val || {}), max: e.target.value ? Number(e.target.value) : "" })}
                    className="bg-transparent outline-none w-14 text-[11px]"
                  />
                </div>
              ) : f.filter_type === "text_search" ? (
                <input
                  disabled={f.is_locked}
                  value={val || ""}
                  onChange={(e) => onChangeValue(f.id, e.target.value)}
                  placeholder={locale === "fa" ? "جستجو..." : "Search..."}
                  className="bg-transparent outline-none w-24"
                />
              ) : f.filter_type === "top_n" ? (
                <input
                  type="number"
                  min={1}
                  max={100}
                  disabled={f.is_locked}
                  value={val || ""}
                  onChange={(e) => onChangeValue(f.id, e.target.value ? Number(e.target.value) : "")}
                  placeholder="Top N"
                  className="bg-transparent outline-none w-16"
                />
              ) : f.operator === "is_null" || f.operator === "not_null" ? (
                <span className="text-[11px] font-mono opacity-80">{f.operator.toUpperCase()}</span>
              ) : (
                <select
                  disabled={f.is_locked}
                  value={Array.isArray(val) ? val[0] ?? "" : val ?? ""}
                  onChange={(e) => {
                    const v = e.target.value;
                    if (!v) onChangeValue(f.id, f.operator === "in" || f.operator === "not_in" ? [] : "");
                    else {
                      const typed = opts.find((o) => String(o) === v) ?? v;
                      onChangeValue(f.id, f.operator === "in" || f.operator === "not_in" ? [typed] : typed);
                    }
                  }}
                  style={{ background: "var(--bi-surface)", color: "var(--bi-text)" }}
                  className="outline-none font-medium min-w-0 max-w-[140px]"
                >
                  <option value="">{locale === "fa" ? "همه" : "All"}</option>
                  {opts.map((o) => (
                    <option key={String(o)} value={String(o)}>
                      {String(o)}
                    </option>
                  ))}
                </select>
              )}

              {editMode && (
                <button
                  onClick={() => handleDeleteFilter(f.id)}
                  className="text-red-500 hover:opacity-80"
                  title="Remove filter"
                >
                  <X className="w-3 h-3" />
                </button>
              )}
            </div>
          );
        })}

        {editMode && (
          <button
            onClick={() => setShowAdd((s) => !s)}
            className="px-2.5 py-1 rounded-lg border border-dashed text-xs font-medium flex items-center gap-1 hover:opacity-80"
            style={{ borderColor: "var(--bi-primary)", color: "var(--bi-primary)" }}
          >
            <Plus className="w-3.5 h-3.5" /> Add Filter
          </button>
        )}

        <button
          onClick={onResetAll}
          className="ms-auto px-2.5 py-1 rounded-lg border text-xs flex items-center gap-1 hover:opacity-80"
          style={{ borderColor: "var(--bi-border)" }}
        >
          <RotateCcw className="w-3 h-3" /> {t("resetFilters")}
        </button>
      </div>

      {/* Active Cross-Filter Pills */}
      {crossFilters.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 pt-1 border-t" style={{ borderColor: "var(--bi-border)" }}>
          <span className="text-[11px] font-semibold flex items-center gap-1 text-sky-500">
            <Sparkles className="w-3 h-3" /> {locale === "fa" ? "فیلتر متقاطع فعال روی داشبورد:" : "Cross-Filtering Active:"}
          </span>
          {crossFilters.map((cf, i) => (
            <span
              key={i}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold bg-sky-500/15 text-sky-600 dark:text-sky-400"
            >
              {cf.field_name} = {String(cf.value)}
              <button onClick={() => onClearCrossFilter(i)}><X className="w-3 h-3" /></button>
            </span>
          ))}
          <button onClick={() => onClearCrossFilter()} className="text-[11px] underline opacity-75">
            {locale === "fa" ? "پاک کردن همه" : "Clear all"}
          </button>
        </div>
      )}

      {/* Add Filter Inline Form */}
      {showAdd && (
        <form onSubmit={handleAddFilter} className="pt-2 border-t grid grid-cols-2 md:grid-cols-8 gap-2 text-xs" style={{ borderColor: "var(--bi-border)" }}>
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="Filter Label"
            className="px-2 py-1.5 rounded border bg-transparent"
            style={{ borderColor: "var(--bi-border)" }}
            required
          />
          <select
            value={newDatasetId || datasets[0]?.id || ""}
            onChange={(e) => setNewDatasetId(e.target.value)}
            className="px-2 py-1.5 rounded border bg-transparent"
            style={{ borderColor: "var(--bi-border)" }}
          >
            {datasets.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select>
          <select
            value={newField}
            onChange={(e) => setNewField(e.target.value)}
            className="px-2 py-1.5 rounded border bg-transparent"
            style={{ borderColor: "var(--bi-border)" }}
            required
          >
            <option value="">Select Field...</option>
            {(selectedDataset?.fields || []).map((f) => (
              <option key={f.field_name} value={f.field_name}>{f.field_name}</option>
            ))}
          </select>
          <select
            value={newScope}
            onChange={(e) => setNewScope(e.target.value as any)}
            className="px-2 py-1.5 rounded border bg-transparent"
            style={{ borderColor: "var(--bi-border)" }}
          >
            <option value="global">Global (All Pages)</option>
            <option value="dashboard">Page Scope</option>
          </select>
          <select
            value={`${newType}:${newOp}`}
            onChange={(e) => {
              const [ft, op] = e.target.value.split(":");
              setNewType(ft);
              setNewOp(op);
            }}
            className="px-2 py-1.5 rounded border bg-transparent"
            style={{ borderColor: "var(--bi-border)" }}
          >
            <option value="multi_select:in">Multi Select (Include)</option>
            <option value="multi_select:not_in">Exclude Values</option>
            <option value="dropdown:eq">Dropdown (=)</option>
            <option value="dropdown:neq">Not Equal (!=)</option>
            <option value="date_range:date_range">Date Range (Between)</option>
            <option value="relative_date:relative_date">Relative Date</option>
            <option value="numeric_range:between">Numeric Range (Min/Max)</option>
            <option value="text_search:contains">Text Search (Contains)</option>
            <option value="text_search:starts_with">Text Starts With</option>
            <option value="text_search:ends_with">Text Ends With</option>
            <option value="top_n:top_n">Top N</option>
            <option value="is_null:is_null">Is Null</option>
            <option value="not_null:not_null">Is Not Null</option>
          </select>
          <select
            value={newParentId}
            onChange={(e) => setNewParentId(e.target.value)}
            className="px-2 py-1.5 rounded border bg-transparent"
            style={{ borderColor: "var(--bi-border)" }}
          >
            <option value="">No Parent (Independent)</option>
            {filters.map((pf) => <option key={pf.id} value={pf.id}>Cascade: {pf.name}</option>)}
          </select>
          <label className="flex items-center gap-1 px-2">
            <input type="checkbox" checked={newLocked} onChange={(e) => setNewLocked(e.target.checked)} />
            <span>Lock</span>
          </label>
          <button type="submit" className="px-3 py-1.5 rounded text-white font-semibold" style={{ background: "var(--bi-primary)" }}>
            Create Filter
          </button>
        </form>
      )}
    </div>
  );
}
