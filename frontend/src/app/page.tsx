"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { Activity, ArrowRight, Bot, Database, FolderKanban, Layers, Plus, RefreshCw, ShieldCheck } from "lucide-react";
import AppShell from "@/components/layout/AppShell";
import { apiFetch } from "@/lib/api";
import { useBi } from "@/lib/context";

export default function HomePage() {
  const { user, t, showToast } = useBi();
  const [dashboards, setDashboards] = useState<any[]>([]);
  const [datasets, setDatasets] = useState<any[]>([]);
  const [etlMon, setEtlMon] = useState<any>(null);
  const [aiDatasetId, setAiDatasetId] = useState("");
  const [generating, setGenerating] = useState(false);

  useEffect(() => {
    if (!user) return;
    apiFetch("/dashboards").then(setDashboards).catch(() => {});
    apiFetch("/datasets").then((ds) => { setDatasets(ds); if (ds[0]) setAiDatasetId(ds[0].id); }).catch(() => {});
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
      showToast("AI Dashboard generated!");
      window.location.href = `/dashboards/${res.dashboard_id}`;
    } catch (err: any) {
      showToast(err.message);
    } finally {
      setGenerating(false);
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
          <div className="flex items-center gap-2">
            <Link
              href="/data-sources"
              className="px-4 py-2 rounded-lg border text-xs font-semibold flex items-center gap-1.5"
              style={{ borderColor: "var(--bi-border)", background: "var(--bi-card)" }}
            >
              <Database className="w-4 h-4" /> Connect Data / Upload Excel
            </Link>
            <Link
              href="/dashboards"
              className="px-4 py-2 rounded-lg text-xs font-semibold text-white flex items-center gap-1.5"
              style={{ background: "var(--bi-primary)" }}
            >
              <Plus className="w-4 h-4" /> Open Dashboards
            </Link>
          </div>
        </div>

        {/* Summary Cards */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="bi-card p-4">
            <div className="flex items-center justify-between text-xs" style={{ color: "var(--bi-muted)" }}>
              <span>Accessible Dashboards</span>
              <FolderKanban className="w-4 h-4 text-blue-500" />
            </div>
            <div className="text-2xl font-bold mt-2">{dashboards.length}</div>
          </div>
          <div className="bi-card p-4">
            <div className="flex items-center justify-between text-xs" style={{ color: "var(--bi-muted)" }}>
              <span>Warehouse Datasets</span>
              <Layers className="w-4 h-4 text-emerald-500" />
            </div>
            <div className="text-2xl font-bold mt-2">{datasets.length}</div>
          </div>
          <div className="bi-card p-4">
            <div className="flex items-center justify-between text-xs" style={{ color: "var(--bi-muted)" }}>
              <span>ETL Pipelines Active</span>
              <RefreshCw className="w-4 h-4 text-amber-500" />
            </div>
            <div className="text-2xl font-bold mt-2">{etlMon?.kpis?.pipelines_count ?? "—"}</div>
          </div>
          <div className="bi-card p-4">
            <div className="flex items-center justify-between text-xs" style={{ color: "var(--bi-muted)" }}>
              <span>Security Posture</span>
              <ShieldCheck className="w-4 h-4 text-violet-500" />
            </div>
            <div className="text-sm font-bold mt-2.5 truncate">
              {user?.effective_permissions?.is_super_admin ? "Super Admin" : (user?.effective_permissions?.roles || []).join(", ")}
            </div>
          </div>
        </div>

        {/* AI 1-Click Dashboard Builder Banner */}
        {datasets.length > 0 && (
          <div className="bi-card p-5 flex flex-wrap items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl bg-violet-500/15 text-violet-500 flex items-center justify-center">
                <Bot className="w-5 h-5" />
              </div>
              <div>
                <h2 className="text-sm font-bold">AI Assisted Dashboard Generator</h2>
                <p className="text-xs" style={{ color: "var(--bi-muted)" }}>
                  Automatically profiles dataset schema, extracts KPIs, sets drill rules & builds a full editable dashboard.
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <select
                value={aiDatasetId}
                onChange={(e) => setAiDatasetId(e.target.value)}
                className="px-3 py-2 rounded-lg border text-xs bg-transparent"
                style={{ borderColor: "var(--bi-border)" }}
              >
                {datasets.map((d) => <option key={d.id} value={d.id}>{d.name} ({d.row_count} rows)</option>)}
              </select>
              <button
                onClick={handleAiBuild}
                disabled={generating}
                className="px-4 py-2 rounded-lg text-xs font-semibold text-white bg-violet-600 hover:bg-violet-700"
              >
                {generating ? "Generating..." : "Auto-Generate Dashboard"}
              </button>
            </div>
          </div>
        )}

        {/* Dashboards Grid */}
        <div>
          <h2 className="text-sm font-bold mb-3">Enterprise Dashboards</h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {dashboards.map((d) => (
              <Link
                key={d.id}
                href={`/dashboards/${d.id}`}
                className="bi-card p-5 hover:scale-[1.01] transition flex flex-col justify-between gap-4"
              >
                <div>
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-bold">{d.title}</span>
                    {d.is_template && <span className="text-[10px] px-2 py-0.5 rounded bg-violet-500/15 text-violet-500 font-semibold">TEMPLATE</span>}
                  </div>
                  <p className="text-xs mt-1.5 line-clamp-2" style={{ color: "var(--bi-muted)" }}>{d.description}</p>
                </div>
                <div className="flex items-center justify-between text-xs font-semibold" style={{ color: "var(--bi-primary)" }}>
                  <span>{d.widget_count} widgets</span>
                  <span className="flex items-center gap-1">Open Workspace <ArrowRight className="w-3.5 h-3.5" /></span>
                </div>
              </Link>
            ))}
          </div>
        </div>
      </div>
    </AppShell>
  );
}
