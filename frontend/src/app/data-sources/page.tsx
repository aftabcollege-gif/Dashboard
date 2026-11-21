"use client";

import React, { useEffect, useState } from "react";
import { CheckCircle2, Database, FileSpreadsheet, Play, Plus, Trash2, UploadCloud, XCircle } from "lucide-react";
import AppShell from "@/components/layout/AppShell";
import { apiFetch } from "@/lib/api";
import { useBi } from "@/lib/context";

export default function DataSourcesPage() {
  const { user, showToast } = useBi();
  const [sources, setSources] = useState<any[]>([]);
  const [supported, setSupported] = useState<{ databases: string[]; files: string[] }>({ databases: [], files: [] });

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
  const [colRenames, setColRenames] = useState<Record<string, string>>({});
  const [typeOverrides, setTypeOverrides] = useState<Record<string, string>>({});
  const [dragging, setDragging] = useState(false);

  const load = () => {
    apiFetch("/data-sources").then(setSources).catch(() => {});
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
    if (!datasetName) setDatasetName(file.name.replace(/\.[^.]+$/, ""));
    const fd = new FormData();
    fd.append("file", file);
    if (chosenSheet) fd.append("sheet_name", chosenSheet);
    fd.append("has_header", String(hasHeader));
    fd.append("null_strategy", nullStrategy);
    fd.append("deduplicate", String(deduplicate));
    fd.append("column_renames_json", JSON.stringify(colRenames));
    fd.append("type_overrides_json", JSON.stringify(typeOverrides));

    try {
      const res = await apiFetch("/data-sources/files/preview", { method: "POST", body: fd });
      setPreviewData(res);
      if (res.sheets?.length && !chosenSheet) setSheetName(res.sheets[0]);
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleImportDataset = async () => {
    if (!fileObj || !datasetName) return;
    const fd = new FormData();
    fd.append("file", fileObj);
    fd.append("dataset_name", datasetName);
    if (sheetName) fd.append("sheet_name", sheetName);
    fd.append("has_header", String(hasHeader));
    fd.append("null_strategy", nullStrategy);
    fd.append("deduplicate", String(deduplicate));
    fd.append("column_renames_json", JSON.stringify(colRenames));
    fd.append("type_overrides_json", JSON.stringify(typeOverrides));
    try {
      const res = await apiFetch("/data-sources/files/import", { method: "POST", body: fd });
      showToast(`Imported ${res.row_count} rows into Warehouse table '${res.warehouse_table}'!`);
      setPreviewData(null);
      setFileObj(null);
      setColRenames({});
      setTypeOverrides({});
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
            <Database className="w-5 h-5" style={{ color: "var(--bi-primary)" }} /> Data Connectivity & File Ingestion
          </h1>
          <p className="text-xs mt-1" style={{ color: "var(--bi-muted)" }}>
            Connect to PostgreSQL, SQL Server, MySQL, MariaDB, Oracle, SQLite, MongoDB, REST/GraphQL APIs or drag & drop Excel/CSV/JSON/XML.
          </p>
        </div>

        {/* Section 1: Drag & Drop File Ingestion */}
        <div className="bi-card p-5 space-y-4">
          <h2 className="text-sm font-bold flex items-center gap-2">
            <FileSpreadsheet className="w-4 h-4 text-emerald-500" /> Excel / CSV / JSON / XML File Importer
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
              Drag & drop `.xlsx`, `.xls`, `.csv`, `.json`, `.xml` here or select a file
            </p>
            <input
              type="file"
              accept=".xlsx,.xls,.csv,.json,.xml"
              onChange={(e) => e.target.files?.[0] && handlePreviewFile(e.target.files[0])}
              className="mt-3 text-xs"
            />
          </div>

          {previewData && (
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
                <button onClick={handleImportDataset} className="px-3 py-1.5 rounded text-white font-bold bg-emerald-600">
                  Import ({previewData.total_rows} rows)
                </button>
              </div>

              <div className="overflow-auto max-h-56 rounded border" style={{ borderColor: "var(--bi-border)" }}>
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b" style={{ borderColor: "var(--bi-border)" }}>
                      {previewData.columns.map((c: any) => (
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
                        {previewData.columns.map((c: any) => (
                          <td key={c.field_name} className="px-2.5 py-1 whitespace-nowrap">{String(r[c.field_name] ?? "")}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
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
