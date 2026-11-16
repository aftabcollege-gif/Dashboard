"use client";

import React, { useEffect, useState } from "react";
import { CheckCircle2, Clock, Play, Plus, RefreshCw, XCircle } from "lucide-react";
import AppShell from "@/components/layout/AppShell";
import { apiFetch } from "@/lib/api";
import { useBi } from "@/lib/context";

export default function EtlPage() {
  const { user, showToast } = useBi();
  const [monitor, setMonitor] = useState<any>(null);
  const [sources, setSources] = useState<any[]>([]);
  const [datasets, setDatasets] = useState<any[]>([]);

  // Create Pipeline Form
  const [pName, setPName] = useState("");
  const [sourceId, setSourceId] = useState("");
  const [targetDsId, setTargetDsId] = useState("");
  const [sourceQuery, setSourceQuery] = useState("SELECT * FROM erp_orders");
  const [extractMode, setExtractMode] = useState("incremental");
  const [pkCol, setPkCol] = useState("order_id");
  const [intervalMin, setIntervalMin] = useState(15);
  const [scheduleType, setScheduleType] = useState("interval");
  const [cronExpr, setCronExpr] = useState("0 * * * *");

  const load = () => {
    apiFetch("/etl/monitor").then(setMonitor).catch(() => {});
    apiFetch("/data-sources").then((s) => { setSources(s); if (s[0] && !sourceId) setSourceId(s[0].id); }).catch(() => {});
    apiFetch("/datasets").then((d) => { setDatasets(d); if (d[0] && !targetDsId) setTargetDsId(d[0].id); }).catch(() => {});
  };

  useEffect(() => {
    if (user) load();
  }, [user]);

  const handleCreatePipeline = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const created = await apiFetch<{ id: string }>("/etl/pipelines", {
        method: "POST",
        body: JSON.stringify({
          name: pName,
          data_source_id: sourceId,
          target_dataset_id: targetDsId,
          source_query: sourceQuery,
          extract_mode: extractMode,
          primary_key_column: pkCol || null,
        }),
      });
      await apiFetch(`/etl/pipelines/${created.id}/schedule`, {
        method: "PUT",
        body: JSON.stringify({
          schedule_type: scheduleType,
          interval_minutes: intervalMin,
          cron_expression: scheduleType === "cron" ? cronExpr : null,
          timezone: "UTC",
          is_enabled: true,
        }),
      });
      setPName("");
      showToast("ETL pipeline & schedule created");
      load();
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleRunNow = async (id: string) => {
    try {
      const res = await apiFetch<any>(`/etl/pipelines/${id}/run`, { method: "POST" });
      showToast(`ETL Run ${res.status}: extracted ${res.rows_extracted}, inserted ${res.rows_inserted}, unchanged ${res.rows_unchanged} (${res.duration_ms}ms)`);
      load();
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const pipelines = monitor?.pipelines || [];
  const runs = monitor?.recent_runs || [];
  const mvs = monitor?.materialized_views || [];

  return (
    <AppShell>
      <div className="space-y-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-bold flex items-center gap-2">
              <RefreshCw className="w-5 h-5" style={{ color: "var(--bi-primary)" }} /> Automated ETL, Scheduling & Data Warehouse
            </h1>
            <p className="text-xs mt-1" style={{ color: "var(--bi-muted)" }}>
              Decoupled staging-to-warehouse pipeline with atomic table swap, incremental CDC/hash detection, concurrency locking & retry backoff.
            </p>
          </div>
          {monitor?.scheduler && (
            <span className="px-3 py-1.5 rounded-lg border text-xs font-semibold text-emerald-500" style={{ borderColor: "var(--bi-border)", background: "var(--bi-card)" }}>
              Scheduler: {monitor.scheduler.running ? `Active (${monitor.scheduler.jobs?.length || 0} jobs)` : "Stopped"}
            </span>
          )}
        </div>

        {/* ETL KPIs */}
        {monitor && (
          <div className="grid grid-cols-2 lg:grid-cols-5 gap-3 text-xs">
            <div className="bi-card p-3.5"><div className="opacity-70">Pipelines</div><div className="text-xl font-bold mt-1">{monitor.kpis.pipelines_count}</div></div>
            <div className="bi-card p-3.5"><div className="opacity-70">Warehouse Datasets</div><div className="text-xl font-bold mt-1">{monitor.kpis.datasets_count}</div></div>
            <div className="bi-card p-3.5"><div className="opacity-70">Warehouse Storage</div><div className="text-xl font-bold mt-1">{(monitor.kpis.warehouse_storage_bytes / (1024 * 1024)).toFixed(2)} MB</div></div>
            <div className="bi-card p-3.5"><div className="opacity-70">Succeeded Jobs</div><div className="text-xl font-bold mt-1 text-emerald-500">{monitor.kpis.jobs_succeeded}</div></div>
            <div className="bi-card p-3.5"><div className="opacity-70">Failed Jobs</div><div className="text-xl font-bold mt-1 text-red-500">{monitor.kpis.jobs_failed}</div></div>
          </div>
        )}

        {/* Create Pipeline + Active Pipelines */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
          <form onSubmit={handleCreatePipeline} className="bi-card p-5 space-y-3 text-xs">
            <h2 className="text-sm font-bold flex items-center gap-1.5"><Plus className="w-4 h-4" /> New ETL Pipeline & Schedule</h2>
            <input value={pName} onChange={(e) => setPName(e.target.value)} placeholder="Pipeline Name" className="w-full px-3 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} required />
            <select value={sourceId} onChange={(e) => setSourceId(e.target.value)} className="w-full px-3 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
              {sources.map((s) => <option key={s.id} value={s.id}>Source: {s.name}</option>)}
            </select>
            <select value={targetDsId} onChange={(e) => setTargetDsId(e.target.value)} className="w-full px-3 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
              {datasets.map((d) => <option key={d.id} value={d.id}>Target Warehouse: {d.name}</option>)}
            </select>
            <textarea rows={2} value={sourceQuery} onChange={(e) => setSourceQuery(e.target.value)} className="w-full px-3 py-2 rounded border font-mono bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
            <div className="grid grid-cols-2 gap-2">
              <select value={extractMode} onChange={(e) => setExtractMode(e.target.value)} className="px-2 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                <option value="full">Full Snapshot</option>
                <option value="incremental">Incremental Watermark</option>
                <option value="cdc_hash">CDC Hash Detection</option>
              </select>
              <input value={pkCol} onChange={(e) => setPkCol(e.target.value)} placeholder="Primary Key Col" className="px-2 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <select value={scheduleType} onChange={(e) => setScheduleType(e.target.value)} className="px-2 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                <option value="interval">Interval (Minutes)</option>
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
                <option value="monthly">Monthly</option>
                <option value="cron">Custom Cron</option>
              </select>
              {scheduleType === "cron" ? (
                <input value={cronExpr} onChange={(e) => setCronExpr(e.target.value)} placeholder="0 * * * *" className="px-2 py-2 rounded border font-mono bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
              ) : (
                <input type="number" min={1} value={intervalMin} onChange={(e) => setIntervalMin(Number(e.target.value))} placeholder="Minutes" className="px-2 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
              )}
            </div>
            <button type="submit" className="w-full py-2 rounded-lg text-white font-semibold" style={{ background: "var(--bi-primary)" }}>
              Create Pipeline
            </button>
          </form>

          <div className="lg:col-span-2 bi-card p-5 space-y-3 text-xs">
            <h2 className="text-sm font-bold">Configured ETL Pipelines ({pipelines.length})</h2>
            <div className="space-y-2.5">
              {pipelines.map((p: any) => (
                <div key={p.id} className="p-3.5 rounded-lg border flex flex-wrap items-center justify-between gap-3" style={{ borderColor: "var(--bi-border)" }}>
                  <div>
                    <div className="font-bold text-sm flex items-center gap-2">
                      {p.name}
                      <span className="text-[10px] px-2 py-0.5 rounded bg-blue-500/15 text-blue-500 uppercase">{p.extract_mode}</span>
                    </div>
                    <div className="text-[11px] opacity-75 mt-1 font-mono truncate max-w-lg">{p.source_query}</div>
                    <div className="text-[11px] opacity-75 mt-1 flex flex-wrap items-center gap-3">
                      <span className="flex items-center gap-1">
                        <Clock className="w-3 h-3" /> {p.schedule?.schedule_type || "manual"} ({p.schedule?.interval_minutes}m)
                      </span>
                      {p.last_run && (
                        <span>
                          Last: <b>{p.last_run.status}</b> ({p.last_run.rows_extracted} ext / {p.last_run.rows_inserted} ins / {p.last_run.rows_unchanged} skip · {Number(p.last_run.duration_ms).toFixed(1)}ms)
                        </span>
                      )}
                    </div>
                  </div>
                  <button
                    onClick={() => handleRunNow(p.id)}
                    className="px-3.5 py-2 rounded-lg text-white font-semibold flex items-center gap-1.5"
                    style={{ background: "var(--bi-primary)" }}
                  >
                    <Play className="w-3.5 h-3.5" /> Run Now
                  </button>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Materialized Views */}
        {mvs.length > 0 && (
          <div className="bi-card p-5 space-y-2 text-xs">
            <h2 className="text-sm font-bold">Pre-Aggregated Materialized Warehouse Views</h2>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              {mvs.map((mv: any) => (
                <div key={mv.id} className="p-3 rounded border" style={{ borderColor: "var(--bi-border)" }}>
                  <div className="font-bold font-mono">{mv.view_name}</div>
                  <div className="opacity-75 mt-1">Granularity: {mv.granularity} · {mv.row_count} rows</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ETL Execution Logs */}
        <div className="bi-card p-5 space-y-3 text-xs">
          <h2 className="text-sm font-bold">Recent ETL Execution Runs</h2>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b text-start" style={{ borderColor: "var(--bi-border)" }}>
                  <th className="py-2 text-start">Status</th>
                  <th className="py-2 text-start">Pipeline</th>
                  <th className="py-2 text-start">Extracted</th>
                  <th className="py-2 text-start">Inserted / Updated</th>
                  <th className="py-2 text-start">Unchanged</th>
                  <th className="py-2 text-start">Duration</th>
                  <th className="py-2 text-start">Log Summary</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r: any) => (
                  <tr key={r.id} className="border-b" style={{ borderColor: "var(--bi-border)" }}>
                    <td className="py-2">
                      {r.status === "succeeded" ? (
                        <span className="inline-flex items-center gap-1 text-emerald-500 font-semibold"><CheckCircle2 className="w-3.5 h-3.5" /> Succeeded</span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-red-500 font-semibold"><XCircle className="w-3.5 h-3.5" /> {r.status}</span>
                      )}
                    </td>
                    <td className="py-2 font-mono">{r.pipeline_id.slice(0, 8)}</td>
                    <td className="py-2">{r.rows_extracted}</td>
                    <td className="py-2">{r.rows_inserted} / {r.rows_updated}</td>
                    <td className="py-2">{r.rows_unchanged}</td>
                    <td className="py-2">{Number(r.duration_ms).toFixed(1)} ms</td>
                    <td className="py-2 opacity-75 truncate max-w-md">{r.log_text}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </AppShell>
  );
}
