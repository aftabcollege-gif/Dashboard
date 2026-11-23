"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Award,
  BarChart3,
  BookmarkCheck,
  Check,
  CheckCircle2,
  Database,
  Layers,
  Loader2,
  Play,
  Plus,
  RefreshCw,
  ShieldCheck,
  Sliders,
  Sparkles,
  Star,
  Target,
  Wand2,
  X,
  XCircle,
} from "lucide-react";
import { apiFetch } from "@/lib/api";
import { useBi } from "@/lib/context";

interface Props {
  initialDatasetId?: string;
  onClose?: () => void;
  onGenerated?: (dashboardId: string) => void;
  compact?: boolean;
}

const CATEGORY_LABELS_FA: Record<string, string> = {
  aggregate: "تجمیعی",
  ratio: "نسبت",
  growth: "رشد و تغییر",
  comparison: "مقایسه‌ای",
  temporal: "زمانی / تاخیر",
  statistical: "آماری",
  compound: "ترکیبی",
  domain: "تخصصی حوزه",
  data_quality: "کیفیت داده",
  custom: "سفارشی",
};

export function IntelligentDashboardStudio({
  initialDatasetId,
  onClose,
  onGenerated,
}: Props) {
  const { locale, themes, showToast } = useBi();
  const isFa = locale === "fa";

  const [datasets, setDatasets] = useState<any[]>([]);
  const [datasetId, setDatasetId] = useState<string>(initialDatasetId || "");
  const [semanticClasses, setSemanticClasses] = useState<any[]>([]);
  const [analysis, setAnalysis] = useState<any | null>(null);
  const [loading, setLoading] = useState(false);
  const [savingOverrides, setSavingOverrides] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [registering, setRegistering] = useState(false);

  const [activeTab, setActiveTab] = useState<"kpis" | "profile" | "settings">("kpis");
  const [kpiSubTab, setKpiSubTab] = useState<"business" | "quality" | "rejected">("business");

  // User overrides & KPI curation state
  const [fieldEdits, setFieldEdits] = useState<Record<string, { semantic_class?: string; target_value?: string; display_name?: string }>>({});
  const [selectedKpiIds, setSelectedKpiIds] = useState<Set<string>>(new Set());
  const [kpiEdits, setKpiEdits] = useState<Record<string, { name: string; formula: string; target_value?: string; is_favorite?: boolean; value?: number; formatted_value?: string }>>({});
  const [customKpis, setCustomKpis] = useState<any[]>([]);

  // New custom KPI builder
  const [newKpiName, setNewKpiName] = useState("");
  const [newKpiFormula, setNewKpiFormula] = useState("");
  const [newKpiFormat, setNewKpiFormat] = useState("number");
  const [newKpiTarget, setNewKpiTarget] = useState("");
  const [previewingFormula, setPreviewingFormula] = useState(false);

  // Dashboard generation settings
  const [dashTitle, setDashTitle] = useState("");
  const [templateMode, setTemplateMode] = useState<"executive" | "analytical" | "operational">("executive");
  const [detailLevel, setDetailLevel] = useState<"summary" | "standard" | "comprehensive">("standard");
  const [calendarMode, setCalendarMode] = useState<"jalali" | "gregorian">("jalali");
  const [maxPages, setMaxPages] = useState<number>(7);
  const [themeId, setThemeId] = useState<string>("");
  const [dateStart, setDateStart] = useState("");
  const [dateEnd, setDateEnd] = useState("");

  useEffect(() => {
    apiFetch<any[]>("/datasets")
      .then((list) => {
        setDatasets(list || []);
        if (!initialDatasetId && list?.[0]?.id) {
          setDatasetId(list[0].id);
        }
      })
      .catch(() => {});
    apiFetch<any[]>("/ai/semantic-classes")
      .then((cls) => setSemanticClasses(cls || []))
      .catch(() => {});
  }, [initialDatasetId]);

  useEffect(() => {
    if (themes.length > 0 && !themeId) {
      setThemeId(themes[0].id);
    }
  }, [themes, themeId]);

  const loadRecommendation = useCallback(async (dsId: string, forceRefresh = false) => {
    if (!dsId) return;
    setLoading(true);
    try {
      const res = await apiFetch<any>(`/ai/recommend/${dsId}${forceRefresh ? "?refresh=true" : ""}`);
      setAnalysis(res);

      // Initialize selected KPIs from engine's dynamic selection
      const selIds = new Set<string>((res?.kpis?.selected || []).map((k: any) => k.id));
      setSelectedKpiIds(selIds);

      const editInit: Record<string, any> = {};
      for (const k of (res?.kpis?.all_business || [])) {
        editInit[k.id] = {
          name: k.name,
          formula: k.formula,
          target_value: k.target_value != null ? String(k.target_value) : "",
          is_favorite: Boolean(k.is_favorite),
          value: k.value,
          formatted_value: k.formatted_value,
        };
      }
      setKpiEdits(editInit);

      const fInit: Record<string, any> = {};
      for (const f of (res?.field_classifications || [])) {
        fInit[f.field_name] = {
          semantic_class: f.semantic_class,
          display_name: f.display_name,
          target_value: f.target_value != null ? String(f.target_value) : "",
        };
      }
      setFieldEdits(fInit);
      if (!dashTitle && res?.dataset_name) {
        setDashTitle(`داشبورد هوشمند ${res.dataset_name}`);
      }
    } catch (err: any) {
      showToast(err.message || "خطا در پروفایل داده");
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    if (datasetId) {
      loadRecommendation(datasetId, false);
    }
  }, [datasetId, loadRecommendation]);

  const handleSaveFieldOverrides = async () => {
    if (!datasetId) return;
    setSavingOverrides(true);
    try {
      const overrides = Object.entries(fieldEdits).map(([fname, val]) => ({
        field_name: fname,
        semantic_class: val.semantic_class,
        display_name: val.display_name,
        target_value: val.target_value ? Number(val.target_value) : undefined,
      }));
      await apiFetch(`/ai/datasets/${datasetId}/field-overrides`, {
        method: "POST",
        body: JSON.stringify({ overrides }),
      });
      showToast(isFa ? "تنظیمات معنایی فیلدها ذخیره و بازتحلیل شد" : "Field overrides saved & re-analyzed");
      await loadRecommendation(datasetId, true);
    } catch (err: any) {
      showToast(err.message);
    } finally {
      setSavingOverrides(false);
    }
  };

  const handlePreviewKpiFormula = async (kpiId: string, formulaStr: string, nameStr: string) => {
    if (!datasetId || !formulaStr.trim()) return;
    try {
      const res = await apiFetch<any>(`/ai/datasets/${datasetId}/kpis/preview`, {
        method: "POST",
        body: JSON.stringify({ formula: formulaStr, name: nameStr }),
      });
      setKpiEdits((prev) => ({
        ...prev,
        [kpiId]: { ...prev[kpiId], value: res.value, formatted_value: res.formatted_value },
      }));
      showToast(isFa ? `مقدار محاسبه‌شده: ${res.formatted_value}` : `Value: ${res.formatted_value}`);
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleAddCustomKpi = async () => {
    if (!datasetId || !newKpiName.trim() || !newKpiFormula.trim()) return;
    setPreviewingFormula(true);
    try {
      const res = await apiFetch<any>(`/ai/datasets/${datasetId}/kpis/preview`, {
        method: "POST",
        body: JSON.stringify({
          name: newKpiName.trim(),
          formula: newKpiFormula.trim(),
          format_type: newKpiFormat,
          target_value: newKpiTarget ? Number(newKpiTarget) : undefined,
        }),
      });
      const cid = `custom_${Date.now()}`;
      const created = {
        id: cid,
        name: newKpiName.trim(),
        formula: newKpiFormula.trim(),
        format_type: newKpiFormat,
        target_value: newKpiTarget ? Number(newKpiTarget) : undefined,
        value: res.value,
        formatted_value: res.formatted_value,
        category: "custom",
        confidence: 0.99,
        score: 99,
      };
      setCustomKpis((prev) => [created, ...prev]);
      setSelectedKpiIds((prev) => new Set([...Array.from(prev), cid]));
      setKpiEdits((prev) => ({
        ...prev,
        [cid]: {
          name: created.name,
          formula: created.formula,
          target_value: newKpiTarget,
          value: res.value,
          formatted_value: res.formatted_value,
          is_favorite: true,
        },
      }));
      setNewKpiName("");
      setNewKpiFormula("");
      setNewKpiTarget("");
      showToast(isFa ? `شاخص سفارشی با مقدار ${res.formatted_value} افزوده شد` : `Custom KPI added (${res.formatted_value})`);
    } catch (err: any) {
      showToast(err.message);
    } finally {
      setPreviewingFormula(false);
    }
  };

  const handleRegisterSelectedSemantic = async () => {
    if (!datasetId) return;
    setRegistering(true);
    try {
      const allKpis = [...customKpis, ...(analysis?.kpis?.all_business || [])];
      const chosen = allKpis.filter((k) => selectedKpiIds.has(k.id)).map((k) => {
        const ed = kpiEdits[k.id];
        return {
          id: k.id,
          slug: k.slug || k.id,
          name: ed?.name || k.name,
          formula: ed?.formula || k.formula,
          format_type: k.format_type || "number",
          unit: k.unit || "",
          category: k.category || "analytical",
          confidence: k.confidence || 0.9,
          target_value: ed?.target_value ? Number(ed.target_value) : k.target_value,
          is_favorite: ed?.is_favorite ?? k.is_favorite ?? false,
          limitations: k.limitations || [],
          comparison_basis: k.comparison_basis,
          source_datasets: k.source_datasets || [analysis?.dataset_name],
          score: k.score,
        };
      });
      const res = await apiFetch<any>(`/ai/datasets/${datasetId}/kpis/register`, {
        method: "POST",
        body: JSON.stringify({ kpis: chosen }),
      });
      showToast(isFa ? `${res.count} شاخص با موفقیت در Semantic Layer ثبت شد` : `${res.count} KPIs registered in Semantic Layer`);
    } catch (err: any) {
      showToast(err.message);
    } finally {
      setRegistering(false);
    }
  };

  const handleGenerate = async (autoOneClick = false) => {
    if (!datasetId) return;
    setGenerating(true);
    try {
      const allKnown = [...(analysis?.kpis?.all_business || [])];
      const modifiedCustom: any[] = [...customKpis];
      const targetValues: Record<string, number> = {};
      const selList = Array.from(selectedKpiIds);

      if (!autoOneClick) {
        for (const k of allKnown) {
          const ed = kpiEdits[k.id];
          if (ed && ed.target_value && !Number.isNaN(Number(ed.target_value))) {
            targetValues[k.id] = Number(ed.target_value);
          }
          if (ed && selectedKpiIds.has(k.id) && (ed.formula !== k.formula || ed.name !== k.name)) {
            modifiedCustom.push({
              id: k.id,
              name: ed.name,
              formula: ed.formula,
              format_type: k.format_type,
              unit: k.unit,
              target_value: ed.target_value ? Number(ed.target_value) : k.target_value,
            });
          }
        }
      }

      const payload: any = autoOneClick
        ? { dataset_id: datasetId }
        : {
            dataset_id: datasetId,
            title: dashTitle || undefined,
            template_mode: templateMode,
            detail_level: detailLevel,
            calendar_mode: calendarMode,
            max_pages: maxPages,
            theme_id: themeId || undefined,
            selected_kpi_ids: selList.length > 0 ? selList : undefined,
            custom_kpis: modifiedCustom.length > 0 ? modifiedCustom : undefined,
            target_values: Object.keys(targetValues).length > 0 ? targetValues : undefined,
            date_range: dateStart || dateEnd ? { start: dateStart || undefined, end: dateEnd || undefined } : undefined,
          };

      const res = await apiFetch<{ dashboard_id: string; pages_count?: number; widgets_count?: number }>(
        "/ai/generate-dashboard",
        {
          method: "POST",
          body: JSON.stringify(payload),
        }
      );
      showToast(
        isFa
          ? `داشبورد هوشمند (${res.pages_count || 1} صفحه، ${res.widgets_count || ""} ویجت) ساخته شد!`
          : `Intelligent Dashboard (${res.pages_count || 1} pages) created!`
      );
      if (onGenerated) {
        onGenerated(res.dashboard_id);
      } else {
        window.location.href = `/dashboards/${res.dashboard_id}`;
      }
    } catch (err: any) {
      showToast(err.message);
    } finally {
      setGenerating(false);
    }
  };

  const allBusinessKpis = useMemo(() => {
    const base = analysis?.kpis?.all_business || [];
    return [...customKpis, ...base];
  }, [analysis, customKpis]);

  const dqKpis = analysis?.kpis?.data_quality || [];
  const rejectedKpis = analysis?.kpis?.uncomputable || [];
  const domain = analysis?.domain_detection;
  const deepProf = analysis?.deep_profile;
  const fieldsCls = analysis?.field_classifications || [];

  return (
    <div dir={isFa ? "rtl" : "ltr"} className="bi-card p-4 sm:p-5 space-y-4 border-2 border-violet-500/30">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 pb-3 border-b" style={{ borderColor: "var(--bi-border)" }}>
        <div className="flex items-center gap-2.5">
          <div className="w-9 h-9 rounded-xl bg-violet-500/15 text-violet-500 flex items-center justify-center shrink-0">
            <Sparkles className="w-5 h-5" />
          </div>
          <div>
            <h2 className="text-sm sm:text-base font-bold">
              {isFa ? "موتور هوشمند کشف KPI و تولید داشبورد چندصفحه‌ای" : "Intelligent 5-Stage KPI & Multi-Page Dashboard Studio"}
            </h2>
            <p className="text-[11px]" style={{ color: "var(--bi-muted)" }}>
              {isFa
                ? "پروفایل عمیق، طبقه‌بندی ۲۲گانه فیلدها، تشخیص حوزه، اعتبارسنجی AST و شخصی‌سازی KPI قبل از ساخت داشبورد"
                : "Deep profiling, 22-class semantic typing, AST-guarded KPI formulas & customizable multi-page generation"}
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={datasetId}
            onChange={(e) => setDatasetId(e.target.value)}
            className="px-3 py-1.5 rounded-lg border text-xs font-medium min-w-[180px]"
            style={{ background: "var(--bi-surface)", color: "var(--bi-text)", borderColor: "var(--bi-border)" }}
          >
            {datasets.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name} ({(d.row_count || 0).toLocaleString()} ردیف)
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => loadRecommendation(datasetId, true)}
            disabled={loading || !datasetId}
            className="px-2.5 py-1.5 rounded-lg border text-xs flex items-center gap-1 hover:bg-blue-500/10"
            style={{ borderColor: "var(--bi-border)" }}
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
            {isFa ? "تحلیل مجدد" : "Re-Analyze"}
          </button>
          <button
            type="button"
            onClick={() => handleGenerate(true)}
            disabled={generating || !datasetId}
            className="px-3 py-1.5 rounded-lg text-xs font-semibold text-white bg-emerald-600 hover:bg-emerald-700 flex items-center gap-1 disabled:opacity-60"
          >
            <Wand2 className="w-3.5 h-3.5" />
            {generating ? "..." : isFa ? "ساخت سریع ۱-کلیکی" : "1-Click Auto"}
          </button>
          {onClose && (
            <button type="button" onClick={onClose} className="p-1.5 rounded-lg hover:bg-red-500/10 text-red-500">
              <X className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>

      {loading && (
        <div className="py-10 flex items-center justify-center gap-2 text-xs font-semibold" style={{ color: "var(--bi-muted)" }}>
          <Loader2 className="w-4 h-4 animate-spin text-violet-500" />
          {isFa ? "در حال اجرای موتور ۵ مرحله‌ای پروفایل و استخراج KPI..." : "Running 5-stage profiling & KPI discovery..."}
        </div>
      )}

      {!loading && analysis && (
        <>
          {/* Summary KPI Strip */}
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-2.5 text-xs">
            <div className="p-2.5 rounded-lg border" style={{ borderColor: "var(--bi-border)", background: "var(--bi-surface)" }}>
              <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>حوزه شناسایی‌شده</div>
              <div className="font-bold mt-0.5 text-violet-500 truncate">{domain?.primary_domain_fa}</div>
              <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>اطمینان {Math.round((domain?.confidence || 0) * 100)}%</div>
            </div>
            <div className="p-2.5 rounded-lg border" style={{ borderColor: "var(--bi-border)", background: "var(--bi-surface)" }}>
              <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>تعداد رکورد / ستون</div>
              <div className="font-bold mt-0.5">{(deepProf?.row_count || 0).toLocaleString()} × {deepProf?.field_count || 0}</div>
              <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>{deepProf?.is_approximate ? "نمونه‌برداری هوشمند" : "پروفایل دقیق کامل"}</div>
            </div>
            <div className="p-2.5 rounded-lg border" style={{ borderColor: "var(--bi-border)", background: "var(--bi-surface)" }}>
              <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>تکمیل بودن داده</div>
              <div className="font-bold mt-0.5 text-emerald-500">{deepProf?.completeness_pct}%</div>
              <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>تکراری: {deepProf?.duplicate_row_count || 0}</div>
            </div>
            <div className="p-2.5 rounded-lg border" style={{ borderColor: "var(--bi-border)", background: "var(--bi-surface)" }}>
              <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>KPIهای کشف‌شده</div>
              <div className="font-bold mt-0.5">{selectedKpiIds.size} منتخب / {allBusinessKpis.length} کل</div>
              <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>ردشده: {rejectedKpis.length}</div>
            </div>
            <div className="p-2.5 rounded-lg border" style={{ borderColor: "var(--bi-border)", background: "var(--bi-surface)" }}>
              <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>ساختار پیشنهادی</div>
              <div className="font-bold mt-0.5">{(analysis?.recommended_pages || []).length} صفحه تحلیلی</div>
              <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>{(analysis?.suggested_widgets || []).length} ویجت معتبر</div>
            </div>
          </div>

          {deepProf?.approximation_note_fa && (
            <div className="p-2.5 rounded-lg bg-amber-500/10 border border-amber-500/30 text-[11px] flex items-center gap-2 text-amber-600 dark:text-amber-400">
              <AlertTriangle className="w-4 h-4 shrink-0" />
              <span>{deepProf.approximation_note_fa}</span>
            </div>
          )}

          {domain?.evidence_fa && (
            <div className="text-[11px] px-3 py-1.5 rounded-lg bg-violet-500/10 text-violet-600 dark:text-violet-300 flex items-center gap-1.5">
              <Award className="w-3.5 h-3.5 shrink-0" />
              <span><b>شواهد تشخیص حوزه:</b> {domain.evidence_fa}</span>
            </div>
          )}

          {/* Main Studio Tabs */}
          <div className="flex flex-wrap items-center gap-2 border-b pb-2" style={{ borderColor: "var(--bi-border)" }}>
            <button
              type="button"
              onClick={() => setActiveTab("kpis")}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-1.5 ${
                activeTab === "kpis" ? "bg-violet-600 text-white" : "border"
              }`}
              style={{ borderColor: "var(--bi-border)" }}
            >
              <BarChart3 className="w-3.5 h-3.5" />
              {isFa ? `بررسی، ویرایش و انتخاب KPIها (${selectedKpiIds.size})` : `Review & Edit KPIs (${selectedKpiIds.size})`}
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("profile")}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-1.5 ${
                activeTab === "profile" ? "bg-violet-600 text-white" : "border"
              }`}
              style={{ borderColor: "var(--bi-border)" }}
            >
              <Database className="w-3.5 h-3.5" />
              {isFa ? `پروفایل عمیق و طبقه‌بندی ۲۲گانه (${fieldsCls.length})` : `Deep Profile & Semantic Classes`}
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("settings")}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-1.5 ${
                activeTab === "settings" ? "bg-violet-600 text-white" : "border"
              }`}
              style={{ borderColor: "var(--bi-border)" }}
            >
              <Sliders className="w-3.5 h-3.5" />
              {isFa ? "تنظیمات قالب، صفحات و تقویم" : "Dashboard Settings & Pages"}
            </button>
          </div>

          {/* TAB 1: KPI SELECTION, FORMULA EDITING & TARGETS */}
          {activeTab === "kpis" && (
            <div className="space-y-4">
              {/* Sub-tabs */}
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap gap-1.5 text-xs">
                  <button
                    type="button"
                    onClick={() => setKpiSubTab("business")}
                    className={`px-2.5 py-1 rounded-md font-semibold ${kpiSubTab === "business" ? "bg-blue-600 text-white" : "border"}`}
                    style={{ borderColor: "var(--bi-border)" }}
                  >
                    شاخص‌های کسب‌وکار ({allBusinessKpis.length})
                  </button>
                  <button
                    type="button"
                    onClick={() => setKpiSubTab("quality")}
                    className={`px-2.5 py-1 rounded-md font-semibold ${kpiSubTab === "quality" ? "bg-emerald-600 text-white" : "border"}`}
                    style={{ borderColor: "var(--bi-border)" }}
                  >
                    کیفیت داده ({dqKpis.length})
                  </button>
                  <button
                    type="button"
                    onClick={() => setKpiSubTab("rejected")}
                    className={`px-2.5 py-1 rounded-md font-semibold ${kpiSubTab === "rejected" ? "bg-rose-600 text-white" : "border"}`}
                    style={{ borderColor: "var(--bi-border)" }}
                  >
                    غیرقابل محاسبه / ردشده ({rejectedKpis.length})
                  </button>
                </div>

                <button
                  type="button"
                  onClick={handleRegisterSelectedSemantic}
                  disabled={registering || selectedKpiIds.size === 0}
                  className="px-3 py-1.5 rounded-lg border border-emerald-500/40 text-emerald-600 dark:text-emerald-400 text-xs font-semibold flex items-center gap-1 hover:bg-emerald-500/10 disabled:opacity-50"
                >
                  <BookmarkCheck className="w-3.5 h-3.5" />
                  {registering ? "..." : isFa ? "ثبت KPIهای منتخب در Semantic Layer" : "Save Selected to Semantic Layer"}
                </button>
              </div>

              {kpiSubTab === "business" && (
                <div className="space-y-3">
                  {/* Add Custom KPI Row */}
                  <div className="p-3 rounded-xl border border-dashed grid grid-cols-1 md:grid-cols-12 gap-2 text-xs" style={{ borderColor: "var(--bi-border)" }}>
                    <input
                      value={newKpiName}
                      onChange={(e) => setNewKpiName(e.target.value)}
                      placeholder="نام KPI سفارشی (مثلاً حاشیه سود خالص)"
                      className="md:col-span-3 px-2.5 py-1.5 rounded-lg border bg-transparent"
                      style={{ borderColor: "var(--bi-border)" }}
                    />
                    <input
                      dir="ltr"
                      value={newKpiFormula}
                      onChange={(e) => setNewKpiFormula(e.target.value)}
                      placeholder='فرمول SQL: e.g. SUM("revenue") / NULLIF(SUM("cost"), 0)'
                      className="md:col-span-4 px-2.5 py-1.5 rounded-lg border bg-transparent font-mono text-[11px]"
                      style={{ borderColor: "var(--bi-border)" }}
                    />
                    <select
                      value={newKpiFormat}
                      onChange={(e) => setNewKpiFormat(e.target.value)}
                      className="md:col-span-2 px-2 py-1.5 rounded-lg border text-xs"
                      style={{ background: "var(--bi-surface)", color: "var(--bi-text)", borderColor: "var(--bi-border)" }}
                    >
                      <option value="number">عددی</option>
                      <option value="currency">مالی</option>
                      <option value="percentage">درصد (%)</option>
                      <option value="duration">مدت زمان</option>
                    </select>
                    <input
                      type="number"
                      value={newKpiTarget}
                      onChange={(e) => setNewKpiTarget(e.target.value)}
                      placeholder="هدف (اختیاری)"
                      className="md:col-span-1 px-2 py-1.5 rounded-lg border bg-transparent"
                      style={{ borderColor: "var(--bi-border)" }}
                    />
                    <button
                      type="button"
                      onClick={handleAddCustomKpi}
                      disabled={previewingFormula || !newKpiName || !newKpiFormula}
                      className="md:col-span-2 px-3 py-1.5 rounded-lg bg-violet-600 text-white font-semibold flex items-center justify-center gap-1 disabled:opacity-50"
                    >
                      <Plus className="w-3.5 h-3.5" /> افزودن KPI
                    </button>
                  </div>

                  {/* Business KPI Cards */}
                  <div className="space-y-2 max-h-[380px] overflow-y-auto pr-1">
                    {allBusinessKpis.map((k: any) => {
                      const isChecked = selectedKpiIds.has(k.id);
                      const ed = kpiEdits[k.id] || {
                        name: k.name,
                        formula: k.formula,
                        target_value: "",
                        is_favorite: false,
                        formatted_value: k.formatted_value,
                      };
                      return (
                        <div
                          key={k.id}
                          className={`p-3 rounded-xl border text-xs space-y-2 transition ${
                            isChecked ? "border-violet-500/50 bg-violet-500/5" : ""
                          }`}
                          style={{ borderColor: isChecked ? undefined : "var(--bi-border)" }}
                        >
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <div className="flex items-center gap-2 flex-1 min-w-[200px]">
                              <input
                                type="checkbox"
                                checked={isChecked}
                                onChange={() => {
                                  const next = new Set(selectedKpiIds);
                                  if (next.has(k.id)) next.delete(k.id);
                                  else next.add(k.id);
                                  setSelectedKpiIds(next);
                                }}
                                className="w-4 h-4 accent-violet-600"
                              />
                              <input
                                value={ed.name}
                                onChange={(e) =>
                                  setKpiEdits((p) => ({ ...p, [k.id]: { ...ed, name: e.target.value } }))
                                }
                                className="font-bold bg-transparent border-b border-transparent focus:border-violet-500 outline-none flex-1 min-w-0"
                              />
                              <button
                                type="button"
                                onClick={() =>
                                  setKpiEdits((p) => ({ ...p, [k.id]: { ...ed, is_favorite: !ed.is_favorite } }))
                                }
                                className="p-0.5"
                                title="مورد علاقه"
                              >
                                <Star className={`w-3.5 h-3.5 ${ed.is_favorite ? "fill-amber-400 text-amber-400" : "opacity-40"}`} />
                              </button>
                            </div>
                            <div className="flex flex-wrap items-center gap-1.5">
                              <span className="px-2 py-0.5 rounded bg-blue-500/10 text-blue-500 font-semibold text-[10px]">
                                {CATEGORY_LABELS_FA[k.category] || k.category}
                              </span>
                              <span className="px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 font-bold text-[11px]">
                                {ed.formatted_value || k.formatted_value}
                              </span>
                              <span className="px-1.5 py-0.5 rounded bg-slate-500/15 text-[10px]">
                                امتیاز: {k.score}
                              </span>
                              {k.status === "needs_confirmation" && (
                                <span className="px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-600 text-[10px] font-semibold">
                                  نیازمند تأیید
                                </span>
                              )}
                            </div>
                          </div>

                          <div className="grid grid-cols-1 md:grid-cols-12 gap-2 items-center">
                            <div className="md:col-span-7 flex items-center gap-1">
                              <input
                                dir="ltr"
                                value={ed.formula}
                                onChange={(e) =>
                                  setKpiEdits((p) => ({ ...p, [k.id]: { ...ed, formula: e.target.value } }))
                                }
                                className="w-full px-2 py-1 rounded border bg-transparent font-mono text-[11px]"
                                style={{ borderColor: "var(--bi-border)" }}
                              />
                              <button
                                type="button"
                                onClick={() => handlePreviewKpiFormula(k.id, ed.formula, ed.name)}
                                className="px-2 py-1 rounded border text-[10px] font-semibold hover:bg-blue-500/10 shrink-0 flex items-center gap-0.5"
                                style={{ borderColor: "var(--bi-border)" }}
                                title="محاسبه و تست فرمول"
                              >
                                <Play className="w-3 h-3" /> تست
                              </button>
                            </div>
                            <div className="md:col-span-3 flex items-center gap-1">
                              <Target className="w-3.5 h-3.5 opacity-60 shrink-0" />
                              <input
                                type="number"
                                value={ed.target_value ?? ""}
                                onChange={(e) =>
                                  setKpiEdits((p) => ({ ...p, [k.id]: { ...ed, target_value: e.target.value } }))
                                }
                                placeholder="هدف (اختیاری)"
                                className="w-full px-2 py-1 rounded border bg-transparent text-[11px]"
                                style={{ borderColor: "var(--bi-border)" }}
                              />
                            </div>
                            <div className="md:col-span-2 text-[10px] truncate" style={{ color: "var(--bi-muted)" }} title={k.source}>
                              منبع: {k.source}
                            </div>
                          </div>

                          {(k.comparison_basis || (k.limitations && k.limitations.length > 0)) && (
                            <div className="text-[10px] flex flex-wrap gap-2" style={{ color: "var(--bi-muted)" }}>
                              {k.comparison_basis && <span>مبنا: {k.comparison_basis}</span>}
                              {k.limitations?.length > 0 && (
                                <span className="text-amber-600 dark:text-amber-400">محدودیت: {k.limitations.join(" | ")}</span>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {kpiSubTab === "quality" && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5 text-xs">
                  {dqKpis.map((dq: any) => (
                    <div key={dq.id} className="p-3 rounded-xl border space-y-1" style={{ borderColor: "var(--bi-border)" }}>
                      <div className="flex items-center justify-between">
                        <span className="font-bold flex items-center gap-1.5">
                          <ShieldCheck className="w-4 h-4 text-emerald-500" /> {dq.name}
                        </span>
                        <span className="font-bold text-emerald-500">{dq.formatted_value}</span>
                      </div>
                      <div dir="ltr" className="text-[10px] font-mono opacity-70 truncate">{dq.formula}</div>
                    </div>
                  ))}
                </div>
              )}

              {kpiSubTab === "rejected" && (
                <div className="space-y-2 text-xs">
                  {rejectedKpis.length === 0 ? (
                    <div className="p-4 text-center rounded-xl border" style={{ borderColor: "var(--bi-border)", color: "var(--bi-muted)" }}>
                      هیچ شاخص ردشده یا نامعتبری وجود ندارد.
                    </div>
                  ) : (
                    rejectedKpis.map((rk: any) => (
                      <div key={rk.id} className="p-3 rounded-xl border border-rose-500/30 bg-rose-500/5 space-y-1">
                        <div className="flex items-center justify-between">
                          <span className="font-bold flex items-center gap-1.5 text-rose-600 dark:text-rose-400">
                            <XCircle className="w-4 h-4" /> {rk.name}
                          </span>
                          <span className="text-[10px] font-mono opacity-70" dir="ltr">{rk.formula}</span>
                        </div>
                        <div className="text-[11px]">{rk.rejection_reason_fa}</div>
                      </div>
                    ))
                  )}
                </div>
              )}
            </div>
          )}

          {/* TAB 2: DEEP PROFILE & 22 SEMANTIC CLASSES OVERRIDES */}
          {activeTab === "profile" && (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <p className="text-xs" style={{ color: "var(--bi-muted)" }}>
                  {isFa
                    ? "برای هر ستون می‌توانید نقش معنایی (از ۲۲ کلاس)، عنوان نمایشی و هدف پیش‌فرض را اصلاح کرده و بازتحلیل کنید."
                    : "Review or override the 22 semantic field classes, aggregation policy, and field-level targets."}
                </p>
                <button
                  type="button"
                  onClick={handleSaveFieldOverrides}
                  disabled={savingOverrides}
                  className="px-3 py-1.5 rounded-lg bg-emerald-600 text-white text-xs font-semibold flex items-center gap-1"
                >
                  <Check className="w-3.5 h-3.5" />
                  {savingOverrides ? "..." : isFa ? "ذخیره و بازتحلیل" : "Save Overrides"}
                </button>
              </div>
              <div className="overflow-x-auto max-h-[380px] rounded-xl border" style={{ borderColor: "var(--bi-border)" }}>
                <table className="w-full text-[11px] text-start border-collapse">
                  <thead className="sticky top-0 z-10" style={{ background: "var(--bi-surface)" }}>
                    <tr className="border-b" style={{ borderColor: "var(--bi-border)" }}>
                      <th className="p-2">ستون</th>
                      <th className="p-2">کلاس معنایی (۲۲گانه)</th>
                      <th className="p-2">سیاست تجمیع</th>
                      <th className="p-2">اطمینان</th>
                      <th className="p-2">آمار توصیفی</th>
                      <th className="p-2">خالی % / پرت</th>
                      <th className="p-2">هدف پیش‌فرض</th>
                    </tr>
                  </thead>
                  <tbody>
                    {fieldsCls.map((f: any) => {
                      const ed = fieldEdits[f.field_name] || {};
                      return (
                        <tr key={f.field_name} className="border-b hover:bg-blue-500/5" style={{ borderColor: "var(--bi-border)" }}>
                          <td className="p-2">
                            <div className="font-bold">{f.display_name}</div>
                            <div dir="ltr" className="text-[10px] font-mono opacity-60">{f.field_name} ({f.raw_type})</div>
                          </td>
                          <td className="p-2">
                            <select
                              value={ed.semantic_class || f.semantic_class}
                              onChange={(e) =>
                                setFieldEdits((p) => ({
                                  ...p,
                                  [f.field_name]: { ...ed, semantic_class: e.target.value },
                                }))
                              }
                              className="px-2 py-1 rounded border text-[11px]"
                              style={{ background: "var(--bi-surface)", color: "var(--bi-text)", borderColor: "var(--bi-border)" }}
                            >
                              {semanticClasses.map((sc) => (
                                <option key={sc.id} value={sc.id}>
                                  {sc.name_fa} ({sc.id})
                                </option>
                              ))}
                            </select>
                          </td>
                          <td className="p-2">
                            <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${
                              f.sum_allowed ? "bg-emerald-500/15 text-emerald-600" : "bg-amber-500/15 text-amber-600"
                            }`}>
                              {f.default_aggregation} {!f.sum_allowed && "(SUM ممنوع)"}
                            </span>
                          </td>
                          <td className="p-2">
                            <span className={`font-semibold ${f.needs_user_confirmation ? "text-amber-500" : "text-emerald-500"}`}>
                              {Math.round((f.confidence || 0) * 100)}%
                            </span>
                          </td>
                          <td className="p-2 text-[10px]" style={{ color: "var(--bi-muted)" }}>
                            {f.data_type === "numeric"
                              ? `میانگین: ${f.stats?.avg ?? "—"} | میانه: ${f.stats?.median ?? "—"} | SD: ${f.stats?.stddev ?? "—"}`
                              : `یکتا: ${f.stats?.distinct_count ?? 0}${f.stats?.dominant_category ? ` | غالب: ${f.stats.dominant_category}` : ""}`}
                          </td>
                          <td className="p-2 text-[10px]">
                            {f.stats?.null_pct ?? 0}% / {f.stats?.iqr_outlier_count ?? 0}
                          </td>
                          <td className="p-2">
                            <input
                              type="number"
                              value={ed.target_value ?? ""}
                              onChange={(e) =>
                                setFieldEdits((p) => ({
                                  ...p,
                                  [f.field_name]: { ...ed, target_value: e.target.value },
                                }))
                              }
                              placeholder="—"
                              className="w-20 px-1.5 py-1 rounded border bg-transparent text-[11px]"
                              style={{ borderColor: "var(--bi-border)" }}
                            />
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* TAB 3: DASHBOARD SETTINGS & MULTI-PAGE PREVIEW */}
          {activeTab === "settings" && (
            <div className="space-y-4 text-xs">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <div>
                  <label className="block font-semibold mb-1">عنوان داشبورد</label>
                  <input
                    value={dashTitle}
                    onChange={(e) => setDashTitle(e.target.value)}
                    className="w-full px-3 py-2 rounded-lg border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  />
                </div>
                <div>
                  <label className="block font-semibold mb-1">نوع قالب داشبورد</label>
                  <select
                    value={templateMode}
                    onChange={(e) => setTemplateMode(e.target.value as any)}
                    className="w-full px-3 py-2 rounded-lg border"
                    style={{ background: "var(--bi-surface)", color: "var(--bi-text)", borderColor: "var(--bi-border)" }}
                  >
                    <option value="executive">مدیریتی (Executive)</option>
                    <option value="analytical">تحلیلی و آماری (Analytical)</option>
                    <option value="operational">عملیاتی و پایش (Operational)</option>
                  </select>
                </div>
                <div>
                  <label className="block font-semibold mb-1">سطح جزئیات</label>
                  <select
                    value={detailLevel}
                    onChange={(e) => setDetailLevel(e.target.value as any)}
                    className="w-full px-3 py-2 rounded-lg border"
                    style={{ background: "var(--bi-surface)", color: "var(--bi-text)", borderColor: "var(--bi-border)" }}
                  >
                    <option value="summary">خلاصه (تا ۳ KPI کلیدی)</option>
                    <option value="standard">استاندارد (تا ۶ KPI)</option>
                    <option value="comprehensive">جامع (تا ۹ KPI + تمام تحلیل‌ها)</option>
                  </select>
                </div>
                <div>
                  <label className="block font-semibold mb-1">تقویم روندهای زمانی</label>
                  <select
                    value={calendarMode}
                    onChange={(e) => setCalendarMode(e.target.value as any)}
                    className="w-full px-3 py-2 rounded-lg border"
                    style={{ background: "var(--bi-surface)", color: "var(--bi-text)", borderColor: "var(--bi-border)" }}
                  >
                    <option value="jalali">شمسی (هجری شمسی)</option>
                    <option value="gregorian">میلادی (Gregorian)</option>
                  </select>
                </div>
                <div>
                  <label className="block font-semibold mb-1">حداکثر صفحات داشبورد</label>
                  <input
                    type="number"
                    min={1}
                    max={7}
                    value={maxPages}
                    onChange={(e) => setMaxPages(Math.max(1, Math.min(7, Number(e.target.value) || 7)))}
                    className="w-full px-3 py-2 rounded-lg border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  />
                </div>
                <div>
                  <label className="block font-semibold mb-1">تم ظاهری</label>
                  <select
                    value={themeId}
                    onChange={(e) => setThemeId(e.target.value)}
                    className="w-full px-3 py-2 rounded-lg border"
                    style={{ background: "var(--bi-surface)", color: "var(--bi-text)", borderColor: "var(--bi-border)" }}
                  >
                    {themes.map((th) => (
                      <option key={th.id} value={th.id}>{th.name}</option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className="block font-semibold mb-1">از تاریخ (اختیاری - شمسی یا میلادی)</label>
                  <input
                    dir="ltr"
                    value={dateStart}
                    onChange={(e) => setDateStart(e.target.value)}
                    placeholder="1404/01/01 or 2025-01-01"
                    className="w-full px-3 py-2 rounded-lg border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  />
                </div>
                <div>
                  <label className="block font-semibold mb-1">تا تاریخ (اختیاری)</label>
                  <input
                    dir="ltr"
                    value={dateEnd}
                    onChange={(e) => setDateEnd(e.target.value)}
                    placeholder="1404/12/29 or 2025-12-31"
                    className="w-full px-3 py-2 rounded-lg border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  />
                </div>
              </div>

              {/* Pages Preview */}
              <div className="p-3 rounded-xl border space-y-2" style={{ borderColor: "var(--bi-border)", background: "var(--bi-surface)" }}>
                <div className="font-bold flex items-center gap-1.5">
                  <Layers className="w-4 h-4 text-violet-500" /> صفحات پیشنهادی تولیدشونده
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                  {(analysis?.recommended_pages || []).slice(0, maxPages).map((p: any, idx: number) => (
                    <div key={idx} className="p-2 rounded-lg border text-[11px]" style={{ borderColor: "var(--bi-border)" }}>
                      <div className="font-bold">{idx + 1}. {p.title}</div>
                      <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>
                        {p.widgets_count} ویجت ({(p.chart_types || []).slice(0, 5).join("، ")})
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* Footer Generate Button */}
          <div className="pt-3 border-t flex flex-wrap items-center justify-between gap-2" style={{ borderColor: "var(--bi-border)" }}>
            <span className="text-[11px] flex items-center gap-1.5" style={{ color: "var(--bi-muted)" }}>
              <CheckCircle2 className="w-4 h-4 text-emerald-500" />
              تمامی ویجت‌ها قبل از ایجاد با DuckDB و سیاست‌های RLS/CLS راستی‌آزمایی می‌شوند.
            </span>
            <button
              type="button"
              onClick={() => handleGenerate(false)}
              disabled={generating || !datasetId}
              className="px-5 py-2.5 rounded-xl text-xs font-bold text-white bg-violet-600 hover:bg-violet-700 flex items-center gap-2 disabled:opacity-60"
            >
              <Sparkles className="w-4 h-4" />
              {generating ? "در حال تولید داشبورد چندصفحه‌ای..." : "تولید داشبورد هوشمند با KPIها و تنظیمات انتخابی"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
