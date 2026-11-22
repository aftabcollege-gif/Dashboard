"use client";

import React, { useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  Clock,
  Filter,
  Flame,
  Inbox,
  Maximize2,
  Search,
  UserCheck,
  Users,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import {
  BpmnNode,
  BpmnStepMetrics,
  BpmnWidgetConfig,
  computeBpmnAnalytics,
} from "@/lib/bpmnParser";
import { useBi } from "@/lib/context";

interface Props {
  columns: string[];
  rows: Record<string, any>[];
  styleConfig?: Record<string, any>;
  vizConfig?: Record<string, any>;
  visualSpec?: Record<string, any>;
  onPointClick?: (field: string, value: any) => void;
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
      return rem > 0 ? `${h.toLocaleString("fa-IR")} ساعت (${days} روز و ${rem} س)` : `${h.toLocaleString("fa-IR")} ساعت (${days} روز)`;
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

  // Backward loop edge (e.g. revision -> review)
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

  // Branch down (gateway -> lower branch)
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
}: Props) {
  const { locale, activeThemeMode, activeThemeTokens } = useBi();
  const isFa = locale === "fa";
  const isDark = activeThemeMode === "dark";

  const bpmnConfig: BpmnWidgetConfig = useMemo(
    () => ({
      ...(visualSpec?.bpmn_config || {}),
      ...(styleConfig?.bpmn_config || {}),
    }),
    [visualSpec, styleConfig]
  );

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

  const analytics = useMemo(
    () => computeBpmnAnalytics(rows || [], columns || [], bpmnConfig, heatmapMetric),
    [rows, columns, bpmnConfig, heatmapMetric]
  );

  const { graph, stepMap, minMetricValue, maxMetricValue, totalPendingRequests, overallAvgWaitHours, bottleneckStep, mapping } =
    analytics;

  // Compute SVG Bounding Box
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

  if (!rows || rows.length === 0) {
    return (
      <div className="h-full flex flex-col items-center justify-center text-xs p-4 text-center" style={{ color: activeThemeTokens.muted_text }}>
        <Inbox className="w-7 h-7 mb-2 opacity-50" />
        <p className="font-semibold">
          {isFa ? "هیچ ردیف فرآیندی با فیلترهای فعلی یافت نشد." : "No process events matched the active filters."}
        </p>
      </div>
    );
  }

  // ---------------- DRILL-DOWN VIEW FOR SELECTED BPMN STEP ----------------
  if (activeStepMetrics) {
    const baseList = showAllStatusesInDrill
      ? activeStepMetrics.items
      : activeStepMetrics.pendingItems.length > 0
      ? activeStepMetrics.pendingItems
      : activeStepMetrics.items;
    const qLow = drillSearch.trim().toLowerCase();
    const filteredList = baseList.filter((it) => {
      if (selectedAssignee && it.assigneeName !== selectedAssignee) return false;
      if (!qLow) return true;
      return (
        it.requestId.toLowerCase().includes(qLow) ||
        it.requestTitle.toLowerCase().includes(qLow) ||
        it.assigneeName.toLowerCase().includes(qLow) ||
        it.department.toLowerCase().includes(qLow)
      );
    });

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
            </div>
          </div>

          <div className="flex items-center gap-1.5">
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

        {/* Summary KPI Strip for Selected Step */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 shrink-0">
          <div className="p-2 rounded-lg border" style={{ borderColor: activeThemeTokens.border, backgroundColor: activeThemeTokens.surface }}>
            <div className="text-[10px]" style={{ color: activeThemeTokens.muted_text }}>
              {isFa ? "درخواست مانده در کارتابل" : "Pending in Cartable"}
            </div>
            <div className="text-sm font-bold mt-0.5">{activeStepMetrics.pendingCount}</div>
          </div>
          <div className="p-2 rounded-lg border" style={{ borderColor: activeThemeTokens.border, backgroundColor: activeThemeTokens.surface }}>
            <div className="text-[10px]" style={{ color: activeThemeTokens.muted_text }}>
              {isFa ? "میانگین زمان انتظار" : "Avg Wait Time"}
            </div>
            <div className="text-sm font-bold mt-0.5 text-amber-500">
              {activeStepMetrics.avgWaitHours} {isFa ? "ساعت" : "hrs"}
            </div>
          </div>
          <div className="p-2 rounded-lg border" style={{ borderColor: activeThemeTokens.border, backgroundColor: activeThemeTokens.surface }}>
            <div className="text-[10px]" style={{ color: activeThemeTokens.muted_text }}>
              {isFa ? "بیشترین زمان انتظار" : "Max Wait Time"}
            </div>
            <div className="text-sm font-bold mt-0.5 text-red-500">
              {activeStepMetrics.maxWaitHours} {isFa ? "ساعت" : "hrs"}
            </div>
          </div>
          <div className="p-2 rounded-lg border" style={{ borderColor: activeThemeTokens.border, backgroundColor: activeThemeTokens.surface }}>
            <div className="text-[10px]" style={{ color: activeThemeTokens.muted_text }}>
              {isFa ? "عبور از سقف SLA" : "SLA Overdue"}
            </div>
            <div className="text-sm font-bold mt-0.5">
              {activeStepMetrics.overdueCount} <span className="text-[10px] opacity-65">(SLA: {activeStepMetrics.slaHours}h)</span>
            </div>
          </div>
        </div>

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
                {isFa ? "افرادی که درخواست در کارتابل آن‌ها مانده" : "Cartable Assignees"}
              </div>
              {selectedAssignee && (
                <button
                  type="button"
                  onClick={() => setSelectedAssignee(null)}
                  className="text-[10px] font-semibold underline"
                  style={{ color: activeThemeTokens.primary }}
                >
                  {isFa ? "نمایش همه افراد" : "Show All"}
                </button>
              )}
            </div>
            <div className="flex-1 overflow-y-auto space-y-1.5 pe-0.5">
              {activeStepMetrics.assignees.map((asgn) => {
                const isActive = selectedAssignee === asgn.assigneeName;
                return (
                  <button
                    key={asgn.assigneeName}
                    type="button"
                    onClick={() => setSelectedAssignee(isActive ? null : asgn.assigneeName)}
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
                        {asgn.pendingCount > 0 ? asgn.pendingCount : asgn.totalCount}{" "}
                        {isFa ? "درخواست" : "req"}
                      </div>
                      <div className={`text-[10px] font-semibold ${asgn.avgWaitHours > activeStepMetrics.slaHours ? "text-red-500" : "text-amber-500"}`}>
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
                {activeStepMetrics.completedCount > 0 && (
                  <button
                    type="button"
                    onClick={() => setShowAllStatusesInDrill((s) => !s)}
                    className="px-2 py-0.5 rounded border text-[10px] font-semibold"
                    style={{ borderColor: activeThemeTokens.border }}
                  >
                    {showAllStatusesInDrill
                      ? isFa ? "فقط مانده در کارتابل" : "Pending Only"
                      : isFa ? `نمایش کل (${activeStepMetrics.totalCount})` : `Show All (${activeStepMetrics.totalCount})`}
                  </button>
                )}
                <div className="relative">
                  <Search className="w-3 h-3 absolute top-1.5 start-2 opacity-50" />
                  <input
                    value={drillSearch}
                    onChange={(e) => setDrillSearch(e.target.value)}
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
                  {filteredList.map((it, i) => {
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
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // ---------------- MAIN BPMN DIAGRAM + HEATMAP VIEW ----------------
  return (
    <div dir={isFa ? "rtl" : "ltr"} className="h-full flex flex-col gap-2 overflow-hidden text-xs">
      {/* Top Interactive Controls & Summary Strip */}
      <div className="flex flex-wrap items-center justify-between gap-2 shrink-0">
        <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
          <span
            className="px-2 py-1 rounded-md border font-semibold flex items-center gap-1"
            style={{ borderColor: activeThemeTokens.border, backgroundColor: activeThemeTokens.surface }}
          >
            <Inbox className="w-3.5 h-3.5 text-blue-500" />
            {isFa ? `در کارتابل: ${totalPendingRequests}` : `Pending: ${totalPendingRequests}`}
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
              onClick={() => setSelectedStepKey(bottleneckStep.stepKey)}
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

        {/* Heatmap Toggle + Metric + Zoom */}
        <div className="flex flex-wrap items-center gap-1.5">
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
            viewBox={`${bounds.minX} ${bounds.minY} ${bounds.width} ${bounds.height}`}
            className="w-full h-full select-none"
            style={{ fontFamily: "Vazirmatn, Inter, sans-serif" }}
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

            {/* 1. Heatmap Aura Underlay behind Hot Task Nodes */}
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

            {/* 2. Sequence Flows (Edges) with Heat Gradient */}
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

            {/* 3. BPMN Nodes */}
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

              // Task / UserTask / ServiceTask
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
                  onClick={() => st && setSelectedStepKey(st.stepKey)}
                  onMouseEnter={() => setHoveredStepId(node.id)}
                  onMouseLeave={() => setHoveredStepId((cur) => (cur === node.id ? null : cur))}
                  className="cursor-pointer"
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
                  {/* Top heat/accent strip */}
                  <rect
                    x={node.x + 1}
                    y={node.y + 1}
                    width={node.width - 2}
                    height={6}
                    rx={4}
                    fill={pal.stroke}
                    opacity={0.85}
                  />

                  {/* Bottleneck pill */}
                  {st?.isBottleneck && (
                    <g>
                      <rect x={node.x + node.width - 54} y={node.y - 11} width={50} height={17} rx={8} fill="#ef4444" />
                      <text x={node.x + node.width - 29} y={node.y + 1} textAnchor="middle" fontSize={9} fontWeight={700} fill="#ffffff">
                        {isFa ? "گلوگاه" : "Bottleneck"}
                      </text>
                    </g>
                  )}

                  {/* Step Title via foreignObject for crisp RTL Persian wrapping */}
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
                          {isFa ? `${st?.pendingCount ?? 0} درخواست` : `${st?.pendingCount ?? 0} req`}
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

        {/* Hover Floating Tooltip */}
        {hoveredStepMetrics && (
          <div
            dir={isFa ? "rtl" : "ltr"}
            className="absolute top-2 end-2 z-10 p-2.5 rounded-lg border shadow-lg text-[11px] max-w-[235px] pointer-events-none"
            style={{
              backgroundColor: activeThemeTokens.card_bg,
              borderColor: activeThemeTokens.border,
              color: activeThemeTokens.text,
            }}
          >
            <div className="font-bold mb-1">{hoveredStepMetrics.stepLabel}</div>
            <div className="space-y-0.5 text-[10px]">
              <div>
                {isFa ? "درخواست در گام (کارتابل):" : "Pending in step:"}{" "}
                <b>{hoveredStepMetrics.pendingCount}</b> {isFa ? `(کل عبوری: ${hoveredStepMetrics.totalCount})` : `(Total: ${hoveredStepMetrics.totalCount})`}
              </div>
              <div>
                {isFa ? "میانگین زمان انتظار:" : "Avg wait time:"}{" "}
                <b>{hoveredStepMetrics.avgWaitHours} {isFa ? "ساعت" : "hrs"}</b>
              </div>
              <div>
                {isFa ? "بیشترین زمان انتظار:" : "Max wait time:"}{" "}
                <b>{hoveredStepMetrics.maxWaitHours} {isFa ? "ساعت" : "hrs"}</b>
              </div>
              <div>
                {isFa ? "افراد درگیر در کارتابل:" : "Assignees:"}{" "}
                <b>{hoveredStepMetrics.assignees.length}</b>
              </div>
            </div>
            <div className="mt-1.5 pt-1 border-t text-[10px] font-semibold text-blue-500" style={{ borderColor: activeThemeTokens.border }}>
              {isFa ? "برای دریل‌داون کارتابل روی گام کلیک کنید" : "Click step to drill into assignee cartable"}
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
            ? "💡 روی هر گام نمودار کلیک کنید تا لیست افراد و زمان انتظار درخواست‌ها در کارتابل را ببینید."
            : "💡 Click any BPMN task node to drill down into pending cartable assignees and request wait times."}
        </span>
      </div>
    </div>
  );
}
