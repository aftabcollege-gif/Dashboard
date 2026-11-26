"use client";

import React, { useEffect, useState } from "react";
import { Check, Download, Info, Palette, Plus, Shield } from "lucide-react";
import AppShell from "@/components/layout/AppShell";
import { apiDownload, apiFetch } from "@/lib/api";
import { useBi } from "@/lib/context";
import { APP_VERSION, PREVIOUS_VERSION, RELEASE_DATE, RELEASE_NOTES } from "@/lib/version";

export default function SettingsPage() {
  const { user, locale, themes, activeThemeTokens, applyThemeTokens, refreshThemes, showToast } = useBi();
  const isFa = locale === "fa";
  const [aboutMeta, setAboutMeta] = useState<any>(null);

  useEffect(() => {
    apiFetch("/about")
      .then((res) => setAboutMeta(res))
      .catch(() => {});
  }, []);

  // Custom Theme Builder
  const [tName, setTName] = useState("");
  const [tMode, setTMode] = useState<"light" | "dark">("dark");
  const [tPrimary, setTPrimary] = useState("#3b82f6");
  const [tBg, setTBg] = useState("#0b1120");
  const [tCard, setTCard] = useState("#111827");
  const [tRadius, setTRadius] = useState("14px");

  // MFA Setup
  const [mfaSetup, setMfaSetup] = useState<{ secret: string; provisioning_uri: string } | null>(null);
  const [mfaCode, setMfaCode] = useState("");

  const handleSaveTheme = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await apiFetch("/themes", {
        method: "POST",
        body: JSON.stringify({
          name: tName || "Custom Enterprise Theme",
          mode: tMode,
        tokens_json: {
            ...activeThemeTokens,
            background: tBg,
            surface: tBg,
            card_bg: tCard,
            text: tMode === "dark" ? "#f8fafc" : "#0f172a",
            muted_text: tMode === "dark" ? "#94a3b8" : "#64748b",
            primary: tPrimary,
            border: tMode === "dark" ? "#1e293b" : "#e2e8f0",
            radius: parseInt(tRadius, 10) || 14,
          },
        }),
      });
      setTName("");
      showToast("Custom theme created!");
      refreshThemes();
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleMfaSetup = async () => {
    const res = await apiFetch<{ secret: string; provisioning_uri: string }>("/auth/mfa/setup", { method: "POST" });
    setMfaSetup(res);
  };

  const handleMfaConfirm = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await apiFetch("/auth/mfa/enable", { method: "POST", body: JSON.stringify({ code: mfaCode }) });
      showToast("TOTP MFA Enabled on your account!");
      setMfaSetup(null);
    } catch (err: any) {
      showToast(err.message);
    }
  };

  return (
    <AppShell>
      <div className="space-y-6">
        <div>
          <h1 className="text-xl font-bold flex items-center gap-2">
            <Palette className="w-5 h-5" style={{ color: "var(--bi-primary)" }} />{" "}
            {isFa ? "موتور تم و تنظیمات امنیتی" : "Theme Engine & Security Preferences"}
          </h1>
          <p className="text-xs mt-1" style={{ color: "var(--bi-muted)" }}>
            {isFa
              ? "از بین ۸ تم حرفه‌ای (Minimal، Liquid، Glassmorphism، Dark، Light، Neon، Corporate، Material) انتخاب کنید یا تم اختصاصی بسازید."
              : "Switch across 8 built-in enterprise themes (Minimal, Liquid, Glassmorphism, Dark, Light, Neon, Corporate, Material) or design custom themes."}
          </p>
        </div>

        {/* Built-in + Custom Theme Gallery */}
        <div className="bi-card p-4 sm:p-5 space-y-4">
          <h2 className="text-sm font-bold">{isFa ? `گالری تم‌ها (${themes.length})` : `Theme Gallery (${themes.length})`}</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-3 text-xs">
            {themes.map((th) => (
              <button
                key={th.id}
                onClick={() => {
                  applyThemeTokens(th.tokens, th.mode);
                  if (typeof window !== "undefined") localStorage.setItem("bi_global_theme_id", th.id);
                  showToast(isFa ? `تم به ${th.name} تغییر یافت` : `Previewing ${th.name}`);
                }}
                className="p-3.5 rounded-xl border text-start transition hover:scale-[1.01]"
                style={{
                  background: th.tokens.card_bg,
                  color: th.tokens.text,
                  borderColor: th.tokens.primary,
                }}
              >
                <div className="flex items-center justify-between font-bold">
                  <span>{th.name}</span>
                  <span className="w-3 h-3 rounded-full" style={{ background: th.tokens.primary }} />
                </div>
                <div className="text-[10px] opacity-75 mt-1 uppercase">{th.mode} · {th.is_builtin ? "Built-in" : "Custom"}</div>
                <div className="flex items-center justify-between mt-2">
                  <div className="flex gap-1">
                    {(th.tokens.chart_palette || []).slice(0, 5).map((c: string) => (
                      <span key={c} className="w-4 h-2 rounded-sm" style={{ background: c }} />
                    ))}
                  </div>
                  <span
                    role="button"
                    onClick={(ev) => { ev.stopPropagation(); apiDownload(`/themes/${th.id}/export`, `${th.slug}.json`); }}
                    className="text-[10px] opacity-80 hover:underline flex items-center gap-0.5"
                    title="Export Theme JSON"
                  >
                    <Download className="w-3 h-3" /> JSON
                  </span>
                </div>
              </button>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 text-xs">
          {/* Custom Theme Designer */}
          <form onSubmit={handleSaveTheme} className="bi-card p-5 space-y-3">
            <h2 className="text-sm font-bold flex items-center gap-1.5"><Plus className="w-4 h-4" /> Create Custom Theme</h2>
            <div className="grid grid-cols-2 gap-3">
              <input value={tName} onChange={(e) => setTName(e.target.value)} placeholder="Theme Name" className="px-3 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} required />
              <select value={tMode} onChange={(e) => setTMode(e.target.value as any)} className="px-3 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                <option value="dark">Dark Mode</option>
                <option value="light">Light Mode</option>
              </select>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div><label className="block mb-1 font-semibold">Primary Accent</label><input type="color" value={tPrimary} onChange={(e) => setTPrimary(e.target.value)} className="w-full h-9 rounded" /></div>
              <div><label className="block mb-1 font-semibold">Background</label><input type="color" value={tBg} onChange={(e) => setTBg(e.target.value)} className="w-full h-9 rounded" /></div>
              <div><label className="block mb-1 font-semibold">Card Surface</label><input type="color" value={tCard} onChange={(e) => setTCard(e.target.value)} className="w-full h-9 rounded" /></div>
            </div>
            <input value={tRadius} onChange={(e) => setTRadius(e.target.value)} placeholder="Border radius (e.g., 14px)" className="w-full px-3 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
            <button type="submit" className="w-full py-2 rounded-lg text-white font-semibold" style={{ background: "var(--bi-primary)" }}>
              Save Custom Theme
            </button>
          </form>

          {/* TOTP MFA Setup */}
          <div className="bi-card p-5 space-y-3">
            <h2 className="text-sm font-bold flex items-center gap-1.5"><Shield className="w-4 h-4 text-emerald-500" /> Two-Factor Authentication (TOTP MFA)</h2>
            <p className="opacity-75">
              Account: <b>{user?.username}</b> · MFA Status: <b>{user?.mfa_enabled ? "Enabled" : "Not Enabled"}</b>
            </p>
            <button onClick={handleMfaSetup} className="px-4 py-2 rounded-lg border font-semibold" style={{ borderColor: "var(--bi-border)" }}>
              Generate Authenticator Secret (OTP)
            </button>
            {mfaSetup && (
              <form onSubmit={handleMfaConfirm} className="space-y-2 pt-2 border-t" style={{ borderColor: "var(--bi-border)" }}>
                <div>Secret Key: <code className="font-bold select-all">{mfaSetup.secret}</code></div>
                <div className="text-[11px] opacity-75 break-all">{mfaSetup.provisioning_uri}</div>
                <div className="flex gap-2">
                  <input value={mfaCode} onChange={(e) => setMfaCode(e.target.value)} placeholder="Enter 6-digit OTP code" className="flex-1 px-3 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} required />
                  <button type="submit" className="px-4 py-1.5 rounded bg-emerald-600 text-white font-semibold flex items-center gap-1">
                    <Check className="w-3.5 h-3.5" /> Verify & Enable
                  </button>
                </div>
              </form>
            )}
          </div>
        </div>

        {/* About & Release Notes (Versioning Continuity) */}
        <section id="about" className="bi-card p-5 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b pb-3" style={{ borderColor: "var(--bi-border)" }}>
            <div>
              <h2 className="text-sm font-bold flex items-center gap-2">
                <Info className="w-4 h-4 text-blue-500" />
                {isFa ? "درباره سامانه و یادداشت انتشار (Release Notes)" : "About Platform & Release Notes"}
              </h2>
              <p className="text-[11px] mt-0.5" style={{ color: "var(--bi-muted)" }}>
                {isFa
                  ? "نسخه‌گذاری استاندارد Semantic Versioning با حفظ کامل داده‌ها و سازگاری افزایشی از نسخه ۱.۱.۰"
                  : "Semantic Versioning with backward-compatible incremental upgrades from v1.1.0"}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2 text-[11px]">
              <span className="px-2.5 py-1 rounded-full bg-blue-500/15 text-blue-500 font-mono font-bold">
                v{aboutMeta?.version || APP_VERSION}
              </span>
              <span className="px-2.5 py-1 rounded-full border font-mono" style={{ borderColor: "var(--bi-border)" }}>
                {isFa ? `نسخه قبلی: v${aboutMeta?.previous_version || PREVIOUS_VERSION}` : `Prev: v${PREVIOUS_VERSION}`}
              </span>
              <span className="px-2.5 py-1 rounded-full border" style={{ borderColor: "var(--bi-border)" }}>
                {aboutMeta?.release_date || RELEASE_DATE}
              </span>
            </div>
          </div>

          <div className="space-y-3">
            {RELEASE_NOTES.map((rel) => (
              <div
                key={rel.version}
                className="p-3.5 rounded-xl border space-y-2 text-xs"
                style={{ borderColor: "var(--bi-border)", background: "rgba(100,116,139,0.04)" }}
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="font-bold flex items-center gap-2">
                    <span className="px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-500 font-mono">v{rel.version}</span>
                    <span>{isFa ? rel.titleFa : rel.titleEn}</span>
                  </div>
                  <span className="text-[11px]" style={{ color: "var(--bi-muted)" }}>{rel.date}</span>
                </div>
                <ul className="list-disc ps-5 space-y-1 text-[11px]">
                  {rel.highlightsFa.map((item, idx) => (
                    <li key={idx}>{item}</li>
                  ))}
                </ul>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2 pt-1 text-[10px]" style={{ color: "var(--bi-muted)" }}>
                  <div>
                    <strong>{isFa ? "مهاجرت پایگاه‌داده:" : "DB Migration:"}</strong> {rel.migrations.join(", ")}
                  </div>
                  <div>
                    <strong>{isFa ? "تغییرات ناسازگار (Breaking):" : "Breaking Changes:"}</strong>{" "}
                    {rel.breakingChanges.length === 0 ? (isFa ? "ندارد (سازگاری کامل با گذشته)" : "None") : rel.breakingChanges.join(", ")}
                  </div>
                  <div>
                    <strong>{isFa ? "راهنمای ارتقا:" : "Upgrade:"}</strong> {rel.upgradeNotesFa}
                  </div>
                  <div>
                    <strong>{isFa ? "راهنمای بازگشت (Rollback):" : "Rollback:"}</strong> {rel.rollbackNotesFa}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>
    </AppShell>
  );
}
