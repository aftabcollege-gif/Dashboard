"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Clock,
  Database,
  GitBranch,
  Layers,
  ListChecks,
  Loader2,
  Plus,
  RefreshCw,
  Search,
  Sliders,
  Workflow,
  X,
} from "lucide-react";
import AppShell from "@/components/layout/AppShell";
import { apiFetch } from "@/lib/api";
import { useBi } from "@/lib/context";

const MAPPING_ROLES = [
  { key: "case_id", labelFa: "شناسه پرونده (Case ID)*", required: true },
  { key: "activity", labelFa: "فعالیت / گام (Activity)*", required: true },
  { key: "timestamp", labelFa: "زمان رویداد (Timestamp)*", required: true },
  { key: "start_time", labelFa: "زمان شروع گام", required: false },
  { key: "end_time", labelFa: "زمان پایان گام", required: false },
  { key: "wait_hours", labelFa: "مدت انتظار (ساعت)", required: false },
  { key: "sla_hours", labelFa: "سقف مجاز SLA (ساعت)", required: false },
  { key: "actor", labelFa: "مسئول / مجری (Actor)", required: false },
  { key: "department", labelFa: "واحد / دپارتمان", required: false },
  { key: "status", labelFa: "وضعیت", required: false },
  { key: "priority", labelFa: "اولویت", required: false },
  { key: "rework_flag", labelFa: "پرچم دوباره‌کاری", required: false },
];

export default function ProcessIntelligencePage() {
  const { locale, showToast } = useBi();
  const isFa = locale === "fa";

  const [datasets, setDatasets] = useState<any[]>([]);
  const [datasetId, setDatasetId] = useState("");
  const [processList, setProcessList] = useState<{ process_name: string; total_count: number }[]>([]);
  const [selectedProcess, setSelectedProcess] = useState<string>("");
  const [mappingInfo, setMappingInfo] = useState<any>(null);
  const [mappingEdits, setMappingEdits] = useState<Record<string, string>>({});
  const [refSequenceText, setRefSequenceText] = useState("");
  const [analysis, setAnalysis] = useState<any>(null);
  const [actions, setActions] = useState<any[]>([]);
  const [rulePresets, setRulePresets] = useState<any[]>([]);
  const [activeRules, setActiveRules] = useState<any[]>([]);
  const [selectedPresetKey, setSelectedPresetKey] = useState<string>("weighted_wait_sla");
  const [loading, setLoading] = useState(false);
  const [savingMap, setSavingMap] = useState(false);

  // Drill-down cases
  const [caseModalOpen, setCaseModalOpen] = useState(false);
  const [caseFilterAct, setCaseFilterAct] = useState("");
  const [casesList, setCasesList] = useState<any[]>([]);
  const [selectedCaseTrace, setSelectedCaseTrace] = useState<any>(null);

  // Corrective action form
  const [actTitle, setActTitle] = useState("");
  const [actTarget, setActTarget] = useState("");
  const [actMetric, setActMetric] = useState("avg_cycle_hours");
  const [baseStart, setBaseStart] = useState("");
  const [baseEnd, setBaseEnd] = useState("");
  const [postStart, setPostStart] = useState("");
  const [postEnd, setPostEnd] = useState("");

  useEffect(() => {
    apiFetch<any[]>("/datasets")
      .then((rows) => {
        setDatasets(rows || []);
        const preferred =
          rows.find((d) => /bpmn|process|workflow|فرآیند|فرایند/i.test(`${d.name} ${d.description || ""}`)) ||
          rows[0];
        if (preferred) setDatasetId(preferred.id);
      })
      .catch(() => {});
  }, []);

  const loadProcessIntelligence = useCallback(
    async (dsId: string, procName: string = "") => {
      if (!dsId) return;
      setLoading(true);
      try {
        const [mapRes, procRes, anaRes, actRes, rulesRes] = await Promise.all([
          apiFetch<any>(`/process-intelligence/datasets/${dsId}/mapping`),
          apiFetch<any>(`/process-intelligence/datasets/${dsId}/processes`).catch(() => ({ processes: [] })),
          apiFetch<any>(`/process-intelligence/datasets/${dsId}/analyze`, {
            method: "POST",
            body: JSON.stringify(procName ? { process_name: procName } : {}),
          }),
          apiFetch<any[]>(`/process-intelligence/datasets/${dsId}/actions`).catch(() => []),
          apiFetch<any>(`/process-intelligence/datasets/${dsId}/bottleneck-rules`).catch(() => ({ presets: [], rules: [] })),
        ]);
        setRulePresets(rulesRes?.presets || []);
        setActiveRules(rulesRes?.rules || []);
        setMappingInfo(mapRes);
        const pList = procRes?.processes || [];
        setProcessList(pList);
        const initM: Record<string, string> = {};
        for (const r of MAPPING_ROLES) {
          initM[r.key] = mapRes?.mapping?.[r.key] || "";
        }
        setMappingEdits(initM);
        setRefSequenceText((mapRes?.reference_sequence || []).join(", "));
        setAnalysis(anaRes);
        setActions(actRes || []);
      } catch (err: any) {
        showToast(err.message || "خطا در تحلیل هوش فرآیندی");
      } finally {
        setLoading(false);
      }
    },
    [showToast]
  );

  useEffect(() => {
    if (datasetId) {
      setSelectedProcess("");
      loadProcessIntelligence(datasetId, "");
    }
  }, [datasetId, loadProcessIntelligence]);

  const handleSelectProcess = async (procName: string) => {
    setSelectedProcess(procName);
    if (datasetId) {
      await loadProcessIntelligence(datasetId, procName);
    }
  };

  const handleSaveMapping = async () => {
    if (!datasetId) return;
    setSavingMap(true);
    try {
      const payloadMapping: Record<string, string | null> = {};
      for (const r of MAPPING_ROLES) {
        payloadMapping[r.key] = mappingEdits[r.key] || null;
      }
      const refSeq = refSequenceText
        .split(/[,،\n]/)
        .map((s) => s.trim())
        .filter(Boolean);
      await apiFetch(`/process-intelligence/datasets/${datasetId}/mapping`, {
        method: "POST",
        body: JSON.stringify({ mapping: payloadMapping, reference_sequence: refSeq, confirmed: true }),
      });
      showToast(isFa ? "نگاشت رویداد فرآیندی تأیید و ذخیره شد" : "Event log mapping confirmed");
      await loadProcessIntelligence(datasetId, selectedProcess);
    } catch (err: any) {
      showToast(err.message);
    } finally {
      setSavingMap(false);
    }
  };

  const openActivityCases = async (activityName: string = "") => {
    if (!datasetId) return;
    setCaseFilterAct(activityName);
    setCaseModalOpen(true);
    setSelectedCaseTrace(null);
    try {
      const qp = new URLSearchParams();
      if (activityName) qp.set("activity", activityName);
      if (selectedProcess) qp.set("process_name", selectedProcess);
      qp.set("limit", "40");
      const res = await apiFetch<any>(`/process-intelligence/datasets/${datasetId}/cases?${qp.toString()}`);
      setCasesList(res.cases || []);
    } catch (err: any) {
      showToast(err.message);
      setCasesList([]);
    }
  };

  const inspectSingleCase = async (caseId: string) => {
    if (!datasetId) return;
    try {
      const res = await apiFetch<any>(`/process-intelligence/datasets/${datasetId}/cases/${encodeURIComponent(caseId)}`);
      setSelectedCaseTrace(res);
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleCreateAction = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!datasetId || !actTitle.trim()) return;
    try {
      await apiFetch(`/process-intelligence/datasets/${datasetId}/actions`, {
        method: "POST",
        body: JSON.stringify({
          title: actTitle,
          target_activity: actTarget,
          kpi_metric: actMetric,
          baseline_start: baseStart || undefined,
          baseline_end: baseEnd || undefined,
          post_start: postStart || undefined,
          post_end: postEnd || undefined,
        }),
      });
      setActTitle("");
      showToast(isFa ? "اقدام اصلاحی ثبت و ارزیابی شد" : "Corrective action recorded");
      await loadProcessIntelligence(datasetId, selectedProcess);
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleApplyBottleneckPreset = async (presetKey: string) => {
    setSelectedPresetKey(presetKey);
    const p = rulePresets.find((x) => x.key === presetKey);
    if (!p || !datasetId) return;
    try {
      await apiFetch(`/process-intelligence/datasets/${datasetId}/bottleneck-rules`, {
        method: "POST",
        body: JSON.stringify({
          name: p.label_fa,
          process_name: selectedProcess || null,
          weights: p.weights,
          threshold_score: p.threshold_score,
          severity: p.severity,
          is_active: true,
        }),
      });
      showToast(isFa ? `فرمول «${p.label_fa}» فعال شد` : `Activated rule: ${p.key}`);
      await loadProcessIntelligence(datasetId, selectedProcess);
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const summary = analysis?.summary;
  const dq = analysis?.data_quality;
  const bottlenecks: any[] = analysis?.bottlenecks || [];
  const variants: any[] = analysis?.variants || [];
  const hypotheses: any[] = analysis?.hypotheses || [];

  return (
    <AppShell>
      <div className="space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-bold flex items-center gap-2">
              <Workflow className="w-5 h-5 text-violet-500" />
              {isFa ? "هوش فرآیندی و کاوش رویداد (Process Intelligence)" : "Process Intelligence & Trace Mining"}
            </h1>
            <p className="text-xs mt-1" style={{ color: "var(--bi-muted)" }}>
              {isFa
                ? "تحلیل ۱۰۰٪ ردیف‌ها در موتور SQL · بازسازی مسیر پرونده‌ها، واریانت‌ها، دوباره‌کاری و گلوگاه‌های چندعاملی"
                : "100% DuckDB SQL-backed trace reconstruction, variants, rework & skip detection, and composite bottlenecks"}
            </p>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            {summary?.total_events_analyzed > 0 && (
              <span className="px-2.5 py-1.5 rounded-lg border border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 text-[11px] font-semibold flex items-center gap-1.5">
                <Database className="w-3.5 h-3.5" />
                {isFa
                  ? `${Number(summary.total_events_analyzed).toLocaleString("fa-IR")} رویداد (۱۰۰٪ SQL)`
                  : `${Number(summary.total_events_analyzed).toLocaleString()} events (100% SQL)`}
              </span>
            )}
            <select
              value={datasetId}
              onChange={(e) => setDatasetId(e.target.value)}
              className="px-3 py-2 rounded-lg border text-xs bg-transparent min-w-[240px]"
              style={{ borderColor: "var(--bi-border)" }}
            >
              {datasets.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name} ({Number(d.row_count || 0).toLocaleString()} rows)
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={() => datasetId && loadProcessIntelligence(datasetId, selectedProcess)}
              disabled={loading}
              className="px-3 py-2 rounded-lg border text-xs font-semibold flex items-center gap-1.5"
              style={{ borderColor: "var(--bi-border)" }}
            >
              {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
              {isFa ? "بازتحلیل" : "Re-Analyze"}
            </button>
          </div>
        </div>

        {/* Configurable Bottleneck Formula Strip */}
        {rulePresets.length > 0 && (
          <div className="bi-card p-3 flex flex-wrap items-center justify-between gap-2 text-xs">
            <div className="flex items-center gap-2">
              <Sliders className="w-4 h-4 text-violet-500" />
              <span className="font-bold">{isFa ? "فرمول تشخیص گلوگاه:" : "Bottleneck Formula:"}</span>
              {activeRules.find((r) => r.is_active) && (
                <span className="px-2 py-0.5 rounded bg-violet-500/15 text-violet-500 text-[10px] font-semibold">
                  فعال: {activeRules.find((r) => r.is_active)?.name} (آستانه {activeRules.find((r) => r.is_active)?.threshold_score})
                </span>
              )}
            </div>
            <div className="flex items-center gap-1.5 flex-wrap">
              {rulePresets.map((p) => (
                <button
                  key={p.key}
                  type="button"
                  onClick={() => handleApplyBottleneckPreset(p.key)}
                  className={`px-2.5 py-1 rounded-lg border text-[11px] font-semibold transition ${
                    selectedPresetKey === p.key ? "bg-violet-600 text-white border-violet-600" : "hover:bg-violet-500/10"
                  }`}
                  style={selectedPresetKey === p.key ? undefined : { borderColor: "var(--bi-border)" }}
                >
                  {p.label_fa}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Multi-Process Switcher Bar */}
        {processList.length > 0 && (
          <div className="bi-card p-3 flex flex-wrap items-center gap-2 text-xs">
            <span className="font-bold flex items-center gap-1.5" style={{ color: "var(--bi-muted)" }}>
              <GitBranch className="w-4 h-4 text-violet-500" />
              {isFa ? `فرآیندهای سازمان (${processList.length}):` : `Processes (${processList.length}):`}
            </span>
            <button
              type="button"
              onClick={() => handleSelectProcess("")}
              className={`px-2.5 py-1 rounded-lg font-semibold border transition ${
                !selectedProcess ? "text-white" : "hover:opacity-80"
              }`}
              style={!selectedProcess ? { background: "var(--bi-primary)", borderColor: "var(--bi-primary)" } : { borderColor: "var(--bi-border)" }}
            >
              {isFa ? "همه فرآیندها" : "All Processes"}
            </button>
            {processList.slice(0, 14).map((p) => (
              <button
                key={p.process_name}
                type="button"
                onClick={() => handleSelectProcess(p.process_name)}
                className={`px-2.5 py-1 rounded-lg font-semibold border transition ${
                  selectedProcess === p.process_name ? "text-white" : "hover:opacity-80"
                }`}
                style={
                  selectedProcess === p.process_name
                    ? { background: "var(--bi-primary)", borderColor: "var(--bi-primary)" }
                    : { borderColor: "var(--bi-border)" }
                }
              >
                {p.process_name}{" "}
                <span className="opacity-75">({Number(p.total_count).toLocaleString(isFa ? "fa-IR" : "en-US")})</span>
              </button>
            ))}
          </div>
        )}

        {/* Column Mapping & Confirmation Banner */}
        {mappingInfo && (
          <div className="bi-card p-4 space-y-3 text-xs">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2 font-bold text-sm">
                <Sliders className="w-4 h-4 text-sky-500" />
                <span>{isFa ? "نگاشت ستون‌های Event Log" : "Event Log Column Mapping"}</span>
                {mappingInfo.user_confirmed ? (
                  <span className="px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-600 dark:text-emerald-300 text-[10px]">
                    ✓ تأییدشده توسط کاربر
                  </span>
                ) : mappingInfo.needs_confirmation ? (
                  <span className="px-2 py-0.5 rounded bg-amber-500/15 text-amber-600 dark:text-amber-300 text-[10px]">
                    ⚠ نیازمند بررسی و تأیید
                  </span>
                ) : null}
              </div>
              <button
                type="button"
                onClick={handleSaveMapping}
                disabled={savingMap}
                className="px-3 py-1.5 rounded-lg text-white font-semibold text-xs flex items-center gap-1.5"
                style={{ background: "var(--bi-primary)" }}
              >
                {savingMap ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <CheckCircle2 className="w-3.5 h-3.5" />}
                {isFa ? "تأیید و اجرای کاوش فرآیند" : "Confirm & Run Mining"}
              </button>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
              {MAPPING_ROLES.map((r) => (
                <div key={r.key}>
                  <label className="block text-[10px] mb-1 truncate" style={{ color: "var(--bi-muted)" }}>
                    {r.labelFa}{" "}
                    {mappingInfo.confidences?.[r.key] > 0 && (
                      <span className="opacity-75">({Math.round(mappingInfo.confidences[r.key] * 100)}%)</span>
                    )}
                  </label>
                  <select
                    value={mappingEdits[r.key] || ""}
                    onChange={(e) => setMappingEdits((prev) => ({ ...prev, [r.key]: e.target.value }))}
                    className="w-full px-2 py-1.5 rounded border text-[11px] bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  >
                    <option value="">— ندارد —</option>
                    {(mappingInfo.available_columns || []).map((c: any) => (
                      <option key={c.field_name} value={c.field_name}>
                        {c.display_name} ({c.field_name})
                      </option>
                    ))}
                  </select>
                </div>
              ))}
            </div>
            <div>
              <label className="block text-[10px] mb-1" style={{ color: "var(--bi-muted)" }}>
                {isFa
                  ? "مسیر مرجع استاندارد (اختیاری، با کاما جدا کنید — برای تشخیص پرش گام‌ها / Skips):"
                  : "Optional Reference Sequence (comma-separated, for Skip detection):"}
              </label>
              <input
                value={refSequenceText}
                onChange={(e) => setRefSequenceText(e.target.value)}
                placeholder="مثال: ثبت درخواست، بررسی فنی، تایید مالی، صدور"
                className="w-full px-3 py-1.5 rounded border text-xs bg-transparent"
                style={{ borderColor: "var(--bi-border)" }}
              />
            </div>
          </div>
        )}

        {analysis?.status !== "ok" && analysis?.error_fa && (
          <div className="bi-card p-5 border border-amber-500/40 bg-amber-500/10 text-xs flex items-center gap-2 text-amber-600 dark:text-amber-300">
            <AlertTriangle className="w-4 h-4 shrink-0" />
            <span>{analysis.error_fa}</span>
          </div>
        )}

        {analysis?.status === "ok" && summary && (
          <>
            {/* Summary Metrics */}
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 text-xs">
              <div className="bi-card p-3">
                <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>کل پرونده‌ها / رویدادها</div>
                <div className="text-lg font-bold mt-1">
                  {summary.total_cases != null ? Number(summary.total_cases).toLocaleString() : "—"}
                </div>
                <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>
                  {Number(summary.total_events_analyzed || 0).toLocaleString()} رویداد · {summary.unique_variants ?? 0} واریانت
                </div>
              </div>
              <div className="bi-card p-3">
                <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>زمان چرخه (Cycle Time)</div>
                <div className="text-lg font-bold mt-1 text-sky-500">
                  {summary.avg_cycle_hours != null ? `${summary.avg_cycle_hours} h` : "—"}
                </div>
                <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>
                  میانه: {summary.median_cycle_hours ?? "—"} h
                </div>
              </div>
              <div className="bi-card p-3">
                <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>زمان انتظار (Wait Time)</div>
                <div className="text-lg font-bold mt-1 text-amber-500">
                  {summary.avg_wait_hours != null ? `${summary.avg_wait_hours} h` : "—"}
                </div>
                <div className="text-[10px] truncate" title={summary.wait_time_basis_fa} style={{ color: "var(--bi-muted)" }}>
                  {summary.wait_time_basis_fa}
                </div>
              </div>
              <div className="bi-card p-3">
                <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>زمان پردازش (Processing)</div>
                <div className="text-lg font-bold mt-1">
                  {summary.avg_processing_hours != null ? `${summary.avg_processing_hours} h` : "نیازمند داده"}
                </div>
                <div className="text-[10px] line-clamp-2" style={{ color: "var(--bi-muted)" }}>
                  {summary.processing_time_note_fa}
                </div>
              </div>
              <div className="bi-card p-3">
                <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>دوباره‌کاری و حلقه</div>
                <div className="text-lg font-bold mt-1 text-rose-500">{summary.rework_rate_pct}%</div>
                <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>
                  {summary.rework_case_count} پرونده · حلقه تکراری: {summary.self_loop_case_count}
                </div>
              </div>
              <div className="bi-card p-3">
                <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>نرخ نقض SLA</div>
                <div className="text-lg font-bold mt-1 text-violet-500">
                  {summary.sla_breach_rate_pct != null ? `${summary.sla_breach_rate_pct}%` : "—"}
                </div>
                <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>
                  {summary.sla_breach_cases} از {summary.sla_evaluated_cases} پرونده
                </div>
              </div>
            </div>

            {dq?.warnings_fa?.length > 0 && (
              <div className="p-3 rounded-xl border border-amber-500/30 bg-amber-500/10 text-xs space-y-1 text-amber-700 dark:text-amber-300">
                <div className="font-semibold flex items-center gap-1.5">
                  <AlertTriangle className="w-4 h-4 shrink-0" />
                  <span>تشخیص کیفیت و نواقص داده فرآیندی:</span>
                </div>
                <ul className="list-disc ps-5 space-y-0.5 text-[11px]">
                  {dq.warnings_fa.map((w: string, idx: number) => (
                    <li key={idx}>{w}</li>
                  ))}
                </ul>
              </div>
            )}

            {/* Composite Multi-Factor Bottleneck Ranking */}
            <div className="bi-card p-4 space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 className="text-sm font-bold flex items-center gap-2">
                  <Activity className="w-4 h-4 text-rose-500" />
                  <span>رتبه‌بندی گلوگاه‌های چندعاملی (مدت انتظار + حجم + دوباره‌کاری + سهم از چرخه + بار ظرفیت)</span>
                </h2>
                <button
                  type="button"
                  onClick={() => openActivityCases("")}
                  className="px-2.5 py-1 rounded border text-xs font-semibold hover:opacity-80"
                  style={{ borderColor: "var(--bi-border)" }}
                >
                  مشاهده تمام پرونده‌ها
                </button>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b text-[11px]" style={{ borderColor: "var(--bi-border)", color: "var(--bi-muted)" }}>
                      <th className="py-2 text-start">گام / فعالیت</th>
                      <th className="py-2 text-start">امتیاز ترکیبی گلوگاه</th>
                      <th className="py-2 text-start">میانگین انتظار</th>
                      <th className="py-2 text-start">پرونده‌ها</th>
                      <th className="py-2 text-start">دوباره‌کاری</th>
                      <th className="py-2 text-start">نقض SLA</th>
                      <th className="py-2 text-start">جزئیات پرونده‌ها</th>
                    </tr>
                  </thead>
                  <tbody>
                    {bottlenecks.map((b) => (
                      <tr key={b.activity} className="border-b" style={{ borderColor: "var(--bi-border)" }}>
                        <td className="py-2 font-semibold">{b.activity}</td>
                        <td className="py-2">
                          <div className="flex items-center gap-2">
                            <span className="font-bold text-rose-500 w-10">{b.composite_bottleneck_score}</span>
                            <div className="w-24 h-2 rounded bg-slate-500/20 overflow-hidden">
                              <div
                                className="h-full bg-rose-500"
                                style={{ width: `${Math.min(100, b.composite_bottleneck_score)}%` }}
                              />
                            </div>
                          </div>
                        </td>
                        <td className="py-2">{b.avg_wait_hours != null ? `${b.avg_wait_hours} h` : "—"}</td>
                        <td className="py-2">{b.case_count}</td>
                        <td className="py-2">{b.rework_rate_pct}% ({b.rework_events})</td>
                        <td className="py-2">{b.sla_breach_pct != null ? `${b.sla_breach_pct}%` : "—"}</td>
                        <td className="py-2">
                          <button
                            type="button"
                            onClick={() => openActivityCases(b.activity)}
                            className="px-2 py-0.5 rounded border text-[11px] hover:bg-sky-500/10"
                            style={{ borderColor: "var(--bi-border)" }}
                          >
                            Drill-Down
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Variants & Root Cause Hypotheses */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <div className="bi-card p-4 space-y-3">
                <h2 className="text-sm font-bold flex items-center gap-2">
                  <GitBranch className="w-4 h-4 text-sky-500" />
                  <span>مسیرهای واقعی (Variants) و انحراف‌ها ({variants.length})</span>
                </h2>
                <div className="space-y-2 max-h-80 overflow-y-auto text-xs">
                  {variants.map((v) => (
                    <div
                      key={v.rank}
                      className="p-2.5 rounded-lg border space-y-1"
                      style={{ borderColor: "var(--bi-border)" }}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-bold">
                          واریانت #{v.rank} · {v.case_count} پرونده ({v.share_pct}%)
                        </span>
                        {v.is_deviant ? (
                          <span className="px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-600 text-[10px]">
                            مسیر انحرافی
                          </span>
                        ) : (
                          <span className="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-600 text-[10px]">
                            مسیر غالب
                          </span>
                        )}
                      </div>
                      <div className="text-[11px] font-medium opacity-90">{v.variant_key}</div>
                      <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>
                        میانگین چرخه: {v.avg_cycle_hours ?? "—"}h · دوباره‌کاری: {v.rework_rate_pct}% · نقض SLA: {v.sla_breach_pct}%
                        {v.skipped_steps?.length > 0 && ` · پرش: ${v.skipped_steps.join("، ")}`}
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="bi-card p-4 space-y-3">
                <h2 className="text-sm font-bold flex items-center gap-2">
                  <Search className="w-4 h-4 text-amber-500" />
                  <span>فرضیات ریشه‌یابی و همبستگی آماری (غیرعلّی)</span>
                </h2>
                <div className="space-y-2 max-h-80 overflow-y-auto text-xs">
                  {hypotheses.length === 0 ? (
                    <div className="text-xs opacity-70">انحراف زیرگروهی معناداری (ratio &gt;= 1.35 با حداقل ۳ نمونه) مشاهده نشد.</div>
                  ) : (
                    hypotheses.map((h, i) => (
                      <div key={i} className="p-2.5 rounded-lg border space-y-1" style={{ borderColor: "var(--bi-border)" }}>
                        <div className="flex items-center justify-between gap-2">
                          <span className="px-2 py-0.5 rounded bg-violet-500/15 text-violet-500 text-[10px] font-semibold">
                            {h.label_fa}
                          </span>
                          <span className="text-[10px]" style={{ color: "var(--bi-muted)" }}>
                            n={h.sample_count} · {h.time_range}
                          </span>
                        </div>
                        <div className="font-semibold text-xs">{h.summary_fa}</div>
                        <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>{h.limitations_fa}</div>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>

            {/* Corrective Actions & Reproducible Before/After */}
            <div className="bi-card p-4 space-y-4">
              <h2 className="text-sm font-bold flex items-center gap-2">
                <ListChecks className="w-4 h-4 text-emerald-500" />
                <span>ثبت اقدام اصلاحی و سنجش تکرارپذیر قبل و بعد (Before / After)</span>
              </h2>
              <form onSubmit={handleCreateAction} className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2 text-xs">
                <input
                  value={actTitle}
                  onChange={(e) => setActTitle(e.target.value)}
                  placeholder="عنوان اقدام (مثلاً تفویض تأیید فنی)"
                  className="px-2.5 py-2 rounded border bg-transparent"
                  style={{ borderColor: "var(--bi-border)" }}
                  required
                />
                <select
                  value={actTarget}
                  onChange={(e) => setActTarget(e.target.value)}
                  className="px-2.5 py-2 rounded border bg-transparent"
                  style={{ borderColor: "var(--bi-border)" }}
                >
                  <option value="">کل فرآیند (همه گام‌ها)</option>
                  {bottlenecks.map((b) => (
                    <option key={b.activity} value={b.activity}>{b.activity}</option>
                  ))}
                </select>
                <select
                  value={actMetric}
                  onChange={(e) => setActMetric(e.target.value)}
                  className="px-2.5 py-2 rounded border bg-transparent"
                  style={{ borderColor: "var(--bi-border)" }}
                >
                  <option value="avg_cycle_hours">میانگین زمان چرخه</option>
                  <option value="avg_wait_hours">میانگین زمان انتظار</option>
                  <option value="sla_breach_pct">نرخ نقض SLA</option>
                  <option value="rework_rate_pct">نرخ دوباره‌کاری</option>
                </select>
                <button
                  type="submit"
                  className="px-3 py-2 rounded text-white font-semibold flex items-center justify-center gap-1"
                  style={{ background: "var(--bi-primary)" }}
                >
                  <Plus className="w-3.5 h-3.5" />
                  ثبت و اندازه‌گیری
                </button>
                <input value={baseStart} onChange={(e) => setBaseStart(e.target.value)} placeholder="شروع قبل (YYYY-MM-DD)" className="px-2.5 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
                <input value={baseEnd} onChange={(e) => setBaseEnd(e.target.value)} placeholder="پایان قبل (YYYY-MM-DD)" className="px-2.5 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
                <input value={postStart} onChange={(e) => setPostStart(e.target.value)} placeholder="شروع بعد (YYYY-MM-DD)" className="px-2.5 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
                <input value={postEnd} onChange={(e) => setPostEnd(e.target.value)} placeholder="پایان بعد (YYYY-MM-DD)" className="px-2.5 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
              </form>

              {actions.length > 0 && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
                  {actions.map((a) => (
                    <div key={a.id} className="p-3 rounded-lg border space-y-1.5" style={{ borderColor: "var(--bi-border)" }}>
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-bold">{a.title}</span>
                        <span className="text-[10px] px-2 py-0.5 rounded bg-sky-500/15 text-sky-500">{a.kpi_metric}</span>
                      </div>
                      <div className="flex items-center gap-3 text-[11px]">
                        <span>قبل: <b>{a.measured_before ?? "—"}</b> (n={a.sample_before})</span>
                        <ArrowRight className="w-3 h-3 opacity-60" />
                        <span>بعد: <b>{a.measured_after ?? "—"}</b> (n={a.sample_after})</span>
                        {a.delta_pct != null && (
                          <span className={`font-bold ${a.delta_pct < 0 ? "text-emerald-500" : "text-rose-500"}`}>
                            ({a.delta_pct > 0 ? `+${a.delta_pct}%` : `${a.delta_pct}%`})
                          </span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </div>

      {/* Case Drill-Down Modal */}
      {caseModalOpen && (
        <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4">
          <div className="bi-card w-full max-w-4xl max-h-[85vh] flex flex-col p-5 space-y-3 overflow-hidden">
            <div className="flex items-center justify-between border-b pb-2" style={{ borderColor: "var(--bi-border)" }}>
              <div className="font-bold text-sm flex items-center gap-2">
                <Layers className="w-4 h-4 text-sky-500" />
                <span>جزئیات پرونده‌ها {caseFilterAct ? `— گام «${caseFilterAct}»` : ""}</span>
              </div>
              <button type="button" onClick={() => setCaseModalOpen(false)} className="p-1">
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 overflow-y-auto text-xs">
              <div className="space-y-1.5 max-h-[60vh] overflow-y-auto">
                {casesList.map((c) => (
                  <button
                    key={c.case_id}
                    type="button"
                    onClick={() => inspectSingleCase(c.case_id)}
                    className="w-full text-start p-2.5 rounded-lg border hover:border-sky-500 transition flex items-center justify-between"
                    style={{ borderColor: "var(--bi-border)" }}
                  >
                    <div>
                      <div className="font-bold">{c.case_id}</div>
                      <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>
                        {c.event_count} رویداد · انتظار: {c.wait_hours ?? "—"}h
                      </div>
                    </div>
                    <div className="flex gap-1">
                      {c.has_rework && <span className="px-1.5 py-0.5 rounded bg-rose-500/15 text-rose-500 text-[10px]">دوباره‌کاری</span>}
                      {c.sla_breached && <span className="px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-500 text-[10px]">SLA</span>}
                    </div>
                  </button>
                ))}
              </div>
              <div className="border rounded-lg p-3 max-h-[60vh] overflow-y-auto" style={{ borderColor: "var(--bi-border)" }}>
                {!selectedCaseTrace ? (
                  <div className="opacity-70 text-center py-8">روی یک پرونده کلیک کنید تا خط زمانی رویدادهای آن نمایش داده شود.</div>
                ) : (
                  <div className="space-y-2">
                    <div className="font-bold border-b pb-1.5" style={{ borderColor: "var(--bi-border)" }}>
                      خط زمانی پرونده {selectedCaseTrace.case_id}
                    </div>
                    {(selectedCaseTrace.events || []).map((ev: any) => (
                      <div key={ev.seq} className="p-2 rounded border text-[11px]" style={{ borderColor: "var(--bi-border)" }}>
                        <div className="flex justify-between font-semibold">
                          <span>{ev.seq}. {ev.activity}</span>
                          <span className="opacity-75">{ev.timestamp || "بدون زمان"}</span>
                        </div>
                        <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>
                          مسئول: {ev.actor || "—"} · واحد: {ev.department || "—"} · انتظار: {ev.wait_hours ?? "—"}h
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}
