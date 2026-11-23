"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Award,
  BarChart3,
  BookmarkCheck,
  Calendar,
  Check,
  CheckCircle2,
  Database,
  HelpCircle,
  Layers,
  Link2,
  Loader2,
  Play,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Sliders,
  Sparkles,
  Star,
  Target,
  X,
  XCircle,
} from "lucide-react";
import { apiFetch } from "@/lib/api";
import { useBi } from "@/lib/context";
import type { KpiDefinition } from "@/types/bi";

interface Props {
  initialDatasetId?: string;
  editingDashboardId?: string;
  initialConfirmedKpis?: KpiDefinition[];
  initialSettings?: {
    title?: string;
    template_mode?: "executive" | "analytical" | "operational";
    detail_level?: "summary" | "standard" | "comprehensive";
    calendar_mode?: "jalali" | "gregorian";
    related_dataset_ids?: string[];
  };
  onClose?: () => void;
  onGenerated?: (dashboardId: string) => void;
  compact?: boolean;
}

const CATEGORY_LABELS_FA: Record<string, string> = {
  standard: "شاخص استاندارد",
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

const FORMAT_OPTIONS = [
  { value: "currency", labelFa: "مالی (ریال/ارز)", labelEn: "Currency" },
  { value: "percentage", labelFa: "درصد (%)", labelEn: "Percentage (%)" },
  { value: "number", labelFa: "عددی", labelEn: "Number" },
  { value: "count", labelFa: "تعدادی (شمارش)", labelEn: "Count" },
  { value: "duration", labelFa: "مدت زمان", labelEn: "Duration" },
];

const COMPARISON_OPTIONS = [
  { value: "none", labelFa: "بدون مقایسه", labelEn: "None" },
  { value: "previous_period", labelFa: "نسبت به دوره قبل", labelEn: "Vs Previous Period" },
  { value: "target", labelFa: "نسبت به هدف (Target)", labelEn: "Vs Target" },
];

interface KpiEditState {
  name: string;
  formula: string;
  display_format: string;
  unit: string;
  comparison_mode: string;
  target_value: string;
  priority: number;
  is_favorite: boolean;
  value?: number | null;
  formatted_value?: string;
}

export function IntelligentDashboardStudio({
  initialDatasetId,
  editingDashboardId,
  initialConfirmedKpis,
  initialSettings,
  onClose,
  onGenerated,
}: Props) {
  const { locale, themes, showToast } = useBi();
  const isFa = locale === "fa";

  const [datasets, setDatasets] = useState<any[]>([]);
  const [datasetId, setDatasetId] = useState<string>(initialDatasetId || "");
  const [relatedDatasetIds, setRelatedDatasetIds] = useState<string[]>(
    initialSettings?.related_dataset_ids || []
  );
  const [semanticClasses, setSemanticClasses] = useState<any[]>([]);
  const [analysis, setAnalysis] = useState<any | null>(null);
  const [loading, setLoading] = useState(false);
  const [savingOverrides, setSavingOverrides] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [registering, setRegistering] = useState(false);

  const [activeTab, setActiveTab] = useState<"kpis" | "profile" | "settings">("kpis");
  const [kpiSubTab, setKpiSubTab] = useState<"business" | "needs_data" | "quality" | "rejected">("business");
  const [kpiSearch, setKpiSearch] = useState("");

  // User overrides & KPI curation state
  const [fieldEdits, setFieldEdits] = useState<
    Record<string, { semantic_class?: string; target_value?: string; display_name?: string }>
  >({});
  const [selectedKpiIds, setSelectedKpiIds] = useState<Set<string>>(new Set());
  const [kpiEdits, setKpiEdits] = useState<Record<string, KpiEditState>>({});
  const [customKpis, setCustomKpis] = useState<KpiDefinition[]>([]);
  const [manualTargetsByKpi, setManualTargetsByKpi] = useState<Record<string, string>>({});

  // New custom KPI builder
  const [newKpiName, setNewKpiName] = useState("");
  const [newKpiFormula, setNewKpiFormula] = useState("");
  const [newKpiFormat, setNewKpiFormat] = useState("number");
  const [newKpiTarget, setNewKpiTarget] = useState("");
  const [previewingFormula, setPreviewingFormula] = useState(false);

  // Dashboard generation settings
  const [dashTitle, setDashTitle] = useState(initialSettings?.title || "");
  const [templateMode, setTemplateMode] = useState<"executive" | "analytical" | "operational">(
    initialSettings?.template_mode || "executive"
  );
  const [detailLevel, setDetailLevel] = useState<"summary" | "standard" | "comprehensive">(
    initialSettings?.detail_level || "standard"
  );
  const [calendarMode, setCalendarMode] = useState<"jalali" | "gregorian">(
    initialSettings?.calendar_mode || "jalali"
  );
  const [maxPages, setMaxPages] = useState<number>(7);
  const [themeId, setThemeId] = useState<string>("");
  const [dateStart, setDateStart] = useState("");
  const [dateEnd, setDateEnd] = useState("");

  useEffect(() => {
    apiFetch<any[]>("/datasets")
      .then((list) => {
        setDatasets(list || []);
        if (!initialDatasetId && !datasetId && list?.[0]?.id) {
          setDatasetId(list[0].id);
        }
      })
      .catch(() => {});
    apiFetch<any[]>("/ai/semantic-classes")
      .then((cls) => setSemanticClasses(cls || []))
      .catch(() => {});
  }, [initialDatasetId, datasetId]);

  useEffect(() => {
    if (themes.length > 0 && !themeId) {
      setThemeId(themes[0].id);
    }
  }, [themes, themeId]);

  const loadRecommendation = useCallback(
    async (dsId: string, forceRefresh = false, relIds: string[] = relatedDatasetIds, calMode: string = calendarMode) => {
      if (!dsId) return;
      setLoading(true);
      try {
        const qp = new URLSearchParams();
        if (forceRefresh) qp.set("refresh", "true");
        if (relIds.length > 0) qp.set("related_dataset_ids", relIds.join(","));
        qp.set("calendar_mode", calMode);
        const qs = qp.toString();
        const res = await apiFetch<any>(`/ai/recommend/${dsId}${qs ? `?${qs}` : ""}`);
        setAnalysis(res);

        // Merge standard computable KPIs + discovered business KPIs cleanly
        const stdAll: KpiDefinition[] = res?.kpis?.standard || [];
        const allBizRaw: KpiDefinition[] = res?.kpis?.all_business || [];
        const seen = new Set<string>();
        const mergedBiz: KpiDefinition[] = [];
        for (const k of [...stdAll.filter((s) => s.computability_status === "computable" || s.computability_status === "computable_with_warning"), ...allBizRaw]) {
          if (!seen.has(k.id)) {
            seen.add(k.id);
            mergedBiz.push(k);
          }
        }

        const editInit: Record<string, KpiEditState> = {};
        mergedBiz.forEach((k, idx) => {
          const fmt = k.display_format || k.format_type || "number";
          editInit[k.id] = {
            name: k.name,
            formula: k.formula,
            display_format: fmt,
            unit: k.unit || (fmt === "percentage" ? "%" : ""),
            comparison_mode: k.comparison_mode || "none",
            target_value: k.target_value != null ? String(k.target_value) : "",
            priority: Number(k.priority || idx + 1),
            is_favorite: Boolean(k.is_favorite),
            value: k.value,
            formatted_value: k.formatted_value,
          };
        });

        // If editing an existing dashboard, restore exact prior confirmed KPIs & any custom ones!
        if (initialConfirmedKpis && initialConfirmedKpis.length > 0) {
          const confirmedIds = new Set<string>();
          const extraCustoms: KpiDefinition[] = [];
          initialConfirmedKpis.forEach((ck, idx) => {
            confirmedIds.add(ck.id);
            const fmt = ck.display_format || ck.format_type || "number";
            if (!seen.has(ck.id)) {
              extraCustoms.push({ ...ck, category: ck.category || "custom", computability_status: "computable" });
            }
            editInit[ck.id] = {
              name: ck.name,
              formula: ck.formula,
              display_format: fmt,
              unit: ck.unit || (fmt === "percentage" ? "%" : ""),
              comparison_mode: ck.comparison_mode || (ck.target_value ? "target" : "none"),
              target_value: ck.target_value != null ? String(ck.target_value) : "",
              priority: Number(ck.priority || idx + 1),
              is_favorite: Boolean(ck.is_favorite),
              value: ck.value ?? editInit[ck.id]?.value,
              formatted_value: ck.formatted_value ?? editInit[ck.id]?.formatted_value,
            };
          });
          setCustomKpis(extraCustoms);
          setSelectedKpiIds(confirmedIds);
        } else {
          // Initialize suggested selection (user still must explicitly review & confirm before Generate!)
          const defaultSel = new Set<string>((res?.kpis?.selected || []).map((k: any) => k.id));
          if (defaultSel.size === 0 && mergedBiz.length > 0) {
            mergedBiz.slice(0, 4).forEach((k) => defaultSel.add(k.id));
          }
          setSelectedKpiIds(defaultSel);
        }

        setKpiEdits(editInit);

        const fInit: Record<string, any> = {};
        for (const f of res?.field_classifications || []) {
          fInit[f.field_name] = {
            semantic_class: f.semantic_class,
            display_name: f.display_name,
            target_value: f.target_value != null ? String(f.target_value) : "",
          };
        }
        setFieldEdits(fInit);
        if (!dashTitle && !initialSettings?.title && res?.dataset_name) {
          setDashTitle(`داشبورد هوشمند ${res.dataset_name}`);
        }
      } catch (err: any) {
        showToast(err.message || "خطا در پروفایل داده");
      } finally {
        setLoading(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [showToast]
  );

  useEffect(() => {
    if (datasetId) {
      loadRecommendation(datasetId, false, relatedDatasetIds, calendarMode);
    }
  }, [datasetId, calendarMode, relatedDatasetIds, loadRecommendation]);

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
      await loadRecommendation(datasetId, true, relatedDatasetIds, calendarMode);
    } catch (err: any) {
      showToast(err.message);
    } finally {
      setSavingOverrides(false);
    }
  };

  const handlePreviewKpiFormula = async (kpiId: string, formulaStr: string, nameStr: string, fmt?: string) => {
    if (!datasetId || !formulaStr.trim()) return;
    try {
      const res = await apiFetch<any>(`/ai/datasets/${datasetId}/kpis/preview`, {
        method: "POST",
        body: JSON.stringify({
          formula: formulaStr,
          name: nameStr,
          format_type: fmt || kpiEdits[kpiId]?.display_format || "number",
          calendar_mode: calendarMode,
          related_dataset_ids: relatedDatasetIds.length ? relatedDatasetIds : undefined,
        }),
      });
      setKpiEdits((prev) => ({
        ...prev,
        [kpiId]: { ...prev[kpiId], value: res.value, formatted_value: res.formatted_value },
      }));
      showToast(isFa ? `فرمول معتبر است · مقدار: ${res.formatted_value}` : `Valid formula · Value: ${res.formatted_value}`);
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
          calendar_mode: calendarMode,
          related_dataset_ids: relatedDatasetIds.length ? relatedDatasetIds : undefined,
        }),
      });
      const cid = `custom_${Date.now()}`;
      const unit = newKpiFormat === "percentage" ? "%" : newKpiFormat === "currency" ? "ریال" : "";
      const created: KpiDefinition = {
        id: cid,
        name: newKpiName.trim(),
        formula: newKpiFormula.trim(),
        base_fields: res.base_fields || [],
        format_type: newKpiFormat,
        display_format: newKpiFormat,
        unit,
        target_value: newKpiTarget ? Number(newKpiTarget) : undefined,
        comparison_mode: newKpiTarget ? "target" : "none",
        value: res.value,
        formatted_value: res.formatted_value,
        category: "custom",
        priority: 1,
        confidence: 0.99,
        score: 99,
        computability_status: "computable",
        computability_status_fa: "قابل محاسبه",
        source: analysis?.dataset_name,
        source_datasets: [analysis?.dataset_name].filter(Boolean),
      };
      setCustomKpis((prev) => [created, ...prev]);
      setSelectedKpiIds((prev) => new Set([...Array.from(prev), cid]));
      setKpiEdits((prev) => ({
        ...prev,
        [cid]: {
          name: created.name,
          formula: created.formula,
          display_format: newKpiFormat,
          unit,
          comparison_mode: newKpiTarget ? "target" : "none",
          target_value: newKpiTarget,
          priority: 1,
          value: res.value,
          formatted_value: res.formatted_value,
          is_favorite: true,
        },
      }));
      setNewKpiName("");
      setNewKpiFormula("");
      setNewKpiTarget("");
      showToast(isFa ? `شاخص سفارشی با مقدار ${res.formatted_value} تأیید و افزوده شد` : `Custom KPI added (${res.formatted_value})`);
    } catch (err: any) {
      showToast(err.message);
    } finally {
      setPreviewingFormula(false);
    }
  };

  const handleActivateManualTargetKpi = async (nk: KpiDefinition) => {
    const rawT = manualTargetsByKpi[nk.id];
    const numT = Number(rawT);
    if (!rawT || !Number.isFinite(numT) || numT <= 0) {
      showToast(isFa ? "لطفاً یک مقدار هدف مثبت وارد کنید" : "Please enter a valid positive target value");
      return;
    }
    const baseField = nk.base_fields?.[0] || analysis?.field_classifications?.find((f: any) => f.data_type === "numeric" && f.sum_allowed)?.field_name;
    if (!baseField) {
      showToast(isFa ? "ستون عددی پایه برای تحقق هدف یافت نشد" : "No numeric base field found");
      return;
    }
    const formula = `COALESCE(SUM("${baseField}"), 0) * 100.0 / NULLIF(${numT}, 0)`;
    try {
      const prev = await apiFetch<any>(`/ai/datasets/${datasetId}/kpis/preview`, {
        method: "POST",
        body: JSON.stringify({ name: nk.name, formula, format_type: "percentage", unit: "%", target_value: numT }),
      });
      const promoted: KpiDefinition = {
        ...nk,
        formula,
        base_fields: [baseField],
        target_value: numT,
        comparison_mode: "target",
        display_format: "percentage",
        format_type: "percentage",
        unit: "%",
        value: prev.value,
        formatted_value: prev.formatted_value,
        computability_status: "computable_with_warning",
        computability_status_fa: "قابل محاسبه (هدف دستی)",
        warnings_fa: [`مقدار هدف دستی (${numT.toLocaleString()}) ثبت شد.`],
      };
      setCustomKpis((p) => [promoted, ...p.filter((x) => x.id !== nk.id)]);
      setKpiEdits((p) => ({
        ...p,
        [nk.id]: {
          name: promoted.name,
          formula,
          display_format: "percentage",
          unit: "%",
          comparison_mode: "target",
          target_value: String(numT),
          priority: nk.priority || 6,
          is_favorite: false,
          value: prev.value,
          formatted_value: prev.formatted_value,
        },
      }));
      setSelectedKpiIds((p) => new Set([...Array.from(p), nk.id]));
      setKpiSubTab("business");
      showToast(isFa ? `شاخص «${nk.name}» با هدف دستی فعال و انتخاب شد (${prev.formatted_value})` : `Activated "${nk.name}"`);
    } catch (err: any) {
      showToast(err.message);
    }
  };

  // All computable business + standard KPIs + user custom KPIs
  const allBusinessKpis = useMemo(() => {
    const stdAll: KpiDefinition[] = analysis?.kpis?.standard || [];
    const base: KpiDefinition[] = analysis?.kpis?.all_business || [];
    const merged: KpiDefinition[] = [];
    const seen = new Set<string>();

    for (const k of [
      ...customKpis,
      ...stdAll.filter((s) => s.computability_status === "computable" || s.computability_status === "computable_with_warning"),
      ...base,
    ]) {
      if (!seen.has(k.id)) {
        seen.add(k.id);
        merged.push(k);
      }
    }
    return merged;
  }, [analysis, customKpis]);

  const filteredBusinessKpis = useMemo(() => {
    const q = kpiSearch.trim().toLowerCase();
    if (!q) return allBusinessKpis;
    return allBusinessKpis.filter(
      (k) =>
        k.name.toLowerCase().includes(q) ||
        k.formula.toLowerCase().includes(q) ||
        (k.source || "").toLowerCase().includes(q)
    );
  }, [allBusinessKpis, kpiSearch]);

  const needsDataKpis: KpiDefinition[] = useMemo(() => {
    const customIds = new Set(customKpis.map((c) => c.id));
    const list: KpiDefinition[] = analysis?.kpis?.needs_data || [];
    return list.filter((k) => !customIds.has(k.id));
  }, [analysis, customKpis]);

  const dqKpis: KpiDefinition[] = analysis?.kpis?.data_quality || [];
  const rejectedKpis: KpiDefinition[] = analysis?.kpis?.uncomputable || [];

  // Build final confirmed KPIs payload strictly from checked computable KPIs
  const confirmedPayloadList = useMemo(() => {
    const byId = new Map<string, KpiDefinition>(allBusinessKpis.map((k) => [k.id, k]));
    const items: any[] = [];
    selectedKpiIds.forEach((id) => {
      const k = byId.get(id);
      if (!k) return;
      const ed = kpiEdits[id];
      const fmt = ed?.display_format || k.display_format || k.format_type || "number";
      items.push({
        id: k.id,
        slug: k.slug || k.id,
        name: ed?.name || k.name,
        formula: ed?.formula || k.formula,
        base_fields: k.base_fields || k.required_fields || [],
        display_format: fmt,
        format_type: fmt,
        unit: ed?.unit ?? k.unit ?? (fmt === "percentage" ? "%" : ""),
        priority: Number(ed?.priority ?? k.priority ?? items.length + 1),
        comparison_mode: ed?.comparison_mode || k.comparison_mode || "none",
        target_value: ed?.target_value ? Number(ed.target_value) : k.target_value ?? null,
        is_standard: Boolean(k.is_standard),
        category: k.category || "custom",
        multi_source_agg: k.multi_source_agg || undefined,
        pre_agg_grain: k.pre_agg_grain || undefined,
        time_field: k.time_field || undefined,
        source_datasets: k.source_datasets || [analysis?.dataset_name].filter(Boolean),
        is_confirmed: true,
      });
    });
    return items.sort((a, b) => a.priority - b.priority);
  }, [allBusinessKpis, selectedKpiIds, kpiEdits, analysis?.dataset_name]);

  const handleRegisterSelectedSemantic = async () => {
    if (!datasetId || confirmedPayloadList.length === 0) return;
    setRegistering(true);
    try {
      const chosen = confirmedPayloadList.map((k) => ({
        ...k,
        is_favorite: kpiEdits[k.id]?.is_favorite ?? false,
      }));
      const res = await apiFetch<any>(`/ai/datasets/${datasetId}/kpis/register`, {
        method: "POST",
        body: JSON.stringify({
          kpis: chosen,
          related_dataset_ids: relatedDatasetIds.length ? relatedDatasetIds : undefined,
        }),
      });
      showToast(
        isFa ? `${res.count} شاخص با موفقیت در Semantic Layer ثبت شد` : `${res.count} KPIs registered in Semantic Layer`
      );
    } catch (err: any) {
      showToast(err.message);
    } finally {
      setRegistering(false);
    }
  };

  const handleGenerateConfirmed = async () => {
    if (!datasetId) return;
    if (confirmedPayloadList.length === 0) {
      showToast(
        isFa
          ? "حداقل یک KPI معتبر را انتخاب و تأیید کنید پیش از تولید داشبورد."
          : "Please select and confirm at least one valid KPI first."
      );
      return;
    }
    setGenerating(true);
    try {
      const targetValues: Record<string, number> = {};
      for (const ck of confirmedPayloadList) {
        if (ck.target_value != null && !Number.isNaN(Number(ck.target_value))) {
          targetValues[ck.id] = Number(ck.target_value);
        }
      }
      const payload: any = {
        dataset_id: datasetId,
        dashboard_id: editingDashboardId || undefined,
        title: dashTitle || undefined,
        template_mode: templateMode,
        detail_level: detailLevel,
        calendar_mode: calendarMode,
        max_pages: maxPages,
        theme_id: themeId || undefined,
        confirmed_kpis: confirmedPayloadList,
        selected_kpi_ids: confirmedPayloadList.map((c) => c.id),
        target_values: Object.keys(targetValues).length > 0 ? targetValues : undefined,
        related_dataset_ids: relatedDatasetIds.length ? relatedDatasetIds : undefined,
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
        editingDashboardId
          ? isFa
            ? `KPIهای داشبورد با موفقیت به‌روزرسانی شد (${confirmedPayloadList.length} شاخص)!`
            : `Dashboard KPIs updated (${confirmedPayloadList.length} KPIs)!`
          : isFa
          ? `داشبورد با ${confirmedPayloadList.length} KPI تأییدشده (${res.pages_count || 1} صفحه) ساخته شد!`
          : `Dashboard built with ${confirmedPayloadList.length} confirmed KPIs!`
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

  const domain = analysis?.domain_detection;
  const deepProf = analysis?.deep_profile;
  const fieldsCls = analysis?.field_classifications || [];
  const otherDatasets = datasets.filter((d) => d.id !== datasetId);

  return (
    <div dir={isFa ? "rtl" : "ltr"} className="bi-card p-4 sm:p-5 space-y-4 border-2 border-violet-500/35">
      {/* Header + 3-Step Workflow Indicator */}
      <div className="flex flex-wrap items-start justify-between gap-3 pb-3 border-b" style={{ borderColor: "var(--bi-border)" }}>
        <div className="space-y-1.5">
          <div className="flex items-center gap-2.5">
            <div className="w-9 h-9 rounded-xl bg-violet-500/15 text-violet-500 flex items-center justify-center shrink-0">
              <Sparkles className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-sm sm:text-base font-bold">
                {editingDashboardId
                  ? isFa
                    ? "ویرایش، انتخاب و تأیید مجدد KPIهای داشبورد"
                    : "Edit & Re-Confirm Dashboard KPIs"
                  : isFa
                  ? "انتخاب و تأیید KPIها قبل از تولید داشبورد"
                  : "KPI Selection & Confirmation"}
              </h2>
              <p className="text-[11px]" style={{ color: "var(--bi-muted)" }}>
                {isFa
                  ? "مرحله اجباری پیش از ساخت داشبورد: بررسی فرمول، مبنای محاسبه، هدف، فرمت نمایش و تأیید نهایی شاخص‌ها"
                  : "Mandatory confirmation step before dashboard generation: verify formulas, targets, display formats & priorities"}
              </p>
            </div>
          </div>

          {/* 3-Step Progress Stepper */}
          <div className="flex flex-wrap items-center gap-1.5 text-[11px] pt-1">
            <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 font-semibold">
              <CheckCircle2 className="w-3 h-3" /> ۱. تحلیل منبع داده
            </span>
            <span className="opacity-40">←</span>
            <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full bg-violet-600 text-white font-bold shadow-sm">
              ۲. انتخاب و تأیید KPIها ({confirmedPayloadList.length})
            </span>
            <span className="opacity-40">←</span>
            <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full border opacity-75" style={{ borderColor: "var(--bi-border)" }}>
              ۳. تولید داشبورد چندصفحه‌ای
            </span>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <select
            aria-label={isFa ? "انتخاب منبع داده" : "Select Dataset"}
            value={datasetId}
            disabled={Boolean(editingDashboardId)}
            onChange={(e) => {
              setDatasetId(e.target.value);
              setRelatedDatasetIds([]);
              setCustomKpis([]);
            }}
            className="px-3 py-1.5 rounded-lg border text-xs font-medium min-w-[180px] disabled:opacity-70"
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
            onClick={() => setCalendarMode((m) => (m === "jalali" ? "gregorian" : "jalali"))}
            className="px-2.5 py-1.5 rounded-lg border text-xs flex items-center gap-1 hover:bg-violet-500/10"
            style={{ borderColor: "var(--bi-border)" }}
            title={isFa ? "تغییر تقویم مبنای رشد ماهانه و روندها" : "Toggle calendar alignment"}
          >
            <Calendar className="w-3.5 h-3.5 text-violet-500" />
            {calendarMode === "jalali" ? "تقویم شمسی" : "تقویم میلادی"}
          </button>

          <button
            type="button"
            onClick={() => loadRecommendation(datasetId, true, relatedDatasetIds, calendarMode)}
            disabled={loading || !datasetId}
            className="px-2.5 py-1.5 rounded-lg border text-xs flex items-center gap-1 hover:bg-blue-500/10"
            style={{ borderColor: "var(--bi-border)" }}
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
            {isFa ? "تحلیل مجدد" : "Re-Analyze"}
          </button>

          {onClose && (
            <button type="button" onClick={onClose} aria-label="Close" className="p-1.5 rounded-lg hover:bg-red-500/10 text-red-500">
              <X className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>

      {/* Multi-Source Join Selector (optional partner datasets) */}
      {otherDatasets.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 text-[11px] px-3 py-2 rounded-xl border" style={{ borderColor: "var(--bi-border)", background: "var(--bi-surface)" }}>
          <span className="font-semibold flex items-center gap-1 opacity-80">
            <Link2 className="w-3.5 h-3.5 text-blue-500" />
            {isFa ? "اتصال منابع مرتبط (جلوگیری از Fan-out):" : "Related Sources (Key-grain pre-agg):"}
          </span>
          {otherDatasets.slice(0, 6).map((od) => {
            const active = relatedDatasetIds.includes(od.id);
            return (
              <button
                key={od.id}
                type="button"
                onClick={() =>
                  setRelatedDatasetIds((prev) =>
                    prev.includes(od.id) ? prev.filter((x) => x !== od.id) : [...prev, od.id]
                  )
                }
                className={`px-2 py-0.5 rounded-full border text-[10px] font-semibold transition ${
                  active ? "bg-blue-600 text-white border-blue-600" : "hover:bg-blue-500/10"
                }`}
                style={{ borderColor: active ? undefined : "var(--bi-border)" }}
              >
                {active ? "✓ " : "+ "}
                {od.name}
              </button>
            );
          })}
        </div>
      )}

      {loading && (
        <div className="py-10 flex items-center justify-center gap-2 text-xs font-semibold" style={{ color: "var(--bi-muted)" }}>
          <Loader2 className="w-4 h-4 animate-spin text-violet-500" />
          {isFa ? "در حال تحلیل ساختار داده و ارزیابی ۷ KPI استاندارد و شاخص‌های حوزه..." : "Analyzing schema & evaluating KPIs..."}
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
              <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>ابعاد منبع داده</div>
              <div className="font-bold mt-0.5">{(deepProf?.row_count || 0).toLocaleString()} × {deepProf?.field_count || 0}</div>
              <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>{deepProf?.is_approximate ? "نمونه‌برداری" : "پروفایل کامل"}</div>
            </div>
            <div className="p-2.5 rounded-lg border" style={{ borderColor: "var(--bi-border)", background: "var(--bi-surface)" }}>
              <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>کیفیت داده</div>
              <div className="font-bold mt-0.5 text-emerald-500">{deepProf?.completeness_pct}% تکمیل</div>
              <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>تکراری: {deepProf?.duplicate_row_count || 0}</div>
            </div>
            <div className="p-2.5 rounded-lg border" style={{ borderColor: "var(--bi-border)", background: "var(--bi-surface)" }}>
              <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>KPIهای قابل محاسبه / نیازمند داده</div>
              <div className="font-bold mt-0.5">
                {confirmedPayloadList.length} تأییدشده / {allBusinessKpis.length} آماده
              </div>
              <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>
                نیازمند داده: {needsDataKpis.length} · نامعتبر: {rejectedKpis.length}
              </div>
            </div>
            <div className="p-2.5 rounded-lg border" style={{ borderColor: "var(--bi-border)", background: "var(--bi-surface)" }}>
              <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>صفحات خروجی داشبورد</div>
              <div className="font-bold mt-0.5">{Math.min(maxPages, (analysis?.recommended_pages || []).length)} صفحه تحلیلی</div>
              <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>+ صفحه Drill-Through جزئیات</div>
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
              {isFa ? `انتخاب و تأیید KPIها (${confirmedPayloadList.length})` : `Select & Confirm KPIs (${confirmedPayloadList.length})`}
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
              {isFa ? `ستون‌ها و نقش معنایی (${fieldsCls.length})` : `Schema & Semantic Classes (${fieldsCls.length})`}
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

          {/* TAB 1: KPI SELECTION, FORMULA EDITING & CONFIRMATION */}
          {activeTab === "kpis" && (
            <div className="space-y-4">
              {/* Sub-tabs + Semantic Register */}
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap gap-1.5 text-xs">
                  <button
                    type="button"
                    onClick={() => setKpiSubTab("business")}
                    className={`px-2.5 py-1 rounded-md font-semibold ${kpiSubTab === "business" ? "bg-blue-600 text-white" : "border"}`}
                    style={{ borderColor: "var(--bi-border)" }}
                  >
                    KPIهای قابل محاسبه ({allBusinessKpis.length})
                  </button>
                  <button
                    type="button"
                    onClick={() => setKpiSubTab("needs_data")}
                    className={`px-2.5 py-1 rounded-md font-semibold ${kpiSubTab === "needs_data" ? "bg-amber-600 text-white" : "border"}`}
                    style={{ borderColor: "var(--bi-border)" }}
                  >
                    نیازمند تکمیل داده ({needsDataKpis.length})
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
                    غیرقابل محاسبه ({rejectedKpis.length})
                  </button>
                </div>

                <button
                  type="button"
                  onClick={handleRegisterSelectedSemantic}
                  disabled={registering || confirmedPayloadList.length === 0}
                  className="px-3 py-1.5 rounded-lg border border-emerald-500/40 text-emerald-600 dark:text-emerald-400 text-xs font-semibold flex items-center gap-1 hover:bg-emerald-500/10 disabled:opacity-50"
                >
                  <BookmarkCheck className="w-3.5 h-3.5" />
                  {registering ? "..." : isFa ? "ذخیره KPIهای منتخب در Semantic Layer" : "Save Selected to Semantic Layer"}
                </button>
              </div>

              {kpiSubTab === "business" && (
                <div className="space-y-3">
                  {/* Search & Bulk Toggle Row */}
                  <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                    <div className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border flex-1 min-w-[200px] max-w-sm" style={{ borderColor: "var(--bi-border)" }}>
                      <Search className="w-3.5 h-3.5 opacity-60 shrink-0" />
                      <input
                        value={kpiSearch}
                        onChange={(e) => setKpiSearch(e.target.value)}
                        placeholder={isFa ? "جستجوی KPI یا نام ستون..." : "Filter KPIs..."}
                        className="bg-transparent outline-none flex-1 min-w-0"
                      />
                    </div>
                    <div className="flex items-center gap-1.5">
                      <button
                        type="button"
                        onClick={() => setSelectedKpiIds(new Set(allBusinessKpis.slice(0, 7).map((k) => k.id)))}
                        className="px-2.5 py-1 rounded-md border hover:bg-violet-500/10"
                        style={{ borderColor: "var(--bi-border)" }}
                      >
                        انتخاب شاخص‌های کلیدی
                      </button>
                      <button
                        type="button"
                        onClick={() => setSelectedKpiIds(new Set())}
                        className="px-2.5 py-1 rounded-md border hover:bg-rose-500/10 text-rose-500"
                        style={{ borderColor: "var(--bi-border)" }}
                      >
                        پاک کردن انتخاب
                      </button>
                    </div>
                  </div>

                  {/* Add Custom KPI Row */}
                  <div className="p-3 rounded-xl border border-dashed space-y-2 text-xs" style={{ borderColor: "var(--bi-border)" }}>
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-bold text-[11px] flex items-center gap-1">
                        <Plus className="w-3.5 h-3.5 text-violet-500" /> افزودن KPI سفارشی جدید (با تست زنده قبل از تأیید)
                      </span>
                      <div className="flex flex-wrap gap-1 text-[10px]">
                        <button
                          type="button"
                          onClick={() => {
                            const numF = fieldsCls.find((f: any) => f.data_type === "numeric" && f.sum_allowed)?.field_name || "revenue";
                            const ordF = fieldsCls.find((f: any) => /order|invoice|tx/.test(f.field_name.toLowerCase()))?.field_name || "order_id";
                            setNewKpiFormula(`AOV("${numF}", "${ordF}")`);
                            setNewKpiFormat("currency");
                            if (!newKpiName) setNewKpiName("میانگین ارزش سفارش");
                          }}
                          className="px-2 py-0.5 rounded bg-blue-500/10 text-blue-600 dark:text-blue-400 hover:bg-blue-500/20"
                        >
                          + الگوی AOV دومرحله‌ای
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            const numF = fieldsCls.find((f: any) => f.data_type === "numeric" && f.sum_allowed)?.field_name || "revenue";
                            const dtF = fieldsCls.find((f: any) => f.data_type === "date" || f.semantic_class === "Date/Time")?.field_name || "order_date";
                            setNewKpiFormula(`MOM_CALENDAR("${numF}", "${dtF}")`);
                            setNewKpiFormat("percentage");
                            if (!newKpiName) setNewKpiName("رشد ماهانه تقویمی");
                          }}
                          className="px-2 py-0.5 rounded bg-violet-500/10 text-violet-600 dark:text-violet-400 hover:bg-violet-500/20"
                        >
                          + الگوی رشد ماهانه
                        </button>
                      </div>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-12 gap-2">
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
                        placeholder='SUM("revenue") / NULLIF(SUM("cost"), 0)'
                        className="md:col-span-4 px-2.5 py-1.5 rounded-lg border bg-transparent font-mono text-[11px]"
                        style={{ borderColor: "var(--bi-border)" }}
                      />
                      <select
                        value={newKpiFormat}
                        onChange={(e) => setNewKpiFormat(e.target.value)}
                        className="md:col-span-2 px-2 py-1.5 rounded-lg border text-xs"
                        style={{ background: "var(--bi-surface)", color: "var(--bi-text)", borderColor: "var(--bi-border)" }}
                      >
                        {FORMAT_OPTIONS.map((fo) => (
                          <option key={fo.value} value={fo.value}>
                            {isFa ? fo.labelFa : fo.labelEn}
                          </option>
                        ))}
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
                        <Plus className="w-3.5 h-3.5" /> تست و افزودن
                      </button>
                    </div>
                  </div>

                  {/* Business & Standard KPI Cards */}
                  <div className="space-y-2.5 max-h-[420px] overflow-y-auto pr-1">
                    {filteredBusinessKpis.map((k) => {
                      const isChecked = selectedKpiIds.has(k.id);
                      const ed: KpiEditState = kpiEdits[k.id] || {
                        name: k.name,
                        formula: k.formula,
                        display_format: k.display_format || k.format_type || "number",
                        unit: k.unit || "",
                        comparison_mode: k.comparison_mode || "none",
                        target_value: k.target_value != null ? String(k.target_value) : "",
                        priority: k.priority || 10,
                        is_favorite: Boolean(k.is_favorite),
                        formatted_value: k.formatted_value,
                      };
                      const hasWarn = k.computability_status === "computable_with_warning" || (k.warnings_fa && k.warnings_fa.length > 0);
                      return (
                        <div
                          key={k.id}
                          className={`p-3 rounded-xl border text-xs space-y-2 transition ${
                            isChecked ? "border-violet-500/60 bg-violet-500/5" : ""
                          }`}
                          style={{ borderColor: isChecked ? undefined : "var(--bi-border)" }}
                        >
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <div className="flex items-center gap-2 flex-1 min-w-[210px]">
                              <input
                                type="checkbox"
                                checked={isChecked}
                                aria-label={`Select KPI ${ed.name}`}
                                onChange={() => {
                                  const next = new Set(selectedKpiIds);
                                  if (next.has(k.id)) next.delete(k.id);
                                  else next.add(k.id);
                                  setSelectedKpiIds(next);
                                }}
                                className="w-4 h-4 accent-violet-600 cursor-pointer"
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
                              {k.is_standard && (
                                <span className="px-2 py-0.5 rounded bg-indigo-500/15 text-indigo-600 dark:text-indigo-300 font-bold text-[10px]">
                                  KPI استاندارد
                                </span>
                              )}
                              <span className="px-2 py-0.5 rounded bg-blue-500/10 text-blue-500 font-semibold text-[10px]">
                                {CATEGORY_LABELS_FA[k.category || ""] || k.category}
                              </span>
                              <span
                                className={`px-2 py-0.5 rounded font-bold text-[11px] ${
                                  hasWarn
                                    ? "bg-amber-500/15 text-amber-600 dark:text-amber-300"
                                    : "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                                }`}
                              >
                                {ed.formatted_value || k.formatted_value || "—"}
                              </span>
                              {k.computability_status_fa && (
                                <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${
                                  hasWarn ? "bg-amber-500/15 text-amber-600" : "bg-emerald-500/10 text-emerald-600"
                                }`}>
                                  {k.computability_status_fa}
                                </span>
                              )}
                            </div>
                          </div>

                          {/* Formula + Format + Comparison + Target + Priority */}
                          <div className="grid grid-cols-1 md:grid-cols-12 gap-2 items-center">
                            <div className="md:col-span-5 flex items-center gap-1">
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
                                onClick={() => handlePreviewKpiFormula(k.id, ed.formula, ed.name, ed.display_format)}
                                className="px-2 py-1 rounded border text-[10px] font-semibold hover:bg-blue-500/10 shrink-0 flex items-center gap-0.5"
                                style={{ borderColor: "var(--bi-border)" }}
                                title="تست زنده فرمول"
                              >
                                <Play className="w-3 h-3" /> تست
                              </button>
                            </div>
                            <div className="md:col-span-2">
                              <select
                                aria-label="Display format"
                                value={ed.display_format}
                                onChange={(e) =>
                                  setKpiEdits((p) => ({ ...p, [k.id]: { ...ed, display_format: e.target.value } }))
                                }
                                className="w-full px-1.5 py-1 rounded border text-[11px]"
                                style={{ background: "var(--bi-surface)", color: "var(--bi-text)", borderColor: "var(--bi-border)" }}
                              >
                                {FORMAT_OPTIONS.map((fo) => (
                                  <option key={fo.value} value={fo.value}>
                                    {isFa ? fo.labelFa : fo.labelEn}
                                  </option>
                                ))}
                              </select>
                            </div>
                            <div className="md:col-span-2">
                              <select
                                aria-label="Comparison mode"
                                value={ed.comparison_mode}
                                onChange={(e) =>
                                  setKpiEdits((p) => ({ ...p, [k.id]: { ...ed, comparison_mode: e.target.value } }))
                                }
                                className="w-full px-1.5 py-1 rounded border text-[11px]"
                                style={{ background: "var(--bi-surface)", color: "var(--bi-text)", borderColor: "var(--bi-border)" }}
                              >
                                {COMPARISON_OPTIONS.map((co) => (
                                  <option key={co.value} value={co.value}>
                                    {isFa ? co.labelFa : co.labelEn}
                                  </option>
                                ))}
                              </select>
                            </div>
                            <div className="md:col-span-2 flex items-center gap-1">
                              <Target className="w-3.5 h-3.5 opacity-60 shrink-0" />
                              <input
                                type="number"
                                value={ed.target_value ?? ""}
                                onChange={(e) =>
                                  setKpiEdits((p) => ({
                                    ...p,
                                    [k.id]: {
                                      ...ed,
                                      target_value: e.target.value,
                                      comparison_mode: e.target.value ? "target" : ed.comparison_mode,
                                    },
                                  }))
                                }
                                placeholder="هدف (Target)"
                                className="w-full px-2 py-1 rounded border bg-transparent text-[11px]"
                                style={{ borderColor: "var(--bi-border)" }}
                              />
                            </div>
                            <div className="md:col-span-1 flex items-center gap-1" title="اولویت نمایش (۱ = بالاترین)">
                              <span className="text-[10px] opacity-60">#</span>
                              <input
                                type="number"
                                min={1}
                                max={99}
                                value={ed.priority}
                                onChange={(e) =>
                                  setKpiEdits((p) => ({
                                    ...p,
                                    [k.id]: { ...ed, priority: Math.max(1, Number(e.target.value) || 1) },
                                  }))
                                }
                                className="w-full px-1.5 py-1 rounded border bg-transparent text-[11px] text-center"
                                style={{ borderColor: "var(--bi-border)" }}
                              />
                            </div>
                          </div>

                          {/* Explanations, Source, Eligible/Delayed rules & Warnings */}
                          <div className="text-[10px] flex flex-wrap items-center gap-x-3 gap-y-1" style={{ color: "var(--bi-muted)" }}>
                            <span>منبع: <b>{k.source || analysis?.dataset_name}</b></span>
                            {k.comparison_basis && <span>مبنا: {k.comparison_basis}</span>}
                            {k.eligible_definition_fa && (
                              <span className="text-blue-500">واجد شرایط: {k.eligible_definition_fa}</span>
                            )}
                            {k.delayed_definition_fa && (
                              <span className="text-indigo-500">شرط تأخیر: {k.delayed_definition_fa}</span>
                            )}
                            {(k.warnings_fa || k.limitations || []).length > 0 && (
                              <span className="text-amber-600 dark:text-amber-400 font-medium">
                                ⚠ {(k.warnings_fa || k.limitations || []).join(" | ")}
                              </span>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* NEEDS DATA SUB-TAB */}
              {kpiSubTab === "needs_data" && (
                <div className="space-y-2.5 text-xs">
                  {needsDataKpis.length === 0 ? (
                    <div className="p-4 text-center rounded-xl border" style={{ borderColor: "var(--bi-border)", color: "var(--bi-muted)" }}>
                      تمام شاخص‌های استاندارد برای این منبع داده کامل هستند.
                    </div>
                  ) : (
                    needsDataKpis.map((nk) => (
                      <div key={nk.id} className="p-3 rounded-xl border border-amber-500/40 bg-amber-500/5 space-y-2">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <span className="font-bold flex items-center gap-1.5 text-amber-600 dark:text-amber-400">
                            <HelpCircle className="w-4 h-4" /> {nk.name}
                          </span>
                          <span className="px-2 py-0.5 rounded bg-amber-500/15 text-amber-600 text-[10px] font-bold">
                            نیازمند تکمیل داده (در داشبورد تولید نمی‌شود تا تکمیل شود)
                          </span>
                        </div>
                        <div className="text-[11px]">{nk.rejection_reason_fa || nk.description}</div>
                        {nk.missing_fields_fa && nk.missing_fields_fa.length > 0 && (
                          <div className="text-[11px] font-semibold">
                            فیلدهای مورد نیاز: {nk.missing_fields_fa.join("، ")}
                          </div>
                        )}
                        {nk.id === "std_target_achievement" && (
                          <div className="flex flex-wrap items-center gap-2 pt-1">
                            <input
                              type="number"
                              value={manualTargetsByKpi[nk.id] || ""}
                              onChange={(e) =>
                                setManualTargetsByKpi((p) => ({ ...p, [nk.id]: e.target.value }))
                              }
                              placeholder="مقدار هدف دستی کل دوره را وارد کنید (مثلاً 1000000)"
                              className="px-2.5 py-1.5 rounded-lg border bg-transparent text-xs min-w-[260px]"
                              style={{ borderColor: "var(--bi-border)" }}
                            />
                            <button
                              type="button"
                              onClick={() => handleActivateManualTargetKpi(nk)}
                              className="px-3 py-1.5 rounded-lg bg-amber-600 hover:bg-amber-700 text-white font-semibold"
                            >
                              ثبت هدف دستی و فعال‌سازی این KPI
                            </button>
                          </div>
                        )}
                      </div>
                    ))
                  )}
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
                        <div className="text-[11px]">{rk.rejection_reason_fa || rk.errors_fa?.join(" | ")}</div>
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
                  <Layers className="w-4 h-4 text-violet-500" /> صفحات خروجی داشبورد
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

          {/* Pre-Generation Confirmed KPIs Summary & Strict Confirmation CTA */}
          <div className="pt-3 border-t space-y-2.5" style={{ borderColor: "var(--bi-border)" }}>
            <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
              <span className="font-bold flex items-center gap-1 text-violet-600 dark:text-violet-300">
                <CheckCircle2 className="w-3.5 h-3.5" />
                {isFa
                  ? `خلاصه KPIهای تأییدشده برای ساخت داشبورد (${confirmedPayloadList.length}):`
                  : `Confirmed KPIs Summary (${confirmedPayloadList.length}):`}
              </span>
              {confirmedPayloadList.length === 0 ? (
                <span className="text-rose-500 font-semibold">
                  {isFa
                    ? "هیچ KPI انتخاب نشده است — لطفاً حداقل یک KPI معتبر انتخاب کنید."
                    : "No KPIs selected — select at least 1 valid KPI to proceed."}
                </span>
              ) : (
                confirmedPayloadList.map((ck) => (
                  <span
                    key={ck.id}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-violet-500/15 text-violet-700 dark:text-violet-200 font-medium"
                  >
                    <span className="opacity-70">#{ck.priority}</span> {ck.name}
                    <span className="opacity-65">({ck.display_format})</span>
                  </span>
                ))
              )}
            </div>

            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-[11px] flex items-center gap-1.5" style={{ color: "var(--bi-muted)" }}>
                <ShieldCheck className="w-4 h-4 text-emerald-500 shrink-0" />
                {isFa
                  ? "تنها KPIهای تأییدشده در بالا روی داشبورد ساخته می‌شوند (همراه با Drill-Down و RBAC/RLS/CLS)."
                  : "Only explicitly confirmed KPIs above are generated into the dashboard with Drill-Down & RBAC/RLS/CLS."}
              </span>
              <button
                type="button"
                onClick={handleGenerateConfirmed}
                disabled={generating || !datasetId || confirmedPayloadList.length === 0}
                className="px-5 py-2.5 rounded-xl text-xs font-bold text-white bg-violet-600 hover:bg-violet-700 flex items-center gap-2 disabled:opacity-50"
              >
                <Sparkles className="w-4 h-4" />
                {generating
                  ? isFa
                    ? "در حال ساخت داشبورد با KPIهای تأییدشده..."
                    : "Building Dashboard..."
                  : editingDashboardId
                  ? isFa
                    ? `تأیید و بازساخت داشبورد (${confirmedPayloadList.length} KPI)`
                    : `Confirm & Rebuild Dashboard (${confirmedPayloadList.length} KPIs)`
                  : isFa
                  ? `تأیید KPIها و تولید داشبورد (${confirmedPayloadList.length} KPI)`
                  : `Confirm KPIs & Generate Dashboard (${confirmedPayloadList.length} KPIs)`}
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
