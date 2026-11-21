"use client";

import React, { createContext, useContext, useEffect, useState } from "react";
import { apiFetch, getStoredToken, setStoredToken } from "@/lib/api";
import { Locale, TRANSLATIONS } from "@/lib/i18n";
import { BiTheme, ThemeTokens } from "@/types/bi";

const DEFAULT_TOKENS: ThemeTokens = {
  primary: "#2563eb",
  secondary: "#0ea5e9",
  background: "#f1f5f9",
  surface: "#ffffff",
  card_bg: "#ffffff",
  text: "#0f172a",
  muted_text: "#64748b",
  border: "#e2e8f0",
  radius: 12,
  shadow: "0 4px 20px rgba(15, 23, 42, 0.06)",
  blur: 0,
  font_family: "Inter, Vazirmatn, sans-serif",
  chart_palette: ["#2563eb", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#ec4899"],
};

export interface AuthStatusInfo {
  needs_initial_setup: boolean;
  demo_mode: boolean;
  setup_token_required: boolean;
  environment?: string;
}

interface BiContextType {
  user: any | null;
  authStatus: AuthStatusInfo | null;
  loadingAuth: boolean;
  locale: Locale;
  setLocale: (l: Locale) => void;
  t: (key: string) => string;
  themes: BiTheme[];
  activeThemeTokens: ThemeTokens;
  activeThemeMode: "light" | "dark";
  applyThemeTokens: (tokens: ThemeTokens, mode?: "light" | "dark") => void;
  refreshUser: () => Promise<void>;
  refreshThemes: () => Promise<void>;
  logout: () => Promise<void>;
  toast: string | null;
  showToast: (msg: string) => void;
}

const BiContext = createContext<BiContextType | undefined>(undefined);

export function BiProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<any | null>(null);
  const [authStatus, setAuthStatus] = useState<AuthStatusInfo | null>(null);
  const [loadingAuth, setLoadingAuth] = useState(true);
  const [locale, setLocaleState] = useState<Locale>("en");
  const [themes, setThemes] = useState<BiTheme[]>([]);
  const [activeThemeTokens, setActiveThemeTokens] = useState<ThemeTokens>(DEFAULT_TOKENS);
  const [activeThemeMode, setActiveThemeMode] = useState<"light" | "dark">("light");
  const [toast, setToast] = useState<string | null>(null);

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast((curr) => (curr === msg ? null : curr)), 3500);
  };

  const applyThemeTokens = (tokens: ThemeTokens, mode: "light" | "dark" = "light") => {
    setActiveThemeTokens(tokens);
    setActiveThemeMode(mode);
    if (typeof document !== "undefined") {
      const r = document.documentElement.style;
      const isRtl = document.documentElement.dir === "rtl";
      r.setProperty("--bi-primary", tokens.primary);
      r.setProperty("--bi-secondary", tokens.secondary);
      r.setProperty("--bi-bg", tokens.background);
      r.setProperty("--bi-surface", tokens.surface);
      r.setProperty("--bi-card", tokens.card_bg);
      r.setProperty("--bi-text", tokens.text);
      r.setProperty("--bi-muted", tokens.muted_text);
      r.setProperty("--bi-border", tokens.border);
      r.setProperty("--bi-radius", `${tokens.radius}px`);
      r.setProperty("--bi-shadow", tokens.shadow);
      r.setProperty("--bi-blur", `${tokens.blur || 0}px`);
      r.setProperty(
        "--bi-font",
        isRtl ? "Vazirmatn, Inter, sans-serif" : (tokens.font_family || "Inter, Vazirmatn, sans-serif")
      );
    }
  };

  const setLocale = (l: Locale) => {
    setLocaleState(l);
    if (typeof window !== "undefined") {
      localStorage.setItem("bi_locale", l);
      document.documentElement.dir = l === "fa" ? "rtl" : "ltr";
      document.documentElement.lang = l;
      document.documentElement.style.setProperty(
        "--bi-font",
        l === "fa" ? "Vazirmatn, Inter, sans-serif" : (activeThemeTokens.font_family || "Inter, Vazirmatn, sans-serif")
      );
    }
  };

  const t = (key: string) => TRANSLATIONS[locale]?.[key] || TRANSLATIONS.en[key] || key;

  const refreshThemes = async () => {
    try {
      const ths = await apiFetch<BiTheme[]>("/themes");
      setThemes(ths);
    } catch {
      // ignore if limited role
    }
  };

  const refreshAuthStatus = async () => {
    try {
      const st = await apiFetch<AuthStatusInfo>("/auth/status");
      setAuthStatus(st);
      return st;
    } catch {
      return null;
    }
  };

  const refreshUser = async () => {
    const status = await refreshAuthStatus();
    if (status?.needs_initial_setup) {
      setStoredToken(null);
      setUser(null);
      setLoadingAuth(false);
      return;
    }
    const tok = getStoredToken();
    if (!tok) {
      setUser(null);
      setLoadingAuth(false);
      return;
    }
    try {
      const me = await apiFetch("/auth/me");
      setUser(me);
      await refreshThemes();
    } catch {
      setStoredToken(null);
      setUser(null);
    } finally {
      setLoadingAuth(false);
    }
  };

  const logout = async () => {
    try {
      await apiFetch("/auth/logout", { method: "POST" });
    } catch {
      // ignore
    }
    setStoredToken(null);
    setUser(null);
    await refreshAuthStatus();
  };

  useEffect(() => {
    const savedLoc = typeof window !== "undefined" ? (localStorage.getItem("bi_locale") as Locale) : null;
    if (savedLoc === "fa" || savedLoc === "en") {
      setLocale(savedLoc);
    }
    applyThemeTokens(DEFAULT_TOKENS, "light");
    refreshUser();
  }, []);

  return (
    <BiContext.Provider
      value={{
        user,
        authStatus,
        loadingAuth,
        locale,
        setLocale,
        t,
        themes,
        activeThemeTokens,
        activeThemeMode,
        applyThemeTokens,
        refreshUser,
        refreshThemes,
        logout,
        toast,
        showToast,
      }}
    >
      {children}
      {toast && (
        <div
          className="fixed bottom-4 inset-x-4 sm:inset-x-auto sm:bottom-5 sm:end-5 sm:max-w-sm z-[100] px-4 py-3 rounded-xl text-xs sm:text-sm font-medium shadow-xl border transition-all text-center sm:text-start"
          style={{
            background: "var(--bi-surface)",
            color: "var(--bi-text)",
            borderColor: "var(--bi-primary)",
          }}
        >
          {toast}
        </div>
      )}
    </BiContext.Provider>
  );
}

export function useBi() {
  const ctx = useContext(BiContext);
  if (!ctx) throw new Error("useBi must be used inside BiProvider");
  return ctx;
}
