export interface ChartTypeMeta {
  id: string;
  label: string;
  labelFa: string;
  category: "Basic" | "Comparison" | "Part-to-Whole" | "Statistical" | "Specialized" | "KPI & Table";
  requiresSeries?: boolean;
  requiresSecondary?: boolean;
}

export const CHART_REGISTRY: ChartTypeMeta[] = [
  { id: "bar", label: "Bar Chart", labelFa: "نمودار ستونی", category: "Basic" },
  { id: "horizontal_bar", label: "Horizontal Bar", labelFa: "نمودار میله‌ای افقی", category: "Basic" },
  { id: "grouped_bar", label: "Grouped Bar", labelFa: "ستونی خوشه‌ای", category: "Comparison", requiresSeries: true },
  { id: "stacked_bar", label: "Stacked Bar", labelFa: "ستونی انباشته", category: "Comparison", requiresSeries: true },
  { id: "stacked_bar_100", label: "100% Stacked Bar", labelFa: "ستونی انباشته ۱۰۰٪", category: "Comparison", requiresSeries: true },
  { id: "line", label: "Line Chart", labelFa: "نمودار خطی", category: "Basic" },
  { id: "area", label: "Area Chart", labelFa: "نمودار مساحتی", category: "Basic" },
  { id: "stacked_area", label: "Stacked Area", labelFa: "مساحتی انباشته", category: "Comparison", requiresSeries: true },
  { id: "combo", label: "Combo Chart", labelFa: "نمودار ترکیبی", category: "Comparison", requiresSecondary: true },
  { id: "pie", label: "Pie Chart", labelFa: "نمودار دایره‌ای", category: "Part-to-Whole" },
  { id: "donut", label: "Donut Chart", labelFa: "نمودار دونات", category: "Part-to-Whole" },
  { id: "treemap", label: "Treemap", labelFa: "نقشه درختی", category: "Part-to-Whole" },
  { id: "sunburst", label: "Sunburst", labelFa: "نمودار خورشیدی", category: "Part-to-Whole" },
  { id: "funnel", label: "Funnel", labelFa: "نمودار قیفی", category: "Specialized" },
  { id: "waterfall", label: "Waterfall", labelFa: "نمودار آبشاری", category: "Specialized" },
  { id: "radar", label: "Radar Chart", labelFa: "نمودار راداری", category: "Comparison" },
  { id: "scatter", label: "Scatter Plot", labelFa: "نمودار پراکندگی", category: "Statistical", requiresSecondary: true },
  { id: "bubble", label: "Bubble Chart", labelFa: "نمودار حبابی", category: "Statistical", requiresSecondary: true },
  { id: "heatmap", label: "Heatmap", labelFa: "نقشه حرارتی", category: "Statistical", requiresSeries: true },
  { id: "calendar_heatmap", label: "Calendar Heatmap", labelFa: "حرارتی تقویمی", category: "Statistical" },
  { id: "histogram", label: "Histogram", labelFa: "هیستوگرام", category: "Statistical" },
  { id: "boxplot", label: "Box Plot", labelFa: "نمودار جعبه‌ای", category: "Statistical" },
  { id: "candlestick", label: "Candlestick", labelFa: "نمودار شمعی", category: "Statistical" },
  { id: "geo_map", label: "Geographic Map", labelFa: "نقشه جغرافیایی", category: "Specialized" },
  { id: "sankey", label: "Sankey Diagram", labelFa: "نمودار جریان سنکی", category: "Specialized", requiresSeries: true },
  { id: "bpmn", label: "BPMN Process & Bottlenecks", labelFa: "فرایند BPMN و گلوگاه‌ها", category: "Specialized" },
  { id: "gauge", label: "Gauge", labelFa: "عقربه‌ای (Gauge)", category: "KPI & Table" },
  { id: "bullet", label: "Bullet Chart", labelFa: "نمودار بولِت (هدف در برابر عملکرد)", category: "KPI & Table" },
  { id: "kpi", label: "KPI Card", labelFa: "کارت شاخص KPI", category: "KPI & Table" },
  { id: "progress", label: "Progress Bar", labelFa: "نوار پیشرفت", category: "KPI & Table" },
  { id: "table", label: "Data Table", labelFa: "جدول داده", category: "KPI & Table" },
  { id: "pivot_table", label: "Pivot Table", labelFa: "جدول محوری (Pivot)", category: "KPI & Table", requiresSeries: true },
  { id: "matrix", label: "Matrix", labelFa: "جدول ماتریسی", category: "KPI & Table", requiresSeries: true },
];

export function formatBIValue(
  raw: any,
  fmt: string = "number",
  decimals: number = 0,
  unit: string = "",
  abbreviate: boolean = false
): string {
  if (raw === null || raw === undefined || raw === "") return "—";
  const num = Number(raw);
  if (Number.isNaN(num)) return String(raw);

  if (abbreviate && Math.abs(num) >= 1_000_000) {
    const val = (num / 1_000_000).toFixed(Math.max(1, decimals));
    return fmt === "currency" ? `$${val}M` : `${val}M ${unit}`.trim();
  }
  if (abbreviate && Math.abs(num) >= 1_000) {
    const val = (num / 1_000).toFixed(Math.max(1, decimals));
    return fmt === "currency" ? `$${val}K` : `${val}K ${unit}`.trim();
  }

  const formatted = num.toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });

  if (fmt === "currency") return `$${formatted}`;
  if (fmt === "percentage") return `${formatted}%`;
  if (fmt === "custom" && unit) return `${formatted} ${unit}`;
  return unit ? `${formatted} ${unit}` : formatted;
}
