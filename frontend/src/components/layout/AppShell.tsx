"use client";

import React, { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  BarChart3,
  Database,
  FileSpreadsheet,
  FolderKanban,
  Home,
  Languages,
  Layers,
  Lock,
  LogOut,
  Menu,
  Palette,
  RefreshCw,
  ShieldCheck,
  Terminal,
  X,
} from "lucide-react";
import { apiFetch, setStoredToken } from "@/lib/api";
import { useBi } from "@/lib/context";

function LoginScreen() {
  const { t, locale, setLocale, refreshUser, showToast } = useBi();
  const [authMode, setAuthMode] = useState<"local" | "active_directory">("local");
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("Admin!2026");
  const [domain, setDomain] = useState("corp.enterprise.local");
  const [mfaRequired, setMfaRequired] = useState(false);
  const [mfaCode, setMfaCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const res = await apiFetch("/auth/login", {
        method: "POST",
        body: JSON.stringify({
          username,
          password,
          auth_mode: authMode,
          domain: authMode === "active_directory" ? domain : undefined,
          mfa_code: mfaRequired ? mfaCode : undefined,
        }),
      });
      if (res.mfa_required) {
        setMfaRequired(true);
        showToast("Enter your 6-digit MFA code");
        return;
      }
      setStoredToken(res.access_token);
      await refreshUser();
    } catch (err: any) {
      setError(err.message || "Authentication failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-4" style={{ background: "var(--bi-bg)" }}>
      <div className="bi-card w-full max-w-md p-8 space-y-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div
              className="w-10 h-10 rounded-xl flex items-center justify-center text-white font-bold"
              style={{ background: "var(--bi-primary)" }}
            >
              <BarChart3 className="w-5 h-5" />
            </div>
            <div>
              <h1 className="text-lg font-bold">{t("brand")}</h1>
              <p className="text-xs" style={{ color: "var(--bi-muted)" }}>{t("tagline")}</p>
            </div>
          </div>
          <button
            onClick={() => setLocale(locale === "en" ? "fa" : "en")}
            className="px-2.5 py-1.5 rounded-lg border text-xs font-medium flex items-center gap-1"
            style={{ borderColor: "var(--bi-border)" }}
          >
            <Languages className="w-3.5 h-3.5" />
            {locale === "en" ? "فارسی" : "EN"}
          </button>
        </div>

        <div className="flex rounded-lg p-1 border" style={{ borderColor: "var(--bi-border)" }}>
          <button
            type="button"
            onClick={() => { setAuthMode("local"); setUsername("admin"); setPassword("Admin!2026"); }}
            className="flex-1 py-1.5 text-xs font-semibold rounded-md transition"
            style={{
              background: authMode === "local" ? "var(--bi-primary)" : "transparent",
              color: authMode === "local" ? "#fff" : "var(--bi-muted)",
            }}
          >
            {t("localAuth")}
          </button>
          <button
            type="button"
            onClick={() => { setAuthMode("active_directory"); setUsername("sara.ahmadi"); setPassword("DomainUser!123"); }}
            className="flex-1 py-1.5 text-xs font-semibold rounded-md transition"
            style={{
              background: authMode === "active_directory" ? "var(--bi-primary)" : "transparent",
              color: authMode === "active_directory" ? "#fff" : "var(--bi-muted)",
            }}
          >
            {t("adAuth")}
          </button>
        </div>

        <form onSubmit={handleLogin} className="space-y-4">
          {authMode === "active_directory" && (
            <div>
              <label className="block text-xs font-semibold mb-1">Domain</label>
              <input
                value={domain}
                onChange={(e) => setDomain(e.target.value)}
                className="w-full px-3 py-2 rounded-lg border text-sm bg-transparent"
                style={{ borderColor: "var(--bi-border)" }}
              />
            </div>
          )}
          <div>
            <label className="block text-xs font-semibold mb-1">Username</label>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full px-3 py-2 rounded-lg border text-sm bg-transparent"
              style={{ borderColor: "var(--bi-border)" }}
              required
            />
          </div>
          <div>
            <label className="block text-xs font-semibold mb-1">Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-3 py-2 rounded-lg border text-sm bg-transparent"
              style={{ borderColor: "var(--bi-border)" }}
              required
            />
          </div>
          {mfaRequired && (
            <div>
              <label className="block text-xs font-semibold mb-1">MFA TOTP Code</label>
              <input
                value={mfaCode}
                onChange={(e) => setMfaCode(e.target.value)}
                placeholder="123456"
                className="w-full px-3 py-2 rounded-lg border text-sm bg-transparent"
                style={{ borderColor: "var(--bi-border)" }}
                required
              />
            </div>
          )}
          {error && <div className="text-xs text-red-500 font-medium">{error}</div>}
          <button
            type="submit"
            disabled={busy}
            className="w-full py-2.5 rounded-lg text-sm font-semibold text-white transition flex items-center justify-center gap-2"
            style={{ background: "var(--bi-primary)" }}
          >
            <Lock className="w-4 h-4" />
            {busy ? "..." : t("signIn")}
          </button>
        </form>

        <div className="pt-3 border-t text-xs space-y-1.5" style={{ borderColor: "var(--bi-border)", color: "var(--bi-muted)" }}>
          <div className="font-semibold">Quick Demo Accounts (Enterprise Roles):</div>
          <div className="grid grid-cols-2 gap-1.5">
            <button type="button" onClick={() => { setAuthMode("local"); setUsername("admin"); setPassword("Admin!2026"); }} className="text-left px-2 py-1 rounded border hover:opacity-80" style={{ borderColor: "var(--bi-border)" }}>
              admin / Admin!2026 (Super Admin)
            </button>
            <button type="button" onClick={() => { setAuthMode("local"); setUsername("designer"); setPassword("Designer!2026"); }} className="text-left px-2 py-1 rounded border hover:opacity-80" style={{ borderColor: "var(--bi-border)" }}>
              designer / Designer!2026
            </button>
            <button type="button" onClick={() => { setAuthMode("local"); setUsername("north_mgr"); setPassword("Manager!2026"); }} className="text-left px-2 py-1 rounded border hover:opacity-80" style={{ borderColor: "var(--bi-border)" }}>
              north_mgr (RLS North)
            </button>
            <button type="button" onClick={() => { setAuthMode("local"); setUsername("south_mgr"); setPassword("Manager!2026"); }} className="text-left px-2 py-1 rounded border hover:opacity-80" style={{ borderColor: "var(--bi-border)" }}>
              south_mgr (RLS South)
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function AppShell({ children }: { children: React.ReactNode }) {
  const { user, loadingAuth, t, locale, setLocale, logout } = useBi();
  const pathname = usePathname();
  const [sidebarOpen, setSidebarOpen] = useState(false);

  if (loadingAuth) {
    return (
      <div className="min-h-screen flex items-center justify-center text-sm" style={{ color: "var(--bi-muted)" }}>
        Loading Enterprise BI Platform...
      </div>
    );
  }

  if (!user) {
    return <LoginScreen />;
  }

  const navItems = [
    { href: "/", label: t("home"), icon: Home },
    { href: "/dashboards", label: t("dashboards"), icon: FolderKanban },
    { href: "/data-sources", label: t("dataSources"), icon: Database },
    { href: "/datasets", label: t("datasets"), icon: Layers },
    { href: "/query-builder", label: t("queryBuilder"), icon: Terminal },
    { href: "/etl", label: t("etl"), icon: RefreshCw },
    { href: "/reports", label: t("reports"), icon: FileSpreadsheet },
    { href: "/admin/security", label: t("security"), icon: ShieldCheck },
    { href: "/admin/monitoring", label: t("monitoring"), icon: Activity },
    { href: "/settings", label: t("settings"), icon: Palette },
  ];

  return (
    <div className="min-h-screen flex" style={{ background: "var(--bi-bg)", color: "var(--bi-text)" }}>
      {/* Mobile Sidebar Backdrop */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/40 z-30 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside
        className={`fixed lg:sticky top-0 h-screen w-64 shrink-0 z-40 bi-surface border-e flex flex-col justify-between transition-transform duration-200 ${
          sidebarOpen ? "translate-x-0" : "-translate-x-full lg:translate-x-0"
        }`}
        style={{ borderColor: "var(--bi-border)" }}
      >
        <div>
          <div className="h-14 px-4 flex items-center justify-between border-b" style={{ borderColor: "var(--bi-border)" }}>
            <Link href="/" className="flex items-center gap-2.5 font-bold text-sm">
              <div
                className="w-8 h-8 rounded-lg flex items-center justify-center text-white"
                style={{ background: "var(--bi-primary)" }}
              >
                <BarChart3 className="w-4 h-4" />
              </div>
              <span className="truncate">{t("brand")}</span>
            </Link>
            <button onClick={() => setSidebarOpen(false)} className="lg:hidden p-1">
              <X className="w-4 h-4" />
            </button>
          </div>

          <nav className="p-3 space-y-1 overflow-y-auto max-h-[calc(100vh-140px)]">
            {navItems.map((item) => {
              const active =
                item.href === "/" ? pathname === "/" : pathname?.startsWith(item.href);
              const Icon = item.icon;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  onClick={() => setSidebarOpen(false)}
                  className="flex items-center gap-2.5 px-3 py-2 rounded-lg text-xs font-medium transition"
                  style={{
                    background: active ? "var(--bi-primary)" : "transparent",
                    color: active ? "#ffffff" : "var(--bi-text)",
                  }}
                >
                  <Icon className="w-4 h-4 shrink-0" />
                  <span className="truncate">{item.label}</span>
                </Link>
              );
            })}
          </nav>
        </div>

        <div className="p-3 border-t space-y-2" style={{ borderColor: "var(--bi-border)" }}>
          <div className="px-2 py-1.5 rounded-lg text-xs" style={{ background: "rgba(100,116,139,0.08)" }}>
            <div className="font-semibold truncate">{user.profile?.full_name || user.username}</div>
            <div className="text-[11px] truncate" style={{ color: "var(--bi-muted)" }}>
              {(user.effective_permissions?.roles || []).join(", ") || "user"}
              {user.profile?.region ? ` · ${user.profile.region}` : ""}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setLocale(locale === "en" ? "fa" : "en")}
              className="flex-1 py-1.5 px-2 rounded-lg border text-xs font-medium flex items-center justify-center gap-1 hover:opacity-80"
              style={{ borderColor: "var(--bi-border)" }}
            >
              <Languages className="w-3.5 h-3.5" />
              {locale === "en" ? "فارسی (RTL)" : "English (LTR)"}
            </button>
            <button
              onClick={logout}
              title={t("logout")}
              className="py-1.5 px-2.5 rounded-lg border text-xs text-red-500 hover:bg-red-500/10"
              style={{ borderColor: "var(--bi-border)" }}
            >
              <LogOut className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      </aside>

      {/* Main Content */}
      <div className="flex-1 min-w-0 flex flex-col">
        <header
          className="h-14 px-4 lg:hidden flex items-center justify-between border-b bi-surface sticky top-0 z-20"
          style={{ borderColor: "var(--bi-border)" }}
        >
          <button onClick={() => setSidebarOpen(true)} className="p-1.5 rounded-lg border" style={{ borderColor: "var(--bi-border)" }}>
            <Menu className="w-4 h-4" />
          </button>
          <span className="font-bold text-sm">{t("brand")}</span>
          <button onClick={() => setLocale(locale === "en" ? "fa" : "en")} className="text-xs font-semibold">
            {locale === "en" ? "FA" : "EN"}
          </button>
        </header>
        <main className="flex-1 min-w-0 p-4 lg:p-6">{children}</main>
      </div>
    </div>
  );
}
