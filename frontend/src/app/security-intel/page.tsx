"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  GitMerge,
  Loader2,
  Lock,
  Plus,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Upload,
} from "lucide-react";
import AppShell from "@/components/layout/AppShell";
import { apiFetch } from "@/lib/api";
import { useBi } from "@/lib/context";

export default function SecurityIntelPage() {
  const { locale, showToast } = useBi();
  const isFa = locale === "fa";

  const [connectors, setConnectors] = useState<any[]>([]);
  const [collectors, setCollectors] = useState<any[]>([]);
  const [overview, setOverview] = useState<any>(null);
  const [eventsData, setEventsData] = useState<any>(null);
  const [unparsed, setUnparsed] = useState<any[]>([]);
  const [unparsedDenied, setUnparsedDenied] = useState(false);
  const [alerts, setAlerts] = useState<any[]>([]);
  const [incidents, setIncidents] = useState<any[]>([]);
  const [datasets, setDatasets] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);

  // Collector creation & log ingestion
  const [newColName, setNewColName] = useState("Syslog / JSONL SOC Collector");
  const [selectedColId, setSelectedColId] = useState("");
  const [rawPaste, setRawPaste] = useState(
    '{"timestamp":"2026-11-16T08:00:00Z","vendor":"NexusSOC","product":"AuthGateway","event_type":"auth_login","severity":"high","action":"Failed","username":"ali.rezaei","hostname":"WS-FIN-04","src_ip":"10.20.4.55","rule_name":"BRUTE_FORCE_WATCH"}\n' +
    '2026-11-16T08:05:00Z WS-FIN-04 vendor=EDR product=EndpointGuard event_type=malware_scan action=Quarantine severity=critical user=ali.rezaei threat_name=Trojan.Win32.Sample'
  );

  // Correlation state
  const [corrDatasetId, setCorrDatasetId] = useState("");
  const [corrKey, setCorrKey] = useState("username");
  const [corrWindowMin, setCorrWindowMin] = useState(180);
  const [corrResult, setCorrResult] = useState<any>(null);

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [connRes, colRes, ovRes, evRes, alRes, incRes, dsRes] = await Promise.all([
        apiFetch<any>("/security-intel/connectors"),
        apiFetch<any[]>("/security-intel/collectors"),
        apiFetch<any>("/security-intel/overview?hours=720"),
        apiFetch<any>("/security-intel/events?limit=30"),
        apiFetch<any[]>("/security-intel/alerts"),
        apiFetch<any[]>("/security-intel/incidents"),
        apiFetch<any[]>("/datasets").catch(() => []),
      ]);
      setConnectors(connRes?.connectors || []);
      setCollectors(colRes || []);
      if (!selectedColId && colRes?.[0]?.id) setSelectedColId(colRes[0].id);
      setOverview(ovRes);
      setEventsData(evRes);
      setAlerts(alRes || []);
      setIncidents(incRes || []);
      setDatasets(dsRes || []);
      if (!corrDatasetId && dsRes?.length) {
        const proc = dsRes.find((d: any) => /bpmn|process|workflow|فرآیند/i.test(`${d.name} ${d.description || ""}`)) || dsRes[0];
        setCorrDatasetId(proc.id);
      }

      try {
        const unp = await apiFetch<any[]>("/security-intel/unparsed?limit=25");
        setUnparsed(unp || []);
        setUnparsedDenied(false);
      } catch {
        setUnparsed([]);
        setUnparsedDenied(true);
      }
    } catch (err: any) {
      showToast(err.message || "خطا در بارگذاری ماژول امنیتی");
    } finally {
      setLoading(false);
    }
  }, [corrDatasetId, selectedColId, showToast]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const handleCreateCollector = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const created = await apiFetch<any>("/security-intel/collectors", {
        method: "POST",
        body: JSON.stringify({ name: newColName, source_type: "syslog_security_jsonl", retention_days: 90 }),
      });
      setSelectedColId(created.id);
      showToast("جمع‌آورنده امنیتی ایجاد شد");
      await loadAll();
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleIngestPaste = async () => {
    if (!selectedColId) {
      showToast("ابتدا یک جمع‌آورنده فعال ایجاد یا انتخاب کنید");
      return;
    }
    const lines = rawPaste.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
    try {
      const res = await apiFetch<any>("/security-intel/ingest/batch", {
        method: "POST",
        body: JSON.stringify({ collector_id: selectedColId, source_name: "ui_paste", items: lines }),
      });
      showToast(`ثبت شد: ${res.ingested} · تکراری حذف‌شده: ${res.deduplicated} · قرنطینه نامعتبر: ${res.unparsed_quarantined}`);
      await loadAll();
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleEvaluateAlerts = async () => {
    try {
      const res = await apiFetch<any>("/security-intel/alerts/evaluate", { method: "POST" });
      showToast(`${res.count} هشدار آماری جدید ایجاد شد`);
      await loadAll();
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleCreateIncidentFromAlert = async (al: any) => {
    try {
      await apiFetch("/security-intel/incidents", {
        method: "POST",
        body: JSON.stringify({
          title: `رخداد مشورتی: ${al.title}`,
          severity: al.severity,
          alert_ids: [al.id],
          recommended_action_fa: "بررسی نشست‌های کاربر/میزبان و تایید تیم امنیت پیش از هرگونه تغییر دسترسی",
          rollback_plan_fa: "بازگردانی دستی تنظیمات احراز هویت پس از بررسی لاگ",
        }),
      });
      showToast("پرونده رخداد (حالت مشورتی) ثبت شد");
      await loadAll();
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleApproveIncident = async (incId: string) => {
    try {
      await apiFetch(`/security-intel/incidents/${incId}/approve`, {
        method: "POST",
        body: JSON.stringify({
          human_approved: true,
          approval_note: "تأیید تحلیلگر برای پیگیری دستی",
          new_status: "contained_proposal",
        }),
      });
      showToast("تأیید انسانی در لاگ Audit ثبت شد (بدون اجرای خودکار مسدودسازی)");
      await loadAll();
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleRunCorrelation = async () => {
    if (!corrDatasetId) return;
    try {
      const res = await apiFetch<any>("/correlation/analyze", {
        method: "POST",
        body: JSON.stringify({
          process_dataset_id: corrDatasetId,
          correlation_key: corrKey,
          window_minutes: corrWindowMin,
        }),
      });
      setCorrResult(res);
    } catch (err: any) {
      showToast(err.message);
    }
  };

  return (
    <AppShell>
      <div className="space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-bold flex items-center gap-2">
              <ShieldAlert className="w-5 h-5 text-rose-500" />
              {isFa ? "هوش لاگ‌های امنیتی و همبستگی میان‌دامنه‌ای" : "Security Log Intelligence & Correlation"}
            </h1>
            <p className="text-xs mt-1" style={{ color: "var(--bi-muted)" }}>
              {isFa
                ? "دریافت واقعی JSONL/Syslog، قرنطینه لاگ‌های نامعتبر، تفکیک Detect/Block/Quarantine/Clean/Failed، هشدارهای مبنا و رخدادهای مشورتی"
                : "Structured security ingestion, unparsed DLQ quarantine, statistical baselines, advisory incidents, and cross-domain correlation"}
            </p>
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={handleEvaluateAlerts}
              className="px-3 py-2 rounded-lg border text-xs font-semibold flex items-center gap-1.5"
              style={{ borderColor: "var(--bi-border)" }}
            >
              <Activity className="w-3.5 h-3.5 text-amber-500" />
              ارزیابی هشدارهای آماری
            </button>
            <button
              type="button"
              onClick={loadAll}
              disabled={loading}
              className="px-3 py-2 rounded-lg border text-xs font-semibold flex items-center gap-1.5"
              style={{ borderColor: "var(--bi-border)" }}
            >
              {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
              بروزرسانی
            </button>
          </div>
        </div>

        {/* Overview Cards & Honesty Warnings */}
        {overview && (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2.5 text-xs">
              {Object.entries(overview.action_breakdown || {}).map(([act, cnt]) => (
                <div key={act} className="bi-card p-3">
                  <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>اقدام {act}</div>
                  <div className="text-lg font-bold mt-0.5">{Number(cnt).toLocaleString()}</div>
                </div>
              ))}
            </div>
            <div className="p-3 rounded-xl border border-amber-500/30 bg-amber-500/10 text-xs space-y-1">
              {(overview.interpretation_notes_fa || []).map((n: string, idx: number) => (
                <div key={idx} className="flex items-center gap-1.5 text-[11px]">
                  <AlertTriangle className="w-3.5 h-3.5 text-amber-500 shrink-0" />
                  <span>{n}</span>
                </div>
              ))}
            </div>
          </>
        )}

        {/* Connector Honesty Matrix & Real Ingestion */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 text-xs">
          <div className="bi-card p-4 space-y-3">
            <h2 className="text-sm font-bold flex items-center gap-2">
              <ShieldCheck className="w-4 h-4 text-emerald-500" />
              <span>ماتریس شفاف وضعیت کانکتورهای امنیتی</span>
            </h2>
            <div className="space-y-1.5 max-h-72 overflow-y-auto">
              {connectors.map((c) => (
                <div key={c.id} className="p-2.5 rounded-lg border flex items-start justify-between gap-2" style={{ borderColor: "var(--bi-border)" }}>
                  <div>
                    <div className="font-bold">{c.name_fa}</div>
                    <div className="text-[10px] mt-0.5" style={{ color: "var(--bi-muted)" }}>{c.requirements_fa}</div>
                  </div>
                  <span
                    className={`px-2 py-0.5 rounded text-[10px] font-semibold shrink-0 ${
                      c.tested ? "bg-emerald-500/15 text-emerald-600" : "bg-slate-500/15 text-slate-400"
                    }`}
                  >
                    {c.status_fa}
                  </span>
                </div>
              ))}
            </div>
          </div>

          <div className="bi-card p-4 space-y-3">
            <h2 className="text-sm font-bold flex items-center gap-2">
              <Upload className="w-4 h-4 text-sky-500" />
              <span>جمع‌آورنده فعال و دریافت واقعی لاگ امنیتی (JSONL / Syslog KV)</span>
            </h2>
            <form onSubmit={handleCreateCollector} className="flex gap-2">
              <input
                value={newColName}
                onChange={(e) => setNewColName(e.target.value)}
                className="flex-1 px-2.5 py-1.5 rounded border bg-transparent"
                style={{ borderColor: "var(--bi-border)" }}
                required
              />
              <button type="submit" className="px-3 py-1.5 rounded text-white font-semibold flex items-center gap-1" style={{ background: "var(--bi-primary)" }}>
                <Plus className="w-3.5 h-3.5" /> ایجاد Collector
              </button>
            </form>
            {collectors.length > 0 && (
              <select
                value={selectedColId}
                onChange={(e) => setSelectedColId(e.target.value)}
                className="w-full px-2.5 py-1.5 rounded border bg-transparent"
                style={{ borderColor: "var(--bi-border)" }}
              >
                {collectors.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} — ثبت: {c.events_ingested} · تکراری: {c.events_deduplicated} · قرنطینه: {c.events_unparsed}
                  </option>
                ))}
              </select>
            )}
            <textarea
              value={rawPaste}
              onChange={(e) => setRawPaste(e.target.value)}
              rows={4}
              dir="ltr"
              className="w-full p-2.5 rounded border font-mono text-[11px] bg-transparent"
              style={{ borderColor: "var(--bi-border)" }}
            />
            <button
              type="button"
              onClick={handleIngestPaste}
              className="px-3 py-2 rounded text-white font-semibold bg-emerald-600"
            >
              پردازش، نرمال‌سازی و ذخیره رویدادها
            </button>
          </div>
        </div>

        {/* Events & Unparsed Quarantine DLQ */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 text-xs">
          <div className="lg:col-span-2 bi-card p-4 space-y-2.5">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-bold">رویدادهای نرمال‌شده ({eventsData?.total || 0})</h2>
              <span className="text-[10px] flex items-center gap-1" style={{ color: "var(--bi-muted)" }}>
                <Lock className="w-3 h-3" />
                {eventsData?.can_view_raw ? "دسترسی پیام خام فعال است" : "پیام خام محدودشده (نیازمند security_log:view_raw)"}
              </span>
            </div>
            <div className="overflow-x-auto max-h-72">
              <table className="w-full text-[11px]">
                <thead>
                  <tr className="border-b" style={{ borderColor: "var(--bi-border)", color: "var(--bi-muted)" }}>
                    <th className="py-1.5 text-start">زمان</th>
                    <th className="py-1.5 text-start">اقدام</th>
                    <th className="py-1.5 text-start">شدت</th>
                    <th className="py-1.5 text-start">کاربر / میزبان</th>
                    <th className="py-1.5 text-start">IP / تهدید</th>
                  </tr>
                </thead>
                <tbody>
                  {(eventsData?.events || []).map((ev: any) => (
                    <tr key={ev.id} className="border-b" style={{ borderColor: "var(--bi-border)" }}>
                      <td className="py-1.5 whitespace-nowrap">{String(ev.event_time || "").slice(0, 19).replace("T", " ")}</td>
                      <td className="py-1.5 font-semibold">{ev.action}</td>
                      <td className="py-1.5">{ev.severity}</td>
                      <td className="py-1.5">{[ev.username, ev.hostname].filter(Boolean).join(" @ ") || "—"}</td>
                      <td className="py-1.5">{[ev.src_ip, ev.threat_name, ev.rule_name].filter(Boolean).join(" · ") || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="bi-card p-4 space-y-2.5">
            <h2 className="text-sm font-bold">قرنطینه لاگ‌های پارس‌نشده (DLQ)</h2>
            {unparsedDenied ? (
              <div className="p-3 rounded border border-amber-500/30 bg-amber-500/10 text-[11px]">
                مشاهده محتوای خام خطوط قرنطینه‌شده نیازمند مجوز مستقل <code>security_log:view_raw</code> است.
              </div>
            ) : unparsed.length === 0 ? (
              <div className="opacity-70 text-[11px]">خط نامعتبری در قرنطینه نیست.</div>
            ) : (
              <div className="space-y-1.5 max-h-64 overflow-y-auto">
                {unparsed.map((u) => (
                  <div key={u.id} className="p-2 rounded border text-[10px]" style={{ borderColor: "var(--bi-border)" }}>
                    <div className="font-bold text-rose-500">{u.source_name} (خط {u.line_number}): {u.parse_error}</div>
                    <div className="font-mono truncate opacity-75">{u.raw_line}</div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Explainable Alerts & Advisory Incidents */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 text-xs">
          <div className="bi-card p-4 space-y-2.5">
            <h2 className="text-sm font-bold">هشدارهای آماری توضیح‌پذیر ({alerts.length})</h2>
            <div className="space-y-2 max-h-64 overflow-y-auto">
              {alerts.map((al) => (
                <div key={al.id} className="p-2.5 rounded-lg border space-y-1" style={{ borderColor: "var(--bi-border)" }}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-bold">{al.title}</span>
                    <button
                      type="button"
                      onClick={() => handleCreateIncidentFromAlert(al)}
                      className="px-2 py-0.5 rounded border text-[10px] hover:bg-rose-500/10"
                      style={{ borderColor: "var(--bi-border)" }}
                    >
                      + ثبت رخداد مشورتی
                    </button>
                  </div>
                  <div className="text-[11px]">
                    مشاهده: <b>{al.observed_value}</b> · خط مبنا: <b>{al.baseline_value} ± {al.baseline_stddev}</b> · نمونه: {al.sample_count}
                  </div>
                  <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>{al.limitations_fa}</div>
                </div>
              ))}
            </div>
          </div>

          <div className="bi-card p-4 space-y-2.5">
            <h2 className="text-sm font-bold">پرونده‌های رخداد مشورتی (بدون اقدام خودکار) ({incidents.length})</h2>
            <div className="space-y-2 max-h-64 overflow-y-auto">
              {incidents.map((inc) => (
                <div key={inc.id} className="p-2.5 rounded-lg border space-y-1" style={{ borderColor: "var(--bi-border)" }}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-bold">{inc.title}</span>
                    <span className="px-2 py-0.5 rounded bg-sky-500/15 text-sky-500 text-[10px]">{inc.status}</span>
                  </div>
                  <div className="text-[11px]">{inc.recommended_action_fa}</div>
                  <div className="flex items-center justify-between pt-1">
                    <span className="text-[10px]" style={{ color: "var(--bi-muted)" }}>Rollback: {inc.rollback_plan_fa}</span>
                    {!inc.human_approved ? (
                      <button
                        type="button"
                        onClick={() => handleApproveIncident(inc.id)}
                        className="px-2 py-0.5 rounded text-white text-[10px] font-semibold bg-emerald-600"
                      >
                        تأیید انسانی (Audit)
                      </button>
                    ) : (
                      <span className="text-emerald-500 text-[10px] flex items-center gap-1">
                        <CheckCircle2 className="w-3 h-3" /> تأییدشده توسط انسان
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Phase 4: Cross-Domain Process <-> Security Correlation */}
        <div className="bi-card p-4 space-y-3 text-xs">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-bold flex items-center gap-2">
              <GitMerge className="w-4 h-4 text-violet-500" />
              <span>همبستگی میان‌دامنه‌ای فرآیند و امنیت (کلید قابل ردیابی + پنجره زمانی)</span>
            </h2>
            <div className="flex flex-wrap items-center gap-2">
              <select
                value={corrDatasetId}
                onChange={(e) => setCorrDatasetId(e.target.value)}
                className="px-2.5 py-1.5 rounded border bg-transparent"
                style={{ borderColor: "var(--bi-border)" }}
              >
                {datasets.map((d) => (
                  <option key={d.id} value={d.id}>{d.name}</option>
                ))}
              </select>
              <select
                value={corrKey}
                onChange={(e) => setCorrKey(e.target.value)}
                className="px-2.5 py-1.5 rounded border bg-transparent"
                style={{ borderColor: "var(--bi-border)" }}
              >
                <option value="username">کلید: نام کاربری / مسئول</option>
                <option value="hostname">کلید: Hostname / دستگاه</option>
                <option value="ip">کلید: آدرس IP</option>
              </select>
              <input
                type="number"
                value={corrWindowMin}
                onChange={(e) => setCorrWindowMin(Number(e.target.value || 120))}
                className="w-24 px-2 py-1.5 rounded border bg-transparent"
                style={{ borderColor: "var(--bi-border)" }}
                min={1}
                max={10080}
              />
              <button
                type="button"
                onClick={handleRunCorrelation}
                className="px-3 py-1.5 rounded text-white font-semibold"
                style={{ background: "var(--bi-primary)" }}
              >
                اجرای همبستگی
              </button>
            </div>
          </div>

          {corrResult && (
            <div className="space-y-2.5">
              {corrResult.error_fa ? (
                <div className="p-2.5 rounded border border-amber-500/40 bg-amber-500/10">{corrResult.error_fa}</div>
              ) : (
                <>
                  <div className="p-2.5 rounded bg-violet-500/10 text-violet-600 dark:text-violet-300 text-[11px]">
                    <b>توضیح علمی:</b> {corrResult.causation_disclaimer_fa}
                  </div>
                  <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
                    <div className="p-2 rounded border" style={{ borderColor: "var(--bi-border)" }}>
                      پرونده‌های تطبیق‌یافته: <b>{corrResult.match_quality?.matched_process_cases}</b>
                    </div>
                    <div className="p-2 rounded border" style={{ borderColor: "var(--bi-border)" }}>
                      بدون تطبیق: <b>{corrResult.match_quality?.unmatched_process_cases}</b>
                    </div>
                    <div className="p-2 rounded border" style={{ borderColor: "var(--bi-border)" }}>
                      رویدادهای امنیتی مرتبط: <b>{corrResult.match_quality?.matched_security_events}</b>
                    </div>
                    <div className="p-2 rounded border" style={{ borderColor: "var(--bi-border)" }}>
                      تطبیق چندگانه (مبهم): <b>{corrResult.match_quality?.ambiguous_multi_event_matches}</b>
                    </div>
                    <div className="p-2 rounded border" style={{ borderColor: "var(--bi-border)" }}>
                      نرخ تطبیق: <b>{corrResult.match_quality?.match_rate_pct}%</b>
                    </div>
                  </div>
                  <div className="max-h-56 overflow-y-auto space-y-1">
                    {(corrResult.shared_timeline || []).map((t: any, i: number) => (
                      <div key={i} className="p-2 rounded border flex items-center justify-between gap-2 text-[11px]" style={{ borderColor: "var(--bi-border)" }}>
                        <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${t.domain === "security" ? "bg-rose-500/15 text-rose-500" : "bg-sky-500/15 text-sky-500"}`}>
                          {t.domain === "security" ? "امنیت" : "فرآیند"}
                        </span>
                        <span className="flex-1 truncate">{t.summary}</span>
                        <span className="opacity-70">{String(t.timestamp).slice(0, 19).replace("T", " ")}</span>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </AppShell>
  );
}
