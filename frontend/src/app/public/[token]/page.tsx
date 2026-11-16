"use client";

import React, { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import WidgetRenderer from "@/components/charts/WidgetRenderer";
import { apiFetch } from "@/lib/api";

export default function PublicDashboardPage() {
  const params = useParams();
  const token = String(params?.token || "");
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    apiFetch(`/dashboards/public/${token}`)
      .then(setData)
      .catch((e) => setErr(e.message));
  }, [token]);

  if (err) return <div className="p-10 text-center text-red-500 text-sm">{err}</div>;
  if (!data) return <div className="p-10 text-center text-sm opacity-60">Loading Shared Dashboard...</div>;

  return (
    <div className="min-h-screen p-6 space-y-6" style={{ background: data.theme?.tokens?.background || "#f1f5f9", color: data.theme?.tokens?.text || "#0f172a" }}>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">{data.title}</h1>
          <p className="text-sm opacity-75">{data.description}</p>
        </div>
        <span className="text-xs px-3 py-1 rounded-full bg-blue-500/15 text-blue-600 font-semibold">
          Embedded Public View
        </span>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {data.widgets.map((w: any) => (
          <div key={w.id} className="bi-card p-4 h-80 flex flex-col">
            <h3 className="text-xs font-bold mb-2 pb-2 border-b" style={{ borderColor: "var(--bi-border)" }}>{w.title}</h3>
            <div className="flex-1 min-h-0">
              <WidgetRenderer
                chartType={w.chart_type}
                columns={w.data?.columns || []}
                rows={w.data?.rows || []}
                styleConfig={w.config?.style || {}}
                vizConfig={w.config?.visualization || {}}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
