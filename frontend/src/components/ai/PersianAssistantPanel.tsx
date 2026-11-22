"use client";

import React, { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Sparkles, CheckCircle2, ArrowLeft, Wand2, LayoutDashboard, Pencil, PlusCircle, RotateCcw, FileSpreadsheet, Upload } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { useBi } from "@/lib/context";
import {
  addDemoCustomWidget,
  getDemoCreatedDashboards,
  getDemoDeletedWidgetIds,
  getDemoWidgetEdits,
  markDemoWidgetDeleted,
  saveDemoCreatedDashboard,
  saveDemoDashOverride,
  saveDemoWidgetEdit,
} from "@/lib/demoStore";
import WidgetRenderer from "@/components/charts/WidgetRenderer";

interface AssistantPanelProps {
  dashboardId?: string | null;
  pageId?: string | null;
  initialWidgetId?: string | null;
  initialMode?: "auto" | "create_dashboard" | "add_widget" | "edit";
  currentWidgets?: any[];
  onApplied?: (result: any) => void;
  compact?: boolean;
}

const QUICK_IDEAS = [
  {
    label: "ساخت داشبورد کامل پیمانکاران",
    mode: "create_dashboard" as const,
    prompt: "می‌خواهم یک داشبورد کامل مدیریتی برای پیمانکاران بسازم",
  },
  {
    label: "ساخت داشبورد فروش (با انتخاب ظاهر)",
    mode: "create_dashboard" as const,
    prompt: "یک داشبورد برای گزارش فروش بساز",
  },
  {
    label: "افزودن نمودار با توضیح فارسی",
    mode: "add_widget" as const,
    prompt: "از روی منبع تعداد یک نمودار میله ای برای عملکرد پیمانکاران بر اساس نام پیمانکار بساز",
  },
  {
    label: "ویرایش رنگ، نام یا نمودارها",
    mode: "edit" as const,
    prompt: "می‌خواهم این داشبورد یا یکی از نمودارهایش را ویرایش کنم",
  },
];

export function PersianAssistantPanel({
  dashboardId,
  pageId,
  initialWidgetId,
  initialMode = "auto",
  currentWidgets,
  onApplied,
  compact = false,
}: AssistantPanelProps) {
  const router = useRouter();
  const { showToast, authStatus } = useBi();

  const [mode, setMode] = useState<"auto" | "create_dashboard" | "add_widget" | "edit">(initialMode);
  const [prompt, setPrompt] = useState("");
  const [clarifications, setClarifications] = useState<Record<string, string>>({});
  const [freeTextReply, setFreeTextReply] = useState("");
  const [loading, setLoading] = useState(false);
  const [excelUploading, setExcelUploading] = useState(false);
  const [uploadedDataset, setUploadedDataset] = useState<{ id: string; name: string; row_count: number; columns_count?: number } | null>(null);
  const [result, setResult] = useState<any>(null);
  const [targetWidgetId, setTargetWidgetId] = useState<string | null>(initialWidgetId || null);
  const excelInputRef = useRef<HTMLInputElement | null>(null);

  const handleQuickExcelUpload = async (file: File | undefined) => {
    if (!file) return;
    setExcelUploading(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const cleanName = file.name.replace(/\.[^.]+$/, "").trim() || "Excel Data";
      fd.append("dataset_name", cleanName);
      fd.append("has_header", "true");
      fd.append("null_strategy", "keep");
      fd.append("deduplicate", "false");
      const res = await apiFetch<any>("/data-sources/files/import", {
        method: "POST",
        body: fd,
      });
      const dsId = res.dataset_id || res.id;
      const dsName = res.name || cleanName;
      setUploadedDataset({
        id: dsId,
        name: dsName,
        row_count: Number(res.row_count || 0),
        columns_count: Number(res.columns_count || res.fields?.length || 0),
      });
      setMode("create_dashboard");
      setPrompt(`از فایل اکسل «${dsName}» یک داشبورد کامل بساز`);
      setClarifications({ dataset_id: dsId });
      setResult(null);
      showToast(`فایل «${dsName}» با ${res.row_count} ردیف با موفقیت وارد شد!`);
    } catch (e: any) {
      showToast(e.message || "خطا در خواندن فایل اکسل");
    } finally {
      setExcelUploading(false);
      if (excelInputRef.current) excelInputRef.current.value = "";
    }
  };

  const buildDashboardFromUploaded = async () => {
    if (!uploadedDataset) return;
    setLoading(true);
    try {
      const res = await apiFetch<any>("/ai/assistant", {
        method: "POST",
        body: JSON.stringify({
          prompt: `از روی داده‌های «${uploadedDataset.name}» یک داشبورد کامل و آماده با عنوان «گزارش ${uploadedDataset.name}» بساز`,
          mode: "create_dashboard",
          clarifications: {
            dataset_id: uploadedDataset.id,
            layout_preset: "executive_full",
            theme_id: "__default__",
            title: `داشبورد ${uploadedDataset.name}`,
          },
          demo_context: buildDemoContext(),
        }),
      });
      setResult(res);
      if (res.status !== "needs_clarification") {
        persistDemoResult(res);
        showToast(res.message_fa || "داشبورد با موفقیت ساخته شد!");
        onApplied?.(res);
      }
    } catch (e: any) {
      showToast(e.message || "خطا در ساخت داشبورد از فایل اکسل");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (initialWidgetId) {
      setTargetWidgetId(initialWidgetId);
      setMode("edit");
      setResult(null);
      setClarifications({});
      const matched = (currentWidgets || []).find((w) => w.id === initialWidgetId);
      setPrompt(matched ? `می‌خواهم «${matched.title}» را تغییر دهم` : "می‌خواهم این نمودار را ویرایش کنم");
    }
  }, [initialWidgetId, currentWidgets]);

  const buildDemoContext = () => {
    const customDashes = getDemoCreatedDashboards();
    let effectiveWidgets = currentWidgets;
    if (effectiveWidgets && dashboardId) {
      const delIds = new Set(getDemoDeletedWidgetIds(dashboardId));
      const edits = getDemoWidgetEdits(dashboardId);
      effectiveWidgets = effectiveWidgets
        .filter((w) => !delIds.has(w.id))
        .map((w) => (edits[w.id] ? { ...w, ...edits[w.id] } : w));
    }
    return {
      custom_dashboards: customDashes,
      ...(effectiveWidgets ? { widgets: effectiveWidgets } : {}),
    };
  };

  const persistDemoResult = (res: any) => {
    if (!res) return;
    const isDemo = authStatus?.environment === "vercel-preview-demo" || typeof window !== "undefined";
    if (!isDemo) return;

    if (res.status === "dashboard_created" && res.created_dashboard) {
      if (authStatus?.environment === "vercel-preview-demo") {
        saveDemoCreatedDashboard(res.created_dashboard);
      }
    } else if (res.status === "dashboard_updated" && res.updated_dashboard) {
      if (authStatus?.environment === "vercel-preview-demo") {
        const u = res.updated_dashboard;
        saveDemoDashOverride(u.id, {
          title: u.title,
          theme_id: u.theme_id,
          add_filter: u.add_filter,
        });
      }
    } else if (res.status === "widget_updated" && res.widget) {
      if (authStatus?.environment === "vercel-preview-demo") {
        const w = res.widget;
        saveDemoWidgetEdit(w.dashboard_id || dashboardId || "", w.id, {
          title: w.title,
          chart_type: w.chart_type,
          visual_spec: w.visual_spec,
        });
      }
    } else if (res.status === "widget_deleted" && res.deleted_widget_id) {
      if (authStatus?.environment === "vercel-preview-demo") {
        markDemoWidgetDeleted(res.dashboard_id || dashboardId || "", res.deleted_widget_id);
      }
    } else if (res.status === "created" && res.widget && (res.widget.dashboard_id || dashboardId)) {
      if (authStatus?.environment === "vercel-preview-demo") {
        const dId = res.widget.dashboard_id || dashboardId || "";
        addDemoCustomWidget(dId, {
          id: res.widget.id,
          dashboard_id: dId,
          page_id: res.widget.page_id || pageId || "page-1",
          title: res.widget.title || res.widget_spec?.title || "نمودار جدید",
          description: "ساخته‌شده با راهنمای فارسی",
          chart_type: res.widget.chart_type || res.widget_spec?.chart_type || "bar",
          dataset_id: res.widget.dataset_id || res.widget_spec?.dataset_id,
          dataset_freshness: {
            status: "fresh",
            last_refreshed_at: new Date().toISOString(),
            row_count: res.preview_data?.row_count || 0,
            data_version: 1,
          },
          layout: res.widget.layout || { x: 0, y: 20, w: 6, h: 7 },
          config: {
            style: res.widget_spec?.style || {},
            visualization: res.widget_spec?.visualization || { show_legend: true, show_tooltip: true },
            interactions: { cross_filter: true },
            advanced: { ai_text_prompt: prompt },
          },
          query: {
            query_mode: "visual",
            sql_text: res.generated_sql || null,
            visual_spec: res.widget_spec?.visual_spec || {},
            parameters: {},
          },
          drill_rule: { hierarchy: [], level_rules: [], drill_through_page_id: null },
          widget_filters: [],
          permissions: { view: true, edit: true, delete: true, drill_down: true },
        } as any);
      }
    }
  };

  const runAssistant = async (
    overridePrompt?: string,
    overrideMode?: "auto" | "create_dashboard" | "add_widget" | "edit",
    overrideClar?: Record<string, string>
  ) => {
    const pText = (overridePrompt ?? prompt).trim();
    const effMode = overrideMode ?? mode;
    const effClar = overrideClar ?? clarifications;
    if (!pText && effMode === "auto") {
      showToast("لطفاً به زبان ساده بنویسید چه کاری می‌خواهید انجام دهید.");
      return;
    }
    const finalPrompt =
      pText ||
      (effMode === "create_dashboard"
        ? "می‌خواهم یک داشبورد جدید بسازم"
        : effMode === "edit"
        ? "می‌خواهم داشبورد یا نمودار را ویرایش کنم"
        : "یک نمودار جدید بساز");

    setLoading(true);
    try {
      const payloadClar = {
        ...effClar,
        ...(freeTextReply.trim() ? { free_text: freeTextReply.trim() } : {}),
      };
      const res = await apiFetch<any>("/ai/assistant", {
        method: "POST",
        body: JSON.stringify({
          prompt: finalPrompt,
          dashboard_id: dashboardId || effClar.dashboard_id || undefined,
          page_id: pageId || undefined,
          widget_id: targetWidgetId || undefined,
          mode: effMode,
          clarifications: payloadClar,
          demo_context: buildDemoContext(),
        }),
      });
      setResult(res);
      setFreeTextReply("");

      if (res.status !== "needs_clarification") {
        persistDemoResult(res);
        showToast(res.message_fa || "انجام شد!");
        onApplied?.(res);
      }
    } catch (e: any) {
      showToast(e.message || "خطا در انجام درخواست");
    } finally {
      setLoading(false);
    }
  };

  const resetChat = () => {
    setPrompt("");
    setClarifications({});
    setFreeTextReply("");
    setResult(null);
    setTargetWidgetId(null);
    setMode("auto");
  };

  const handleOptionSelect = (key: string, val: string) => {
    const nextClar: Record<string, string> = {
      ...clarifications,
      ...(result?.parsed?.mode ? { mode: result.parsed.mode } : {}),
      ...(result?.parsed?.dashboard_id ? { dashboard_id: result.parsed.dashboard_id } : {}),
      ...(result?.parsed?.target_widget_id ? { target_widget_id: result.parsed.target_widget_id } : {}),
      [key]: val,
    };
    setClarifications(nextClar);
    const hasMoreQuestions = (result?.questions || []).length > 1;
    if (!hasMoreQuestions) {
      const nextMode = key === "mode" ? (val as any) : mode;
      if (key === "mode") setMode(nextMode);
      runAssistant(prompt, nextMode, nextClar);
    }
  };

  return (
    <div
      dir="rtl"
      className="bi-card p-4 sm:p-5 space-y-4 border border-violet-500/30"
      style={{ fontFamily: "Vazirmatn, Inter, sans-serif" }}
    >
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2.5">
          <span className="w-9 h-9 rounded-xl bg-violet-600 flex items-center justify-center text-white shrink-0">
            <Wand2 className="w-5 h-5" />
          </span>
          <div>
            <h2 className="text-sm sm:text-base font-bold">
              دستیار هوشمند ساخت و ویرایش داشبورد (بدون نیاز به دانش فنی)
            </h2>
            <p className="text-xs mt-0.5" style={{ color: "var(--bi-muted)" }}>
              فقط به فارسی ساده بنویسید چه می‌خواهید؛ در صورت نیاز، دستیار با چند گزینه ساده راهنمایی‌تان می‌کند.
            </p>
          </div>
        </div>
        {(result || prompt) && (
          <button
            type="button"
            onClick={resetChat}
            className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg border text-xs font-medium"
            style={{ borderColor: "var(--bi-border)", color: "var(--bi-muted)" }}
          >
            <RotateCcw className="w-3.5 h-3.5" />
            شروع مکالمه جدید
          </button>
        )}
      </div>

      {/* 3 Friendly Action Mode Buttons */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
        <button
          type="button"
          onClick={() => {
            setMode("create_dashboard");
            setClarifications({});
            setResult(null);
            setPrompt((p) => p || "می‌خواهم یک داشبورد جدید بسازم");
          }}
          className={`flex items-center gap-2 px-3 py-2.5 rounded-lg border text-xs font-semibold text-right transition-all ${
            mode === "create_dashboard" ? "bg-violet-600/15 border-violet-500 text-violet-500" : ""
          }`}
          style={mode === "create_dashboard" ? undefined : { borderColor: "var(--bi-border)", backgroundColor: "var(--bi-surface)" }}
        >
          <LayoutDashboard className="w-4 h-4 shrink-0" />
          <span>ساخت داشبورد کامل جدید</span>
        </button>

        <button
          type="button"
          onClick={() => {
            setMode("add_widget");
            setClarifications({});
            setResult(null);
            if (!prompt) setPrompt("یک نمودار جدید بساز");
          }}
          className={`flex items-center gap-2 px-3 py-2.5 rounded-lg border text-xs font-semibold text-right transition-all ${
            mode === "add_widget" ? "bg-violet-600/15 border-violet-500 text-violet-500" : ""
          }`}
          style={mode === "add_widget" ? undefined : { borderColor: "var(--bi-border)", backgroundColor: "var(--bi-surface)" }}
        >
          <PlusCircle className="w-4 h-4 shrink-0" />
          <span>افزودن یک نمودار یا آمار</span>
        </button>

        <button
          type="button"
          onClick={() => {
            setMode("edit");
            setClarifications({});
            setResult(null);
            setPrompt((p) => p || "می‌خواهم داشبورد یا نمودار را ویرایش کنم");
          }}
          className={`flex items-center gap-2 px-3 py-2.5 rounded-lg border text-xs font-semibold text-right transition-all ${
            mode === "edit" ? "bg-violet-600/15 border-violet-500 text-violet-500" : ""
          }`}
          style={mode === "edit" ? undefined : { borderColor: "var(--bi-border)", backgroundColor: "var(--bi-surface)" }}
        >
          <Pencil className="w-4 h-4 shrink-0" />
          <span>ویرایش داشبورد یا نمودار</span>
        </button>
      </div>

      {/* Direct Excel / CSV Upload Bar */}
      <div
        className="rounded-xl border border-dashed p-3 flex flex-wrap items-center justify-between gap-2.5"
        style={{ borderColor: "var(--bi-border)", backgroundColor: "var(--bi-surface)" }}
      >
        <input
          ref={excelInputRef}
          type="file"
          accept=".xlsx,.xls,.csv,.tsv,.json"
          className="hidden"
          onChange={(e) => handleQuickExcelUpload(e.target.files?.[0])}
        />
        <div className="flex items-center gap-2.5 min-w-0">
          <span className="w-8 h-8 rounded-lg bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 flex items-center justify-center shrink-0">
            <FileSpreadsheet className="w-4 h-4" />
          </span>
          <div className="min-w-0">
            <p className="text-xs font-semibold">
              {uploadedDataset
                ? `فایل «${uploadedDataset.name}» آماده است (${uploadedDataset.row_count.toLocaleString()} ردیف${uploadedDataset.columns_count ? ` · ${uploadedDataset.columns_count} ستون` : ""})`
                : "فایل اکسل یا CSV خودتان را دارید؟ همین‌جا بارگذاری کنید"}
            </p>
            <p className="text-[11px]" style={{ color: "var(--bi-muted)" }}>
              پشتیبانی از ستون‌های فارسی و انگلیسی (xlsx, xls, csv) — بلافاصله می‌توانید از آن داشبورد بسازید
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={excelUploading}
            onClick={() => excelInputRef.current?.click()}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-semibold hover:bg-emerald-500/10"
            style={{ borderColor: "var(--bi-border)" }}
          >
            <Upload className="w-3.5 h-3.5" />
            {excelUploading ? "در حال خواندن فایل..." : uploadedDataset ? "بارگذاری فایل دیگر" : "انتخاب فایل اکسل"}
          </button>
          {uploadedDataset && (
            <button
              type="button"
              disabled={loading}
              onClick={buildDashboardFromUploaded}
              className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-semibold text-white bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60"
            >
              <Sparkles className="w-3.5 h-3.5" />
              ساخت فوری داشبورد از این فایل
            </button>
          )}
        </div>
      </div>

      {/* Quick Starter Chips */}
      {!compact && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] font-medium" style={{ color: "var(--bi-muted)" }}>
            نمونه‌های آماده:
          </span>
          {QUICK_IDEAS.map((idea) => (
            <button
              key={idea.label}
              type="button"
              onClick={() => {
                setMode(idea.mode);
                setPrompt(idea.prompt);
                setClarifications({});
                setResult(null);
              }}
              className="px-2.5 py-1 rounded-full border text-[11px] hover:bg-violet-500/10"
              style={{ borderColor: "var(--bi-border)" }}
            >
              {idea.label}
            </button>
          ))}
        </div>
      )}

      {/* Main Prompt Input */}
      <div className="flex flex-col sm:flex-row gap-2">
        <textarea
          dir="rtl"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={2}
          placeholder="مثال: یک داشبورد تیره برای پیمانکاران بساز — یا: نمودار فروش را تبدیل به دایره‌ای کن — یا: عنوان داشبورد را بگذار گزارش ماهانه"
          className="flex-1 px-3 py-2.5 rounded-lg border text-xs sm:text-sm outline-none resize-none bg-transparent"
          style={{ borderColor: "var(--bi-border)" }}
        />
        <button
          type="button"
          disabled={loading}
          onClick={() => {
            setClarifications({});
            runAssistant(prompt, mode, {});
          }}
          className="px-5 py-2.5 rounded-lg text-xs sm:text-sm font-semibold text-white bg-violet-600 hover:bg-violet-700 flex items-center justify-center gap-1.5 shrink-0 disabled:opacity-60"
        >
          <Sparkles className="w-4 h-4" />
          {loading ? "در حال بررسی..." : "شروع / اجرا"}
        </button>
      </div>

      {/* Clarification Q&A Block */}
      {result?.status === "needs_clarification" && (
        <div className="rounded-xl border border-amber-500/40 bg-amber-500/5 p-4 space-y-3">
          <p className="text-xs sm:text-sm font-bold text-amber-600 dark:text-amber-400">
            {result.message_fa}
          </p>

          {(result.questions || []).map((q: any) => (
            <div key={q.key} className="space-y-1.5">
              <label className="block text-xs font-semibold">
                {q.question}
              </label>
              <div className="flex flex-wrap gap-1.5">
                {(q.options || []).map((opt: any) => {
                  const selected = clarifications[q.key] === opt.value;
                  return (
                    <button
                      key={opt.value}
                      type="button"
                      disabled={loading}
                      onClick={() => handleOptionSelect(q.key, opt.value)}
                      className={`px-3 py-1.5 rounded-lg border text-xs text-right transition-all ${
                        selected ? "bg-violet-600 text-white border-violet-600" : "hover:bg-violet-500/10"
                      }`}
                      style={{
                        borderColor: selected ? undefined : "var(--bi-border)",
                        backgroundColor: selected ? undefined : "var(--bi-surface)",
                      }}
                    >
                      <span className="font-semibold">{opt.label}</span>
                      {opt.hint && <span className="opacity-75 text-[10px] mr-1">({opt.hint})</span>}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}

          {/* Natural Persian Reply Box */}
          <div className="pt-2 border-t flex flex-col sm:flex-row gap-2" style={{ borderColor: "var(--bi-border)" }}>
            <input
              dir="rtl"
              type="text"
              value={freeTextReply}
              onChange={(e) => setFreeTextReply(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  runAssistant(prompt, result?.parsed?.mode || mode, {
                    ...clarifications,
                    ...(result?.parsed?.mode ? { mode: result.parsed.mode } : {}),
                    ...(result?.parsed?.dashboard_id ? { dashboard_id: result.parsed.dashboard_id } : {}),
                    ...(result?.parsed?.target_widget_id ? { target_widget_id: result.parsed.target_widget_id } : {}),
                  });
                }
              }}
              placeholder="یا با زبان خودتان اینجا بنویسید (مثلاً: عنوانش را بگذار آمار پیمانکاران و دایره‌ای کن)..."
              className="flex-1 px-3 py-2 rounded-lg border text-xs outline-none bg-transparent"
              style={{ borderColor: "var(--bi-border)" }}
            />
            <button
              type="button"
              disabled={loading}
              onClick={() =>
                runAssistant(prompt, result?.parsed?.mode || mode, {
                  ...clarifications,
                  ...(result?.parsed?.mode ? { mode: result.parsed.mode } : {}),
                  ...(result?.parsed?.dashboard_id ? { dashboard_id: result.parsed.dashboard_id } : {}),
                  ...(result?.parsed?.target_widget_id ? { target_widget_id: result.parsed.target_widget_id } : {}),
                })
              }
              className="px-4 py-2 rounded-lg text-xs font-semibold text-white bg-amber-600 hover:bg-amber-700 shrink-0"
            >
              ادامه و اعمال
            </button>
          </div>
        </div>
      )}

      {/* Success: Dashboard Created */}
      {result?.status === "dashboard_created" && result.created_dashboard && (
        <div className="rounded-xl border border-emerald-500/40 bg-emerald-500/5 p-4 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2 text-emerald-600 dark:text-emerald-400">
              <CheckCircle2 className="w-5 h-5 shrink-0" />
              <span className="text-sm font-bold">{result.message_fa}</span>
            </div>
            <button
              type="button"
              onClick={() => router.push(`/dashboards/${result.created_dashboard.id}`)}
              className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold text-white bg-emerald-600 hover:bg-emerald-700"
            >
              <span>مشاهده داشبورد جدید</span>
              <ArrowLeft className="w-3.5 h-3.5" />
            </button>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {(result.created_dashboard.widgets || []).map((w: any) => (
              <span
                key={w.id}
                className="px-2.5 py-1 rounded-md border text-[11px]"
                style={{ borderColor: "var(--bi-border)", backgroundColor: "var(--bi-surface)" }}
              >
                {w.title} {w.chart_label_fa ? `(${w.chart_label_fa})` : ""}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Success: Dashboard Updated */}
      {result?.status === "dashboard_updated" && (
        <div className="rounded-xl border border-emerald-500/40 bg-emerald-500/5 p-4 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-emerald-600 dark:text-emerald-400 text-xs sm:text-sm font-semibold">
            <CheckCircle2 className="w-5 h-5 shrink-0" />
            <span>{result.message_fa}</span>
          </div>
          {result.updated_dashboard?.id && (
            <Link
              href={`/dashboards/${result.updated_dashboard.id}`}
              className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-semibold text-white bg-emerald-600"
            >
              <span>باز کردن داشبورد</span>
              <ArrowLeft className="w-3.5 h-3.5" />
            </Link>
          )}
        </div>
      )}

      {/* Success: Widget Deleted */}
      {result?.status === "widget_deleted" && (
        <div className="rounded-xl border border-emerald-500/40 bg-emerald-500/5 p-4 flex items-center gap-2 text-emerald-600 dark:text-emerald-400 text-xs sm:text-sm font-semibold">
          <CheckCircle2 className="w-5 h-5 shrink-0" />
          <span>{result.message_fa}</span>
        </div>
      )}

      {/* Success: Widget Created or Updated with Live Preview */}
      {(result?.status === "created" || result?.status === "ready" || result?.status === "widget_updated") &&
        result?.widget_spec && (
          <div className="rounded-xl border border-emerald-500/40 bg-emerald-500/5 p-4 space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2 text-emerald-600 dark:text-emerald-400 text-xs sm:text-sm font-semibold">
                <CheckCircle2 className="w-5 h-5 shrink-0" />
                <span>{result.message_fa}</span>
              </div>
              {(result.widget?.dashboard_id || dashboardId) && (
                <Link
                  href={`/dashboards/${result.widget?.dashboard_id || dashboardId}`}
                  className="inline-flex items-center gap-1 text-xs font-semibold hover:underline"
                  style={{ color: "var(--bi-primary)" }}
                >
                  <span>مشاهده در داشبورد</span>
                  <ArrowLeft className="w-3.5 h-3.5" />
                </Link>
              )}
            </div>
            {result.preview_data && (
              <div className="h-56 rounded-lg border p-2" style={{ backgroundColor: "var(--bi-surface)", borderColor: "var(--bi-border)" }}>
                <WidgetRenderer
                  chartType={result.widget_spec.chart_type || "bar"}
                  columns={result.preview_data.columns || []}
                  rows={result.preview_data.rows || []}
                  styleConfig={result.widget_spec.style || {}}
                  vizConfig={result.widget_spec.visualization || { show_legend: true, show_tooltip: true }}
                  visualSpec={result.widget_spec.visual_spec}
                />
              </div>
            )}
          </div>
        )}
    </div>
  );
}
