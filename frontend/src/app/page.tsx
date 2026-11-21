"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, Bot, Database, FolderKanban, Layers, Plus, RefreshCw, ShieldCheck, Sparkles } from "lucide-react";
import AppShell from "@/components/layout/AppShell";
import WidgetRenderer from "@/components/charts/WidgetRenderer";
import { apiFetch } from "@/lib/api";
import { useBi } from "@/lib/context";

export default function HomePage() {
  const { user, locale, t, showToast, authStatus } = useBi();
  const isFa = locale === "fa";
  const [dashboards, setDashboards] = useState<any[]>([]);
  const [datasets, setDatasets] = useState<any[]>([]);
  const [etlMon, setEtlMon] = useState<any>(null);
  const [aiDatasetId, setAiDatasetId] = useState("");
  const [generating, setGenerating] = useState(false);

  // Conversational Text-to-Widget Builder state on Home
  const [targetDashId, setTargetDashId] = useState("");
  const [twPrompt, setTwPrompt] = useState(
    "از روی منبع تعداد یک ویجت میله ای بر اساس نام پیمانکار بساز تحت عنوان عملکرد پیمانکار"
  );
  const [twClarifications, setTwClarifications] = useState<Record<string, any>>({});
  const [twAnswerDraft, setTwAnswerDraft] = useState("");
  const [twResponse, setTwResponse] = useState<any>(null);
  const [twHistory, setTwHistory] = useState<{ role: "user" | "assistant"; text: string }[]>([]);
  const [twLoading, setTwLoading] = useState(false);

  useEffect(() => {
    if (!user) return;
    apiFetch<any[]>("/dashboards")
      .then((ds) => {
        setDashboards(ds);
        if (ds[0]) setTargetDashId(ds[0].id);
      })
      .catch(() => {});
    apiFetch<any[]>("/datasets")
      .then((ds) => {
        setDatasets(ds);
        if (ds[0]) setAiDatasetId(ds[0].id);
      })
      .catch(() => {});
    apiFetch("/etl/monitor").then(setEtlMon).catch(() => {});
  }, [user]);

  const handleAiBuild = async () => {
    if (!aiDatasetId) return;
    setGenerating(true);
    try {
      const res = await apiFetch<{ dashboard_id: string }>("/ai/generate-dashboard", {
        method: "POST",
        body: JSON.stringify({ dataset_id: aiDatasetId }),
      });
      showToast(isFa ? "داشبورد هوشمند ساخته شد!" : "AI Dashboard generated!");
      window.location.href = `/dashboards/${res.dashboard_id}`;
    } catch (err: any) {
      showToast(err.message);
    } finally {
      setGenerating(false);
    }
  };

  const submitHomeTextToWidget = async (
    overridePrompt?: string,
    overrideClar?: Record<string, any>,
    userMessageForLog?: string
  ) => {
    const activePrompt = (overridePrompt !== undefined ? overridePrompt : twPrompt).trim();
    const activeClar = overrideClar !== undefined ? overrideClar : twClarifications;
    if (!activePrompt) return;

    if (userMessageForLog || overridePrompt !== undefined) {
      setTwHistory((prev) => [...prev, { role: "user", text: userMessageForLog || activePrompt }]);
    }

    setTwLoading(true);
    try {
      const res = await apiFetch<any>("/ai/text-to-widget", {
        method: "POST",
        body: JSON.stringify({
          prompt: activePrompt,
          dashboard_id: targetDashId || dashboards[0]?.id || undefined,
          clarifications: activeClar,
          create_widget: true,
        }),
      });
      setTwResponse(res);
      if (res.parsed) {
        setTwClarifications((prev) => ({
          ...prev,
          ...(res.parsed.dataset_id ? { dataset_id: res.parsed.dataset_id } : {}),
          ...(res.parsed.chart_type ? { chart_type: res.parsed.chart_type } : {}),
          ...(res.parsed.x_field ? { x_field: res.parsed.x_field } : {}),
          ...(res.parsed.y_field ? { y_field: res.parsed.y_field } : {}),
          ...(res.parsed.aggregation ? { aggregation: res.parsed.aggregation } : {}),
          ...(res.parsed.title ? { title: res.parsed.title } : {}),
        }));
      }
      if (res.message_fa) {
        setTwHistory((prev) => [...prev, { role: "assistant", text: res.message_fa }]);
      }
      if ((res.status === "created" || res.status === "ready") && res.widget) {
        const dashId = res.widget.dashboard_id || targetDashId || dashboards[0]?.id;
        if (typeof window !== "undefined" && dashId && authStatus?.environment === "vercel-preview-demo") {
          try {
            const rawCustom = localStorage.getItem(`bi_demo_custom_widgets_${dashId}`);
            const list: any[] = rawCustom ? JSON.parse(rawCustom) : [];
            localStorage.setItem(`bi_demo_custom_widgets_${dashId}`, JSON.stringify([...list, res.widget]));
          } catch {
            // ignore
          }
        }
        showToast(res.message_fa || (isFa ? "ویجت جدید ساخته شد" : "Widget created"));
      }
    } catch (err: any) {
      showToast(err.message);
    } finally {
      setTwLoading(false);
    }
  };

  return (
    <AppShell>
      <div className="space-y-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold">{t("welcome")}</h1>
            <p className="text-sm mt-1" style={{ color: "var(--bi-muted)" }}>
              Decoupled Analytical Warehouse · Granular RBAC & RLS · Live 30-Chart Designer
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Link
              href="/data-sources"
              className="px-3 sm:px-4 py-2 rounded-lg border text-xs font-semibold flex items-center gap-1.5"
              style={{ borderColor: "var(--bi-border)", background: "var(--bi-card)" }}
            >
              <Database className="w-4 h-4 shrink-0" /> {isFa ? "اتصال داده / آپلود اکسل" : "Connect Data / Upload Excel"}
            </Link>
            <Link
              href="/dashboards"
              className="px-3 sm:px-4 py-2 rounded-lg text-xs font-semibold text-white flex items-center gap-1.5"
              style={{ background: "var(--bi-primary)" }}
            >
              <Plus className="w-4 h-4 shrink-0" /> {isFa ? "باز کردن داشبوردها" : "Open Dashboards"}
            </Link>
          </div>
        </div>

        {/* Summary Cards */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
          <div className="bi-card p-3.5 sm:p-4">
            <div className="flex items-center justify-between text-xs" style={{ color: "var(--bi-muted)" }}>
              <span>{isFa ? "داشبوردهای در دسترس" : "Accessible Dashboards"}</span>
              <FolderKanban className="w-4 h-4 text-blue-500 shrink-0" />
            </div>
            <div className="text-xl sm:text-2xl font-bold mt-2">{dashboards.length}</div>
          </div>
          <div className="bi-card p-3.5 sm:p-4">
            <div className="flex items-center justify-between text-xs" style={{ color: "var(--bi-muted)" }}>
              <span>{isFa ? "دیتاست‌های انبار داده" : "Warehouse Datasets"}</span>
              <Layers className="w-4 h-4 text-emerald-500 shrink-0" />
            </div>
            <div className="text-xl sm:text-2xl font-bold mt-2">{datasets.length}</div>
          </div>
          <div className="bi-card p-3.5 sm:p-4">
            <div className="flex items-center justify-between text-xs" style={{ color: "var(--bi-muted)" }}>
              <span>{isFa ? "پایپ‌لاین‌های فعال ETL" : "ETL Pipelines Active"}</span>
              <RefreshCw className="w-4 h-4 text-amber-500 shrink-0" />
            </div>
            <div className="text-xl sm:text-2xl font-bold mt-2">{etlMon?.kpis?.pipelines_count ?? "—"}</div>
          </div>
          <div className="bi-card p-3.5 sm:p-4">
            <div className="flex items-center justify-between text-xs" style={{ color: "var(--bi-muted)" }}>
              <span>{isFa ? "نقش و سطح دسترسی" : "Security Posture"}</span>
              <ShieldCheck className="w-4 h-4 text-violet-500 shrink-0" />
            </div>
            <div className="text-xs sm:text-sm font-bold mt-2.5 truncate">
              {user?.effective_permissions?.is_super_admin ? "Super Admin" : (user?.effective_permissions?.roles || []).join(", ")}
            </div>
          </div>
        </div>

        {/* Conversational Text-to-Widget & KPI Copilot Card */}
        <div dir="rtl" className="bi-card p-4 sm:p-5 space-y-4 border border-violet-500/30">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl bg-violet-500/15 text-violet-500 flex items-center justify-center shrink-0">
                <Sparkles className="w-5 h-5" />
              </div>
              <div>
                <h2 className="text-sm font-bold">
                  ساخت ویجت و KPI با متن فارسی (دستیار مکالمه‌ای AI)
                </h2>
                <p className="text-xs mt-0.5" style={{ color: "var(--bi-muted)" }}>
                  ویجت یا شاخص مورد نظر را به فارسی بنویسید؛ در صورت ابهام در منبع، ستون یا شاخص، سیستم با پرسش و پاسخ فارسی راهنمایی می‌کند.
                </p>
              </div>
            </div>
            {dashboards.length > 0 && (
              <div className="flex items-center gap-2 text-xs">
                <span style={{ color: "var(--bi-muted)" }}>داشبورد مقصد:</span>
                <select
                  value={targetDashId}
                  onChange={(e) => setTargetDashId(e.target.value)}
                  className="px-2.5 py-1.5 rounded-lg border text-xs"
                  style={{ background: "var(--bi-surface)", color: "var(--bi-text)", borderColor: "var(--bi-border)" }}
                >
                  {dashboards.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.title}
                    </option>
                  ))}
                </select>
              </div>
            )}
          </div>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              setTwClarifications({});
              setTwHistory([]);
              setTwResponse(null);
              submitHomeTextToWidget(twPrompt, {}, twPrompt);
            }}
            className="space-y-2.5"
          >
            <div className="flex flex-col sm:flex-row gap-2">
              <input
                dir="rtl"
                value={twPrompt}
                onChange={(e) => setTwPrompt(e.target.value)}
                placeholder="مثال: از روی منبع تعداد یک ویجت میله ای بر اساس نام پیمانکار بساز تحت عنوان عملکرد پیمانکار"
                className="flex-1 px-3 py-2.5 rounded-lg border text-xs bg-transparent"
                style={{ borderColor: "var(--bi-border)" }}
              />
              <button
                type="submit"
                disabled={twLoading || !twPrompt.trim()}
                className="px-4 py-2.5 rounded-lg text-xs font-bold text-white bg-violet-600 hover:bg-violet-700 disabled:opacity-50 shrink-0"
              >
                {twLoading ? "در حال تحلیل..." : "تولید خودکار ویجت"}
              </button>
              {(twResponse || twHistory.length > 0) && (
                <button
                  type="button"
                  onClick={() => {
                    setTwClarifications({});
                    setTwAnswerDraft("");
                    setTwResponse(null);
                    setTwHistory([]);
                  }}
                  className="px-3 py-2.5 rounded-lg border text-xs shrink-0"
                  style={{ borderColor: "var(--bi-border)" }}
                >
                  شروع جدید
                </button>
              )}
            </div>
            <div className="flex flex-wrap gap-1.5">
              <button
                type="button"
                onClick={() =>
                  setTwPrompt("از روی منبع تعداد یک ویجت میله ای بر اساس نام پیمانکار بساز تحت عنوان عملکرد پیمانکار")
                }
                className="text-[11px] px-2.5 py-1 rounded-full border hover:bg-violet-500/10"
                style={{ borderColor: "var(--bi-border)" }}
              >
                تست نمونه تستی: عملکرد پیمانکار (منبع تعداد)
              </button>
              <button
                type="button"
                onClick={() =>
                  setTwPrompt("از روی منبع تعداد یک ویجت میله ای از مجموع تعداد پروژه بر اساس نام پیمانکار بساز تحت عنوان عملکرد پیمانکار")
                }
                className="text-[11px] px-2.5 py-1 rounded-full border hover:bg-violet-500/10"
                style={{ borderColor: "var(--bi-border)" }}
              >
                تست مستقیم: تعداد پروژه پیمانکاران
              </button>
              <button
                type="button"
                onClick={() =>
                  setTwPrompt("از روی منبع فروش یک ویجت میله ای از مجموع فروش بر اساس منطقه بساز تحت عنوان عملکرد مناطق")
                }
                className="text-[11px] px-2.5 py-1 rounded-full border hover:bg-violet-500/10"
                style={{ borderColor: "var(--bi-border)" }}
              >
                تست مستقیم: عملکرد مناطق فروش
              </button>
              <button
                type="button"
                onClick={() => setTwPrompt("یک ویجت برای مقایسه وضعیت شعب بساز")}
                className="text-[11px] px-2.5 py-1 rounded-full border hover:bg-violet-500/10"
                style={{ borderColor: "var(--bi-border)" }}
              >
                تست پرسش‌وپاسخ رفع ابهام
              </button>
            </div>
          </form>

          {twHistory.length > 0 && (
            <div className="space-y-1.5 max-h-40 overflow-y-auto pt-2 border-t" style={{ borderColor: "var(--bi-border)" }}>
              {twHistory.map((item, idx) => (
                <div
                  key={idx}
                  className={`p-2 rounded-lg text-xs ${
                    item.role === "user"
                      ? "bg-blue-500/10 text-blue-600 dark:text-blue-300"
                      : "bg-violet-500/10"
                  }`}
                >
                  <span className="font-bold me-1.5">{item.role === "user" ? "شما:" : "دستیار:"}</span>
                  {item.text}
                </div>
              ))}
            </div>
          )}

          {twResponse?.status === "needs_clarification" && twResponse.questions?.length > 0 && (
            <div className="p-3 rounded-xl border space-y-3 bg-amber-500/5 border-amber-500/30">
              <div className="text-xs font-bold text-amber-600 dark:text-amber-400">
                {twResponse.message_fa}
              </div>
              {twResponse.questions.map((q: any) => (
                <div key={q.key} className="space-y-1.5">
                  <div className="text-xs font-semibold">{q.question}</div>
                  <div className="flex flex-wrap gap-1.5">
                    {(q.options || []).map((opt: any) => {
                      const selected = twClarifications[q.key] === opt.value;
                      return (
                        <button
                          key={opt.value}
                          type="button"
                          disabled={twLoading}
                          onClick={() => {
                            const nextClar = { ...twClarifications, [q.key]: opt.value };
                            setTwClarifications(nextClar);
                            const allAnswered = twResponse.questions.every((item: any) => nextClar[item.key]);
                            if (allAnswered) {
                              submitHomeTextToWidget(twPrompt, nextClar, opt.label);
                            }
                          }}
                          className={`px-3 py-1.5 rounded-lg border text-xs font-medium transition flex items-center gap-1 ${
                            selected ? "bg-violet-600 text-white border-violet-600" : "hover:bg-violet-500/10"
                          }`}
                          style={{ borderColor: selected ? undefined : "var(--bi-border)" }}
                        >
                          <span>{opt.label}</span>
                          {opt.hint && <span className="opacity-70 text-[10px]">({opt.hint})</span>}
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}

              <div className="pt-2 border-t flex flex-col sm:flex-row gap-2" style={{ borderColor: "var(--bi-border)" }}>
                <input
                  dir="rtl"
                  value={twAnswerDraft}
                  onChange={(e) => setTwAnswerDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      const draft = twAnswerDraft.trim();
                      const nextClar = draft
                        ? { ...twClarifications, free_text: `${twClarifications.free_text || ""} ${draft}`.trim() }
                        : twClarifications;
                      setTwClarifications(nextClar);
                      setTwAnswerDraft("");
                      submitHomeTextToWidget(twPrompt, nextClar, draft || "ادامه با گزینه‌های انتخاب‌شده");
                    }
                  }}
                  placeholder="یا پاسخ خود را به فارسی تایپ کنید (مثلاً: امتیاز عملکرد)..."
                  className="flex-1 px-3 py-1.5 rounded-lg border text-xs bg-transparent"
                  style={{ borderColor: "var(--bi-border)" }}
                />
                <button
                  type="button"
                  disabled={twLoading}
                  onClick={() => {
                    const draft = twAnswerDraft.trim();
                    const nextClar = draft
                      ? { ...twClarifications, free_text: `${twClarifications.free_text || ""} ${draft}`.trim() }
                      : twClarifications;
                    setTwClarifications(nextClar);
                    setTwAnswerDraft("");
                    submitHomeTextToWidget(twPrompt, nextClar, draft || "ادامه با گزینه‌های انتخاب‌شده");
                  }}
                  className="px-4 py-1.5 rounded-lg text-xs font-bold text-white bg-amber-600 hover:bg-amber-700 disabled:opacity-50"
                >
                  {twLoading ? "در حال بررسی..." : "ارسال پاسخ و ادامه"}
                </button>
              </div>
            </div>
          )}

          {(twResponse?.status === "created" || twResponse?.status === "ready") && twResponse.preview_data && (
            <div className="p-3.5 rounded-xl border border-emerald-500/40 bg-emerald-500/5 space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="text-xs font-bold text-emerald-600 dark:text-emerald-400">
                  {twResponse.message_fa}
                </div>
                {(twResponse.widget?.dashboard_id || targetDashId) && (
                  <Link
                    href={`/dashboards/${twResponse.widget?.dashboard_id || targetDashId}?ai=1`}
                    className="px-3 py-1.5 rounded-lg text-xs font-semibold text-white bg-emerald-600 hover:bg-emerald-700"
                  >
                    مشاهده ویجت در داشبورد
                  </Link>
                )}
              </div>
              <div dir="ltr" className="font-mono text-[10px] p-2 rounded bg-slate-900/80 text-emerald-400 overflow-x-auto">
                {twResponse.generated_sql}
              </div>
              <div className="h-56">
                <WidgetRenderer
                  chartType={twResponse.widget_spec?.chart_type || "bar"}
                  columns={twResponse.preview_data.columns || []}
                  rows={twResponse.preview_data.rows || []}
                  styleConfig={twResponse.widget_spec?.style || {}}
                  vizConfig={twResponse.widget_spec?.visualization || {}}
                  visualSpec={twResponse.widget_spec?.visual_spec}
                />
              </div>
            </div>
          )}
        </div>

        {/* AI 1-Click Dashboard Builder Banner */}
        {datasets.length > 0 && (
          <div className="bi-card p-4 sm:p-5 flex flex-col md:flex-row md:items-center justify-between gap-3 sm:gap-4">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl bg-violet-500/15 text-violet-500 flex items-center justify-center shrink-0">
                <Bot className="w-5 h-5" />
              </div>
              <div className="min-w-0">
                <h2 className="text-sm font-bold">{isFa ? "ساخت داشبورد هوشمند با AI" : "AI Assisted Dashboard Generator"}</h2>
                <p className="text-xs" style={{ color: "var(--bi-muted)" }}>
                  {isFa
                    ? "تحلیل خودکار ساختار دیتاست، استخراج شاخص‌ها و ساخت داشبورد آماده ویرایش."
                    : "Automatically profiles dataset schema, extracts KPIs, sets drill rules & builds a full editable dashboard."}
                </p>
              </div>
            </div>
            <div className="flex flex-wrap sm:flex-nowrap items-center gap-2 w-full md:w-auto">
              <select
                value={aiDatasetId}
                onChange={(e) => setAiDatasetId(e.target.value)}
                className="px-3 py-2 rounded-lg border text-xs flex-1 md:flex-initial min-w-0"
                style={{ background: "var(--bi-surface)", color: "var(--bi-text)", borderColor: "var(--bi-border)" }}
              >
                {datasets.map((d) => <option key={d.id} value={d.id}>{d.name} ({d.row_count} rows)</option>)}
              </select>
              <button
                onClick={handleAiBuild}
                disabled={generating}
                className="px-4 py-2 rounded-lg text-xs font-semibold text-white bg-violet-600 hover:bg-violet-700 w-full sm:w-auto shrink-0"
              >
                {generating ? "..." : isFa ? "تولید خودکار داشبورد" : "Auto-Generate Dashboard"}
              </button>
            </div>
          </div>
        )}

        {/* Dashboards Grid */}
        <div>
          <h2 className="text-sm font-bold mb-3">{isFa ? "داشبوردهای سازمانی" : "Enterprise Dashboards"}</h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 sm:gap-4">
            {dashboards.map((d) => (
              <Link
                key={d.id}
                href={`/dashboards/${d.id}`}
                className="bi-card p-4 sm:p-5 hover:scale-[1.01] transition flex flex-col justify-between gap-3 sm:gap-4"
              >
                <div>
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-bold">{d.title}</span>
                    {d.is_template && <span className="text-[10px] px-2 py-0.5 rounded bg-violet-500/15 text-violet-500 font-semibold shrink-0">TEMPLATE</span>}
                  </div>
                  <p className="text-xs mt-1.5 line-clamp-2" style={{ color: "var(--bi-muted)" }}>{d.description}</p>
                </div>
                <div className="flex items-center justify-between text-xs font-semibold" style={{ color: "var(--bi-primary)" }}>
                  <span>{d.widget_count} {isFa ? "ویجت" : "widgets"}</span>
                  <span className="flex items-center gap-1">
                    {isFa ? "باز کردن داشبورد" : "Open Workspace"} <ArrowRight className={`w-3.5 h-3.5 ${isFa ? "rotate-180" : ""}`} />
                  </span>
                </div>
              </Link>
            ))}
          </div>
        </div>
      </div>
    </AppShell>
  );
}
