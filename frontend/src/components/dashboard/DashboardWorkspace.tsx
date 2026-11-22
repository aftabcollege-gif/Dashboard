"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Responsive, WidthProvider } from "react-grid-layout";
import {
  ArrowLeft,
  ArrowUp,
  BookmarkCheck,
  Bot,
  Check,
  Clipboard,
  Copy,
  Download,
  Edit3,
  Eye,
  Filter,
  History,
  Layers,
  MessageSquare,
  Plus,
  Redo2,
  RefreshCw,
  Save,
  Settings2,
  Share2,
  Sparkles,
  Trash2,
  Undo2,
  Wand2,
  X,
} from "lucide-react";
import Link from "next/link";
import WidgetRenderer from "@/components/charts/WidgetRenderer";
import GlobalFilterBar from "@/components/dashboard/GlobalFilterBar";
import PropertiesPanel from "@/components/dashboard/PropertiesPanel";
import { PersianAssistantPanel } from "@/components/ai/PersianAssistantPanel";
import { apiDownload, apiFetch } from "@/lib/api";
import { CHART_REGISTRY } from "@/lib/chart-registry";
import { useBi } from "@/lib/context";
import {
  addDemoCustomWidget,
  applyDemoDashboardOverrides,
  getDemoCreatedDashboards,
  markDemoWidgetDeleted,
  mergeDemoDashboardList,
} from "@/lib/demoStore";
import { BpmnGraphModel, inferBpmnFieldMapping, parseBpmnXml } from "@/lib/bpmnParser";
import {
  BiTheme,
  DashboardDetail,
  DashboardWidget,
  DatasetInfo,
  WidgetDataResult,
} from "@/types/bi";

const ResponsiveGridLayout = WidthProvider(Responsive);

export default function DashboardWorkspace({ dashboardId }: { dashboardId: string }) {
  const { locale, t, authStatus, themes, applyThemeTokens, showToast } = useBi();

  const [dash, setDash] = useState<DashboardDetail | null>(null);
  const [allDashboards, setAllDashboards] = useState<{ id: string; title: string; is_template?: boolean }[]>([]);
  const [datasets, setDatasets] = useState<DatasetInfo[]>([]);
  const [activePageId, setActivePageId] = useState<string>("");
  const [editMode, setEditMode] = useState(false);
  const [selectedWidgetId, setSelectedWidgetId] = useState<string | null>(null);
  const [isMobile, setIsMobile] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const mq = window.matchMedia("(max-width: 767px)");
    const sync = () => setIsMobile(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);

  // Widget Data Results (Progressive map)
  const [widgetData, setWidgetData] = useState<Record<string, WidgetDataResult>>({});
  const [loadingWidgets, setLoadingWidgets] = useState<Record<string, boolean>>({});
  const [lastBatchMs, setLastBatchMs] = useState<number | null>(null);

  // Filter, Cross-Filter & Drill States
  const [filterValues, setFilterValues] = useState<Record<string, any>>({});
  const [crossFilters, setCrossFilters] = useState<
    { field_name: string; operator: string; value: any; source_widget_id: string }[]
  >([]);
  const [drillStates, setDrillStates] = useState<
    Record<string, { field: string; value: any }[]>
  >({});
  const [widgetChartOverrides, setWidgetChartOverrides] = useState<Record<string, string>>({});
  const [clickModes, setClickModes] = useState<Record<string, "drill" | "cross_filter">>({});

  // Undo / Redo Stack for Layout & Edits
  const [undoStack, setUndoStack] = useState<any[]>([]);
  const [redoStack, setRedoStack] = useState<any[]>([]);
  const [copiedWidget, setCopiedWidget] = useState<DashboardWidget | null>(null);

  // Side Drawers & Modals
  const [showAddWidget, setShowAddWidget] = useState(false);
  const [showAiDrawer, setShowAiDrawer] = useState(false);
  const [showVersions, setShowVersions] = useState(false);
  const [showComments, setShowComments] = useState(false);
  const [shareInfo, setShareInfo] = useState<string | null>(null);

  // Add Widget Form
  const [newWTitle, setNewWTitle] = useState("");
  const [newWChart, setNewWChart] = useState("bar");
  const [newWDs, setNewWDs] = useState("");
  const [newWX, setNewWX] = useState("");
  const [newWY, setNewWY] = useState("");
  const [newWAgg, setNewWAgg] = useState("SUM");
  const [newWBpmnAssignee, setNewWBpmnAssignee] = useState("");
  const [newWBpmnReqId, setNewWBpmnReqId] = useState("");
  const [newWBpmnStatus, setNewWBpmnStatus] = useState("");
  const [newWBpmnHeatmap, setNewWBpmnHeatmap] = useState(true);
  const [newWBpmnGraph, setNewWBpmnGraph] = useState<BpmnGraphModel | null>(null);
  const [newWBpmnXml, setNewWBpmnXml] = useState<string>("");

  useEffect(() => {
    if (newWChart === "bpmn") {
      const targetDs = datasets.find((d) => d.id === newWDs) || datasets[0];
      if (!targetDs) return;
      const cols = (targetDs.fields || []).map((f) => f.field_name);
      const inferred = inferBpmnFieldMapping(cols);
      if (inferred.step_field) setNewWX(inferred.step_field);
      if (inferred.wait_field) setNewWY(inferred.wait_field);
      if (inferred.assignee_field) setNewWBpmnAssignee(inferred.assignee_field);
      if (inferred.request_id_field) setNewWBpmnReqId(inferred.request_id_field);
      if (inferred.status_field) setNewWBpmnStatus(inferred.status_field);
      setNewWAgg("AVG");
    }
  }, [newWChart, newWDs, datasets]);

  // AI Drawer State
  const [aiTab, setAiTab] = useState<"builder" | "nlq">("builder");
  const [assistantEditWidgetId, setAssistantEditWidgetId] = useState<string | null>(null);
  const [assistantMode, setAssistantMode] = useState<"auto" | "create_dashboard" | "add_widget" | "edit">("auto");
  const [nlqQuestion, setNlqQuestion] = useState("");
  const [nlqResult, setNlqResult] = useState<any>(null);
  const [aiInsights, setAiInsights] = useState<any>(null);

  // Versions & Comments State
  const [versionsList, setVersionsList] = useState<any[]>([]);
  const [commentsList, setCommentsList] = useState<any[]>([]);
  const [newComment, setNewComment] = useState("");

  const workspaceDomRef = useRef<HTMLDivElement>(null);

  const loadDashboard = useCallback(async () => {
    try {
      const localCreated = getDemoCreatedDashboards().find((x) => x.id === dashboardId) || null;
      const [d, dsList, savedState, dashList] = await Promise.all([
        localCreated
          ? Promise.resolve(localCreated)
          : apiFetch<DashboardDetail>(`/dashboards/${dashboardId}`).catch((e) => {
              const fallback = getDemoCreatedDashboards().find((x) => x.id === dashboardId);
              if (fallback) return fallback;
              throw e;
            }),
        apiFetch<DatasetInfo[]>("/datasets").catch(() => []),
        apiFetch<any>(`/dashboards/${dashboardId}/state`).catch(() => null),
        apiFetch<any[]>("/dashboards").catch(() => []),
      ]);
      const mergedList = authStatus?.environment === "vercel-preview-demo" ? mergeDemoDashboardList(dashList || []) : (dashList || []);
      setAllDashboards(mergedList);
      const resolvedDash =
        typeof window !== "undefined" && authStatus?.environment === "vercel-preview-demo"
          ? applyDemoDashboardOverrides(d, themes)
          : d;
      setDash(resolvedDash);
      setDatasets(dsList);
      if (resolvedDash.theme) {
        applyThemeTokens(resolvedDash.theme.tokens, resolvedDash.theme.mode);
      }
      if (savedState?.active_page_id && d.pages.some((p) => p.id === savedState.active_page_id)) {
        setActivePageId(savedState.active_page_id);
      } else if (!activePageId && d.pages.length > 0) {
        setActivePageId(d.pages[0].id);
      }
      if (savedState?.filter_state && Object.keys(filterValues).length === 0) {
        setFilterValues(savedState.filter_state);
      }
      if (savedState?.drill_state && Object.keys(drillStates).length === 0) {
        setDrillStates(savedState.drill_state);
      }
      if (savedState?.widget_overrides && Object.keys(widgetChartOverrides).length === 0) {
        setWidgetChartOverrides(savedState.widget_overrides);
      }
      if (dsList.length > 0 && !newWDs) setNewWDs(dsList[0].id);
    } catch (err: any) {
      showToast(err.message);
    }
  }, [dashboardId]);

  useEffect(() => {
    loadDashboard();
  }, [loadDashboard]);

  useEffect(() => {
    if (typeof window !== "undefined") {
      const sp = new URLSearchParams(window.location.search);
      if (sp.get("ai") === "1") {
        setAiTab("builder");
        setShowAiDrawer(true);
      }
    }
  }, []);

  useEffect(() => {
    if (typeof window === "undefined" || authStatus?.environment !== "vercel-preview-demo" || !dash || themes.length === 0) return;
    const storedThemeId = localStorage.getItem(`bi_demo_theme_${dash.id}`);
    if (!storedThemeId || storedThemeId === dash.theme_id) return;
    const overrideTh = themes.find((x) => x.id === storedThemeId);
    if (overrideTh) {
      applyThemeTokens(overrideTh.tokens, overrideTh.mode);
      setDash((prev) => (prev ? { ...prev, theme_id: overrideTh.id, theme: overrideTh } : prev));
    }
  }, [authStatus?.environment, dash?.id, themes]);

  // Build compiled active_filters array for API
  const compiledActiveFilters = useMemo(() => {
    if (!dash) return [];
    return dash.filters
      .map((f) => ({
        filter_id: f.id,
        field_name: f.field_name,
        operator: f.operator,
        value: filterValues[f.id],
        is_active: true,
      }))
      .filter((af) => af.value !== undefined && af.value !== "" && (!Array.isArray(af.value) || af.value.length > 0));
  }, [dash, filterValues]);

  // Execute all widgets on active page (progressive + cached)
  const executePageWidgets = useCallback(
    async (pageId: string, useCache: boolean = true) => {
      if (!dash || !pageId) return;
      const pageWidgets = dash.widgets.filter((w) => w.page_id === pageId);
      const loadingMap: Record<string, boolean> = {};
      pageWidgets.forEach((w) => {
        loadingMap[w.id] = true;
      });
      setLoadingWidgets((prev) => ({ ...prev, ...loadingMap }));

      try {
        const res = await apiFetch<any>(`/dashboards/${dash.id}/execute-batch`, {
          method: "POST",
          body: JSON.stringify({
            page_id: pageId,
            active_filters: compiledActiveFilters,
            cross_filters: crossFilters,
            drill_states: drillStates,
            use_cache: useCache,
          }),
        }).catch(() => ({ widgets: {}, load_ms: 1 }));
        const mergedWidgets: Record<string, any> = { ...(res.widgets || {}) };
        const missingCustom = pageWidgets.filter(
          (w) => !(w.id in mergedWidgets) && w.dataset_id && w.query?.visual_spec
        );
        if (missingCustom.length > 0) {
          const allFilters = [
            ...compiledActiveFilters,
            ...crossFilters.map((cf) => ({
              field_name: cf.field_name,
              operator: cf.operator,
              value: cf.value,
              is_active: true,
            })),
          ];
          await Promise.all(
            missingCustom.map(async (cw) => {
              try {
                const vr = await apiFetch<any>("/queries/execute-visual", {
                  method: "POST",
                  body: JSON.stringify({
                    dataset_id: cw.dataset_id,
                    visual_spec: cw.query.visual_spec,
                    filters: allFilters,
                  }),
                });
                mergedWidgets[cw.id] = {
                  ...vr,
                  effective_chart_type: cw.chart_type,
                };
              } catch {
                // ignore
              }
            })
          );
        }
        setWidgetData((prev) => ({ ...prev, ...mergedWidgets }));
        setLastBatchMs(res.load_ms);
      } catch (err: any) {
        showToast(err.message);
      } finally {
        const doneMap: Record<string, boolean> = {};
        pageWidgets.forEach((w) => {
          doneMap[w.id] = false;
        });
        setLoadingWidgets((prev) => ({ ...prev, ...doneMap }));
      }
    },
    [dash, compiledActiveFilters, crossFilters, drillStates]
  );

  useEffect(() => {
    if (dash && activePageId) {
      executePageWidgets(activePageId, true);
    }
  }, [dash, activePageId, compiledActiveFilters, crossFilters, drillStates]);

  // Execute single widget (used on drill down / manual widget refresh)
  const executeSingleWidget = async (w: DashboardWidget, nextDrillStack: { field: string; value: any }[], useCache = true) => {
    const prevLen = (drillStates[w.id] || []).length;
    if (nextDrillStack.length !== prevLen) {
      setWidgetChartOverrides((prev) => {
        if (!(w.id in prev)) return prev;
        const copy = { ...prev };
        delete copy[w.id];
        return copy;
      });
    }
    setLoadingWidgets((p) => ({ ...p, [w.id]: true }));
    try {
      const res = await apiFetch<WidgetDataResult>(`/dashboards/widgets/${w.id}/data`, {
        method: "POST",
        body: JSON.stringify({
          active_filters: compiledActiveFilters,
          cross_filters: crossFilters,
          drill_stack: nextDrillStack,
          use_cache: useCache,
        }),
      });
      setWidgetData((prev) => ({ ...prev, [w.id]: res }));
      setDrillStates((prev) => ({ ...prev, [w.id]: nextDrillStack }));
    } catch (err: any) {
      showToast(err.message);
    } finally {
      setLoadingWidgets((p) => ({ ...p, [w.id]: false }));
    }
  };

  const getWidgetClickMode = (w: DashboardWidget): "drill" | "cross_filter" => {
    if (clickModes[w.id]) return clickModes[w.id];
    const hasDrill = (w.drill_rule?.hierarchy?.length || 0) > 0;
    if (!hasDrill) return "cross_filter";
    if (w.chart_type === "donut" && w.config.interactions?.cross_filter !== false) return "cross_filter";
    return "drill";
  };

  // Handle click on any chart point (Drill Down OR Cross-Filter!)
  const handleWidgetPointClick = async (w: DashboardWidget, field: string, value: any) => {
    const hierarchy = w.drill_rule?.hierarchy || [];
    const currentStack = drillStates[w.id] || [];
    const mode = getWidgetClickMode(w);

    // 1. If in Drill mode and widget has deeper drill level and user can drill down -> Drill Down!
    if (mode === "drill" && hierarchy.length > 0 && currentStack.length < hierarchy.length && w.permissions.drill_down) {
      const currentDim = hierarchy[currentStack.length]?.field || field;
      const nextStack = [...currentStack, { field: currentDim, value }];
      await executeSingleWidget(w, nextStack);
      return;
    }

    // 2. Otherwise trigger Cross-Filtering if enabled (replace prior selection from same widget)!
    if (w.config.interactions?.cross_filter !== false) {
      setCrossFilters((prev) => {
        const sameExact = prev.some(
          (cf) => cf.source_widget_id === w.id && cf.field_name === field && String(cf.value) === String(value)
        );
        const withoutThisWidget = prev.filter((cf) => cf.source_widget_id !== w.id);
        if (sameExact) return withoutThisWidget;
        return [...withoutThisWidget, { field_name: field, operator: "eq", value, source_widget_id: w.id }];
      });
    }
  };

  const applyDrillToDashboardCrossFilter = (w: DashboardWidget) => {
    const stack = drillStates[w.id] || [];
    if (stack.length === 0) return;
    setCrossFilters((prev) => {
      const withoutThisWidget = prev.filter((cf) => cf.source_widget_id !== w.id);
      const nextEntries = stack.map((item) => ({
        field_name: item.field,
        operator: "eq",
        value: item.value,
        source_widget_id: w.id,
      }));
      return [...withoutThisWidget, ...nextEntries];
    });
    showToast(`Applied ${stack.map((s) => `${s.field}=${s.value}`).join(" & ")} to all dashboard widgets`);
  };

  // Layout change handler with Undo stack
  const currentPage = dash?.pages.find((p) => p.id === activePageId);
  const pageWidgets = useMemo(
    () => (dash?.widgets || []).filter((w) => w.page_id === activePageId),
    [dash, activePageId]
  );

  const gridLayouts = useMemo(() => {
    const lg = pageWidgets.map((w) => ({
      i: w.id,
      x: w.layout.x,
      y: w.layout.y,
      w: w.layout.w,
      h: w.layout.h,
      minW: 2,
      minH: 2,
    }));
    return { lg, md: lg, sm: lg.map((item) => ({ ...item, w: Math.min(item.w, 6) })), xs: lg.map((item) => ({ ...item, w: 4, x: 0 })) };
  }, [pageWidgets]);

  const handleLayoutChange = (currentLayout: any[]) => {
    if (!editMode || !dash) return;
    const prevSnapshot = pageWidgets.map((w) => ({ i: w.id, ...w.layout }));
    // Only record if coordinates actually shifted
    const changed = currentLayout.some((item) => {
      const old = prevSnapshot.find((o) => o.i === item.i);
      return old && (old.x !== item.x || old.y !== item.y || old.w !== item.w || old.h !== item.h);
    });
    if (!changed) return;

    setUndoStack((s) => [...s.slice(-19), prevSnapshot]);
    setRedoStack([]);
    setDash((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        widgets: prev.widgets.map((w) => {
          const updated = currentLayout.find((l) => l.i === w.id);
          return updated ? { ...w, layout: { x: updated.x, y: updated.y, w: updated.w, h: updated.h } } : w;
        }),
      };
    });
  };

  const handleUndo = () => {
    if (undoStack.length === 0 || !dash) return;
    const prevCoords = undoStack[undoStack.length - 1];
    const currCoords = pageWidgets.map((w) => ({ i: w.id, ...w.layout }));
    setUndoStack((s) => s.slice(0, -1));
    setRedoStack((s) => [...s, currCoords]);
    setDash({
      ...dash,
      widgets: dash.widgets.map((w) => {
        const c = prevCoords.find((p: any) => p.i === w.id);
        return c ? { ...w, layout: { x: c.x, y: c.y, w: c.w, h: c.h } } : w;
      }),
    });
  };

  const handleRedo = () => {
    if (redoStack.length === 0 || !dash) return;
    const nextCoords = redoStack[redoStack.length - 1];
    const currCoords = pageWidgets.map((w) => ({ i: w.id, ...w.layout }));
    setRedoStack((s) => s.slice(0, -1));
    setUndoStack((s) => [...s, currCoords]);
    setDash({
      ...dash,
      widgets: dash.widgets.map((w) => {
        const c = nextCoords.find((p: any) => p.i === w.id);
        return c ? { ...w, layout: { x: c.x, y: c.y, w: c.w, h: c.h } } : w;
      }),
    });
  };

  const saveLayoutToServer = async () => {
    if (!dash || !activePageId) return;
    try {
      await apiFetch(`/dashboards/${dash.id}/layout`, {
        method: "PUT",
        body: JSON.stringify({
          page_id: activePageId,
          columns_count: currentPage?.layout_settings?.columns_count || 12,
          items: pageWidgets.map((w) => ({ i: w.id, ...w.layout })),
        }),
      });
      showToast("Layout saved");
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const savePersonalState = async () => {
    if (!dash) return;
    try {
      await apiFetch(`/dashboards/${dash.id}/state`, {
        method: "PUT",
        body: JSON.stringify({
          active_page_id: activePageId,
          filter_state: filterValues,
          drill_state: drillStates,
          widget_overrides: widgetChartOverrides,
        }),
      });
      showToast("Personal dashboard state saved");
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleThemeChange = async (themeId: string) => {
    if (!dash) return;
    const th = themes.find((x) => x.id === themeId);
    if (th) {
      applyThemeTokens(th.tokens, th.mode);
      setDash((prev) =>
        prev
          ? {
              ...prev,
              theme_id: th.id,
              theme: th,
            }
          : prev
      );
    }
    if (typeof window !== "undefined") {
      localStorage.setItem(`bi_demo_theme_${dash.id}`, themeId);
    }
    try {
      await apiFetch(`/dashboards/${dash.id}`, {
        method: "PUT",
        body: JSON.stringify({ theme_id: themeId }),
      });
      showToast(locale === "fa" ? `قالب به ${th?.name || "سفارشی"} تغییر کرد` : `Theme switched to ${th?.name || "custom"}`);
      if (authStatus?.environment !== "vercel-preview-demo") {
        loadDashboard();
      }
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleAddWidgetSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!dash || !activePageId) return;
    const isBpmn = newWChart === "bpmn";
    const visualSpecPayload: Record<string, any> = {
      x_field: newWX || undefined,
      y_field: newWY || "*",
      aggregation: isBpmn ? "AVG" : newWAgg,
      ...(isBpmn
        ? {
            bpmn_config: {
              process_name: newWBpmnGraph?.processName || newWTitle || "فرآیند BPMN",
              step_field: newWX || "step_name",
              wait_field: newWY && newWY !== "*" ? newWY : "wait_hours",
              assignee_field: newWBpmnAssignee || undefined,
              request_id_field: newWBpmnReqId || undefined,
              status_field: newWBpmnStatus || undefined,
              heatmap_enabled: newWBpmnHeatmap,
              heatmap_metric: "avg_wait",
              ...(newWBpmnXml ? { bpmn_xml: newWBpmnXml } : {}),
              ...(newWBpmnGraph?.nodes?.length ? { nodes: newWBpmnGraph.nodes, edges: newWBpmnGraph.edges } : {}),
            },
          }
        : {}),
    };
    const wTitle = newWTitle || (isBpmn ? "نمودار گردش فرآیند و کشف گلوگاه (BPMN)" : "New BI Widget");
    const wLayout = {
      x: 0,
      y: 99,
      w: newWChart === "kpi" ? 4 : isBpmn ? 12 : 6,
      h: newWChart === "kpi" ? 3 : isBpmn ? 9 : 7,
    };
    try {
      const createdRes = await apiFetch<any>(`/dashboards/${dash.id}/widgets`, {
        method: "POST",
        body: JSON.stringify({
          page_id: activePageId,
          title: wTitle,
          chart_type: newWChart,
          dataset_id: newWDs || null,
          layout_x: wLayout.x,
          layout_y: wLayout.y,
          layout_w: wLayout.w,
          layout_h: wLayout.h,
          visual_spec: visualSpecPayload,
        }),
      });
      if (typeof window !== "undefined" && authStatus?.environment === "vercel-preview-demo") {
        const wid = createdRes?.id && !String(createdRes.id).startsWith("preview") ? createdRes.id : `w_custom_${Date.now()}`;
        addDemoCustomWidget(dash.id, {
          id: wid,
          dashboard_id: dash.id,
          page_id: activePageId,
          title: wTitle,
          description: isBpmn ? "BPMN Process & Bottleneck Map" : "",
          chart_type: newWChart,
          dataset_id: newWDs || null,
          layout: wLayout,
          config: {
            style: {},
            visualization: { show_legend: true, show_tooltip: true, heatmap_enabled: newWBpmnHeatmap },
            interactions: { cross_filter: true },
            advanced: {},
          },
          query: {
            query_mode: "visual",
            sql_text: null,
            visual_spec: visualSpecPayload,
            parameters: {},
          },
          drill_rule: { hierarchy: [], level_rules: [], drill_through_page_id: null },
          widget_filters: [],
          permissions: { view: true, edit: true, delete: true, drill_down: true },
        });
      }
      setShowAddWidget(false);
      setNewWTitle("");
      setNewWBpmnGraph(null);
      setNewWBpmnXml("");
      showToast(locale === "fa" ? "ویجت اضافه شد" : "Widget added");
      loadDashboard();
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleDuplicateOrPaste = async (w: DashboardWidget) => {
    try {
      await apiFetch(`/dashboards/widgets/${w.id}/duplicate`, { method: "POST" });
      showToast("Widget duplicated");
      loadDashboard();
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleDeleteWidget = async (wId: string) => {
    try {
      await apiFetch(`/dashboards/widgets/${wId}`, { method: "DELETE" });
      if (selectedWidgetId === wId) setSelectedWidgetId(null);
      if (dash && typeof window !== "undefined" && authStatus?.environment === "vercel-preview-demo") {
        markDemoWidgetDeleted(dash.id, wId);
        setDash((prev) => (prev ? { ...prev, widgets: prev.widgets.filter((x) => x.id !== wId) } : prev));
      } else {
        loadDashboard();
      }
      showToast(locale === "fa" ? "ویجت حذف شد" : "Widget deleted");
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleAddPage = async () => {
    if (!dash) return;
    const title = prompt("New page title:");
    if (!title) return;
    try {
      const p = await apiFetch<{ id: string }>(`/dashboards/${dash.id}/pages`, {
        method: "POST",
        body: JSON.stringify({ title }),
      });
      await loadDashboard();
      setActivePageId(p.id);
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleNlq = async (e: React.FormEvent) => {
    e.preventDefault();
    const targetDsId = datasets[0]?.id;
    if (!targetDsId || !nlqQuestion.trim()) return;
    try {
      const res = await apiFetch("/ai/nlq", {
        method: "POST",
        body: JSON.stringify({ dataset_id: targetDsId, question: nlqQuestion }),
      });
      setNlqResult(res);
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleRunAnomalyForecast = async (wId: string) => {
    const wd = widgetData[wId];
    if (!wd || !wd.rows?.length) return;
    const labelKey = wd.columns[0];
    const valKey = wd.columns.includes("value") ? "value" : wd.columns[wd.columns.length - 1];
    const labels = wd.rows.map((r) => String(r[labelKey]));
    const values = wd.rows.map((r) => Number(r[valKey] || 0));
    try {
      const res = await apiFetch("/ai/insights", {
        method: "POST",
        body: JSON.stringify({ labels, values, forecast_periods: 3 }),
      });
      setAiInsights({ widgetTitle: dash?.widgets.find((x) => x.id === wId)?.title, ...res });
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const openVersions = async () => {
    if (!dash) return;
    setShowVersions(true);
    const list = await apiFetch(`/dashboards/${dash.id}/versions`).catch(() => []);
    setVersionsList(list);
  };

  const saveVersionSnapshot = async () => {
    if (!dash) return;
    const label = prompt("Version label:", `Snapshot ${new Date().toLocaleTimeString()}`);
    if (!label) return;
    await apiFetch(`/dashboards/${dash.id}/versions`, { method: "POST", body: JSON.stringify({ label }) });
    openVersions();
    showToast("Dashboard version saved");
  };

  const openComments = async () => {
    if (!dash) return;
    setShowComments(true);
    const list = await apiFetch(`/dashboards/${dash.id}/comments`).catch(() => []);
    setCommentsList(list);
  };

  const submitComment = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!dash || !newComment.trim()) return;
    await apiFetch(`/dashboards/${dash.id}/comments`, {
      method: "POST",
      body: JSON.stringify({ content: newComment }),
    });
    setNewComment("");
    openComments();
  };

  const handleShare = async () => {
    if (!dash) return;
    try {
      const res = await apiFetch<{ embed_token: string }>(`/dashboards/${dash.id}/share`, { method: "POST" });
      setShareInfo(`${window.location.origin}/public/${res.embed_token}`);
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleExportPng = () => {
    const canvases = workspaceDomRef.current?.querySelectorAll("canvas");
    if (!canvases || canvases.length === 0) {
      showToast("No rendered chart canvas available; try PDF/Excel export.");
      return;
    }
    const url = (canvases[0] as HTMLCanvasElement).toDataURL("image/png");
    const a = document.createElement("a");
    a.href = url;
    a.download = `dashboard_chart_${dash?.id.slice(0, 6)}.png`;
    a.click();
  };

  if (!dash) {
    return <div className="p-8 text-sm opacity-70">{locale === "fa" ? "در حال بارگذاری داشبورد..." : "Loading Dashboard Workspace..."}</div>;
  }

  const selectedWidget = dash.widgets.find((w) => w.id === selectedWidgetId) || null;
  const addDs = datasets.find((d) => d.id === newWDs) || datasets[0];

  const sortedMobileWidgets = [...pageWidgets].sort(
    (a, b) => (a.layout.y - b.layout.y) || (a.layout.x - b.layout.x)
  );

  const renderWidgetCard = (w: DashboardWidget, mobileStack = false) => {
    const wd = widgetData[w.id];
    const isSelected = selectedWidgetId === w.id;
    const autoType = wd?.effective_chart_type || w.chart_type;
    const effType = widgetChartOverrides[w.id] || autoType;
    const drillCrumbs = wd?.drill?.breadcrumbs || [];
    const hasDrill = (w.drill_rule?.hierarchy?.length || 0) > 0;
    const isDrilled = drillCrumbs.length > 1;
    const clickMode = getWidgetClickMode(w);
    const isCrossSource = crossFilters.some((cf) => cf.source_widget_id === w.id);
    const canCrossFilter =
      w.config.interactions?.cross_filter !== false &&
      w.chart_type !== "kpi" &&
      w.chart_type !== "gauge" &&
      w.chart_type !== "table";
    const canConvertChart = w.chart_type !== "kpi" && w.chart_type !== "gauge";
    const convertOptions = Array.from(
      new Set([
        effType,
        autoType,
        w.chart_type,
        "bar",
        "horizontal_bar",
        "line",
        "area",
        "pie",
        "donut",
        "stacked_bar",
        "combo",
        "waterfall",
        "funnel",
        "treemap",
        "radar",
        "bpmn",
        "table",
      ])
    );
    const customStyle = w.config.style || {};
    const mobileCardClass = mobileStack
      ? w.chart_type === "kpi"
        ? "col-span-1 h-36"
        : effType === "bpmn"
        ? "col-span-2 h-[440px]"
        : effType === "table" || effType === "pivot_table"
        ? "col-span-2 h-[350px]"
        : "col-span-2 h-[310px]"
      : "";

    return (
      <div
        key={w.id}
        onClick={() => editMode && setSelectedWidgetId(w.id)}
        className={`bi-card flex flex-col overflow-hidden transition-shadow ${mobileCardClass} ${
          isSelected ? "ring-2 ring-blue-500" : isCrossSource ? "ring-2 ring-emerald-500/70" : ""
        }`}
        style={{
          background: customStyle.background_color || "var(--bi-card)",
          color: customStyle.text_color || "var(--bi-text)",
          borderRadius: customStyle.border_radius ? `${customStyle.border_radius}px` : "var(--bi-radius)",
        }}
      >
        {/* Widget Header */}
        <div
          className={`px-3 sm:px-3.5 py-2 border-b flex flex-wrap items-center justify-between gap-1.5 sm:gap-2 ${
            editMode && !mobileStack ? "widget-drag-handle cursor-move" : ""
          }`}
          style={{ borderColor: "var(--bi-border)" }}
        >
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="text-xs font-bold leading-snug line-clamp-1 sm:truncate">{w.title}</span>
              {hasDrill && canCrossFilter ? (
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    const nextMode = clickMode === "drill" ? "cross_filter" : "drill";
                    setClickModes((prev) => ({ ...prev, [w.id]: nextMode }));
                    showToast(
                      nextMode === "drill"
                        ? `${w.title}: ${locale === "fa" ? "کلیک برای دریل‌داون" : "Click points to Drill-Down"}`
                        : `${w.title}: ${locale === "fa" ? "کلیک برای فیلتر کل داشبورد" : "Click points to Cross-Filter whole dashboard"}`
                    );
                  }}
                  className={`text-[9px] px-1.5 py-0.5 rounded font-semibold shrink-0 transition ${
                    clickMode === "drill"
                      ? "bg-blue-500/15 text-blue-500 hover:bg-blue-500/25"
                      : isCrossSource
                      ? "bg-emerald-500 text-white"
                      : "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 hover:bg-emerald-500/25"
                  }`}
                  title={locale === "fa" ? "برای تغییر حالت دریل‌داون / فیلتر کل داشبورد کلیک کنید" : "Click badge to toggle between Drill-Down and Dashboard Cross-Filter"}
                >
                  {clickMode === "drill"
                    ? "Drill-Down ⇄"
                    : isCrossSource
                    ? locale === "fa" ? "در حال فیلتر ⇄" : "Filtering Dashboard ⇄"
                    : "Cross-Filter ⇄"}
                </button>
              ) : hasDrill ? (
                <span className="text-[9px] px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-500 font-semibold shrink-0">
                  Drill-Down
                </span>
              ) : canCrossFilter ? (
                <span
                  className={`text-[9px] px-1.5 py-0.5 rounded font-semibold shrink-0 ${
                    isCrossSource
                      ? "bg-emerald-500 text-white"
                      : "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                  }`}
                >
                  {isCrossSource ? (locale === "fa" ? "در حال فیلتر" : "Filtering Dashboard") : "Cross-Filter"}
                </span>
              ) : null}
              {isDrilled && (
                <span className="text-[9px] px-1.5 py-0.5 rounded bg-violet-500/15 text-violet-500 font-semibold shrink-0">
                  {w.chart_type} → {effType}
                </span>
              )}
            </div>
            {isDrilled && (
              <div className="flex items-center gap-1 text-[10px] mt-0.5 flex-wrap">
                {drillCrumbs.map((bc, idx) => (
                  <React.Fragment key={bc.level}>
                    {idx > 0 && <span>/</span>}
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        const nextStack = (drillStates[w.id] || []).slice(0, bc.level);
                        executeSingleWidget(w, nextStack);
                      }}
                      className="hover:underline text-blue-500 font-semibold"
                    >
                      {bc.label}
                    </button>
                  </React.Fragment>
                ))}
              </div>
            )}
          </div>

          <div className="flex items-center gap-1 shrink-0 flex-wrap">
            {wd?.cache_hit && (
              <span className="text-[9px] px-1.5 py-0.5 rounded bg-sky-500/15 text-sky-500 font-semibold">
                CACHE
              </span>
            )}
            {isDrilled && (
              <>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    applyDrillToDashboardCrossFilter(w);
                  }}
                  className={`text-[10px] px-1.5 py-1 sm:py-0.5 rounded border font-semibold flex items-center gap-1 transition ${
                    isCrossSource
                      ? "bg-emerald-500 text-white border-emerald-500"
                      : "border-emerald-500/40 text-emerald-600 dark:text-emerald-400 hover:bg-emerald-500/10"
                  }`}
                  title={locale === "fa" ? "اعمال این دریل‌داون روی کل داشبورد" : "Filter entire dashboard by this drill level"}
                >
                  <Filter className="w-2.5 h-2.5" />
                  <span>{locale === "fa" ? "فیلتر داشبورد" : "Filter All"}</span>
                </button>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    const nextStack = (drillStates[w.id] || []).slice(0, -1);
                    executeSingleWidget(w, nextStack);
                  }}
                  className="p-1.5 sm:p-1 rounded hover:bg-blue-500/10 text-blue-500"
                  title={t("drillUp")}
                >
                  <ArrowUp className="w-3.5 h-3.5" />
                </button>
              </>
            )}
            {canConvertChart && (
              <select
                aria-label={`Convert ${w.title} chart type`}
                value={effType}
                onClick={(e) => e.stopPropagation()}
                onChange={(e) => {
                  e.stopPropagation();
                  const nextChart = e.target.value;
                  setWidgetChartOverrides((prev) => ({ ...prev, [w.id]: nextChart }));
                  if (dash.permissions.edit) {
                    apiFetch(`/dashboards/widgets/${w.id}/switch-chart`, {
                      method: "POST",
                      body: JSON.stringify({ target_chart_type: nextChart }),
                    }).catch(() => {});
                  }
                  showToast(locale === "fa" ? `ویجت به ${nextChart} تبدیل شد` : `Widget converted to ${nextChart}`);
                }}
                className={`text-[10px] px-1.5 py-1 sm:py-0.5 rounded border font-semibold cursor-pointer max-w-[125px] ${
                  isDrilled ? "border-violet-500/60 text-violet-500" : ""
                }`}
                style={{
                  background: "var(--bi-surface)",
                  color: isDrilled ? undefined : "var(--bi-text)",
                  borderColor: isDrilled ? undefined : "var(--bi-border)",
                }}
                title={locale === "fa" ? "تبدیل نوع ویجت (قبل یا بعد از دریل‌داون)" : "Convert widget type (works after drill-down too)"}
              >
                {convertOptions.map((ct) => {
                  const meta = CHART_REGISTRY.find((c) => c.id === ct);
                  return (
                    <option key={ct} value={ct} style={{ background: "var(--bi-surface)", color: "var(--bi-text)" }}>
                      {meta ? (locale === "fa" ? meta.labelFa : meta.label) : ct}
                    </option>
                  );
                })}
              </select>
            )}
            {w.drill_rule?.drill_through_page_id && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setActivePageId(w.drill_rule.drill_through_page_id!);
                }}
                className="text-[10px] px-1.5 py-1 sm:py-0.5 rounded border hover:bg-blue-500/10"
                style={{ borderColor: "var(--bi-border)" }}
              >
                {locale === "fa" ? "جزئیات" : "Details →"}
              </button>
            )}
            {dash.permissions.edit && (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  setAssistantEditWidgetId(w.id);
                  setAssistantMode("edit");
                  setAiTab("builder");
                  setShowAiDrawer(true);
                }}
                className="p-1.5 sm:p-1 rounded text-violet-500 hover:bg-violet-500/10"
                title={locale === "fa" ? "ویرایش این نمودار با دستیار فارسی" : "Edit with Persian AI Assistant"}
              >
                <Wand2 className="w-3.5 h-3.5" />
              </button>
            )}
            <button
              onClick={(e) => {
                e.stopPropagation();
                executeSingleWidget(w, drillStates[w.id] || [], false);
              }}
              className="p-1.5 sm:p-1 rounded hover:opacity-70"
              title="Refresh widget"
            >
              <RefreshCw className={`w-3 h-3 ${loadingWidgets[w.id] ? "animate-spin" : ""}`} />
            </button>

            {editMode && (
              <>
                <button
                  onClick={(e) => { e.stopPropagation(); setSelectedWidgetId(w.id); }}
                  className="p-1.5 sm:p-1 rounded hover:opacity-70"
                  title="Properties"
                >
                  <Settings2 className="w-3.5 h-3.5" />
                </button>
                <button
                  onClick={(e) => { e.stopPropagation(); setCopiedWidget(w); showToast("Widget copied"); }}
                  className="p-1.5 sm:p-1 rounded hover:opacity-70"
                  title={t("copy")}
                >
                  <Copy className="w-3 h-3" />
                </button>
                <button
                  onClick={(e) => { e.stopPropagation(); handleDeleteWidget(w.id); }}
                  className="p-1.5 sm:p-1 rounded text-red-500 hover:bg-red-500/10"
                  title="Delete"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </>
            )}
          </div>
        </div>

        {/* Widget Body */}
        <div className="flex-1 p-2.5 sm:p-3 min-h-0">
          {wd?.error ? (
            <div className="h-full flex items-center justify-center text-xs text-red-500 text-center p-2">
              {wd.error}
            </div>
          ) : (
            <WidgetRenderer
              chartType={effType}
              columns={wd?.columns || []}
              rows={wd?.rows || []}
              styleConfig={customStyle}
              vizConfig={w.config.visualization}
              visualSpec={w.query.visual_spec}
              onPointClick={(field, val) => handleWidgetPointClick(w, field, val)}
            />
          )}
        </div>
      </div>
    );
  };

  return (
    <div ref={workspaceDomRef} className="space-y-4">
      {/* Top Toolbar */}
      <div className="bi-card p-2.5 sm:p-3 flex flex-col lg:flex-row lg:items-center justify-between gap-2.5 sm:gap-3">
        <div className="flex items-start sm:items-center gap-2.5 sm:gap-3 min-w-0">
          <Link href="/dashboards" className="p-2 sm:p-1.5 rounded-lg border hover:opacity-80 shrink-0" style={{ borderColor: "var(--bi-border)" }}>
            <ArrowLeft className={`w-4 h-4 ${locale === "fa" ? "rotate-180" : ""}`} />
          </Link>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <h1 className="text-sm sm:text-base font-bold truncate">{dash.title}</h1>
              {lastBatchMs !== null && (
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-500 font-semibold shrink-0">
                  {lastBatchMs} ms
                </span>
              )}
            </div>
            <p className="text-[11px] sm:text-xs line-clamp-1 sm:truncate max-w-md" style={{ color: "var(--bi-muted)" }}>{dash.description}</p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          {/* Quick Switch Between Dashboards / Templates */}
          {allDashboards.length > 1 && (
            <select
              aria-label={locale === "fa" ? "انتخاب داشبورد" : "Switch Dashboard"}
              value={dash.id}
              onChange={(e) => {
                if (e.target.value && e.target.value !== dash.id) {
                  window.location.href = `/dashboards/${e.target.value}`;
                }
              }}
              className="px-2 py-1.5 rounded-lg border text-xs font-medium max-w-[165px] sm:max-w-[200px]"
              style={{ background: "var(--bi-surface)", color: "var(--bi-text)", borderColor: "var(--bi-border)" }}
              title={locale === "fa" ? "تغییر داشبورد / نمونه" : "Switch Dashboard / Template"}
            >
              {allDashboards.map((dItem) => (
                <option key={dItem.id} value={dItem.id} style={{ background: "var(--bi-surface)", color: "var(--bi-text)" }}>
                  {dItem.title}
                </option>
              ))}
            </select>
          )}

          {/* Edit / View Mode Toggle */}
          {dash.permissions.edit && (
            <button
              onClick={() => { setEditMode((m) => !m); if (editMode) setSelectedWidgetId(null); }}
              className="px-2.5 sm:px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-1.5 transition text-white"
              style={{ background: editMode ? "#10b981" : "var(--bi-primary)" }}
            >
              {editMode ? <><Eye className="w-3.5 h-3.5" /> {t("viewMode")}</> : <><Edit3 className="w-3.5 h-3.5" /> {t("editMode")}</>}
            </button>
          )}

          {editMode && (
            <>
              {dash.permissions.add_widget && (
                <>
                  <button
                    onClick={() => setShowAddWidget(true)}
                    className="px-2.5 py-1.5 rounded-lg border text-xs font-semibold flex items-center gap-1"
                    style={{ borderColor: "var(--bi-border)" }}
                  >
                    <Plus className="w-3.5 h-3.5" /> {t("addWidget")}
                  </button>
                  <button
                    onClick={() => {
                      setAiTab("builder");
                      setShowAiDrawer(true);
                    }}
                    className="px-2.5 py-1.5 rounded-lg border text-xs font-semibold flex items-center gap-1 text-violet-500 border-violet-500/40 hover:bg-violet-500/10"
                  >
                    <Sparkles className="w-3.5 h-3.5" /> {locale === "fa" ? "ساخت ویجت با متن" : "Text KPI"}
                  </button>
                </>
              )}
              <button onClick={handleUndo} disabled={undoStack.length === 0} className="p-1.5 rounded-lg border disabled:opacity-30" style={{ borderColor: "var(--bi-border)" }} title={t("undo")}>
                <Undo2 className="w-3.5 h-3.5" />
              </button>
              <button onClick={handleRedo} disabled={redoStack.length === 0} className="p-1.5 rounded-lg border disabled:opacity-30" style={{ borderColor: "var(--bi-border)" }} title={t("redo")}>
                <Redo2 className="w-3.5 h-3.5" />
              </button>
              {copiedWidget && (
                <button onClick={() => handleDuplicateOrPaste(copiedWidget)} className="px-2 py-1.5 rounded-lg border text-xs flex items-center gap-1" style={{ borderColor: "var(--bi-border)" }}>
                  <Clipboard className="w-3.5 h-3.5" /> {t("paste")}
                </button>
              )}
              <button onClick={saveLayoutToServer} className="px-2.5 py-1.5 rounded-lg border text-xs font-semibold flex items-center gap-1" style={{ borderColor: "var(--bi-primary)", color: "var(--bi-primary)" }}>
                <Save className="w-3.5 h-3.5" /> {t("saveLayout")}
              </button>
              <select
                value={currentPage?.layout_settings?.columns_count || 12}
                onChange={(e) => {
                  const colsVal = Number(e.target.value);
                  setDash((prev) =>
                    prev
                      ? {
                          ...prev,
                          pages: prev.pages.map((p) =>
                            p.id === activePageId
                              ? { ...p, layout_settings: { ...p.layout_settings, columns_count: colsVal } }
                              : p
                          ),
                        }
                      : prev
                  );
                }}
                className="px-2 py-1.5 rounded-lg border text-xs"
                style={{ background: "var(--bi-surface)", color: "var(--bi-text)", borderColor: "var(--bi-border)" }}
                title="Grid Columns"
              >
                <option value={12}>12 Cols</option>
                <option value={16}>16 Cols</option>
                <option value={24}>24 Cols</option>
              </select>
            </>
          )}

          {/* Theme / Visual Template Selector */}
          {(dash.permissions.change_theme || themes.length > 0) && (
            <select
              aria-label={locale === "fa" ? "انتخاب تم و قالب ظاهری" : "Dashboard Theme / Template"}
              value={dash.theme_id || ""}
              onChange={(e) => handleThemeChange(e.target.value)}
              className="px-2.5 py-1.5 rounded-lg border text-xs font-medium"
              style={{ background: "var(--bi-surface)", color: "var(--bi-text)", borderColor: "var(--bi-border)" }}
              title={locale === "fa" ? "تغییر تم / قالب داشبورد" : "Dashboard Theme / Template"}
            >
              {themes.map((th) => (
                <option key={th.id} value={th.id} style={{ background: "var(--bi-surface)", color: "var(--bi-text)" }}>
                  {locale === "fa" ? `قالب: ${th.name}` : th.name}
                </option>
              ))}
            </select>
          )}

          <button onClick={() => executePageWidgets(activePageId, false)} className="p-1.5 rounded-lg border hover:opacity-80" style={{ borderColor: "var(--bi-border)" }} title="Live Refresh">
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
          <button onClick={savePersonalState} className="p-1.5 rounded-lg border hover:opacity-80" style={{ borderColor: "var(--bi-border)" }} title={t("saveState")}>
            <BookmarkCheck className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => {
              setAssistantEditWidgetId(null);
              setAssistantMode("auto");
              setAiTab("builder");
              setShowAiDrawer((s) => !s);
            }}
            className="px-2.5 py-1.5 rounded-lg border text-xs font-semibold flex items-center gap-1 text-violet-500"
            style={{ borderColor: "var(--bi-border)" }}
          >
            <Wand2 className="w-3.5 h-3.5" /> {locale === "fa" ? "دستیار فارسی ساخت و ویرایش" : "Persian AI Assistant"}
          </button>
          <button onClick={openVersions} className="p-1.5 rounded-lg border" style={{ borderColor: "var(--bi-border)" }} title={t("versions")}>
            <History className="w-3.5 h-3.5" />
          </button>
          <button onClick={openComments} className="p-1.5 rounded-lg border" style={{ borderColor: "var(--bi-border)" }} title={t("comments")}>
            <MessageSquare className="w-3.5 h-3.5" />
          </button>
          <button onClick={handleShare} className="p-1.5 rounded-lg border" style={{ borderColor: "var(--bi-border)" }} title={t("share")}>
            <Share2 className="w-3.5 h-3.5" />
          </button>

          {dash.permissions.export && (
            <div className="flex items-center border rounded-lg overflow-hidden text-[11px]" style={{ borderColor: "var(--bi-border)" }}>
              <button onClick={() => apiDownload(`/exports/dashboards/${dash.id}/pdf`, `${dash.title}.pdf`)} className="px-2 py-1.5 hover:bg-blue-500/10 font-semibold">PDF</button>
              <button onClick={() => apiDownload(`/exports/dashboards/${dash.id}/xlsx`, `${dash.title}.xlsx`)} className="px-2 py-1.5 border-s hover:bg-blue-500/10 font-semibold" style={{ borderColor: "var(--bi-border)" }}>XLSX</button>
              <button onClick={() => apiDownload(`/exports/dashboards/${dash.id}/csv`, `${dash.title}.csv`)} className="px-2 py-1.5 border-s hover:bg-blue-500/10 font-semibold" style={{ borderColor: "var(--bi-border)" }}>CSV</button>
              <button onClick={handleExportPng} className="px-2 py-1.5 border-s hover:bg-blue-500/10 font-semibold" style={{ borderColor: "var(--bi-border)" }}>PNG</button>
            </div>
          )}
        </div>
      </div>

      {/* Share link banner */}
      {shareInfo && (
        <div className="bi-card p-3 flex items-center justify-between text-xs border-emerald-500">
          <span><b>Public Embed Link:</b> <a href={shareInfo} target="_blank" className="underline text-blue-500">{shareInfo}</a></span>
          <button onClick={() => setShareInfo(null)}><X className="w-4 h-4" /></button>
        </div>
      )}

      {/* Multi-Page Tabs */}
      <div className="flex items-center gap-2 overflow-x-auto pb-1">
        {dash.pages.map((p) => (
          <button
            key={p.id}
            onClick={() => setActivePageId(p.id)}
            className="px-3.5 py-1.5 rounded-lg text-xs font-semibold border transition whitespace-nowrap flex items-center gap-1.5"
            style={{
              background: p.id === activePageId ? "var(--bi-primary)" : "var(--bi-card)",
              color: p.id === activePageId ? "#fff" : "var(--bi-text)",
              borderColor: "var(--bi-border)",
            }}
          >
            <Layers className="w-3.5 h-3.5" />
            {p.title}
            {p.is_drill_through && <span className="text-[10px] opacity-75">(Detail)</span>}
          </button>
        ))}
        {editMode && (
          <button
            onClick={handleAddPage}
            className="px-2.5 py-1.5 rounded-lg border border-dashed text-xs font-semibold flex items-center gap-1"
            style={{ borderColor: "var(--bi-border)" }}
          >
            <Plus className="w-3.5 h-3.5" /> Page
          </button>
        )}
      </div>

      {/* Interactive Analytics Guide Strip */}
      {pageWidgets.some((w) => (w.drill_rule?.hierarchy?.length || 0) > 0 || w.config.interactions?.cross_filter !== false) && (
        <div
          className="px-3.5 py-2 rounded-xl border text-[11px] flex flex-wrap items-center justify-between gap-2"
          style={{
            background: "color-mix(in srgb, var(--bi-primary) 6%, var(--bi-card))",
            borderColor: "color-mix(in srgb, var(--bi-primary) 24%, var(--bi-border))",
          }}
        >
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            {locale === "fa" ? (
              <>
                <span>
                  <strong className="text-blue-500">۱. دریل‌داون:</strong> روی میله‌های نمودار دارای نشانگر{" "}
                  <span className="px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-500 font-semibold">Drill-Down</span> کلیک کنید (مثلاً سال ← فصل ← ماه ← روز).
                </span>
                <span>
                  <strong className="text-emerald-500">۲. فیلتر کل داشبورد:</strong> روی هر بخش از نمودارهای{" "}
                  <span className="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-500 font-semibold">Cross-Filter</span> کلیک کنید تا تمام ویجت‌ها فیلتر شوند.
                </span>
                <span>
                  <strong className="text-violet-500">۳. تبدیل ویجت بعد دریل‌داون:</strong> علاوه بر تبدیل خودکار نمودار در هر سطح، از منوی بالای ویجت آن را به هر ویجت دیگری تبدیل کنید.
                </span>
              </>
            ) : (
              <>
                <span>
                  <strong className="text-blue-500">1. Drill-Down:</strong> Click bars on{" "}
                  <span className="px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-500 font-semibold">Drill-Down</span> widgets (e.g., Year → Quarter → Month → Day).
                </span>
                <span>
                  <strong className="text-emerald-500">2. Dashboard Cross-Filter:</strong> Click any slice/bar on{" "}
                  <span className="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-500 font-semibold">Cross-Filter</span> widgets to filter the entire dashboard.
                </span>
                <span>
                  <strong className="text-violet-500">3. Post-Drill Conversion:</strong> Widgets auto-convert per drill level and can be switched anytime via the header chart selector.
                </span>
              </>
            )}
          </div>
          {(crossFilters.length > 0 || Object.values(drillStates).some((s) => s.length > 0) || Object.keys(widgetChartOverrides).length > 0) && (
            <button
              onClick={() => {
                setFilterValues({});
                setCrossFilters([]);
                setDrillStates({});
                setWidgetChartOverrides({});
              }}
              className="px-2.5 py-1 rounded-lg bg-red-500/10 text-red-500 font-semibold hover:bg-red-500/20 shrink-0"
            >
              {locale === "fa" ? "بازنشانی تعاملات" : "Reset Interactions"}
            </button>
          )}
        </div>
      )}

      {/* Global & Dashboard Filter Bar */}
      <GlobalFilterBar
        dashboardId={dash.id}
        pageId={activePageId}
        filters={dash.filters}
        datasets={datasets}
        filterValues={filterValues}
        onChangeValue={(fid, val) => setFilterValues((prev) => ({ ...prev, [fid]: val }))}
        onResetAll={() => {
          setFilterValues({});
          setCrossFilters([]);
          setDrillStates({});
          setWidgetChartOverrides({});
        }}
        crossFilters={crossFilters}
        onClearCrossFilter={(idx) =>
          setCrossFilters((prev) => (idx === undefined ? [] : prev.filter((_, i) => i !== idx)))
        }
        editMode={editMode}
        onFilterAdded={loadDashboard}
      />

      {/* Main Split: Grid Canvas + Right Properties Panel */}
      <div className="flex flex-col lg:flex-row gap-4 items-start">
        <div className="flex-1 min-w-0 w-full">
          {isMobile ? (
            <div className="grid grid-cols-2 gap-3">
              {sortedMobileWidgets.map((w) => renderWidgetCard(w, true))}
            </div>
          ) : (
            <ResponsiveGridLayout
              className="layout"
              layouts={gridLayouts}
              breakpoints={{ lg: 1200, md: 996, sm: 768, xs: 480 }}
              cols={{
                lg: currentPage?.layout_settings?.columns_count || 12,
                md: currentPage?.layout_settings?.columns_count || 12,
                sm: 6,
                xs: 4,
              }}
              rowHeight={currentPage?.layout_settings?.row_height || 42}
              margin={[currentPage?.layout_settings?.gap_px || 16, currentPage?.layout_settings?.gap_px || 16]}
              isDraggable={editMode}
              isResizable={editMode}
              draggableHandle=".widget-drag-handle"
              onLayoutChange={(layout) => handleLayoutChange(layout)}
            >
              {pageWidgets.map((w) => renderWidgetCard(w, false))}
            </ResponsiveGridLayout>
          )}
        </div>

        {/* Right Properties Panel (Edit Mode + Selected Widget) */}
        {editMode && selectedWidget && (
          <div className="fixed inset-0 z-50 bg-black/50 p-3 flex items-end sm:items-center justify-center lg:static lg:inset-auto lg:bg-transparent lg:p-0 lg:block shrink-0">
            <div className="w-full max-w-md max-h-[88vh] overflow-y-auto lg:max-h-none lg:w-auto">
              <PropertiesPanel
                key={selectedWidget.id}
                widget={selectedWidget}
                datasets={datasets}
                pages={dash.pages}
                onClose={() => setSelectedWidgetId(null)}
                onUpdated={loadDashboard}
                onChartSwitched={(wid, chartType) =>
                  setWidgetChartOverrides((prev) => ({ ...prev, [wid]: chartType }))
                }
              />
            </div>
          </div>
        )}

        {/* AI Assistant Drawer */}
        {showAiDrawer && (
          <div className="fixed inset-0 z-50 bg-black/50 p-3 flex items-end sm:items-center justify-center lg:static lg:inset-auto lg:bg-transparent lg:p-0 lg:block shrink-0">
            <div className="w-full max-w-md lg:w-96 shrink-0 bi-card p-4 space-y-3 max-h-[88vh] lg:h-[calc(100vh-140px)] overflow-y-auto lg:sticky lg:top-4">
              <div className="flex items-center justify-between border-b pb-2" style={{ borderColor: "var(--bi-border)" }}>
                <div className="flex items-center gap-2 font-bold text-sm">
                  <Sparkles className="w-4 h-4 text-violet-500" />
                  {locale === "fa" ? "دستیار هوشمند ساخت و ویرایش" : "AI Dashboard & Widget Copilot"}
                </div>
                <button onClick={() => setShowAiDrawer(false)} aria-label="Close AI Copilot">
                  <X className="w-4 h-4" />
                </button>
              </div>

              {/* Tabs */}
              <div className="grid grid-cols-2 gap-1 p-1 rounded-lg border text-[11px] font-semibold" style={{ borderColor: "var(--bi-border)" }}>
                <button
                  type="button"
                  onClick={() => setAiTab("builder")}
                  className="py-1.5 px-2 rounded-md transition"
                  style={{
                    background: aiTab === "builder" ? "var(--bi-primary)" : "transparent",
                    color: aiTab === "builder" ? "#fff" : "var(--bi-muted)",
                  }}
                >
                  {locale === "fa" ? "ساخت ویجت / KPI متنی" : "Text-to-Widget & KPI"}
                </button>
                <button
                  type="button"
                  onClick={() => setAiTab("nlq")}
                  className="py-1.5 px-2 rounded-md transition"
                  style={{
                    background: aiTab === "nlq" ? "var(--bi-primary)" : "transparent",
                    color: aiTab === "nlq" ? "#fff" : "var(--bi-muted)",
                  }}
                >
                  {locale === "fa" ? "پرسش تحلیلی و پیش‌بینی" : "NLQ & Insights"}
                </button>
              </div>

              {aiTab === "builder" ? (
                <PersianAssistantPanel
                  compact
                  dashboardId={dash.id}
                  pageId={activePageId}
                  initialWidgetId={assistantEditWidgetId}
                  initialMode={assistantMode}
                  currentWidgets={dash.widgets}
                  onApplied={async (res) => {
                    await loadDashboard();
                    if (res?.status === "widget_updated" && res.widget && res.preview_data) {
                      setWidgetData((prev) => ({
                        ...prev,
                        [res.widget.id]: {
                          ...res.preview_data,
                          effective_chart_type: res.widget.chart_type,
                        },
                      }));
                    }
                  }}
                />
              ) : (
                <>
                  <form onSubmit={handleNlq} className="space-y-2">
                    <label className="block text-xs font-semibold">Natural Language Query (EN / فارسی)</label>
                    <input
                      value={nlqQuestion}
                      onChange={(e) => setNlqQuestion(e.target.value)}
                      placeholder="e.g., total revenue by region / مجموع فروش بر اساس منطقه"
                      className="w-full px-3 py-2 rounded-lg border text-xs bg-transparent"
                      style={{ borderColor: "var(--bi-border)" }}
                    />
                    <button type="submit" className="w-full py-2 rounded-lg text-xs font-bold text-white bg-violet-600">
                      Ask AI Data Engine
                    </button>
                  </form>

                  {nlqResult && (
                    <div className="space-y-2 pt-2 border-t text-xs" style={{ borderColor: "var(--bi-border)" }}>
                      <div className="font-mono text-[10px] p-2 rounded bg-slate-900/80 text-emerald-400 overflow-x-auto">
                        {nlqResult.generated_sql}
                      </div>
                      <div className="h-48">
                        <WidgetRenderer
                          chartType={nlqResult.recommended_chart_type}
                          columns={nlqResult.result.columns}
                          rows={nlqResult.result.rows}
                        />
                      </div>
                    </div>
                  )}

                  <div className="pt-2 border-t space-y-2 text-xs" style={{ borderColor: "var(--bi-border)" }}>
                    <div className="font-semibold">Statistical Anomaly Detection & Forecast</div>
                    <div className="space-y-1">
                      {pageWidgets.filter((w) => w.chart_type !== "kpi").slice(0, 4).map((w) => (
                        <button
                          key={w.id}
                          onClick={() => handleRunAnomalyForecast(w.id)}
                          className="w-full text-start px-2.5 py-1.5 rounded border hover:bg-violet-500/10 truncate"
                          style={{ borderColor: "var(--bi-border)" }}
                        >
                          Analyze: {w.title}
                        </button>
                      ))}
                    </div>
                    {aiInsights && (
                      <div className="p-2.5 rounded border space-y-1.5 text-[11px]" style={{ borderColor: "var(--bi-border)" }}>
                        <div className="font-bold">{aiInsights.widgetTitle}</div>
                        <div>Trend: <b>{aiInsights.trend_direction}</b> (slope {aiInsights.trend_slope})</div>
                        <div>Mean: {aiInsights.mean} · StdDev: {aiInsights.std_dev}</div>
                        <div>Anomalies detected: <b>{aiInsights.anomalies?.length || 0}</b></div>
                        {aiInsights.forecast?.length > 0 && (
                          <div>
                            Forecast: {aiInsights.forecast.map((f: any) => `${f.label}: ${f.value}`).join(" | ")}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Add Widget Modal */}
      {showAddWidget && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
          <form onSubmit={handleAddWidgetSubmit} className="bi-card w-full max-w-lg p-6 space-y-4 text-xs">
            <div className="flex items-center justify-between">
              <h3 className="text-base font-bold">Add New Widget</h3>
              <button type="button" onClick={() => setShowAddWidget(false)}><X className="w-4 h-4" /></button>
            </div>
            <div>
              <label className="block font-semibold mb-1">Title</label>
              <input value={newWTitle} onChange={(e) => setNewWTitle(e.target.value)} placeholder="Revenue by Product" className="w-full px-3 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} required />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block font-semibold mb-1">{locale === "fa" ? "نوع نمودار" : "Chart Type (31 Supported)"}</label>
                <select value={newWChart} onChange={(e) => setNewWChart(e.target.value)} className="w-full px-3 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                  {CHART_REGISTRY.map((c) => <option key={c.id} value={c.id}>{locale === "fa" ? `${c.labelFa} (${c.label})` : c.label}</option>)}
                </select>
              </div>
              <div>
                <label className="block font-semibold mb-1">{locale === "fa" ? "منبع داده" : "Dataset"}</label>
                <select value={newWDs} onChange={(e) => setNewWDs(e.target.value)} className="w-full px-3 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                  {datasets.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                </select>
              </div>
            </div>
            <div className="grid grid-cols-3 gap-2">
              <div>
                <label className="block font-semibold mb-1">
                  {newWChart === "bpmn" ? (locale === "fa" ? "ستون گام فرآیند" : "BPMN Step Field") : "X Dimension"}
                </label>
                <select value={newWX} onChange={(e) => setNewWX(e.target.value)} className="w-full px-2 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                  <option value="">None</option>
                  {(addDs?.fields || []).map((f) => <option key={f.field_name} value={f.field_name}>{f.display_name || f.field_name}</option>)}
                </select>
              </div>
              <div>
                <label className="block font-semibold mb-1">
                  {newWChart === "bpmn" ? (locale === "fa" ? "ستون زمان انتظار" : "Wait Time Field") : "Y Measure"}
                </label>
                <select value={newWY} onChange={(e) => setNewWY(e.target.value)} className="w-full px-2 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                  <option value="*">* (Count)</option>
                  {(addDs?.fields || []).map((f) => <option key={f.field_name} value={f.field_name}>{f.display_name || f.field_name}</option>)}
                </select>
              </div>
              <div>
                <label className="block font-semibold mb-1">Aggregation</label>
                <select value={newWAgg} onChange={(e) => setNewWAgg(e.target.value)} className="w-full px-2 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                  {["SUM", "AVG", "COUNT", "COUNT_DISTINCT", "MIN", "MAX", "MEDIAN"].map((a) => <option key={a} value={a}>{a}</option>)}
                </select>
              </div>
            </div>
            {newWChart === "bpmn" && (
              <div className="p-3 rounded-lg border space-y-2.5 bg-blue-500/5" style={{ borderColor: "var(--bi-border)" }}>
                <div className="font-bold text-[11px] flex items-center justify-between">
                  <span>{locale === "fa" ? "تنظیمات مدل BPMN، کارتابل و نقشه حرارتی" : "BPMN Model, Cartable & Heatmap Config"}</span>
                  <label className="flex items-center gap-1.5 font-semibold cursor-pointer">
                    <input type="checkbox" checked={newWBpmnHeatmap} onChange={(e) => setNewWBpmnHeatmap(e.target.checked)} />
                    <span>{locale === "fa" ? "نقشه حرارتی" : "Wait Heatmap"}</span>
                  </label>
                </div>
                <div className="grid grid-cols-3 gap-2">
                  <div>
                    <label className="block text-[10px] font-semibold mb-0.5">{locale === "fa" ? "ستون فرد کارتابل" : "Assignee Field"}</label>
                    <select value={newWBpmnAssignee} onChange={(e) => setNewWBpmnAssignee(e.target.value)} className="w-full px-2 py-1.5 rounded border bg-transparent text-[11px]" style={{ borderColor: "var(--bi-border)" }}>
                      <option value="">Auto</option>
                      {(addDs?.fields || []).map((f) => <option key={f.field_name} value={f.field_name}>{f.display_name || f.field_name}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="block text-[10px] font-semibold mb-0.5">{locale === "fa" ? "ستون کد درخواست" : "Request ID"}</label>
                    <select value={newWBpmnReqId} onChange={(e) => setNewWBpmnReqId(e.target.value)} className="w-full px-2 py-1.5 rounded border bg-transparent text-[11px]" style={{ borderColor: "var(--bi-border)" }}>
                      <option value="">Auto</option>
                      {(addDs?.fields || []).map((f) => <option key={f.field_name} value={f.field_name}>{f.display_name || f.field_name}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="block text-[10px] font-semibold mb-0.5">{locale === "fa" ? "ستون وضعیت" : "Status Field"}</label>
                    <select value={newWBpmnStatus} onChange={(e) => setNewWBpmnStatus(e.target.value)} className="w-full px-2 py-1.5 rounded border bg-transparent text-[11px]" style={{ borderColor: "var(--bi-border)" }}>
                      <option value="">Auto</option>
                      {(addDs?.fields || []).map((f) => <option key={f.field_name} value={f.field_name}>{f.display_name || f.field_name}</option>)}
                    </select>
                  </div>
                </div>
                <div>
                  <label className="block text-[10px] font-semibold mb-1">
                    {locale === "fa" ? "آپلود فایل استاندارد BPMN (.bpmn / .xml) — اختیاری (در غیر این صورت خودکار از گام‌های داده ساخته می‌شود)" : "Upload BPMN 2.0 XML (.bpmn / .xml) — optional (auto-generated from data if omitted)"}
                  </label>
                  <input
                    type="file"
                    accept=".bpmn,.xml"
                    onChange={async (ev) => {
                      const f = ev.target.files?.[0];
                      if (!f) return;
                      const xml = await f.text();
                      const parsed = parseBpmnXml(xml);
                      setNewWBpmnXml(xml);
                      setNewWBpmnGraph(parsed);
                      if (!newWTitle) setNewWTitle(parsed.processName);
                    }}
                    className="text-[11px]"
                  />
                  {newWBpmnGraph && (
                    <div className="mt-1 text-[10px] text-emerald-500 font-semibold">
                      ✅ {newWBpmnGraph.processName}: {newWBpmnGraph.nodes.length} nodes, {newWBpmnGraph.edges.length} flows
                    </div>
                  )}
                </div>
              </div>
            )}
            <div className="flex justify-end gap-2 pt-2">
              <button type="button" onClick={() => setShowAddWidget(false)} className="px-4 py-2 rounded-lg border" style={{ borderColor: "var(--bi-border)" }}>Cancel</button>
              <button type="submit" className="px-4 py-2 rounded-lg text-white font-semibold" style={{ background: "var(--bi-primary)" }}>Create Widget</button>
            </div>
          </form>
        </div>
      )}

      {/* Versions Modal */}
      {showVersions && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
          <div className="bi-card w-full max-w-md p-5 space-y-3 text-xs">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-bold">Dashboard Versions</h3>
              <button onClick={() => setShowVersions(false)}><X className="w-4 h-4" /></button>
            </div>
            <button onClick={saveVersionSnapshot} className="w-full py-2 rounded-lg text-white font-semibold" style={{ background: "var(--bi-primary)" }}>
              Save Snapshot Now
            </button>
            <div className="space-y-1.5 max-h-64 overflow-y-auto">
              {versionsList.map((v) => (
                <div key={v.id} className="p-2 rounded border flex justify-between" style={{ borderColor: "var(--bi-border)" }}>
                  <span><b>v{v.version_number}</b> — {v.label}</span>
                  <span className="opacity-60">{new Date(v.created_at).toLocaleString()}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Comments Modal */}
      {showComments && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
          <div className="bi-card w-full max-w-md p-5 space-y-3 text-xs">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-bold">Dashboard Comments</h3>
              <button onClick={() => setShowComments(false)}><X className="w-4 h-4" /></button>
            </div>
            <form onSubmit={submitComment} className="flex gap-2">
              <input value={newComment} onChange={(e) => setNewComment(e.target.value)} placeholder="Write annotation..." className="flex-1 px-3 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
              <button type="submit" className="px-3 py-1.5 rounded text-white font-semibold" style={{ background: "var(--bi-primary)" }}>Post</button>
            </form>
            <div className="space-y-2 max-h-64 overflow-y-auto">
              {commentsList.map((c) => (
                <div key={c.id} className="p-2.5 rounded border" style={{ borderColor: "var(--bi-border)" }}>
                  <div className="font-bold">{c.author_name}</div>
                  <div className="mt-0.5">{c.content}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
