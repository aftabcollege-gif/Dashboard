import {
  DemoUploadedDataset,
  deleteDemoUploadedDataset,
  getDemoCreatedDashboards,
  getDemoUploadedDatasets,
  isDemoPreviewMode,
  mergeDemoDatasetList,
  saveDemoCreatedDashboard,
  saveDemoUploadedDataset,
} from "@/lib/demoStore";
import { parseSpreadsheetBuffer } from "@/lib/excelParser";

const TOKEN_KEY = "bi_access_token";
const CSRF_COOKIE_NAME = "bi_csrf_token";

export function getStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function setStoredToken(token: string | null) {
  if (typeof window === "undefined") return;
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export function getCsrfCookie(): string | null {
  if (typeof document === "undefined") return null;
  const escaped = CSRF_COOKIE_NAME.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = document.cookie.match(new RegExp(`(?:^|;\\s*)${escaped}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

let refreshPromise: Promise<boolean> | null = null;

async function attemptCookieRefresh(): Promise<boolean> {
  if (typeof window === "undefined") return false;
  if (isDemoPreviewMode()) return false;
  if (refreshPromise) return refreshPromise;

  refreshPromise = (async () => {
    try {
      const headers: Record<string, string> = {};
      const csrf = getCsrfCookie();
      if (csrf) headers["X-CSRF-Token"] = csrf;
      const res = await fetch("/api/v1/auth/refresh", {
        method: "POST",
        credentials: "include",
        headers,
      });
      return res.ok;
    } catch {
      return false;
    } finally {
      refreshPromise = null;
    }
  })();

  return refreshPromise;
}

async function handleClientDemoFileUpload(path: string, fd: FormData): Promise<any> {
  const fileEntry = fd.get("file");
  if (!(fileEntry instanceof File)) {
    throw new ApiError(400, "No file selected");
  }
  const buf = await fileEntry.arrayBuffer();
  let colRenames: Record<string, string> = {};
  let typeOverrides: Record<string, string> = {};
  try {
    const r = fd.get("column_renames_json");
    if (typeof r === "string" && r) colRenames = JSON.parse(r);
  } catch { /* ignore */ }
  try {
    const t = fd.get("type_overrides_json");
    if (typeof t === "string" && t) typeOverrides = JSON.parse(t);
  } catch { /* ignore */ }

  const parsed = await parseSpreadsheetBuffer(buf, fileEntry.name, {
    sheetName: (fd.get("sheet_name") as string) || undefined,
    hasHeader: String(fd.get("has_header") ?? "true") !== "false",
    nullStrategy: (fd.get("null_strategy") as string) || "keep",
    deduplicate: String(fd.get("deduplicate") ?? "false") === "true",
    columnRenames: colRenames,
    typeOverrides: typeOverrides,
  });

  if (path === "/data-sources/files/preview") {
    return {
      sheets: parsed.sheets,
      selected_sheet: parsed.selected_sheet,
      total_rows: parsed.total_rows,
      duplicate_count: parsed.duplicate_count,
      columns: parsed.columns,
      preview_rows: parsed.preview_rows,
    };
  }

  const dsName = String(fd.get("dataset_name") || fileEntry.name.replace(/\.[^.]+$/, "")).trim() || "Excel Dataset";
  const dsId = `ds_xl_${Date.now()}_${Math.floor(Math.random() * 1000)}`;
  const whTable = `wh_upload_${Date.now()}`;
  const uploaded: DemoUploadedDataset = {
    id: dsId,
    name: dsName,
    warehouse_table: whTable,
    row_count: parsed.total_rows,
    columns_count: parsed.columns.length,
    columns: parsed.columns.map((c) => c.field_name),
    fields: parsed.columns.map((c, idx) => ({
      id: `${dsId}_f_${idx + 1}`,
      field_name: c.field_name,
      display_name: c.display_name,
      data_type: c.data_type,
      semantic_role: c.semantic_role,
      nullable: c.nullable,
    })),
    rows: parsed.all_rows,
    created_at: new Date().toISOString(),
  };
  saveDemoUploadedDataset(uploaded);
  return {
    id: uploaded.id,
    dataset_id: uploaded.id,
    name: uploaded.name,
    warehouse_table: uploaded.warehouse_table,
    row_count: uploaded.row_count,
    columns_count: uploaded.columns_count,
    fields: uploaded.fields,
  };
}

export async function apiFetch<T = any>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const method = (options.method || "GET").toUpperCase();
  const inBrowser = typeof window !== "undefined";
  const inDemo = inBrowser && isDemoPreviewMode();

  // Client-side Vercel Preview Demo interception for user-uploaded Excel/CSV datasets
  if (inDemo) {
    if (method === "POST" && (path === "/data-sources/files/preview" || path === "/data-sources/files/import") && options.body instanceof FormData) {
      return handleClientDemoFileUpload(path, options.body) as Promise<T>;
    }

    const uploaded = getDemoUploadedDatasets();
    if (uploaded.length > 0) {
      const mProfile = path.match(/^\/datasets\/([^/]+)\/profile$/);
      if (method === "GET" && mProfile) {
        const hit = uploaded.find((d) => d.id === mProfile[1]);
        if (hit) {
          const profFields = hit.fields.map((f) => {
            let nullCount = 0;
            const distinctSet = new Set<any>();
            for (const r of hit.rows) {
              const v = r[f.field_name];
              if (v === "" || v === null || v === undefined) {
                nullCount++;
              } else if (distinctSet.size < 50000) {
                distinctSet.add(v);
              }
            }
            return {
              field_name: f.field_name,
              column_name: f.field_name,
              display_name: f.display_name,
              data_type: f.data_type,
              raw_type: f.data_type.toUpperCase(),
              semantic_role: f.semantic_role,
              nullable: f.nullable,
              null_count: nullCount,
              distinct_count: distinctSet.size,
            };
          });
          return {
            dataset_id: hit.id,
            row_count: hit.row_count,
            fields: profFields,
            columns: profFields,
          } as unknown as T;
        }
      }

      const mPreview = path.match(/^\/datasets\/([^/?]+)\/preview(\?.*)?$/);
      if (method === "GET" && mPreview) {
        const hit = uploaded.find((d) => d.id === mPreview[1]);
        if (hit) {
          const sp = new URLSearchParams((mPreview[2] || "").replace(/^\?/, ""));
          const lim = Number(sp.get("limit") || 30);
          return { columns: hit.columns, rows: hit.rows.slice(0, lim), row_count: Math.min(lim, hit.rows.length) } as unknown as T;
        }
      }

      const mDel = path.match(/^\/datasets\/([^/]+)$/);
      if (method === "DELETE" && mDel) {
        const hit = uploaded.find((d) => d.id === mDel[1]);
        if (hit) {
          deleteDemoUploadedDataset(hit.id);
          return { status: "deleted" } as unknown as T;
        }
      }
    }
  }

  const buildHeaders = () => {
    const headers: Record<string, string> = {
      ...(options.headers as Record<string, string> || {}),
    };
    const token = inDemo ? getStoredToken() : null;
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
    if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
      const csrf = getCsrfCookie();
      if (csrf && !headers["X-CSRF-Token"]) {
        headers["X-CSRF-Token"] = csrf;
      }
    }
    if (!(options.body instanceof FormData) && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    return headers;
  };

  let effectiveBody = options.body;
  // Automatically include uploaded Excel datasets and custom dashboards in Preview Demo API calls
  if (
    inDemo &&
    method === "POST" &&
    typeof effectiveBody === "string" &&
    (path.startsWith("/ai/") ||
      path.startsWith("/queries/") ||
      path.includes("/execute-batch") ||
      path.includes("/filter-options") ||
      (path.startsWith("/dashboards/widgets/") && path.endsWith("/data")))
  ) {
    try {
      const parsedBody = JSON.parse(effectiveBody);
      const uploaded = getDemoUploadedDatasets();
      const customDashes = getDemoCreatedDashboards();
      if (uploaded.length > 0 && !parsedBody.demo_uploaded_datasets) {
        const stripRowsForSchemaOnly = (u: DemoUploadedDataset) => ({
          ...u,
          is_uploaded_excel: true,
          rows: u.rows.slice(0, 5),
        });
        if (path === "/ai/generate-dashboard") {
          const target = parsedBody.dataset_id
            ? uploaded.filter((u) => u.id === parsedBody.dataset_id)
            : uploaded;
          parsedBody.demo_uploaded_datasets = target.map(stripRowsForSchemaOnly);
        } else if (path.startsWith("/ai/")) {
          parsedBody.demo_uploaded_datasets = uploaded;
        } else if (parsedBody.dataset_id) {
          const one = uploaded.find((u) => u.id === parsedBody.dataset_id);
          if (one) parsedBody.demo_uploaded_datasets = [one];
        } else {
          parsedBody.demo_uploaded_datasets = uploaded;
        }
      }
      if (customDashes.length > 0 && !parsedBody.demo_context?.custom_dashboards) {
        parsedBody.demo_context = {
          ...(parsedBody.demo_context || {}),
          custom_dashboards: customDashes,
        };
      }
      effectiveBody = JSON.stringify(parsedBody);
    } catch {
      // ignore non-JSON body
    }
  }

  const doFetch = () =>
    fetch(`/api/v1${path}`, {
      ...options,
      credentials: "include",
      body: effectiveBody,
      headers: buildHeaders(),
    });

  let res = await doFetch();

  const skipRefreshPaths = ["/auth/login", "/auth/refresh", "/auth/bootstrap-admin", "/auth/status"];
  if (
    res.status === 401 &&
    !inDemo &&
    !skipRefreshPaths.some((p) => path.startsWith(p))
  ) {
    const refreshed = await attemptCookieRefresh();
    if (refreshed) {
      res = await doFetch();
    }
  }

  if (!res.ok) {
    let msg = `Request failed (${res.status})`;
    try {
      const errJson = await res.json();
      msg = errJson.detail || errJson.message || msg;
    } catch {
      // ignore parse err
    }
    throw new ApiError(res.status, msg);
  }

  const contentType = res.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    const jsonRes = await res.json();
    if (inDemo && method === "GET" && path === "/datasets" && Array.isArray(jsonRes)) {
      return mergeDemoDatasetList(jsonRes) as unknown as T;
    }
    if (inDemo && method === "POST" && path === "/ai/generate-dashboard" && jsonRes?.created_dashboard) {
      saveDemoCreatedDashboard(jsonRes.created_dashboard);
    }
    return jsonRes;
  }
  return res.text() as unknown as T;
}

export async function apiDownload(path: string, filename: string) {
  const inDemo = typeof window !== "undefined" && isDemoPreviewMode();
  const token = inDemo ? getStoredToken() : null;
  let res = await fetch(`/api/v1${path}`, {
    credentials: "include",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (res.status === 401 && !inDemo) {
    const refreshed = await attemptCookieRefresh();
    if (refreshed) {
      res = await fetch(`/api/v1${path}`, {
        credentials: "include",
      });
    }
  }
  if (!res.ok) {
    throw new ApiError(res.status, `Download failed (${res.status})`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
