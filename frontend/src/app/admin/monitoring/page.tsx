"use client";

import React, { useEffect, useState } from "react";
import { Activity, Cpu, Download, Search, Trash2 } from "lucide-react";
import AppShell from "@/components/layout/AppShell";
import { apiDownload, apiFetch } from "@/lib/api";
import { useBi } from "@/lib/context";

export default function MonitoringAdminPage() {
  const { user, showToast } = useBi();
  const [auditLogs, setAuditLogs] = useState<any[]>([]);
  const [perf, setPerf] = useState<any>(null);
  const [search, setSearch] = useState("");
  const [selectedDiff, setSelectedDiff] = useState<any>(null);

  const load = () => {
    apiFetch(`/monitoring/audit-logs?search=${encodeURIComponent(search)}&limit=100`).then(setAuditLogs).catch(() => {});
    apiFetch("/monitoring/performance").then(setPerf).catch(() => {});
  };

  useEffect(() => {
    if (user) load();
  }, [user]);

  const handleInvalidateCache = async () => {
    await apiFetch("/monitoring/cache/invalidate", { method: "POST" });
    showToast("Layer 3 Cache invalidated");
    load();
  };

  return (
    <AppShell>
      <div className="space-y-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-bold flex items-center gap-2">
              <Activity className="w-5 h-5" style={{ color: "var(--bi-primary)" }} /> Performance Monitor & Security Audit Trail
            </h1>
            <p className="text-xs mt-1" style={{ color: "var(--bi-muted)" }}>
              Three-layer observability: Cache hit ratio, slowest queries, system CPU/RAM/DuckDB metrics, and immutable before/after audit diffs.
            </p>
          </div>
          <div className="flex gap-2">
            <button onClick={handleInvalidateCache} className="px-3 py-2 rounded-lg border text-xs font-semibold flex items-center gap-1" style={{ borderColor: "var(--bi-border)", background: "var(--bi-card)" }}>
              <Trash2 className="w-3.5 h-3.5 text-amber-500" /> Clear Cache
            </button>
            <button onClick={() => apiDownload("/monitoring/audit-logs/export", "audit_logs.csv")} className="px-3 py-2 rounded-lg text-xs font-semibold text-white flex items-center gap-1" style={{ background: "var(--bi-primary)" }}>
              <Download className="w-3.5 h-3.5" /> Export Audit CSV
            </button>
          </div>
        </div>

        {/* Performance Telemetry */}
        {perf && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 lg:grid-cols-5 gap-3 text-xs">
              <div className="bi-card p-3.5"><div className="opacity-70">Cache Hit Ratio</div><div className="text-xl font-bold text-emerald-500 mt-1">{perf.cache.hit_ratio_percent}%</div></div>
              <div className="bi-card p-3.5"><div className="opacity-70">Cache Hits / Misses</div><div className="text-xl font-bold mt-1">{perf.cache.hits} / {perf.cache.misses}</div></div>
              <div className="bi-card p-3.5"><div className="opacity-70">Avg Widget / Dash</div><div className="text-xl font-bold mt-1">{perf.averages.widget_load_ms} / {perf.averages.dashboard_load_ms} ms</div></div>
              <div className="bi-card p-3.5"><div className="opacity-70">CPU / Process RAM</div><div className="text-xl font-bold mt-1">{perf.system.cpu_percent}% / {perf.system.memory_mb} MB</div></div>
              <div className="bi-card p-3.5"><div className="opacity-70">Warehouse DuckDB</div><div className="text-xl font-bold mt-1">{(perf.system.warehouse_size_bytes / (1024 * 1024)).toFixed(2)} MB</div></div>
            </div>

            {perf.slowest_queries?.length > 0 && (
              <div className="bi-card p-4 text-xs space-y-2">
                <h2 className="text-sm font-bold flex items-center gap-1.5"><Cpu className="w-4 h-4 text-amber-500" /> Top Slowest Executed Queries</h2>
                <div className="space-y-1">
                  {perf.slowest_queries.map((q: any, i: number) => (
                    <div key={i} className="flex justify-between p-2 rounded border font-mono text-[11px]" style={{ borderColor: "var(--bi-border)" }}>
                      <span>Query Hash: {q.query_hash} ({q.status})</span>
                      <span className="font-bold text-amber-500">{q.execution_ms} ms ({q.rows_returned} rows)</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Audit Logs */}
        <div className="bi-card p-5 space-y-3 text-xs">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-bold">Audit & Security Logs ({auditLogs.length})</h2>
            <div className="flex gap-2">
              <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search action, username, resource..." className="px-3 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
              <button onClick={load} className="px-3 py-1.5 rounded border font-semibold flex items-center gap-1" style={{ borderColor: "var(--bi-border)" }}>
                <Search className="w-3.5 h-3.5" /> Filter
              </button>
            </div>
          </div>

          <div className="overflow-x-auto max-h-96">
            <table className="w-full">
              <thead>
                <tr className="border-b text-start" style={{ borderColor: "var(--bi-border)" }}>
                  <th className="py-2 text-start">Time</th>
                  <th className="py-2 text-start">Actor</th>
                  <th className="py-2 text-start">Action</th>
                  <th className="py-2 text-start">Target</th>
                  <th className="py-2 text-start">Status</th>
                  <th className="py-2 text-start">Diff</th>
                </tr>
              </thead>
              <tbody>
                {auditLogs.map((a) => (
                  <tr key={a.id} className="border-b" style={{ borderColor: "var(--bi-border)" }}>
                    <td className="py-2 whitespace-nowrap">{new Date(a.created_at).toLocaleString()}</td>
                    <td className="py-2 font-semibold">{a.username || "system"}</td>
                    <td className="py-2 font-mono">{a.action}</td>
                    <td className="py-2">{a.resource_type} {a.resource_id ? a.resource_id.slice(0, 8) : ""}</td>
                    <td className="py-2">
                      <span className={a.status === "success" ? "text-emerald-500 font-semibold" : "text-red-500 font-semibold"}>{a.status}</span>
                    </td>
                    <td className="py-2">
                      {(a.before || a.after) && (
                        <button onClick={() => setSelectedDiff(a)} className="underline text-blue-500">Inspect</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {selectedDiff && (
            <div className="p-3 rounded border font-mono text-[11px] grid grid-cols-2 gap-3" style={{ borderColor: "var(--bi-border)" }}>
              <div><div className="font-bold mb-1">Before:</div><pre className="overflow-x-auto">{JSON.stringify(selectedDiff.before, null, 2)}</pre></div>
              <div><div className="font-bold mb-1">After:</div><pre className="overflow-x-auto">{JSON.stringify(selectedDiff.after, null, 2)}</pre></div>
            </div>
          )}
        </div>
      </div>
    </AppShell>
  );
}
