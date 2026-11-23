"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Copy,
  KeyRound,
  Lock,
  Plus,
  RefreshCw,
  Server,
  ShieldAlert,
  ShieldCheck,
  Trash2,
  Unlock,
  Users,
} from "lucide-react";
import AppShell from "@/components/layout/AppShell";
import { apiFetch } from "@/lib/api";
import { useBi } from "@/lib/context";

interface LoadErrorItem {
  section: string;
  message: string;
}

interface MatrixData {
  rules: any[];
  dashboards: any[];
  widgets: any[];
}

function asArray<T = any>(value: any, fallbackKey?: string): T[] {
  if (Array.isArray(value)) return value;
  if (fallbackKey && value && Array.isArray(value[fallbackKey])) {
    return value[fallbackKey];
  }
  return [];
}

function normalizeMatrix(value: any): MatrixData {
  return {
    rules: Array.isArray(value?.rules) ? value.rules : [],
    dashboards: Array.isArray(value?.dashboards) ? value.dashboards : [],
    widgets: Array.isArray(value?.widgets) ? value.widgets : [],
  };
}

function errMessage(err: unknown, fallback = "Request failed"): string {
  if (err instanceof Error && err.message) return err.message;
  if (typeof err === "string" && err) return err;
  return fallback;
}

export default function SecurityAdminPage() {
  const { user, showToast, t } = useBi();
  const [tab, setTab] = useState<"users" | "matrix" | "ad" | "rls">("users");
  const [loading, setLoading] = useState<boolean>(true);
  const [loadErrors, setLoadErrors] = useState<LoadErrorItem[]>([]);
  const [actionError, setActionError] = useState<string>("");

  // Users, Roles, Groups
  const [users, setUsers] = useState<any[]>([]);
  const [roles, setRoles] = useState<any[]>([]);
  const [catalog, setCatalog] = useState<any[]>([]);
  const [groups, setGroups] = useState<any[]>([]);
  const [newUsername, setNewUsername] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [newFullName, setNewFullName] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newRoleId, setNewRoleId] = useState("");
  const [newRoleName, setNewRoleName] = useState("");
  const [newRoleSlug, setNewRoleSlug] = useState("");
  const [newGroupName, setNewGroupName] = useState("");
  const [newGroupRoleId, setNewGroupRoleId] = useState("");

  // Permission Matrix
  const [matrix, setMatrix] = useState<MatrixData | null>(null);
  const [matrixSubjectType, setMatrixSubjectType] = useState<"role" | "group" | "user">("role");
  const [matrixScope, setMatrixScope] = useState<"dashboards" | "widgets" | "catalog">("dashboards");
  const [matrixClickMode, setMatrixClickMode] = useState<"toggle" | "remove">("toggle");
  const [copySubjectType, setCopySubjectType] = useState<"role" | "group" | "user">("role");
  const [copySourceId, setCopySourceId] = useState("");
  const [copyTargetId, setCopyTargetId] = useState("");

  // AD Connections
  const [adConns, setAdConns] = useState<any[]>([]);
  const [adSyncJobs, setAdSyncJobs] = useState<any[]>([]);
  const [adSearchQ, setAdSearchQ] = useState("");
  const [adPreview, setAdPreview] = useState<any>(null);
  const [adSyncResult, setAdSyncResult] = useState<any>(null);

  // RLS & CLS Policies
  const [policies, setPolicies] = useState<any[]>([]);
  const [datasets, setDatasets] = useState<any[]>([]);
  const [rlsDs, setRlsDs] = useState("");
  const [rlsName, setRlsName] = useState("");
  const [rlsSubjectId, setRlsSubjectId] = useState("");
  const [rlsCol, setRlsCol] = useState("region");
  const [rlsOp, setRlsOp] = useState("eq");
  const [rlsVal, setRlsVal] = useState("North");

  const [clsDs, setClsDs] = useState("");
  const [clsCol, setClsCol] = useState("email");
  const [clsSubjectId, setClsSubjectId] = useState("");
  const [clsRule, setClsRule] = useState("partial");

  const loadAll = useCallback(async () => {
    setLoading(true);
    const errors: LoadErrorItem[] = [];

    const [
      usersRes,
      rolesRes,
      groupsRes,
      matrixRes,
      adRes,
      policiesRes,
      datasetsRes,
    ] = await Promise.allSettled([
      apiFetch("/rbac/users"),
      apiFetch("/rbac/roles"),
      apiFetch("/rbac/groups"),
      apiFetch("/rbac/permissions/matrix"),
      apiFetch<any>("/rbac/ad/connections"),
      apiFetch("/rbac/policies"),
      apiFetch("/datasets"),
    ]);

    if (usersRes.status === "fulfilled") {
      setUsers(asArray(usersRes.value, "users"));
    } else {
      errors.push({ section: "Users", message: errMessage(usersRes.reason) });
    }

    if (rolesRes.status === "fulfilled") {
      const roleList = asArray(rolesRes.value, "roles");
      const permCatalog = asArray(rolesRes.value?.catalog);
      setRoles(roleList);
      setCatalog(permCatalog);
      if (roleList[0]) {
        setNewRoleId((prev) => prev || roleList[0].id);
        setNewGroupRoleId((prev) => prev || roleList[0].id);
      }
    } else {
      errors.push({ section: "Roles", message: errMessage(rolesRes.reason) });
    }

    if (groupsRes.status === "fulfilled") {
      setGroups(asArray(groupsRes.value, "groups"));
    } else {
      errors.push({ section: "Groups", message: errMessage(groupsRes.reason) });
    }

    if (matrixRes.status === "fulfilled") {
      setMatrix(normalizeMatrix(matrixRes.value));
    } else {
      errors.push({ section: "Permission Matrix", message: errMessage(matrixRes.reason) });
    }

    if (adRes.status === "fulfilled") {
      setAdConns(asArray(adRes.value?.connections));
      setAdSyncJobs(asArray(adRes.value?.sync_jobs));
    } else {
      errors.push({ section: "Active Directory", message: errMessage(adRes.reason) });
    }

    if (policiesRes.status === "fulfilled") {
      setPolicies(asArray(policiesRes.value));
    } else {
      errors.push({ section: "RLS/CLS Policies", message: errMessage(policiesRes.reason) });
    }

    if (datasetsRes.status === "fulfilled") {
      const dsList = asArray(datasetsRes.value);
      setDatasets(dsList);
      if (dsList[0]) {
        setRlsDs((prev) => prev || dsList[0].id);
        setClsDs((prev) => prev || dsList[0].id);
      }
    } else {
      errors.push({ section: "Datasets", message: errMessage(datasetsRes.reason) });
    }

    setLoadErrors(errors);
    setLoading(false);
  }, []);

  useEffect(() => {
    if (user) {
      loadAll();
    }
  }, [user, loadAll]);

  const roleMap = useMemo(() => {
    const map: Record<string, any> = {};
    for (const r of roles) map[r.id] = r;
    return map;
  }, [roles]);

  const groupMap = useMemo(() => {
    const map: Record<string, any> = {};
    for (const g of groups) map[g.id] = g;
    return map;
  }, [groups]);

  const matrixSubjects = useMemo(() => {
    if (matrixSubjectType === "group") {
      return groups.map((g) => ({ id: g.id, label: g.name, sub: `${(g.member_user_ids || []).length} members` }));
    }
    if (matrixSubjectType === "user") {
      return users.map((u) => ({ id: u.id, label: u.username, sub: u.full_name || u.email || "" }));
    }
    return roles.map((r) => ({ id: r.id, label: r.display_name || r.name, sub: r.slug }));
  }, [matrixSubjectType, roles, groups, users]);

  const copySubjects = useMemo(() => {
    if (copySubjectType === "group") {
      return groups.map((g) => ({ id: g.id, label: g.name }));
    }
    if (copySubjectType === "user") {
      return users.map((u) => ({ id: u.id, label: u.username }));
    }
    return roles.map((r) => ({ id: r.id, label: r.display_name || r.name }));
  }, [copySubjectType, roles, groups, users]);

  const handleCreateUser = async (e: React.FormEvent) => {
    e.preventDefault();
    setActionError("");
    try {
      await apiFetch("/rbac/users", {
        method: "POST",
        body: JSON.stringify({
          username: newUsername,
          email: newEmail,
          full_name: newFullName,
          password: newPassword,
          role_ids: newRoleId ? [newRoleId] : [],
        }),
      });
      setNewUsername("");
      setNewEmail("");
      setNewFullName("");
      setNewPassword("");
      showToast("User created");
      await loadAll();
    } catch (err: any) {
      const msg = errMessage(err, "Failed to create user");
      setActionError(msg);
      showToast(msg);
    }
  };

  const handleToggleUserStatus = async (u: any, patch: Record<string, any>, toastMsg: string) => {
    setActionError("");
    try {
      await apiFetch(`/rbac/users/${u.id}`, {
        method: "PUT",
        body: JSON.stringify(patch),
      });
      showToast(toastMsg);
      await loadAll();
    } catch (err: any) {
      const msg = errMessage(err, "Failed to update user");
      setActionError(msg);
      showToast(msg);
    }
  };

  const handleCreateRole = async (e: React.FormEvent) => {
    e.preventDefault();
    setActionError("");
    const slug = (newRoleSlug || newRoleName).trim().toLowerCase().replace(/[^a-z0-9_]+/g, "_");
    try {
      await apiFetch("/rbac/roles", {
        method: "POST",
        body: JSON.stringify({
          name: newRoleName.trim(),
          slug,
          description: "Custom role",
          permission_ids: [],
        }),
      });
      setNewRoleName("");
      setNewRoleSlug("");
      showToast("Role created");
      await loadAll();
    } catch (err: any) {
      const msg = errMessage(err, "Failed to create role");
      setActionError(msg);
      showToast(msg);
    }
  };

  const handleCloneRole = async (roleId: string) => {
    setActionError("");
    try {
      await apiFetch(`/rbac/roles/${roleId}/clone`, { method: "POST" });
      showToast("Role cloned");
      await loadAll();
    } catch (err: any) {
      const msg = errMessage(err, "Failed to clone role");
      setActionError(msg);
      showToast(msg);
    }
  };

  const handleToggleRoleBasePerm = async (role: any, permId: string, hasPerm: boolean) => {
    setActionError("");
    const current: string[] = Array.isArray(role.permission_ids) ? role.permission_ids : [];
    const nextIds = hasPerm ? current.filter((id) => id !== permId) : [...current, permId];
    try {
      await apiFetch(`/rbac/roles/${role.id}`, {
        method: "PUT",
        body: JSON.stringify({
          name: role.name,
          slug: role.slug,
          description: role.description || "",
          permission_ids: nextIds,
        }),
      });
      showToast("Role permission updated & cache invalidated");
      await loadAll();
    } catch (err: any) {
      const msg = errMessage(err, "Failed to update role permissions");
      setActionError(msg);
      showToast(msg);
    }
  };

  const handleCreateGroup = async (e: React.FormEvent) => {
    e.preventDefault();
    setActionError("");
    try {
      await apiFetch("/rbac/groups", {
        method: "POST",
        body: JSON.stringify({
          name: newGroupName.trim(),
          description: "Local RBAC Group",
          role_ids: newGroupRoleId ? [newGroupRoleId] : [],
          member_user_ids: [],
        }),
      });
      setNewGroupName("");
      showToast("Group created");
      await loadAll();
    } catch (err: any) {
      const msg = errMessage(err, "Failed to create group");
      setActionError(msg);
      showToast(msg);
    }
  };

  const toggleMatrixCell = async (
    subjectType: string,
    subjectId: string,
    resourceType: string,
    resourceId: string,
    action: string,
    currentEffect?: string
  ) => {
    setActionError("");
    const nextEffect =
      matrixClickMode === "remove"
        ? "remove"
        : currentEffect === "allow"
        ? "deny"
        : "allow";
    try {
      await apiFetch("/rbac/permissions/matrix/set", {
        method: "PUT",
        body: JSON.stringify({
          subject_type: subjectType,
          subject_id: subjectId,
          resource_type: resourceType,
          resource_id: resourceId,
          action,
          effect: nextEffect,
        }),
      });
      showToast(`Permission ${action} -> ${nextEffect.toUpperCase()} & cache invalidated`);
      await loadAll();
    } catch (err: any) {
      const msg = errMessage(err, "Failed to update permission rule");
      setActionError(msg);
      showToast(msg);
    }
  };

  const handleCopyPerms = async () => {
    if (!copySourceId || !copyTargetId) return;
    setActionError("");
    try {
      const res = await apiFetch<any>("/rbac/permissions/matrix/copy", {
        method: "POST",
        body: JSON.stringify({
          from_subject_type: copySubjectType,
          from_subject_id: copySourceId,
          to_subject_type: copySubjectType,
          to_subject_id: copyTargetId,
        }),
      });
      const count = res.copied ?? res.copied_rules ?? 0;
      showToast(`Copied ${count} permission rules`);
      await loadAll();
    } catch (err: any) {
      const msg = errMessage(err, "Failed to copy permission rules");
      setActionError(msg);
      showToast(msg);
    }
  };

  const primaryAd = adConns[0];

  const handleTestAd = async () => {
    if (!primaryAd) return;
    setActionError("");
    try {
      const res = await apiFetch<any>(`/rbac/ad/connections/${primaryAd.id}/test`, { method: "POST" });
      showToast(res.ok ? `AD Connected: ${res.message}` : `AD Error: ${res.error}`);
    } catch (err: any) {
      const msg = errMessage(err, "AD connection test failed");
      setActionError(msg);
      showToast(msg);
    }
  };

  const handleSearchAd = async () => {
    if (!primaryAd) return;
    setActionError("");
    try {
      const res = await apiFetch(`/rbac/ad/connections/${primaryAd.id}/search?q=${encodeURIComponent(adSearchQ)}`);
      setAdPreview(res);
    } catch (err: any) {
      const msg = errMessage(err, "AD directory search failed");
      setActionError(msg);
      showToast(msg);
    }
  };

  const handleSyncAd = async () => {
    if (!primaryAd) return;
    setActionError("");
    try {
      const res = await apiFetch<any>(`/rbac/ad/connections/${primaryAd.id}/sync`, { method: "POST" });
      setAdSyncResult(res);
      showToast(`AD Synced: ${res.users_imported} imported, ${res.users_updated} updated, ${res.users_deactivated} disabled`);
      await loadAll();
    } catch (err: any) {
      const msg = errMessage(err, "AD synchronization failed");
      setActionError(msg);
      showToast(msg);
    }
  };

  const handleAddRls = async (e: React.FormEvent) => {
    e.preventDefault();
    setActionError("");
    try {
      await apiFetch("/rbac/policies", {
        method: "POST",
        body: JSON.stringify({
          name: rlsName,
          policy_type: "rls",
          dataset_id: rlsDs || null,
          target_type: rlsSubjectId ? "user" : "all",
          target_id: rlsSubjectId || null,
          rule_json: {
            column: rlsCol.trim(),
            operator: rlsOp,
            value: rlsVal,
          },
          priority: 15,
        }),
      });
      setRlsName("");
      showToast("RLS rule saved & cache invalidated");
      await loadAll();
    } catch (err: any) {
      const msg = errMessage(err, "Failed to save RLS rule");
      setActionError(msg);
      showToast(msg);
    }
  };

  const handleAddCls = async (e: React.FormEvent) => {
    e.preventDefault();
    setActionError("");
    const colName = clsCol.trim();
    if (!colName) return;
    try {
      const isDeny = clsRule === "deny";
      await apiFetch("/rbac/policies", {
        method: "POST",
        body: JSON.stringify({
          name: `${isDeny ? "Hide" : "Mask"} ${colName}`,
          policy_type: isDeny ? "cls" : "masking",
          dataset_id: clsDs || null,
          target_type: clsSubjectId ? "role" : "all",
          target_id: clsSubjectId || null,
          rule_json: isDeny
            ? { hidden_columns: [colName], column: colName, strategy: "deny" }
            : { column: colName, strategy: clsRule },
          priority: 25,
        }),
      });
      showToast("Column security/masking rule saved");
      await loadAll();
    } catch (err: any) {
      const msg = errMessage(err, "Failed to save CLS/masking rule");
      setActionError(msg);
      showToast(msg);
    }
  };

  const handleDeletePolicy = async (policyId: string) => {
    setActionError("");
    try {
      await apiFetch(`/rbac/policies/${policyId}`, { method: "DELETE" });
      showToast("Policy deleted & cache invalidated");
      await loadAll();
    } catch (err: any) {
      const msg = errMessage(err, "Failed to delete policy");
      setActionError(msg);
      showToast(msg);
    }
  };

  const rlsPolicies = policies.filter((p) => p.policy_type === "rls");
  const clsPolicies = policies.filter((p) => p.policy_type !== "rls");

  const findRule = (
    subjectType: string,
    subjectId: string,
    resourceType: string,
    resourceId: string,
    action: string
  ) =>
    (matrix?.rules || []).find(
      (r: any) =>
        r.subject_type === subjectType &&
        r.subject_id === subjectId &&
        r.resource_type === resourceType &&
        r.resource_id === resourceId &&
        r.action === action
    );

  return (
    <AppShell>
      <div className="space-y-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-xl font-bold flex items-center gap-2">
              <ShieldCheck className="w-5 h-5" style={{ color: "var(--bi-primary)" }} />{" "}
              {t("security")} — Enterprise RBAC, Active Directory &amp; RLS/CLS
            </h1>
            <p className="text-xs mt-1" style={{ color: "var(--bi-muted)" }}>
              Granular Role/Group/User Permission Matrix (Default Deny, Explicit Deny overrides Allow), AD/LDAP Sync, and Server-Side Row/Column Security.
            </p>
          </div>
          <button
            type="button"
            onClick={() => loadAll()}
            disabled={loading}
            className="px-3 py-1.5 rounded-lg border text-xs font-semibold flex items-center gap-1.5"
            style={{ borderColor: "var(--bi-border)", background: "var(--bi-card)" }}
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </button>
        </div>

        {/* Load / Action Error Banners (Never fail silently) */}
        {loadErrors.length > 0 && (
          <div className="p-3.5 rounded-xl border border-red-500/40 bg-red-500/10 text-xs space-y-1.5">
            <div className="font-bold text-red-500 flex items-center gap-1.5">
              <ShieldAlert className="w-4 h-4 shrink-0" />
              Failed to load one or more security sections:
            </div>
            <ul className="list-disc ps-5 space-y-0.5 text-red-400">
              {loadErrors.map((e, i) => (
                <li key={i}>
                  <b>{e.section}:</b> {e.message}
                </li>
              ))}
            </ul>
            <div>
              <button
                type="button"
                onClick={() => loadAll()}
                className="mt-1 px-2.5 py-1 rounded border border-red-500/40 font-semibold hover:bg-red-500/20"
              >
                Retry Loading
              </button>
            </div>
          </div>
        )}

        {actionError && (
          <div className="p-3 rounded-xl border border-amber-500/40 bg-amber-500/10 text-xs flex items-center justify-between gap-2 text-amber-500">
            <div className="flex items-center gap-2 font-medium">
              <AlertTriangle className="w-4 h-4 shrink-0" />
              <span>{actionError}</span>
            </div>
            <button type="button" onClick={() => setActionError("")} className="underline">
              Dismiss
            </button>
          </div>
        )}

        {/* Section Tabs */}
        <div className="flex flex-wrap gap-2 text-xs">
          {[
            { id: "users", label: "Users & Roles", icon: Users },
            { id: "matrix", label: "Permission Matrix", icon: KeyRound },
            { id: "ad", label: "Active Directory / LDAP", icon: Server },
            { id: "rls", label: "Row & Column Security (RLS/CLS)", icon: Lock },
          ].map((tb) => (
            <button
              key={tb.id}
              type="button"
              onClick={() => setTab(tb.id as any)}
              className="px-4 py-2 rounded-lg font-semibold border flex items-center gap-1.5"
              style={{
                background: tab === tb.id ? "var(--bi-primary)" : "var(--bi-card)",
                color: tab === tb.id ? "#fff" : "var(--bi-text)",
                borderColor: "var(--bi-border)",
              }}
            >
              <tb.icon className="w-3.5 h-3.5" /> {tb.label}
            </button>
          ))}
        </div>

        {/* TAB 1: USERS, ROLES & GROUPS */}
        {tab === "users" && (
          <div className="space-y-5 text-xs">
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
              <form onSubmit={handleCreateUser} className="bi-card p-5 space-y-3">
                <h2 className="text-sm font-bold flex items-center gap-1.5">
                  <Plus className="w-4 h-4" /> Create Local User
                </h2>
                <input
                  value={newUsername}
                  onChange={(e) => setNewUsername(e.target.value)}
                  placeholder="Username"
                  className="w-full px-3 py-2 rounded border bg-transparent"
                  style={{ borderColor: "var(--bi-border)" }}
                  required
                />
                <input
                  value={newFullName}
                  onChange={(e) => setNewFullName(e.target.value)}
                  placeholder="Full Name"
                  className="w-full px-3 py-2 rounded border bg-transparent"
                  style={{ borderColor: "var(--bi-border)" }}
                />
                <input
                  type="email"
                  value={newEmail}
                  onChange={(e) => setNewEmail(e.target.value)}
                  placeholder="Email"
                  className="w-full px-3 py-2 rounded border bg-transparent"
                  style={{ borderColor: "var(--bi-border)" }}
                  required
                />
                <div>
                  <input
                    type="password"
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    placeholder="Password (10+ chars, Aa1!)"
                    className="w-full px-3 py-2 rounded border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                    required
                  />
                  <div className="text-[10px] opacity-70 mt-1">
                    Min 10 chars with uppercase, lowercase, and a digit or symbol.
                  </div>
                </div>
                <select
                  value={newRoleId}
                  onChange={(e) => setNewRoleId(e.target.value)}
                  className="w-full px-3 py-2 rounded border bg-transparent"
                  style={{ borderColor: "var(--bi-border)" }}
                >
                  {roles.length === 0 && <option value="">No roles loaded</option>}
                  {roles.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.display_name || r.name}
                    </option>
                  ))}
                </select>
                <button
                  type="submit"
                  className="w-full py-2 rounded-lg text-white font-semibold"
                  style={{ background: "var(--bi-primary)" }}
                >
                  Create User
                </button>
              </form>

              <div className="lg:col-span-2 bi-card p-5 space-y-3">
                <h2 className="text-sm font-bold">Directory &amp; Local Users ({users.length})</h2>
                {loading && users.length === 0 ? (
                  <div className="py-6 text-center opacity-70">Loading users...</div>
                ) : users.length === 0 ? (
                  <div className="py-6 text-center opacity-70">No users found.</div>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full">
                      <thead>
                        <tr className="border-b text-start" style={{ borderColor: "var(--bi-border)" }}>
                          <th className="py-2 text-start">User</th>
                          <th className="py-2 text-start">Auth Source</th>
                          <th className="py-2 text-start">Roles &amp; Groups</th>
                          <th className="py-2 text-start">Status</th>
                          <th className="py-2 text-start">Action</th>
                        </tr>
                      </thead>
                      <tbody>
                        {users.map((u) => {
                          const roleLabels =
                            Array.isArray(u.roles) && u.roles.length > 0
                              ? u.roles
                              : (u.role_ids || []).map((rid: string) => roleMap[rid]?.name || rid);
                          const groupLabels = (u.group_ids || []).map(
                            (gid: string) => groupMap[gid]?.name || gid
                          );
                          return (
                            <tr key={u.id} className="border-b" style={{ borderColor: "var(--bi-border)" }}>
                              <td className="py-2">
                                <div className="font-bold">{u.username}</div>
                                <div className="text-[11px] opacity-70">
                                  {u.full_name} · {u.email}
                                </div>
                              </td>
                              <td className="py-2">
                                <span className="px-2 py-0.5 rounded bg-slate-500/15 uppercase text-[10px] font-semibold">
                                  {u.auth_source}
                                </span>
                              </td>
                              <td className="py-2">
                                <div>{roleLabels.length ? roleLabels.join(", ") : "—"}</div>
                                {groupLabels.length > 0 && (
                                  <div className="text-[10px] opacity-70">
                                    Groups: {groupLabels.join(", ")}
                                  </div>
                                )}
                              </td>
                              <td className="py-2">
                                <span className={`font-semibold ${u.is_active ? "text-emerald-500" : "text-red-500"}`}>
                                  {u.is_active ? "active" : "disabled"}
                                </span>
                                {u.is_locked && (
                                  <span className="ms-1.5 px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-500 text-[10px] font-semibold">
                                    locked
                                  </span>
                                )}
                              </td>
                              <td className="py-2">
                                <div className="flex flex-wrap gap-1.5">
                                  <button
                                    type="button"
                                    onClick={() =>
                                      handleToggleUserStatus(
                                        u,
                                        { is_active: !u.is_active },
                                        u.is_active ? `User ${u.username} disabled` : `User ${u.username} activated`
                                      )
                                    }
                                    className="px-2 py-1 rounded border text-[11px]"
                                    style={{ borderColor: "var(--bi-border)" }}
                                  >
                                    {u.is_active ? "Disable" : "Activate"}
                                  </button>
                                  {u.is_locked && (
                                    <button
                                      type="button"
                                      onClick={() =>
                                        handleToggleUserStatus(u, { is_locked: false }, `Unlocked ${u.username}`)
                                      }
                                      className="px-2 py-1 rounded border text-[11px] flex items-center gap-1 text-amber-500"
                                      style={{ borderColor: "var(--bi-border)" }}
                                    >
                                      <Unlock className="w-3 h-3" /> Unlock
                                    </button>
                                  )}
                                </div>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </div>

            {/* Roles & Groups Management */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
              <div className="bi-card p-5 space-y-3">
                <div className="flex items-center justify-between">
                  <h2 className="text-sm font-bold">Roles ({roles.length})</h2>
                </div>
                <form onSubmit={handleCreateRole} className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                  <input
                    value={newRoleName}
                    onChange={(e) => setNewRoleName(e.target.value)}
                    placeholder="New Role Name"
                    className="px-2.5 py-1.5 rounded border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                    required
                  />
                  <input
                    value={newRoleSlug}
                    onChange={(e) => setNewRoleSlug(e.target.value)}
                    placeholder="slug (optional)"
                    className="px-2.5 py-1.5 rounded border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  />
                  <button
                    type="submit"
                    className="px-3 py-1.5 rounded text-white font-semibold"
                    style={{ background: "var(--bi-primary)" }}
                  >
                    + Create Role
                  </button>
                </form>
                <div className="max-h-60 overflow-y-auto space-y-1.5">
                  {roles.map((r) => (
                    <div
                      key={r.id}
                      className="p-2.5 rounded border flex items-center justify-between gap-2"
                      style={{ borderColor: "var(--bi-border)" }}
                    >
                      <div className="min-w-0">
                        <div className="font-bold truncate">
                          {r.display_name || r.name}{" "}
                          <span className="font-mono text-[10px] opacity-70">({r.slug})</span>
                        </div>
                        <div className="text-[11px] opacity-75 truncate">
                          {r.description || "Custom role"} · {(r.permission_ids || []).length} base permissions
                        </div>
                      </div>
                      {!r.is_super_admin && (
                        <button
                          type="button"
                          onClick={() => handleCloneRole(r.id)}
                          className="px-2 py-1 rounded border text-[11px] flex items-center gap-1 shrink-0"
                          style={{ borderColor: "var(--bi-border)" }}
                        >
                          <Copy className="w-3 h-3" /> Clone
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              </div>

              <div className="bi-card p-5 space-y-3">
                <h2 className="text-sm font-bold">Security Groups ({groups.length})</h2>
                <form onSubmit={handleCreateGroup} className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                  <input
                    value={newGroupName}
                    onChange={(e) => setNewGroupName(e.target.value)}
                    placeholder="New Group Name"
                    className="px-2.5 py-1.5 rounded border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                    required
                  />
                  <select
                    value={newGroupRoleId}
                    onChange={(e) => setNewGroupRoleId(e.target.value)}
                    className="px-2.5 py-1.5 rounded border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  >
                    <option value="">No default role</option>
                    {roles.map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.display_name || r.name}
                      </option>
                    ))}
                  </select>
                  <button
                    type="submit"
                    className="px-3 py-1.5 rounded text-white font-semibold"
                    style={{ background: "var(--bi-primary)" }}
                  >
                    + Create Group
                  </button>
                </form>
                {groups.length === 0 ? (
                  <div className="py-4 text-center opacity-70">No security groups created yet.</div>
                ) : (
                  <div className="max-h-60 overflow-y-auto space-y-1.5">
                    {groups.map((g) => (
                      <div
                        key={g.id}
                        className="p-2.5 rounded border flex items-center justify-between gap-2"
                        style={{ borderColor: "var(--bi-border)" }}
                      >
                        <div>
                          <div className="font-bold">
                            {g.name}{" "}
                            <span className="px-1.5 py-0.5 rounded bg-slate-500/15 text-[10px] uppercase">
                              {g.source}
                            </span>
                          </div>
                          <div className="text-[11px] opacity-75">
                            Roles:{" "}
                            {(g.role_ids || []).map((rid: string) => roleMap[rid]?.name || rid).join(", ") || "None"} ·{" "}
                            {(g.member_user_ids || []).length} members
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* TAB 2: PERMISSION MATRIX */}
        {tab === "matrix" && (
          <div className="space-y-4 text-xs">
            {/* Matrix Controls & Copy Bar */}
            <div className="bi-card p-4 flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-bold">Copy Permissions:</span>
                <select
                  value={copySubjectType}
                  onChange={(e) => {
                    setCopySubjectType(e.target.value as any);
                    setCopySourceId("");
                    setCopyTargetId("");
                  }}
                  className="px-2.5 py-1.5 rounded border bg-transparent"
                  style={{ borderColor: "var(--bi-border)" }}
                >
                  <option value="role">Roles</option>
                  <option value="group">Groups</option>
                  <option value="user">Users</option>
                </select>
                <select
                  value={copySourceId}
                  onChange={(e) => setCopySourceId(e.target.value)}
                  className="px-2.5 py-1.5 rounded border bg-transparent"
                  style={{ borderColor: "var(--bi-border)" }}
                >
                  <option value="">Source...</option>
                  {copySubjects.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.label}
                    </option>
                  ))}
                </select>
                <span>→</span>
                <select
                  value={copyTargetId}
                  onChange={(e) => setCopyTargetId(e.target.value)}
                  className="px-2.5 py-1.5 rounded border bg-transparent"
                  style={{ borderColor: "var(--bi-border)" }}
                >
                  <option value="">Target...</option>
                  {copySubjects.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.label}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={handleCopyPerms}
                  className="px-3 py-1.5 rounded text-white font-semibold"
                  style={{ background: "var(--bi-primary)" }}
                >
                  Copy Rules
                </button>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <span className="font-semibold opacity-75">Subject:</span>
                {(["role", "group", "user"] as const).map((st) => (
                  <button
                    key={st}
                    type="button"
                    onClick={() => setMatrixSubjectType(st)}
                    className="px-2.5 py-1 rounded border capitalize font-semibold"
                    style={{
                      background: matrixSubjectType === st ? "var(--bi-primary)" : "transparent",
                      color: matrixSubjectType === st ? "#fff" : "var(--bi-text)",
                      borderColor: "var(--bi-border)",
                    }}
                  >
                    {st}s
                  </button>
                ))}

                <span className="font-semibold opacity-75 ms-2">Scope:</span>
                {[
                  { id: "dashboards", label: "Dashboards" },
                  { id: "widgets", label: "Widgets" },
                  { id: "catalog", label: "Global & Role Catalog" },
                ].map((sc) => (
                  <button
                    key={sc.id}
                    type="button"
                    onClick={() => setMatrixScope(sc.id as any)}
                    className="px-2.5 py-1 rounded border font-semibold"
                    style={{
                      background: matrixScope === sc.id ? "var(--bi-primary)" : "transparent",
                      color: matrixScope === sc.id ? "#fff" : "var(--bi-text)",
                      borderColor: "var(--bi-border)",
                    }}
                  >
                    {sc.label}
                  </button>
                ))}

                <button
                  type="button"
                  onClick={() => setMatrixClickMode((m) => (m === "toggle" ? "remove" : "toggle"))}
                  className={`px-2.5 py-1 rounded border font-semibold ${
                    matrixClickMode === "remove" ? "bg-amber-600 text-white border-amber-600" : ""
                  }`}
                  style={matrixClickMode === "toggle" ? { borderColor: "var(--bi-border)" } : undefined}
                  title="Switch between Allow/Deny toggle and Remove Override click mode"
                >
                  Click: {matrixClickMode === "toggle" ? "Allow / Deny" : "Remove Rule"}
                </button>
              </div>
            </div>

            {loading && !matrix ? (
              <div className="bi-card p-8 text-center opacity-75">Loading permission matrix...</div>
            ) : !matrix ? (
              <div className="bi-card p-6 text-center space-y-2">
                <div className="font-semibold text-red-500">Permission matrix could not be loaded.</div>
                <button
                  type="button"
                  onClick={() => loadAll()}
                  className="px-3 py-1.5 rounded border font-semibold"
                  style={{ borderColor: "var(--bi-border)" }}
                >
                  Retry
                </button>
              </div>
            ) : (
              <>
                {matrixScope === "dashboards" && (
                  <div className="bi-card p-5 overflow-x-auto space-y-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <h2 className="text-sm font-bold">
                        {matrixSubjectType === "role" ? "Role" : matrixSubjectType === "group" ? "Group" : "User"} × Dashboard Permission Matrix (View / Edit / Add / Delete / Drill-Down)
                      </h2>
                      <div className="flex items-center gap-2 text-[11px]">
                        <span className="px-1.5 py-0.5 rounded bg-emerald-500 text-white font-semibold">Allow</span>
                        <span className="px-1.5 py-0.5 rounded bg-red-500 text-white font-semibold">Deny</span>
                        <span className="px-1.5 py-0.5 rounded border opacity-60 font-semibold">Unset (Default Deny)</span>
                      </div>
                    </div>

                    {matrix.dashboards.length === 0 ? (
                      <div className="p-4 rounded-lg border text-xs opacity-80" style={{ borderColor: "var(--bi-border)" }}>
                        No dashboards exist in this workspace yet. Create a dashboard or switch to <b>Global &amp; Role Catalog</b> below to manage system-wide permissions.
                      </div>
                    ) : matrixSubjects.length === 0 ? (
                      <div className="p-4 rounded-lg border text-xs opacity-80" style={{ borderColor: "var(--bi-border)" }}>
                        No {matrixSubjectType}s available to display.
                      </div>
                    ) : (
                      <table className="w-full text-[11px]">
                        <thead>
                          <tr className="border-b" style={{ borderColor: "var(--bi-border)" }}>
                            <th className="py-2 text-start">{matrixSubjectType.toUpperCase()}</th>
                            {matrix.dashboards.map((d: any) => (
                              <th key={d.id} className="py-2 px-2 text-center">
                                {d.title}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {matrixSubjects.map((subj) => (
                            <tr key={subj.id} className="border-b" style={{ borderColor: "var(--bi-border)" }}>
                              <td className="py-2.5 font-bold whitespace-nowrap">
                                <div>{subj.label}</div>
                                {subj.sub && <div className="text-[10px] font-normal opacity-65">{subj.sub}</div>}
                              </td>
                              {matrix.dashboards.map((dash: any) => (
                                <td key={dash.id} className="py-2 px-2 text-center">
                                  <div className="inline-flex items-center gap-1.5">
                                    {(["view", "edit", "add", "delete", "drill_down"] as const).map((act) => {
                                      const rule = findRule(matrixSubjectType, subj.id, "dashboard", dash.id, act);
                                      const effect = rule?.effect;
                                      const isAllowed = effect === "allow";
                                      const isDenied = effect === "deny";
                                      return (
                                        <button
                                          key={act}
                                          type="button"
                                          onClick={() =>
                                            toggleMatrixCell(
                                              matrixSubjectType,
                                              subj.id,
                                              "dashboard",
                                              dash.id,
                                              act,
                                              effect
                                            )
                                          }
                                          title={`${subj.label} → ${dash.title}: ${act} (${effect || "unset"})`}
                                          className={`px-1.5 py-0.5 rounded border font-semibold ${
                                            isAllowed
                                              ? "bg-emerald-500 text-white border-emerald-500"
                                              : isDenied
                                              ? "bg-red-500 text-white border-red-500"
                                              : "opacity-60"
                                          }`}
                                        >
                                          {act === "drill_down" ? "D" : act[0].toUpperCase()}
                                        </button>
                                      );
                                    })}
                                  </div>
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                )}

                {matrixScope === "widgets" && (
                  <div className="bi-card p-5 overflow-x-auto space-y-3">
                    <h2 className="text-sm font-bold">
                      {matrixSubjectType === "role" ? "Role" : matrixSubjectType === "group" ? "Group" : "User"} × Widget Matrix (View / Edit / Delete / Drill-Down)
                    </h2>
                    {matrix.widgets.length === 0 ? (
                      <div className="p-4 rounded border opacity-75">No widgets exist yet.</div>
                    ) : (
                      <table className="w-full text-[11px]">
                        <thead>
                          <tr className="border-b" style={{ borderColor: "var(--bi-border)" }}>
                            <th className="py-2 text-start">{matrixSubjectType.toUpperCase()}</th>
                            {matrix.widgets.slice(0, 12).map((w: any) => (
                              <th key={w.id} className="py-2 px-2 text-center max-w-[140px] truncate" title={w.title}>
                                {w.title}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {matrixSubjects.map((subj) => (
                            <tr key={subj.id} className="border-b" style={{ borderColor: "var(--bi-border)" }}>
                              <td className="py-2 font-bold whitespace-nowrap">{subj.label}</td>
                              {matrix.widgets.slice(0, 12).map((w: any) => (
                                <td key={w.id} className="py-2 px-2 text-center">
                                  <div className="inline-flex items-center gap-1">
                                    {(["view", "edit", "delete", "drill_down"] as const).map((act) => {
                                      const rule = findRule(matrixSubjectType, subj.id, "widget", w.id, act);
                                      const effect = rule?.effect;
                                      return (
                                        <button
                                          key={act}
                                          type="button"
                                          onClick={() =>
                                            toggleMatrixCell(matrixSubjectType, subj.id, "widget", w.id, act, effect)
                                          }
                                          title={`${subj.label} → ${w.title}: ${act} (${effect || "unset"})`}
                                          className={`px-1.5 py-0.5 rounded border font-semibold ${
                                            effect === "allow"
                                              ? "bg-emerald-500 text-white border-emerald-500"
                                              : effect === "deny"
                                              ? "bg-red-500 text-white border-red-500"
                                              : "opacity-60"
                                          }`}
                                        >
                                          {act === "drill_down" ? "D" : act[0].toUpperCase()}
                                        </button>
                                      );
                                    })}
                                  </div>
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                )}

                {(matrixScope === "catalog" || matrix.dashboards.length === 0) && (
                  <div className="bi-card p-5 overflow-x-auto space-y-3">
                    <h2 className="text-sm font-bold">
                      Role Permission Catalog &amp; System-Wide Capabilities ({catalog.length} permissions)
                    </h2>
                    <p className="text-[11px] opacity-75">
                      Manage base role grants across dashboards, datasets, queries, ETL, exports, and admin actions. Super Admin holds unrestricted access.
                    </p>
                    {catalog.length === 0 || roles.length === 0 ? (
                      <div className="p-4 rounded border opacity-75">No role catalog entries loaded.</div>
                    ) : (
                      <table className="w-full text-[11px]">
                        <thead>
                          <tr className="border-b" style={{ borderColor: "var(--bi-border)" }}>
                            <th className="py-2 text-start">Resource : Action</th>
                            {roles.map((r) => (
                              <th key={r.id} className="py-2 px-2 text-center whitespace-nowrap">
                                {r.display_name || r.name}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {catalog.map((perm: any) => (
                            <tr key={perm.id} className="border-b" style={{ borderColor: "var(--bi-border)" }}>
                              <td className="py-1.5 font-mono">
                                <span className="font-bold">{perm.resource_type}</span>:{perm.action}
                                {perm.description && (
                                  <div className="font-sans text-[10px] opacity-65">{perm.description}</div>
                                )}
                              </td>
                              {roles.map((role) => {
                                const hasPerm = (role.permission_ids || []).includes(perm.id);
                                return (
                                  <td key={role.id} className="py-1.5 px-2 text-center">
                                    <button
                                      type="button"
                                      disabled={role.is_super_admin}
                                      onClick={() => handleToggleRoleBasePerm(role, perm.id, hasPerm)}
                                      className={`px-2 py-0.5 rounded border font-semibold ${
                                        role.is_super_admin || hasPerm
                                          ? "bg-emerald-500 text-white border-emerald-500"
                                          : "opacity-55"
                                      } ${role.is_super_admin ? "cursor-not-allowed opacity-80" : ""}`}
                                    >
                                      {role.is_super_admin ? "ALL" : hasPerm ? "ALLOW" : "—"}
                                    </button>
                                  </td>
                                );
                              })}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {/* TAB 3: ACTIVE DIRECTORY / LDAP */}
        {tab === "ad" && (
          <div className="space-y-4 text-xs">
            {primaryAd ? (
              <div className="bi-card p-5 space-y-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <h2 className="text-sm font-bold">
                      {primaryAd.name} ({primaryAd.domain_name})
                    </h2>
                    <p className="opacity-75">
                      Mode: <b>{primaryAd.directory_mode}</b> · URL: {primaryAd.server_url} · Base DN:{" "}
                      <code>{primaryAd.base_dn}</code>
                    </p>
                  </div>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={handleTestAd}
                      className="px-3 py-1.5 rounded border font-semibold"
                      style={{ borderColor: "var(--bi-border)" }}
                    >
                      Test AD Bind
                    </button>
                    <button
                      type="button"
                      onClick={handleSyncAd}
                      className="px-3 py-1.5 rounded text-white font-semibold flex items-center gap-1"
                      style={{ background: "var(--bi-primary)" }}
                    >
                      <RefreshCw className="w-3.5 h-3.5" /> Sync Users &amp; Groups Now
                    </button>
                  </div>
                </div>

                {adSyncResult && (
                  <div className="p-3 rounded border bg-emerald-500/10 text-emerald-500 font-semibold">
                    Sync {adSyncResult.status}: Imported {adSyncResult.users_imported}, Updated{" "}
                    {adSyncResult.users_updated}, Deactivated {adSyncResult.users_deactivated}, Groups{" "}
                    {adSyncResult.groups_synced}
                  </div>
                )}

                <div className="flex gap-2">
                  <input
                    value={adSearchQ}
                    onChange={(e) => setAdSearchQ(e.target.value)}
                    placeholder="Search AD Users & Groups..."
                    className="flex-1 px-3 py-2 rounded border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  />
                  <button
                    type="button"
                    onClick={handleSearchAd}
                    className="px-4 py-2 rounded border font-semibold"
                    style={{ borderColor: "var(--bi-border)" }}
                  >
                    Search AD Directory
                  </button>
                </div>

                {adPreview && (
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    <div className="border rounded p-3" style={{ borderColor: "var(--bi-border)" }}>
                      <div className="font-bold mb-1">AD Users ({adPreview.users?.length || 0})</div>
                      {(adPreview.users || []).map((u: any) => (
                        <div key={u.sAMAccountName} className="py-1 border-b" style={{ borderColor: "var(--bi-border)" }}>
                          <b>{u.sAMAccountName}</b> ({u.displayName}) — {u.department} [{u.enabled ? "Enabled" : "Disabled"}]
                        </div>
                      ))}
                    </div>
                    <div className="border rounded p-3" style={{ borderColor: "var(--bi-border)" }}>
                      <div className="font-bold mb-1">AD Security Groups ({adPreview.groups?.length || 0})</div>
                      {(adPreview.groups || []).map((g: any) => (
                        <div key={g.dn} className="py-1 border-b" style={{ borderColor: "var(--bi-border)" }}>
                          <b>{g.cn}</b> → Role: <code>{g.mapped_role_slug}</code>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {adSyncJobs.length > 0 && (
                  <div className="space-y-1 pt-2 border-t" style={{ borderColor: "var(--bi-border)" }}>
                    <div className="font-bold">Recent AD Sync Jobs</div>
                    {adSyncJobs.slice(0, 5).map((j: any) => (
                      <div
                        key={j.id}
                        className="p-2 rounded border flex justify-between"
                        style={{ borderColor: "var(--bi-border)" }}
                      >
                        <span>
                          {j.sync_type} — <b>{j.status}</b> (+{j.users_imported} / ~{j.users_updated} / -
                          {j.users_deactivated})
                        </span>
                        <span className="opacity-60">{new Date(j.started_at).toLocaleString()}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <div className="bi-card p-5">No Active Directory connections configured.</div>
            )}
          </div>
        )}

        {/* TAB 4: RLS & CLS */}
        {tab === "rls" && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 text-xs">
            <div className="bi-card p-5 space-y-3">
              <h2 className="text-sm font-bold">Row-Level Security (RLS) Policies</h2>
              <form onSubmit={handleAddRls} className="space-y-2 border-b pb-3" style={{ borderColor: "var(--bi-border)" }}>
                <input
                  value={rlsName}
                  onChange={(e) => setRlsName(e.target.value)}
                  placeholder="Policy Name (e.g., West Region Only)"
                  className="w-full px-3 py-1.5 rounded border bg-transparent"
                  style={{ borderColor: "var(--bi-border)" }}
                  required
                />
                <div className="grid grid-cols-2 gap-2">
                  <select
                    value={rlsDs}
                    onChange={(e) => setRlsDs(e.target.value)}
                    className="px-2 py-1.5 rounded border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  >
                    {datasets.length === 0 && <option value="">All Datasets</option>}
                    {datasets.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.name}
                      </option>
                    ))}
                  </select>
                  <select
                    value={rlsSubjectId}
                    onChange={(e) => setRlsSubjectId(e.target.value)}
                    className="px-2 py-1.5 rounded border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  >
                    <option value="">All Users</option>
                    {users.map((u) => (
                      <option key={u.id} value={u.id}>
                        {u.username}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="grid grid-cols-3 gap-2">
                  <input
                    value={rlsCol}
                    onChange={(e) => setRlsCol(e.target.value)}
                    placeholder="Column (region)"
                    className="px-2 py-1.5 rounded border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  />
                  <select
                    value={rlsOp}
                    onChange={(e) => setRlsOp(e.target.value)}
                    className="px-2 py-1.5 rounded border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  >
                    <option value="eq">Equals</option>
                    <option value="neq">Not Equals</option>
                    <option value="in">In (CSV)</option>
                  </select>
                  <input
                    value={rlsVal}
                    onChange={(e) => setRlsVal(e.target.value)}
                    placeholder="Allowed Value"
                    className="px-2 py-1.5 rounded border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  />
                </div>
                <button
                  type="submit"
                  className="w-full py-1.5 rounded text-white font-semibold"
                  style={{ background: "var(--bi-primary)" }}
                >
                  Add RLS Policy
                </button>
              </form>
              <div className="space-y-1.5">
                {rlsPolicies.map((p) => (
                  <div
                    key={p.id}
                    className="p-2.5 rounded border flex justify-between items-center"
                    style={{ borderColor: "var(--bi-border)" }}
                  >
                    <div>
                      <div className="font-bold">{p.name}</div>
                      <div className="opacity-75">
                        {p.target_type} ·{" "}
                        <code>
                          {p.rule_json?.column} {p.rule_json?.operator} {String(p.rule_json?.value)}
                        </code>
                      </div>
                    </div>
                    <button type="button" onClick={() => handleDeletePolicy(p.id)} className="text-red-500">
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            </div>

            <div className="bi-card p-5 space-y-3">
              <h2 className="text-sm font-bold">Column-Level Security &amp; Masking (CLS)</h2>
              <form onSubmit={handleAddCls} className="space-y-2 border-b pb-3" style={{ borderColor: "var(--bi-border)" }}>
                <div className="grid grid-cols-2 gap-2">
                  <select
                    value={clsDs}
                    onChange={(e) => setClsDs(e.target.value)}
                    className="px-2 py-1.5 rounded border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  >
                    {datasets.length === 0 && <option value="">All Datasets</option>}
                    {datasets.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.name}
                      </option>
                    ))}
                  </select>
                  <select
                    value={clsSubjectId}
                    onChange={(e) => setClsSubjectId(e.target.value)}
                    className="px-2 py-1.5 rounded border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  >
                    <option value="">All Users</option>
                    {roles.map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.display_name || r.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <input
                    value={clsCol}
                    onChange={(e) => setClsCol(e.target.value)}
                    placeholder="Column (e.g., email, salary)"
                    className="px-2 py-1.5 rounded border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  />
                  <select
                    value={clsRule}
                    onChange={(e) => setClsRule(e.target.value)}
                    className="px-2 py-1.5 rounded border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  >
                    <option value="deny">Deny / Hide Column</option>
                    <option value="partial">Partial Mask (a***@domain)</option>
                    <option value="redact">Full Redact (***)</option>
                    <option value="hash">SHA-256 Hash</option>
                  </select>
                </div>
                <button type="submit" className="w-full py-1.5 rounded text-white font-semibold bg-amber-600">
                  Add CLS / Masking Rule
                </button>
              </form>
              <div className="space-y-1.5">
                {clsPolicies.map((p) => {
                  const cols = Array.isArray(p.rule_json?.hidden_columns)
                    ? p.rule_json.hidden_columns.join(", ")
                    : p.rule_json?.column || "";
                  return (
                    <div
                      key={p.id}
                      className="p-2.5 rounded border flex justify-between items-center"
                      style={{ borderColor: "var(--bi-border)" }}
                    >
                      <div>
                        <div className="font-bold">
                          {p.name} ({p.policy_type})
                        </div>
                        <div className="opacity-75">
                          Column: <code>{cols}</code> → {p.rule_json?.strategy || "hide"}
                        </div>
                      </div>
                      <button type="button" onClick={() => handleDeletePolicy(p.id)} className="text-red-500">
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
