"use client";

import React from "react";
import { useParams } from "next/navigation";
import AppShell from "@/components/layout/AppShell";
import DashboardWorkspace from "@/components/dashboard/DashboardWorkspace";

export default function DashboardDetailPage() {
  const params = useParams();
  const id = String(params?.id || "");
  return (
    <AppShell>
      <DashboardWorkspace dashboardId={id} />
    </AppShell>
  );
}
