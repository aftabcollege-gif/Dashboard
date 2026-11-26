"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  Bug,
  CheckCircle2,
  FileSearch,
  GitMerge,
  Loader2,
  Lock,
  Plus,
  RefreshCw,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Trash2,
  Upload,
} from "lucide-react";
import AppShell from "@/components/layout/AppShell";
import { apiFetch } from "@/lib/api";
import { useBi } from "@/lib/context";

type SocTab = "operations" | "soc_triage" | "nlp_rules" | "cve_exposure" | "correlation";

const STATUS_LABELS_FA: Record<string, string> = {
  resolved_cleaned: "پاکسازی / برطرف‌شده",
  blocked_quarantined: "مسدود / قرنطینه‌شده",
  active_threat: "تهدید فعال",
  requires_review: "نیازمند بررسی",
  false_positive: "هشدار کاذب",
};

export default function SecurityIntelPage() {
  const { locale, showToast } = useBi();
  const isFa = locale === "fa";

  const [tab, setTab] = useState<SocTab>("operations");
  const [connectors, setConnectors] = useState<any[]>([]);
  const [collectors, setCollectors] = useState<any[]>([]);
  const [overview, setOverview] = useState<any>(null);
  const [eventsData, setEventsData] = useState<any>(null);
  const [unparsed, setUnparsed] = useState<any[]>([]);
  const [unparsedDenied, setUnparsedDenied] = useState(false);
  const [alerts, setAlerts] = useState<any[]>([]);
  const [incidents, setIncidents] = useState<any[]>([]);
  const [datasets, setDatasets] = useState<any[]>([]);
  const [categories, setCategories] = useState<any[]>([]);
  const [clsRules, setClsRules] = useState<any[]>([]);
  const [vulns, setVulns] = useState<any[]>([]);
  const [exposures, setExposures] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);

  // Collector creation & log ingestion
  const [newColName, setNewColName] = useState("Syslog / JSONL SOC Collector");
  const [selectedColId, setSelectedColId] = useState("");
  const [rawPaste, setRawPaste] = useState(
    '{"timestamp":"2026-11-16T08:00:00Z","vendor":"NexusSOC","product":"openssh","software_version":"9.3p1","event_type":"auth_login","severity":"high","action":"Failed","username":"ali.rezaei","hostname":"WS-FIN-04","src_ip":"10.20.4.55","rule_name":"BRUTE_FORCE_WATCH","message":"تلاش مکرر ورود ناموفق - نیازمند بررسی"}\n' +
      '2026-11-16T08:05:00Z WS-FIN-04 vendor=EDR product=EndpointGuard event_type=malware_scan action=Quarantine severity=critical user=ali.rezaei threat_name=Trojan.Win32.Sample msg="بدافزار شناسایی و قرنطینه شد"'
  );

  // NLP Custom Rule & Dataset Classification
  const [ruleKind, setRuleKind] = useState<"status_map" | "category_map">("status_map");
  const [ruleName, setRuleName] = useState("");
  const [rulePattern, setRulePattern] = useState("");
  const [ruleTarget, setRuleTarget] = useState("blocked_quarantined");
  const [nlpDsId, setNlpDsId] = useState("");
  const [nlpResult, setNlpResult] = useState<any>(null);

  // Tier 2 Incident Investigation notes
  const [incNotes, setIncNotes] = useState<Record<string, { hypothesis: string; finalReport: string }>>({});

  // Correlation state
  const [corrDatasetId, setCorrDatasetId] = useState("");
  const [corrKey, setCorrKey] = useState("username");
  const [corrWindowMin, setCorrWindowMin] = useState(180);
  const [corrResult, setCorrResult] = useState<any>(null);

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [connRes, colRes, ovRes, evRes, alRes, incRes, dsRes, rulesRes, vulnRes, expRes] = await Promise.all([
        apiFetch<any>("/security-intel/connectors"),
        apiFetch<any[]>("/security-intel/collectors"),
        apiFetch<any>("/security-intel/overview?hours=720"),
        apiFetch<any>("/security-intel/events?limit=40"),
        apiFetch<any[]>("/security-intel/alerts"),
        apiFetch<any[]>("/security-intel/incidents"),
        apiFetch<any[]>("/datasets").catch(() => []),
        apiFetch<any>("/security-intel/classification-rules").catch(() => ({ categories: [], rules: [] })),
        apiFetch<any[]>("/security-intel/vulnerabilities").catch(() => []),
        apiFetch<any[]>("/security-intel/exposures").catch(() => []),
      ]);
      setConnectors(connRes?.connectors || []);
      setCollectors(colRes || []);
      if (!selectedColId && colRes?.[0]?.id) setSelectedColId(colRes[0].id);
      setOverview(ovRes);
      setEventsData(evRes);
      setAlerts(alRes || []);
      setIncidents(incRes || []);
      setDatasets(dsRes || []);
      setCategories(rulesRes?.categories || []);
      setClsRules(rulesRes?.rules || []);
      setVulns(vulnRes || []);
      setExposures(expRes || []);

      if (dsRes?.length) {
        if (!corrDatasetId) {
          const proc = dsRes.find((d: any) => /bpmn|process|workflow|فرآیند/i.test(`${d.name} ${d.description || ""}`)) || dsRes[0];
          setCorrDatasetId(proc.id);
        }
        if (!nlpDsId) {
          setNlpDsId(dsRes[0].id);
        }
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
  }, [corrDatasetId, nlpDsId, selectedColId, showToast]);

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
      showToast(`ثبت شد: ${res.ingested} · تکراری: ${res.deduplicated} · قرنطینه: ${res.unparsed_quarantined}`);
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

  const handleTriageAlert = async (alertId: string, action: string) => {
    try {
      const res = await apiFetch<any>(`/security-intel/alerts/${alertId}/triage`, {
        method: "POST",
        body: JSON.stringify({
          action,
          note: action === "escalate_tier2" ? "ارجاع به کارشناس سطح ۲ برای بررسی عمیق" : "",
          false_positive_reason: action === "false_positive" ? "بررسی دستی سطح ۱: فعالیت مجاز سازمان" : "",
        }),
      });
      showToast(
        action === "escalate_tier2"
          ? `هشدار به Tier 2 ارتقا یافت${res.escalated_incident_id ? " و پرونده رخداد باز شد" : ""}`
          : "وضعیت تریاژ هشدار به‌روزرسانی شد"
      );
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

  const handleUpdateIncidentLifecycle = async (inc: any, newStatus?: string) => {
    const draft = incNotes[inc.id] || {
      hypothesis: inc.hypothesis_notes_fa || "",
      finalReport: inc.final_report_fa || "",
    };
    try {
      await apiFetch(`/security-intel/incidents/${inc.id}/lifecycle`, {
        method: "POST",
        body: JSON.stringify({
          status: newStatus,
          hypothesis_notes_fa: draft.hypothesis,
          final_report_fa: draft.finalReport,
        }),
      });
      showToast("چرخه حیات و گزارش رخداد به‌روزرسانی شد");
      await loadAll();
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleCreateClsRule = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await apiFetch("/security-intel/classification-rules", {
        method: "POST",
        body: JSON.stringify({
          rule_kind: ruleKind,
          name: ruleName,
          pattern: rulePattern,
          target_value: ruleTarget,
        }),
      });
      setRuleName("");
      setRulePattern("");
      showToast("قانون طبقه‌بندی امنیتی ذخیره شد");
      await loadAll();
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleClassifyDataset = async () => {
    if (!nlpDsId) return;
    try {
      const res = await apiFetch<any>(`/security-intel/classify-dataset/${nlpDsId}`, {
        method: "POST",
        body: JSON.stringify({ limit: 500 }),
      });
      setNlpResult(res);
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleScanExposures = async () => {
    try {
      const res = await apiFetch<any>("/security-intel/exposures/scan", { method: "POST" });
      showToast(`اسکن انجام شد: ${res.scanned_assets} دارایی · ${res.cve_matches} تطبیق CVE · ${res.needs_review} نیازمند بررسی`);
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
              {isFa ? "مرکز عملیات امنیت (SOC Tier 1/2) و هوش تهدیدات" : "SOC Tier 1/2 & Security Log Intelligence"}
            </h1>
            <p className="text-xs mt-1" style={{ color: "var(--bi-muted)" }}>
              {isFa
                ? "پایش رخدادها، تریاژ سطح ۱ و ۲، موتور NLP فارسی وضعیت با ۱۵ رده تهدید، مدیریت آسیب‌پذیری CVE/CVSS و همبستگی فرآیند"
                : "Structured SOC ingestion, Tier 1/2 triage, bilingual NLP 15-category classification, CVE/CVSS exposures, and cross-domain correlation"}
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

        {/* SOC Tab Navigation */}
        <div className="flex flex-wrap gap-2 border-b pb-2 text-xs" style={{ borderColor: "var(--bi-border)" }}>
          {[
            { id: "operations", label: "۱. پایش رخداد و دریافت لاگ" },
            { id: "soc_triage", label: `۲. تریاژ Tier 1/2 و پرونده‌ها (${alerts.length}/${incidents.length})` },
            { id: "nlp_rules", label: "۳. موتور NLP فارسی و ۱۵ رده تهدید" },
            { id: "cve_exposure", label: `۴. آسیب‌پذیری CVE/CVSS و دارایی‌ها (${exposures.length})` },
            { id: "correlation", label: "۵. همبستگی میان‌دامنه‌ای فرآیند" },
          ].map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setTab(t.id as SocTab)}
              className={`px-3 py-1.5 rounded-lg font-semibold border transition ${
                tab === t.id ? "text-white" : "hover:bg-slate-500/10"
              }`}
              style={tab === t.id ? { background: "var(--bi-primary)", borderColor: "var(--bi-primary)" } : { borderColor: "var(--bi-border)" }}
            >
              {t.label}
            </button>
          ))}
        </div>

        {tab === "operations" && (
          <div className="space-y-4">
            {overview && (
              <>
                <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-2.5 text-xs">
                  {Object.entries(overview.action_breakdown || {}).map(([act, cnt]) => (
                    <div key={act} className="bi-card p-3">
                      <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>اقدام {act}</div>
                      <div className="text-lg font-bold mt-0.5">{Number(cnt).toLocaleString()}</div>
                    </div>
                  ))}
                  <div className="bi-card p-3">
                    <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>میانگین MTTA</div>
                    <div className="text-lg font-bold mt-0.5 text-sky-500">
                      {overview.soc_metrics?.avg_mtta_minutes != null ? `${overview.soc_metrics.avg_mtta_minutes}m` : "—"}
                    </div>
                  </div>
                  <div className="bi-card p-3">
                    <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>میانگین MTTR</div>
                    <div className="text-lg font-bold mt-0.5 text-emerald-500">
                      {overview.soc_metrics?.avg_mttr_minutes != null ? `${overview.soc_metrics.avg_mttr_minutes}m` : "—"}
                    </div>
                  </div>
                </div>

                {(overview.threat_category_breakdown || []).length > 0 && (
                  <div className="bi-card p-3.5 space-y-2 text-xs">
                    <div className="font-bold">پراکندگی بر اساس ۱۵ رده تهدید:</div>
                    <div className="flex flex-wrap gap-2">
                      {overview.threat_category_breakdown.map((c: any) => (
                        <span key={c.key} className="px-2.5 py-1 rounded-lg border text-[11px]" style={{ borderColor: "var(--bi-border)" }}>
                          {c.label_fa}: <b>{c.count}</b>
                        </span>
                      ))}
                    </div>
                  </div>
                )}

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
                  پردازش، طبقه‌بندی NLP و ذخیره رویدادها
                </button>
              </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 text-xs">
              <div className="lg:col-span-2 bi-card p-4 space-y-2.5">
                <div className="flex items-center justify-between">
                  <h2 className="text-sm font-bold">رویدادهای نرمال‌شده و طبقه‌بندی‌شده ({eventsData?.total || 0})</h2>
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
                        <th className="py-1.5 text-start">اقدام / شدت</th>
                        <th className="py-1.5 text-start">رده تهدید</th>
                        <th className="py-1.5 text-start">وضعیت استخراج‌شده</th>
                        <th className="py-1.5 text-start">کاربر / میزبان</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(eventsData?.events || []).map((ev: any) => (
                        <tr key={ev.id} className="border-b" style={{ borderColor: "var(--bi-border)" }} title={ev.extraction_reason || ""}>
                          <td className="py-1.5 whitespace-nowrap">{String(ev.event_time || "").slice(0, 19).replace("T", " ")}</td>
                          <td className="py-1.5 font-semibold">{ev.action} · {ev.severity}</td>
                          <td className="py-1.5">{ev.threat_category_fa || ev.threat_category}</td>
                          <td className="py-1.5">
                            <div>{STATUS_LABELS_FA[ev.threat_status] || ev.threat_status}</div>
                            {ev.extraction_reason && <div className="text-[10px] opacity-65 truncate max-w-[180px]">{ev.extraction_reason}</div>}
                          </td>
                          <td className="py-1.5">{[ev.username, ev.hostname].filter(Boolean).join(" @ ") || "—"}</td>
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
          </div>
        )}

        {tab === "soc_triage" && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 text-xs">
            <div className="bi-card p-4 space-y-3">
              <h2 className="text-sm font-bold flex items-center gap-2">
                <Shield className="w-4 h-4 text-amber-500" />
                صف تریاژ Tier 1 و ارتقا به Tier 2 ({alerts.length})
              </h2>
              <div className="space-y-2.5 max-h-[540px] overflow-y-auto">
                {alerts.map((al) => (
                  <div key={al.id} className="p-3 rounded-xl border space-y-2" style={{ borderColor: "var(--bi-border)" }}>
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <div className="font-bold flex items-center gap-2 flex-wrap">
                          <span>{al.title}</span>
                          <span className="px-1.5 py-0.5 rounded bg-slate-500/15 text-[10px]">Tier {al.tier || 1}</span>
                          {(al.duplicate_count || 1) > 1 && (
                            <span className="px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-500 text-[10px]">
                              تکرار تجمیع‌شده: {al.duplicate_count}
                            </span>
                          )}
                          {al.false_positive && (
                            <span className="px-1.5 py-0.5 rounded bg-slate-500/20 text-[10px]">مثبت کاذب</span>
                          )}
                        </div>
                        <div className="text-[11px] mt-0.5">
                          مشاهده: <b>{al.observed_value}</b> · خط مبنا: <b>{al.baseline_value} ± {al.baseline_stddev}</b> · وضعیت: <b>{al.status}</b>
                        </div>
                      </div>
                      <span className="px-2 py-0.5 rounded bg-rose-500/15 text-rose-500 text-[10px] font-bold shrink-0">{al.severity}</span>
                    </div>
                    <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>{al.limitations_fa}</div>
                    <div className="flex flex-wrap gap-1.5 pt-1">
                      <button
                        type="button"
                        onClick={() => handleTriageAlert(al.id, "acknowledge")}
                        className="px-2 py-1 rounded border text-[10px] hover:bg-blue-500/10"
                        style={{ borderColor: "var(--bi-border)" }}
                      >
                        تأیید دریافت (Ack)
                      </button>
                      <button
                        type="button"
                        onClick={() => handleTriageAlert(al.id, "false_positive")}
                        className="px-2 py-1 rounded border text-[10px] hover:bg-slate-500/15"
                        style={{ borderColor: "var(--bi-border)" }}
                      >
                        علامت مثبت کاذب
                      </button>
                      <button
                        type="button"
                        onClick={() => handleTriageAlert(al.id, "escalate_tier2")}
                        className="px-2 py-1 rounded bg-violet-600 text-white text-[10px] font-semibold inline-flex items-center gap-1"
                      >
                        <ArrowUpRight className="w-3 h-3" /> ارتقا به Tier 2 و بازکردن پرونده
                      </button>
                      <button
                        type="button"
                        onClick={() => handleCreateIncidentFromAlert(al)}
                        className="px-2 py-1 rounded border text-[10px] hover:bg-rose-500/10"
                        style={{ borderColor: "var(--bi-border)" }}
                      >
                        + رخداد مشورتی
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="bi-card p-4 space-y-3">
              <h2 className="text-sm font-bold flex items-center gap-2">
                <ShieldAlert className="w-4 h-4 text-rose-500" />
                پرونده‌های بررسی Tier 2 و چرخه حیات (MTTA/MTTR) ({incidents.length})
              </h2>
              <div className="space-y-3 max-h-[540px] overflow-y-auto">
                {incidents.map((inc) => {
                  const draft = incNotes[inc.id] || {
                    hypothesis: inc.hypothesis_notes_fa || "",
                    finalReport: inc.final_report_fa || "",
                  };
                  return (
                    <div key={inc.id} className="p-3 rounded-xl border space-y-2" style={{ borderColor: "var(--bi-border)" }}>
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-bold">{inc.title}</span>
                        <span className="px-2 py-0.5 rounded bg-sky-500/15 text-sky-500 text-[10px] font-semibold">{inc.status}</span>
                      </div>
                      <div className="flex flex-wrap gap-3 text-[10px]" style={{ color: "var(--bi-muted)" }}>
                        <span>MTTA: <b>{inc.mtta_minutes != null ? `${inc.mtta_minutes} دقیقه` : "—"}</b></span>
                        <span>MTTR: <b>{inc.mttr_minutes != null ? `${inc.mttr_minutes} دقیقه` : "—"}</b></span>
                        {inc.blast_radius?.affected_hosts?.length > 0 && (
                          <span>میزبان‌ها: <b>{inc.blast_radius.affected_hosts.join(", ")}</b></span>
                        )}
                      </div>
                      <div className="text-[11px]">{inc.recommended_action_fa}</div>
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                        <input
                          value={draft.hypothesis}
                          onChange={(e) => setIncNotes({ ...incNotes, [inc.id]: { ...draft, hypothesis: e.target.value } })}
                          placeholder="فرضیه تحقیق Tier 2..."
                          className="px-2 py-1 rounded border bg-transparent text-[11px]"
                          style={{ borderColor: "var(--bi-border)" }}
                        />
                        <input
                          value={draft.finalReport}
                          onChange={(e) => setIncNotes({ ...incNotes, [inc.id]: { ...draft, finalReport: e.target.value } })}
                          placeholder="گزارش نهایی / جمع‌بندی..."
                          className="px-2 py-1 rounded border bg-transparent text-[11px]"
                          style={{ borderColor: "var(--bi-border)" }}
                        />
                      </div>
                      <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
                        <div className="flex gap-1.5">
                          <button
                            type="button"
                            onClick={() => handleUpdateIncidentLifecycle(inc, "investigating")}
                            className="px-2 py-0.5 rounded border text-[10px]"
                            style={{ borderColor: "var(--bi-border)" }}
                          >
                            شروع بررسی (Investigating)
                          </button>
                          <button
                            type="button"
                            onClick={() => handleUpdateIncidentLifecycle(inc, "resolved")}
                            className="px-2 py-0.5 rounded bg-blue-600 text-white text-[10px] font-semibold"
                          >
                            بستن و محاسبه MTTR
                          </button>
                        </div>
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
                  );
                })}
              </div>
            </div>
          </div>
        )}

        {tab === "nlp_rules" && (
          <div className="space-y-4 text-xs">
            <div className="bi-card p-4 space-y-2.5">
              <h2 className="text-sm font-bold flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-violet-500" />
                ۱۵ رده استاندارد طبقه‌بندی تهدیدات امنیتی
              </h2>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2">
                {categories.map((c, idx) => (
                  <div key={c.key} className="p-2 rounded-lg border" style={{ borderColor: "var(--bi-border)" }}>
                    <div className="font-bold">{idx + 1}. {c.label_fa}</div>
                    <div className="text-[10px] font-mono opacity-65">{c.key} ({c.default_severity})</div>
                  </div>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <div className="bi-card p-4 space-y-3">
                <h2 className="text-sm font-bold">قوانین سفارشی استخراج وضعیت و رده تهدید (Regex / NLP فارسی)</h2>
                <form onSubmit={handleCreateClsRule} className="space-y-2">
                  <div className="grid grid-cols-2 gap-2">
                    <select
                      value={ruleKind}
                      onChange={(e) => setRuleKind(e.target.value as any)}
                      className="px-2.5 py-1.5 rounded border bg-transparent"
                      style={{ borderColor: "var(--bi-border)" }}
                    >
                      <option value="status_map">نگاشت وضعیت متنی (status_map)</option>
                      <option value="category_map">نگاشت رده تهدید (category_map)</option>
                    </select>
                    <input
                      value={ruleName}
                      onChange={(e) => setRuleName(e.target.value)}
                      placeholder="نام قانون (مثلاً: تشخیص قرنطینه EDR)"
                      className="px-2.5 py-1.5 rounded border bg-transparent"
                      style={{ borderColor: "var(--bi-border)" }}
                      required
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <input
                      value={rulePattern}
                      onChange={(e) => setRulePattern(e.target.value)}
                      placeholder="الگوی Regex (مثلاً: ایزوله|quarantine)"
                      className="px-2.5 py-1.5 rounded border font-mono bg-transparent"
                      style={{ borderColor: "var(--bi-border)" }}
                      required
                    />
                    <input
                      value={ruleTarget}
                      onChange={(e) => setRuleTarget(e.target.value)}
                      placeholder="مقدار هدف (مثلاً: blocked_quarantined)"
                      className="px-2.5 py-1.5 rounded border font-mono bg-transparent"
                      style={{ borderColor: "var(--bi-border)" }}
                      required
                    />
                  </div>
                  <button type="submit" className="px-3 py-1.5 rounded text-white font-semibold" style={{ background: "var(--bi-primary)" }}>
                    افزودن قانون NLP
                  </button>
                </form>

                <div className="space-y-1.5 max-h-56 overflow-y-auto">
                  {clsRules.map((r) => (
                    <div key={r.id} className="p-2 rounded border flex items-center justify-between gap-2" style={{ borderColor: "var(--bi-border)" }}>
                      <div>
                        <div className="font-bold">{r.name} <span className="opacity-60 font-mono text-[10px]">({r.rule_kind})</span></div>
                        <div className="text-[10px] font-mono opacity-80">/{r.pattern}/ → {r.target_value}</div>
                      </div>
                      <button
                        type="button"
                        onClick={() => apiFetch(`/security-intel/classification-rules/${r.id}`, { method: "DELETE" }).then(loadAll)}
                        className="p-1 text-red-500"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  ))}
                </div>
              </div>

              <div className="bi-card p-4 space-y-3">
                <h2 className="text-sm font-bold flex items-center gap-2">
                  <FileSearch className="w-4 h-4 text-emerald-500" />
                  تحلیل و طبقه‌بندی هوشمند فایل اکسل/لاگ امنیتی
                </h2>
                <div className="flex gap-2">
                  <select
                    value={nlpDsId}
                    onChange={(e) => setNlpDsId(e.target.value)}
                    className="flex-1 px-2.5 py-1.5 rounded border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  >
                    {datasets.map((d) => (
                      <option key={d.id} value={d.id}>{d.name}</option>
                    ))}
                  </select>
                  <button
                    type="button"
                    onClick={handleClassifyDataset}
                    className="px-3 py-1.5 rounded bg-emerald-600 text-white font-semibold"
                  >
                    اجرای طبقه‌بندی NLP
                  </button>
                </div>
                {nlpResult && (
                  <div className="space-y-2">
                    <div className="flex flex-wrap gap-2">
                      {(nlpResult.category_breakdown || []).map((c: any) => (
                        <span key={c.key} className="px-2 py-0.5 rounded border text-[11px]" style={{ borderColor: "var(--bi-border)" }}>
                          {c.label_fa}: <b>{c.count}</b>
                        </span>
                      ))}
                    </div>
                    <div className="max-h-48 overflow-y-auto space-y-1">
                      {(nlpResult.samples || []).map((s: any) => (
                        <div key={s.row_index} className="p-2 rounded border text-[10px]" style={{ borderColor: "var(--bi-border)" }}>
                          <div className="font-bold">{s.threat_category_fa} · {STATUS_LABELS_FA[s.threat_status] || s.threat_status}</div>
                          <div className="opacity-75 truncate">{s.source_text}</div>
                          <div className="text-blue-500">{s.extraction_reason}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {tab === "cve_exposure" && (
          <div className="space-y-4 text-xs">
            <div className="bi-card p-4 flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 className="text-sm font-bold flex items-center gap-2">
                  <Bug className="w-4 h-4 text-rose-500" />
                  اسکن آسیب‌پذیری‌های نرم‌افزاری (CVE / CVSS) و وضعیت دارایی‌ها
                </h2>
                <p className="text-[11px] mt-0.5" style={{ color: "var(--bi-muted)" }}>
                  دارایی‌هایی که نام/نسخه نرم‌افزار در لاگ ندارند هرگز CVE جعلی نمی‌گیرند و صراحتاً با وضعیت NEEDS_REVIEW علامت‌گذاری می‌شوند.
                </p>
              </div>
              <button
                type="button"
                onClick={handleScanExposures}
                className="px-3 py-2 rounded-lg text-white font-semibold bg-rose-600 hover:bg-rose-700"
              >
                اسکن و تطبیق دارایی‌ها با کاتالوگ CVE
              </button>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <div className="bi-card p-4 space-y-2.5">
                <h3 className="text-sm font-bold">آسیب‌پذیری‌ها و ریسک دارایی‌ها ({exposures.length})</h3>
                <div className="space-y-2 max-h-96 overflow-y-auto">
                  {exposures.map((ex) => (
                    <div key={ex.id} className="p-2.5 rounded-lg border space-y-1" style={{ borderColor: "var(--bi-border)" }}>
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-bold">{ex.asset_name} ({ex.software_name} {ex.software_version})</span>
                        <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${ex.risk_level === "critical" ? "bg-red-500/15 text-red-500" : ex.risk_level === "needs_review" ? "bg-amber-500/15 text-amber-500" : "bg-blue-500/15 text-blue-500"}`}>
                          {ex.cve_id} {ex.cvss_score > 0 ? `(CVSS ${ex.cvss_score})` : ""}
                        </span>
                      </div>
                      <div className="text-[10px]" style={{ color: "var(--bi-muted)" }}>{ex.notes}</div>
                      <div className="flex items-center justify-between pt-1">
                        <span className="text-[10px]">تعداد رخداد مرتبط: <b>{ex.related_event_count}</b></span>
                        <select
                          value={ex.remediation_status}
                          onChange={(e) =>
                            apiFetch(`/security-intel/exposures/${ex.id}/status`, {
                              method: "POST",
                              body: JSON.stringify({ remediation_status: e.target.value }),
                            }).then(loadAll)
                          }
                          className="px-2 py-0.5 rounded border bg-transparent text-[10px]"
                          style={{ borderColor: "var(--bi-border)" }}
                        >
                          <option value="open">باز (Open)</option>
                          <option value="mitigated">کاهش‌یافته (Mitigated)</option>
                          <option value="patched">پچ‌شده (Patched)</option>
                          <option value="accepted_risk">پذیرش ریسک</option>
                        </select>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="bi-card p-4 space-y-2.5">
                <h3 className="text-sm font-bold">کاتالوگ CVE / CVSS فعال ({vulns.length})</h3>
                <div className="space-y-2 max-h-96 overflow-y-auto">
                  {vulns.map((v) => (
                    <div key={v.id} className="p-2.5 rounded-lg border space-y-1" style={{ borderColor: "var(--bi-border)" }}>
                      <div className="flex items-center justify-between">
                        <span className="font-bold font-mono">{v.cve_id} · {v.software_product}</span>
                        <span className="px-2 py-0.5 rounded bg-rose-500/15 text-rose-500 text-[10px] font-bold">CVSS {v.cvss_score}</span>
                      </div>
                      <div className="text-[11px]">{v.summary}</div>
                      <div className="text-[10px] font-mono opacity-65">Pattern: {v.affected_version_pattern}</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )}

        {tab === "correlation" && (
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
        )}
      </div>
    </AppShell>
  );
}
