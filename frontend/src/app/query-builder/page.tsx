"use client";

import React, { useEffect, useState } from "react";
import CodeMirror from "@uiw/react-codemirror";
import { sql } from "@codemirror/lang-sql";
import { Code2, LayoutGrid, Play, Save, Sparkles, Wand2 } from "lucide-react";
import AppShell from "@/components/layout/AppShell";
import WidgetRenderer from "@/components/charts/WidgetRenderer";
import { apiFetch } from "@/lib/api";
import { CHART_REGISTRY } from "@/lib/chart-registry";
import { useBi } from "@/lib/context";

export default function QueryBuilderPage() {
  const { user, activeThemeMode, showToast } = useBi();
  const [datasets, setDatasets] = useState<any[]>([]);
  const [dashboards, setDashboards] = useState<any[]>([]);
  const [savedQueries, setSavedQueries] = useState<any[]>([]);
  const [history, setHistory] = useState<any[]>([]);

  const [mode, setMode] = useState<"visual" | "sql">("sql");
  const [selectedDsId, setSelectedDsId] = useState("");
  const [sqlText, setSqlText] = useState("SELECT region, category, SUM(revenue) AS total_revenue, SUM(profit) AS total_profit\nFROM wh_enterprise_sales\nGROUP BY region, category\nORDER BY total_revenue DESC");
  const [paramsJson, setParamsJson] = useState("{}");
  const [result, setResult] = useState<any>(null);
  const [plan, setPlan] = useState<string[]>([]);
  const [running, setRunning] = useState(false);

  // Visual Builder fields
  const [vDims, setVDims] = useState<string[]>(["region"]);
  const [vMeas, setVMeas] = useState<string>("revenue");
  const [vAgg, setVAgg] = useState<string>("SUM");
  const [vFilterCol, setVFilterCol] = useState<string>("");
  const [vFilterOp, setVFilterOp] = useState<string>("eq");
  const [vFilterVal, setVFilterVal] = useState<string>("");
  const [vLimit, setVLimit] = useState<number>(100);

  // Save & Convert to Widget modal state
  const [queryName, setQueryName] = useState("");
  const [targetDashId, setTargetDashId] = useState("");
  const [widgetTitle, setWidgetTitle] = useState("Query Result Widget");
  const [widgetChart, setWidgetChart] = useState("bar");

  const load = () => {
    apiFetch("/datasets").then((ds) => {
      setDatasets(ds);
      if (ds[0] && !selectedDsId) setSelectedDsId(ds[0].id);
    }).catch(() => {});
    apiFetch("/dashboards").then((d) => {
      setDashboards(d);
      if (d[0] && !targetDashId) setTargetDashId(d[0].id);
    }).catch(() => {});
    apiFetch("/queries/saved").then(setSavedQueries).catch(() => {});
    apiFetch("/queries/history").then(setHistory).catch(() => {});
  };

  useEffect(() => {
    if (user) load();
  }, [user]);

  const currentDs = datasets.find((d) => d.id === selectedDsId);

  const handleRunSql = async () => {
    setRunning(true);
    setPlan([]);
    try {
      const parsedParams = JSON.parse(paramsJson || "{}");
      const res = await apiFetch<any>("/queries/execute-sql", {
        method: "POST",
        body: JSON.stringify({
          dataset_id: selectedDsId || null,
          sql_text: sqlText,
          parameters: parsedParams,
          limit: 500,
          include_explain: true,
        }),
      });
      setResult(res);
      if (res.explain_plan) {
        setPlan(Array.isArray(res.explain_plan) ? res.explain_plan : [String(res.explain_plan)]);
      }
      load();
    } catch (err: any) {
      showToast(err.message);
    } finally {
      setRunning(false);
    }
  };

  const handleVisualRun = async () => {
    if (!selectedDsId) return;
    setRunning(true);
    try {
      const filters = vFilterCol && vFilterVal
        ? [{ field_name: vFilterCol, operator: vFilterOp, value: vFilterVal, is_active: true }]
        : [];
      const res = await apiFetch<any>("/queries/execute-visual", {
        method: "POST",
        body: JSON.stringify({
          dataset_id: selectedDsId,
          visual_spec: {
            x_field: vDims[0] || undefined,
            y_field: vMeas,
            aggregation: vAgg,
            limit: vLimit,
          },
          filters,
        }),
      });
      if (res.generated_sql) setSqlText(res.generated_sql);
      setResult(res);
    } catch (err: any) {
      showToast(err.message);
    } finally {
      setRunning(false);
    }
  };

  const handleFormatSql = async () => {
    try {
      const res = await apiFetch<{ formatted_sql: string }>("/queries/format-sql", {
        method: "POST",
        body: JSON.stringify({ sql_text: sqlText }),
      });
      setSqlText(res.formatted_sql);
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleExplain = async () => {
    await handleRunSql();
  };

  const handleSaveQuery = async () => {
    if (!queryName) {
      showToast("Enter a query name first");
      return;
    }
    try {
      await apiFetch("/queries/saved", {
        method: "POST",
        body: JSON.stringify({
          name: queryName,
          dataset_id: selectedDsId || null,
          sql_text: sqlText,
        }),
      });
      setQueryName("");
      showToast("Query saved");
      load();
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleConvertToWidget = async () => {
    if (!targetDashId) return;
    try {
      const dashDetail = await apiFetch<any>(`/dashboards/${targetDashId}`);
      const firstPageId = dashDetail.pages?.[0]?.id;
      if (!firstPageId) throw new Error("Target dashboard has no pages");
      await apiFetch("/queries/convert-to-widget", {
        method: "POST",
        body: JSON.stringify({
          dashboard_id: targetDashId,
          page_id: firstPageId,
          title: widgetTitle,
          chart_type: widgetChart,
          dataset_id: selectedDsId || null,
          query_mode: "sql",
          sql_text: sqlText,
        }),
      });
      showToast("Query converted to live dashboard widget!");
      window.location.href = `/dashboards/${targetDashId}`;
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
              <Code2 className="w-5 h-5" style={{ color: "var(--bi-primary)" }} /> Visual Query Builder & SQL Editor
            </h1>
            <p className="text-xs mt-1" style={{ color: "var(--bi-muted)" }}>
              Server-side OLAP query execution with automatic RBAC, RLS, CLS enforcement & 1-click conversion to Dashboard Widgets.
            </p>
          </div>
          <div className="flex rounded-lg border overflow-hidden text-xs" style={{ borderColor: "var(--bi-border)" }}>
            <button onClick={() => setMode("visual")} className="px-3 py-1.5 font-semibold" style={{ background: mode === "visual" ? "var(--bi-primary)" : "transparent", color: mode === "visual" ? "#fff" : "var(--bi-text)" }}>
              Visual No-Code
            </button>
            <button onClick={() => setMode("sql")} className="px-3 py-1.5 font-semibold" style={{ background: mode === "sql" ? "var(--bi-primary)" : "transparent", color: mode === "sql" ? "#fff" : "var(--bi-text)" }}>
              SQL Editor
            </button>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-4 gap-5">
          {/* Schema Sidebar + Saved Queries */}
          <div className="bi-card p-4 space-y-4 text-xs">
            <div>
              <label className="block font-bold mb-1">Active Dataset Context (RLS/CLS)</label>
              <select value={selectedDsId} onChange={(e) => setSelectedDsId(e.target.value)} className="w-full px-2.5 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                {datasets.map((d) => <option key={d.id} value={d.id}>{d.name} ({d.warehouse_table})</option>)}
              </select>
            </div>

            {currentDs && (
              <div>
                <div className="font-bold mb-1">Columns ({currentDs.fields?.length || 0})</div>
                <div className="space-y-1 max-h-40 overflow-y-auto">
                  {currentDs.fields.map((f: any) => (
                    <div key={f.field_name} className="px-2 py-1 rounded border flex justify-between text-[11px]" style={{ borderColor: "var(--bi-border)" }}>
                      <span className="font-mono">{f.field_name}</span>
                      <span className="opacity-60">{f.data_type}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div>
              <div className="font-bold mb-1">Saved Queries ({savedQueries.length})</div>
              <div className="space-y-1 max-h-36 overflow-y-auto">
                {savedQueries.map((sq) => (
                  <button
                    key={sq.id}
                    onClick={() => { setSqlText(sq.sql_text); setMode("sql"); }}
                    className="w-full text-start px-2.5 py-1.5 rounded border hover:bg-blue-500/10 truncate"
                    style={{ borderColor: "var(--bi-border)" }}
                  >
                    {sq.name}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Editor & Results */}
          <div className="lg:col-span-3 space-y-4">
            {mode === "visual" ? (
              <div className="bi-card p-4 space-y-3 text-xs">
                <h2 className="font-bold text-sm">Visual Query Designer</h2>
                <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
                  <div>
                    <label className="block font-semibold mb-1">Group By Dimension</label>
                    <select value={vDims[0] || ""} onChange={(e) => setVDims(e.target.value ? [e.target.value] : [])} className="w-full px-2.5 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                      <option value="">None</option>
                      {(currentDs?.fields || []).map((f: any) => <option key={f.field_name} value={f.field_name}>{f.field_name}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="block font-semibold mb-1">Measure Column</label>
                    <select value={vMeas} onChange={(e) => setVMeas(e.target.value)} className="w-full px-2.5 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                      <option value="*">*</option>
                      {(currentDs?.fields || []).map((f: any) => <option key={f.field_name} value={f.field_name}>{f.field_name}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="block font-semibold mb-1">Aggregation</label>
                    <select value={vAgg} onChange={(e) => setVAgg(e.target.value)} className="w-full px-2.5 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                      {["SUM", "AVG", "COUNT", "COUNT_DISTINCT", "MIN", "MAX", "MEDIAN"].map((a) => <option key={a} value={a}>{a}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="block font-semibold mb-1">Limit</label>
                    <input type="number" value={vLimit} onChange={(e) => setVLimit(Number(e.target.value))} className="w-full px-2.5 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
                  </div>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  <div>
                    <label className="block font-semibold mb-1">WHERE Column (Optional)</label>
                    <select value={vFilterCol} onChange={(e) => setVFilterCol(e.target.value)} className="w-full px-2.5 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                      <option value="">No WHERE filter</option>
                      {(currentDs?.fields || []).map((f: any) => <option key={f.field_name} value={f.field_name}>{f.field_name}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="block font-semibold mb-1">Operator</label>
                    <select value={vFilterOp} onChange={(e) => setVFilterOp(e.target.value)} className="w-full px-2.5 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                      <option value="eq">Equals (=)</option>
                      <option value="neq">Not Equals (!=)</option>
                      <option value="gt">Greater Than (&gt;)</option>
                      <option value="lt">Less Than (&lt;)</option>
                      <option value="contains">Contains</option>
                    </select>
                  </div>
                  <div>
                    <label className="block font-semibold mb-1">Value</label>
                    <input value={vFilterVal} onChange={(e) => setVFilterVal(e.target.value)} placeholder="Filter value..." className="w-full px-2.5 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
                  </div>
                </div>
                <button onClick={handleVisualRun} className="px-4 py-2 rounded-lg text-white font-semibold flex items-center gap-1.5" style={{ background: "var(--bi-primary)" }}>
                  <Play className="w-3.5 h-3.5" /> Execute Visual Query
                </button>
              </div>
            ) : (
              <div className="bi-card p-4 space-y-3 text-xs">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <button onClick={handleRunSql} disabled={running} className="px-4 py-1.5 rounded-lg text-white font-semibold flex items-center gap-1.5" style={{ background: "var(--bi-primary)" }}>
                      <Play className="w-3.5 h-3.5" /> {running ? "Executing..." : "Run SQL"}
                    </button>
                    <button onClick={handleFormatSql} className="px-3 py-1.5 rounded-lg border flex items-center gap-1" style={{ borderColor: "var(--bi-border)" }}>
                      <Wand2 className="w-3.5 h-3.5" /> Format
                    </button>
                    <button onClick={handleExplain} className="px-3 py-1.5 rounded-lg border" style={{ borderColor: "var(--bi-border)" }}>
                      Explain Plan
                    </button>
                  </div>
                  <div className="flex items-center gap-2">
                    <input value={queryName} onChange={(e) => setQueryName(e.target.value)} placeholder="Query Name..." className="px-2.5 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
                    <button onClick={handleSaveQuery} className="px-3 py-1.5 rounded-lg border font-semibold flex items-center gap-1" style={{ borderColor: "var(--bi-border)" }}>
                      <Save className="w-3.5 h-3.5" /> Save
                    </button>
                  </div>
                </div>

                <div className="border rounded-lg overflow-hidden" style={{ borderColor: "var(--bi-border)" }}>
                  <CodeMirror
                    value={sqlText}
                    height="180px"
                    extensions={[sql()]}
                    theme={activeThemeMode === "dark" ? "dark" : "light"}
                    onChange={(val) => setSqlText(val)}
                  />
                </div>

                <div className="flex items-center gap-2">
                  <span className="font-semibold">Parameters JSON (:param):</span>
                  <input value={paramsJson} onChange={(e) => setParamsJson(e.target.value)} className="flex-1 px-2.5 py-1 rounded border font-mono bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
                </div>
              </div>
            )}

            {plan.length > 0 && (
              <div className="bi-card p-3 font-mono text-[11px] overflow-x-auto">
                <div className="font-bold mb-1">DuckDB Execution Plan:</div>
                {plan.map((line, i) => <div key={i}>{line}</div>)}
              </div>
            )}

            {/* Results & Convert to Widget */}
            {result && (
              <div className="bi-card p-4 space-y-4 text-xs">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <b>{result.row_count} rows</b> returned in <b>{result.execution_ms} ms</b>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <input value={widgetTitle} onChange={(e) => setWidgetTitle(e.target.value)} className="px-2.5 py-1 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
                    <select value={widgetChart} onChange={(e) => setWidgetChart(e.target.value)} className="px-2.5 py-1 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                      {CHART_REGISTRY.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
                    </select>
                    <select value={targetDashId} onChange={(e) => setTargetDashId(e.target.value)} className="px-2.5 py-1 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                      {dashboards.map((d) => <option key={d.id} value={d.id}>{d.title}</option>)}
                    </select>
                    <button onClick={handleConvertToWidget} className="px-3 py-1.5 rounded bg-emerald-600 text-white font-semibold flex items-center gap-1">
                      <LayoutGrid className="w-3.5 h-3.5" /> Convert to Widget
                    </button>
                  </div>
                </div>

                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                  <div className="overflow-auto max-h-64 border rounded" style={{ borderColor: "var(--bi-border)" }}>
                    <table className="w-full text-[11px]">
                      <thead>
                        <tr className="border-b" style={{ borderColor: "var(--bi-border)" }}>
                          {result.columns.map((c: string) => <th key={c} className="px-2.5 py-1.5 text-start font-bold">{c}</th>)}
                        </tr>
                      </thead>
                      <tbody>
                        {result.rows.slice(0, 50).map((r: any, i: number) => (
                          <tr key={i} className="border-b" style={{ borderColor: "var(--bi-border)" }}>
                            {result.columns.map((c: string) => <td key={c} className="px-2.5 py-1">{String(r[c] ?? "")}</td>)}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="h-64 border rounded p-2" style={{ borderColor: "var(--bi-border)" }}>
                    <WidgetRenderer chartType={widgetChart} columns={result.columns} rows={result.rows} />
                  </div>
                </div>
              </div>
            )}

            {/* Recent Execution History */}
            {history.length > 0 && (
              <div className="bi-card p-4 space-y-2 text-xs">
                <div className="font-bold">Execution History</div>
                <div className="space-y-1 max-h-36 overflow-y-auto">
                  {history.slice(0, 10).map((h) => (
                    <div key={h.id} className="flex justify-between p-2 rounded border text-[11px]" style={{ borderColor: "var(--bi-border)" }}>
                      <span className="font-mono truncate max-w-md">{h.sql_text}</span>
                      <span>{h.status} · {h.execution_ms} ms ({h.rows_returned} rows)</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </AppShell>
  );
}
