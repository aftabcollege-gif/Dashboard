"use client";

import { DashboardDetail, DashboardWidget } from "@/types/bi";

const CREATED_DASH_KEY = "bi_demo_created_dashboards_v1";

export function isBrowserDemo(): boolean {
  return typeof window !== "undefined";
}

export function getDemoCreatedDashboards(): DashboardDetail[] {
  if (!isBrowserDemo()) return [];
  try {
    const raw = localStorage.getItem(CREATED_DASH_KEY);
    const list = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) ? list : [];
  } catch {
    return [];
  }
}

export function saveDemoCreatedDashboard(dash: DashboardDetail): void {
  if (!isBrowserDemo() || !dash?.id) return;
  try {
    const list = getDemoCreatedDashboards().filter((d) => d.id !== dash.id);
    localStorage.setItem(CREATED_DASH_KEY, JSON.stringify([dash, ...list]));
  } catch {
    // ignore storage quota errors
  }
}

export function deleteDemoCreatedDashboard(dashId: string): void {
  if (!isBrowserDemo()) return;
  try {
    const list = getDemoCreatedDashboards().filter((d) => d.id !== dashId);
    localStorage.setItem(CREATED_DASH_KEY, JSON.stringify(list));
  } catch {
    // ignore
  }
}

export function getDemoDashOverride(dashId: string): { title?: string; theme_id?: string; added_filters?: any[] } {
  if (!isBrowserDemo()) return {};
  try {
    const raw = localStorage.getItem(`bi_demo_dash_override_${dashId}`);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

export function saveDemoDashOverride(
  dashId: string,
  patch: { title?: string; theme_id?: string; add_filter?: any }
): void {
  if (!isBrowserDemo() || !dashId) return;
  try {
    const cur = getDemoDashOverride(dashId);
    const next = {
      ...cur,
      ...(patch.title !== undefined ? { title: patch.title } : {}),
      ...(patch.theme_id !== undefined ? { theme_id: patch.theme_id } : {}),
      added_filters: patch.add_filter
        ? [...(cur.added_filters || []).filter((f: any) => f.field_name !== patch.add_filter.field_name), patch.add_filter]
        : cur.added_filters,
    };
    localStorage.setItem(`bi_demo_dash_override_${dashId}`, JSON.stringify(next));
    if (patch.theme_id) {
      localStorage.setItem(`bi_demo_theme_${dashId}`, patch.theme_id);
    }
    // Also update created dashboard record if it exists
    const created = getDemoCreatedDashboards();
    const idx = created.findIndex((d) => d.id === dashId);
    if (idx >= 0) {
      if (patch.title !== undefined) created[idx].title = patch.title;
      if (patch.theme_id !== undefined) created[idx].theme_id = patch.theme_id;
      if (patch.add_filter) {
        created[idx].filters = [...(created[idx].filters || []), patch.add_filter];
      }
      localStorage.setItem(CREATED_DASH_KEY, JSON.stringify(created));
    }
  } catch {
    // ignore
  }
}

export function getDemoCustomWidgets(dashId: string): DashboardWidget[] {
  if (!isBrowserDemo()) return [];
  try {
    const raw = localStorage.getItem(`bi_demo_custom_widgets_${dashId}`);
    const list = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) ? list : [];
  } catch {
    return [];
  }
}

export function addDemoCustomWidget(dashId: string, w: DashboardWidget): void {
  if (!isBrowserDemo() || !dashId || !w?.id) return;
  try {
    const list = getDemoCustomWidgets(dashId).filter((x) => x.id !== w.id);
    localStorage.setItem(`bi_demo_custom_widgets_${dashId}`, JSON.stringify([...list, w]));
  } catch {
    // ignore
  }
}

export function getDemoWidgetEdits(dashId: string): Record<string, any> {
  if (!isBrowserDemo()) return {};
  try {
    const raw = localStorage.getItem(`bi_demo_widget_edits_${dashId}`);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

export function saveDemoWidgetEdit(dashId: string, widgetId: string, patch: any): void {
  if (!isBrowserDemo() || !dashId || !widgetId) return;
  try {
    const cur = getDemoWidgetEdits(dashId);
    cur[widgetId] = { ...(cur[widgetId] || {}), ...patch };
    localStorage.setItem(`bi_demo_widget_edits_${dashId}`, JSON.stringify(cur));
  } catch {
    // ignore
  }
}

export function getDemoDeletedWidgetIds(dashId: string): string[] {
  if (!isBrowserDemo()) return [];
  try {
    const raw = localStorage.getItem(`bi_demo_deleted_widgets_${dashId}`);
    const list = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) ? list : [];
  } catch {
    return [];
  }
}

export function markDemoWidgetDeleted(dashId: string, widgetId: string): void {
  if (!isBrowserDemo() || !dashId || !widgetId) return;
  try {
    const custom = getDemoCustomWidgets(dashId).filter((x) => x.id !== widgetId);
    localStorage.setItem(`bi_demo_custom_widgets_${dashId}`, JSON.stringify(custom));
    const del = Array.from(new Set([...getDemoDeletedWidgetIds(dashId), widgetId]));
    localStorage.setItem(`bi_demo_deleted_widgets_${dashId}`, JSON.stringify(del));
  } catch {
    // ignore
  }
}

export function mergeDemoDashboardList(serverList: any[]): any[] {
  const created = getDemoCreatedDashboards();
  const createdSummaries = created.map((d) => ({
    id: d.id,
    title: d.title,
    description: d.description,
    is_template: false,
    widget_count: (d.widgets || []).length,
    theme_id: d.theme_id,
    updated_at: new Date().toISOString(),
  }));
  const merged = [...createdSummaries, ...(serverList || [])];
  return merged.map((item) => {
    const ov = getDemoDashOverride(item.id);
    const cw = getDemoCustomWidgets(item.id);
    const del = getDemoDeletedWidgetIds(item.id);
    const baseCount = item.widget_count ?? 0;
    const adjustedCount = Math.max(0, baseCount + cw.length - del.length);
    return {
      ...item,
      ...(ov.title ? { title: ov.title } : {}),
      ...(ov.theme_id ? { theme_id: ov.theme_id } : {}),
      widget_count: created.some((c) => c.id === item.id)
        ? Math.max(0, (created.find((c) => c.id === item.id)?.widgets?.length || 0) + cw.length - del.length)
        : adjustedCount,
    };
  });
}

export function applyDemoDashboardOverrides(
  dash: DashboardDetail,
  themes: any[]
): DashboardDetail {
  const ov = getDemoDashOverride(dash.id);
  const storedThemeId = ov.theme_id || (isBrowserDemo() ? localStorage.getItem(`bi_demo_theme_${dash.id}`) : null);
  const overrideTh = storedThemeId ? themes.find((x) => x.id === storedThemeId) : null;
  const customWidgets = getDemoCustomWidgets(dash.id);
  const widgetEdits = getDemoWidgetEdits(dash.id);
  const deletedIds = new Set(getDemoDeletedWidgetIds(dash.id));

  const existingIds = new Set((dash.widgets || []).map((w) => w.id));
  const combinedWidgets = [
    ...(dash.widgets || []),
    ...customWidgets.filter((cw) => !existingIds.has(cw.id)),
  ]
    .filter((w) => !deletedIds.has(w.id))
    .map((w) => {
      const ed = widgetEdits[w.id];
      if (!ed) return w;
      return {
        ...w,
        ...(ed.title ? { title: ed.title } : {}),
        ...(ed.chart_type ? { chart_type: ed.chart_type } : {}),
        query: ed.visual_spec ? { ...w.query, visual_spec: ed.visual_spec } : w.query,
      };
    });

  const extraFilters = (ov.added_filters || []).filter(
    (af: any) => !(dash.filters || []).some((ef: any) => ef.field_name === af.field_name)
  );

  return {
    ...dash,
    ...(ov.title ? { title: ov.title } : {}),
    ...(overrideTh ? { theme_id: overrideTh.id, theme: overrideTh } : {}),
    widgets: combinedWidgets,
    filters: [...(dash.filters || []), ...extraFilters],
  };
}

export interface DemoUploadedDataset {
  id: string;
  name: string;
  warehouse_table: string;
  row_count: number;
  columns_count: number;
  columns: string[];
  fields: {
    id: string;
    field_name: string;
    display_name: string;
    data_type: string;
    semantic_role: string;
    nullable: boolean;
  }[];
  rows: Record<string, any>[];
  created_at: string;
}

const UPLOADED_DATASETS_KEY = "bi_demo_uploaded_datasets_v1";
const PREVIEW_DEMO_FLAG_KEY = "bi_is_preview_demo";

export function isDemoPreviewMode(): boolean {
  if (!isBrowserDemo()) return false;
  try {
    return localStorage.getItem(PREVIEW_DEMO_FLAG_KEY) === "1";
  } catch {
    return false;
  }
}

export function setDemoPreviewMode(active: boolean): void {
  if (!isBrowserDemo()) return;
  try {
    localStorage.setItem(PREVIEW_DEMO_FLAG_KEY, active ? "1" : "0");
  } catch {
    // ignore
  }
}

export function getDemoUploadedDatasets(): DemoUploadedDataset[] {
  if (!isBrowserDemo()) return [];
  try {
    const raw = localStorage.getItem(UPLOADED_DATASETS_KEY);
    const list = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) ? list : [];
  } catch {
    return [];
  }
}

export function saveDemoUploadedDataset(ds: DemoUploadedDataset): void {
  if (!isBrowserDemo() || !ds?.id) return;
  try {
    const list = getDemoUploadedDatasets().filter((d) => d.id !== ds.id);
    // Keep rows trimmed cleanly to fit comfortably within localStorage if extremely huge (> 3000 rows)
    const trimmed = ds.rows.length > 3000 ? { ...ds, rows: ds.rows.slice(0, 3000) } : ds;
    localStorage.setItem(UPLOADED_DATASETS_KEY, JSON.stringify([trimmed, ...list]));
  } catch {
    // ignore storage quota errors
  }
}

export function deleteDemoUploadedDataset(dsId: string): void {
  if (!isBrowserDemo()) return;
  try {
    const list = getDemoUploadedDatasets().filter((d) => d.id !== dsId);
    localStorage.setItem(UPLOADED_DATASETS_KEY, JSON.stringify(list));
  } catch {
    // ignore
  }
}

export function mergeDemoDatasetList(serverDatasets: any[]): any[] {
  const uploaded = getDemoUploadedDatasets();
  if (!uploaded.length) return serverDatasets || [];
  const existingIds = new Set(uploaded.map((u) => u.id));
  const summaryUploaded = uploaded.map((u) => ({
    id: u.id,
    name: u.name,
    warehouse_table: u.warehouse_table,
    row_count: u.row_count,
    columns_count: u.columns_count,
    sync_status: "synced",
    last_synced_at: u.created_at,
    fields: u.fields,
    is_uploaded_excel: true,
  }));
  return [...summaryUploaded, ...(serverDatasets || []).filter((s) => !existingIds.has(s.id))];
}
