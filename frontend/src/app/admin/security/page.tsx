"use client";

import React, { useEffect, useState } from "react";
import { KeyRound, Lock, Plus, RefreshCw, Server, ShieldCheck, Trash2, Users } from "lucide-react";
import AppShell from "@/components/layout/AppShell";
import { apiFetch } from "@/lib/api";
import { useBi } from "@/lib/context";

export default function SecurityAdminPage() {
  const { user, showToast } = useBi();
  const [tab, setTab] = useState<"users" | "matrix" | "ad" | "rls">("users");

  // Users, Roles, Groups
  const [users, setUsers] = useState<any[]>([]);
  const [roles, setRoles] = useState<any[]>([]);
  const [newUsername, setNewUsername] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [newFullName, setNewFullName] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newRoleId, setNewRoleId] = useState("");

  // Permission Matrix
  const [matrix, setMatrix] = useState<any>(null);
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

  const loadAll = () => {
    apiFetch("/rbac/users").then(setUsers).catch(() => {});
    apiFetch("/rbac/roles").then((r) => { setRoles(r); if (r[0] && !newRoleId) setNewRoleId(r[0].id); }).catch(() => {});
    apiFetch("/rbac/permissions/matrix").then(setMatrix).catch(() => {});
    apiFetch<any>("/rbac/ad/connections").then((res) => {
      setAdConns(res.connections || []);
      setAdSyncJobs(res.sync_jobs || []);
    }).catch(() => {});
    apiFetch("/rbac/policies").then(setPolicies).catch(() => {});
    apiFetch("/datasets").then((ds) => { setDatasets(ds); if (ds[0] && !rlsDs) { setRlsDs(ds[0].id); setClsDs(ds[0].id); } }).catch(() => {});
  };

  useEffect(() => {
    if (user) loadAll();
  }, [user]);

  const handleCreateUser = async (e: React.FormEvent) => {
    e.preventDefault();
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
      loadAll();
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const toggleMatrixCell = async (
    subjectType: string,
    subjectId: string,
    resourceType: string,
    resourceId: string,
    action: string,
    currentlyAllowed: boolean
  ) => {
    try {
      await apiFetch("/rbac/permissions/matrix/set", {
        method: "PUT",
        body: JSON.stringify({
          subject_type: subjectType,
          subject_id: subjectId,
          resource_type: resourceType,
          resource_id: resourceId,
          action,
          effect: currentlyAllowed ? "deny" : "allow",
        }),
      });
      showToast(`Permission ${action} -> ${currentlyAllowed ? "DENY" : "ALLOW"} & cache invalidated`);
      loadAll();
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleCopyPerms = async () => {
    if (!copySourceId || !copyTargetId) return;
    try {
      const res = await apiFetch<any>("/rbac/permissions/matrix/copy", {
        method: "POST",
        body: JSON.stringify({
          from_subject_type: "role",
          from_subject_id: copySourceId,
          to_subject_type: "role",
          to_subject_id: copyTargetId,
        }),
      });
      showToast(`Copied ${res.copied_rules} permission rules`);
      loadAll();
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const primaryAd = adConns[0];

  const handleTestAd = async () => {
    if (!primaryAd) return;
    const res = await apiFetch<any>(`/rbac/ad/connections/${primaryAd.id}/test`, { method: "POST" });
    showToast(res.ok ? `AD Connected: ${res.message}` : `AD Error: ${res.error}`);
  };

  const handleSearchAd = async () => {
    if (!primaryAd) return;
    const res = await apiFetch(`/rbac/ad/connections/${primaryAd.id}/search?q=${encodeURIComponent(adSearchQ)}`);
    setAdPreview(res);
  };

  const handleSyncAd = async () => {
    if (!primaryAd) return;
    const res = await apiFetch<any>(`/rbac/ad/connections/${primaryAd.id}/sync`, { method: "POST" });
    setAdSyncResult(res);
    showToast(`AD Synced: ${res.users_imported} imported, ${res.users_updated} updated, ${res.users_deactivated} disabled`);
    loadAll();
  };

  const handleAddRls = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await apiFetch("/rbac/policies", {
        method: "POST",
        body: JSON.stringify({
          name: rlsName,
          policy_type: "rls",
          dataset_id: rlsDs,
          target_type: rlsSubjectId ? "user" : "all",
          target_id: rlsSubjectId || null,
          rule_json: {
            column: rlsCol,
            operator: rlsOp,
            value: rlsVal,
          },
          priority: 15,
        }),
      });
      setRlsName("");
      showToast("RLS rule saved & cache invalidated");
      loadAll();
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const handleAddCls = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await apiFetch("/rbac/policies", {
        method: "POST",
        body: JSON.stringify({
          name: `Mask ${clsCol}`,
          policy_type: clsRule === "deny" ? "cls" : "masking",
          dataset_id: clsDs,
          target_type: clsSubjectId ? "role" : "all",
          target_id: clsSubjectId || null,
          rule_json: {
            column: clsCol,
            strategy: clsRule,
          },
          priority: 25,
        }),
      });
      showToast("Column security/masking rule saved");
      loadAll();
    } catch (err: any) {
      showToast(err.message);
    }
  };

  const rlsPolicies = policies.filter((p) => p.policy_type === "rls");
  const clsPolicies = policies.filter((p) => p.policy_type !== "rls");

  return (
    <AppShell>
      <div className="space-y-6">
        <div>
          <h1 className="text-xl font-bold flex items-center gap-2">
            <ShieldCheck className="w-5 h-5" style={{ color: "var(--bi-primary)" }} /> Enterprise Security, RBAC, Active Directory & RLS/CLS
          </h1>
          <p className="text-xs mt-1" style={{ color: "var(--bi-muted)" }}>
            Granular Role/Group/User Permission Matrix (Default Deny, Explicit Deny overrides Allow), AD/LDAP Sync, and Server-Side Row/Column Security.
          </p>
        </div>

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

        {/* TAB 1: USERS & ROLES */}
        {tab === "users" && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-5 text-xs">
            <form onSubmit={handleCreateUser} className="bi-card p-5 space-y-3">
              <h2 className="text-sm font-bold flex items-center gap-1.5"><Plus className="w-4 h-4" /> Create Local User</h2>
              <input value={newUsername} onChange={(e) => setNewUsername(e.target.value)} placeholder="Username" className="w-full px-3 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} required />
              <input value={newFullName} onChange={(e) => setNewFullName(e.target.value)} placeholder="Full Name" className="w-full px-3 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
              <input type="email" value={newEmail} onChange={(e) => setNewEmail(e.target.value)} placeholder="Email" className="w-full px-3 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
              <input type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} placeholder="Password" className="w-full px-3 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} required />
              <select value={newRoleId} onChange={(e) => setNewRoleId(e.target.value)} className="w-full px-3 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                {roles.map((r) => <option key={r.id} value={r.id}>{r.display_name || r.name}</option>)}
              </select>
              <button type="submit" className="w-full py-2 rounded-lg text-white font-semibold" style={{ background: "var(--bi-primary)" }}>Create User</button>
            </form>

            <div className="lg:col-span-2 bi-card p-5 space-y-3">
              <h2 className="text-sm font-bold">Directory & Local Users ({users.length})</h2>
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="border-b text-start" style={{ borderColor: "var(--bi-border)" }}>
                      <th className="py-2 text-start">User</th>
                      <th className="py-2 text-start">Auth Source</th>
                      <th className="py-2 text-start">Roles</th>
                      <th className="py-2 text-start">Status</th>
                      <th className="py-2 text-start">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {users.map((u) => (
                      <tr key={u.id} className="border-b" style={{ borderColor: "var(--bi-border)" }}>
                        <td className="py-2">
                          <div className="font-bold">{u.username}</div>
                          <div className="text-[11px] opacity-70">{u.full_name} · {u.email}</div>
                        </td>
                        <td className="py-2">
                          <span className="px-2 py-0.5 rounded bg-slate-500/15 uppercase text-[10px] font-semibold">{u.auth_source}</span>
                        </td>
                        <td className="py-2">{(u.roles || []).join(", ")}</td>
                        <td className="py-2">
                          <span className={`font-semibold ${u.is_active ? "text-emerald-500" : "text-red-500"}`}>{u.is_active ? "active" : "disabled"}</span>
                        </td>
                        <td className="py-2">
                          <button
                            onClick={() => apiFetch(`/rbac/users/${u.id}`, { method: "PUT", body: JSON.stringify({ is_active: !u.is_active }) }).then(loadAll)}
                            className="px-2 py-1 rounded border text-[11px]"
                            style={{ borderColor: "var(--bi-border)" }}
                          >
                            {u.is_active ? "Disable" : "Activate"}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {/* TAB 2: PERMISSION MATRIX */}
        {tab === "matrix" && matrix && (
          <div className="space-y-4 text-xs">
            <div className="bi-card p-4 flex flex-wrap items-center gap-2">
              <span className="font-bold">Copy Permissions:</span>
              <select value={copySourceId} onChange={(e) => setCopySourceId(e.target.value)} className="px-2.5 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                <option value="">Source Role...</option>
                {roles.map((r) => <option key={r.id} value={r.id}>{r.display_name || r.name}</option>)}
              </select>
              <span>→</span>
              <select value={copyTargetId} onChange={(e) => setCopyTargetId(e.target.value)} className="px-2.5 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                <option value="">Target Role...</option>
                {roles.map((r) => <option key={r.id} value={r.id}>{r.display_name || r.name}</option>)}
              </select>
              <button onClick={handleCopyPerms} className="px-3 py-1.5 rounded text-white font-semibold" style={{ background: "var(--bi-primary)" }}>Copy Rules</button>
            </div>

            <div className="bi-card p-5 overflow-x-auto">
              <h2 className="text-sm font-bold mb-3">Role × Dashboard Permission Matrix (View / Edit / Add / Delete / Drill-Down)</h2>
              <table className="w-full text-[11px]">
                <thead>
                  <tr className="border-b" style={{ borderColor: "var(--bi-border)" }}>
                    <th className="py-2 text-start">Role</th>
                    {matrix.dashboards.map((d: any) => (
                      <th key={d.id} className="py-2 px-2 text-center">{d.title}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {roles.map((role: any) => (
                    <tr key={role.id} className="border-b" style={{ borderColor: "var(--bi-border)" }}>
                      <td className="py-2.5 font-bold whitespace-nowrap">{role.display_name || role.name}</td>
                      {matrix.dashboards.map((dash: any) => (
                        <td key={dash.id} className="py-2 px-2 text-center">
                          <div className="inline-flex items-center gap-1.5">
                            {(["view", "edit", "add", "delete", "drill_down"] as const).map((act) => {
                              const rule = (matrix.rules || []).find(
                                (r: any) => r.subject_type === "role" && r.subject_id === role.id && r.resource_id === dash.id && r.action === act
                              );
                              const isAllowed = rule?.effect === "allow";
                              const isDenied = rule?.effect === "deny";
                              return (
                                <button
                                  key={act}
                                  onClick={() => toggleMatrixCell("role", role.id, "dashboard", dash.id, act, isAllowed)}
                                  title={`${role.name} → ${dash.title}: ${act} (${rule?.effect || "unset"})`}
                                  className={`px-1.5 py-0.5 rounded border font-semibold ${
                                    isAllowed ? "bg-emerald-500 text-white border-emerald-500" : isDenied ? "bg-red-500 text-white border-red-500" : "opacity-50"
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
            </div>
          </div>
        )}

        {/* TAB 3: ACTIVE DIRECTORY / LDAP */}
        {tab === "ad" && (
          <div className="space-y-4 text-xs">
            {primaryAd ? (
              <div className="bi-card p-5 space-y-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <h2 className="text-sm font-bold">{primaryAd.name} ({primaryAd.domain_name})</h2>
                    <p className="opacity-75">
                      Mode: <b>{primaryAd.directory_mode}</b> · URL: {primaryAd.server_url} · Base DN: <code>{primaryAd.base_dn}</code>
                    </p>
                  </div>
                  <div className="flex gap-2">
                    <button onClick={handleTestAd} className="px-3 py-1.5 rounded border font-semibold" style={{ borderColor: "var(--bi-border)" }}>Test AD Bind</button>
                    <button onClick={handleSyncAd} className="px-3 py-1.5 rounded text-white font-semibold flex items-center gap-1" style={{ background: "var(--bi-primary)" }}>
                      <RefreshCw className="w-3.5 h-3.5" /> Sync Users & Groups Now
                    </button>
                  </div>
                </div>

                {adSyncResult && (
                  <div className="p-3 rounded border bg-emerald-500/10 text-emerald-500 font-semibold">
                    Sync {adSyncResult.status}: Imported {adSyncResult.users_imported}, Updated {adSyncResult.users_updated}, Deactivated {adSyncResult.users_deactivated}, Groups {adSyncResult.groups_synced}
                  </div>
                )}

                <div className="flex gap-2">
                  <input value={adSearchQ} onChange={(e) => setAdSearchQ(e.target.value)} placeholder="Search AD Users & Groups..." className="flex-1 px-3 py-2 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
                  <button onClick={handleSearchAd} className="px-4 py-2 rounded border font-semibold" style={{ borderColor: "var(--bi-border)" }}>Search AD Directory</button>
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
                      <div key={j.id} className="p-2 rounded border flex justify-between" style={{ borderColor: "var(--bi-border)" }}>
                        <span>{j.sync_type} — <b>{j.status}</b> (+{j.users_imported} / ~{j.users_updated} / -{j.users_deactivated})</span>
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
                <input value={rlsName} onChange={(e) => setRlsName(e.target.value)} placeholder="Policy Name (e.g., West Region Only)" className="w-full px-3 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} required />
                <div className="grid grid-cols-2 gap-2">
                  <select value={rlsDs} onChange={(e) => setRlsDs(e.target.value)} className="px-2 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                    {datasets.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                  </select>
                  <select value={rlsSubjectId} onChange={(e) => setRlsSubjectId(e.target.value)} className="px-2 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                    <option value="">All Users</option>
                    {users.map((u) => <option key={u.id} value={u.id}>{u.username}</option>)}
                  </select>
                </div>
                <div className="grid grid-cols-3 gap-2">
                  <input value={rlsCol} onChange={(e) => setRlsCol(e.target.value)} placeholder="Column (region)" className="px-2 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
                  <select value={rlsOp} onChange={(e) => setRlsOp(e.target.value)} className="px-2 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                    <option value="eq">Equals</option>
                    <option value="neq">Not Equals</option>
                    <option value="in">In (CSV)</option>
                  </select>
                  <input value={rlsVal} onChange={(e) => setRlsVal(e.target.value)} placeholder="Allowed Value" className="px-2 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
                </div>
                <button type="submit" className="w-full py-1.5 rounded text-white font-semibold" style={{ background: "var(--bi-primary)" }}>Add RLS Policy</button>
              </form>
              <div className="space-y-1.5">
                {rlsPolicies.map((p) => (
                  <div key={p.id} className="p-2.5 rounded border flex justify-between items-center" style={{ borderColor: "var(--bi-border)" }}>
                    <div>
                      <div className="font-bold">{p.name}</div>
                      <div className="opacity-75">{p.target_type} · <code>{p.rule_json?.column} {p.rule_json?.operator} {String(p.rule_json?.value)}</code></div>
                    </div>
                    <button onClick={() => apiFetch(`/rbac/policies/${p.id}`, { method: "DELETE" }).then(loadAll)} className="text-red-500"><Trash2 className="w-3.5 h-3.5" /></button>
                  </div>
                ))}
              </div>
            </div>

            <div className="bi-card p-5 space-y-3">
              <h2 className="text-sm font-bold">Column-Level Security & Masking (CLS)</h2>
              <form onSubmit={handleAddCls} className="space-y-2 border-b pb-3" style={{ borderColor: "var(--bi-border)" }}>
                <div className="grid grid-cols-2 gap-2">
                  <select value={clsDs} onChange={(e) => setClsDs(e.target.value)} className="px-2 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                    {datasets.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                  </select>
                  <select value={clsSubjectId} onChange={(e) => setClsSubjectId(e.target.value)} className="px-2 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                    <option value="">All Users</option>
                    {roles.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
                  </select>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <input value={clsCol} onChange={(e) => setClsCol(e.target.value)} placeholder="Column (e.g., email, salary)" className="px-2 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
                  <select value={clsRule} onChange={(e) => setClsRule(e.target.value)} className="px-2 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                    <option value="deny">Deny / Hide Column</option>
                    <option value="partial">Partial Mask (a***@domain)</option>
                    <option value="redact">Full Redact (***)</option>
                    <option value="hash">SHA-256 Hash</option>
                  </select>
                </div>
                <button type="submit" className="w-full py-1.5 rounded text-white font-semibold bg-amber-600">Add CLS / Masking Rule</button>
              </form>
              <div className="space-y-1.5">
                {clsPolicies.map((p) => (
                  <div key={p.id} className="p-2.5 rounded border flex justify-between items-center" style={{ borderColor: "var(--bi-border)" }}>
                    <div>
                      <div className="font-bold">{p.name} ({p.policy_type})</div>
                      <div className="opacity-75">Column: <code>{p.rule_json?.column}</code> → {p.rule_json?.strategy || "hide"}</div>
                    </div>
                    <button onClick={() => apiFetch(`/rbac/policies/${p.id}`, { method: "DELETE" }).then(loadAll)} className="text-red-500"><Trash2 className="w-3.5 h-3.5" /></button>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
