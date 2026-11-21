"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, Bot, FolderKanban, Plus, Trash2 } from "lucide-react";
import AppShell from "@/components/layout/AppShell";
import { apiFetch } from "@/lib/api";
import { useBi } from "@/lib/context";

export default function DashboardsListPage() {
  const { user, locale, showToast } = useBi();
  const isFa = locale === "fa";
  const [dashboards, setDashboards] = useState<any[]>([]);
  const [datasets, setDatasets] = useState<any[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [title, setTitle] = useState("");
  const [desc, setDesc] = useState("");

  const load = () => {
    apiFetch("/dashboards").then(setDashboards).catch(() => {});
    apiFetch("/datasets").then(setDatasets).catch(() => {});
  };

  useEffect(() => {
    if (user) load();
  }, [user]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const res = await apiFetch<{ id: string }>("/dashboards", {
        method: "POST",
        body: JSON.stringify({ title, description: desc }),
      });
      window.location.href = `/dashboards/${res.id}`;
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await apiFetch(`/dashboards/${id}`, { method: "DELETE" });
      showToast("Dashboard deleted");
      load();
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleAiGen = async (dsId: string) => {
    try {
      const res = await apiFetch<{ dashboard_id: string }>("/ai/generate-dashboard", {
        method: "POST",
        body: JSON.stringify({ dataset_id: dsId }),
      });
      window.location.href = `/dashboards/${res.dashboard_id}`;
    } catch (err: any) {
      showToast(err.message);
    }
  };

  return (
    <AppShell>
      <div className="space-y-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-bold flex items-center gap-2">
              <FolderKanban className="w-5 h-5" style={{ color: "var(--bi-primary)" }} />{" "}
              {isFa ? "داشبوردها و قالب‌ها" : "Dashboards"}
            </h1>
            <p className="text-xs mt-1" style={{ color: "var(--bi-muted)" }}>
              {isFa
                ? "تنها داشبوردهای مجاز بر اساس سیاست دسترسی RBAC نمایش داده می‌شوند."
                : "Only dashboards permitted by your RBAC & Resource Permission policy appear here."}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Link
              href="/reports"
              className="px-3.5 py-2 rounded-lg border text-xs font-semibold flex items-center gap-1.5"
              style={{ borderColor: "var(--bi-border)", background: "var(--bi-card)" }}
            >
              {isFa ? "گالری قالب‌ها" : "Templates Gallery"}
            </Link>
            <button
              onClick={() => setShowCreate((s) => !s)}
              className="px-4 py-2 rounded-lg text-xs font-semibold text-white flex items-center gap-1.5"
              style={{ background: "var(--bi-primary)" }}
            >
              <Plus className="w-4 h-4" /> {isFa ? "داشبورد جدید" : "New Dashboard"}
            </button>
          </div>
        </div>

        {showCreate && (
          <form onSubmit={handleCreate} className="bi-card p-4 sm:p-5 grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={isFa ? "عنوان داشبورد" : "Dashboard Title"}
              className="px-3 py-2 rounded-lg border bg-transparent"
              style={{ borderColor: "var(--bi-border)" }}
              required
            />
            <input
              value={desc}
              onChange={(e) => setDesc(e.target.value)}
              placeholder={isFa ? "توضیحات" : "Description"}
              className="px-3 py-2 rounded-lg border bg-transparent"
              style={{ borderColor: "var(--bi-border)" }}
            />
            <button type="submit" className="px-4 py-2 rounded-lg text-white font-semibold" style={{ background: "var(--bi-primary)" }}>
              {isFa ? "ساخت و ورود به طراح" : "Create & Open Designer"}
            </button>
          </form>
        )}

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 sm:gap-4">
          {dashboards.map((d) => (
            <div key={d.id} className="bi-card p-4 sm:p-5 flex flex-col justify-between gap-3 sm:gap-4">
              <div>
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2 flex-wrap">
                    <Link href={`/dashboards/${d.id}`} className="text-sm sm:text-base font-bold hover:underline">
                      {d.title}
                    </Link>
                    {d.is_template && (
                      <span className="text-[10px] px-2 py-0.5 rounded bg-violet-500/15 text-violet-500 font-semibold">
                        TEMPLATE
                      </span>
                    )}
                  </div>
                  <button onClick={() => handleDelete(d.id)} className="text-red-500 p-1 hover:bg-red-500/10 rounded shrink-0">
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
                <p className="text-xs mt-1.5" style={{ color: "var(--bi-muted)" }}>{d.description}</p>
              </div>
              <div className="flex items-center justify-between pt-3 border-t text-xs" style={{ borderColor: "var(--bi-border)" }}>
                <span style={{ color: "var(--bi-muted)" }}>{d.widget_count} {isFa ? "ویجت" : "widgets"}</span>
                <Link href={`/dashboards/${d.id}`} className="font-bold flex items-center gap-1" style={{ color: "var(--bi-primary)" }}>
                  {isFa ? "باز کردن" : "Launch"} <ArrowRight className={`w-3.5 h-3.5 ${isFa ? "rotate-180" : ""}`} />
                </Link>
              </div>
            </div>
          ))}
        </div>

        {datasets.length > 0 && (
          <div className="bi-card p-5 space-y-3">
            <div className="flex items-center gap-2 text-sm font-bold">
              <Bot className="w-4 h-4 text-violet-500" /> Generate AI Dashboard from Dataset
            </div>
            <div className="flex flex-wrap gap-2">
              {datasets.map((ds) => (
                <button
                  key={ds.id}
                  onClick={() => handleAiGen(ds.id)}
                  className="px-3 py-1.5 rounded-lg border text-xs font-semibold hover:bg-violet-500/10"
                  style={{ borderColor: "var(--bi-border)" }}
                >
                  + AI Dashboard from &ldquo;{ds.name}&rdquo;
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
