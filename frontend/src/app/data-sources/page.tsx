"use client";

import React, { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { CheckCircle2, Database, FileSpreadsheet, Play, Plus, Sparkles, Trash2, UploadCloud, XCircle } from "lucide-react";
import AppShell from "@/components/layout/AppShell";
import { IntelligentDashboardStudio } from "@/components/ai/IntelligentDashboardStudio";
import { apiFetch } from "@/lib/api";
import { useBi } from "@/lib/context";

export default function DataSourcesPage() {
  const router = useRouter();
  const { user, locale, showToast } = useBi();
  const [sources, setSources] = useState<any[]>([]);
  const [datasets, setDatasets] = useState<any[]>([]);
  const [supported, setSupported] = useState<{ databases: string[]; files: string[] }>({ databases: [], files: [] });
  const [lastImported, setLastImported] = useState<any>(null);
  const [buildingDash, setBuildingDash] = useState(false);
  const [studioDatasetId, setStudioDatasetId] = useState<string>("");

  // New DB Connector form
  const [name, setName] = useState("");
  const [sourceType, setSourceType] = useState("postgresql");
  const [host, setHost] = useState("localhost");
  const [port, setPort] = useState("5432");
  const [database, setDatabase] = useState("");
  const [filePath, setFilePath] = useState("");
  const [url, setUrl] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [readOnly, setReadOnly] = useState(true);

  // Schema browser + live query
  const [selectedSchema, setSelectedSchema] = useState<any>(null);
  const [directQuerySql, setDirectQuerySql] = useState("SELECT * FROM erp_orders LIMIT 10");
  const [directQueryRes, setDirectQueryRes] = useState<any>(null);

  // File Drag & Drop Importer
  const [fileObj, setFileObj] = useState<File | null>(null);
  const [datasetName, setDatasetName] = useState("");
  const [sheetName, setSheetName] = useState("");
  const [hasHeader, setHasHeader] = useState(true);
  const [nullStrategy, setNullStrategy] = useState("keep");
  const [deduplicate, setDeduplicate] = useState(false);
  const [previewData, setPreviewData] = useState<any>(null);
  const [previewColPage, setPreviewColPage] = useState(0);
  const [previewing, setPreviewing] = useState(false);
  const [importing, setImporting] = useState(false);
  const [colRenames, setColRenames] = useState<Record<string, string>>({});
  const [typeOverrides, setTypeOverrides] = useState<Record<string, string>>({});
  const [dragging, setDragging] = useState(false);

  const load = () => {
    apiFetch("/data-sources").then(setSources).catch(() => {});
    apiFetch("/datasets").then(setDatasets).catch(() => {});
    apiFetch("/data-sources/supported-types").then(setSupported).catch(() => {});
  };

  useEffect(() => {
    if (user) load();
  }, [user]);

  const handleCreateSource = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const cfg: any = {};
      if (sourceType === "sqlite") cfg.file_path = filePath;
      else if (sourceType === "rest_api" || sourceType === "graphql") cfg.url = url;
      else {
        cfg.host = host;
        cfg.port = Number(port);
        cfg.database = database;
      }
      await apiFetch("/data-sources", {
        method: "POST",
        body: JSON.stringify({
          name,
          source_type: sourceType,
          read_only: readOnly,
          config_json: cfg,
          credentials: { username, password },
        }),
      });
      setName("");
      showToast("Data source saved (credentials encrypted)");
      load();
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleTest = async (id: string) => {
    try {
      const res = await apiFetch<any>(`/data-sources/${id}/test`, { method: "POST" });
      showToast(res.ok ? `Connected: ${res.message}` : `Failed: ${res.error}`);
      load();
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleInspectSchema = async (id: string) => {
    try {
      const res = await apiFetch(`/data-sources/${id}/schema`);
      setSelectedSchema(res);
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleRunDirectQuery = async () => {
    if (!selectedSchema) return;
    try {
      const res = await apiFetch(`/data-sources/${selectedSchema.source_id}/query`, {
        method: "POST",
        body: JSON.stringify({ query: directQuerySql, limit: 50 }),
      });
      setDirectQueryRes(res);
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handlePreviewFile = async (file: File, chosenSheet?: string) => {
    setFileObj(file);
    setPreviewColPage(0);
    if (!chosenSheet) setDatasetName(file.name.replace(/\.[^.]+$/, ""));
    const fd = new FormData();
    fd.append("file", file);
    if (chosenSheet) fd.append("sheet_name", chosenSheet);
    fd.append("has_header", String(hasHeader));
    fd.append("null_strategy", nullStrategy);
    fd.append("deduplicate", String(deduplicate));
    fd.append("column_renames_json", JSON.stringify(colRenames));
    fd.append("type_overrides_json", JSON.stringify(typeOverrides));

    setPreviewing(true);
    try {
      const res = await apiFetch("/data-sources/files/preview", { method: "POST", body: fd });
      setPreviewData(res);
      if (res.sheets?.length && !chosenSheet) setSheetName(res.sheets[0]);
    } catch (err: any) {
      showToast(err.message);
    } finally {
      setPreviewing(false);
    }
  };

  const handleImportDataset = async () => {
    if (!fileObj || !datasetName || importing) return;
    const fd = new FormData();
    fd.append("file", fileObj);
    fd.append("dataset_name", datasetName);
    if (sheetName) fd.append("sheet_name", sheetName);
    fd.append("has_header", String(hasHeader));
    fd.append("null_strategy", nullStrategy);
    fd.append("deduplicate", String(deduplicate));
    fd.append("column_renames_json", JSON.stringify(colRenames));
    fd.append("type_overrides_json", JSON.stringify(typeOverrides));
    setImporting(true);
    try {
      const res = await apiFetch("/data-sources/files/import", { method: "POST", body: fd });
      showToast(`Imported ${res.row_count} rows into '${res.name || res.warehouse_table}'!`);
      setLastImported(res);
      setPreviewData(null);
      setPreviewColPage(0);
      setFileObj(null);
      setColRenames({});
      setTypeOverrides({});
      load();
    } catch (err: any) {
      showToast(err.message);
    } finally {
      setImporting(false);
    }
  };

  const handleBuildDashboardForDataset = async (dsId: string) => {
    setBuildingDash(true);
    try {
      const res = await apiFetch<any>("/ai/generate-dashboard", {
        method: "POST",
        body: JSON.stringify({ dataset_id: dsId }),
      });
      showToast(locale === "fa" ? `داشبورد «${res.title}» ساخته شد!` : `Created dashboard "${res.title}"`);
      router.push(`/dashboards/${res.dashboard_id}`);
    } catch (err: any) {
      showToast(err.message);
    } finally {
      setBuildingDash(false);
    }
  };

  return (
    <AppShell>
      <div className="space-y-6">
        <div>
          <h1 className="text-xl font-bold flex items-center gap-2">
            <Database className="w-5 h-5" style={{ color: "var(--bi-primary)" }} /> Data Connectivity & File Ingestion
          </h1>
          <p className="text-xs mt-1" style={{ color: "var(--bi-muted)" }}>
            Connect to PostgreSQL, SQL Server, MySQL, MariaDB, Oracle, SQLite, MongoDB, REST/GraphQL APIs or drag & drop Excel, CSV, TSV, JSON/JSONL, Log (.log/.txt), or XML files (no row/column limits).
          </p>
        </div>

        {/* Section 1: Drag & Drop File Ingestion */}
        <div className="bi-card p-5 space-y-4">
          <h2 className="text-sm font-bold flex items-center gap-2">
            <FileSpreadsheet className="w-4 h-4 text-emerald-500" /> Excel / Log / CSV / JSON File Importer
          </h2>

          <div
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              const f = e.dataTransfer.files?.[0];
              if (f) handlePreviewFile(f);
            }}
            className={`border-2 border-dashed rounded-xl p-6 text-center transition cursor-pointer ${
              dragging ? "bg-blue-500/10 border-blue-500" : ""
            }`}
            style={{ borderColor: dragging ? "var(--bi-primary)" : "var(--bi-border)" }}
          >
            <UploadCloud className="w-8 h-8 mx-auto mb-2 opacity-60" />
            <p className="text-xs font-semibold">
              {locale === "fa"
                ? "فایل اکسل (.xlsx / .xls)، فایل لاگ (.log / .txt / .jsonl) یا CSV/JSON/XML را اینجا بکشید و رها کنید"
                : "Drag & drop .xlsx, .xls, .csv, .tsv, .json, .jsonl, .ndjson, .log, .txt, .xml here or select a file"}
            </p>
            <input
              type="file"
              accept=".xlsx,.xls,.csv,.tsv,.json,.jsonl,.ndjson,.log,.txt,.xml"
              onChange={(e) => e.target.files?.[0] && handlePreviewFile(e.target.files[0])}
              className="mt-3 text-xs"
            />
            {previewing && (
              <p className="text-xs mt-2 text-blue-500 font-semibold">
                {locale === "fa" ? "در حال خواندن و پیش‌نمایش فایل..." : "Reading & generating preview..."}
              </p>
            )}
          </div>

          {lastImported && (
            <div className="rounded-xl border border-emerald-500/40 bg-emerald-500/5 p-3.5 flex flex-wrap items-center justify-between gap-2 text-xs">
              <div className="flex items-center gap-2 text-emerald-600 dark:text-emerald-400 font-semibold">
                <CheckCircle2 className="w-4 h-4 shrink-0" />
                <span>
                  {locale === "fa"
                    ? `فایل «${lastImported.name}» با ${lastImported.row_count} ردیف با موفقیت وارد شد!`
                    : `Dataset "${lastImported.name}" (${lastImported.row_count} rows) imported and ready!`}
                </span>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => setStudioDatasetId(lastImported.dataset_id || lastImported.id)}
                  className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-white font-semibold bg-indigo-600 hover:bg-indigo-700"
                >
                  <Sparkles className="w-3.5 h-3.5" />
                  {locale === "fa" ? "شخصی‌سازی KPIها و صفحات" : "Customize KPIs"}
                </button>
                <button
                  type="button"
                  disabled={buildingDash}
                  onClick={() => handleBuildDashboardForDataset(lastImported.dataset_id || lastImported.id)}
                  className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-white font-semibold bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60"
                >
                  <Sparkles className="w-3.5 h-3.5" />
                  {buildingDash
                    ? locale === "fa" ? "در حال ساخت داشبورد..." : "Building..."
                    : locale === "fa" ? "ساخت فوری داشبورد از این فایل" : "Auto-Build Dashboard"}
                </button>
              </div>
            </div>
          )}

          {studioDatasetId && (
            <IntelligentDashboardStudio
              initialDatasetId={studioDatasetId}
              onClose={() => setStudioDatasetId("")}
            />
          )}

          {datasets.length > 0 && (
            <div className="pt-3 border-t space-y-2" style={{ borderColor: "var(--bi-border)" }}>
              <div className="text-xs font-bold flex items-center justify-between">
                <span>
                  {locale === "fa" ? `مجموعه‌داده‌های آماده (${datasets.length})` : `Available Datasets (${datasets.length})`}
                </span>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2 text-xs">
                {datasets.map((ds) => (
                  <div
                    key={ds.id}
                    className="p-2.5 rounded-lg border flex items-center justify-between gap-2"
                    style={{ borderColor: "var(--bi-border)", backgroundColor: "var(--bi-surface)" }}
                  >
                    <div className="min-w-0">
                      <div className="font-semibold truncate flex items-center gap-1.5">
                        {ds.name}
                        {ds.is_uploaded_excel && (
                          <span className="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-500 text-[10px]">Excel</span>
                        )}
                      </div>
                      <div className="text-[11px]" style={{ color: "var(--bi-muted)" }}>
                        {(ds.row_count || 0).toLocaleString()} {locale === "fa" ? "ردیف" : "rows"}
                      </div>
                    </div>
                    <div className="flex items-center gap-1 shrink-0">
                      <button
                        type="button"
                        disabled={buildingDash}
                        onClick={() => handleBuildDashboardForDataset(ds.id)}
                        className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-violet-600 text-white text-[11px] font-semibold hover:bg-violet-700 disabled:opacity-60"
                      >
                        <Sparkles className="w-3 h-3" />
                        {locale === "fa" ? "ساخت داشبورد" : "Build Dashboard"}
                      </button>
                      {ds.is_uploaded_excel && (
                        <button
                          type="button"
                          onClick={() => apiFetch(`/datasets/${ds.id}`, { method: "DELETE" }).then(load)}
                          className="p-1 text-red-500 hover:bg-red-500/10 rounded"
                          title="Delete uploaded dataset"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {previewData && (() => {
            const PREVIEW_COL_PAGE_SIZE = 40;
            const totalCols = previewData.columns?.length || 0;
            const totalColPages = Math.max(1, Math.ceil(totalCols / PREVIEW_COL_PAGE_SIZE));
            const safeColPage = Math.min(previewColPage, totalColPages - 1);
            const startCol = safeColPage * PREVIEW_COL_PAGE_SIZE;
            const visibleCols = (previewData.columns || []).slice(startCol, startCol + PREVIEW_COL_PAGE_SIZE);
            return (
              <div className="space-y-3 pt-2 border-t text-xs" style={{ borderColor: "var(--bi-border)" }}>
                <div className="grid grid-cols-2 md:grid-cols-6 gap-2">
                  <input
                    value={datasetName}
                    onChange={(e) => setDatasetName(e.target.value)}
                    placeholder="Dataset Name"
                    className="px-2.5 py-1.5 rounded border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  />
                  {previewData.sheets?.length > 0 && (
                    <select
                      value={sheetName}
                      onChange={(e) => { setSheetName(e.target.value); fileObj && handlePreviewFile(fileObj, e.target.value); }}
                      className="px-2.5 py-1.5 rounded border bg-transparent"
                      style={{ borderColor: "var(--bi-border)" }}
                    >
                      {previewData.sheets.map((s: string) => <option key={s} value={s}>Sheet: {s}</option>)}
                    </select>
                  )}
                  <select value={nullStrategy} onChange={(e) => setNullStrategy(e.target.value)} className="px-2.5 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                    <option value="keep">Nulls: Keep</option>
                    <option value="drop_rows">Nulls: Drop Rows</option>
                    <option value="fill_zero">Nulls: Fill Zero/Unknown</option>
                    <option value="fill_mean">Nulls: Fill Mean</option>
                  </select>
                  <label className="flex items-center gap-1.5 px-2">
                    <input type="checkbox" checked={hasHeader} onChange={(e) => setHasHeader(e.target.checked)} />
                    <span>Auto Header</span>
                  </label>
                  <label className="flex items-center gap-1.5 px-2">
                    <input type="checkbox" checked={deduplicate} onChange={(e) => setDeduplicate(e.target.checked)} />
                    <span>Dedup ({previewData.duplicate_count})</span>
                  </label>
                  <button
                    type="button"
                    disabled={importing}
                    onClick={handleImportDataset}
                    className="px-3 py-1.5 rounded text-white font-bold bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60"
                  >
                    {importing
                      ? locale === "fa" ? "در حال ورود داده‌ها..." : "Importing..."
                      : `Import (${previewData.row_count_estimated ? "~" : ""}${(previewData.total_rows || 0).toLocaleString()} rows · ${totalCols.toLocaleString()} cols)`}
                  </button>
                </div>

                {totalCols > PREVIEW_COL_PAGE_SIZE && (
                  <div className="flex items-center justify-between px-2 py-1 rounded bg-slate-500/10 text-[11px]">
                    <span>
                      Showing columns {startCol + 1}–{Math.min(startCol + PREVIEW_COL_PAGE_SIZE, totalCols)} of {totalCols.toLocaleString()}
                    </span>
                    <div className="flex items-center gap-1.5">
                      <button
                        type="button"
                        disabled={safeColPage <= 0}
                        onClick={() => setPreviewColPage((p) => Math.max(0, p - 1))}
                        className="px-2 py-0.5 rounded border disabled:opacity-40"
                        style={{ borderColor: "var(--bi-border)" }}
                      >
                        Prev Cols
                      </button>
                      <button
                        type="button"
                        disabled={safeColPage >= totalColPages - 1}
                        onClick={() => setPreviewColPage((p) => Math.min(totalColPages - 1, p + 1))}
                        className="px-2 py-0.5 rounded border disabled:opacity-40"
                        style={{ borderColor: "var(--bi-border)" }}
                      >
                        Next Cols
                      </button>
                    </div>
                  </div>
                )}

                <div className="overflow-auto max-h-56 rounded border" style={{ borderColor: "var(--bi-border)" }}>
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b" style={{ borderColor: "var(--bi-border)" }}>
                        {visibleCols.map((c: any) => (
                          <th key={c.field_name} className="px-2.5 py-2 text-start font-semibold whitespace-nowrap align-top">
                            <div className="space-y-1">
                              <input
                                value={colRenames[c.field_name] ?? c.field_name}
                                onChange={(e) => setColRenames({ ...colRenames, [c.field_name]: e.target.value })}
                                className="w-28 px-1.5 py-0.5 rounded border bg-transparent text-[11px] font-semibold"
                                style={{ borderColor: "var(--bi-border)" }}
                                title="Rename column"
                              />
                              <select
                                value={typeOverrides[c.field_name] || ""}
                                onChange={(e) => setTypeOverrides({ ...typeOverrides, [c.field_name]: e.target.value })}
                                className="block w-28 px-1 py-0.5 rounded border bg-transparent text-[10px] opacity-80"
                                style={{ borderColor: "var(--bi-border)" }}
                                title="Cast data type"
                              >
                                <option value="">Auto ({c.data_type})</option>
                                <option value="string">string</option>
                                <option value="integer">integer</option>
                                <option value="float">float</option>
                                <option value="date">date</option>
                                <option value="boolean">boolean</option>
                              </select>
                            </div>
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {previewData.preview_rows.slice(0, 15).map((r: any, i: number) => (
                        <tr key={i} className="border-b" style={{ borderColor: "var(--bi-border)" }}>
                          {visibleCols.map((c: any) => (
                            <td key={c.field_name} className="px-2.5 py-1 whitespace-nowrap">{String(r[c.field_name] ?? "")}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            );
          })()}
        </div>

        {/* Section 2: Database & API Connections */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <form onSubmit={handleCreateSource} className="bi-card p-5 space-y-3 text-xs">
            <h2 className="text-sm font-bold flex items-center gap-1.5"><Plus className="w-4 h-4" /> New Connection</h2>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Connection Name" className="w-full px-3 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} required />
            <select value={sourceType} onChange={(e) => setSourceType(e.target.value)} className="w-full px-3 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
              {supported.databases.map((dbType) => <option key={dbType} value={dbType}>{dbType.toUpperCase()}</option>)}
            </select>

            {sourceType === "sqlite" ? (
              <input value={filePath} onChange={(e) => setFilePath(e.target.value)} placeholder="/path/to/database.sqlite" className="w-full px-3 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
            ) : sourceType === "rest_api" || sourceType === "graphql" ? (
              <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://api.example.com/data" className="w-full px-3 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
            ) : (
              <>
                <div className="grid grid-cols-3 gap-2">
                  <input value={host} onChange={(e) => setHost(e.target.value)} placeholder="Host" className="col-span-2 px-3 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
                  <input value={port} onChange={(e) => setPort(e.target.value)} placeholder="Port" className="px-3 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
                </div>
                <input value={database} onChange={(e) => setDatabase(e.target.value)} placeholder="Database / Schema" className="w-full px-3 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
                <div className="grid grid-cols-2 gap-2">
                  <input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="Username" className="px-3 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
                  <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Password" className="px-3 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
                </div>
              </>
            )}

            <label className="flex items-center gap-2">
              <input type="checkbox" checked={readOnly} onChange={(e) => setReadOnly(e.target.checked)} />
              <span>Enforce Read-Only Connection</span>
            </label>
            <button type="submit" className="w-full py-2 rounded-lg text-white font-semibold" style={{ background: "var(--bi-primary)" }}>
              Save Encrypted Connection
            </button>
          </form>

          <div className="lg:col-span-2 bi-card p-5 space-y-3">
            <h2 className="text-sm font-bold">Configured Data Sources ({sources.length})</h2>
            <div className="space-y-2 text-xs">
              {sources.map((s) => (
                <div key={s.id} className="p-3 rounded-lg border flex flex-wrap items-center justify-between gap-2" style={{ borderColor: "var(--bi-border)" }}>
                  <div>
                    <div className="font-bold flex items-center gap-2">
                      {s.name}
                      <span className="px-2 py-0.5 rounded bg-slate-500/15 text-[10px] uppercase">{s.source_type}</span>
                      {s.read_only && <span className="px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-500 text-[10px]">READ-ONLY</span>}
                    </div>
                    <div className="text-[11px] flex items-center gap-1.5 mt-0.5" style={{ color: "var(--bi-muted)" }}>
                      {s.health_status === "healthy" ? <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" /> : <XCircle className="w-3.5 h-3.5 text-amber-500" />}
                      Status: {s.health_status} {s.last_error ? `· ${s.last_error}` : ""}
                    </div>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <button onClick={() => handleTest(s.id)} className="px-2.5 py-1 rounded border hover:opacity-80" style={{ borderColor: "var(--bi-border)" }}>Test</button>
                    <button onClick={() => handleInspectSchema(s.id)} className="px-2.5 py-1 rounded border hover:opacity-80" style={{ borderColor: "var(--bi-border)" }}>Schema & Query</button>
                    <button onClick={() => apiFetch(`/data-sources/${s.id}`, { method: "DELETE" }).then(load)} className="p-1 text-red-500"><Trash2 className="w-3.5 h-3.5" /></button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Schema Inspector & Live Query */}
        {selectedSchema && (
          <div className="bi-card p-5 space-y-4 text-xs">
            <h3 className="text-sm font-bold">Schema Explorer & Direct Query</h3>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div className="space-y-2 max-h-64 overflow-y-auto border rounded p-2.5" style={{ borderColor: "var(--bi-border)" }}>
                {selectedSchema.tables.map((tb: any) => (
                  <div key={tb.table_name} className="space-y-1">
                    <div className="font-bold text-blue-500">{tb.table_name} ({tb.object_type})</div>
                    <div className="ps-3 text-[11px] opacity-80 space-y-0.5">
                      {tb.columns.map((c: any) => (
                        <div key={c.name}>{c.name} : {c.type} {c.primary_key ? "🔑" : ""}</div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
              <div className="md:col-span-2 space-y-2">
                <div className="flex gap-2">
                  <input value={directQuerySql} onChange={(e) => setDirectQuerySql(e.target.value)} className="flex-1 px-3 py-2 rounded border font-mono bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
                  <button onClick={handleRunDirectQuery} className="px-4 py-2 rounded text-white font-semibold flex items-center gap-1" style={{ background: "var(--bi-primary)" }}>
                    <Play className="w-3.5 h-3.5" /> Run
                  </button>
                </div>
                {directQueryRes && (
                  <div className="overflow-auto max-h-52 border rounded" style={{ borderColor: "var(--bi-border)" }}>
                    <table className="w-full text-[11px]">
                      <thead>
                        <tr className="border-b" style={{ borderColor: "var(--bi-border)" }}>
                          {directQueryRes.columns.map((c: string) => <th key={c} className="px-2 py-1 text-start">{c}</th>)}
                        </tr>
                      </thead>
                      <tbody>
                        {directQueryRes.rows.slice(0, 20).map((r: any, idx: number) => (
                          <tr key={idx} className="border-b" style={{ borderColor: "var(--bi-border)" }}>
                            {directQueryRes.columns.map((c: string) => <td key={c} className="px-2 py-1">{String(r[c] ?? "")}</td>)}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
