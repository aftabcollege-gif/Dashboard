"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { ArrowUp, Filter, RefreshCw, X } from "lucide-react";
import WidgetRenderer from "@/components/charts/WidgetRenderer";
import { apiFetch } from "@/lib/api";
import { CHART_REGISTRY } from "@/lib/chart-registry";
import { useBi } from "@/lib/context";

interface CrossFilterItem {
  field_name: string;
  operator: string;
  value: any;
  source_widget_id: string;
}

export default function PublicDashboardPage() {
  const params = useParams();
  const token = String(params?.token || "");
  const { locale, setLocale, applyThemeTokens } = useBi();

  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState("");
  const [widgetResults, setWidgetResults] = useState<Record<string, any>>({});
  const [crossFilters, setCrossFilters] = useState<CrossFilterItem[]>([]);
  const [drillStates, setDrillStates] = useState<Record<string, { field: string; value: any }[]>>({});
  const [widgetChartOverrides, setWidgetChartOverrides] = useState<Record<string, string>>({});
  const [clickModes, setClickModes] = useState<Record<string, "drill" | "cross_filter">>({});
  const [loadingBatch, setLoadingBatch] = useState(false);

  useEffect(() => {
    apiFetch(`/dashboards/public/${token}`)
      .then((res) => {
        setData(res);
        if (res.theme?.tokens) {
          applyThemeTokens(res.theme.tokens, res.theme.mode || "light");
        }
        const initialResults: Record<string, any> = {};
        for (const w of res.widgets || []) {
          const hierarchy = w.drill_rule?.hierarchy || [];
          initialResults[w.id] = {
            ...(w.data || { columns: [], rows: [] }),
            effective_chart_type: w.chart_type,
            drill: {
              level: 0,
              breadcrumbs: [{ level: 0, label: "All", field: null, value: null }],
              can_drill_down: hierarchy.length > 1,
            },
          };
        }
        setWidgetResults(initialResults);
      })
      .catch((e) => setErr(e.message));
  }, [token]);

  const executePublicBatch = useCallback(
    async (nextCross: CrossFilterItem[], nextDrill: Record<string, { field: string; value: any }[]>) => {
      setLoadingBatch(true);
      try {
        const res = await apiFetch<{ widgets: Record<string, any> }>(`/dashboards/public/${token}/execute-batch`, {
          method: "POST",
          body: JSON.stringify({
            active_filters: [],
            cross_filters: nextCross,
            drill_states: nextDrill,
            use_cache: false,
          }),
        });
        if (res?.widgets) {
          setWidgetResults(res.widgets);
        }
      } catch (e: any) {
        setErr(e.message);
      } finally {
        setLoadingBatch(false);
      }
    },
    [token]
  );

  const getWidgetClickMode = (w: any): "drill" | "cross_filter" => {
    if (clickModes[w.id]) return clickModes[w.id];
    const hasDrill = (w.drill_rule?.hierarchy?.length || 0) > 0;
    if (!hasDrill) return "cross_filter";
    if (w.chart_type === "donut" && w.config?.interactions?.cross_filter !== false) return "cross_filter";
    return "drill";
  };

  const handlePointClick = async (w: any, field: string, value: any) => {
    const hierarchy = w.drill_rule?.hierarchy || [];
    const currentStack = drillStates[w.id] || [];
    const mode = getWidgetClickMode(w);

    if (mode === "drill" && hierarchy.length > 0 && currentStack.length < hierarchy.length - 1) {
      const currentDim = hierarchy[currentStack.length]?.field || field;
      const nextStack = [...currentStack, { field: currentDim, value }];
      const nextDrill = { ...drillStates, [w.id]: nextStack };
      setWidgetChartOverrides((prev) => {
        if (!(w.id in prev)) return prev;
        const copy = { ...prev };
        delete copy[w.id];
        return copy;
      });
      setDrillStates(nextDrill);
      await executePublicBatch(crossFilters, nextDrill);
      return;
    }

    if (w.config?.interactions?.cross_filter !== false) {
      const sameExact = crossFilters.some(
        (cf) => cf.source_widget_id === w.id && cf.field_name === field && String(cf.value) === String(value)
      );
      const withoutThis = crossFilters.filter((cf) => cf.source_widget_id !== w.id);
      const nextCross = sameExact
        ? withoutThis
        : [...withoutThis, { field_name: field, operator: "eq", value, source_widget_id: w.id }];
      setCrossFilters(nextCross);
      await executePublicBatch(nextCross, drillStates);
    }
  };

  const handleDrillJump = async (w: any, targetLevel: number) => {
    const nextStack = (drillStates[w.id] || []).slice(0, targetLevel);
    const nextDrill = { ...drillStates, [w.id]: nextStack };
    setWidgetChartOverrides((prev) => {
      if (!(w.id in prev)) return prev;
      const copy = { ...prev };
      delete copy[w.id];
      return copy;
    });
    setDrillStates(nextDrill);
    await executePublicBatch(crossFilters, nextDrill);
  };

  const applyDrillToCrossFilter = async (w: any) => {
    const stack = drillStates[w.id] || [];
    if (stack.length === 0) return;
    const withoutThis = crossFilters.filter((cf) => cf.source_widget_id !== w.id);
    const nextCross = [
      ...withoutThis,
      ...stack.map((item) => ({
        field_name: item.field,
        operator: "eq",
        value: item.value,
        source_widget_id: w.id,
      })),
    ];
    setCrossFilters(nextCross);
    await executePublicBatch(nextCross, drillStates);
  };

  const resetAll = async () => {
    setCrossFilters([]);
    setDrillStates({});
    setWidgetChartOverrides({});
    await executePublicBatch([], {});
  };

  if (err) return <div className="p-10 text-center text-red-500 text-sm">{err}</div>;
  if (!data) return <div className="p-10 text-center text-sm opacity-60">Loading Shared Dashboard...</div>;

  const isFa = locale === "fa";

  return (
    <div
      dir={isFa ? "rtl" : "ltr"}
      className="min-h-screen p-4 md:p-6 space-y-4"
      style={{ background: "var(--bi-bg)", color: "var(--bi-text)" }}
    >
      {/* Top Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl md:text-2xl font-bold">{data.title}</h1>
          <p className="text-xs md:text-sm opacity-75">{data.description}</p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {loadingBatch && (
            <span className="text-xs px-2.5 py-1 rounded-full bg-blue-500/10 text-blue-500 flex items-center gap-1 font-semibold">
              <RefreshCw className="w-3 h-3 animate-spin" /> Updating...
            </span>
          )}
          <button
            onClick={() => setLocale(isFa ? "en" : "fa")}
            className="text-xs px-3 py-1.5 rounded-lg border font-semibold hover:opacity-80"
            style={{ background: "var(--bi-card)", borderColor: "var(--bi-border)" }}
          >
            {isFa ? "English" : "فارسی"}
          </button>
          <Link
            href="/"
            className="text-xs px-3 py-1.5 rounded-lg font-semibold text-white"
            style={{ background: "var(--bi-primary)" }}
          >
            {isFa ? "ورود به پنل کامل BI" : "Open Full BI Workspace"}
          </Link>
          <span className="text-xs px-3 py-1 rounded-full bg-blue-500/15 text-blue-600 font-semibold">
            {isFa ? "نمای عمومی تعاملی" : "Interactive Public View"}
          </span>
        </div>
      </div>

      {/* Interactive Analytics Guide Strip */}
      <div
        className="px-3.5 py-2.5 rounded-xl border text-xs flex flex-wrap items-center justify-between gap-2"
        style={{
          background: "color-mix(in srgb, var(--bi-primary) 7%, var(--bi-card))",
          borderColor: "color-mix(in srgb, var(--bi-primary) 26%, var(--bi-border))",
        }}
      >
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1">
          {isFa ? (
            <>
              <span>
                <strong className="text-blue-500">۱. دریل‌داون:</strong> روی میله‌های{" "}
                <span className="px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-500 font-semibold">Drill-Down</span> کلیک کنید (سال ← فصل ← ماه ← روز).
              </span>
              <span>
                <strong className="text-emerald-500">۲. فیلتر کل داشبورد:</strong> روی هر بخش نمودارهای{" "}
                <span className="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-500 font-semibold">Cross-Filter</span> کلیک کنید تا همه ویجت‌ها فیلتر شوند.
              </span>
              <span>
                <strong className="text-violet-500">۳. تبدیل ویجت بعد دریل‌داون:</strong> نمودار پس از دریل‌داون خودکار تغییر می‌کند و با منوی بالای ویجت به هر نوعی قابل تبدیل است.
              </span>
            </>
          ) : (
            <>
              <span>
                <strong className="text-blue-500">1. Drill-Down:</strong> Click bars on{" "}
                <span className="px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-500 font-semibold">Drill-Down</span> widgets (Year → Quarter → Month → Day).
              </span>
              <span>
                <strong className="text-emerald-500">2. Dashboard Cross-Filter:</strong> Click any slice/bar on{" "}
                <span className="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-500 font-semibold">Cross-Filter</span> widgets to filter every widget.
              </span>
              <span>
                <strong className="text-violet-500">3. Post-Drill Chart Conversion:</strong> Auto-converts per drill level and can be converted to any chart via the widget header selector.
              </span>
            </>
          )}
        </div>
        {(crossFilters.length > 0 ||
          Object.values(drillStates).some((s) => s.length > 0) ||
          Object.keys(widgetChartOverrides).length > 0) && (
          <button
            onClick={resetAll}
            className="px-2.5 py-1 rounded-lg bg-red-500/10 text-red-500 font-semibold hover:bg-red-500/20 shrink-0"
          >
            {isFa ? "بازنشانی همه" : "Reset All"}
          </button>
        )}
      </div>

      {/* Active Cross-Filter Pills */}
      {crossFilters.length > 0 && (
        <div className="bi-card px-3.5 py-2 flex flex-wrap items-center gap-2 text-xs">
          <span className="font-semibold opacity-75">
            {isFa ? "فیلتر فعال روی کل داشبورد:" : "Active Dashboard Cross-Filters:"}
          </span>
          {crossFilters.map((cf, idx) => (
            <span
              key={`${cf.field_name}-${cf.value}-${idx}`}
              className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 font-semibold"
            >
              {cf.field_name} = {String(cf.value)}
              <button
                onClick={async () => {
                  const nextCross = crossFilters.filter((_, i) => i !== idx);
                  setCrossFilters(nextCross);
                  await executePublicBatch(nextCross, drillStates);
                }}
                className="hover:opacity-70"
              >
                <X className="w-3 h-3" />
              </button>
            </span>
          ))}
        </div>
      )}

      {/* Widget Grid */}
      <div className="grid grid-cols-1 md:grid-cols-6 lg:grid-cols-12 gap-4">
        {data.widgets.map((w: any) => {
          const wd = widgetResults[w.id] || w.data || { columns: [], rows: [] };
          const autoType = wd.effective_chart_type || w.chart_type;
          const effType = widgetChartOverrides[w.id] || autoType;
          const drillCrumbs = wd.drill?.breadcrumbs || [];
          const hasDrill = (w.drill_rule?.hierarchy?.length || 0) > 0;
          const isDrilled = drillCrumbs.length > 1;
          const clickMode = getWidgetClickMode(w);
          const isCrossSource = crossFilters.some((cf) => cf.source_widget_id === w.id);
          const canCrossFilter =
            w.config?.interactions?.cross_filter !== false &&
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
              "table",
            ])
          );
          const lgSpan = Math.min(12, Math.max(3, Number(w.layout?.w || 4)));
          const colClass =
            lgSpan >= 10
              ? "md:col-span-6 lg:col-span-12"
              : lgSpan >= 7
              ? "md:col-span-6 lg:col-span-8"
              : lgSpan >= 5
              ? "md:col-span-6 lg:col-span-6"
              : lgSpan <= 3
              ? "md:col-span-3 lg:col-span-3"
              : "md:col-span-3 lg:col-span-4";
          const heightClass = w.chart_type === "kpi" ? "h-40" : "h-80";

          return (
            <div
              key={w.id}
              className={`bi-card p-4 flex flex-col ${colClass} ${heightClass} ${
                isCrossSource ? "ring-2 ring-emerald-500/70" : ""
              }`}
            >
              <div className="mb-2 pb-2 border-b flex items-center justify-between gap-2" style={{ borderColor: "var(--bi-border)" }}>
                <div className="min-w-0">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <h3 className="text-xs font-bold truncate">{w.title}</h3>
                    {hasDrill && canCrossFilter ? (
                      <button
                        type="button"
                        onClick={() => {
                          const nextMode = clickMode === "drill" ? "cross_filter" : "drill";
                          setClickModes((prev) => ({ ...prev, [w.id]: nextMode }));
                        }}
                        className={`text-[9px] px-1.5 py-0.5 rounded font-semibold transition ${
                          clickMode === "drill"
                            ? "bg-blue-500/15 text-blue-500 hover:bg-blue-500/25"
                            : isCrossSource
                            ? "bg-emerald-500 text-white"
                            : "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 hover:bg-emerald-500/25"
                        }`}
                        title="Click badge to toggle between Drill-Down and Dashboard Cross-Filter"
                      >
                        {clickMode === "drill"
                          ? "Drill-Down ⇄"
                          : isCrossSource
                          ? "Filtering Dashboard ⇄"
                          : "Cross-Filter ⇄"}
                      </button>
                    ) : hasDrill ? (
                      <span className="text-[9px] px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-500 font-semibold">
                        Drill-Down
                      </span>
                    ) : canCrossFilter ? (
                      <span
                        className={`text-[9px] px-1.5 py-0.5 rounded font-semibold ${
                          isCrossSource
                            ? "bg-emerald-500 text-white"
                            : "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                        }`}
                      >
                        {isCrossSource ? "Filtering Dashboard" : "Cross-Filter"}
                      </span>
                    ) : null}
                    {isDrilled && (
                      <span className="text-[9px] px-1.5 py-0.5 rounded bg-violet-500/15 text-violet-500 font-semibold">
                        {w.chart_type} → {effType}
                      </span>
                    )}
                  </div>
                  {isDrilled && (
                    <div className="flex items-center gap-1 text-[10px] mt-0.5 flex-wrap">
                      {drillCrumbs.map((bc: any, idx: number) => (
                        <React.Fragment key={bc.level}>
                          {idx > 0 && <span>/</span>}
                          <button
                            onClick={() => handleDrillJump(w, bc.level)}
                            className="hover:underline text-blue-500 font-semibold"
                          >
                            {bc.label}
                          </button>
                        </React.Fragment>
                      ))}
                    </div>
                  )}
                </div>

                <div className="flex items-center gap-1 shrink-0">
                  {isDrilled && (
                    <>
                      <button
                        onClick={() => applyDrillToCrossFilter(w)}
                        className={`text-[10px] px-1.5 py-0.5 rounded border font-semibold flex items-center gap-1 ${
                          isCrossSource
                            ? "bg-emerald-500 text-white border-emerald-500"
                            : "border-emerald-500/40 text-emerald-600 dark:text-emerald-400 hover:bg-emerald-500/10"
                        }`}
                        title={isFa ? "اعمال روی کل داشبورد" : "Filter entire dashboard by this drill level"}
                      >
                        <Filter className="w-2.5 h-2.5" />
                        <span>{isFa ? "فیلتر داشبورد" : "Filter All"}</span>
                      </button>
                      <button
                        onClick={() => handleDrillJump(w, (drillStates[w.id] || []).length - 1)}
                        className="p-1 rounded hover:bg-blue-500/10 text-blue-500"
                        title="Drill Up"
                      >
                        <ArrowUp className="w-3.5 h-3.5" />
                      </button>
                    </>
                  )}
                  {canConvertChart && (
                    <select
                      aria-label={`Convert ${w.title} chart type`}
                      value={effType}
                      onChange={(e) => setWidgetChartOverrides((prev) => ({ ...prev, [w.id]: e.target.value }))}
                      className={`text-[10px] px-1.5 py-0.5 rounded border font-semibold cursor-pointer ${
                        isDrilled ? "border-violet-500/60 text-violet-500" : ""
                      }`}
                      style={{ background: "var(--bi-surface)", borderColor: isDrilled ? undefined : "var(--bi-border)" }}
                      title={isFa ? "تبدیل نوع ویجت" : "Convert widget type (works after drill-down too)"}
                    >
                      {convertOptions.map((ct) => {
                        const meta = CHART_REGISTRY.find((c) => c.id === ct);
                        return (
                          <option key={ct} value={ct}>
                            {meta ? (isFa ? meta.labelFa : meta.label) : ct}
                          </option>
                        );
                      })}
                    </select>
                  )}
                </div>
              </div>

              <div className="flex-1 min-h-0">
                <WidgetRenderer
                  chartType={effType}
                  columns={wd.columns || []}
                  rows={wd.rows || []}
                  styleConfig={w.config?.style || {}}
                  vizConfig={w.config?.visualization || {}}
                  visualSpec={w.query?.visual_spec}
                  onPointClick={(field, val) => handlePointClick(w, field, val)}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
