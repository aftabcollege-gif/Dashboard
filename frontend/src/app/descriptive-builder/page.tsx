"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  Bookmark,
  Calendar,
  CheckCircle2,
  Database,
  Filter,
  FolderOpen,
  Layers,
  Lock,
  Palette,
  Play,
  Plus,
  Rocket,
  Save,
  Search,
  Sliders,
  Sparkles,
  Trash2,
  Upload,
} from "lucide-react";
import AppShell from "@/components/layout/AppShell";
import { DescriptivePreviewPanel } from "@/components/descriptive-builder/DescriptivePreviewPanel";
import { FormulaStudioPanel } from "@/components/descriptive-builder/FormulaStudioPanel";
import { RelationshipGraphPanel } from "@/components/descriptive-builder/RelationshipGraphPanel";
import { apiFetch } from "@/lib/api";
import { useBi } from "@/lib/context";
import type {
  AccessibleDatasetMeta,
  AnalysisCatalogItem,
  BuilderCatalogResponse,
  DataModelRecord,
  DescriptiveFilterDraft,
  DescriptiveWidgetDraft,
  FormulaFunctionMeta,
  GenerateDashboardResponse,
  RelationshipAnalyzeResponse,
} from "@/types/descriptiveBuilder";

const WIZARD_STEPS = [
  { num: 1, key: "sources", fa: "۱. منابع داده", en: "1. Datasets" },
  { num: 2, key: "relations", fa: "۲. مدل روابط", en: "2. Relationships" },
  { num: 3, key: "fields", fa: "۳. فیلدها و اهداف", en: "3. Fields & Targets" },
  { num: 4, key: "formulas", fa: "۴. فرمول و شاخص", en: "4. Formulas & Metrics" },
  { num: 5, key: "analyses", fa: "۵. نوع تحلیل‌ها", en: "5. Analyses (15)" },
  { num: 6, key: "calendar", fa: "۶. تقویم و فیلترها", en: "6. Calendar & Filters" },
  { num: 7, key: "preview", fa: "۷. تولید و پیش‌نمایش", en: "7. Generate & Preview" },
  { num: 8, key: "customize", fa: "۸. چیدمان و تم", en: "8. Layout & Theme" },
  { num: 9, key: "publish", fa: "۹. ذخیره و انتشار", en: "9. Save & Publish" },
];

export default function DescriptiveBuilderPage() {
  const { user, locale, showToast } = useBi();
  const isFa = locale === "fa";

  const [mode, setMode] = useState<"simple" | "advanced">("advanced");
  const [step, setStep] = useState<number>(1);
  const [loadingInit, setLoadingInit] = useState(true);

  // Catalogs & Accessible Metadata
  const [functions, setFunctions] = useState<FormulaFunctionMeta[]>([]);
  const [analyses, setAnalyses] = useState<AnalysisCatalogItem[]>([]);
  const [datasets, setDatasets] = useState<AccessibleDatasetMeta[]>([]);
  const [themes, setThemes] = useState<any[]>([]);
  const [savedModels, setSavedModels] = useState<DataModelRecord[]>([]);
  const [activeModelId, setActiveModelId] = useState<string | null>(null);
  const [showModelsModal, setShowModelsModal] = useState(false);

  // Step 1: Source Selection
  const [dsSearch, setDsSearch] = useState("");
  const [selectedDatasetIds, setSelectedDatasetIds] = useState<string[]>([]);
  const [primaryDatasetId, setPrimaryDatasetId] = useState<string>("");

  // Step 2: Relationships
  const [relAnalysis, setRelAnalysis] = useState<RelationshipAnalyzeResponse | null>(null);
  const [analyzingRel, setAnalyzingRel] = useState(false);
  const [chosenRelIds, setChosenRelIds] = useState<string[]>([]);
  const [adHocJoins, setAdHocJoins] = useState<Record<string, any>[]>([]);

  // Step 3: Fields & Measures & Targets
  const [selectedDimensions, setSelectedDimensions] = useState<string[]>([]);
  const [selectedMeasures, setSelectedMeasures] = useState<{ field_name: string; aggregation: string }[]>([]);
  const [targetValues, setTargetValues] = useState<Record<string, number>>({});

  // Step 5: Analyses
  const [selectedAnalyses, setSelectedAnalyses] = useState<string[]>([
    "kpi",
    "trend",
    "org_unit_comparison",
    "share",
    "ranking",
    "performance",
  ]);

  // Step 6: Calendar & Filters
  const [calendarMode, setCalendarMode] = useState<"jalali" | "gregorian">("jalali");
  const [dateStart, setDateStart] = useState("");
  const [dateEnd, setDateEnd] = useState("");
  const [relativeDateMode, setRelativeDateMode] = useState("");
  const [customFilters, setCustomFilters] = useState<DescriptiveFilterDraft[]>([]);

  // Step 7-8: Generated Dashboard Plan & Customization
  const [dashTitle, setDashTitle] = useState("داشبورد تحلیل توصیفی سازمانی");
  const [dashDescription, setDashDescription] = useState("");
  const [selectedThemeId, setSelectedThemeId] = useState<string>("");
  const [generating, setGenerating] = useState(false);
  const [plan, setPlan] = useState<GenerateDashboardResponse | null>(null);

  // Step 9: Saving & Publishing
  const [isPublic, setIsPublic] = useState(false);
  const [saveAsModel, setSaveAsModel] = useState(true);
  const [modelName, setModelName] = useState("مدل تحلیل توصیفی");
  const [savingDash, setSavingDash] = useState(false);

  const loadMetadata = useCallback(async () => {
    setLoadingInit(true);
    try {
      const [catRes, dsRes, thRes, modRes] = await Promise.all([
        apiFetch<BuilderCatalogResponse>("/descriptive-builder/catalog"),
        apiFetch<{ datasets: AccessibleDatasetMeta[] }>("/descriptive-builder/datasets"),
        apiFetch<any[]>("/dashboards/themes/list").catch(() => []),
        apiFetch<DataModelRecord[]>("/descriptive-builder/models").catch(() => []),
      ]);
      setFunctions(catRes.functions || []);
      setAnalyses(catRes.analyses || []);
      setDatasets(dsRes.datasets || []);
      setThemes(Array.isArray(thRes) ? thRes : []);
      setSavedModels(Array.isArray(modRes) ? modRes : []);
      if (Array.isArray(thRes) && thRes.length > 0 && !selectedThemeId) {
        setSelectedThemeId(thRes[0].id);
      }
      // Smart initial default if datasets exist and none selected yet
      if ((dsRes.datasets || []).length > 0 && selectedDatasetIds.length === 0) {
        const first = dsRes.datasets[0];
        setSelectedDatasetIds([first.id]);
        setPrimaryDatasetId(first.id);
      }
    } catch (err: any) {
      showToast(err.message || (isFa ? "خطا در بارگذاری متادیتای سازنده داشبورد" : "Failed to load builder catalog"));
    } finally {
      setLoadingInit(false);
    }
  }, [isFa, selectedDatasetIds.length, selectedThemeId, showToast]);

  useEffect(() => {
    if (user) loadMetadata();
  }, [user, loadMetadata]);

  const refreshDatasetsOnly = useCallback(async () => {
    try {
      const dsRes = await apiFetch<{ datasets: AccessibleDatasetMeta[] }>("/descriptive-builder/datasets");
      setDatasets(dsRes.datasets || []);
    } catch {
      // ignore
    }
  }, []);

  const selectedDatasets = useMemo(
    () => datasets.filter((d) => selectedDatasetIds.includes(d.id)),
    [datasets, selectedDatasetIds]
  );

  // Auto-analyze relationships whenever selected datasets or disambiguation/adHoc changes
  const runRelationshipAnalysis = useCallback(async () => {
    if (selectedDatasetIds.length < 2) {
      setRelAnalysis(null);
      return;
    }
    setAnalyzingRel(true);
    try {
      const res = await apiFetch<RelationshipAnalyzeResponse>("/descriptive-builder/relationships/analyze", {
        method: "POST",
        body: JSON.stringify({
          dataset_ids: selectedDatasetIds,
          primary_dataset_id: primaryDatasetId || selectedDatasetIds[0],
          chosen_relationship_ids: chosenRelIds,
          ad_hoc_joins: adHocJoins,
        }),
      });
      setRelAnalysis(res);
    } catch (err: any) {
      showToast(err.message || "Relationship analysis failed");
    } finally {
      setAnalyzingRel(false);
    }
  }, [selectedDatasetIds, primaryDatasetId, chosenRelIds, adHocJoins, showToast]);

  useEffect(() => {
    if (selectedDatasetIds.length >= 2) {
      runRelationshipAnalysis();
    } else {
      setRelAnalysis(null);
    }
  }, [selectedDatasetIds, primaryDatasetId, chosenRelIds, adHocJoins, runRelationshipAnalysis]);

  // Auto-populate suggested dimensions & measures when user picks datasets and hasn't explicitly chosen yet
  const allAvailableFields = useMemo(() => {
    const out: { dsId: string; dsName: string; field: AccessibleDatasetMeta["fields"][0] }[] = [];
    for (const ds of selectedDatasets) {
      for (const f of ds.fields) {
        out.push({ dsId: ds.id, dsName: ds.name, field: f });
      }
    }
    return out;
  }, [selectedDatasets]);

  const hasTimeField = useMemo(
    () =>
      allAvailableFields.some(
        ({ field }) => field.semantic_role === "time" || ["date", "datetime", "timestamp"].includes(field.data_type)
      ),
    [allAvailableFields]
  );

  const hasRestrictedData = useMemo(
    () => selectedDatasets.some((d) => d.has_security_restrictions),
    [selectedDatasets]
  );

  const toggleDataset = (id: string) => {
    setSelectedDatasetIds((prev) => {
      const next = prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id];
      if (!next.includes(primaryDatasetId)) {
        setPrimaryDatasetId(next[0] || "");
      }
      return next;
    });
  };

  const toggleMeasure = (fieldName: string) => {
    setSelectedMeasures((prev) => {
      const exists = prev.some((m) => m.field_name === fieldName);
      if (exists) return prev.filter((m) => m.field_name !== fieldName);
      return [...prev, { field_name: fieldName, aggregation: "SUM" }];
    });
  };

  const toggleDimension = (fieldName: string) => {
    setSelectedDimensions((prev) =>
      prev.includes(fieldName) ? prev.filter((x) => x !== fieldName) : [...prev, fieldName]
    );
  };

  const handleGenerateDashboard = async () => {
    if (selectedDatasetIds.length === 0) {
      showToast(isFa ? "ابتدا حداقل یک دیتاست انتخاب کنید." : "Select at least one dataset first.");
      return;
    }
    if (selectedDatasetIds.length > 1 && relAnalysis && !relAnalysis.ready_for_join) {
      showToast(
        isFa
          ? "روابط بین دیتاست‌ها هنوز کامل یا شفاف نیست؛ لطفاً در مرحله ۲ اتصال‌ها را مشخص کنید."
          : "Resolve disconnected or ambiguous dataset relationships in Step 2 first."
      );
      setStep(2);
      return;
    }
    setGenerating(true);
    try {
      const dateRangePayload = relativeDateMode
        ? { relative: relativeDateMode }
        : dateStart || dateEnd
        ? { start: dateStart || undefined, end: dateEnd || undefined }
        : undefined;

      const selectedFieldNames = Array.from(
        new Set([...selectedDimensions, ...selectedMeasures.map((m) => m.field_name)])
      );

      const res = await apiFetch<GenerateDashboardResponse>("/descriptive-builder/generate", {
        method: "POST",
        body: JSON.stringify({
          dataset_ids: selectedDatasetIds,
          title: dashTitle.trim() || "داشبورد تحلیل توصیفی",
          selected_fields: selectedFieldNames,
          dimensions: selectedDimensions,
          measures: selectedMeasures,
          analysis_types: selectedAnalyses,
          filters: customFilters,
          calendar_mode: calendarMode,
          date_range: dateRangePayload,
          chosen_relationship_ids: chosenRelIds,
          ad_hoc_joins: adHocJoins,
          target_values: targetValues,
        }),
      });
      setPlan(res);
      showToast(
        isFa
          ? `پیش‌نویس هوشمند داشبورد با ${res.widgets.length} ویجت و تحلیل متنی آماده شد!`
          : `Generated ${res.widgets.length} descriptive widgets.`
      );
      if (mode === "advanced") setStep(7);
    } catch (err: any) {
      showToast(err.message || (isFa ? "خطا در تولید داشبورد توصیفی" : "Dashboard generation failed"));
    } finally {
      setGenerating(false);
    }
  };

  const handleSaveDataModelOnly = async () => {
    const defPayload = {
      dataset_ids: selectedDatasetIds,
      primary_dataset_id: primaryDatasetId,
      chosen_relationship_ids: chosenRelIds,
      ad_hoc_joins: adHocJoins,
      dimensions: selectedDimensions,
      measures: selectedMeasures,
      analysis_types: selectedAnalyses,
      calendar_mode: calendarMode,
      target_values: targetValues,
      title: dashTitle,
    };
    try {
      if (activeModelId) {
        const res = await apiFetch<{ id: string; version: number }>(`/descriptive-builder/models/${activeModelId}`, {
          method: "PUT",
          body: JSON.stringify({
            name: modelName.trim() || dashTitle,
            description: dashDescription,
            model_type: "descriptive_model",
            definition_json: defPayload,
          }),
        });
        showToast(isFa ? `مدل تحلیلی به نسخه ${res.version} به‌روزرسانی شد.` : `Model updated to v${res.version}.`);
      } else {
        const res = await apiFetch<{ id: string; version: number }>("/descriptive-builder/models", {
          method: "POST",
          body: JSON.stringify({
            name: modelName.trim() || dashTitle,
            description: dashDescription,
            model_type: "descriptive_model",
            definition_json: defPayload,
          }),
        });
        setActiveModelId(res.id);
        showToast(isFa ? "مدل تحلیلی جدید ذخیره شد." : "Analytical model saved.");
      }
      const modRes = await apiFetch<DataModelRecord[]>("/descriptive-builder/models").catch(() => []);
      setSavedModels(Array.isArray(modRes) ? modRes : []);
    } catch (err: any) {
      showToast(err.message || "Failed to save data model");
    }
  };

  const handleLoadModel = (mod: DataModelRecord) => {
    const def = mod.definition_json || {};
    setActiveModelId(mod.id);
    setModelName(mod.name);
    if (Array.isArray(def.dataset_ids)) setSelectedDatasetIds(def.dataset_ids);
    if (def.primary_dataset_id) setPrimaryDatasetId(def.primary_dataset_id);
    if (Array.isArray(def.chosen_relationship_ids)) setChosenRelIds(def.chosen_relationship_ids);
    if (Array.isArray(def.ad_hoc_joins)) setAdHocJoins(def.ad_hoc_joins);
    if (Array.isArray(def.dimensions)) setSelectedDimensions(def.dimensions);
    if (Array.isArray(def.measures)) setSelectedMeasures(def.measures);
    if (Array.isArray(def.analysis_types)) setSelectedAnalyses(def.analysis_types);
    if (def.calendar_mode === "gregorian" || def.calendar_mode === "jalali") setCalendarMode(def.calendar_mode);
    if (def.target_values) setTargetValues(def.target_values);
    if (def.title) setDashTitle(def.title);
    setShowModelsModal(false);
    showToast(isFa ? `مدل «${mod.name}» (نسخه ${mod.version}) بارگذاری شد.` : `Loaded model ${mod.name}`);
  };

  const handleSaveAndPublishDashboard = async () => {
    if (!plan || plan.widgets.length === 0) {
      showToast(isFa ? "ابتدا داشبورد را در مرحله پیش‌نمایش تولید کنید." : "Generate the dashboard plan first.");
      return;
    }
    setSavingDash(true);
    try {
      const res = await apiFetch<{ id: string; title: string }>("/descriptive-builder/save", {
        method: "POST",
        body: JSON.stringify({
          title: dashTitle.trim() || plan.title,
          description: dashDescription || plan.executive_summary_fa,
          theme_id: selectedThemeId || undefined,
          is_public: isPublic && !hasRestrictedData,
          dataset_ids: plan.dataset_ids,
          model_id: saveAsModel ? activeModelId || undefined : undefined,
          model_definition: {
            ...plan.model_definition,
            dimensions: selectedDimensions,
            measures: selectedMeasures,
            analysis_types: selectedAnalyses,
            calendar_mode: calendarMode,
          },
          filters: customFilters.length > 0 ? customFilters : plan.filters,
          widgets: plan.widgets.map((w) => ({
            title: w.title,
            chart_type: w.chart_type,
            dataset_id: w.dataset_id,
            query_mode: w.query_mode || "visual",
            sql_text: w.sql_text || null,
            visual_spec: w.visual_spec,
            style: w.style,
            visualization: w.visualization,
            drill_hierarchy: w.drill_hierarchy || [],
            layout: w.layout,
            narrative_fa: w.narrative_fa,
            sources_used: w.sources_used,
            analysis_type: w.analysis_type,
          })),
        }),
      });
      showToast(isFa ? "داشبورد توصیفی ذخیره و منتشر شد!" : "Descriptive dashboard saved!");
      window.location.href = `/dashboards/${res.id}`;
    } catch (err: any) {
      showToast(err.message || (isFa ? "خطا در ذخیره داشبورد" : "Save failed"));
    } finally {
      setSavingDash(false);
    }
  };

  return (
    <AppShell>
      <div className="space-y-5 max-w-[1440px] mx-auto">
        {/* Top Studio Header */}
        <div className="bi-card p-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-lg sm:text-xl font-bold flex items-center gap-2">
              <Sparkles className="w-5 h-5 text-violet-500" />
              {isFa ? "تولید داشبورد توصیفی هوشمند" : "Descriptive Dashboard Builder Studio"}
            </h1>
            <p className="text-xs mt-1" style={{ color: "var(--bi-muted)" }}>
              {isFa
                ? "استودیوی جامع تحلیل توصیفی چندمنبعی، مدل‌سازی روابط، فرمول‌ساز پیشرفته، تقویم شمسی و تحلیل متنی خودکار"
                : "Multi-source descriptive modeling, IDE formula builder, Jalali/Gregorian calendar analytics, and fact-grounded narratives."}
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {/* Load/Save Data Model Button */}
            <button
              type="button"
              onClick={() => setShowModelsModal(true)}
              className="px-3 py-1.5 rounded-lg border text-xs font-semibold flex items-center gap-1.5 hover:opacity-85"
              style={{ borderColor: "var(--bi-border)", background: "var(--bi-surface)" }}
            >
              <FolderOpen className="w-3.5 h-3.5 text-blue-500" />
              {isFa ? `مدل‌های ذخیره‌شده (${savedModels.length})` : `Models (${savedModels.length})`}
            </button>

            {/* Simple vs Advanced Mode Toggle */}
            <div className="flex items-center p-1 rounded-xl border text-xs font-semibold" style={{ borderColor: "var(--bi-border)" }}>
              <button
                type="button"
                onClick={() => setMode("simple")}
                className="px-3 py-1 rounded-lg transition"
                style={{
                  background: mode === "simple" ? "var(--bi-primary)" : "transparent",
                  color: mode === "simple" ? "#fff" : "var(--bi-muted)",
                }}
              >
                {isFa ? "حالت ساده و سریع" : "Simple Mode"}
              </button>
              <button
                type="button"
                onClick={() => setMode("advanced")}
                className="px-3 py-1 rounded-lg transition"
                style={{
                  background: mode === "advanced" ? "var(--bi-primary)" : "transparent",
                  color: mode === "advanced" ? "#fff" : "var(--bi-muted)",
                }}
              >
                {isFa ? "حالت پیشرفته (۹ مرحله)" : "Advanced (9 Steps)"}
              </button>
            </div>
          </div>
        </div>

        {/* Saved Models Modal */}
        {showModelsModal && (
          <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4">
            <div className="bi-card w-full max-w-xl p-5 space-y-4 text-xs">
              <div className="flex items-center justify-between border-b pb-2" style={{ borderColor: "var(--bi-border)" }}>
                <h3 className="font-bold text-sm">{isFa ? "مدیریت مدل‌های تحلیلی ذخیره‌شده" : "Saved Analytical Models"}</h3>
                <button type="button" onClick={() => setShowModelsModal(false)}>✕</button>
              </div>
              <div className="space-y-2 max-h-72 overflow-y-auto">
                {savedModels.length === 0 ? (
                  <p style={{ color: "var(--bi-muted)" }}>{isFa ? "هنوز مدلی ذخیره نشده است." : "No saved models yet."}</p>
                ) : (
                  savedModels.map((m) => (
                    <div
                      key={m.id}
                      className="p-3 rounded-xl border flex items-center justify-between gap-2"
                      style={{ borderColor: "var(--bi-border)", background: "var(--bi-surface)" }}
                    >
                      <div>
                        <div className="font-bold">{m.name}</div>
                        <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>
                          {isFa ? `نسخه ${m.version}` : `v${m.version}`} · {(m.definition_json?.dataset_ids || []).length}{" "}
                          {isFa ? "منبع داده" : "datasets"}
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() => handleLoadModel(m)}
                        className="px-3 py-1.5 rounded-lg text-white font-semibold"
                        style={{ background: "var(--bi-primary)" }}
                      >
                        {isFa ? "بارگذاری مدل" : "Load"}
                      </button>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        )}

        {loadingInit ? (
          <div className="bi-card p-10 text-center text-xs" style={{ color: "var(--bi-muted)" }}>
            {isFa ? "در حال آماده‌سازی استودیوی تحلیل توصیفی..." : "Loading Descriptive Dashboard Studio..."}
          </div>
        ) : mode === "simple" ? (
          /* ===================== SIMPLE MODE ===================== */
          <div className="space-y-4">
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 text-xs">
              {/* Simple Card 1: Sources */}
              <div className="bi-card p-4 space-y-3">
                <div className="font-bold text-sm flex items-center gap-2">
                  <Database className="w-4 h-4 text-blue-500" />
                  {isFa ? "۱. انتخاب منابع داده" : "1. Select Datasets"}
                </div>
                <div className="space-y-2 max-h-64 overflow-y-auto">
                  {datasets.map((ds) => {
                    const checked = selectedDatasetIds.includes(ds.id);
                    return (
                      <label
                        key={ds.id}
                        className={`flex items-center justify-between gap-2 p-2.5 rounded-xl border cursor-pointer ${
                          checked ? "border-blue-500 bg-blue-500/10" : ""
                        }`}
                        style={!checked ? { borderColor: "var(--bi-border)" } : undefined}
                      >
                        <div className="flex items-center gap-2 min-w-0">
                          <input type="checkbox" checked={checked} onChange={() => toggleDataset(ds.id)} />
                          <div className="min-w-0">
                            <div className="font-bold truncate">{ds.name}</div>
                            <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>
                              {ds.row_count.toLocaleString()} {isFa ? "ردیف" : "rows"} · {ds.fields.length} {isFa ? "ستون" : "cols"}
                            </div>
                          </div>
                        </div>
                        {ds.has_security_restrictions && <Lock className="w-3.5 h-3.5 text-amber-500 shrink-0" />}
                      </label>
                    );
                  })}
                </div>
                {selectedDatasetIds.length > 1 && relAnalysis && !relAnalysis.ready_for_join && (
                  <div className="p-2.5 rounded-lg bg-amber-500/10 border border-amber-500/40 text-amber-600 dark:text-amber-400 text-[11px] space-y-1">
                    <div>{isFa ? "برخی منابع انتخابی هنوز به هم متصل نیستند." : "Some selected datasets need relationship mapping."}</div>
                    <button
                      type="button"
                      onClick={() => { setMode("advanced"); setStep(2); }}
                      className="underline font-bold"
                    >
                      {isFa ? "تنظیم روابط در حالت پیشرفته ←" : "Configure Relationships →"}
                    </button>
                  </div>
                )}
              </div>

              {/* Simple Card 2: Analyses & Calendar */}
              <div className="bi-card p-4 space-y-3">
                <div className="font-bold text-sm flex items-center gap-2">
                  <Sliders className="w-4 h-4 text-violet-500" />
                  {isFa ? "۲. تحلیل‌های موردنظر و تقویم" : "2. Analyses & Calendar"}
                </div>
                <div>
                  <label className="block text-[10px] font-semibold mb-1">{isFa ? "عنوان داشبورد" : "Dashboard Title"}</label>
                  <input
                    value={dashTitle}
                    onChange={(e) => setDashTitle(e.target.value)}
                    className="w-full px-3 py-1.5 rounded-lg border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  />
                </div>
                <div className="flex gap-2">
                  {[
                    { id: "jalali", label: isFa ? "تقویم شمسی (۱۴۰۴)" : "Jalali" },
                    { id: "gregorian", label: isFa ? "تقویم میلادی" : "Gregorian" },
                  ].map((cal) => (
                    <button
                      key={cal.id}
                      type="button"
                      onClick={() => setCalendarMode(cal.id as any)}
                      className="flex-1 py-1.5 rounded-lg border font-semibold"
                      style={{
                        background: calendarMode === cal.id ? "var(--bi-primary)" : "transparent",
                        color: calendarMode === cal.id ? "#fff" : "var(--bi-text)",
                        borderColor: "var(--bi-border)",
                      }}
                    >
                      {cal.label}
                    </button>
                  ))}
                </div>
                <div className="flex flex-wrap gap-1.5 max-h-44 overflow-y-auto pt-1">
                  {analyses.map((a) => {
                    const active = selectedAnalyses.includes(a.id);
                    return (
                      <button
                        key={a.id}
                        type="button"
                        onClick={() =>
                          setSelectedAnalyses((prev) =>
                            prev.includes(a.id) ? prev.filter((x) => x !== a.id) : [...prev, a.id]
                          )
                        }
                        className="px-2.5 py-1 rounded-full border text-[11px] font-medium transition"
                        style={{
                          background: active ? "var(--bi-primary)" : "transparent",
                          color: active ? "#fff" : "var(--bi-text)",
                          borderColor: "var(--bi-border)",
                        }}
                      >
                        {a.name_fa}
                      </button>
                    );
                  })}
                </div>
              </div>

              {/* Simple Card 3: Generate & Save */}
              <div className="bi-card p-4 flex flex-col justify-between gap-3">
                <div className="space-y-2">
                  <div className="font-bold text-sm flex items-center gap-2">
                    <Rocket className="w-4 h-4 text-emerald-500" />
                    {isFa ? "۳. تولید هوشمند و ذخیره" : "3. Generate & Save"}
                  </div>
                  <p style={{ color: "var(--bi-muted)" }} className="leading-relaxed">
                    {isFa
                      ? "موتور تحلیل توصیفی به‌طور خودکار شاخص‌ها، ابعاد، نمودارهای مناسب و متن تحلیلی دقیق را از داده‌های واقعی می‌سازد."
                      : "Automatically selects measures, dimensions, charts, and generates fact-grounded Persian narratives."}
                  </p>
                </div>
                <div className="space-y-2">
                  <button
                    type="button"
                    disabled={generating || selectedDatasetIds.length === 0}
                    onClick={handleGenerateDashboard}
                    className="w-full py-2.5 rounded-xl font-bold text-white flex items-center justify-center gap-2 disabled:opacity-40"
                    style={{ background: "var(--bi-primary)" }}
                  >
                    <Play className="w-4 h-4" />
                    {generating
                      ? isFa ? "در حال تحلیل و ساخت نمودارها..." : "Generating..."
                      : isFa ? "تولید و پیش‌نمایش داشبورد" : "Generate Live Preview"}
                  </button>
                  {plan && (
                    <button
                      type="button"
                      disabled={savingDash}
                      onClick={handleSaveAndPublishDashboard}
                      className="w-full py-2.5 rounded-xl font-bold text-white bg-emerald-600 hover:bg-emerald-700 flex items-center justify-center gap-2"
                    >
                      <Save className="w-4 h-4" />
                      {savingDash ? "..." : isFa ? "ذخیره و ورود به داشبورد" : "Save & Open Dashboard"}
                    </button>
                  )}
                </div>
              </div>
            </div>

            {plan && (
              <DescriptivePreviewPanel
                isFa={isFa}
                plan={plan}
                onUpdateWidget={(tid, patch) =>
                  setPlan((p) =>
                    p ? { ...p, widgets: p.widgets.map((w) => (w.temp_id === tid ? { ...w, ...patch } : w)) } : p
                  )
                }
                onRemoveWidget={(tid) =>
                  setPlan((p) => (p ? { ...p, widgets: p.widgets.filter((w) => w.temp_id !== tid) } : p))
                }
                showToast={showToast}
              />
            )}
          </div>
        ) : (
          /* ===================== ADVANCED 9-STEP WIZARD ===================== */
          <div className="space-y-4">
            {/* 9-Step Progress Bar */}
            <div className="bi-card p-3 overflow-x-auto">
              <div className="flex items-center gap-1.5 min-w-max">
                {WIZARD_STEPS.map((st) => {
                  const active = step === st.num;
                  const completed = step > st.num;
                  return (
                    <button
                      key={st.num}
                      type="button"
                      onClick={() => setStep(st.num)}
                      className="px-3 py-2 rounded-xl text-xs font-semibold border transition flex items-center gap-1.5 whitespace-nowrap"
                      style={{
                        background: active
                          ? "var(--bi-primary)"
                          : completed
                          ? "color-mix(in srgb, #10b981 12%, transparent)"
                          : "transparent",
                        color: active ? "#fff" : completed ? "#10b981" : "var(--bi-text)",
                        borderColor: active ? "var(--bi-primary)" : completed ? "rgba(16,185,129,0.4)" : "var(--bi-border)",
                      }}
                    >
                      {completed && <CheckCircle2 className="w-3.5 h-3.5" />}
                      <span>{isFa ? st.fa : st.en}</span>
                    </button>
                  );
                })}
              </div>
            </div>

            {/* STEP 1: Multi-Dataset Selection */}
            {step === 1 && (
              <div className="bi-card p-5 space-y-4 text-xs">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <h2 className="text-base font-bold">{isFa ? "گام ۱: انتخاب یک یا چند منبع داده" : "Step 1: Select Datasets"}</h2>
                    <p style={{ color: "var(--bi-muted)" }}>
                      {isFa
                        ? "فقط دیتاست‌ها و ستون‌هایی که دسترسی مجاز (RBAC/CLS) دارید نمایش داده می‌شوند."
                        : "Only RBAC-authorized datasets and CLS-visible columns are shown."}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="relative">
                      <Search className="w-3.5 h-3.5 absolute top-2.5 start-2.5" style={{ color: "var(--bi-muted)" }} />
                      <input
                        value={dsSearch}
                        onChange={(e) => setDsSearch(e.target.value)}
                        placeholder={isFa ? "جستجوی دیتاست..." : "Search datasets..."}
                        className="ps-8 pe-3 py-1.5 rounded-lg border bg-transparent"
                        style={{ borderColor: "var(--bi-border)" }}
                      />
                    </div>
                    <Link
                      href="/data-sources"
                      className="px-3 py-1.5 rounded-lg border font-semibold flex items-center gap-1 hover:opacity-80"
                      style={{ borderColor: "var(--bi-border)" }}
                    >
                      <Upload className="w-3.5 h-3.5" /> {isFa ? "آپلود اکسل / CSV" : "Upload Excel"}
                    </Link>
                  </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                  {datasets
                    .filter((d) => !dsSearch.trim() || d.name.toLowerCase().includes(dsSearch.toLowerCase()))
                    .map((ds) => {
                      const selected = selectedDatasetIds.includes(ds.id);
                      return (
                        <div
                          key={ds.id}
                          onClick={() => toggleDataset(ds.id)}
                          className={`p-4 rounded-xl border cursor-pointer space-y-2 transition ${
                            selected ? "ring-2 ring-blue-500 border-blue-500 bg-blue-500/5" : "hover:border-blue-500/40"
                          }`}
                          style={!selected ? { borderColor: "var(--bi-border)" } : undefined}
                        >
                          <div className="flex items-start justify-between gap-2">
                            <div className="flex items-center gap-2">
                              <input type="checkbox" checked={selected} onChange={() => {}} />
                              <div>
                                <div className="font-bold text-sm">{ds.name}</div>
                                <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>
                                  {ds.schema_type} · {ds.row_count.toLocaleString()} {isFa ? "ردیف" : "rows"} · {ds.fields.length}{" "}
                                  {isFa ? "ستون" : "cols"}
                                </div>
                              </div>
                            </div>
                            {ds.has_security_restrictions && (
                              <span className="text-[9px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-500 font-semibold flex items-center gap-1">
                                <Lock className="w-2.5 h-2.5" /> RLS/CLS
                              </span>
                            )}
                          </div>
                          <div className="flex flex-wrap gap-1">
                            {ds.fields.slice(0, 6).map((f) => (
                              <span
                                key={f.id}
                                className="text-[10px] px-1.5 py-0.5 rounded border"
                                style={{ borderColor: "var(--bi-border)" }}
                              >
                                {f.display_name || f.field_name}
                              </span>
                            ))}
                            {ds.fields.length > 6 && (
                              <span className="text-[10px]" style={{ color: "var(--bi-muted)" }}>
                                +{ds.fields.length - 6}
                              </span>
                            )}
                          </div>
                        </div>
                      );
                    })}
                </div>
              </div>
            )}

            {/* STEP 2: Multi-Source Relationships */}
            {step === 2 && (
              <RelationshipGraphPanel
                isFa={isFa}
                selectedDatasets={selectedDatasets}
                analysis={relAnalysis}
                analyzing={analyzingRel}
                primaryDatasetId={primaryDatasetId}
                onPrimaryDatasetChange={setPrimaryDatasetId}
                chosenRelIds={chosenRelIds}
                onChosenRelIdsChange={setChosenRelIds}
                adHocJoins={adHocJoins}
                onAdHocJoinsChange={setAdHocJoins}
                onReanalyze={runRelationshipAnalysis}
                showToast={showToast}
              />
            )}

            {/* STEP 3: Fields, Measures, Dimensions & Targets */}
            {step === 3 && (
              <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 text-xs">
                {/* Dimensions & Time picker */}
                <div className="lg:col-span-5 bi-card p-4 space-y-3">
                  <div className="font-bold text-sm flex items-center gap-2">
                    <Layers className="w-4 h-4 text-blue-500" />
                    {isFa ? "ابعاد دسته‌بندی و زمان (Dimensions)" : "Dimensions & Time Fields"}
                  </div>
                  <p className="text-[11px]" style={{ color: "var(--bi-muted)" }}>
                    {isFa ? "در صورت خالی گذاشتن، موتور تحلیل خودکار بهترین ابعاد را برمی‌گزیند." : "Leave empty for smart auto-selection."}
                  </p>
                  <div className="space-y-1.5 max-h-96 overflow-y-auto">
                    {allAvailableFields
                      .filter(({ field }) => field.semantic_role !== "measure")
                      .map(({ dsName, field }) => {
                        const checked = selectedDimensions.includes(field.field_name);
                        return (
                          <label
                            key={`${dsName}-${field.id}`}
                            className={`flex items-center justify-between gap-2 p-2 rounded-lg border cursor-pointer ${
                              checked ? "border-blue-500 bg-blue-500/10" : ""
                            }`}
                            style={!checked ? { borderColor: "var(--bi-border)" } : undefined}
                          >
                            <div className="flex items-center gap-2">
                              <input
                                type="checkbox"
                                checked={checked}
                                onChange={() => toggleDimension(field.field_name)}
                              />
                              <span className="font-semibold">{field.display_name || field.field_name}</span>
                            </div>
                            <span className="text-[10px]" style={{ color: "var(--bi-muted)" }}>
                              {dsName} · {field.semantic_role}
                            </span>
                          </label>
                        );
                      })}
                  </div>
                </div>

                {/* Measures + Aggregation + Target Goals */}
                <div className="lg:col-span-7 bi-card p-4 space-y-3">
                  <div className="font-bold text-sm flex items-center gap-2">
                    <Sliders className="w-4 h-4 text-emerald-500" />
                    {isFa ? "شاخص‌های عددی، تجمیع و مقادیر هدف (Measures & Targets)" : "Measures, Aggregations & Targets"}
                  </div>
                  <div className="space-y-2 max-h-96 overflow-y-auto">
                    {allAvailableFields
                      .filter(
                        ({ field }) =>
                          field.semantic_role === "measure" ||
                          ["integer", "double", "float", "decimal", "numeric"].includes(field.data_type)
                      )
                      .map(({ dsName, field }) => {
                        const existing = selectedMeasures.find((m) => m.field_name === field.field_name);
                        return (
                          <div
                            key={`${dsName}-${field.id}`}
                            className={`p-2.5 rounded-xl border flex flex-wrap items-center justify-between gap-2 ${
                              existing ? "border-emerald-500 bg-emerald-500/5" : ""
                            }`}
                            style={!existing ? { borderColor: "var(--bi-border)" } : undefined}
                          >
                            <label className="flex items-center gap-2 cursor-pointer">
                              <input
                                type="checkbox"
                                checked={Boolean(existing)}
                                onChange={() => toggleMeasure(field.field_name)}
                              />
                              <div>
                                <span className="font-bold">{field.display_name || field.field_name}</span>
                                <span className="ms-2 text-[10px]" style={{ color: "var(--bi-muted)" }}>
                                  ({dsName})
                                </span>
                              </div>
                            </label>
                            {existing && (
                              <div className="flex items-center gap-2">
                                <select
                                  value={existing.aggregation}
                                  onChange={(e) =>
                                    setSelectedMeasures((prev) =>
                                      prev.map((m) =>
                                        m.field_name === field.field_name ? { ...m, aggregation: e.target.value } : m
                                      )
                                    )
                                  }
                                  className="px-2 py-1 rounded border text-[11px]"
                                  style={{ background: "var(--bi-surface)", borderColor: "var(--bi-border)" }}
                                >
                                  {["SUM", "AVG", "COUNT", "COUNT_DISTINCT", "MIN", "MAX", "MEDIAN", "STDDEV"].map((ag) => (
                                    <option key={ag} value={ag}>{ag}</option>
                                  ))}
                                </select>
                                <input
                                  type="number"
                                  placeholder={isFa ? "هدف (اختیاری)" : "Target"}
                                  value={targetValues[field.field_name] ?? ""}
                                  onChange={(e) => {
                                    const val = e.target.value;
                                    setTargetValues((prev) => {
                                      const copy = { ...prev };
                                      if (!val) delete copy[field.field_name];
                                      else copy[field.field_name] = Number(val);
                                      return copy;
                                    });
                                  }}
                                  className="w-28 px-2 py-1 rounded border text-[11px] bg-transparent"
                                  style={{ borderColor: "var(--bi-border)" }}
                                />
                              </div>
                            )}
                          </div>
                        );
                      })}
                  </div>
                </div>
              </div>
            )}

            {/* STEP 4: IDE Formula & Measure Studio */}
            {step === 4 && (
              <FormulaStudioPanel
                isFa={isFa}
                selectedDatasets={selectedDatasets}
                functions={functions}
                chosenRelIds={chosenRelIds}
                adHocJoins={adHocJoins}
                onRefreshDatasets={refreshDatasetsOnly}
                onSelectMeasureOrField={(fname, kind) => {
                  if (kind === "measure" || kind === "field") {
                    setSelectedMeasures((prev) =>
                      prev.some((m) => m.field_name === fname) ? prev : [...prev, { field_name: fname, aggregation: "SUM" }]
                    );
                  }
                }}
                showToast={showToast}
              />
            )}

            {/* STEP 5: 15-Analysis Catalog Selection */}
            {step === 5 && (
              <div className="bi-card p-5 space-y-4 text-xs">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <h2 className="text-base font-bold">
                      {isFa ? "گام ۵: انتخاب بسته‌های تحلیل توصیفی (۱۵ نوع تحلیل)" : "Step 5: Select Descriptive Analyses (15 Presets)"}
                    </h2>
                    <p style={{ color: "var(--bi-muted)" }}>
                      {hasTimeField
                        ? isFa ? "ستون زمانی شناسایی شد؛ تمامی تحلیل‌های روند و مقایسه دوره در دسترس هستند." : "Time field detected; trend analyses enabled."
                        : isFa ? "توجه: ستون زمانی در منابع انتخابی یافت نشد؛ برای تحلیل‌های روندی می‌توانید در گام ۶ یا ۱ جدول تاریخ اضافه کنید." : "No time dimension detected yet."}
                    </p>
                  </div>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => setSelectedAnalyses(analyses.map((a) => a.id))}
                      className="px-3 py-1.5 rounded-lg border font-semibold"
                      style={{ borderColor: "var(--bi-border)" }}
                    >
                      {isFa ? "انتخاب همه" : "Select All"}
                    </button>
                    <button
                      type="button"
                      onClick={() =>
                        setSelectedAnalyses(
                          hasTimeField
                            ? ["kpi", "trend", "org_unit_comparison", "share", "ranking", "performance"]
                            : ["kpi", "org_unit_comparison", "distribution", "share", "ranking", "performance"]
                        )
                      }
                      className="px-3 py-1.5 rounded-lg text-white font-semibold"
                      style={{ background: "var(--bi-primary)" }}
                    >
                      {isFa ? "انتخاب هوشمند پیشنهادی" : "Smart Preset"}
                    </button>
                  </div>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                  {analyses.map((an) => {
                    const checked = selectedAnalyses.includes(an.id);
                    const needsTimeWarn = an.requires_time && !hasTimeField;
                    return (
                      <div
                        key={an.id}
                        onClick={() =>
                          setSelectedAnalyses((prev) =>
                            prev.includes(an.id) ? prev.filter((x) => x !== an.id) : [...prev, an.id]
                          )
                        }
                        className={`p-3.5 rounded-xl border cursor-pointer space-y-1.5 transition ${
                          checked ? "border-blue-500 bg-blue-500/10 ring-1 ring-blue-500" : ""
                        }`}
                        style={!checked ? { borderColor: "var(--bi-border)" } : undefined}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <div className="flex items-center gap-2 font-bold">
                            <input type="checkbox" checked={checked} onChange={() => {}} />
                            <span>{an.name_fa}</span>
                          </div>
                          <span className="text-[9px] px-1.5 py-0.5 rounded bg-slate-500/15 uppercase">{an.category}</span>
                        </div>
                        <div className="flex items-center justify-between text-[10px]" style={{ color: "var(--bi-muted)" }}>
                          <span>{isFa ? "نمودارها:" : "Charts:"} {an.recommended_charts.join(" / ")}</span>
                          {needsTimeWarn && (
                            <span className="text-amber-500 font-semibold">{isFa ? "نیازمند ستون تاریخ" : "Needs Date"}</span>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* STEP 6: Calendar & Date Range & Filters */}
            {step === 6 && (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 text-xs">
                <div className="bi-card p-5 space-y-4">
                  <div className="font-bold text-sm flex items-center gap-2">
                    <Calendar className="w-4 h-4 text-blue-500" />
                    {isFa ? "تنظیمات تقویم و بازه زمانی (شمسی / میلادی)" : "Calendar & Date Range Settings"}
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    {[
                      { id: "jalali", label: isFa ? "تقویم شمسی (۱۴۰۴/۰۱/۰۱)" : "Jalali (Solar Hijri)" },
                      { id: "gregorian", label: isFa ? "تقویم میلادی (2025-03-21)" : "Gregorian" },
                    ].map((cal) => (
                      <button
                        key={cal.id}
                        type="button"
                        onClick={() => setCalendarMode(cal.id as any)}
                        className="py-2.5 px-3 rounded-xl border font-bold"
                        style={{
                          background: calendarMode === cal.id ? "var(--bi-primary)" : "transparent",
                          color: calendarMode === cal.id ? "#fff" : "var(--bi-text)",
                          borderColor: "var(--bi-border)",
                        }}
                      >
                        {cal.label}
                      </button>
                    ))}
                  </div>
                  <div className="space-y-2">
                    <label className="block font-semibold">{isFa ? "بازه سریع پویا (Relative Date):" : "Quick Relative Range:"}</label>
                    <select
                      value={relativeDateMode}
                      onChange={(e) => setRelativeDateMode(e.target.value)}
                      className="w-full px-3 py-2 rounded-lg border"
                      style={{ background: "var(--bi-surface)", borderColor: "var(--bi-border)" }}
                    >
                      <option value="">{isFa ? "بدون محدودیت پیش‌فرض (همه تاریخ‌ها)" : "All Dates"}</option>
                      <option value="jalali_ytd">{isFa ? "ابتدای سال شمسی تا امروز (Jalali YTD)" : "Jalali Year-to-Date"}</option>
                      <option value="jalali_mtd">{isFa ? "ابتدای ماه شمسی تا امروز (Jalali MTD)" : "Jalali Month-to-Date"}</option>
                      <option value="last_30_days">{isFa ? "۳۰ روز اخیر" : "Last 30 Days"}</option>
                      <option value="last_90_days">{isFa ? "۹۰ روز اخیر" : "Last 90 Days"}</option>
                      <option value="last_365_days">{isFa ? "یک سال اخیر" : "Last 365 Days"}</option>
                    </select>
                  </div>
                  {!relativeDateMode && (
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="block text-[10px] font-semibold mb-1">
                          {isFa ? `از تاریخ (${calendarMode === "jalali" ? "مثلاً 1404/01/01" : "YYYY-MM-DD"})` : "From"}
                        </label>
                        <input
                          dir="ltr"
                          value={dateStart}
                          onChange={(e) => setDateStart(e.target.value)}
                          placeholder={calendarMode === "jalali" ? "1404/01/01" : "2025-03-21"}
                          className="w-full px-3 py-1.5 rounded-lg border bg-transparent"
                          style={{ borderColor: "var(--bi-border)" }}
                        />
                      </div>
                      <div>
                        <label className="block text-[10px] font-semibold mb-1">
                          {isFa ? `تا تاریخ (${calendarMode === "jalali" ? "مثلاً 1404/12/29" : "YYYY-MM-DD"})` : "To"}
                        </label>
                        <input
                          dir="ltr"
                          value={dateEnd}
                          onChange={(e) => setDateEnd(e.target.value)}
                          placeholder={calendarMode === "jalali" ? "1404/12/29" : "2026-03-20"}
                          className="w-full px-3 py-1.5 rounded-lg border bg-transparent"
                          style={{ borderColor: "var(--bi-border)" }}
                        />
                      </div>
                    </div>
                  )}
                </div>

                {/* Custom Dashboard Filters Builder */}
                <div className="bi-card p-5 space-y-4">
                  <div className="flex items-center justify-between">
                    <div className="font-bold text-sm flex items-center gap-2">
                      <Filter className="w-4 h-4 text-emerald-500" />
                      {isFa ? "فیلترهای تعاملی بالای داشبورد" : "Dashboard Interactive Filters"}
                    </div>
                    <button
                      type="button"
                      onClick={() => {
                        const firstDim = allAvailableFields.find(({ field }) => field.semantic_role === "dimension") || allAvailableFields[0];
                        if (!firstDim) return;
                        setCustomFilters((prev) => [
                          ...prev,
                          {
                            name: firstDim.field.display_name || firstDim.field.field_name,
                            field_name: firstDim.field.field_name,
                            dataset_id: firstDim.dsId,
                            filter_type: "multi_select",
                            operator: "in",
                          },
                        ]);
                      }}
                      className="px-2.5 py-1 rounded-lg border font-semibold flex items-center gap-1"
                      style={{ borderColor: "var(--bi-border)" }}
                    >
                      <Plus className="w-3.5 h-3.5" /> {isFa ? "افزودن فیلتر" : "Add Filter"}
                    </button>
                  </div>
                  {customFilters.length === 0 ? (
                    <p style={{ color: "var(--bi-muted)" }}>
                      {isFa
                        ? "در صورت عدم تعریف دستی، فیلترهای مناسب (مانند منطقه یا دسته‌بندی) به‌صورت خودکار تولید می‌شوند."
                        : "Leave empty for automatic filter creation."}
                    </p>
                  ) : (
                    <div className="space-y-2">
                      {customFilters.map((flt, idx) => (
                        <div key={idx} className="p-2.5 rounded-xl border flex flex-wrap items-center gap-2" style={{ borderColor: "var(--bi-border)" }}>
                          <select
                            value={`${flt.dataset_id}::${flt.field_name}`}
                            onChange={(e) => {
                              const [dsId, fname] = e.target.value.split("::");
                              const found = allAvailableFields.find((x) => x.dsId === dsId && x.field.field_name === fname);
                              setCustomFilters((prev) =>
                                prev.map((f, i) =>
                                  i === idx
                                    ? {
                                        ...f,
                                        dataset_id: dsId,
                                        field_name: fname,
                                        name: found?.field.display_name || fname,
                                      }
                                    : f
                                )
                              );
                            }}
                            className="flex-1 px-2 py-1 rounded border"
                            style={{ background: "var(--bi-surface)", borderColor: "var(--bi-border)" }}
                          >
                            {allAvailableFields.map(({ dsId, dsName, field }) => (
                              <option key={`${dsId}::${field.field_name}`} value={`${dsId}::${field.field_name}`}>
                                {dsName} · {field.display_name || field.field_name}
                              </option>
                            ))}
                          </select>
                          <select
                            value={flt.filter_type}
                            onChange={(e) =>
                              setCustomFilters((prev) =>
                                prev.map((f, i) => (i === idx ? { ...f, filter_type: e.target.value } : f))
                              )
                            }
                            className="px-2 py-1 rounded border"
                            style={{ background: "var(--bi-surface)", borderColor: "var(--bi-border)" }}
                          >
                            <option value="multi_select">{isFa ? "چندانتخابی" : "Multi Select"}</option>
                            <option value="date_range">{isFa ? "بازه تاریخ" : "Date Range"}</option>
                            <option value="numeric_range">{isFa ? "بازه عددی" : "Numeric Range"}</option>
                          </select>
                          <button
                            type="button"
                            onClick={() => setCustomFilters((prev) => prev.filter((_, i) => i !== idx))}
                            className="p-1 text-red-500"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* STEP 7 & 8: Generate, Live Interactive Preview & Customize */}
            {(step === 7 || step === 8) && (
              <div className="space-y-4">
                <div className="bi-card p-4 flex flex-wrap items-center justify-between gap-3 text-xs">
                  <div className="flex flex-wrap items-center gap-3 flex-1">
                    <div className="min-w-[220px] flex-1 max-w-sm">
                      <label className="block text-[10px] font-semibold mb-1">{isFa ? "عنوان داشبورد" : "Dashboard Title"}</label>
                      <input
                        value={dashTitle}
                        onChange={(e) => setDashTitle(e.target.value)}
                        className="w-full px-3 py-1.5 rounded-lg border bg-transparent font-bold"
                        style={{ borderColor: "var(--bi-border)" }}
                      />
                    </div>
                    {step === 8 && themes.length > 0 && (
                      <div className="min-w-[180px]">
                        <label className="block text-[10px] font-semibold mb-1 flex items-center gap-1">
                          <Palette className="w-3 h-3" /> {isFa ? "تم و رنگ‌بندی ظاهری" : "Theme"}
                        </label>
                        <select
                          value={selectedThemeId}
                          onChange={(e) => setSelectedThemeId(e.target.value)}
                          className="w-full px-3 py-1.5 rounded-lg border"
                          style={{ background: "var(--bi-surface)", borderColor: "var(--bi-border)" }}
                        >
                          {themes.map((th) => (
                            <option key={th.id} value={th.id}>{th.name}</option>
                          ))}
                        </select>
                      </div>
                    )}
                  </div>

                  <button
                    type="button"
                    disabled={generating}
                    onClick={handleGenerateDashboard}
                    className="px-4 py-2 rounded-xl text-white font-bold flex items-center gap-2"
                    style={{ background: "var(--bi-primary)" }}
                  >
                    <Play className="w-4 h-4" />
                    {generating ? (isFa ? "در حال تحلیل و رندر..." : "Generating...") : plan ? (isFa ? "بازتولید داشبورد" : "Regenerate") : (isFa ? "تولید و پیش‌نمایش زنده" : "Generate Live Preview")}
                  </button>
                </div>

                {plan ? (
                  <DescriptivePreviewPanel
                    isFa={isFa}
                    plan={plan}
                    onUpdateWidget={(tid, patch) =>
                      setPlan((p) =>
                        p ? { ...p, widgets: p.widgets.map((w) => (w.temp_id === tid ? { ...w, ...patch } : w)) } : p
                      )
                    }
                    onRemoveWidget={(tid) =>
                      setPlan((p) => (p ? { ...p, widgets: p.widgets.filter((w) => w.temp_id !== tid) } : p))
                    }
                    showToast={showToast}
                  />
                ) : (
                  <div className="bi-card p-10 text-center space-y-3 text-xs">
                    <Sparkles className="w-8 h-8 mx-auto text-violet-500" />
                    <div className="font-bold text-sm">
                      {isFa ? "آماده تولید داشبورد توصیفی و تحلیل متنی خودکار" : "Ready to Generate Descriptive Dashboard"}
                    </div>
                    <p style={{ color: "var(--bi-muted)" }}>
                      {isFa
                        ? "روی دکمه «تولید و پیش‌نمایش زنده» کلیک کنید تا نمودارها و گزارش‌های متنی با داده‌های واقعی ساخته شوند."
                        : "Click Generate Live Preview to compile queries, execute on DuckDB, and render interactive widgets."}
                    </p>
                  </div>
                )}
              </div>
            )}

            {/* STEP 9: Save Model Version, Save Dashboard & Publish */}
            {step === 9 && (
              <div className="bi-card p-6 space-y-5 text-xs max-w-3xl mx-auto">
                <div className="font-bold text-base flex items-center gap-2">
                  <Rocket className="w-5 h-5 text-emerald-500" />
                  {isFa ? "گام ۹: ذخیره داشبورد، نسخه‌بندی مدل تحلیلی و انتشار امن" : "Step 9: Save Model, Dashboard & Security Check"}
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div>
                    <label className="block font-semibold mb-1">{isFa ? "عنوان نهایی داشبورد" : "Final Dashboard Title"}</label>
                    <input
                      value={dashTitle}
                      onChange={(e) => setDashTitle(e.target.value)}
                      className="w-full px-3 py-2 rounded-lg border bg-transparent"
                      style={{ borderColor: "var(--bi-border)" }}
                    />
                  </div>
                  <div>
                    <label className="block font-semibold mb-1">{isFa ? "نام مدل داده تحلیلی (قابل بازاستفاده)" : "Reusable Analytical Model Name"}</label>
                    <input
                      value={modelName}
                      onChange={(e) => setModelName(e.target.value)}
                      className="w-full px-3 py-2 rounded-lg border bg-transparent"
                      style={{ borderColor: "var(--bi-border)" }}
                    />
                  </div>
                </div>

                <div>
                  <label className="block font-semibold mb-1">{isFa ? "توضیحات / خلاصه اجرایی" : "Description / Executive Notes"}</label>
                  <textarea
                    rows={3}
                    value={dashDescription || plan?.executive_summary_fa || ""}
                    onChange={(e) => setDashDescription(e.target.value)}
                    className="w-full px-3 py-2 rounded-lg border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  />
                </div>

                {hasRestrictedData ? (
                  <div className="p-3 rounded-xl border border-amber-500/50 bg-amber-500/10 text-amber-700 dark:text-amber-300 flex items-start gap-2">
                    <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
                    <div>
                      <strong>{isFa ? "محافظت امنیتی RLS/CLS فعال است:" : "RLS/CLS Protection Active:"}</strong>{" "}
                      {isFa
                        ? "به‌دلیل وجود سیاست‌های محدودیت سطری یا ستونی روی منابع داده این داشبورد، انتشار عمومی بدون مجوز ویژه غیرفعال است و داشبورد فقط برای کاربران مجاز سازمان منتشر می‌شود."
                        : "Because one or more selected datasets enforce RLS/CLS policies, public anonymous sharing is blocked automatically."}
                    </div>
                  </div>
                ) : (
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input type="checkbox" checked={isPublic} onChange={(e) => setIsPublic(e.target.checked)} />
                    <span>{isFa ? "ایجاد لینک اشتراک‌گذاری عمومی (Embed)" : "Enable Public Share Link"}</span>
                  </label>
                )}

                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" checked={saveAsModel} onChange={(e) => setSaveAsModel(e.target.checked)} />
                  <span>{isFa ? "ذخیره/نسخه‌بندی تنظیمات مدل و روابط در DataModel" : "Version & save DataModel workspace state"}</span>
                </label>

                <div className="flex flex-wrap items-center justify-between gap-3 pt-3 border-t" style={{ borderColor: "var(--bi-border)" }}>
                  <button
                    type="button"
                    onClick={handleSaveDataModelOnly}
                    className="px-4 py-2 rounded-xl border font-semibold flex items-center gap-1.5"
                    style={{ borderColor: "var(--bi-border)" }}
                  >
                    <Bookmark className="w-4 h-4 text-blue-500" />
                    {isFa ? "فقط ذخیره مدل تحلیلی" : "Save Data Model Only"}
                  </button>

                  <button
                    type="button"
                    disabled={savingDash || !plan}
                    onClick={handleSaveAndPublishDashboard}
                    className="px-5 py-2.5 rounded-xl text-white font-bold bg-emerald-600 hover:bg-emerald-700 flex items-center gap-2 disabled:opacity-40"
                  >
                    <Save className="w-4 h-4" />
                    {savingDash
                      ? isFa ? "در حال ذخیره..." : "Saving..."
                      : isFa ? "ذخیره نهایی و باز کردن داشبورد" : "Save Dashboard & Launch"}
                  </button>
                </div>
              </div>
            )}

            {/* Wizard Prev / Next Navigation */}
            <div className="flex items-center justify-between pt-2">
              <button
                type="button"
                disabled={step === 1}
                onClick={() => setStep((s) => Math.max(1, s - 1))}
                className="px-4 py-2 rounded-xl border text-xs font-semibold flex items-center gap-1.5 disabled:opacity-35"
                style={{ borderColor: "var(--bi-border)", background: "var(--bi-card)" }}
              >
                <ArrowRight className={`w-4 h-4 ${isFa ? "" : "rotate-180"}`} />
                {isFa ? "مرحله قبل" : "Previous"}
              </button>

              {step < 9 && (
                <button
                  type="button"
                  onClick={() => {
                    if (step === 6 && !plan) {
                      handleGenerateDashboard();
                    } else {
                      setStep((s) => Math.min(9, s + 1));
                    }
                  }}
                  className="px-5 py-2 rounded-xl text-xs font-bold text-white flex items-center gap-1.5"
                  style={{ background: "var(--bi-primary)" }}
                >
                  <span>{step === 6 && !plan ? (isFa ? "تولید و ادامه به پیش‌نمایش" : "Generate & Next") : (isFa ? "مرحله بعد" : "Next")}</span>
                  <ArrowLeft className={`w-4 h-4 ${isFa ? "" : "rotate-180"}`} />
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
