"use client";

import React, { useEffect, useState } from "react";
import { BarChart2, GitBranch, Layers, Plus, Sparkles } from "lucide-react";
import AppShell from "@/components/layout/AppShell";
import { apiFetch } from "@/lib/api";
import { useBi } from "@/lib/context";

export default function DatasetsPage() {
  const { user, showToast } = useBi();
  const [datasets, setDatasets] = useState<any[]>([]);
  const [selected, setSelected] = useState<any>(null);
  const [preview, setPreview] = useState<any>(null);
  const [profile, setProfile] = useState<any>(null);
  const [lineage, setLineage] = useState<any>(null);
  const [metrics, setMetrics] = useState<any[]>([]);
  const [relationships, setRelationships] = useState<any[]>([]);

  // Semantic metric form
  const [mName, setMName] = useState("");
  const [mExpr, setMExpr] = useState("SUM(revenue)");
  const [mFmt, setMFmt] = useState("currency");

  // Calculated column form
  const [calcName, setCalcName] = useState("");
  const [calcExpr, setCalcExpr] = useState("revenue - cost");

  // Relationship / Join form
  const [relRightDs, setRelRightDs] = useState("");
  const [relLeftField, setRelLeftField] = useState("");
  const [relRightField, setRelRightField] = useState("");
  const [relJoinType, setRelJoinType] = useState("left");

  const load = () => {
    apiFetch("/datasets").then((ds) => { setDatasets(ds); if (!selected && ds[0]) selectDs(ds[0]); }).catch(() => {});
    apiFetch("/datasets/semantic-metrics").then(setMetrics).catch(() => {});
    apiFetch("/datasets/relationships").then(setRelationships).catch(() => {});
  };

  useEffect(() => {
    if (user) load();
  }, [user]);

  const selectDs = async (ds: any) => {
    setSelected(ds);
    const [pv, pf, lin] = await Promise.all([
      apiFetch(`/datasets/${ds.id}/preview?limit=30`).catch(() => null),
      apiFetch(`/datasets/${ds.id}/profile`).catch(() => null),
      apiFetch(`/datasets/${ds.id}/lineage`).catch(() => null),
    ]);
    setPreview(pv);
    setProfile(pf);
    setLineage(lin);
  };

  const handleAddCalculatedField = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selected || !calcName) return;
    try {
      await apiFetch(`/datasets/${selected.id}/calculated-fields`, {
        method: "POST",
        body: JSON.stringify({
          field_name: calcName,
          display_name: calcName,
          data_type: "float",
          semantic_role: "measure",
          expression: calcExpr,
        }),
      });
      setCalcName("");
      showToast("Calculated column saved to Semantic Model");
      load();
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleCreateMetric = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selected || !mName) return;
    try {
      await apiFetch("/datasets/semantic-metrics", {
        method: "POST",
        body: JSON.stringify({
          dataset_id: selected.id,
          name: mName,
          slug: mName.toLowerCase().replace(/[^a-z0-9]+/g, "_"),
          formula: mExpr,
          format_type: mFmt,
        }),
      });
      setMName("");
      showToast("Semantic metric added");
      load();
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleCreateRelationship = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selected || !relRightDs || !relLeftField || !relRightField) return;
    try {
      await apiFetch("/datasets/relationships", {
        method: "POST",
        body: JSON.stringify({
          left_dataset_id: selected.id,
          left_field: relLeftField,
          right_dataset_id: relRightDs,
          right_field: relRightField,
          join_type: relJoinType,
        }),
      });
      showToast("Dataset relationship saved");
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
            <Layers className="w-5 h-5" style={{ color: "var(--bi-primary)" }} /> Datasets, Semantic Layer & Lineage
          </h1>
          <p className="text-xs mt-1" style={{ color: "var(--bi-muted)" }}>
            Inspect Analytical Warehouse datasets, column distributions, CLS/masking preview, calculated expressions & lineage.
          </p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-4 gap-5">
          {/* Left Dataset List */}
          <div className="bi-card p-4 space-y-2">
            <h2 className="text-xs font-bold uppercase tracking-wider opacity-70">Warehouse Datasets</h2>
            {datasets.map((ds) => (
              <button
                key={ds.id}
                onClick={() => selectDs(ds)}
                className="w-full text-start p-3 rounded-lg border text-xs transition"
                style={{
                  borderColor: selected?.id === ds.id ? "var(--bi-primary)" : "var(--bi-border)",
                  background: selected?.id === ds.id ? "var(--bi-primary)" : "transparent",
                  color: selected?.id === ds.id ? "#fff" : "var(--bi-text)",
                }}
              >
                <div className="font-bold">{ds.name}</div>
                <div className="text-[11px] opacity-75">{ds.warehouse_table} · {ds.row_count} rows</div>
              </button>
            ))}
          </div>

          {/* Right Workspace */}
          {selected && (
            <div className="lg:col-span-3 space-y-5">
              {/* Data Preview (CLS Enforced) */}
              <div className="bi-card p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-bold">{selected.name} — Live Warehouse Preview</h3>
                  <span className="text-[11px] px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-500 font-semibold">
                    {selected.freshness_status} · v{selected.data_version}
                  </span>
                </div>
                {preview ? (
                  <div className="overflow-auto max-h-64 border rounded text-xs" style={{ borderColor: "var(--bi-border)" }}>
                    <table className="w-full">
                      <thead>
                        <tr className="border-b" style={{ borderColor: "var(--bi-border)" }}>
                          {preview.columns.map((c: string) => <th key={c} className="px-3 py-1.5 text-start font-bold whitespace-nowrap">{c}</th>)}
                        </tr>
                      </thead>
                      <tbody>
                        {preview.rows.map((r: any, i: number) => (
                          <tr key={i} className="border-b" style={{ borderColor: "var(--bi-border)" }}>
                            {preview.columns.map((c: string) => <td key={c} className="px-3 py-1 whitespace-nowrap">{String(r[c] ?? "")}</td>)}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="text-xs opacity-60">Preview requires Dataset View permission.</p>
                )}
              </div>

              {/* Column Profiler */}
              {profile && (
                <div className="bi-card p-4 space-y-3">
                  <h3 className="text-sm font-bold flex items-center gap-1.5">
                    <BarChart2 className="w-4 h-4 text-blue-500" /> Column Statistical Profiler
                  </h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2.5 text-xs">
                    {(profile.fields || []).map((col: any) => (
                      <div key={col.field_name} className="p-2.5 rounded-lg border" style={{ borderColor: "var(--bi-border)" }}>
                        <div className="font-bold flex justify-between">
                          <span>{col.field_name}</span>
                          <span className="text-[10px] opacity-60">{col.raw_type}</span>
                        </div>
                        <div className="text-[11px] mt-1 opacity-80">
                          Distinct: <b>{col.distinct_count}</b> · Nulls: <b>{col.null_count}</b>
                        </div>
                        {col.min !== undefined && col.min !== null && (
                          <div className="text-[11px] opacity-75">Min {col.min} / Max {col.max} {col.avg != null ? `/ Avg ${Number(col.avg).toFixed(2)}` : ""}</div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Calculated Column + Semantic Metrics + Lineage */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
                <form onSubmit={handleAddCalculatedField} className="bi-card p-4 space-y-2.5">
                  <h3 className="text-sm font-bold flex items-center gap-1.5"><Sparkles className="w-4 h-4 text-amber-500" /> Add Calculated Column</h3>
                  <input value={calcName} onChange={(e) => setCalcName(e.target.value)} placeholder="Field Name (e.g., gross_margin)" className="w-full px-3 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} required />
                  <input value={calcExpr} onChange={(e) => setCalcExpr(e.target.value)} placeholder="SQL / Formula Expression" className="w-full px-3 py-2 rounded border font-mono bg-transparent" style={{ borderColor: "var(--bi-border)" }} required />
                  <button type="submit" className="px-4 py-2 rounded text-white font-semibold" style={{ background: "var(--bi-primary)" }}>
                    Save Calculated Column
                  </button>
                </form>

                <form onSubmit={handleCreateMetric} className="bi-card p-4 space-y-2.5">
                  <h3 className="text-sm font-bold flex items-center gap-1.5"><Plus className="w-4 h-4 text-emerald-500" /> Semantic Layer Metric</h3>
                  <input value={mName} onChange={(e) => setMName(e.target.value)} placeholder="Metric Name (e.g., Net Margin %)" className="w-full px-3 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} required />
                  <div className="grid grid-cols-3 gap-2">
                    <input value={mExpr} onChange={(e) => setMExpr(e.target.value)} placeholder="SUM(profit)/SUM(revenue)" className="col-span-2 px-3 py-2 rounded border font-mono bg-transparent" style={{ borderColor: "var(--bi-border)" }} required />
                    <select value={mFmt} onChange={(e) => setMFmt(e.target.value)} className="px-2 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                      <option value="number">Number</option>
                      <option value="currency">Currency</option>
                      <option value="percentage">Percent</option>
                    </select>
                  </div>
                  <button type="submit" className="px-4 py-2 rounded text-white font-semibold bg-emerald-600">Publish Metric</button>
                </form>
              </div>

              {/* Lineage & Relationships */}
              <div className="bi-card p-4 grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
                <div>
                  <h3 className="text-sm font-bold flex items-center gap-1.5 mb-2"><GitBranch className="w-4 h-4 text-sky-500" /> Data Lineage Graph</h3>
                  <div className="space-y-1.5">
                    {(lineage?.upstream || []).map((l: any, i: number) => (
                      <div key={i} className="p-2 rounded border" style={{ borderColor: "var(--bi-border)" }}>
                        Upstream: <b>{l.source_type}</b> ({l.source_id.slice(0, 8)}) → Dataset [{l.transformation}]
                      </div>
                    ))}
                    {(lineage?.downstream || []).map((l: any, i: number) => (
                      <div key={i} className="p-2 rounded border" style={{ borderColor: "var(--bi-border)" }}>
                        Downstream: Dataset → <b>{l.target_type}</b> ({l.target_id.slice(0, 8)})
                      </div>
                    ))}
                  </div>
                </div>
                <div>
                  <h3 className="text-sm font-bold mb-2">Semantic Metrics ({metrics.length}) & Relationships ({relationships.length})</h3>
                  <div className="space-y-1.5 max-h-40 overflow-y-auto mb-3">
                    {metrics.map((m) => (
                      <div key={m.id} className="p-2 rounded border flex justify-between" style={{ borderColor: "var(--bi-border)" }}>
                        <span><b>{m.name}</b></span>
                        <span className="font-mono text-[11px] opacity-75">{m.formula}</span>
                      </div>
                    ))}
                    {relationships.map((rel) => (
                      <div key={rel.id} className="p-2 rounded border flex justify-between text-[11px]" style={{ borderColor: "var(--bi-border)" }}>
                        <span>{rel.left_field} <b>{rel.join_type.toUpperCase()} JOIN</b> {rel.right_field}</span>
                        <span className="opacity-70">{rel.cardinality}</span>
                      </div>
                    ))}
                  </div>
                  <form onSubmit={handleCreateRelationship} className="pt-2 border-t grid grid-cols-2 gap-1.5 text-[11px]" style={{ borderColor: "var(--bi-border)" }}>
                    <input value={relLeftField} onChange={(e) => setRelLeftField(e.target.value)} placeholder="Left Key (e.g., region)" className="px-2 py-1 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} required />
                    <select value={relRightDs} onChange={(e) => setRelRightDs(e.target.value)} className="px-2 py-1 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} required>
                      <option value="">Target Dataset...</option>
                      {datasets.filter((d) => d.id !== selected.id).map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                    </select>
                    <input value={relRightField} onChange={(e) => setRelRightField(e.target.value)} placeholder="Right Key (e.g., region)" className="px-2 py-1 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} required />
                    <div className="flex gap-1">
                      <select value={relJoinType} onChange={(e) => setRelJoinType(e.target.value)} className="px-1.5 py-1 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                        <option value="left">LEFT</option>
                        <option value="inner">INNER</option>
                        <option value="right">RIGHT</option>
                      </select>
                      <button type="submit" className="flex-1 px-2 py-1 rounded text-white font-semibold" style={{ background: "var(--bi-primary)" }}>
                        + Join
                      </button>
                    </div>
                  </form>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </AppShell>
  );
}
