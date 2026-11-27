"use client";

import React, { useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  BookOpen,
  Calculator,
  CheckCircle2,
  Code2,
  Edit3,
  Play,
  Plus,
  Save,
  Search,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { apiFetch } from "@/lib/api";
import type {
  AccessibleDatasetMeta,
  AccessibleFieldMeta,
  AccessibleMetricMeta,
  FormulaFunctionMeta,
  FormulaPreviewResponse,
  FormulaValidateResponse,
  ImpactReport,
} from "@/types/descriptiveBuilder";

interface Props {
  isFa: boolean;
  selectedDatasets: AccessibleDatasetMeta[];
  functions: FormulaFunctionMeta[];
  chosenRelIds: string[];
  adHocJoins: Record<string, any>[];
  onRefreshDatasets: () => Promise<void>;
  onSelectMeasureOrField?: (name: string, kind: "field" | "measure") => void;
  showToast: (msg: string) => void;
}

const FUNC_CATEGORY_LABELS: Record<string, { fa: string; en: string }> = {
  all: { fa: "همه توابع", en: "All" },
  aggregate: { fa: "تجمعی", en: "Aggregate" },
  math: { fa: "ریاضی", en: "Math" },
  conditional: { fa: "شرطی و پوچ", en: "Conditional" },
  string: { fa: "متنی", en: "String" },
  date: { fa: "تاریخ و شمسی", en: "Date & Jalali" },
  statistical: { fa: "تحلیلی و آماری", en: "Statistical" },
};

export function FormulaStudioPanel({
  isFa,
  selectedDatasets,
  functions,
  chosenRelIds,
  adHocJoins,
  onRefreshDatasets,
  onSelectMeasureOrField,
  showToast,
}: Props) {
  const editorRef = useRef<HTMLTextAreaElement | null>(null);
  const [targetDatasetId, setTargetDatasetId] = useState(selectedDatasets[0]?.id || "");
  const [calcType, setCalcType] = useState<"calculated_column" | "calculated_measure" | "analytical_metric">(
    "calculated_column"
  );
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingKind, setEditingKind] = useState<"field" | "metric">("field");
  const [techName, setTechName] = useState("net_profit");
  const [displayName, setDisplayName] = useState("سود خالص");
  const [description, setDescription] = useState("");
  const [formatType, setFormatType] = useState("number");
  const [unit, setUnit] = useState("");
  const [formula, setFormula] = useState("revenue - cost");

  const [funcCategory, setFuncCategory] = useState("all");
  const [funcSearch, setFuncSearch] = useState("");
  const [colSearch, setColSearch] = useState("");

  const [valResult, setValResult] = useState<FormulaValidateResponse | null>(null);
  const [previewResult, setPreviewResult] = useState<FormulaPreviewResponse | null>(null);
  const [busyValidate, setBusyValidate] = useState(false);
  const [busyPreview, setBusyPreview] = useState(false);
  const [busySave, setBusySave] = useState(false);

  // Dependency Impact modal state (when 409 returned)
  const [impactDialog, setImpactDialog] = useState<{
    action: "update" | "delete_field" | "delete_metric";
    targetId: string;
    message: string;
    impact: ImpactReport;
  } | null>(null);

  const effectiveDsId = targetDatasetId && selectedDatasets.some((d) => d.id === targetDatasetId)
    ? targetDatasetId
    : selectedDatasets[0]?.id || "";

  const allCalcFields = useMemo(() => {
    const items: { dsId: string; dsName: string; field: AccessibleFieldMeta }[] = [];
    for (const ds of selectedDatasets) {
      for (const f of ds.fields) {
        if (f.is_calculated) items.push({ dsId: ds.id, dsName: ds.name, field: f });
      }
    }
    return items;
  }, [selectedDatasets]);

  const allMeasures = useMemo(() => {
    const items: { dsId: string; dsName: string; metric: AccessibleMetricMeta }[] = [];
    for (const ds of selectedDatasets) {
      for (const m of ds.metrics || []) {
        items.push({ dsId: ds.id, dsName: ds.name, metric: m });
      }
    }
    return items;
  }, [selectedDatasets]);

  const filteredFuncs = useMemo(() => {
    const q = funcSearch.trim().toLowerCase();
    return functions.filter((fn) => {
      if (funcCategory !== "all" && fn.category !== funcCategory) return false;
      if (!q) return true;
      return (
        fn.name.toLowerCase().includes(q) ||
        fn.description_fa.toLowerCase().includes(q) ||
        fn.signature.toLowerCase().includes(q)
      );
    });
  }, [functions, funcCategory, funcSearch]);

  const insertToken = (snippet: string) => {
    const el = editorRef.current;
    if (!el) {
      setFormula((prev) => (prev ? `${prev} ${snippet}` : snippet));
      return;
    }
    const start = el.selectionStart ?? formula.length;
    const end = el.selectionEnd ?? formula.length;
    const next = formula.slice(0, start) + snippet + formula.slice(end);
    setFormula(next);
    setTimeout(() => {
      el.focus();
      const pos = start + snippet.length;
      el.setSelectionRange(pos, pos);
    }, 0);
  };

  const handleValidate = async () => {
    if (!selectedDatasets.length || !formula.trim()) return;
    setBusyValidate(true);
    try {
      const res = await apiFetch<FormulaValidateResponse>("/descriptive-builder/formulas/validate", {
        method: "POST",
        body: JSON.stringify({
          dataset_ids: selectedDatasets.map((d) => d.id),
          formula,
          calculation_type: calcType === "analytical_metric" ? "calculated_measure" : calcType,
          self_field_name: editingId ? techName : undefined,
          chosen_relationship_ids: chosenRelIds,
          ad_hoc_joins: adHocJoins,
        }),
      });
      setValResult(res);
    } catch (err: any) {
      setValResult({
        valid: false,
        errors: [err.message || "Validation failed"],
        warnings: [],
        compiled_sql: "",
        output_data_type: "unknown",
        calculation_type: calcType,
        has_aggregate: false,
        referenced_fields: [],
        referenced_datasets: [],
        referenced_calculated_fields: [],
        referenced_metrics: [],
      });
    } finally {
      setBusyValidate(false);
    }
  };

  const handlePreview = async () => {
    if (!selectedDatasets.length || !formula.trim()) return;
    setBusyPreview(true);
    try {
      const res = await apiFetch<FormulaPreviewResponse>("/descriptive-builder/formulas/preview", {
        method: "POST",
        body: JSON.stringify({
          dataset_ids: selectedDatasets.map((d) => d.id),
          formula,
          calculation_type: calcType === "analytical_metric" ? "calculated_measure" : calcType,
          field_name: techName.trim() || "calc_preview",
          chosen_relationship_ids: chosenRelIds,
          ad_hoc_joins: adHocJoins,
          limit: 15,
        }),
      });
      setPreviewResult(res);
      setValResult(res);
    } catch (err: any) {
      showToast(err.message || (isFa ? "خطا در پیش‌نمایش فرمول" : "Formula preview failed"));
    } finally {
      setBusyPreview(false);
    }
  };

  const handleSaveFormula = async (force = false) => {
    const cleanTech = techName.trim().replace(/\s+/g, "_").toLowerCase();
    if (!effectiveDsId || !cleanTech || !formula.trim()) {
      showToast(isFa ? "نام فنی، دیتاست هدف و فرمول الزامی است." : "Target dataset, technical name, and formula are required.");
      return;
    }
    setBusySave(true);
    try {
      if (calcType === "calculated_column" && editingKind !== "metric") {
        const url = editingId
          ? `/descriptive-builder/calculated-fields/${editingId}`
          : "/descriptive-builder/calculated-fields";
        const method = editingId ? "PUT" : "POST";
        await apiFetch(url, {
          method,
          body: JSON.stringify({
            dataset_id: effectiveDsId,
            dataset_ids: selectedDatasets.map((d) => d.id),
            field_name: cleanTech,
            display_name: displayName.trim() || cleanTech,
            description,
            formula,
            calculation_type: "calculated_column",
            format_type: formatType,
            unit,
            force,
          }),
        });
        showToast(
          editingId
            ? isFa ? "فیلد محاسبه‌شده با موفقیت به‌روزرسانی شد." : "Calculated field updated."
            : isFa ? "فیلد محاسبه‌شده ذخیره شد و به لیست ستون‌ها اضافه گردید." : "Calculated field saved."
        );
        onSelectMeasureOrField?.(cleanTech, "field");
      } else {
        await apiFetch("/descriptive-builder/measures", {
          method: "POST",
          body: JSON.stringify({
            dataset_id: effectiveDsId,
            dataset_ids: selectedDatasets.map((d) => d.id),
            name: displayName.trim() || cleanTech,
            slug: cleanTech,
            description,
            formula,
            metric_kind: calcType,
            format_type: formatType,
            unit,
            force,
          }),
        });
        showToast(isFa ? "شاخص تحلیلی ذخیره و آماده استفاده در نمودارها شد." : "Analytical measure saved.");
        onSelectMeasureOrField?.(cleanTech, "measure");
      }
      setEditingId(null);
      setImpactDialog(null);
      await onRefreshDatasets();
    } catch (err: any) {
      // Check if 409 impact conflict
      if (err.status === 409 && err.detail?.impact) {
        setImpactDialog({
          action: "update",
          targetId: editingId || "",
          message: err.detail.message || err.message,
          impact: err.detail.impact,
        });
      } else {
        showToast(err.message || (isFa ? "خطا در ذخیره فرمول" : "Failed to save formula"));
      }
    } finally {
      setBusySave(false);
    }
  };

  const handleDeleteFieldOrMetric = async (kind: "field" | "metric", id: string, force = false) => {
    try {
      const path =
        kind === "field"
          ? `/descriptive-builder/calculated-fields/${id}?force=${force}`
          : `/descriptive-builder/measures/${id}?force=${force}`;
      await apiFetch(path, { method: "DELETE" });
      showToast(isFa ? "مورد انتخابی با موفقیت حذف شد." : "Deleted successfully.");
      setImpactDialog(null);
      if (editingId === id) setEditingId(null);
      await onRefreshDatasets();
    } catch (err: any) {
      if (err.status === 409 && err.detail?.impact) {
        setImpactDialog({
          action: kind === "field" ? "delete_field" : "delete_metric",
          targetId: id,
          message: err.detail.message || err.message,
          impact: err.detail.impact,
        });
      } else {
        showToast(err.message || "Delete failed");
      }
    }
  };

  return (
    <div className="space-y-4 text-xs">
      {/* Dependency Impact Confirmation Modal */}
      {impactDialog && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4">
          <div className="bi-card w-full max-w-lg p-5 space-y-4 border-amber-500">
            <div className="flex items-center justify-between">
              <h4 className="font-bold text-sm text-amber-500 flex items-center gap-2">
                <AlertTriangle className="w-4 h-4" />
                {isFa ? "هشدار وابستگی (Dependency Impact)" : "Dependency Impact Warning"}
              </h4>
              <button type="button" onClick={() => setImpactDialog(null)}><X className="w-4 h-4" /></button>
            </div>
            <p>{impactDialog.message}</p>
            <div className="p-3 rounded-lg border space-y-2 text-[11px]" style={{ borderColor: "var(--bi-border)", background: "var(--bi-surface)" }}>
              {impactDialog.impact.calculated_fields.length > 0 && (
                <div>
                  <strong>{isFa ? "فیلدهای محاسبه‌شده وابسته:" : "Dependent Calculated Fields:"}</strong>{" "}
                  {impactDialog.impact.calculated_fields.map((f) => f.display_name || f.field_name).join("، ")}
                </div>
              )}
              {impactDialog.impact.semantic_metrics.length > 0 && (
                <div>
                  <strong>{isFa ? "شاخص‌های تحلیلی وابسته:" : "Dependent Metrics:"}</strong>{" "}
                  {impactDialog.impact.semantic_metrics.map((m) => m.name || m.slug).join("، ")}
                </div>
              )}
              {impactDialog.impact.widgets.length > 0 && (
                <div>
                  <strong>{isFa ? "ویجت‌های داشبورد وابسته:" : "Dependent Dashboard Widgets:"}</strong>{" "}
                  {impactDialog.impact.widgets.map((w) => w.title).join("، ")}
                </div>
              )}
            </div>
            <div className="flex justify-end gap-2">
              <button type="button" onClick={() => setImpactDialog(null)} className="px-3 py-1.5 rounded-lg border" style={{ borderColor: "var(--bi-border)" }}>
                {isFa ? "انصراف" : "Cancel"}
              </button>
              <button
                type="button"
                onClick={() => {
                  if (impactDialog.action === "update") handleSaveFormula(true);
                  else if (impactDialog.action === "delete_field") handleDeleteFieldOrMetric("field", impactDialog.targetId, true);
                  else handleDeleteFieldOrMetric("metric", impactDialog.targetId, true);
                }}
                className="px-3 py-1.5 rounded-lg bg-amber-600 text-white font-bold"
              >
                {isFa ? "تأیید و اعمال اجباری" : "Confirm & Proceed"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Top IDE Grid: Columns Toolbox | Formula Editor | Function Guide */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
        {/* Left: Columns & Saved Formulas Picker (Drag + Click) */}
        <div className="lg:col-span-3 bi-card p-3.5 space-y-3 flex flex-col max-h-[540px]">
          <div className="font-bold flex items-center justify-between">
            <span>{isFa ? "فیلدها و شاخص‌ها (کشیدن / کلیک)" : "Columns & Measures"}</span>
          </div>
          <div className="relative">
            <Search className="w-3.5 h-3.5 absolute top-2 start-2.5" style={{ color: "var(--bi-muted)" }} />
            <input
              value={colSearch}
              onChange={(e) => setColSearch(e.target.value)}
              placeholder={isFa ? "جستجوی ستون..." : "Search column..."}
              className="w-full ps-8 pe-2 py-1.5 rounded-lg border text-[11px] bg-transparent"
              style={{ borderColor: "var(--bi-border)" }}
            />
          </div>
          <div className="flex-1 overflow-y-auto space-y-3 pe-1">
            {selectedDatasets.map((ds) => {
              const fields = ds.fields.filter((f) => {
                const q = colSearch.trim().toLowerCase();
                if (!q) return true;
                return f.field_name.toLowerCase().includes(q) || f.display_name.toLowerCase().includes(q);
              });
              if (!fields.length) return null;
              return (
                <div key={ds.id} className="space-y-1">
                  <div className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-500">
                    {ds.name}
                  </div>
                  {fields.map((f) => {
                    const insertRef = selectedDatasets.length > 1 ? `${ds.name}.${f.field_name}` : f.field_name;
                    return (
                      <button
                        key={f.id}
                        type="button"
                        draggable
                        onDragStart={(e) => e.dataTransfer.setData("text/plain", insertRef)}
                        onClick={() => insertToken(insertRef)}
                        className="w-full text-start px-2 py-1.5 rounded-lg border hover:border-blue-500/50 hover:bg-blue-500/5 flex items-center justify-between gap-1 text-[11px] transition"
                        style={{ borderColor: "var(--bi-border)" }}
                        title={isFa ? "برای درج در فرمول کلیک کنید یا بکشید" : "Click or drag to insert"}
                      >
                        <span className="truncate font-medium">
                          {f.is_calculated ? "fx " : ""}
                          {f.display_name || f.field_name}
                        </span>
                        <span className="text-[9px] px-1 rounded bg-slate-500/15 shrink-0">{f.data_type}</span>
                      </button>
                    );
                  })}
                </div>
              );
            })}
          </div>
        </div>

        {/* Center: Formula Editor, Metadata & Live Preview */}
        <div className="lg:col-span-5 bi-card p-4 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="font-bold text-sm flex items-center gap-1.5">
              <Calculator className="w-4 h-4 text-violet-500" />
              {editingId
                ? isFa ? "ویرایش فرمول محاسباتی" : "Edit Formula"
                : isFa ? "سازنده حرفه‌ای فرمول و شاخص" : "IDE Formula & Measure Studio"}
            </div>
            {editingId && (
              <button
                type="button"
                onClick={() => {
                  setEditingId(null);
                  setTechName("new_metric");
                  setDisplayName("شاخص جدید");
                }}
                className="text-[11px] text-blue-500 hover:underline flex items-center gap-1"
              >
                <Plus className="w-3 h-3" /> {isFa ? "ساخت مورد جدید" : "New"}
              </button>
            )}
          </div>

          {/* Formula Mode Tabs */}
          <div className="grid grid-cols-3 gap-1 p-1 rounded-lg border text-[11px] font-semibold" style={{ borderColor: "var(--bi-border)" }}>
            {[
              { id: "calculated_column", label: isFa ? "ستون محاسبه‌شده (سطری)" : "Row Column" },
              { id: "calculated_measure", label: isFa ? "شاخص تجمعی (Measure)" : "Measure" },
              { id: "analytical_metric", label: isFa ? "شاخص تحلیلی سازمانی" : "Metric" },
            ].map((m) => (
              <button
                key={m.id}
                type="button"
                onClick={() => setCalcType(m.id as any)}
                className="py-1.5 px-2 rounded-md transition"
                style={{
                  background: calcType === m.id ? "var(--bi-primary)" : "transparent",
                  color: calcType === m.id ? "#fff" : "var(--bi-muted)",
                }}
              >
                {m.label}
              </button>
            ))}
          </div>

          {/* Metadata inputs */}
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
            <div>
              <label className="block text-[10px] font-semibold mb-1">{isFa ? "دیتاست مالک" : "Owner Dataset"}</label>
              <select
                value={effectiveDsId}
                onChange={(e) => setTargetDatasetId(e.target.value)}
                className="w-full px-2 py-1.5 rounded border text-[11px]"
                style={{ background: "var(--bi-surface)", borderColor: "var(--bi-border)" }}
              >
                {selectedDatasets.map((d) => (
                  <option key={d.id} value={d.id}>{d.name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-[10px] font-semibold mb-1">{isFa ? "نام فنی (انگلیسی)" : "Technical Name"}</label>
              <input
                dir="ltr"
                value={techName}
                onChange={(e) => setTechName(e.target.value)}
                placeholder="profit_margin"
                className="w-full px-2 py-1.5 rounded border font-mono text-[11px] bg-transparent"
                style={{ borderColor: "var(--bi-border)" }}
              />
            </div>
            <div>
              <label className="block text-[10px] font-semibold mb-1">{isFa ? "عنوان نمایشی فارسی" : "Display Label"}</label>
              <input
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="حاشیه سود"
                className="w-full px-2 py-1.5 rounded border text-[11px] bg-transparent"
                style={{ borderColor: "var(--bi-border)" }}
              />
            </div>
          </div>

          {/* Code Editor */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="text-[10px] font-semibold flex items-center gap-1">
                <Code2 className="w-3.5 h-3.5 text-blue-500" />
                {isFa ? "ویرایشگر عبارت فرمول (پشتیبانی از کشیدن و رها کردن ستون و تابع):" : "Formula Expression Editor (Drop columns/functions here):"}
              </label>
              <div className="flex gap-1">
                {["+", "-", "*", "/", "(", ")", "NULLIF(x, 0)"].map((op) => (
                  <button
                    key={op}
                    type="button"
                    onClick={() => insertToken(op === "NULLIF(x, 0)" ? "NULLIF(, 0)" : ` ${op} `)}
                    className="px-1.5 py-0.5 rounded border font-mono text-[10px] hover:bg-blue-500/10"
                    style={{ borderColor: "var(--bi-border)" }}
                  >
                    {op}
                  </button>
                ))}
              </div>
            </div>
            <textarea
              ref={editorRef}
              dir="ltr"
              rows={5}
              value={formula}
              onChange={(e) => setFormula(e.target.value)}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault();
                const dropped = e.dataTransfer.getData("text/plain");
                if (dropped) insertToken(dropped);
              }}
              placeholder="Example: ROUND((SUM(revenue) - SUM(cost)) * 100.0 / NULLIF(SUM(revenue), 0), 2)"
              className="w-full p-3 rounded-xl border font-mono text-xs bg-slate-950 text-emerald-300 focus:ring-2 focus:ring-blue-500 outline-none"
              style={{ borderColor: "var(--bi-border)" }}
            />
          </div>

          <div className="grid grid-cols-3 gap-2">
            <div>
              <label className="block text-[10px] font-semibold mb-1">{isFa ? "فرمت نمایش" : "Format"}</label>
              <select
                value={formatType}
                onChange={(e) => setFormatType(e.target.value)}
                className="w-full px-2 py-1.5 rounded border text-[11px]"
                style={{ background: "var(--bi-surface)", borderColor: "var(--bi-border)" }}
              >
                <option value="number">{isFa ? "عددی" : "Number"}</option>
                <option value="currency">{isFa ? "مالی / ریال-تومان" : "Currency"}</option>
                <option value="percentage">{isFa ? "درصد (%)" : "Percentage"}</option>
                <option value="date">{isFa ? "تاریخ" : "Date"}</option>
                <option value="text">{isFa ? "متنی" : "Text"}</option>
              </select>
            </div>
            <div>
              <label className="block text-[10px] font-semibold mb-1">{isFa ? "واحد" : "Unit"}</label>
              <input
                value={unit}
                onChange={(e) => setUnit(e.target.value)}
                placeholder={isFa ? "تومان / %" : "% / $"}
                className="w-full px-2 py-1.5 rounded border text-[11px] bg-transparent"
                style={{ borderColor: "var(--bi-border)" }}
              />
            </div>
            <div>
              <label className="block text-[10px] font-semibold mb-1">{isFa ? "توضیح شاخص" : "Description"}</label>
              <input
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder={isFa ? "اختیاری..." : "Optional..."}
                className="w-full px-2 py-1.5 rounded border text-[11px] bg-transparent"
                style={{ borderColor: "var(--bi-border)" }}
              />
            </div>
          </div>

          {/* Action Buttons */}
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={handleValidate}
              disabled={busyValidate}
              className="px-3 py-1.5 rounded-lg border font-semibold flex items-center gap-1 hover:bg-blue-500/10"
              style={{ borderColor: "var(--bi-border)" }}
            >
              <CheckCircle2 className="w-3.5 h-3.5 text-blue-500" />
              {busyValidate ? "..." : isFa ? "اعتبارسنجی نحوی و امنیتی" : "Validate AST"}
            </button>
            <button
              type="button"
              onClick={handlePreview}
              disabled={busyPreview}
              className="px-3 py-1.5 rounded-lg border font-semibold flex items-center gap-1 text-emerald-600 dark:text-emerald-400 border-emerald-500/40 hover:bg-emerald-500/10"
            >
              <Play className="w-3.5 h-3.5" />
              {busyPreview ? "..." : isFa ? "اجرای آزمایشی روی داده‌ها" : "Test & Preview Data"}
            </button>
            <button
              type="button"
              onClick={() => handleSaveFormula(false)}
              disabled={busySave}
              className="px-3.5 py-1.5 rounded-lg font-bold text-white flex items-center gap-1 ms-auto"
              style={{ background: "var(--bi-primary)" }}
            >
              <Save className="w-3.5 h-3.5" />
              {busySave ? "..." : editingId ? (isFa ? "به‌روزرسانی" : "Update") : (isFa ? "ذخیره در مدل" : "Save Formula")}
            </button>
          </div>

          {/* Validation Output */}
          {valResult && (
            <div
              className={`p-3 rounded-xl border space-y-1.5 text-[11px] ${
                valResult.valid
                  ? "border-emerald-500/40 bg-emerald-500/5"
                  : "border-red-500/50 bg-red-500/10"
              }`}
            >
              <div className="flex items-center justify-between font-bold">
                <span className={valResult.valid ? "text-emerald-600 dark:text-emerald-400" : "text-red-500"}>
                  {valResult.valid
                    ? isFa
                      ? `فرمول معتبر و ایمن است (نوع خروجی: ${valResult.output_data_type})`
                      : `Valid Formula (${valResult.output_data_type})`
                    : isFa
                    ? "خطا در فرمول"
                    : "Invalid Formula"}
                </span>
                {valResult.has_aggregate && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-violet-500/15 text-violet-500">
                    Aggregate
                  </span>
                )}
              </div>
              {valResult.errors.map((er, i) => (
                <div key={i} className="text-red-500">• {er}</div>
              ))}
              {valResult.warnings.map((wn, i) => (
                <div key={i} className="text-amber-600 dark:text-amber-400">⚠ {wn}</div>
              ))}
              {valResult.compiled_sql && (
                <div dir="ltr" className="font-mono text-[10px] p-2 rounded bg-slate-900 text-sky-300 overflow-x-auto">
                  {valResult.compiled_sql}
                </div>
              )}
            </div>
          )}

          {/* Live Preview Rows */}
          {previewResult && previewResult.rows?.length > 0 && (
            <div className="border rounded-xl overflow-hidden" style={{ borderColor: "var(--bi-border)" }}>
              <div className="px-3 py-1.5 text-[10px] font-bold flex justify-between border-b" style={{ borderColor: "var(--bi-border)", background: "var(--bi-surface)" }}>
                <span>{isFa ? `نمونه خروجی (${previewResult.row_count} ردیف)` : `Sample Output (${previewResult.row_count} rows)`}</span>
                <span>{previewResult.execution_ms} ms</span>
              </div>
              <div className="max-h-40 overflow-auto">
                <table className="w-full text-[11px]">
                  <thead>
                    <tr className="border-b" style={{ borderColor: "var(--bi-border)" }}>
                      {previewResult.columns.map((c) => (
                        <th key={c} className="px-2 py-1 text-start font-semibold whitespace-nowrap">{c}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {previewResult.rows.slice(0, 8).map((row, idx) => (
                      <tr key={idx} className="border-b last:border-0" style={{ borderColor: "var(--bi-border)" }}>
                        {previewResult.columns.map((c) => (
                          <td key={c} className="px-2 py-1 whitespace-nowrap">
                            {row[c] === null || row[c] === undefined ? "—" : String(row[c])}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>

        {/* Right: Persian SQL Function Catalog Reference */}
        <div className="lg:col-span-4 bi-card p-3.5 space-y-2.5 flex flex-col max-h-[540px]">
          <div className="font-bold flex items-center gap-1.5">
            <BookOpen className="w-4 h-4 text-emerald-500" />
            {isFa ? "مرجع جامع توابع SQL (کلیک یا کشیدن)" : "SQL Function Catalog & Persian Guide"}
          </div>
          <div className="relative">
            <Search className="w-3.5 h-3.5 absolute top-2 start-2.5" style={{ color: "var(--bi-muted)" }} />
            <input
              value={funcSearch}
              onChange={(e) => setFuncSearch(e.target.value)}
              placeholder={isFa ? "جستجوی تابع (مثلاً SUM، شمسی، تقسیم)..." : "Search function..."}
              className="w-full ps-8 pe-2 py-1.5 rounded-lg border text-[11px] bg-transparent"
              style={{ borderColor: "var(--bi-border)" }}
            />
          </div>
          <div className="flex flex-wrap gap-1">
            {Object.entries(FUNC_CATEGORY_LABELS).map(([k, lbl]) => (
              <button
                key={k}
                type="button"
                onClick={() => setFuncCategory(k)}
                className="px-2 py-0.5 rounded-full text-[10px] font-semibold border transition"
                style={{
                  background: funcCategory === k ? "var(--bi-primary)" : "transparent",
                  color: funcCategory === k ? "#fff" : "var(--bi-muted)",
                  borderColor: "var(--bi-border)",
                }}
              >
                {isFa ? lbl.fa : lbl.en}
              </button>
            ))}
          </div>
          <div className="flex-1 overflow-y-auto space-y-2 pe-1">
            {filteredFuncs.map((fn) => (
              <div
                key={fn.name}
                draggable
                onDragStart={(e) => e.dataTransfer.setData("text/plain", `${fn.name}()`)}
                onClick={() => insertToken(fn.example)}
                className="p-2.5 rounded-xl border hover:border-blue-500/60 hover:bg-blue-500/5 cursor-pointer space-y-1 transition"
                style={{ borderColor: "var(--bi-border)" }}
              >
                <div className="flex items-center justify-between gap-2">
                  <code dir="ltr" className="font-mono text-[11px] font-bold text-blue-500">
                    {fn.signature}
                  </code>
                  <span className="text-[9px] px-1.5 py-0.5 rounded bg-slate-500/15">
                    {isFa ? FUNC_CATEGORY_LABELS[fn.category]?.fa || fn.category : fn.category}
                  </span>
                </div>
                <p className="text-[11px]" style={{ color: "var(--bi-text)" }}>{fn.description_fa}</p>
                <div dir="ltr" className="text-[10px] font-mono opacity-75 truncate">
                  Ex: {fn.example}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Saved Calculated Fields & Analytical Measures List */}
      {(allCalcFields.length > 0 || allMeasures.length > 0) && (
        <div className="bi-card p-4 space-y-3">
          <div className="font-bold flex items-center gap-1.5">
            <Sparkles className="w-4 h-4 text-violet-500" />
            {isFa ? "فیلدهای محاسبه‌شده و شاخص‌های ذخیره‌شده در منابع فعلی" : "Saved Calculated Fields & Analytical Measures"}
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2.5">
            {allCalcFields.map(({ dsId, dsName, field }) => (
              <div
                key={field.id}
                className="p-3 rounded-xl border flex flex-col justify-between gap-2"
                style={{ borderColor: "var(--bi-border)", background: "var(--bi-surface)" }}
              >
                <div className="space-y-1">
                  <div className="flex items-center justify-between gap-1">
                    <span className="font-bold truncate">{field.display_name || field.field_name}</span>
                    <span className="text-[9px] px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-500">
                      {dsName} · v{field.version}
                    </span>
                  </div>
                  <code dir="ltr" className="block text-[10px] font-mono opacity-80 truncate">
                    {field.expression}
                  </code>
                </div>
                <div className="flex items-center justify-end gap-1.5">
                  <button
                    type="button"
                    onClick={() => {
                      setTargetDatasetId(dsId);
                      setCalcType("calculated_column");
                      setEditingId(field.id);
                      setEditingKind("field");
                      setTechName(field.field_name);
                      setDisplayName(field.display_name || field.field_name);
                      setDescription(field.description || "");
                      setFormula(field.expression || "");
                      setFormatType(field.format_type || "number");
                      setUnit(field.unit || "");
                    }}
                    className="px-2 py-1 rounded border text-[10px] font-semibold flex items-center gap-1"
                    style={{ borderColor: "var(--bi-border)" }}
                  >
                    <Edit3 className="w-3 h-3" /> {isFa ? "ویرایش" : "Edit"}
                  </button>
                  <button
                    type="button"
                    onClick={() => handleDeleteFieldOrMetric("field", field.id, false)}
                    className="px-2 py-1 rounded border border-red-500/40 text-red-500 text-[10px] font-semibold flex items-center gap-1"
                  >
                    <Trash2 className="w-3 h-3" /> {isFa ? "حذف" : "Delete"}
                  </button>
                </div>
              </div>
            ))}

            {allMeasures.map(({ dsId, dsName, metric }) => (
              <div
                key={metric.id}
                className="p-3 rounded-xl border flex flex-col justify-between gap-2"
                style={{ borderColor: "var(--bi-border)", background: "var(--bi-surface)" }}
              >
                <div className="space-y-1">
                  <div className="flex items-center justify-between gap-1">
                    <span className="font-bold truncate">{metric.name}</span>
                    <span className="text-[9px] px-1.5 py-0.5 rounded bg-violet-500/15 text-violet-500">
                      {dsName} · {metric.metric_kind}
                    </span>
                  </div>
                  <code dir="ltr" className="block text-[10px] font-mono opacity-80 truncate">
                    {metric.formula}
                  </code>
                </div>
                <div className="flex items-center justify-end gap-1.5">
                  <button
                    type="button"
                    onClick={() => {
                      setTargetDatasetId(dsId);
                      setCalcType("analytical_metric");
                      setEditingId(metric.id);
                      setEditingKind("metric");
                      setTechName(metric.slug);
                      setDisplayName(metric.name);
                      setDescription(metric.description || "");
                      setFormula(metric.formula || "");
                      setFormatType(metric.format_type || "number");
                      setUnit(metric.unit || "");
                    }}
                    className="px-2 py-1 rounded border text-[10px] font-semibold flex items-center gap-1"
                    style={{ borderColor: "var(--bi-border)" }}
                  >
                    <Edit3 className="w-3 h-3" /> {isFa ? "ویرایش / نسخه جدید" : "Edit"}
                  </button>
                  <button
                    type="button"
                    onClick={() => handleDeleteFieldOrMetric("metric", metric.id, false)}
                    className="px-2 py-1 rounded border border-red-500/40 text-red-500 text-[10px] font-semibold flex items-center gap-1"
                  >
                    <Trash2 className="w-3 h-3" /> {isFa ? "حذف" : "Delete"}
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
