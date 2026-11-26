"use client";

import React, { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  Clock,
  Database,
  Download,
  Filter,
  Flame,
  GitBranch,
  Inbox,
  Loader2,
  Maximize2,
  Move,
  RotateCcw,
  Save,
  Search,
  SlidersHorizontal,
  Sparkles,
  UserCheck,
  Users,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { apiFetch, getCsrfCookie, getStoredToken } from "@/lib/api";
import {
  BpmnAssigneeSummary,
  BpmnCartableItem,
  BpmnNode,
  BpmnStepMetrics,
  BpmnWidgetConfig,
  computeBpmnAnalytics,
} from "@/lib/bpmnParser";
import { useBi } from "@/lib/context";
import type { BpmnAnalyticsPayload } from "@/types/bi";

interface Props {
  columns: string[];
  rows: Record<string, any>[];
  styleConfig?: Record<string, any>;
  vizConfig?: Record<string, any>;
  visualSpec?: Record<string, any>;
  onPointClick?: (field: string, value: any) => void;
  widgetId?: string;
  datasetId?: string;
  publicShareToken?: string;
  activeFilters?: any[];
  crossFilters?: any[];
  bpmnAnalytics?: BpmnAnalyticsPayload;
  onProcessSelect?: (processName: string) => void;
  onFieldMappingOverride?: (overrides: Record<string, string | null>) => void;
}

interface ServerDrillPayload {
  step_name: string;
  process_name: string | null;
  available_columns: string[];
  step_summary: Record<string, any>;
  timeline?: {
    period: string;
    total_count: number;
    pending_count: number;
    avg_wait_hours: number;
    overdue_count: number;
  }[];
  assignees: {
    assigneeName: string;
    assigneeRole: string;
    pendingCount: number;
    totalCount: number;
    avgWaitHours: number;
    maxWaitHours: number;
    overdueCount: number;
  }[];
  items: BpmnCartableItem[];
  total_items: number;
  limit: number;
  offset: number;
}

function getHeatPalette(ratio: number, isDark: boolean): {
  stroke: string;
  fill: string;
  glow: string;
  badgeBg: string;
  badgeText: string;
} {
  if (ratio >= 0.78) {
    return {
      stroke: "#ef4444",
      fill: isDark ? "rgba(239, 68, 68, 0.24)" : "rgba(254, 226, 226, 0.92)",
      glow: "rgba(239, 68, 68, 0.52)",
      badgeBg: "rgba(239, 68, 68, 0.18)",
      badgeText: isDark ? "#fca5a5" : "#b91c1c",
    };
  }
  if (ratio >= 0.5) {
    return {
      stroke: "#f97316",
      fill: isDark ? "rgba(249, 115, 22, 0.20)" : "rgba(255, 237, 213, 0.92)",
      glow: "rgba(249, 115, 22, 0.42)",
      badgeBg: "rgba(249, 115, 22, 0.18)",
      badgeText: isDark ? "#fdba74" : "#c2410c",
    };
  }
  if (ratio >= 0.25) {
    return {
      stroke: "#f59e0b",
      fill: isDark ? "rgba(245, 158, 11, 0.18)" : "rgba(254, 243, 199, 0.92)",
      glow: "rgba(245, 158, 11, 0.32)",
      badgeBg: "rgba(245, 158, 11, 0.18)",
      badgeText: isDark ? "#fcd34d" : "#b45309",
    };
  }
  return {
    stroke: "#10b981",
    fill: isDark ? "rgba(16, 185, 129, 0.15)" : "rgba(209, 250, 229, 0.88)",
    glow: "rgba(16, 185, 129, 0.24)",
    badgeBg: "rgba(16, 185, 129, 0.16)",
    badgeText: isDark ? "#6ee7b7" : "#047857",
  };
}

function formatWaitDuration(hours: number, isFa: boolean): string {
  const h = Number(hours) || 0;
  if (h >= 24) {
    const days = Math.floor(h / 24);
    const rem = Math.round((h % 24) * 10) / 10;
    if (isFa) {
      return rem > 0
        ? `${h.toLocaleString("fa-IR")} ساعت (${days} روز و ${rem} س)`
        : `${h.toLocaleString("fa-IR")} ساعت (${days} روز)`;
    }
    return `${h.toFixed(1)}h (${days}d ${rem}h)`;
  }
  return isFa ? `${h.toLocaleString("fa-IR")} ساعت` : `${h.toFixed(1)}h`;
}

function getEdgePath(src: BpmnNode, dst: BpmnNode): { path: string; midX: number; midY: number } {
  const sx = src.x + src.width;
  const sy = src.y + src.height / 2;
  const tx = dst.x;
  const ty = dst.y + dst.height / 2;

  if (tx < src.x + 10) {
    const sBotX = src.x + src.width / 2;
    const sBotY = src.y + src.height;
    const tBotX = dst.x + dst.width / 2;
    const tBotY = dst.y + dst.height;
    const loopY = Math.max(sBotY, tBotY) + 46;
    return {
      path: `M ${sBotX} ${sBotY} C ${sBotX} ${loopY}, ${tBotX} ${loopY}, ${tBotX} ${tBotY}`,
      midX: (sBotX + tBotX) / 2,
      midY: loopY - 8,
    };
  }

  if (Math.abs(ty - sy) > 70 && Math.abs(tx - sx) < 90) {
    const sBotX = src.x + src.width / 2;
    const sBotY = src.y + src.height;
    const tTopX = dst.x + dst.width / 2;
    const tTopY = dst.y;
    const midY = (sBotY + tTopY) / 2;
    return {
      path: `M ${sBotX} ${sBotY} C ${sBotX} ${midY}, ${tTopX} ${midY}, ${tTopX} ${tTopY}`,
      midX: (sBotX + tTopX) / 2,
      midY,
    };
  }

  const dx = Math.max(40, Math.abs(tx - sx) * 0.48);
  return {
    path: `M ${sx} ${sy} C ${sx + dx} ${sy}, ${tx - dx} ${ty}, ${tx} ${ty}`,
    midX: (sx + tx) / 2,
    midY: (sy + ty) / 2 - 8,
  };
}

export default function BpmnProcessViewer({
  columns,
  rows,
  styleConfig = {},
  vizConfig = {},
  visualSpec = {},
  onPointClick,
  widgetId,
  datasetId,
  publicShareToken,
  activeFilters = [],
  crossFilters = [],
  bpmnAnalytics,
  onProcessSelect,
  onFieldMappingOverride,
}: Props) {
  const { locale, activeThemeMode, activeThemeTokens, showToast } = useBi();
  const isFa = locale === "fa";
  const isDark = activeThemeMode === "dark";

  const [localFieldOverrides, setLocalFieldOverrides] = useState<Record<string, string | null>>({});
  const [showMapper, setShowMapper] = useState<boolean>(false);
  const [showFormulaPanel, setShowFormulaPanel] = useState<boolean>(false);
  const [dragMode, setDragMode] = useState<boolean>(false);
  const [customPositions, setCustomPositions] = useState<Record<string, { x: number; y: number }>>({});
  const [layoutDirty, setLayoutDirty] = useState<boolean>(false);
  const [savingLayout, setSavingLayout] = useState<boolean>(false);
  const [exportingDrill, setExportingDrill] = useState<boolean>(false);

  // Bottleneck formula editor state
  const [ruleName, setRuleName] = useState<string>("فرمول سفارشی گلوگاه");
  const [ruleThreshold, setRuleThreshold] = useState<number>(60);
  const [ruleWeights, setRuleWeights] = useState<Record<string, number>>({
    avg_wait: 0.3,
    max_wait: 0.15,
    pending_count: 0.2,
    overdue_ratio: 0.2,
    rework_rate: 0.1,
    volume_share: 0.05,
  });
  const [savingRule, setSavingRule] = useState<boolean>(false);

  const dragStateRef = React.useRef<{
    nodeId: string;
    startClientX: number;
    startClientY: number;
    origX: number;
    origY: number;
    moved: boolean;
  } | null>(null);
  const suppressClickRef = React.useRef<boolean>(false);
  const svgRef = React.useRef<SVGSVGElement | null>(null);

  const bpmnConfig: BpmnWidgetConfig = useMemo(() => {
    const base = {
      ...(visualSpec?.bpmn_config || {}),
      ...(vizConfig?.bpmn_config || {}),
      ...(styleConfig?.bpmn_config || {}),
    };
    for (const [k, v] of Object.entries(localFieldOverrides)) {
      if (v) (base as any)[k] = v;
    }
    return base;
  }, [visualSpec, vizConfig, styleConfig, localFieldOverrides]);

  const [heatmapEnabled, setHeatmapEnabled] = useState<boolean>(
    bpmnConfig.heatmap_enabled ?? vizConfig.heatmap_enabled ?? true
  );
  const [heatmapMetric, setHeatmapMetric] = useState<"avg_wait" | "max_wait" | "pending_count">(
    bpmnConfig.heatmap_metric || "avg_wait"
  );
  const [zoom, setZoom] = useState<number>(1);
  const [hoveredStepId, setHoveredStepId] = useState<string | null>(null);
  const [selectedStepKey, setSelectedStepKey] = useState<string | null>(null);
  const [selectedAssignee, setSelectedAssignee] = useState<string | null>(null);
  const [showAllStatusesInDrill, setShowAllStatusesInDrill] = useState<boolean>(false);
  const [drillSearch, setDrillSearch] = useState<string>("");
  const [drillOffset, setDrillOffset] = useState<number>(0);

  const [serverDrill, setServerDrill] = useState<ServerDrillPayload | null>(null);
  const [drillLoading, setDrillLoading] = useState<boolean>(false);

  const analytics = useMemo(
    () => computeBpmnAnalytics(rows || [], columns || [], bpmnConfig, heatmapMetric, bpmnAnalytics),
    [rows, columns, bpmnConfig, heatmapMetric, bpmnAnalytics]
  );

  const { graph: rawGraph, stepMap, minMetricValue, maxMetricValue, totalPendingRequests, overallAvgWaitHours, bottleneckStep, mapping } =
    analytics;

  // Hydrate saved drag-and-drop coordinates & active rule from backend payload
  useEffect(() => {
    if (bpmnAnalytics?.saved_layout?.node_positions) {
      setCustomPositions(bpmnAnalytics.saved_layout.node_positions);
      setLayoutDirty(false);
    } else {
      setCustomPositions({});
      setLayoutDirty(false);
    }
    if (bpmnAnalytics?.active_rule) {
      setRuleName(bpmnAnalytics.active_rule.name || "فرمول سفارشی گلوگاه");
      setRuleThreshold(Number(bpmnAnalytics.active_rule.threshold_score || 60));
      const w = bpmnAnalytics.active_rule.formula_json?.weights;
      if (w && typeof w === "object") {
        setRuleWeights((prev) => ({ ...prev, ...w }));
      }
    }
  }, [bpmnAnalytics?.saved_layout, bpmnAnalytics?.active_rule, bpmnAnalytics?.active_process]);

  const graph = useMemo(() => {
    if (!customPositions || Object.keys(customPositions).length === 0) return rawGraph;
    return {
      ...rawGraph,
      nodes: rawGraph.nodes.map((n) => {
        const p = customPositions[n.id] || customPositions[n.stepKey];
        return p ? { ...n, x: p.x, y: p.y } : n;
      }),
    };
  }, [rawGraph, customPositions]);

  const handleSaveLayout = async (reset: boolean = false) => {
    if (!datasetId) return;
    setSavingLayout(true);
    try {
      await apiFetch("/process-intelligence/bpmn-layout", {
        method: "PUT",
        body: JSON.stringify({
          dataset_id: datasetId,
          widget_id: widgetId || null,
          process_name: bpmnAnalytics?.active_process || null,
          node_positions: reset ? {} : customPositions,
          zoom,
          reset,
        }),
      });
      if (reset) setCustomPositions({});
      setLayoutDirty(false);
      showToast(reset ? (isFa ? "چیدمان به حالت خودکار بازنشانی شد" : "Layout reset") : (isFa ? "چیدمان BPMN ذخیره شد" : "BPMN layout saved"));
    } catch (err: any) {
      showToast(err.message || "Error saving layout");
    } finally {
      setSavingLayout(false);
    }
  };

  const handleSaveBottleneckRule = async () => {
    if (!datasetId) return;
    setSavingRule(true);
    try {
      await apiFetch(`/process-intelligence/datasets/${datasetId}/bottleneck-rules`, {
        method: "POST",
        body: JSON.stringify({
          name: ruleName,
          process_name: bpmnAnalytics?.active_process || null,
          weights: ruleWeights,
          threshold_score: ruleThreshold,
          severity: "high",
          is_active: true,
        }),
      });
      showToast(isFa ? "فرمول تشخیص گلوگاه ذخیره شد و در تحلیل بعدی اعمال می‌شود" : "Bottleneck formula saved");
      setShowFormulaPanel(false);
      if (onProcessSelect && bpmnAnalytics?.active_process) {
        onProcessSelect(bpmnAnalytics.active_process);
      } else if (onProcessSelect) {
        onProcessSelect("__ALL__");
      }
    } catch (err: any) {
      showToast(err.message || "Error saving rule");
    } finally {
      setSavingRule(false);
    }
  };

  const handleExportDrillExcel = async () => {
    if (!widgetId || !activeStepMetrics) return;
    setExportingDrill(true);
    try {
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      const tok = getStoredToken();
      if (tok) headers["Authorization"] = `Bearer ${tok}`;
      const csrf = getCsrfCookie();
      if (csrf) headers["X-CSRF-Token"] = csrf;
      const cleanOverrides: Record<string, string> = {};
      for (const [k, v] of Object.entries(localFieldOverrides)) {
        if (v) cleanOverrides[k] = v;
      }
      const res = await fetch(`/api/v1/dashboards/widgets/${widgetId}/bpmn-drill/export`, {
        method: "POST",
        credentials: "include",
        headers,
        body: JSON.stringify({
          step_name: activeStepMetrics.stepKey,
          process_name: bpmnAnalytics?.active_process || null,
          assignee_name: selectedAssignee,
          pending_only: !showAllStatusesInDrill && (activeStepMetrics.pendingCount ?? 0) > 0,
          search: drillSearch.trim() || null,
          limit: 500,
          offset: 0,
          active_filters: activeFilters,
          cross_filters: crossFilters,
          field_overrides: Object.keys(cleanOverrides).length > 0 ? cleanOverrides : null,
        }),
      });
      if (!res.ok) throw new Error(`Export failed (${res.status})`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `bpmn_cartable_${activeStepMetrics.stepKey}.xlsx`;
      a.click();
      URL.revokeObjectURL(url);
      showToast(isFa ? "فایل اکسل کارتابل دانلود شد" : "Excel exported");
    } catch (err: any) {
      showToast(err.message || "Export failed");
    } finally {
      setExportingDrill(false);
    }
  };

  const availableCols = useMemo(
    () => (bpmnAnalytics?.available_columns?.length ? bpmnAnalytics.available_columns : columns || []),
    [bpmnAnalytics?.available_columns, columns]
  );

  // Fetch live SQL cartable drill-down when widgetId is available
  useEffect(() => {
    if (!selectedStepKey || !widgetId) {
      setServerDrill(null);
      return;
    }
    const stepMeta = stepMap[selectedStepKey];
    const targetStepName = stepMeta?.stepKey || selectedStepKey;
    const cleanOverrides: Record<string, string> = {};
    for (const [k, v] of Object.entries(localFieldOverrides)) {
      if (v) cleanOverrides[k] = v;
    }
    let cancelled = false;
    setDrillLoading(true);
    const endpoint = publicShareToken
      ? `/api/v1/dashboards/public/${publicShareToken}/widgets/${widgetId}/bpmn-drill`
      : `/api/v1/dashboards/widgets/${widgetId}/bpmn-drill`;

    apiFetch<ServerDrillPayload>(endpoint, {
      method: "POST",
      body: JSON.stringify({
        step_name: targetStepName,
        process_name: bpmnAnalytics?.active_process || null,
        assignee_name: selectedAssignee,
        pending_only: !showAllStatusesInDrill && (stepMeta?.pendingCount ?? 0) > 0,
        search: drillSearch.trim() || null,
        limit: 80,
        offset: drillOffset,
        active_filters: activeFilters,
        cross_filters: crossFilters,
        field_overrides: Object.keys(cleanOverrides).length > 0 ? cleanOverrides : null,
      }),
    })
      .then((res) => {
        if (!cancelled) setServerDrill(res);
      })
      .catch(() => {
        if (!cancelled) setServerDrill(null);
      })
      .finally(() => {
        if (!cancelled) setDrillLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [
    selectedStepKey,
    widgetId,
    publicShareToken,
    bpmnAnalytics?.active_process,
    selectedAssignee,
    showAllStatusesInDrill,
    drillSearch,
    drillOffset,
    activeFilters,
    crossFilters,
    localFieldOverrides,
  ]);

  const bounds = useMemo(() => {
    if (graph.nodes.length === 0) return { minX: 0, minY: 0, width: 900, height: 340 };
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    for (const n of graph.nodes) {
      minX = Math.min(minX, n.x);
      minY = Math.min(minY, n.y);
      maxX = Math.max(maxX, n.x + n.width);
      maxY = Math.max(maxY, n.y + n.height);
    }
    return {
      minX: Math.max(-40, minX - 36),
      minY: Math.max(-30, minY - 36),
      width: Math.max(540, maxX - minX + 90),
      height: Math.max(280, maxY - minY + 110),
    };
  }, [graph.nodes]);

  const activeStepMetrics: BpmnStepMetrics | null = selectedStepKey ? stepMap[selectedStepKey] || null : null;
  const hoveredStepMetrics: BpmnStepMetrics | null = hoveredStepId ? stepMap[hoveredStepId] || null : null;

  const hasAnyData = (rows && rows.length > 0) || (bpmnAnalytics?.step_summary && bpmnAnalytics.step_summary.length > 0);
  if (!hasAnyData) {
    return (
      <div
        className="h-full flex flex-col items-center justify-center text-xs p-4 text-center"
        style={{ color: activeThemeTokens.muted_text }}
      >
        <Inbox className="w-7 h-7 mb-2 opacity-50" />
        <p className="font-semibold">
          {isFa ? "هیچ ردیف فرآیندی با فیلترهای فعلی یافت نشد." : "No process events matched the active filters."}
        </p>
      </div>
    );
  }

  const renderFieldMapperPanel = () => {
    if (!showMapper) return null;
    const fields: { key: keyof BpmnWidgetConfig; labelFa: string; labelEn: string }[] = [
      { key: "process_field", labelFa: "ستون نام فرآیند", labelEn: "Process Name Col" },
      { key: "step_field", labelFa: "ستون گام / مرحله", labelEn: "Step / Activity Col" },
      { key: "assignee_field", labelFa: "ستون فرد / کارتابل", labelEn: "Assignee / Cartable Col" },
      { key: "wait_field", labelFa: "ستون زمان انتظار", labelEn: "Wait Hours Col" },
      { key: "status_field", labelFa: "ستون وضعیت", labelEn: "Status Col" },
    ];
    return (
      <div
        className="p-2.5 rounded-lg border grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-2 shrink-0"
        style={{ borderColor: activeThemeTokens.border, backgroundColor: activeThemeTokens.surface }}
      >
        {fields.map((f) => {
          const val = (localFieldOverrides[f.key] ?? (mapping as any)[f.key] ?? "") as string;
          return (
            <label key={f.key} className="block space-y-1 text-[10px]">
              <span className="font-semibold block" style={{ color: activeThemeTokens.muted_text }}>
                {isFa ? f.labelFa : f.labelEn}
              </span>
              <select
                value={val}
                onChange={(e) => {
                  const nextVal = e.target.value || null;
                  const next = { ...localFieldOverrides, [f.key]: nextVal };
                  setLocalFieldOverrides(next);
                  if (onFieldMappingOverride) onFieldMappingOverride(next);
                }}
                className="w-full px-2 py-1 rounded border text-[11px]"
                style={{
                  background: activeThemeTokens.card_bg,
                  color: activeThemeTokens.text,
                  borderColor: activeThemeTokens.border,
                }}
              >
                <option value="">{isFa ? "— خودکار / ندارد —" : "— Auto / None —"}</option>
                {availableCols.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </label>
          );
        })}
      </div>
    );
  };

  // ---------------- DRILL-DOWN VIEW FOR SELECTED BPMN STEP ----------------
  if (activeStepMetrics) {
    const serverAssignees: BpmnAssigneeSummary[] =
      serverDrill?.assignees?.map((sa) => ({
        assigneeName: sa.assigneeName,
        assigneeRole: sa.assigneeRole || "",
        pendingCount: sa.pendingCount,
        totalCount: sa.totalCount,
        avgWaitHours: sa.avgWaitHours,
        maxWaitHours: sa.maxWaitHours,
        overdueCount: sa.overdueCount,
        items: [],
      })) || [];

    const effectiveAssignees =
      serverAssignees.length > 0 ? serverAssignees : activeStepMetrics.assignees;

    const baseList = showAllStatusesInDrill
      ? activeStepMetrics.items
      : activeStepMetrics.pendingItems.length > 0
      ? activeStepMetrics.pendingItems
      : activeStepMetrics.items;
    const qLow = drillSearch.trim().toLowerCase();
    const localFilteredList = baseList.filter((it) => {
      if (selectedAssignee && it.assigneeName !== selectedAssignee) return false;
      if (!qLow) return true;
      return (
        it.requestId.toLowerCase().includes(qLow) ||
        it.requestTitle.toLowerCase().includes(qLow) ||
        it.assigneeName.toLowerCase().includes(qLow) ||
        it.department.toLowerCase().includes(qLow)
      );
    });

    const displayedItems = serverDrill ? serverDrill.items : localFilteredList;
    const totalDisplayedCount = serverDrill ? serverDrill.total_items : localFilteredList.length;

    const effSummary = {
      pendingCount: serverDrill?.step_summary?.pending_count ?? activeStepMetrics.pendingCount,
      completedCount: serverDrill?.step_summary?.completed_count ?? activeStepMetrics.completedCount,
      totalCount: serverDrill?.step_summary?.total_count ?? activeStepMetrics.totalCount,
      avgWaitHours: serverDrill?.step_summary?.avg_wait_hours ?? activeStepMetrics.avgWaitHours,
      maxWaitHours: serverDrill?.step_summary?.max_wait_hours ?? activeStepMetrics.maxWaitHours,
      slaHours: serverDrill?.step_summary?.sla_hours ?? activeStepMetrics.slaHours,
      overdueCount: serverDrill?.step_summary?.overdue_count ?? activeStepMetrics.overdueCount,
    };

    return (
      <div dir={isFa ? "rtl" : "ltr"} className="h-full flex flex-col gap-2.5 overflow-hidden text-xs">
        {/* Drill-Down Breadcrumb Header */}
        <div
          className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 rounded-lg border shrink-0"
          style={{
            borderColor: activeThemeTokens.border,
            backgroundColor: activeThemeTokens.surface,
          }}
        >
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => {
                setSelectedStepKey(null);
                setSelectedAssignee(null);
                setDrillSearch("");
                setDrillOffset(0);
                setServerDrill(null);
              }}
              className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-[11px] font-bold text-white"
              style={{ backgroundColor: activeThemeTokens.primary }}
            >
              <ArrowRight className={`w-3.5 h-3.5 ${isFa ? "" : "rotate-180"}`} />
              {isFa ? "بازگشت به نمودار BPMN" : "Back to BPMN Map"}
            </button>
            <div className="flex items-center gap-1.5 font-semibold">
              <span className="opacity-65">{graph.processName}</span>
              <span className="opacity-50">/</span>
              <span className="font-bold" style={{ color: activeThemeTokens.primary }}>
                {activeStepMetrics.stepLabel}
              </span>
              {activeStepMetrics.isBottleneck && (
                <span className="px-1.5 py-0.5 rounded bg-red-500/15 text-red-500 text-[10px] font-bold">
                  {isFa ? "گلوگاه فرآیند" : "Bottleneck"}
                </span>
              )}
              {drillLoading && <Loader2 className="w-3.5 h-3.5 animate-spin text-blue-500" />}
            </div>
          </div>

          <div className="flex items-center gap-1.5">
            {widgetId && !publicShareToken && (
              <button
                type="button"
                disabled={exportingDrill}
                onClick={handleExportDrillExcel}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md border text-[11px] font-semibold text-emerald-500 border-emerald-500/40 hover:bg-emerald-500/10 disabled:opacity-50"
              >
                <Download className="w-3 h-3" />
                {exportingDrill ? "..." : isFa ? "خروجی اکسل" : "Excel"}
              </button>
            )}
            {availableCols.length > 0 && (
              <button
                type="button"
                onClick={() => setShowMapper((s) => !s)}
                className="inline-flex items-center gap-1 px-2 py-1 rounded-md border text-[11px] font-semibold hover:bg-blue-500/10"
                style={{ borderColor: activeThemeTokens.border }}
              >
                <SlidersHorizontal className="w-3 h-3" />
                {isFa ? "تنظیم ستون کارتابل" : "Columns"}
              </button>
            )}
            {onPointClick && (
              <button
                type="button"
                onClick={() => onPointClick(mapping.step_field, activeStepMetrics.stepKey)}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md border text-[11px] font-semibold hover:bg-blue-500/10"
                style={{ borderColor: activeThemeTokens.border }}
                title={isFa ? "اعمال این گام به سایر نمودارهای داشبورد" : "Cross-filter other dashboard widgets by this step"}
              >
                <Filter className="w-3 h-3" />
                {isFa ? "فیلتر سایر ویجت‌ها" : "Cross-Filter Dashboard"}
              </button>
            )}
          </div>
        </div>

        {renderFieldMapperPanel()}

        {/* Summary KPI Strip for Selected Step */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 shrink-0">
          <div className="p-2 rounded-lg border" style={{ borderColor: activeThemeTokens.border, backgroundColor: activeThemeTokens.surface }}>
            <div className="text-[10px]" style={{ color: activeThemeTokens.muted_text }}>
              {isFa ? "درخواست مانده در کارتابل" : "Pending in Cartable"}
            </div>
            <div className="text-sm font-bold mt-0.5">
              {effSummary.pendingCount.toLocaleString(isFa ? "fa-IR" : "en-US")}{" "}
              <span className="text-[10px] opacity-65">({isFa ? `کل: ${effSummary.totalCount.toLocaleString("fa-IR")}` : `Total: ${effSummary.totalCount}`})</span>
            </div>
          </div>
          <div className="p-2 rounded-lg border" style={{ borderColor: activeThemeTokens.border, backgroundColor: activeThemeTokens.surface }}>
            <div className="text-[10px]" style={{ color: activeThemeTokens.muted_text }}>
              {isFa ? "میانگین / میانه انتظار" : "Avg / Median Wait"}
            </div>
            <div className="text-sm font-bold mt-0.5 text-amber-500">
              {effSummary.avgWaitHours} {isFa ? "ساعت" : "hrs"}
              {serverDrill?.step_summary?.median_wait_hours !== undefined && (
                <span className="text-[10px] opacity-70 ms-1">
                  ({isFa ? "میانه:" : "Med:"} {serverDrill.step_summary.median_wait_hours})
                </span>
              )}
            </div>
          </div>
          <div className="p-2 rounded-lg border" style={{ borderColor: activeThemeTokens.border, backgroundColor: activeThemeTokens.surface }}>
            <div className="text-[10px]" style={{ color: activeThemeTokens.muted_text }}>
              {isFa ? "بیشترین / P95 انتظار" : "Max / P95 Wait"}
            </div>
            <div className="text-sm font-bold mt-0.5 text-red-500">
              {effSummary.maxWaitHours} {isFa ? "ساعت" : "hrs"}
              {serverDrill?.step_summary?.p95_wait_hours !== undefined && (
                <span className="text-[10px] opacity-70 ms-1">
                  (P95: {serverDrill.step_summary.p95_wait_hours})
                </span>
              )}
            </div>
          </div>
          <div className="p-2 rounded-lg border" style={{ borderColor: activeThemeTokens.border, backgroundColor: activeThemeTokens.surface }}>
            <div className="text-[10px]" style={{ color: activeThemeTokens.muted_text }}>
              {isFa ? "عبور از سقف SLA" : "SLA Overdue"}
            </div>
            <div className="text-sm font-bold mt-0.5">
              {effSummary.overdueCount.toLocaleString(isFa ? "fa-IR" : "en-US")}{" "}
              <span className="text-[10px] opacity-65">
                (SLA: {effSummary.slaHours}h{serverDrill?.step_summary?.sla_breach_pct !== undefined ? ` · ${serverDrill.step_summary.sla_breach_pct}%` : ""})
              </span>
            </div>
          </div>
        </div>

        {/* Step Timeline Strip */}
        {serverDrill?.timeline && serverDrill.timeline.length > 0 && (
          <div className="p-2 rounded-lg border shrink-0 space-y-1" style={{ borderColor: activeThemeTokens.border, backgroundColor: activeThemeTokens.surface }}>
            <div className="text-[10px] font-bold flex items-center justify-between" style={{ color: activeThemeTokens.muted_text }}>
              <span>{isFa ? "خط زمانی و روند ماهانه ورود درخواست به این گام" : "Step Timeline"}</span>
              <span>{serverDrill.timeline.length} {isFa ? "دوره" : "periods"}</span>
            </div>
            <div className="flex items-end gap-1.5 h-12 overflow-x-auto pb-0.5">
              {(() => {
                const maxCnt = Math.max(...serverDrill.timeline!.map((t) => t.total_count || 1), 1);
                return serverDrill.timeline!.map((pt) => {
                  const pct = Math.max(12, Math.round((pt.total_count / maxCnt) * 100));
                  return (
                    <div key={pt.period} className="flex flex-col items-center min-w-[44px] flex-1" title={`${pt.period}: ${pt.total_count} درخواست، میانگین ${pt.avg_wait_hours}h`}>
                      <div className="w-full h-8 flex items-end justify-center">
                        <div
                          className="w-5 rounded-t"
                          style={{
                            height: `${pct}%`,
                            backgroundColor: pt.overdue_count > 0 ? "#f97316" : activeThemeTokens.primary,
                          }}
                        />
                      </div>
                      <span className="text-[9px] font-mono opacity-75 truncate">{pt.period.slice(2)}</span>
                    </div>
                  );
                });
              })()}
            </div>
          </div>
        )}

        {/* Assignees Breakdown + Detailed Pending Requests Table */}
        <div className="flex-1 min-h-0 grid grid-cols-1 lg:grid-cols-12 gap-2.5 overflow-hidden">
          {/* Left/Top: People Whose Cartable Holds Requests */}
          <div
            className="lg:col-span-5 rounded-lg border p-2.5 flex flex-col min-h-0"
            style={{ borderColor: activeThemeTokens.border, backgroundColor: activeThemeTokens.surface }}
          >
            <div className="flex items-center justify-between mb-2 shrink-0">
              <div className="font-bold flex items-center gap-1.5 text-[11px]">
                <Users className="w-3.5 h-3.5 text-blue-500" />
                {isFa
                  ? `افراد دارای درخواست در کارتابل (${effectiveAssignees.length})`
                  : `Cartable Assignees (${effectiveAssignees.length})`}
              </div>
              {selectedAssignee && (
                <button
                  type="button"
                  onClick={() => {
                    setSelectedAssignee(null);
                    setDrillOffset(0);
                  }}
                  className="text-[10px] font-semibold underline"
                  style={{ color: activeThemeTokens.primary }}
                >
                  {isFa ? "نمایش همه افراد" : "Show All"}
                </button>
              )}
            </div>
            <div className="flex-1 overflow-y-auto space-y-1.5 pe-0.5">
              {effectiveAssignees.map((asgn) => {
                const isActive = selectedAssignee === asgn.assigneeName;
                return (
                  <button
                    key={asgn.assigneeName}
                    type="button"
                    onClick={() => {
                      setSelectedAssignee(isActive ? null : asgn.assigneeName);
                      setDrillOffset(0);
                    }}
                    className={`w-full text-start p-2 rounded-lg border transition flex items-center justify-between gap-2 ${
                      isActive ? "ring-2 ring-blue-500/60" : "hover:bg-blue-500/5"
                    }`}
                    style={{
                      borderColor: isActive ? activeThemeTokens.primary : activeThemeTokens.border,
                      backgroundColor: isActive ? "color-mix(in srgb, var(--bi-primary) 8%, transparent)" : "transparent",
                    }}
                  >
                    <div className="min-w-0">
                      <div className="font-bold flex items-center gap-1.5 truncate">
                        <UserCheck className="w-3.5 h-3.5 shrink-0 text-emerald-500" />
                        <span className="truncate">{asgn.assigneeName}</span>
                      </div>
                      {asgn.assigneeRole && (
                        <div className="text-[10px] truncate mt-0.5" style={{ color: activeThemeTokens.muted_text }}>
                          {asgn.assigneeRole}
                        </div>
                      )}
                    </div>
                    <div className="text-end shrink-0">
                      <div className="font-bold text-[11px]">
                        {(asgn.pendingCount > 0 ? asgn.pendingCount : asgn.totalCount).toLocaleString(isFa ? "fa-IR" : "en-US")}{" "}
                        {isFa ? "درخواست" : "req"}
                      </div>
                      <div className={`text-[10px] font-semibold ${asgn.avgWaitHours > effSummary.slaHours ? "text-red-500" : "text-amber-500"}`}>
                        {isFa ? `میانگین ${asgn.avgWaitHours} س` : `Avg ${asgn.avgWaitHours}h`}
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Right/Bottom: Request-by-Request Wait Time List in Cartable */}
          <div
            className="lg:col-span-7 rounded-lg border p-2.5 flex flex-col min-h-0"
            style={{ borderColor: activeThemeTokens.border, backgroundColor: activeThemeTokens.surface }}
          >
            <div className="flex flex-wrap items-center justify-between gap-1.5 mb-2 shrink-0">
              <div className="font-bold text-[11px] flex items-center gap-1.5">
                <Clock className="w-3.5 h-3.5 text-amber-500" />
                {isFa ? "جزئیات درخواست‌ها و زمان انتظار در کارتابل" : "Pending Requests & Cartable Wait Time"}
              </div>
              <div className="flex items-center gap-1.5">
                {effSummary.completedCount > 0 && (
                  <button
                    type="button"
                    onClick={() => {
                      setShowAllStatusesInDrill((s) => !s);
                      setDrillOffset(0);
                    }}
                    className="px-2 py-0.5 rounded border text-[10px] font-semibold"
                    style={{ borderColor: activeThemeTokens.border }}
                  >
                    {showAllStatusesInDrill
                      ? isFa ? "فقط مانده در کارتابل" : "Pending Only"
                      : isFa ? `نمایش کل (${effSummary.totalCount.toLocaleString("fa-IR")})` : `Show All (${effSummary.totalCount})`}
                  </button>
                )}
                <div className="relative">
                  <Search className="w-3 h-3 absolute top-1.5 start-2 opacity-50" />
                  <input
                    value={drillSearch}
                    onChange={(e) => {
                      setDrillSearch(e.target.value);
                      setDrillOffset(0);
                    }}
                    placeholder={isFa ? "جستجو..." : "Search..."}
                    className="ps-6 pe-2 py-0.5 rounded border text-[10px] bg-transparent w-28"
                    style={{ borderColor: activeThemeTokens.border }}
                  />
                </div>
              </div>
            </div>

            <div className="flex-1 overflow-auto">
              <table className="w-full text-[11px]">
                <thead>
                  <tr className="border-b text-[10px]" style={{ borderColor: activeThemeTokens.border, color: activeThemeTokens.muted_text }}>
                    <th className="py-1.5 px-2 text-start">{isFa ? "درخواست" : "Request"}</th>
                    <th className="py-1.5 px-2 text-start">{isFa ? "در کارتابل" : "Assignee"}</th>
                    <th className="py-1.5 px-2 text-start">{isFa ? "زمان انتظار" : "Wait Time"}</th>
                    <th className="py-1.5 px-2 text-start">{isFa ? "وضعیت" : "Status"}</th>
                  </tr>
                </thead>
                <tbody>
                  {displayedItems.map((it, i) => {
                    const slaPct = it.slaHours > 0 ? Math.min(200, Math.round((it.waitHours / it.slaHours) * 100)) : 0;
                    return (
                      <tr key={`${it.requestId}-${i}`} className="border-b last:border-0" style={{ borderColor: activeThemeTokens.border }}>
                        <td className="py-1.5 px-2">
                          <div className="font-bold">{it.requestId}</div>
                          {it.requestTitle && (
                            <div className="text-[10px] truncate max-w-[190px]" style={{ color: activeThemeTokens.muted_text }}>
                              {it.requestTitle}
                            </div>
                          )}
                        </td>
                        <td className="py-1.5 px-2 whitespace-nowrap">
                          <div className="font-semibold">{it.assigneeName}</div>
                          {it.department && (
                            <div className="text-[10px]" style={{ color: activeThemeTokens.muted_text }}>
                              {it.department}
                            </div>
                          )}
                        </td>
                        <td className="py-1.5 px-2 whitespace-nowrap">
                          <div className={`font-bold ${it.isOverdue ? "text-red-500" : "text-amber-500"}`}>
                            {formatWaitDuration(it.waitHours, isFa)}
                          </div>
                          <div className="w-24 h-1.5 rounded-full bg-slate-500/20 mt-1 overflow-hidden">
                            <div
                              className="h-full rounded-full"
                              style={{
                                width: `${Math.min(100, slaPct / 1.5)}%`,
                                backgroundColor: it.isOverdue ? "#ef4444" : slaPct > 75 ? "#f59e0b" : "#10b981",
                              }}
                            />
                          </div>
                        </td>
                        <td className="py-1.5 px-2 whitespace-nowrap">
                          <span
                            className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${
                              it.isOverdue
                                ? "bg-red-500/15 text-red-500"
                                : it.isPending
                                ? "bg-amber-500/15 text-amber-500"
                                : "bg-emerald-500/15 text-emerald-500"
                            }`}
                          >
                            {it.status}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                  {displayedItems.length === 0 && !drillLoading && (
                    <tr>
                      <td colSpan={4} className="py-6 text-center text-[11px]" style={{ color: activeThemeTokens.muted_text }}>
                        {isFa ? "موردی یافت نشد." : "No matching requests."}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            {serverDrill && totalDisplayedCount > serverDrill.limit && (
              <div className="flex items-center justify-between pt-2 border-t text-[10px]" style={{ borderColor: activeThemeTokens.border }}>
                <span>
                  {isFa
                    ? `نمایش ${drillOffset + 1} تا ${Math.min(drillOffset + serverDrill.limit, totalDisplayedCount)} از ${totalDisplayedCount.toLocaleString("fa-IR")}`
                    : `${drillOffset + 1}-${Math.min(drillOffset + serverDrill.limit, totalDisplayedCount)} of ${totalDisplayedCount}`}
                </span>
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    disabled={drillOffset === 0}
                    onClick={() => setDrillOffset((o) => Math.max(0, o - serverDrill.limit))}
                    className="px-2 py-0.5 rounded border disabled:opacity-40"
                    style={{ borderColor: activeThemeTokens.border }}
                  >
                    {isFa ? "قبلی" : "Prev"}
                  </button>
                  <button
                    type="button"
                    disabled={drillOffset + serverDrill.limit >= totalDisplayedCount}
                    onClick={() => setDrillOffset((o) => o + serverDrill.limit)}
                    className="px-2 py-0.5 rounded border disabled:opacity-40"
                    style={{ borderColor: activeThemeTokens.border }}
                  >
                    {isFa ? "بعدی" : "Next"}
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  // ---------------- MAIN BPMN DIAGRAM + HEATMAP VIEW ----------------
  const processes = bpmnAnalytics?.processes || [];
  const activeProcess = bpmnAnalytics?.active_process || null;

  return (
    <div dir={isFa ? "rtl" : "ltr"} className="h-full flex flex-col gap-2 overflow-hidden text-xs">
      {/* Multi-Process Switcher Bar when organization dataset has multiple processes */}
      {processes.length > 0 && (
        <div
          className="flex flex-wrap items-center gap-1.5 px-2.5 py-1.5 rounded-lg border shrink-0 overflow-x-auto"
          style={{ borderColor: activeThemeTokens.border, backgroundColor: activeThemeTokens.surface }}
        >
          <span className="text-[10px] font-bold flex items-center gap-1 shrink-0" style={{ color: activeThemeTokens.muted_text }}>
            <GitBranch className="w-3.5 h-3.5 text-blue-500" />
            {isFa ? `فرآیندهای سازمان (${processes.length}):` : `Processes (${processes.length}):`}
          </span>
          {onProcessSelect && (
            <button
              type="button"
              onClick={() => onProcessSelect("__ALL__")}
              className={`px-2 py-0.5 rounded-md text-[10px] font-semibold border transition shrink-0 ${
                !activeProcess ? "text-white" : "hover:bg-blue-500/10"
              }`}
              style={!activeProcess ? { backgroundColor: activeThemeTokens.primary, borderColor: activeThemeTokens.primary } : { borderColor: activeThemeTokens.border }}
            >
              {isFa ? "همه فرآیندها" : "All Processes"}
            </button>
          )}
          {processes.slice(0, 12).map((pr) => {
            const isSel = activeProcess === pr.process_name;
            return (
              <button
                key={pr.process_name}
                type="button"
                onClick={() => onProcessSelect && onProcessSelect(pr.process_name)}
                className={`px-2 py-0.5 rounded-md text-[10px] font-semibold border transition shrink-0 ${
                  isSel ? "text-white" : "hover:bg-blue-500/10"
                }`}
                style={isSel ? { backgroundColor: activeThemeTokens.primary, borderColor: activeThemeTokens.primary } : { borderColor: activeThemeTokens.border }}
                title={`${pr.process_name} (${pr.total_count} رکورد، ${pr.step_count} گام)`}
              >
                {pr.process_name} <span className="opacity-75">({pr.total_count.toLocaleString(isFa ? "fa-IR" : "en-US")})</span>
              </button>
            );
          })}
          {processes.length > 12 && onProcessSelect && (
            <select
              value={activeProcess || "__ALL__"}
              onChange={(e) => onProcessSelect(e.target.value)}
              className="px-2 py-0.5 rounded border text-[10px]"
              style={{
                background: activeThemeTokens.card_bg,
                color: activeThemeTokens.text,
                borderColor: activeThemeTokens.border,
              }}
            >
              <option value="__ALL__">{isFa ? "همه فرآیندها..." : "All Processes..."}</option>
              {processes.map((pr) => (
                <option key={pr.process_name} value={pr.process_name}>
                  {pr.process_name} ({pr.total_count})
                </option>
              ))}
            </select>
          )}
        </div>
      )}

      {/* Top Interactive Controls & Summary Strip */}
      <div className="flex flex-wrap items-center justify-between gap-2 shrink-0">
        <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
          {bpmnAnalytics && bpmnAnalytics.total_source_rows > 0 && (
            <span
              className="px-2 py-1 rounded-md border font-semibold flex items-center gap-1 text-emerald-600 dark:text-emerald-400 border-emerald-500/30 bg-emerald-500/10"
              title={isFa ? "تحلیل ۱۰۰٪ رکوردها مستقیماً در موتور SQL" : "100% of rows analyzed in DuckDB SQL"}
            >
              <Database className="w-3.5 h-3.5" />
              {isFa
                ? `${bpmnAnalytics.active_source_rows.toLocaleString("fa-IR")} از ${bpmnAnalytics.total_source_rows.toLocaleString("fa-IR")} رکورد (SQL)`
                : `${bpmnAnalytics.active_source_rows.toLocaleString()} / ${bpmnAnalytics.total_source_rows.toLocaleString()} rows`}
            </span>
          )}
          <span
            className="px-2 py-1 rounded-md border font-semibold flex items-center gap-1"
            style={{ borderColor: activeThemeTokens.border, backgroundColor: activeThemeTokens.surface }}
          >
            <Inbox className="w-3.5 h-3.5 text-blue-500" />
            {isFa ? `در کارتابل: ${totalPendingRequests.toLocaleString("fa-IR")}` : `Pending: ${totalPendingRequests}`}
          </span>
          <span
            className="px-2 py-1 rounded-md border font-semibold flex items-center gap-1"
            style={{ borderColor: activeThemeTokens.border, backgroundColor: activeThemeTokens.surface }}
          >
            <Clock className="w-3.5 h-3.5 text-amber-500" />
            {isFa ? `میانگین انتظار: ${overallAvgWaitHours} ساعت` : `Avg Wait: ${overallAvgWaitHours}h`}
          </span>
          {bottleneckStep && (
            <button
              type="button"
              onClick={() => {
                setSelectedStepKey(bottleneckStep.stepKey);
                setDrillOffset(0);
              }}
              className="px-2 py-1 rounded-md border border-red-500/40 bg-red-500/10 text-red-500 font-bold flex items-center gap-1 hover:bg-red-500/20"
              title={isFa ? "کلیک برای مشاهده کارتابل گلوگاه" : "Click to inspect bottleneck cartable"}
            >
              <AlertTriangle className="w-3.5 h-3.5" />
              {isFa
                ? `گلوگاه: ${bottleneckStep.stepLabel} (${bottleneckStep.avgWaitHours} س)`
                : `Bottleneck: ${bottleneckStep.stepLabel} (${bottleneckStep.avgWaitHours}h)`}
            </button>
          )}
        </div>

        {/* Column Mapper + Heatmap Toggle + Metric + Zoom */}
        <div className="flex flex-wrap items-center gap-1.5">
          {availableCols.length > 0 && (
            <button
              type="button"
              onClick={() => setShowMapper((s) => !s)}
              className={`inline-flex items-center gap-1 px-2 py-1 rounded-md border text-[11px] font-semibold transition ${
                showMapper ? "bg-blue-500/15 border-blue-500/50 text-blue-500" : ""
              }`}
              style={showMapper ? undefined : { borderColor: activeThemeTokens.border }}
              title={isFa ? "تنظیم ستون‌های فرآیند، گام و نام فرد در کارتابل" : "Map Process/Step/Assignee columns"}
            >
              <SlidersHorizontal className="w-3.5 h-3.5" />
              {isFa ? "ستون‌ها" : "Columns"}
            </button>
          )}

          <button
            type="button"
            onClick={() => setHeatmapEnabled((v) => !v)}
            className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-md border text-[11px] font-semibold transition ${
              heatmapEnabled ? "bg-rose-500/15 border-rose-500/50 text-rose-500" : ""
            }`}
            style={heatmapEnabled ? undefined : { borderColor: activeThemeTokens.border }}
          >
            <Flame className="w-3.5 h-3.5" />
            {isFa ? "نقشه حرارتی" : "Heatmap"}
          </button>

          {heatmapEnabled && (
            <select
              aria-label={isFa ? "معیار نقشه حرارتی" : "Heatmap Metric"}
              value={heatmapMetric}
              onChange={(e) => setHeatmapMetric(e.target.value as any)}
              className="px-2 py-1 rounded-md border text-[11px] font-medium"
              style={{
                background: activeThemeTokens.surface,
                color: activeThemeTokens.text,
                borderColor: activeThemeTokens.border,
              }}
            >
              <option value="avg_wait">{isFa ? "بر اساس میانگین زمان انتظار" : "Avg Wait Time"}</option>
              <option value="max_wait">{isFa ? "بر اساس حداکثر زمان انتظار" : "Max Wait Time"}</option>
              <option value="pending_count">{isFa ? "بر اساس تعداد در کارتابل" : "Pending Count"}</option>
            </select>
          )}

          {datasetId && !publicShareToken && (
            <>
              <button
                type="button"
                onClick={() => setShowFormulaPanel((s) => !s)}
                className={`inline-flex items-center gap-1 px-2 py-1 rounded-md border text-[11px] font-semibold transition ${
                  showFormulaPanel ? "bg-violet-500/15 border-violet-500/50 text-violet-500" : ""
                }`}
                style={showFormulaPanel ? undefined : { borderColor: activeThemeTokens.border }}
              >
                <Sparkles className="w-3.5 h-3.5" />
                {isFa ? "فرمول گلوگاه" : "Bottleneck Rule"}
              </button>

              <button
                type="button"
                onClick={() => setDragMode((d) => !d)}
                className={`inline-flex items-center gap-1 px-2 py-1 rounded-md border text-[11px] font-semibold transition ${
                  dragMode ? "bg-emerald-500/15 border-emerald-500/50 text-emerald-500" : ""
                }`}
                style={dragMode ? undefined : { borderColor: activeThemeTokens.border }}
                title={isFa ? "جابه‌جایی و چیدمان دستی گام‌ها روی نمودار" : "Drag & drop BPMN steps"}
              >
                <Move className="w-3.5 h-3.5" />
                {isFa ? (dragMode ? "چیدمان فعال" : "جابه‌جایی گام") : "Drag Nodes"}
              </button>

              {layoutDirty && (
                <button
                  type="button"
                  disabled={savingLayout}
                  onClick={() => handleSaveLayout(false)}
                  className="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-emerald-600 text-white text-[11px] font-semibold hover:bg-emerald-700 disabled:opacity-50"
                >
                  <Save className="w-3 h-3" />
                  {isFa ? "ذخیره چیدمان" : "Save Layout"}
                </button>
              )}

              {Object.keys(customPositions).length > 0 && (
                <button
                  type="button"
                  disabled={savingLayout}
                  onClick={() => handleSaveLayout(true)}
                  className="inline-flex items-center gap-1 px-2 py-1 rounded-md border text-[11px] font-semibold hover:bg-red-500/10"
                  style={{ borderColor: activeThemeTokens.border }}
                  title={isFa ? "بازنشانی به چیدمان خودکار" : "Reset auto layout"}
                >
                  <RotateCcw className="w-3 h-3" />
                </button>
              )}
            </>
          )}

          <div className="flex items-center border rounded-md overflow-hidden" style={{ borderColor: activeThemeTokens.border }}>
            <button type="button" onClick={() => setZoom((z) => Math.max(0.65, Math.round((z - 0.15) * 100) / 100))} className="p-1 hover:bg-blue-500/10" title="Zoom Out">
              <ZoomOut className="w-3.5 h-3.5" />
            </button>
            <button type="button" onClick={() => setZoom(1)} className="px-1.5 text-[10px] font-semibold hover:bg-blue-500/10" title="Fit">
              <Maximize2 className="w-3 h-3" />
            </button>
            <button type="button" onClick={() => setZoom((z) => Math.min(1.6, Math.round((z + 0.15) * 100) / 100))} className="p-1 hover:bg-blue-500/10" title="Zoom In">
              <ZoomIn className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      </div>

      {renderFieldMapperPanel()}

      {showFormulaPanel && datasetId && (
        <div
          className="p-3 rounded-lg border space-y-2.5 shrink-0 text-[11px]"
          style={{ borderColor: activeThemeTokens.border, backgroundColor: activeThemeTokens.surface }}
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="font-bold text-violet-500">
              {isFa ? "تنظیم فرمول و آستانه تشخیص گلوگاه فرآیندی" : "Custom Bottleneck Formula Builder"}
            </span>
            <div className="flex flex-wrap gap-1.5">
              <input
                value={ruleName}
                onChange={(e) => setRuleName(e.target.value)}
                placeholder={isFa ? "نام قاعده" : "Rule Name"}
                className="px-2 py-1 rounded border bg-transparent text-[11px]"
                style={{ borderColor: activeThemeTokens.border }}
              />
              <label className="flex items-center gap-1">
                <span>{isFa ? "آستانه امتیاز:" : "Threshold:"}</span>
                <input
                  type="number"
                  min={10}
                  max={95}
                  value={ruleThreshold}
                  onChange={(e) => setRuleThreshold(Number(e.target.value) || 60)}
                  className="w-16 px-1.5 py-1 rounded border bg-transparent text-[11px]"
                  style={{ borderColor: activeThemeTokens.border }}
                />
              </label>
            </div>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-6 gap-2">
            {[
              { k: "avg_wait", label: isFa ? "میانگین انتظار" : "Avg Wait" },
              { k: "max_wait", label: isFa ? "حداکثر انتظار" : "Max Wait" },
              { k: "pending_count", label: isFa ? "مانده کارتابل" : "Pending" },
              { k: "overdue_ratio", label: isFa ? "نرخ نقض SLA" : "SLA Breach" },
              { k: "rework_rate", label: isFa ? "نرخ دوباره‌کاری" : "Rework" },
              { k: "volume_share", label: isFa ? "سهم حجم" : "Volume" },
            ].map((item) => (
              <label key={item.k} className="space-y-1">
                <span className="block text-[10px]" style={{ color: activeThemeTokens.muted_text }}>
                  {item.label} ({Math.round((ruleWeights[item.k] ?? 0) * 100)}%)
                </span>
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.05}
                  value={ruleWeights[item.k] ?? 0}
                  onChange={(e) => setRuleWeights((prev) => ({ ...prev, [item.k]: Number(e.target.value) }))}
                  className="w-full"
                />
              </label>
            ))}
          </div>
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setShowFormulaPanel(false)}
              className="px-2.5 py-1 rounded border text-[11px]"
              style={{ borderColor: activeThemeTokens.border }}
            >
              {isFa ? "انصراف" : "Cancel"}
            </button>
            <button
              type="button"
              disabled={savingRule}
              onClick={handleSaveBottleneckRule}
              className="px-3 py-1 rounded bg-violet-600 text-white font-semibold hover:bg-violet-700 disabled:opacity-50"
            >
              {savingRule ? "..." : isFa ? "ذخیره و محاسبه مجدد گلوگاه" : "Save & Recalculate"}
            </button>
          </div>
        </div>
      )}

      {/* Interactive BPMN Canvas */}
      <div
        className="flex-1 min-h-0 relative rounded-xl border overflow-auto"
        style={{
          borderColor: activeThemeTokens.border,
          backgroundColor: isDark ? "rgba(15, 23, 42, 0.55)" : "rgba(248, 250, 252, 0.85)",
          backgroundImage: `radial-gradient(${isDark ? "rgba(148,163,184,0.16)" : "rgba(100,116,139,0.16)"} 1px, transparent 1px)`,
          backgroundSize: "18px 18px",
        }}
      >
        <div
          dir="ltr"
          className="min-w-full min-h-full flex items-center justify-center p-2"
          style={{ width: `${Math.round(bounds.width * zoom)}px`, height: `${Math.max(260, Math.round(bounds.height * zoom))}px` }}
        >
          <svg
            ref={svgRef}
            viewBox={`${bounds.minX} ${bounds.minY} ${bounds.width} ${bounds.height}`}
            className="w-full h-full select-none"
            style={{ fontFamily: "Vazirmatn, Inter, sans-serif" }}
            onPointerMove={(e) => {
              const d = dragStateRef.current;
              if (!d || !svgRef.current) return;
              const rect = svgRef.current.getBoundingClientRect();
              const scaleX = rect.width > 0 ? bounds.width / rect.width : 1;
              const scaleY = rect.height > 0 ? bounds.height / rect.height : 1;
              const dx = (e.clientX - d.startClientX) * scaleX;
              const dy = (e.clientY - d.startClientY) * scaleY;
              if (Math.hypot(e.clientX - d.startClientX, e.clientY - d.startClientY) >= 5) {
                d.moved = true;
                suppressClickRef.current = true;
              }
              if (d.moved) {
                setCustomPositions((prev) => ({
                  ...prev,
                  [d.nodeId]: { x: Math.round(d.origX + dx), y: Math.round(d.origY + dy) },
                }));
                setLayoutDirty(true);
              }
            }}
            onPointerUp={() => {
              dragStateRef.current = null;
              setTimeout(() => {
                suppressClickRef.current = false;
              }, 30);
            }}
            onPointerLeave={() => {
              dragStateRef.current = null;
            }}
          >
            <defs>
              <filter id="bpmnHeatGlow" x="-40%" y="-40%" width="180%" height="180%">
                <feGaussianBlur stdDeviation="11" result="blur" />
                <feMerge>
                  <feMergeNode in="blur" />
                  <feMergeNode in="SourceGraphic" />
                </feMerge>
              </filter>
              <marker id="bpmnArrow" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto">
                <path d="M 0 1 L 8 4.5 L 0 8 z" fill={isDark ? "#94a3b8" : "#64748b"} />
              </marker>
              <marker id="bpmnArrowHot" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto">
                <path d="M 0 1 L 8 4.5 L 0 8 z" fill="#ef4444" />
              </marker>
              <marker id="bpmnArrowWarm" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto">
                <path d="M 0 1 L 8 4.5 L 0 8 z" fill="#f97316" />
              </marker>
            </defs>

            {heatmapEnabled &&
              graph.nodes.map((node) => {
                const st = stepMap[node.id];
                if (!st || st.totalCount === 0) return null;
                const pal = getHeatPalette(st.heatRatio, isDark);
                return (
                  <ellipse
                    key={`heat-${node.id}`}
                    cx={node.x + node.width / 2}
                    cy={node.y + node.height / 2}
                    rx={node.width * 0.66}
                    ry={node.height * 0.72}
                    fill={pal.glow}
                    opacity={0.3 + st.heatRatio * 0.55}
                    filter="url(#bpmnHeatGlow)"
                  />
                );
              })}

            {graph.edges.map((edge) => {
              const src = graph.nodes.find((n) => n.id === edge.source);
              const dst = graph.nodes.find((n) => n.id === edge.target);
              if (!src || !dst) return null;
              const { path, midX, midY } = getEdgePath(src, dst);
              const dstStats = stepMap[dst.id];
              const srcStats = stepMap[src.id];
              const edgeHeat = Math.max(dstStats?.heatRatio || 0, (srcStats?.heatRatio || 0) * 0.75);
              const isHot = heatmapEnabled && edgeHeat >= 0.75;
              const isWarm = heatmapEnabled && !isHot && edgeHeat >= 0.45;
              const strokeColor = isHot
                ? "#ef4444"
                : isWarm
                ? "#f97316"
                : isDark
                ? "#64748b"
                : "#94a3b8";
              const marker = isHot ? "url(#bpmnArrowHot)" : isWarm ? "url(#bpmnArrowWarm)" : "url(#bpmnArrow)";

              return (
                <g key={edge.id}>
                  {heatmapEnabled && edgeHeat > 0.2 && (
                    <path
                      d={path}
                      fill="none"
                      stroke={strokeColor}
                      strokeWidth={6 + edgeHeat * 7}
                      opacity={0.18 + edgeHeat * 0.22}
                      strokeLinecap="round"
                    />
                  )}
                  <path
                    d={path}
                    fill="none"
                    stroke={strokeColor}
                    strokeWidth={isHot ? 2.6 : 2}
                    markerEnd={marker}
                  />
                  {edge.label && (
                    <g>
                      <rect
                        x={midX - 28}
                        y={midY - 10}
                        width={56}
                        height={18}
                        rx={4}
                        fill={isDark ? "#0f172a" : "#ffffff"}
                        stroke={activeThemeTokens.border}
                        strokeWidth={0.8}
                      />
                      <text
                        x={midX}
                        y={midY + 2}
                        textAnchor="middle"
                        fontSize={9.5}
                        fontWeight={600}
                        fill={activeThemeTokens.text}
                      >
                        {edge.label}
                      </text>
                    </g>
                  )}
                </g>
              );
            })}

            {graph.nodes.map((node) => {
              if (node.type === "startEvent" || node.type === "endEvent") {
                const cx = node.x + node.width / 2;
                const cy = node.y + node.height / 2;
                const r = Math.min(node.width, node.height) / 2;
                const isStart = node.type === "startEvent";
                return (
                  <g key={node.id}>
                    <circle
                      cx={cx}
                      cy={cy}
                      r={r}
                      fill={isStart ? (isDark ? "#064e3b" : "#d1fae5") : isDark ? "#3b0764" : "#f1f5f9"}
                      stroke={isStart ? "#10b981" : isDark ? "#cbd5e1" : "#334155"}
                      strokeWidth={isStart ? 2.5 : 4}
                    />
                    <text
                      x={cx}
                      y={cy + r + 14}
                      textAnchor="middle"
                      fontSize={10}
                      fontWeight={700}
                      fill={activeThemeTokens.text}
                    >
                      {node.label}
                    </text>
                  </g>
                );
              }

              if (node.type === "exclusiveGateway" || node.type === "parallelGateway") {
                const cx = node.x + node.width / 2;
                const cy = node.y + node.height / 2;
                const hw = node.width / 2;
                const hh = node.height / 2;
                return (
                  <g key={node.id}>
                    <polygon
                      points={`${cx},${cy - hh} ${cx + hw},${cy} ${cx},${cy + hh} ${cx - hw},${cy}`}
                      fill={isDark ? "#1e293b" : "#fef3c7"}
                      stroke="#f59e0b"
                      strokeWidth={2}
                    />
                    <text x={cx} y={cy + 5} textAnchor="middle" fontSize={17} fontWeight={800} fill="#d97706">
                      {node.type === "parallelGateway" ? "+" : "×"}
                    </text>
                    <text
                      x={cx}
                      y={cy - hh - 7}
                      textAnchor="middle"
                      fontSize={9.5}
                      fontWeight={600}
                      fill={activeThemeTokens.muted_text}
                    >
                      {node.label}
                    </text>
                  </g>
                );
              }

              const st = stepMap[node.id];
              const pal = heatmapEnabled && st
                ? getHeatPalette(st.heatRatio, isDark)
                : {
                    stroke: activeThemeTokens.primary,
                    fill: activeThemeTokens.card_bg,
                    glow: "transparent",
                    badgeBg: "rgba(37, 99, 235, 0.12)",
                    badgeText: activeThemeTokens.primary,
                  };
              const isHovered = hoveredStepId === node.id;

              return (
                <g
                  key={node.id}
                  onPointerDown={(e) => {
                    if (!dragMode) return;
                    e.stopPropagation();
                    dragStateRef.current = {
                      nodeId: node.id,
                      startClientX: e.clientX,
                      startClientY: e.clientY,
                      origX: node.x,
                      origY: node.y,
                      moved: false,
                    };
                  }}
                  onClick={() => {
                    if (suppressClickRef.current) return;
                    if (st) {
                      setSelectedStepKey(st.stepKey);
                      setDrillOffset(0);
                    }
                  }}
                  onMouseEnter={() => setHoveredStepId(node.id)}
                  onMouseLeave={() => setHoveredStepId((cur) => (cur === node.id ? null : cur))}
                  className={dragMode ? "cursor-grab active:cursor-grabbing" : "cursor-pointer"}
                >
                  <rect
                    x={node.x}
                    y={node.y}
                    width={node.width}
                    height={node.height}
                    rx={12}
                    fill={pal.fill}
                    stroke={pal.stroke}
                    strokeWidth={isHovered || st?.isBottleneck ? 2.8 : 1.8}
                  />
                  <rect
                    x={node.x + 1}
                    y={node.y + 1}
                    width={node.width - 2}
                    height={6}
                    rx={4}
                    fill={pal.stroke}
                    opacity={0.85}
                  />

                  {st?.isBottleneck && (
                    <g>
                      <rect x={node.x + node.width - 54} y={node.y - 11} width={50} height={17} rx={8} fill="#ef4444" />
                      <text x={node.x + node.width - 29} y={node.y + 1} textAnchor="middle" fontSize={9} fontWeight={700} fill="#ffffff">
                        {isFa ? "گلوگاه" : "Bottleneck"}
                      </text>
                    </g>
                  )}

                  <foreignObject x={node.x + 6} y={node.y + 10} width={node.width - 12} height={node.height - 14}>
                    <div
                      dir={isFa ? "rtl" : "ltr"}
                      className="h-full flex flex-col justify-between px-1 py-0.5 text-center"
                      style={{ color: activeThemeTokens.text, fontFamily: "Vazirmatn, Inter, sans-serif" }}
                    >
                      <div className="text-[11px] font-bold leading-tight line-clamp-2">
                        {node.label}
                      </div>
                      <div className="flex items-center justify-center gap-1 flex-wrap mt-1">
                        <span
                          className="px-1.5 py-0.5 rounded text-[10px] font-bold"
                          style={{
                            backgroundColor: isDark ? "rgba(15,23,42,0.75)" : "rgba(255,255,255,0.85)",
                            color: activeThemeTokens.text,
                          }}
                        >
                          {isFa
                            ? `${(st?.pendingCount ?? 0).toLocaleString("fa-IR")} درخواست`
                            : `${st?.pendingCount ?? 0} req`}
                        </span>
                        <span
                          className="px-1.5 py-0.5 rounded text-[10px] font-bold"
                          style={{
                            backgroundColor: pal.badgeBg,
                            color: pal.badgeText,
                          }}
                        >
                          {isFa ? `${st?.avgWaitHours ?? 0} س` : `${st?.avgWaitHours ?? 0}h`}
                        </span>
                      </div>
                    </div>
                  </foreignObject>
                </g>
              );
            })}
          </svg>
        </div>

        {/* Rich 11-Metric Hover Floating Tooltip */}
        {hoveredStepMetrics && (
          <div
            dir={isFa ? "rtl" : "ltr"}
            className="absolute top-2 end-2 z-10 p-3 rounded-xl border shadow-xl text-[11px] w-[275px] pointer-events-none"
            style={{
              backgroundColor: activeThemeTokens.card_bg,
              borderColor: activeThemeTokens.border,
              color: activeThemeTokens.text,
            }}
          >
            <div className="flex items-center justify-between gap-2 mb-1.5 pb-1 border-b" style={{ borderColor: activeThemeTokens.border }}>
              <span className="font-bold truncate">{hoveredStepMetrics.stepLabel}</span>
              {hoveredStepMetrics.isBottleneck && (
                <span className="px-1.5 py-0.5 rounded bg-red-500/15 text-red-500 text-[9px] font-bold shrink-0">
                  {isFa ? "گلوگاه" : "Bottleneck"}
                </span>
              )}
            </div>
            <div className="grid grid-cols-2 gap-x-2 gap-y-1 text-[10px]">
              <div>{isFa ? "۱. کل درخواست‌ها:" : "1. Total:"} <b>{hoveredStepMetrics.totalCount.toLocaleString(isFa ? "fa-IR" : "en-US")}</b></div>
              <div>{isFa ? "۲. مانده کارتابل:" : "2. Pending:"} <b>{hoveredStepMetrics.pendingCount.toLocaleString(isFa ? "fa-IR" : "en-US")}</b></div>
              <div>{isFa ? "۳. تکمیل‌شده:" : "3. Completed:"} <b>{hoveredStepMetrics.completedCount.toLocaleString(isFa ? "fa-IR" : "en-US")}</b></div>
              <div>{isFa ? "۴. میانگین انتظار:" : "4. Avg Wait:"} <b>{hoveredStepMetrics.avgWaitHours} س</b></div>
              <div>{isFa ? "۵. حداقل انتظار:" : "5. Min Wait:"} <b>{hoveredStepMetrics.minWaitHours} س</b></div>
              <div>{isFa ? "۶. حداکثر انتظار:" : "6. Max Wait:"} <b>{hoveredStepMetrics.maxWaitHours} س</b></div>
              <div>{isFa ? "۷. سقف SLA:" : "7. SLA Limit:"} <b>{hoveredStepMetrics.slaHours} س</b></div>
              <div>{isFa ? "۸. نقض SLA:" : "8. SLA Breach:"} <b className={hoveredStepMetrics.overdueCount > 0 ? "text-red-500" : ""}>{hoveredStepMetrics.overdueCount} ({hoveredStepMetrics.slaBreachPct}%)</b></div>
              <div>{isFa ? "۹. دوباره‌کاری:" : "9. Rework:"} <b>{hoveredStepMetrics.reworkCount} ({hoveredStepMetrics.reworkRate}%)</b></div>
              <div>{isFa ? "۱۰. افراد کارتابل:" : "10. Assignees:"} <b>{hoveredStepMetrics.assigneeCount}</b></div>
              <div className="col-span-2 pt-0.5 flex items-center justify-between">
                <span>{isFa ? "۱۱. امتیاز گلوگاه:" : "11. Bottleneck Score:"}</span>
                <b className={hoveredStepMetrics.isBottleneck ? "text-red-500" : "text-amber-500"}>
                  {hoveredStepMetrics.bottleneckScore} / 100
                </b>
              </div>
            </div>
            <div className="mt-1.5 pt-1 border-t text-[10px] font-semibold text-blue-500" style={{ borderColor: activeThemeTokens.border }}>
              {dragMode
                ? (isFa ? "در حالت جابه‌جایی هستید؛ گام را بکشید" : "Drag to reposition node")
                : (isFa ? "برای دریل‌داون کارتابل، Timeline و اکسل کلیک کنید" : "Click step for cartable, timeline & Excel")}
            </div>
          </div>
        )}
      </div>

      {/* Bottom Heatmap Legend & Drill-Down Hint */}
      <div className="flex flex-wrap items-center justify-between gap-2 text-[10px] shrink-0" style={{ color: activeThemeTokens.muted_text }}>
        {heatmapEnabled ? (
          <div className="flex items-center gap-2">
            <span className="font-semibold">{isFa ? "نقشه حرارتی زمان انتظار:" : "Wait-Time Heatmap:"}</span>
            <span>{minMetricValue} {heatmapMetric === "pending_count" ? (isFa ? "درخواست" : "req") : (isFa ? "ساعت" : "h")}</span>
            <div
              className="w-28 h-2 rounded-full"
              style={{ background: "linear-gradient(90deg, #10b981 0%, #f59e0b 45%, #f97316 72%, #ef4444 100%)" }}
            />
            <span className="font-bold text-red-500">
              {maxMetricValue} {heatmapMetric === "pending_count" ? (isFa ? "درخواست" : "req") : (isFa ? "ساعت (گلوگاه)" : "h (Bottleneck)")}
            </span>
          </div>
        ) : (
          <span />
        )}
        <span>
          {isFa
            ? "💡 روی هر گام کلیک کنید تا اسامی افراد در کارتابل و زمان انتظار درخواست‌ها را ببینید."
            : "💡 Click any BPMN task node to drill down into pending cartable assignees and request wait times."}
        </span>
      </div>
    </div>
  );
}
