"use client";

import React, { useEffect, useState } from "react";
import { CalendarClock, Copy, FileSpreadsheet, Play, Plus, Sparkles } from "lucide-react";
import AppShell from "@/components/layout/AppShell";
import { apiFetch } from "@/lib/api";
import { useBi } from "@/lib/context";

export default function ReportsPage() {
  const { user, locale, showToast } = useBi();
  const isFa = locale === "fa";
  const [dashboards, setDashboards] = useState<any[]>([]);
  const [schedules, setSchedules] = useState<any[]>([]);

  const [sName, setSName] = useState("");
  const [sDashId, setSDashId] = useState("");
  const [sFmt, setSFmt] = useState("pdf");
  const [sCron, setSCron] = useState("0 8 * * 1");
  const [sRecip, setSRecip] = useState("exec@enterprise.local");

  const load = () => {
    apiFetch("/dashboards").then((d) => { setDashboards(d); if (d[0] && !sDashId) setSDashId(d[0].id); }).catch(() => {});
    apiFetch("/exports/scheduled-reports").then(setSchedules).catch(() => {});
  };

  useEffect(() => {
    if (user) load();
  }, [user]);

  const handleCloneTemplate = async (id: string) => {
    try {
      const res = await apiFetch<{ id: string }>(`/dashboards/${id}/clone-template`, { method: "POST" });
      showToast("Template cloned to new editable dashboard!");
      window.location.href = `/dashboards/${res.id}`;
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleCreateSchedule = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await apiFetch("/exports/scheduled-reports", {
        method: "POST",
        body: JSON.stringify({
          name: sName,
          dashboard_id: sDashId,
          format: sFmt,
          cron_expression: sCron,
          recipients: sRecip.split(",").map((x) => x.trim()).filter(Boolean),
        }),
      });
      setSName("");
      showToast("Scheduled report saved");
      load();
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleRunReportNow = async (id: string) => {
    try {
      const res = await apiFetch<any>(`/exports/scheduled-reports/${id}/run`, { method: "POST" });
      const byteCount = res.bytes ?? res.size_bytes ?? 0;
      const fmt = (res.format || "pdf").toUpperCase();
      showToast(`Generated ${byteCount} byte report (${fmt})`);
      load();
    } catch (err: any) {
      showToast(err.message);
    }
  };

  return (
    <AppShell>
      <div className="space-y-6">
        <div>
          <h1 className="text-xl font-bold flex items-center gap-2">
            <FileSpreadsheet className="w-5 h-5" style={{ color: "var(--bi-primary)" }} />{" "}
            {isFa ? "گالری قالب‌ها و گزارش‌های زمان‌بندی‌شده" : "Templates & Scheduled Reports"}
          </h1>
          <p className="text-xs mt-1" style={{ color: "var(--bi-muted)" }}>
            {isFa
              ? "قالب‌های داشبورد سازمانی را با یک کلیک کپی کنید یا زمان‌بندی ارسال خودکار گزارش‌های PDF/Excel/CSV را تنظیم نمایید."
              : "Clone enterprise dashboard templates in 1 click or configure automated PDF/Excel/CSV report delivery schedules."}
          </p>
        </div>

        {/* Templates Gallery */}
        <div className="bi-card p-4 sm:p-5 space-y-3">
          <h2 className="text-sm font-bold flex items-center gap-1.5">
            <Sparkles className="w-4 h-4 text-violet-500" /> {isFa ? "گالری قالب‌های داشبورد" : "Dashboard Templates Gallery"}
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
            {dashboards.map((d) => (
              <div key={d.id} className="p-4 rounded-xl border flex flex-col justify-between gap-3" style={{ borderColor: "var(--bi-border)" }}>
                <div>
                  <div className="flex items-center justify-between gap-2">
                    <div className="font-bold text-sm">{d.title}</div>
                    {d.is_template && (
                      <span className="text-[10px] px-2 py-0.5 rounded bg-violet-500/15 text-violet-500 font-semibold shrink-0">
                        TEMPLATE
                      </span>
                    )}
                  </div>
                  <p className="opacity-75 mt-1">{d.description}</p>
                </div>
                <button onClick={() => handleCloneTemplate(d.id)} className="w-full py-2 rounded-lg border font-semibold flex items-center justify-center gap-1.5 hover:bg-blue-500/10" style={{ borderColor: "var(--bi-border)" }}>
                  <Copy className="w-3.5 h-3.5" /> {isFa ? "ساخت داشبورد جدید از این قالب" : "Clone as New Dashboard"}
                </button>
              </div>
            ))}
          </div>
        </div>

        {/* Scheduled Reports */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-5 text-xs">
          <form onSubmit={handleCreateSchedule} className="bi-card p-4 sm:p-5 space-y-3">
            <h2 className="text-sm font-bold flex items-center gap-1.5">
              <Plus className="w-4 h-4" /> {isFa ? "زمان‌بندی گزارش خودکار" : "Schedule Automated Report"}
            </h2>
            <input value={sName} onChange={(e) => setSName(e.target.value)} placeholder={isFa ? "عنوان زمان‌بندی" : "Schedule Name (e.g., Weekly Executive PDF)"} className="w-full px-3 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} required />
            <select value={sDashId} onChange={(e) => setSDashId(e.target.value)} className="w-full px-3 py-2 rounded border" style={{ background: "var(--bi-surface)", color: "var(--bi-text)", borderColor: "var(--bi-border)" }}>
              {dashboards.map((d) => <option key={d.id} value={d.id}>{d.title}</option>)}
            </select>
            <div className="grid grid-cols-2 gap-2">
              <select value={sFmt} onChange={(e) => setSFmt(e.target.value)} className="px-2 py-2 rounded border" style={{ background: "var(--bi-surface)", color: "var(--bi-text)", borderColor: "var(--bi-border)" }}>
                <option value="pdf">PDF Document</option>
                <option value="xlsx">Excel Workbook</option>
                <option value="csv">CSV Snapshot</option>
              </select>
              <input value={sCron} onChange={(e) => setSCron(e.target.value)} placeholder="Cron: 0 8 * * 1" className="px-2 py-2 rounded border font-mono bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
            </div>
            <input value={sRecip} onChange={(e) => setSRecip(e.target.value)} placeholder="Recipients (comma-separated emails)" className="w-full px-3 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
            <button type="submit" className="w-full py-2 rounded-lg text-white font-semibold" style={{ background: "var(--bi-primary)" }}>Create Schedule</button>
          </form>

          <div className="lg:col-span-2 bi-card p-5 space-y-3">
            <h2 className="text-sm font-bold flex items-center gap-1.5"><CalendarClock className="w-4 h-4" /> Active Schedules ({schedules.length})</h2>
            <div className="space-y-2">
              {schedules.map((s) => (
                <div key={s.id} className="p-3 rounded-lg border flex items-center justify-between gap-2" style={{ borderColor: "var(--bi-border)" }}>
                  <div>
                    <div className="font-bold">{s.name} ({(s.format || s.export_format || "pdf").toUpperCase()})</div>
                    <div className="opacity-75 text-[11px]">Cron: <code>{s.cron_expression}</code> · Recipients: {s.recipients?.join(", ")}</div>
                  </div>
                  <button onClick={() => handleRunReportNow(s.id)} className="px-3 py-1.5 rounded text-white font-semibold flex items-center gap-1" style={{ background: "var(--bi-primary)" }}>
                    <Play className="w-3 h-3" /> Run
                  </button>
                </div>
              ))}
              {schedules.length === 0 && <p className="opacity-60">No automated report schedules created yet.</p>}
            </div>
          </div>
        </div>
      </div>
    </AppShell>
  );
}
