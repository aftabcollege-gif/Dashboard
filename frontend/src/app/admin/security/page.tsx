"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ClipboardList,
  Copy,
  Edit3,
  Eye,
  Filter,
  KeyRound,
  Lock,
  Plus,
  RefreshCw,
  Search,
  Server,
  ShieldAlert,
  ShieldCheck,
  Trash2,
  Unlock,
  Users,
  X,
} from "lucide-react";
import AppShell from "@/components/layout/AppShell";
import { apiFetch } from "@/lib/api";
import { useBi } from "@/lib/context";
import {
  formatActionLabel,
  formatRoleLabel,
  formatScopeLabel,
  ROLE_META_FA,
} from "@/lib/rbacLabels";

interface LoadErrorItem {
  section: string;
  message: string;
}

interface MatrixData {
  rules: any[];
  dashboards: any[];
  widgets: any[];
}

interface PendingConfirm {
  title: string;
  summary: string;
  beforeText: string;
  afterText: string;
  danger?: boolean;
  onConfirm: (reason: string) => Promise<void>;
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

function errMessage(err: unknown, fallback = "درخواست با خطا مواجه شد"): string {
  if (err instanceof Error && err.message) return err.message;
  if (typeof err === "string" && err) return err;
  return fallback;
}

export default function SecurityAdminPage() {
  const { user, locale, showToast } = useBi();
  const isFa = locale === "fa";

  const [tab, setTab] = useState<"users" | "matrix" | "ad" | "rls" | "audit">("users");
  const [loading, setLoading] = useState<boolean>(true);
  const [loadErrors, setLoadErrors] = useState<LoadErrorItem[]>([]);
  const [actionError, setActionError] = useState<string>("");
  const [actionSuccess, setActionSuccess] = useState<string>("");

  // Data sets
  const [users, setUsers] = useState<any[]>([]);
  const [roles, setRoles] = useState<any[]>([]);
  const [catalog, setCatalog] = useState<any[]>([]);
  const [groups, setGroups] = useState<any[]>([]);
  const [auditLogs, setAuditLogs] = useState<any[]>([]);

  // User search, filter, sort, pagination
  const [userSearch, setUserSearch] = useState("");
  const [userRoleFilter, setUserRoleFilter] = useState("all");
  const [userStatusFilter, setUserStatusFilter] = useState<"all" | "active" | "inactive" | "locked">("all");
  const [userSourceFilter, setUserSourceFilter] = useState<"all" | "local" | "active_directory">("all");
  const [userSort, setUserSort] = useState<"username" | "full_name" | "last_login">("username");
  const [userPage, setUserPage] = useState(1);
  const pageSize = 10;

  // Create User / Role / Group forms
  const [newUsername, setNewUsername] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [newFullName, setNewFullName] = useState("");
  const [newDepartment, setNewDepartment] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newRoleId, setNewRoleId] = useState("");
  const [newRoleName, setNewRoleName] = useState("");
  const [newRoleSlug, setNewRoleSlug] = useState("");
  const [newRoleDesc, setNewRoleDesc] = useState("");
  const [newGroupName, setNewGroupName] = useState("");
  const [newGroupRoleId, setNewGroupRoleId] = useState("");

  // Edit User Modal
  const [editingUser, setEditingUser] = useState<any | null>(null);
  const [editUserFullName, setEditUserFullName] = useState("");
  const [editUserEmail, setEditUserEmail] = useState("");
  const [editUserDepartment, setEditUserDepartment] = useState("");
  const [editUserRegion, setEditUserRegion] = useState("");
  const [editUserRoleIds, setEditUserRoleIds] = useState<string[]>([]);
  const [editUserGroupIds, setEditUserGroupIds] = useState<string[]>([]);
  const [editUserActive, setEditUserActive] = useState(true);
  const [editUserPassword, setEditUserPassword] = useState("");

  // Effective Permissions Inspector
  const [effectiveModalUser, setEffectiveModalUser] = useState<any | null>(null);
  const [effectiveData, setEffectiveData] = useState<any | null>(null);
  const [effectiveLoading, setEffectiveLoading] = useState(false);

  // Role Editor Modal
  const [editingRole, setEditingRole] = useState<any | null>(null);
  const [editRoleName, setEditRoleName] = useState("");
  const [editRoleDesc, setEditRoleDesc] = useState("");
  const [editRolePermIds, setEditRolePermIds] = useState<string[]>([]);
  const [rolePermSearch, setRolePermSearch] = useState("");

  // Sensitive Confirmation Diff Modal
  const [pendingConfirm, setPendingConfirm] = useState<PendingConfirm | null>(null);
  const [confirmReason, setConfirmReason] = useState("");
  const [confirmBusy, setConfirmBusy] = useState(false);

  // Permission Matrix
  const [matrix, setMatrix] = useState<MatrixData | null>(null);
  const [matrixSubjectType, setMatrixSubjectType] = useState<"role" | "group" | "user">("role");
  const [matrixScope, setMatrixScope] = useState<"dashboards" | "widgets" | "catalog">("dashboards");
  const [matrixClickMode, setMatrixClickMode] = useState<"toggle" | "remove">("toggle");
  const [matrixPermFilter, setMatrixPermFilter] = useState("");
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

  const hasAdminView = useMemo(() => {
    const ep = user?.effective_permissions;
    if (!ep) return true;
    if (ep.is_super_admin) return true;
    return (ep.role_permissions || []).some(
      (p: any) => (p.resource_type === "admin" || p.resource_type === "*") && (p.action === "view" || p.action === "*") && p.effect === "allow"
    );
  }, [user]);

  const notifySuccess = (msg: string) => {
    setActionError("");
    setActionSuccess(msg);
    showToast(msg);
  };

  const notifyError = (err: unknown, fallback?: string) => {
    const msg = errMessage(err, fallback);
    setActionSuccess("");
    setActionError(msg);
    showToast(msg);
  };

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
      auditRes,
    ] = await Promise.allSettled([
      apiFetch("/rbac/users"),
      apiFetch("/rbac/roles"),
      apiFetch("/rbac/groups"),
      apiFetch("/rbac/permissions/matrix"),
      apiFetch<any>("/rbac/ad/connections"),
      apiFetch("/rbac/policies"),
      apiFetch("/datasets"),
      apiFetch("/monitoring/audit-logs?limit=100"),
    ]);

    if (usersRes.status === "fulfilled") {
      setUsers(asArray(usersRes.value, "users"));
    } else {
      errors.push({ section: isFa ? "کاربران" : "Users", message: errMessage(usersRes.reason) });
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
      errors.push({ section: isFa ? "نقش‌ها" : "Roles", message: errMessage(rolesRes.reason) });
    }

    if (groupsRes.status === "fulfilled") {
      setGroups(asArray(groupsRes.value, "groups"));
    } else {
      errors.push({ section: isFa ? "گروه‌ها" : "Groups", message: errMessage(groupsRes.reason) });
    }

    if (matrixRes.status === "fulfilled") {
      setMatrix(normalizeMatrix(matrixRes.value));
    } else {
      errors.push({ section: isFa ? "ماتریس دسترسی" : "Permission Matrix", message: errMessage(matrixRes.reason) });
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
      errors.push({ section: isFa ? "سیاست‌های RLS/CLS" : "RLS/CLS Policies", message: errMessage(policiesRes.reason) });
    }

    if (datasetsRes.status === "fulfilled") {
      const dsList = asArray(datasetsRes.value);
      setDatasets(dsList);
      if (dsList[0]) {
        setRlsDs((prev) => prev || dsList[0].id);
        setClsDs((prev) => prev || dsList[0].id);
      }
    } else {
      errors.push({ section: isFa ? "مجموعه داده‌ها" : "Datasets", message: errMessage(datasetsRes.reason) });
    }

    if (auditRes.status === "fulfilled") {
      setAuditLogs(asArray(auditRes.value));
    }

    setLoadErrors(errors);
    setLoading(false);
  }, [isFa]);

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

  const filteredUsers = useMemo(() => {
    const q = userSearch.trim().toLowerCase();
    const list = users.filter((u) => {
      if (q) {
        const hay = `${u.username || ""} ${u.full_name || ""} ${u.email || ""} ${u.department || ""}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      if (userRoleFilter !== "all" && !(u.role_ids || []).includes(userRoleFilter)) return false;
      if (userStatusFilter === "active" && (!u.is_active || u.is_locked)) return false;
      if (userStatusFilter === "inactive" && u.is_active) return false;
      if (userStatusFilter === "locked" && !u.is_locked) return false;
      if (userSourceFilter !== "all" && u.auth_source !== userSourceFilter) return false;
      return true;
    });
    return [...list].sort((a, b) => {
      if (userSort === "full_name") return String(a.full_name || a.username).localeCompare(String(b.full_name || b.username), "fa");
      if (userSort === "last_login") {
        return new Date(b.last_login_at || 0).getTime() - new Date(a.last_login_at || 0).getTime();
      }
      return String(a.username).localeCompare(String(b.username));
    });
  }, [users, userSearch, userRoleFilter, userStatusFilter, userSourceFilter, userSort]);

  const totalUserPages = Math.max(1, Math.ceil(filteredUsers.length / pageSize));
  const pagedUsers = useMemo(() => {
    const safePage = Math.min(userPage, totalUserPages);
    return filteredUsers.slice((safePage - 1) * pageSize, safePage * pageSize);
  }, [filteredUsers, userPage, totalUserPages]);

  const matrixSubjects = useMemo(() => {
    if (matrixSubjectType === "group") {
      return groups.map((g) => ({
        id: g.id,
        label: g.name,
        sub: isFa ? `${(g.member_user_ids || []).length} عضو` : `${(g.member_user_ids || []).length} members`,
      }));
    }
    if (matrixSubjectType === "user") {
      return users.map((u) => ({ id: u.id, label: u.username, sub: u.full_name || u.email || "" }));
    }
    return roles.map((r) => ({ id: r.id, label: formatRoleLabel(r, isFa), sub: r.slug }));
  }, [matrixSubjectType, roles, groups, users, isFa]);

  const copySubjects = useMemo(() => {
    if (copySubjectType === "group") return groups.map((g) => ({ id: g.id, label: g.name }));
    if (copySubjectType === "user") return users.map((u) => ({ id: u.id, label: u.username }));
    return roles.map((r) => ({ id: r.id, label: formatRoleLabel(r, isFa) }));
  }, [copySubjectType, roles, groups, users, isFa]);

  const groupedCatalog = useMemo(() => {
    const q = rolePermSearch.trim().toLowerCase();
    const groupsMap: Record<string, any[]> = {};
    for (const p of catalog) {
      const hay = `${p.resource_type} ${p.action} ${p.description || ""} ${formatScopeLabel(p.resource_type, true)} ${formatActionLabel(p.action, true)}`.toLowerCase();
      if (q && !hay.includes(q)) continue;
      if (!groupsMap[p.resource_type]) groupsMap[p.resource_type] = [];
      groupsMap[p.resource_type].push(p);
    }
    return groupsMap;
  }, [catalog, rolePermSearch]);

  const askConfirm = (cfg: PendingConfirm) => {
    setConfirmReason("");
    setPendingConfirm(cfg);
  };

  const handleConfirmExecute = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!pendingConfirm) return;
    setConfirmBusy(true);
    try {
      await pendingConfirm.onConfirm(confirmReason.trim());
      setPendingConfirm(null);
      setConfirmReason("");
    } finally {
      setConfirmBusy(false);
    }
  };

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
          department: newDepartment || undefined,
          password: newPassword,
          role_ids: newRoleId ? [newRoleId] : [],
          reason: isFa ? "ایجاد کاربر جدید از پنل مدیریت دسترسی" : "Created via security console",
        }),
      });
      setNewUsername("");
      setNewEmail("");
      setNewFullName("");
      setNewDepartment("");
      setNewPassword("");
      notifySuccess(isFa ? "کاربر جدید با موفقیت ایجاد شد." : "User created.");
      await loadAll();
    } catch (err) {
      notifyError(err, isFa ? "ایجاد کاربر ناموفق بود" : "Failed to create user");
    }
  };

  const openEditUser = (u: any) => {
    setEditingUser(u);
    setEditUserFullName(u.full_name || "");
    setEditUserEmail(u.email || "");
    setEditUserDepartment(u.department || "");
    setEditUserRegion(u.region || "");
    setEditUserRoleIds(u.role_ids || []);
    setEditUserGroupIds(u.group_ids || []);
    setEditUserActive(Boolean(u.is_active));
    setEditUserPassword("");
  };

  const submitEditUser = (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingUser) return;
    const oldRoles = (editingUser.role_ids || []).map((id: string) => formatRoleLabel(roleMap[id] || { name: id }, isFa)).join("، ") || "بدون نقش";
    const newRoles = editUserRoleIds.map((id) => formatRoleLabel(roleMap[id] || { name: id }, isFa)).join("، ") || "بدون نقش";
    askConfirm({
      title: isFa ? `تأیید ویرایش کاربر «${editingUser.username}»` : `Confirm User Edit: ${editingUser.username}`,
      summary: isFa
        ? "تغییر نقش، عضویت گروه یا وضعیت حساب کاربری بلافاصله روی دسترسی‌های کاربر اعمال و در ممیزی ثبت می‌شود."
        : "Changes take effect immediately and are recorded in the security audit trail.",
      beforeText: `${isFa ? "وضعیت" : "Active"}: ${editingUser.is_active ? "فعال" : "غیرفعال"} | ${isFa ? "نقش‌ها" : "Roles"}: ${oldRoles}`,
      afterText: `${isFa ? "وضعیت" : "Active"}: ${editUserActive ? "فعال" : "غیرفعال"} | ${isFa ? "نقش‌ها" : "Roles"}: ${newRoles}${editUserPassword ? " | بازنشانی رمز عبور" : ""}`,
      danger: !editUserActive,
      onConfirm: async (reason) => {
        try {
          await apiFetch(`/rbac/users/${editingUser.id}`, {
            method: "PUT",
            body: JSON.stringify({
              email: editUserEmail,
              full_name: editUserFullName,
              department: editUserDepartment || undefined,
              region: editUserRegion || undefined,
              is_active: editUserActive,
              role_ids: editUserRoleIds,
              group_ids: editUserGroupIds,
              reset_password: editUserPassword || undefined,
              reason: reason || (isFa ? "ویرایش دسترسی و مشخصات کاربر" : "User role/profile update"),
            }),
          });
          setEditingUser(null);
          notifySuccess(isFa ? `مشخصات و دسترسی‌های کاربر ${editingUser.username} به‌روزرسانی شد.` : "User updated.");
          await loadAll();
        } catch (err) {
          notifyError(err, isFa ? "به‌روزرسانی کاربر ناموفق بود" : "Failed to update user");
        }
      },
    });
  };

  const handleToggleUserStatus = (u: any, patch: Record<string, any>, actionDescFa: string, danger = false) => {
    const beforeState = `${u.is_active ? "فعال" : "غیرفعال"} / ${u.is_locked ? "قفل" : "باز"}`;
    const nextActive = patch.is_active !== undefined ? patch.is_active : u.is_active;
    const nextLocked = patch.is_locked !== undefined ? patch.is_locked : u.is_locked;
    const afterState = `${nextActive ? "فعال" : "غیرفعال"} / ${nextLocked ? "قفل" : "باز"}`;
    askConfirm({
      title: isFa ? `تأیید ${actionDescFa} (${u.username})` : `Confirm ${actionDescFa}`,
      summary: isFa
        ? "در صورت غیرفعال یا قفل‌شدن، نشست‌های فعال کاربر فوراً ابطال می‌شوند."
        : "Disabling or locking a user immediately revokes active sessions.",
      beforeText: beforeState,
      afterText: afterState,
      danger,
      onConfirm: async (reason) => {
        try {
          await apiFetch(`/rbac/users/${u.id}`, {
            method: "PUT",
            body: JSON.stringify({ ...patch, reason: reason || actionDescFa }),
          });
          notifySuccess(isFa ? `${actionDescFa} برای «${u.username}» انجام شد.` : "Updated.");
          await loadAll();
        } catch (err) {
          notifyError(err);
        }
      },
    });
  };

  const handleDeleteUser = (u: any) => {
    askConfirm({
      title: isFa ? `تأیید حذف کاربر «${u.username}»` : `Confirm Delete User ${u.username}`,
      summary: isFa
        ? "حذف حساب کاربری برگشت‌پذیر نیست (سابقه ممیزی در سیستم باقی می‌ماند). امکان حذف آخرین مدیر ارشد وجود ندارد."
        : "Deleting a user is permanent; audit logs remain.",
      beforeText: `${u.username} (${(u.roles || []).join(", ") || "کاربر"})`,
      afterText: isFa ? "حذف کامل حساب کاربری" : "User deleted",
      danger: true,
      onConfirm: async () => {
        try {
          await apiFetch(`/rbac/users/${u.id}`, { method: "DELETE" });
          notifySuccess(isFa ? `کاربر «${u.username}» حذف شد.` : "User deleted.");
          await loadAll();
        } catch (err) {
          notifyError(err);
        }
      },
    });
  };

  const openEffectivePermissions = async (u: any) => {
    setEffectiveModalUser(u);
    setEffectiveData(null);
    setEffectiveLoading(true);
    try {
      const res = await apiFetch(`/rbac/permissions/effective/${u.id}`);
      setEffectiveData(res);
    } catch (err) {
      notifyError(err, isFa ? "دریافت مجوزهای مؤثر ناموفق بود" : "Failed to inspect effective permissions");
      setEffectiveModalUser(null);
    } finally {
      setEffectiveLoading(false);
    }
  };

  const openRoleEditor = (r: any) => {
    setEditingRole(r);
    setEditRoleName(r.name || "");
    setEditRoleDesc(r.description || (ROLE_META_FA[r.slug]?.description || ""));
    setEditRolePermIds(Array.isArray(r.permission_ids) ? r.permission_ids : []);
    setRolePermSearch("");
  };

  const submitRoleEditor = (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingRole) return;
    const oldCount = (editingRole.permission_ids || []).length;
    const newCount = editRolePermIds.length;
    askConfirm({
      title: isFa ? `تأیید ویرایش نقش «${formatRoleLabel(editingRole, true)}»` : `Update Role ${editingRole.name}`,
      summary: isFa
        ? "تمام کاربران متصل به این نقش بلافاصله مجوزهای جدید را دریافت می‌کنند و کش مجوزها باطل می‌شود."
        : "All assigned users receive updated permissions immediately.",
      beforeText: `${editingRole.name} (${oldCount} مجوز)`,
      afterText: `${editRoleName} (${newCount} مجوز)`,
      onConfirm: async (reason) => {
        try {
          await apiFetch(`/rbac/roles/${editingRole.id}`, {
            method: "PUT",
            body: JSON.stringify({
              name: editRoleName,
              slug: editingRole.slug,
              description: editRoleDesc,
              permission_ids: editRolePermIds,
              reason: reason || "ویرایش نام، توضیحات و مجوزهای نقش",
            }),
          });
          setEditingRole(null);
          notifySuccess(isFa ? `نقش «${editRoleName}» با موفقیت ذخیره شد.` : "Role updated.");
          await loadAll();
        } catch (err) {
          notifyError(err);
        }
      },
    });
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
          description: newRoleDesc.trim() || "نقش سفارشی سازمانی",
          permission_ids: [],
          reason: isFa ? "ایجاد نقش جدید" : "Role created",
        }),
      });
      setNewRoleName("");
      setNewRoleSlug("");
      setNewRoleDesc("");
      notifySuccess(isFa ? "نقش جدید ایجاد شد. از دکمه «ویرایش مجوزها» دسترسی‌های آن را انتخاب کنید." : "Role created.");
      await loadAll();
    } catch (err) {
      notifyError(err, isFa ? "ایجاد نقش ناموفق بود" : "Failed to create role");
    }
  };

  const handleCloneRole = async (roleId: string) => {
    try {
      await apiFetch(`/rbac/roles/${roleId}/clone`, { method: "POST" });
      notifySuccess(isFa ? "نسخه کپی از نقش ساخته شد." : "Role cloned.");
      await loadAll();
    } catch (err) {
      notifyError(err);
    }
  };

  const handleDeleteRole = (r: any) => {
    askConfirm({
      title: isFa ? `حذف نقش «${formatRoleLabel(r, true)}»` : `Delete Role ${r.name}`,
      summary: isFa ? "فقط نقش‌های سفارشی که به هیچ کاربری متصل نیستند قابل حذف هستند." : "Only unassigned custom roles can be deleted.",
      beforeText: `${r.name} (${r.slug})`,
      afterText: isFa ? "حذف نقش" : "Role deleted",
      danger: true,
      onConfirm: async () => {
        try {
          await apiFetch(`/rbac/roles/${r.id}`, { method: "DELETE" });
          notifySuccess(isFa ? "نقش با موفقیت حذف شد." : "Role deleted.");
          await loadAll();
        } catch (err) {
          notifyError(err);
        }
      },
    });
  };

  const handleToggleRoleBasePerm = (role: any, perm: any, hasPerm: boolean) => {
    const current: string[] = Array.isArray(role.permission_ids) ? role.permission_ids : [];
    const nextIds = hasPerm ? current.filter((id) => id !== perm.id) : [...current, perm.id];
    const isSensitive = perm.resource_type === "admin" || perm.action === "public_restricted_override";
    const runSave = async (reason = "") => {
      try {
        await apiFetch(`/rbac/roles/${role.id}`, {
          method: "PUT",
          body: JSON.stringify({
            name: role.name,
            slug: role.slug,
            description: role.description || "",
            permission_ids: nextIds,
            reason: reason || `تغییر مجوز ${perm.resource_type}:${perm.action}`,
          }),
        });
        notifySuccess(isFa ? "مجوز نقش به‌روزرسانی و کش دسترسی‌ها باطل شد." : "Role permission updated.");
        await loadAll();
      } catch (err) {
        notifyError(err);
      }
    };

    if (isSensitive) {
      askConfirm({
        title: isFa ? "تأیید تغییر مجوز حساس نقش" : "Confirm Sensitive Role Permission Change",
        summary: `${formatRoleLabel(role, true)} ← ${formatScopeLabel(perm.resource_type, true)} : ${formatActionLabel(perm.action, true)}`,
        beforeText: hasPerm ? "مجاز (Allow)" : "غیرفعال",
        afterText: hasPerm ? "حذف مجوز" : "مجاز (Allow)",
        danger: true,
        onConfirm: runSave,
      });
    } else {
      runSave();
    }
  };

  const handleCreateGroup = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await apiFetch("/rbac/groups", {
        method: "POST",
        body: JSON.stringify({
          name: newGroupName.trim(),
          description: isFa ? "گروه دسترسی سازمانی" : "Local RBAC Group",
          role_ids: newGroupRoleId ? [newGroupRoleId] : [],
          member_user_ids: [],
          reason: "ایجاد گروه دسترسی جدید",
        }),
      });
      setNewGroupName("");
      notifySuccess(isFa ? "گروه دسترسی ایجاد شد." : "Group created.");
      await loadAll();
    } catch (err) {
      notifyError(err);
    }
  };

  const toggleMatrixCell = (
    subjectType: string,
    subjectId: string,
    subjectLabel: string,
    resourceType: string,
    resourceId: string,
    resourceLabel: string,
    action: string,
    currentEffect?: string
  ) => {
    const nextEffect =
      matrixClickMode === "remove"
        ? "remove"
        : currentEffect === "allow"
        ? "deny"
        : "allow";

    const exec = async (reason = "") => {
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
            reason: reason || `تغییر قانون ماتریس روی ${resourceLabel}`,
          }),
        });
        notifySuccess(
          isFa
            ? `دسترسی «${formatActionLabel(action, true)}» برای ${subjectLabel} روی «${resourceLabel}» به حالت ${
                nextEffect === "allow" ? "مجاز" : nextEffect === "deny" ? "مسدود (Deny)" : "حذف قانون"
              } تغییر یافت.`
            : `Updated ${action} -> ${nextEffect}`
        );
        await loadAll();
      } catch (err) {
        notifyError(err);
      }
    };

    if (nextEffect === "deny" || action === "public_restricted_override") {
      askConfirm({
        title: isFa ? "تأیید تغییر قانون دسترسی" : "Confirm Matrix Permission Change",
        summary: `${subjectLabel} ← ${resourceLabel} (${formatActionLabel(action, true)})`,
        beforeText: currentEffect || (isFa ? "تنظیم‌نشده" : "Unset"),
        afterText: nextEffect === "deny" ? (isFa ? "مسدود صریح (Deny)" : "Explicit Deny") : nextEffect,
        danger: nextEffect === "deny",
        onConfirm: exec,
      });
    } else {
      exec();
    }
  };

  const handleCopyPerms = async () => {
    if (!copySourceId || !copyTargetId) return;
    try {
      const res = await apiFetch<any>("/rbac/permissions/matrix/copy", {
        method: "POST",
        body: JSON.stringify({
          from_subject_type: copySubjectType,
          from_subject_id: copySourceId,
          to_subject_type: copySubjectType,
          to_subject_id: copyTargetId,
          reason: "کپی دسته‌ای قوانین ماتریس دسترسی",
        }),
      });
      const count = res.copied ?? res.copied_rules ?? 0;
      notifySuccess(isFa ? `${count} قانون دسترسی با موفقیت کپی شد.` : `Copied ${count} rules.`);
      await loadAll();
    } catch (err) {
      notifyError(err);
    }
  };

  const primaryAd = adConns[0];

  const handleTestAd = async () => {
    if (!primaryAd) return;
    try {
      const res = await apiFetch<any>(`/rbac/ad/connections/${primaryAd.id}/test`, { method: "POST" });
      notifySuccess(res.ok ? `اتصال برقرار شد: ${res.message}` : `خطا: ${res.error}`);
    } catch (err) {
      notifyError(err);
    }
  };

  const handleSearchAd = async () => {
    if (!primaryAd) return;
    try {
      const res = await apiFetch(`/rbac/ad/connections/${primaryAd.id}/search?q=${encodeURIComponent(adSearchQ)}`);
      setAdPreview(res);
    } catch (err) {
      notifyError(err);
    }
  };

  const handleSyncAd = async () => {
    if (!primaryAd) return;
    try {
      const res = await apiFetch<any>(`/rbac/ad/connections/${primaryAd.id}/sync`, { method: "POST" });
      setAdSyncResult(res);
      notifySuccess(
        isFa
          ? `همگام‌سازی AD انجام شد: ${res.users_imported} جدید، ${res.users_updated} به‌روزرسانی، ${res.users_deactivated} غیرفعال`
          : `AD Synced`
      );
      await loadAll();
    } catch (err) {
      notifyError(err);
    }
  };

  const handleAddRls = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await apiFetch("/rbac/policies", {
        method: "POST",
        body: JSON.stringify({
          name: rlsName,
          policy_type: "rls",
          dataset_id: rlsDs || null,
          target_type: rlsSubjectId ? "user" : "all",
          target_id: rlsSubjectId || null,
          rule_json: { column: rlsCol.trim(), operator: rlsOp, value: rlsVal },
          priority: 15,
          reason: "افزودن سیاست دسترسی سطری RLS",
        }),
      });
      setRlsName("");
      notifySuccess(isFa ? "سیاست محدودسازی سطری (RLS) ذخیره شد و کش کوئری‌ها باطل گردید." : "RLS saved.");
      await loadAll();
    } catch (err) {
      notifyError(err);
    }
  };

  const handleAddCls = async (e: React.FormEvent) => {
    e.preventDefault();
    const colName = clsCol.trim();
    if (!colName) return;
    try {
      const isDeny = clsRule === "deny";
      await apiFetch("/rbac/policies", {
        method: "POST",
        body: JSON.stringify({
          name: isFa ? `${isDeny ? "مخفی‌سازی" : "پوشاندن"} ستون ${colName}` : `${isDeny ? "Hide" : "Mask"} ${colName}`,
          policy_type: isDeny ? "cls" : "masking",
          dataset_id: clsDs || null,
          target_type: clsSubjectId ? "role" : "all",
          target_id: clsSubjectId || null,
          rule_json: isDeny ? { hidden_columns: [colName], column: colName, strategy: "deny" } : { column: colName, strategy: clsRule },
          priority: 25,
          reason: "افزودن سیاست امنیت ستونی/ماسک‌کردن",
        }),
      });
      notifySuccess(isFa ? "قانون امنیت ستونی / ماسک‌سازی ذخیره شد." : "CLS saved.");
      await loadAll();
    } catch (err) {
      notifyError(err);
    }
  };

  const handleDeletePolicy = (p: any) => {
    askConfirm({
      title: isFa ? `حذف سیاست امنیتی «${p.name}»` : `Delete Policy ${p.name}`,
      summary: isFa ? "پس از حذف این سیاست، محدودیت سطری یا ستونی آن برای کاربران هدف برداشته می‌شود." : "Removing this policy unrestricts affected users.",
      beforeText: `${p.name} (${p.policy_type})`,
      afterText: isFa ? "حذف سیاست" : "Policy deleted",
      danger: true,
      onConfirm: async () => {
        try {
          await apiFetch(`/rbac/policies/${p.id}`, { method: "DELETE" });
          notifySuccess(isFa ? "سیاست امنیتی حذف شد." : "Policy deleted.");
          await loadAll();
        } catch (err) {
          notifyError(err);
        }
      },
    });
  };

  const rules = matrix?.rules || [];
  const findRule = (subjectType: string, subjectId: string, resourceType: string, resourceId: string, action: string) =>
    rules.find(
      (r: any) =>
        r.subject_type === subjectType &&
        r.subject_id === subjectId &&
        r.resource_type === resourceType &&
        r.resource_id === resourceId &&
        r.action === action
    );

  const rlsPolicies = policies.filter((p) => p.policy_type === "rls");
  const clsPolicies = policies.filter((p) => p.policy_type !== "rls");

  const filteredCatalog = useMemo(() => {
    const q = matrixPermFilter.trim().toLowerCase();
    if (!q) return catalog;
    return catalog.filter((p) =>
      `${p.resource_type} ${p.action} ${p.description || ""} ${formatScopeLabel(p.resource_type, true)} ${formatActionLabel(p.action, true)}`
        .toLowerCase()
        .includes(q)
    );
  }, [catalog, matrixPermFilter]);

  if (!hasAdminView) {
    return (
      <AppShell>
        <div className="bi-card p-8 text-center space-y-3 max-w-lg mx-auto mt-10">
          <ShieldAlert className="w-10 h-10 text-red-500 mx-auto" />
          <h1 className="text-base font-bold">
            {isFa ? "عدم دسترسی به پنل مدیریت امنیت (۴۰۳)" : "Insufficient Permissions (403)"}
          </h1>
          <p className="text-xs" style={{ color: "var(--bi-muted)" }}>
            {isFa
              ? "حساب کاربری شما دارای مجوز «مدیریت سیستم: مشاهده (admin:view)» نیست. برای دریافت دسترسی با مدیر ارشد سامانه تماس بگیرید."
              : "You do not have admin:view permission to access this console."}
          </p>
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <div dir={isFa ? "rtl" : "ltr"} className="space-y-6">
        {/* Header */}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-bold flex items-center gap-2">
              <ShieldCheck className="w-5 h-5 text-emerald-500" />
              {isFa ? "مدیریت کاربران، نقش‌ها، سطوح دسترسی و امنیت" : "Users, Roles, Permissions & Security Governance"}
            </h1>
            <p className="text-xs mt-1" style={{ color: "var(--bi-muted)" }}>
              {isFa
                ? "مدیریت متمرکز حساب‌های کاربری، نقش‌ها با شرح فارسی، مجوزهای مؤثر، ماتریس دسترسی، RLS/CLS، همگام‌سازی Active Directory و تاریخچه ممیزی"
                : "Central RBAC, effective permissions, matrix overrides, RLS/CLS masking, AD sync, and security audit trail"}
            </p>
          </div>
          <button
            type="button"
            onClick={loadAll}
            disabled={loading}
            className="px-3 py-1.5 rounded-lg border text-xs font-semibold flex items-center gap-1.5 hover:opacity-80 disabled:opacity-50"
            style={{ borderColor: "var(--bi-border)" }}
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
            {isFa ? "به‌روزرسانی اطلاعات" : "Refresh"}
          </button>
        </div>

        {/* Partial / Error / Success banners */}
        {loadErrors.length > 0 && (
          <div className="p-3 rounded-lg border border-amber-500/40 bg-amber-500/10 text-xs space-y-1">
            <div className="font-bold flex items-center gap-1.5 text-amber-500">
              <AlertTriangle className="w-4 h-4" />
              {isFa ? "برخی بخش‌ها با خطای بارگذاری مواجه شدند:" : "Some sections could not be loaded:"}
            </div>
            {loadErrors.map((e, i) => (
              <div key={i} className="text-[11px] opacity-90">
                <b>{e.section}:</b> {e.message}
              </div>
            ))}
          </div>
        )}

        {actionError && (
          <div className="p-3 rounded-lg border border-red-500/40 bg-red-500/10 text-xs text-red-500 font-semibold flex items-center justify-between gap-2">
            <span>{actionError}</span>
            <button type="button" onClick={() => setActionError("")} className="p-1 opacity-70 hover:opacity-100">
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        )}

        {actionSuccess && (
          <div className="p-3 rounded-lg border border-emerald-500/40 bg-emerald-500/10 text-xs text-emerald-500 font-semibold flex items-center justify-between gap-2">
            <span className="flex items-center gap-1.5">
              <CheckCircle2 className="w-4 h-4" /> {actionSuccess}
            </span>
            <button type="button" onClick={() => setActionSuccess("")} className="p-1 opacity-70 hover:opacity-100">
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        )}

        {/* Navigation Tabs */}
        <div className="flex flex-wrap gap-2">
          {[
            { id: "users", label: isFa ? `کاربران، نقش‌ها و گروه‌ها (${users.length})` : `Users, Roles & Groups (${users.length})`, icon: Users },
            { id: "matrix", label: isFa ? `ماتریس دسترسی‌ها و کاتالوگ (${catalog.length})` : `Permission Matrix (${catalog.length})`, icon: KeyRound },
            { id: "rls", label: isFa ? `امنیت سطری و ستونی RLS/CLS (${policies.length})` : `RLS & Column Masking (${policies.length})`, icon: Lock },
            { id: "ad", label: isFa ? "همگام‌سازی Active Directory / LDAP" : "Active Directory / LDAP", icon: Server },
            { id: "audit", label: isFa ? `تاریخچه ممیزی تغییرات (${auditLogs.length})` : `Audit Trail (${auditLogs.length})`, icon: ClipboardList },
          ].map((tb) => {
            const Icon = tb.icon;
            const active = tab === tb.id;
            return (
              <button
                key={tb.id}
                type="button"
                onClick={() => setTab(tb.id as any)}
                className="px-3.5 py-2 rounded-lg text-xs font-semibold border flex items-center gap-1.5 transition"
                style={{
                  background: active ? "var(--bi-primary)" : "transparent",
                  color: active ? "#ffffff" : "var(--bi-text)",
                  borderColor: active ? "var(--bi-primary)" : "var(--bi-border)",
                }}
              >
                <Icon className="w-3.5 h-3.5" />
                {tb.label}
              </button>
            );
          })}
        </div>

        {/* TAB 1: USERS, ROLES & GROUPS */}
        {tab === "users" && (
          <div className="space-y-6">
            {/* Search & Multi-filter toolbar + Users Table */}
            <div className="bi-card p-5 space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 className="text-sm font-bold flex items-center gap-1.5">
                  <Users className="w-4 h-4 text-blue-500" />
                  {isFa ? "فهرست کاربران سازمان" : "Organization Users"}
                </h2>
                <div className="text-[11px]" style={{ color: "var(--bi-muted)" }}>
                  {isFa
                    ? `نمایش ${pagedUsers.length} از ${filteredUsers.length} کاربر مطابق فیلتر (مجموع کل: ${users.length})`
                    : `Showing ${pagedUsers.length} of ${filteredUsers.length}`}
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-2 text-xs">
                <div className="relative lg:col-span-2">
                  <Search className="w-3.5 h-3.5 absolute top-2.5 start-2.5 opacity-55" />
                  <input
                    value={userSearch}
                    onChange={(e) => {
                      setUserSearch(e.target.value);
                      setUserPage(1);
                    }}
                    placeholder={isFa ? "جستجو بر اساس نام کاربری، نام، ایمیل یا واحد..." : "Search username, name, email..."}
                    className="w-full ps-8 pe-3 py-2 rounded-lg border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  />
                </div>
                <select
                  value={userRoleFilter}
                  onChange={(e) => {
                    setUserRoleFilter(e.target.value);
                    setUserPage(1);
                  }}
                  className="px-2.5 py-2 rounded-lg border bg-transparent"
                  style={{ borderColor: "var(--bi-border)" }}
                >
                  <option value="all">{isFa ? "همه نقش‌ها" : "All Roles"}</option>
                  {roles.map((r) => (
                    <option key={r.id} value={r.id}>
                      {formatRoleLabel(r, isFa)}
                    </option>
                  ))}
                </select>
                <select
                  value={userStatusFilter}
                  onChange={(e) => {
                    setUserStatusFilter(e.target.value as any);
                    setUserPage(1);
                  }}
                  className="px-2.5 py-2 rounded-lg border bg-transparent"
                  style={{ borderColor: "var(--bi-border)" }}
                >
                  <option value="all">{isFa ? "همه وضعیت‌ها" : "All Statuses"}</option>
                  <option value="active">{isFa ? "فقط فعال" : "Active"}</option>
                  <option value="inactive">{isFa ? "غیرفعال" : "Inactive"}</option>
                  <option value="locked">{isFa ? "قفل‌شده" : "Locked"}</option>
                </select>
                <div className="flex gap-1.5">
                  <select
                    value={userSourceFilter}
                    onChange={(e) => {
                      setUserSourceFilter(e.target.value as any);
                      setUserPage(1);
                    }}
                    className="flex-1 px-2 py-2 rounded-lg border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  >
                    <option value="all">{isFa ? "همه منابع" : "All Sources"}</option>
                    <option value="local">{isFa ? "داخلی" : "Local"}</option>
                    <option value="active_directory">Active Directory</option>
                  </select>
                  <select
                    value={userSort}
                    onChange={(e) => setUserSort(e.target.value as any)}
                    className="px-2 py-2 rounded-lg border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                    title={isFa ? "مرتب‌سازی" : "Sort"}
                  >
                    <option value="username">{isFa ? "نام کاربری" : "Username"}</option>
                    <option value="full_name">{isFa ? "نام کامل" : "Full Name"}</option>
                    <option value="last_login">{isFa ? "آخرین ورود" : "Last Login"}</option>
                  </select>
                </div>
              </div>

              {loading && users.length === 0 ? (
                <div className="py-10 text-center text-xs opacity-70">
                  {isFa ? "در حال دریافت فهرست کاربران و نقش‌ها..." : "Loading users..."}
                </div>
              ) : filteredUsers.length === 0 ? (
                <div className="py-10 text-center text-xs border rounded-xl space-y-2" style={{ borderColor: "var(--bi-border)" }}>
                  <Filter className="w-6 h-6 mx-auto opacity-50" />
                  <div className="font-semibold">
                    {users.length === 0
                      ? isFa
                        ? "هنوز کاربری در سامانه ثبت نشده است."
                        : "No users found."
                      : isFa
                      ? "هیچ کاربری با فیلترهای انتخابی یافت نشد."
                      : "No users match the active filters."}
                  </div>
                  {(userSearch || userRoleFilter !== "all" || userStatusFilter !== "all" || userSourceFilter !== "all") && (
                    <button
                      type="button"
                      onClick={() => {
                        setUserSearch("");
                        setUserRoleFilter("all");
                        setUserStatusFilter("all");
                        setUserSourceFilter("all");
                      }}
                      className="px-3 py-1 rounded border text-[11px]"
                      style={{ borderColor: "var(--bi-border)" }}
                    >
                      {isFa ? "پاک‌کردن فیلترها" : "Clear filters"}
                    </button>
                  )}
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b opacity-75" style={{ borderColor: "var(--bi-border)" }}>
                        <th className="py-2 text-start">{isFa ? "نام کاربری" : "Username"}</th>
                        <th className="py-2 text-start">{isFa ? "نام و ایمیل" : "Name / Email"}</th>
                        <th className="py-2 text-start">{isFa ? "واحد / منطقه" : "Dept / Region"}</th>
                        <th className="py-2 text-start">{isFa ? "منبع ورود" : "Source"}</th>
                        <th className="py-2 text-start">{isFa ? "نقش‌ها" : "Roles"}</th>
                        <th className="py-2 text-start">{isFa ? "وضعیت" : "Status"}</th>
                        <th className="py-2 text-end">{isFa ? "عملیات و مجوزها" : "Actions"}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {pagedUsers.map((u) => {
                        const userRoles = (u.role_ids || []).map((rid: string) => roleMap[rid]).filter(Boolean);
                        const userGroups = (u.group_ids || []).map((gid: string) => groupMap[gid]?.name).filter(Boolean);
                        return (
                          <tr key={u.id} className="border-b hover:bg-slate-500/5" style={{ borderColor: "var(--bi-border)" }}>
                            <td className="py-2.5 font-bold whitespace-nowrap">
                              <div>{u.username}</div>
                              {u.id === user?.id && (
                                <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-500 font-semibold">
                                  {isFa ? "شما" : "You"}
                                </span>
                              )}
                            </td>
                            <td className="py-2.5">
                              <div className="font-medium">{u.full_name || "—"}</div>
                              <div className="text-[10px] opacity-70">{u.email}</div>
                            </td>
                            <td className="py-2.5 text-[11px]">
                              <div>{u.department || "—"}</div>
                              {u.region && <div className="opacity-65">{u.region}</div>}
                            </td>
                            <td className="py-2.5">
                              <span className="px-2 py-0.5 rounded border text-[10px]" style={{ borderColor: "var(--bi-border)" }}>
                                {u.auth_source === "active_directory" ? "Active Directory" : isFa ? "داخلی" : "Local"}
                              </span>
                            </td>
                            <td className="py-2.5">
                              <div className="flex flex-wrap gap-1">
                                {userRoles.length > 0 ? (
                                  userRoles.map((r: any) => (
                                    <span
                                      key={r.id}
                                      className={`px-2 py-0.5 rounded text-[10px] font-semibold ${
                                        r.is_super_admin
                                          ? "bg-purple-500/15 text-purple-500"
                                          : "bg-blue-500/15 text-blue-500"
                                      }`}
                                    >
                                      {formatRoleLabel(r, isFa)}
                                    </span>
                                  ))
                                ) : (
                                  <span className="text-[10px] opacity-55">{isFa ? "بدون نقش" : "No role"}</span>
                                )}
                              </div>
                              {userGroups.length > 0 && (
                                <div className="text-[10px] opacity-65 mt-0.5">
                                  {isFa ? "گروه‌ها: " : "Groups: "}
                                  {userGroups.join("، ")}
                                </div>
                              )}
                            </td>
                            <td className="py-2.5">
                              {u.is_locked ? (
                                <span className="px-2 py-0.5 rounded bg-red-500/15 text-red-500 font-semibold">
                                  {isFa ? "قفل‌شده" : "Locked"}
                                </span>
                              ) : u.is_active ? (
                                <span className="px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-500 font-semibold">
                                  {isFa ? "فعال" : "Active"}
                                </span>
                              ) : (
                                <span className="px-2 py-0.5 rounded bg-amber-500/15 text-amber-500 font-semibold">
                                  {isFa ? "غیرفعال" : "Disabled"}
                                </span>
                              )}
                            </td>
                            <td className="py-2.5 text-end">
                              <div className="inline-flex flex-wrap justify-end gap-1">
                                <button
                                  type="button"
                                  onClick={() => openEffectivePermissions(u)}
                                  className="px-2 py-1 rounded border text-[11px] hover:bg-blue-500/10 flex items-center gap-1"
                                  style={{ borderColor: "var(--bi-border)" }}
                                  title={isFa ? "مشاهده مجوزهای مؤثر کاربر" : "Inspect effective permissions"}
                                >
                                  <Eye className="w-3 h-3 text-blue-500" />
                                  {isFa ? "مجوزهای مؤثر" : "Effective"}
                                </button>
                                <button
                                  type="button"
                                  onClick={() => openEditUser(u)}
                                  className="px-2 py-1 rounded border text-[11px] hover:bg-blue-500/10 flex items-center gap-1"
                                  style={{ borderColor: "var(--bi-border)" }}
                                >
                                  <Edit3 className="w-3 h-3" />
                                  {isFa ? "ویرایش" : "Edit"}
                                </button>
                                {u.is_locked ? (
                                  <button
                                    type="button"
                                    onClick={() => handleToggleUserStatus(u, { is_locked: false }, "باز کردن قفل حساب")}
                                    className="px-2 py-1 rounded border text-[11px] text-emerald-500 flex items-center gap-1"
                                    style={{ borderColor: "var(--bi-border)" }}
                                  >
                                    <Unlock className="w-3 h-3" /> {isFa ? "باز کردن قفل" : "Unlock"}
                                  </button>
                                ) : (
                                  <button
                                    type="button"
                                    onClick={() =>
                                      handleToggleUserStatus(
                                        u,
                                        { is_active: !u.is_active },
                                        u.is_active ? "غیرفعال‌سازی حساب" : "فعال‌سازی مجدد حساب",
                                        u.is_active
                                      )
                                    }
                                    disabled={u.id === user?.id && u.is_active}
                                    className={`px-2 py-1 rounded border text-[11px] ${
                                      u.id === user?.id && u.is_active ? "opacity-40 cursor-not-allowed" : "hover:bg-amber-500/10"
                                    }`}
                                    style={{ borderColor: "var(--bi-border)" }}
                                  >
                                    {u.is_active ? (isFa ? "غیرفعال‌سازی" : "Disable") : isFa ? "فعال‌سازی" : "Enable"}
                                  </button>
                                )}
                                <button
                                  type="button"
                                  disabled={u.id === user?.id}
                                  onClick={() => handleDeleteUser(u)}
                                  className={`p-1.5 rounded border text-red-500 ${
                                    u.id === user?.id ? "opacity-35 cursor-not-allowed" : "hover:bg-red-500/10"
                                  }`}
                                  style={{ borderColor: "var(--bi-border)" }}
                                  title={isFa ? "حذف کاربر" : "Delete user"}
                                >
                                  <Trash2 className="w-3 h-3" />
                                </button>
                              </div>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}

              {/* Pagination */}
              {totalUserPages > 1 && (
                <div className="flex items-center justify-between pt-2 text-xs">
                  <span style={{ color: "var(--bi-muted)" }}>
                    {isFa ? `صفحه ${userPage} از ${totalUserPages}` : `Page ${userPage} of ${totalUserPages}`}
                  </span>
                  <div className="flex gap-1.5">
                    <button
                      type="button"
                      disabled={userPage <= 1}
                      onClick={() => setUserPage((p) => Math.max(1, p - 1))}
                      className="px-2.5 py-1 rounded border disabled:opacity-40"
                      style={{ borderColor: "var(--bi-border)" }}
                    >
                      {isFa ? "قبلی" : "Prev"}
                    </button>
                    <button
                      type="button"
                      disabled={userPage >= totalUserPages}
                      onClick={() => setUserPage((p) => Math.min(totalUserPages, p + 1))}
                      className="px-2.5 py-1 rounded border disabled:opacity-40"
                      style={{ borderColor: "var(--bi-border)" }}
                    >
                      {isFa ? "بعدی" : "Next"}
                    </button>
                  </div>
                </div>
              )}
            </div>

            {/* Create User + Manage Roles + Manage Groups */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
              {/* Create Local User */}
              <form onSubmit={handleCreateUser} className="bi-card p-5 space-y-3 text-xs">
                <h2 className="text-sm font-bold flex items-center gap-1.5">
                  <Plus className="w-4 h-4 text-blue-500" /> {isFa ? "ایجاد کاربر جدید" : "Create Local User"}
                </h2>
                <input
                  value={newUsername}
                  onChange={(e) => setNewUsername(e.target.value)}
                  placeholder={isFa ? "نام کاربری (انگلیسی، مثلاً a.mohammadi)" : "Username"}
                  className="w-full px-3 py-2 rounded border bg-transparent"
                  style={{ borderColor: "var(--bi-border)" }}
                  required
                />
                <input
                  type="email"
                  value={newEmail}
                  onChange={(e) => setNewEmail(e.target.value)}
                  placeholder={isFa ? "ایمیل سازمانی" : "Email"}
                  className="w-full px-3 py-2 rounded border bg-transparent"
                  style={{ borderColor: "var(--bi-border)" }}
                  required
                />
                <div className="grid grid-cols-2 gap-2">
                  <input
                    value={newFullName}
                    onChange={(e) => setNewFullName(e.target.value)}
                    placeholder={isFa ? "نام و نام خانوادگی" : "Full Name"}
                    className="w-full px-3 py-2 rounded border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  />
                  <input
                    value={newDepartment}
                    onChange={(e) => setNewDepartment(e.target.value)}
                    placeholder={isFa ? "واحد سازمانی" : "Department"}
                    className="w-full px-3 py-2 rounded border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  />
                </div>
                <input
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  placeholder={isFa ? "رمز عبور قوی (حداقل ۱۰ کاراکتر + حروف و عدد)" : "Strong password"}
                  className="w-full px-3 py-2 rounded border bg-transparent"
                  style={{ borderColor: "var(--bi-border)" }}
                  required
                />
                <div>
                  <label className="block text-[11px] font-semibold mb-1">{isFa ? "نقش اولیه کاربر" : "Initial Role"}</label>
                  <select
                    value={newRoleId}
                    onChange={(e) => setNewRoleId(e.target.value)}
                    className="w-full px-3 py-2 rounded border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  >
                    {roles.length === 0 && <option value="">{isFa ? "نقشی موجود نیست" : "No roles"}</option>}
                    {roles.map((r) => (
                      <option key={r.id} value={r.id}>
                        {formatRoleLabel(r, isFa)}
                      </option>
                    ))}
                  </select>
                </div>
                <button type="submit" className="w-full py-2 rounded-lg text-white font-semibold" style={{ background: "var(--bi-primary)" }}>
                  {isFa ? "ثبت کاربر جدید" : "Create User"}
                </button>
              </form>

              {/* Roles Card */}
              <div className="bi-card p-5 space-y-3 text-xs">
                <h2 className="text-sm font-bold">{isFa ? `مدیریت نقش‌ها (${roles.length})` : `Roles (${roles.length})`}</h2>
                <div className="space-y-1.5 max-h-60 overflow-y-auto">
                  {roles.map((r) => {
                    const desc = r.description && !/^Built-in/i.test(r.description)
                      ? r.description
                      : ROLE_META_FA[r.slug]?.description || r.description || "نقش سازمانی";
                    return (
                      <div key={r.id} className="p-2.5 rounded-lg border space-y-1" style={{ borderColor: "var(--bi-border)" }}>
                        <div className="flex items-center justify-between gap-2">
                          <div>
                            <div className="font-bold flex items-center gap-1.5">
                              <span>{formatRoleLabel(r, isFa)}</span>
                              {r.is_super_admin && (
                                <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-500/15 text-purple-500">
                                  {isFa ? "مدیر ارشد" : "Super Admin"}
                                </span>
                              )}
                            </div>
                            <div className="text-[10px] opacity-65">
                              {r.is_super_admin ? (isFa ? "دسترسی کامل سامانه" : "ALL") : `${(r.permission_ids || []).length} مجوز`} ·{" "}
                              <span className="font-mono">{r.slug}</span>
                            </div>
                          </div>
                          <div className="flex items-center gap-1">
                            {!r.is_super_admin && (
                              <button
                                type="button"
                                onClick={() => openRoleEditor(r)}
                                className="px-2 py-1 rounded border text-[11px] hover:bg-blue-500/10"
                                style={{ borderColor: "var(--bi-border)" }}
                              >
                                {isFa ? "ویرایش مجوزها" : "Edit Perms"}
                              </button>
                            )}
                            <button
                              type="button"
                              onClick={() => handleCloneRole(r.id)}
                              className="p-1.5 rounded border hover:bg-blue-500/10"
                              style={{ borderColor: "var(--bi-border)" }}
                              title={isFa ? "کپی از این نقش" : "Clone role"}
                            >
                              <Copy className="w-3 h-3" />
                            </button>
                            {!r.is_system && !r.is_super_admin && (
                              <button
                                type="button"
                                onClick={() => handleDeleteRole(r)}
                                className="p-1.5 rounded border text-red-500 hover:bg-red-500/10"
                                style={{ borderColor: "var(--bi-border)" }}
                                title={isFa ? "حذف نقش" : "Delete role"}
                              >
                                <Trash2 className="w-3 h-3" />
                              </button>
                            )}
                          </div>
                        </div>
                        <div className="text-[10px] opacity-75">{desc}</div>
                      </div>
                    );
                  })}
                </div>
                <form onSubmit={handleCreateRole} className="pt-2 border-t space-y-2" style={{ borderColor: "var(--bi-border)" }}>
                  <div className="font-semibold text-[11px]">{isFa ? "ساخت نقش سفارشی جدید" : "Create Custom Role"}</div>
                  <div className="grid grid-cols-2 gap-1.5">
                    <input
                      value={newRoleName}
                      onChange={(e) => setNewRoleName(e.target.value)}
                      placeholder={isFa ? "نام نقش (مثلاً ناظر مالی)" : "Role name"}
                      className="px-2.5 py-1.5 rounded border bg-transparent"
                      style={{ borderColor: "var(--bi-border)" }}
                      required
                    />
                    <input
                      value={newRoleSlug}
                      onChange={(e) => setNewRoleSlug(e.target.value)}
                      placeholder={isFa ? "شناسه لاتین (finance_auditor)" : "Slug"}
                      className="px-2.5 py-1.5 rounded border bg-transparent"
                      style={{ borderColor: "var(--bi-border)" }}
                    />
                  </div>
                  <input
                    value={newRoleDesc}
                    onChange={(e) => setNewRoleDesc(e.target.value)}
                    placeholder={isFa ? "توضیح فارسی نقش..." : "Role description..."}
                    className="w-full px-2.5 py-1.5 rounded border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  />
                  <button type="submit" className="w-full py-1.5 rounded border font-semibold hover:bg-blue-500/10" style={{ borderColor: "var(--bi-border)" }}>
                    {isFa ? "+ ایجاد نقش جدید" : "+ Create Role"}
                  </button>
                </form>
              </div>

              {/* Groups Card */}
              <div className="bi-card p-5 space-y-3 text-xs">
                <h2 className="text-sm font-bold">{isFa ? `گروه‌های دسترسی (${groups.length})` : `Groups (${groups.length})`}</h2>
                {groups.length === 0 ? (
                  <div className="text-[11px] opacity-70 py-4 text-center">
                    {isFa ? "هنوز گروهی تعریف نشده است." : "No groups yet."}
                  </div>
                ) : (
                  <div className="space-y-1.5 max-h-60 overflow-y-auto">
                    {groups.map((g) => (
                      <div key={g.id} className="p-2.5 rounded-lg border flex justify-between items-center" style={{ borderColor: "var(--bi-border)" }}>
                        <div>
                          <div className="font-bold">{g.name}</div>
                          <div className="text-[10px] opacity-65">
                            {isFa ? "منبع" : "Source"}: {g.source} · {(g.member_user_ids || []).length} {isFa ? "عضو" : "members"}
                          </div>
                        </div>
                        <span className="text-[10px] px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-500 font-semibold">
                          {(g.role_ids || []).length} {isFa ? "نقش" : "roles"}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
                <form onSubmit={handleCreateGroup} className="pt-2 border-t space-y-2" style={{ borderColor: "var(--bi-border)" }}>
                  <div className="font-semibold text-[11px]">{isFa ? "ایجاد گروه جدید" : "Create Group"}</div>
                  <input
                    value={newGroupName}
                    onChange={(e) => setNewGroupName(e.target.value)}
                    placeholder={isFa ? "نام گروه (مثلاً تیم تحلیل فرآیند)" : "Group name"}
                    className="w-full px-2.5 py-1.5 rounded border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                    required
                  />
                  <select
                    value={newGroupRoleId}
                    onChange={(e) => setNewGroupRoleId(e.target.value)}
                    className="w-full px-2.5 py-1.5 rounded border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  >
                    {roles.map((r) => (
                      <option key={r.id} value={r.id}>
                        {isFa ? `اتصال نقش: ${formatRoleLabel(r, true)}` : `Assign Role: ${r.name}`}
                      </option>
                    ))}
                  </select>
                  <button type="submit" className="w-full py-1.5 rounded border font-semibold hover:bg-blue-500/10" style={{ borderColor: "var(--bi-border)" }}>
                    {isFa ? "+ ایجاد گروه جدید" : "+ Create Group"}
                  </button>
                </form>
              </div>
            </div>
          </div>
        )}

        {/* TAB 2: PERMISSION MATRIX */}
        {tab === "matrix" && (
          <div className="space-y-4">
            <div className="bi-card p-4 space-y-3 text-xs">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-bold">{isFa ? "موضوع دسترسی:" : "Subject:"}</span>
                  {(["role", "group", "user"] as const).map((st) => (
                    <button
                      key={st}
                      type="button"
                      onClick={() => {
                        setMatrixSubjectType(st);
                        setCopySubjectType(st);
                        setCopySourceId("");
                        setCopyTargetId("");
                      }}
                      className={`px-2.5 py-1 rounded border font-semibold ${
                        matrixSubjectType === st ? "text-white" : "opacity-75"
                      }`}
                      style={{
                        background: matrixSubjectType === st ? "var(--bi-primary)" : "transparent",
                        borderColor: matrixSubjectType === st ? "var(--bi-primary)" : "var(--bi-border)",
                      }}
                    >
                      {st === "role" ? (isFa ? "نقش" : "Role") : st === "group" ? (isFa ? "گروه" : "Group") : isFa ? "کاربر" : "User"}
                    </button>
                  ))}

                  <span className="font-bold ms-3">{isFa ? "حوزه:" : "Scope:"}</span>
                  {(
                    [
                      { id: "dashboards", label: isFa ? "داشبوردها" : "Dashboards" },
                      { id: "widgets", label: isFa ? "ویجت‌ها" : "Widgets" },
                      { id: "catalog", label: isFa ? "کاتالوگ کامل دسترسی‌های نقش‌ها" : "Role Catalog" },
                    ] as const
                  ).map((sc) => (
                    <button
                      key={sc.id}
                      type="button"
                      onClick={() => setMatrixScope(sc.id)}
                      className={`px-2.5 py-1 rounded border font-semibold ${
                        matrixScope === sc.id ? "text-white" : "opacity-75"
                      }`}
                      style={{
                        background: matrixScope === sc.id ? "#0ea5e9" : "transparent",
                        borderColor: matrixScope === sc.id ? "#0ea5e9" : "var(--bi-border)",
                      }}
                    >
                      {sc.label}
                    </button>
                  ))}
                </div>

                <div className="flex items-center gap-2">
                  <span className="font-bold">{isFa ? "رفتار کلیک:" : "Click:"}</span>
                  <button
                    type="button"
                    onClick={() => setMatrixClickMode((m) => (m === "toggle" ? "remove" : "toggle"))}
                    className={`px-2.5 py-1 rounded border font-semibold ${
                      matrixClickMode === "remove" ? "bg-amber-500/20 border-amber-500 text-amber-500" : ""
                    }`}
                    style={{ borderColor: matrixClickMode === "remove" ? undefined : "var(--bi-border)" }}
                  >
                    {matrixClickMode === "toggle" ? (isFa ? "تغییر بین مجاز / مسدود" : "Allow <-> Deny") : isFa ? "حذف قانون سفارشی" : "Remove Rule"}
                  </button>
                </div>
              </div>

              {/* Copy Matrix Rules */}
              <div className="flex flex-wrap items-center gap-2 pt-2 border-t" style={{ borderColor: "var(--bi-border)" }}>
                <span className="font-bold">{isFa ? "کپی سریع مجوزهای منبع:" : "Bulk Copy Rules:"}</span>
                <select
                  value={copySourceId}
                  onChange={(e) => setCopySourceId(e.target.value)}
                  className="px-2 py-1 rounded border bg-transparent"
                  style={{ borderColor: "var(--bi-border)" }}
                >
                  <option value="">{isFa ? "مبدأ را انتخاب کنید..." : "Select Source..."}</option>
                  {copySubjects.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.label}
                    </option>
                  ))}
                </select>
                <span>←</span>
                <select
                  value={copyTargetId}
                  onChange={(e) => setCopyTargetId(e.target.value)}
                  className="px-2 py-1 rounded border bg-transparent"
                  style={{ borderColor: "var(--bi-border)" }}
                >
                  <option value="">{isFa ? "مقصد را انتخاب کنید..." : "Select Target..."}</option>
                  {copySubjects.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.label}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={handleCopyPerms}
                  disabled={!copySourceId || !copyTargetId || copySourceId === copyTargetId}
                  className="px-3 py-1 rounded text-white font-semibold disabled:opacity-40"
                  style={{ background: "var(--bi-primary)" }}
                >
                  {isFa ? "کپی دسترسی‌ها" : "Copy Rules"}
                </button>
              </div>
            </div>

            {matrix && matrixScope === "dashboards" && (
              <div className="bi-card p-5 overflow-x-auto space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h2 className="text-sm font-bold">
                    {isFa ? "ماتریس دسترسی سطح داشبورد (مشاهده / ویرایش / افزودن / حذف / دریل‌داون)" : "Dashboard Permission Matrix"}
                  </h2>
                  <div className="flex items-center gap-2 text-[11px]">
                    <span className="px-1.5 py-0.5 rounded bg-emerald-500 text-white font-semibold">{isFa ? "مجاز" : "Allow"}</span>
                    <span className="px-1.5 py-0.5 rounded bg-red-500 text-white font-semibold">{isFa ? "مسدود صریح" : "Deny"}</span>
                    <span className="px-1.5 py-0.5 rounded border opacity-60 font-semibold">{isFa ? "پیش‌فرض (بسته)" : "Unset"}</span>
                  </div>
                </div>
                {matrix.dashboards.length === 0 ? (
                  <div className="p-4 rounded-lg border text-xs opacity-75" style={{ borderColor: "var(--bi-border)" }}>
                    {isFa ? "هنوز داشبوردی ساخته نشده است. از تب «کاتالوگ کامل» برای تنظیم مجوزهای نقش استفاده کنید." : "No dashboards found."}
                  </div>
                ) : (
                  <table className="w-full text-[11px]">
                    <thead>
                      <tr className="border-b" style={{ borderColor: "var(--bi-border)" }}>
                        <th className="py-2 text-start">{isFa ? "نقش / گروه / کاربر" : "Subject"}</th>
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
                              <div className="inline-flex items-center gap-1">
                                {(
                                  [
                                    { act: "view", short: "مش" },
                                    { act: "edit", short: "وی" },
                                    { act: "add", short: "اف" },
                                    { act: "delete", short: "ح" },
                                    { act: "drill_down", short: "کا" },
                                  ] as const
                                ).map(({ act, short }) => {
                                  const rule = findRule(matrixSubjectType, subj.id, "dashboard", dash.id, act);
                                  const effect = rule?.effect;
                                  return (
                                    <button
                                      key={act}
                                      type="button"
                                      onClick={() =>
                                        toggleMatrixCell(matrixSubjectType, subj.id, subj.label, "dashboard", dash.id, dash.title, act, effect)
                                      }
                                      title={`${subj.label} ← ${dash.title}: ${formatActionLabel(act, isFa)}`}
                                      className={`px-1.5 py-0.5 rounded border font-semibold ${
                                        effect === "allow"
                                          ? "bg-emerald-500 text-white border-emerald-500"
                                          : effect === "deny"
                                          ? "bg-red-500 text-white border-red-500"
                                          : "opacity-60"
                                      }`}
                                    >
                                      {isFa ? short : act[0].toUpperCase()}
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

            {matrix && matrixScope === "widgets" && (
              <div className="bi-card p-5 overflow-x-auto space-y-3">
                <h2 className="text-sm font-bold">{isFa ? "ماتریس دسترسی ویجت‌ها" : "Widget Matrix"}</h2>
                {matrix.widgets.length === 0 ? (
                  <div className="p-4 rounded border opacity-75 text-xs">{isFa ? "ویجتی موجود نیست." : "No widgets."}</div>
                ) : (
                  <table className="w-full text-[11px]">
                    <thead>
                      <tr className="border-b" style={{ borderColor: "var(--bi-border)" }}>
                        <th className="py-2 text-start">{isFa ? "موضوع" : "Subject"}</th>
                        {matrix.widgets.slice(0, 12).map((w: any) => (
                          <th key={w.id} className="py-2 px-2 text-center max-w-[140px] truncate">
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
                                {(
                                  [
                                    { act: "view", short: "مش" },
                                    { act: "edit", short: "وی" },
                                    { act: "delete", short: "ح" },
                                    { act: "drill_down", short: "کا" },
                                  ] as const
                                ).map(({ act, short }) => {
                                  const rule = findRule(matrixSubjectType, subj.id, "widget", w.id, act);
                                  const effect = rule?.effect;
                                  return (
                                    <button
                                      key={act}
                                      type="button"
                                      onClick={() =>
                                        toggleMatrixCell(matrixSubjectType, subj.id, subj.label, "widget", w.id, w.title, act, effect)
                                      }
                                      title={`${subj.label} ← ${w.title}: ${formatActionLabel(act, isFa)}`}
                                      className={`px-1.5 py-0.5 rounded border font-semibold ${
                                        effect === "allow"
                                          ? "bg-emerald-500 text-white border-emerald-500"
                                          : effect === "deny"
                                          ? "bg-red-500 text-white border-red-500"
                                          : "opacity-60"
                                      }`}
                                    >
                                      {isFa ? short : act[0].toUpperCase()}
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

            {(matrixScope === "catalog" || !matrix || matrix.dashboards.length === 0) && (
              <div className="bi-card p-5 overflow-x-auto space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <h2 className="text-sm font-bold">
                      {isFa ? `کاتالوگ دسترسی‌های نقش‌ها (${filteredCatalog.length} مجوز)` : `Role Permission Catalog (${filteredCatalog.length})`}
                    </h2>
                    <p className="text-[11px] opacity-75">
                      {isFa
                        ? "کلیک روی هر خانه مجوز آن نقش را تغییر می‌دهد. نقش مدیر ارشد دسترسی ثابت و نامحدود دارد."
                        : "Click cells to grant/revoke base role permissions."}
                    </p>
                  </div>
                  <input
                    value={matrixPermFilter}
                    onChange={(e) => setMatrixPermFilter(e.target.value)}
                    placeholder={isFa ? "جستجو در مجوزها (مثلاً امنیت، BPMN، خروجی)..." : "Filter permissions..."}
                    className="px-3 py-1.5 rounded-lg border text-xs bg-transparent min-w-[240px]"
                    style={{ borderColor: "var(--bi-border)" }}
                  />
                </div>
                <table className="w-full text-[11px]">
                  <thead>
                    <tr className="border-b" style={{ borderColor: "var(--bi-border)" }}>
                      <th className="py-2 text-start">{isFa ? "دامنه و عملیات" : "Scope : Action"}</th>
                      {roles.map((r) => (
                        <th key={r.id} className="py-2 px-2 text-center whitespace-nowrap">
                          {formatRoleLabel(r, isFa)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {filteredCatalog.map((perm: any) => (
                      <tr key={perm.id} className="border-b hover:bg-slate-500/5" style={{ borderColor: "var(--bi-border)" }}>
                        <td className="py-2">
                          <div className="font-bold">
                            {formatScopeLabel(perm.resource_type, isFa)} — {formatActionLabel(perm.action, isFa)}
                          </div>
                          <div className="font-mono text-[10px] opacity-60">
                            {perm.resource_type}:{perm.action}
                          </div>
                        </td>
                        {roles.map((role) => {
                          const hasPerm = (role.permission_ids || []).includes(perm.id);
                          return (
                            <td key={role.id} className="py-1.5 px-2 text-center">
                              <button
                                type="button"
                                disabled={role.is_super_admin}
                                onClick={() => handleToggleRoleBasePerm(role, perm, hasPerm)}
                                className={`px-2 py-0.5 rounded border font-semibold ${
                                  role.is_super_admin || hasPerm
                                    ? "bg-emerald-500 text-white border-emerald-500"
                                    : "opacity-55"
                                } ${role.is_super_admin ? "cursor-not-allowed opacity-80" : ""}`}
                              >
                                {role.is_super_admin ? (isFa ? "همه" : "ALL") : hasPerm ? (isFa ? "مجاز" : "ALLOW") : "—"}
                              </button>
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* TAB 3: ACTIVE DIRECTORY */}
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
                      {isFa ? "حالت" : "Mode"}: <b>{primaryAd.directory_mode}</b> · {primaryAd.server_url} · Base DN:{" "}
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
                      {isFa ? "تست اتصال AD" : "Test AD Bind"}
                    </button>
                    <button
                      type="button"
                      onClick={handleSyncAd}
                      className="px-3 py-1.5 rounded text-white font-semibold flex items-center gap-1"
                      style={{ background: "var(--bi-primary)" }}
                    >
                      <RefreshCw className="w-3.5 h-3.5" /> {isFa ? "همگام‌سازی فوری کاربران و گروه‌ها" : "Sync Now"}
                    </button>
                  </div>
                </div>

                {adSyncResult && (
                  <div className="p-3 rounded border bg-emerald-500/10 text-emerald-500 font-semibold">
                    {isFa
                      ? `وضعیت همگام‌سازی (${adSyncResult.status}): ${adSyncResult.users_imported} واردشده، ${adSyncResult.users_updated} به‌روزرسانی، ${adSyncResult.users_deactivated} غیرفعال`
                      : `Synced`}
                  </div>
                )}

                <div className="flex gap-2">
                  <input
                    value={adSearchQ}
                    onChange={(e) => setAdSearchQ(e.target.value)}
                    placeholder={isFa ? "جستجو در کاربران و گروه‌های دایرکتوری..." : "Search AD Users & Groups..."}
                    className="flex-1 px-3 py-2 rounded border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  />
                  <button
                    type="button"
                    onClick={handleSearchAd}
                    className="px-4 py-2 rounded border font-semibold"
                    style={{ borderColor: "var(--bi-border)" }}
                  >
                    {isFa ? "جستجو در AD" : "Search AD"}
                  </button>
                </div>

                {adPreview && (
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    <div className="border rounded p-3" style={{ borderColor: "var(--bi-border)" }}>
                      <div className="font-bold mb-1">کاربران دایرکتوری ({adPreview.users?.length || 0})</div>
                      {(adPreview.users || []).map((u: any) => (
                        <div key={u.sAMAccountName} className="py-1 border-b" style={{ borderColor: "var(--bi-border)" }}>
                          <b>{u.sAMAccountName}</b> ({u.displayName}) — {u.department} [{u.enabled ? "فعال" : "غیرفعال"}]
                        </div>
                      ))}
                    </div>
                    <div className="border rounded p-3" style={{ borderColor: "var(--bi-border)" }}>
                      <div className="font-bold mb-1">گروه‌های امنیتی ({adPreview.groups?.length || 0})</div>
                      {(adPreview.groups || []).map((g: any) => (
                        <div key={g.dn} className="py-1 border-b" style={{ borderColor: "var(--bi-border)" }}>
                          <b>{g.cn}</b> ← نقش نگاشت‌شده: <code>{g.mapped_role_slug}</code>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {adSyncJobs.length > 0 && (
                  <div className="space-y-1 pt-2 border-t" style={{ borderColor: "var(--bi-border)" }}>
                    <div className="font-bold">{isFa ? "تاریخچه همگام‌سازی‌های اخیر" : "Recent Sync Jobs"}</div>
                    {adSyncJobs.slice(0, 5).map((j: any) => (
                      <div key={j.id} className="p-2 rounded border flex justify-between" style={{ borderColor: "var(--bi-border)" }}>
                        <span>
                          {j.sync_type} — <b>{j.status}</b> (+{j.users_imported} / ~{j.users_updated} / -{j.users_deactivated})
                        </span>
                        <span className="opacity-60">{new Date(j.started_at).toLocaleString(isFa ? "fa-IR" : "en-US")}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <div className="bi-card p-5 text-center opacity-75">
                {isFa ? "هیچ اتصال Active Directory / LDAP پیکربندی نشده است." : "No AD connections configured."}
              </div>
            )}
          </div>
        )}

        {/* TAB 4: RLS & CLS */}
        {tab === "rls" && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 text-xs">
            <div className="bi-card p-5 space-y-3">
              <h2 className="text-sm font-bold">{isFa ? "سیاست‌های محدودسازی سطری (RLS)" : "Row-Level Security (RLS)"}</h2>
              <form onSubmit={handleAddRls} className="space-y-2 border-b pb-3" style={{ borderColor: "var(--bi-border)" }}>
                <input
                  value={rlsName}
                  onChange={(e) => setRlsName(e.target.value)}
                  placeholder={isFa ? "نام سیاست (مثلاً دسترسی فقط منطقه شمال)" : "Policy Name"}
                  className="w-full px-3 py-1.5 rounded border bg-transparent"
                  style={{ borderColor: "var(--bi-border)" }}
                  required
                />
                <div className="grid grid-cols-2 gap-2">
                  <select value={rlsDs} onChange={(e) => setRlsDs(e.target.value)} className="px-2 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                    {datasets.length === 0 && <option value="">{isFa ? "همه مجموعه داده‌ها" : "All Datasets"}</option>}
                    {datasets.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.name}
                      </option>
                    ))}
                  </select>
                  <select value={rlsSubjectId} onChange={(e) => setRlsSubjectId(e.target.value)} className="px-2 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                    <option value="">{isFa ? "همه کاربران" : "All Users"}</option>
                    {users.map((u) => (
                      <option key={u.id} value={u.id}>
                        {u.username}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="grid grid-cols-3 gap-2">
                  <input value={rlsCol} onChange={(e) => setRlsCol(e.target.value)} placeholder={isFa ? "نام ستون" : "Column"} className="px-2 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
                  <select value={rlsOp} onChange={(e) => setRlsOp(e.target.value)} className="px-2 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                    <option value="eq">{isFa ? "مساوی (=)" : "Equals"}</option>
                    <option value="neq">{isFa ? "نامساوی (!=)" : "Not Equals"}</option>
                    <option value="in">{isFa ? "عضو مجموعه (IN)" : "In (CSV)"}</option>
                  </select>
                  <input value={rlsVal} onChange={(e) => setRlsVal(e.target.value)} placeholder={isFa ? "مقدار مجاز" : "Value"} className="px-2 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
                </div>
                <button type="submit" className="w-full py-1.5 rounded text-white font-semibold" style={{ background: "var(--bi-primary)" }}>
                  {isFa ? "افزودن سیاست RLS" : "Add RLS Policy"}
                </button>
              </form>
              <div className="space-y-1.5">
                {rlsPolicies.length === 0 && <div className="opacity-65 text-center py-4">{isFa ? "سیاست RLS ثبت نشده است." : "No RLS rules."}</div>}
                {rlsPolicies.map((p) => (
                  <div key={p.id} className="p-2.5 rounded border flex justify-between items-center" style={{ borderColor: "var(--bi-border)" }}>
                    <div>
                      <div className="font-bold">{p.name}</div>
                      <div className="opacity-75">
                        <code>
                          {p.rule_json?.column} {p.rule_json?.operator} {String(p.rule_json?.value)}
                        </code>
                      </div>
                    </div>
                    <button type="button" onClick={() => handleDeletePolicy(p)} className="text-red-500">
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            </div>

            <div className="bi-card p-5 space-y-3">
              <h2 className="text-sm font-bold">{isFa ? "امنیت سطح ستون و ماسک‌کردن داده (CLS)" : "Column Security & Masking"}</h2>
              <form onSubmit={handleAddCls} className="space-y-2 border-b pb-3" style={{ borderColor: "var(--bi-border)" }}>
                <div className="grid grid-cols-2 gap-2">
                  <select value={clsDs} onChange={(e) => setClsDs(e.target.value)} className="px-2 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                    {datasets.length === 0 && <option value="">{isFa ? "همه مجموعه داده‌ها" : "All Datasets"}</option>}
                    {datasets.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.name}
                      </option>
                    ))}
                  </select>
                  <select value={clsSubjectId} onChange={(e) => setClsSubjectId(e.target.value)} className="px-2 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                    <option value="">{isFa ? "همه کاربران" : "All Users"}</option>
                    {roles.map((r) => (
                      <option key={r.id} value={r.id}>
                        {formatRoleLabel(r, isFa)}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <input value={clsCol} onChange={(e) => setClsCol(e.target.value)} placeholder={isFa ? "نام ستون حساس (مثلاً email)" : "Column"} className="px-2 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
                  <select value={clsRule} onChange={(e) => setClsRule(e.target.value)} className="px-2 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }}>
                    <option value="deny">{isFa ? "مخفی‌سازی کامل ستون" : "Deny / Hide Column"}</option>
                    <option value="partial">{isFa ? "ماسک جزئی (a***@domain)" : "Partial Mask"}</option>
                    <option value="redact">{isFa ? "جایگزینی کامل با ***" : "Full Redact"}</option>
                    <option value="hash">{isFa ? "درهم‌سازی SHA-256" : "SHA-256 Hash"}</option>
                  </select>
                </div>
                <button type="submit" className="w-full py-1.5 rounded text-white font-semibold bg-amber-600">
                  {isFa ? "ثبت قانون CLS / ماسک" : "Add CLS Rule"}
                </button>
              </form>
              <div className="space-y-1.5">
                {clsPolicies.length === 0 && <div className="opacity-65 text-center py-4">{isFa ? "قانون ستونی ثبت نشده است." : "No CLS rules."}</div>}
                {clsPolicies.map((p) => {
                  const cols = Array.isArray(p.rule_json?.hidden_columns) ? p.rule_json.hidden_columns.join(", ") : p.rule_json?.column || "";
                  return (
                    <div key={p.id} className="p-2.5 rounded border flex justify-between items-center" style={{ borderColor: "var(--bi-border)" }}>
                      <div>
                        <div className="font-bold">
                          {p.name} ({p.policy_type})
                        </div>
                        <div className="opacity-75">
                          {isFa ? "ستون:" : "Column:"} <code>{cols}</code> ← {p.rule_json?.strategy || "hide"}
                        </div>
                      </div>
                      <button type="button" onClick={() => handleDeletePolicy(p)} className="text-red-500">
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        )}

        {/* TAB 5: AUDIT TRAIL */}
        {tab === "audit" && (
          <div className="bi-card p-5 space-y-3 text-xs">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-bold">{isFa ? "تاریخچه ممیزی تغییرات دسترسی و کاربران" : "Security Audit Log"}</h2>
              <span className="opacity-65">{isFa ? "۱۰۰ رویداد اخیر" : "Latest 100 events"}</span>
            </div>
            {auditLogs.length === 0 ? (
              <div className="py-8 text-center opacity-70">{isFa ? "رویداد ممیزی ثبت نشده است." : "No audit logs."}</div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-[11px]">
                  <thead>
                    <tr className="border-b opacity-75" style={{ borderColor: "var(--bi-border)" }}>
                      <th className="py-2 text-start">{isFa ? "زمان" : "Time"}</th>
                      <th className="py-2 text-start">{isFa ? "انجام‌دهنده" : "Actor"}</th>
                      <th className="py-2 text-start">{isFa ? "عملیات" : "Action"}</th>
                      <th className="py-2 text-start">{isFa ? "مقدار قبلی / جدید" : "Before / After"}</th>
                      <th className="py-2 text-start">{isFa ? "دلیل / جزئیات" : "Reason / Details"}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {auditLogs.map((log) => (
                      <tr key={log.id} className="border-b" style={{ borderColor: "var(--bi-border)" }}>
                        <td className="py-2 whitespace-nowrap">{new Date(log.created_at).toLocaleString(isFa ? "fa-IR" : "en-US")}</td>
                        <td className="py-2 font-semibold">{log.username || "system"}</td>
                        <td className="py-2">
                          <span className="px-2 py-0.5 rounded bg-blue-500/10 text-blue-500 font-mono">{log.action}</span>
                          <div className="text-[10px] opacity-65">{log.resource_type}</div>
                        </td>
                        <td className="py-2 max-w-[280px] text-[10px] font-mono">
                          {log.before && <div className="text-amber-500 truncate">قبل: {JSON.stringify(log.before)}</div>}
                          {log.after && <div className="text-emerald-500 truncate">بعد: {JSON.stringify(log.after)}</div>}
                        </td>
                        <td className="py-2 text-[11px]">
                          {log.details?.reason ? <span className="font-semibold">{log.details.reason}</span> : <span className="opacity-65">{JSON.stringify(log.details || {})}</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* MODAL: Edit User */}
        {editingUser && (
          <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
            <form onSubmit={submitEditUser} className="bi-card w-full max-w-xl p-5 space-y-4 text-xs max-h-[90vh] overflow-y-auto">
              <div className="flex items-center justify-between border-b pb-2" style={{ borderColor: "var(--bi-border)" }}>
                <h3 className="text-sm font-bold">
                  {isFa ? `ویرایش کاربر و نقش‌ها: ${editingUser.username}` : `Edit User: ${editingUser.username}`}
                </h3>
                <button type="button" onClick={() => setEditingUser(null)}>
                  <X className="w-4 h-4" />
                </button>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className="block mb-1 font-semibold">{isFa ? "نام و نام خانوادگی" : "Full Name"}</label>
                  <input value={editUserFullName} onChange={(e) => setEditUserFullName(e.target.value)} className="w-full px-3 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
                </div>
                <div>
                  <label className="block mb-1 font-semibold">{isFa ? "ایمیل" : "Email"}</label>
                  <input type="email" value={editUserEmail} onChange={(e) => setEditUserEmail(e.target.value)} className="w-full px-3 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} required />
                </div>
                <div>
                  <label className="block mb-1 font-semibold">{isFa ? "واحد سازمانی" : "Department"}</label>
                  <input value={editUserDepartment} onChange={(e) => setEditUserDepartment(e.target.value)} className="w-full px-3 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
                </div>
                <div>
                  <label className="block mb-1 font-semibold">{isFa ? "منطقه (برای RLS)" : "Region"}</label>
                  <input value={editUserRegion} onChange={(e) => setEditUserRegion(e.target.value)} className="w-full px-3 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
                </div>
              </div>
              <div>
                <label className="block mb-1.5 font-semibold">{isFa ? "نقش‌های اختصاص‌یافته" : "Assigned Roles"}</label>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
                  {roles.map((r) => {
                    const checked = editUserRoleIds.includes(r.id);
                    return (
                      <label key={r.id} className="flex items-center gap-2 p-2 rounded border cursor-pointer" style={{ borderColor: "var(--bi-border)" }}>
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={(e) =>
                            setEditUserRoleIds((prev) =>
                              e.target.checked ? [...prev, r.id] : prev.filter((id) => id !== r.id)
                            )
                          }
                        />
                        <span>{formatRoleLabel(r, isFa)}</span>
                      </label>
                    );
                  })}
                </div>
              </div>
              {groups.length > 0 && (
                <div>
                  <label className="block mb-1.5 font-semibold">{isFa ? "عضویت در گروه‌ها" : "Groups"}</label>
                  <div className="flex flex-wrap gap-2">
                    {groups.map((g) => (
                      <label key={g.id} className="flex items-center gap-1.5 px-2.5 py-1 rounded border cursor-pointer" style={{ borderColor: "var(--bi-border)" }}>
                        <input
                          type="checkbox"
                          checked={editUserGroupIds.includes(g.id)}
                          onChange={(e) =>
                            setEditUserGroupIds((prev) =>
                              e.target.checked ? [...prev, g.id] : prev.filter((id) => id !== g.id)
                            )
                          }
                        />
                        <span>{g.name}</span>
                      </label>
                    ))}
                  </div>
                </div>
              )}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 items-center">
                <label className="flex items-center gap-2 font-semibold cursor-pointer">
                  <input type="checkbox" checked={editUserActive} onChange={(e) => setEditUserActive(e.target.checked)} />
                  {isFa ? "حساب کاربری فعال باشد" : "Account Active"}
                </label>
                {editingUser.auth_source === "local" && (
                  <input
                    type="password"
                    value={editUserPassword}
                    onChange={(e) => setEditUserPassword(e.target.value)}
                    placeholder={isFa ? "بازنشانی رمز (در صورت نیاز)" : "Reset Password (optional)"}
                    className="px-3 py-1.5 rounded border bg-transparent"
                    style={{ borderColor: "var(--bi-border)" }}
                  />
                )}
              </div>
              <div className="flex justify-end gap-2 pt-2 border-t" style={{ borderColor: "var(--bi-border)" }}>
                <button type="button" onClick={() => setEditingUser(null)} className="px-4 py-1.5 rounded border" style={{ borderColor: "var(--bi-border)" }}>
                  {isFa ? "انصراف" : "Cancel"}
                </button>
                <button type="submit" className="px-4 py-1.5 rounded text-white font-semibold" style={{ background: "var(--bi-primary)" }}>
                  {isFa ? "ادامه و مشاهده پیش‌نمایش تغییر" : "Review & Save"}
                </button>
              </div>
            </form>
          </div>
        )}

        {/* MODAL: Role Editor with Persian Descriptions & Permission Checkboxes */}
        {editingRole && (
          <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
            <form onSubmit={submitRoleEditor} className="bi-card w-full max-w-3xl p-5 space-y-4 text-xs max-h-[90vh] flex flex-col">
              <div className="flex items-center justify-between border-b pb-2 shrink-0" style={{ borderColor: "var(--bi-border)" }}>
                <h3 className="text-sm font-bold">
                  {isFa ? `ویرایش نقش و مجوزها: ${formatRoleLabel(editingRole, true)}` : `Edit Role: ${editingRole.name}`}
                </h3>
                <button type="button" onClick={() => setEditingRole(null)}>
                  <X className="w-4 h-4" />
                </button>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 shrink-0">
                <div>
                  <label className="block mb-1 font-semibold">{isFa ? "نام نقش" : "Role Name"}</label>
                  <input value={editRoleName} onChange={(e) => setEditRoleName(e.target.value)} className="w-full px-3 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} required />
                </div>
                <div>
                  <label className="block mb-1 font-semibold">{isFa ? "توضیحات فارسی نقش" : "Description"}</label>
                  <input value={editRoleDesc} onChange={(e) => setEditRoleDesc(e.target.value)} className="w-full px-3 py-1.5 rounded border bg-transparent" style={{ borderColor: "var(--bi-border)" }} />
                </div>
              </div>
              <div className="flex items-center justify-between gap-2 shrink-0">
                <input
                  value={rolePermSearch}
                  onChange={(e) => setRolePermSearch(e.target.value)}
                  placeholder={isFa ? "جستجو در مجوزها..." : "Search permissions..."}
                  className="flex-1 px-3 py-1.5 rounded border bg-transparent"
                  style={{ borderColor: "var(--bi-border)" }}
                />
                <span className="font-semibold">
                  {editRolePermIds.length} / {catalog.length} {isFa ? "مجوز انتخاب‌شده" : "selected"}
                </span>
              </div>
              <div className="overflow-y-auto space-y-3 flex-1 pe-1">
                {Object.entries(groupedCatalog).map(([scope, perms]) => (
                  <div key={scope} className="p-3 rounded-lg border space-y-2" style={{ borderColor: "var(--bi-border)" }}>
                    <div className="font-bold text-[11px] text-blue-500">
                      {formatScopeLabel(scope, isFa)} ({scope})
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
                      {perms.map((p: any) => (
                        <label key={p.id} className="flex items-center gap-2 cursor-pointer py-0.5">
                          <input
                            type="checkbox"
                            checked={editRolePermIds.includes(p.id)}
                            onChange={(e) =>
                              setEditRolePermIds((prev) =>
                                e.target.checked ? [...prev, p.id] : prev.filter((id) => id !== p.id)
                              )
                            }
                          />
                          <span>{formatActionLabel(p.action, isFa)}</span>
                          <code className="text-[10px] opacity-55">({p.action})</code>
                        </label>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
              <div className="flex justify-end gap-2 pt-2 border-t shrink-0" style={{ borderColor: "var(--bi-border)" }}>
                <button type="button" onClick={() => setEditingRole(null)} className="px-4 py-1.5 rounded border" style={{ borderColor: "var(--bi-border)" }}>
                  {isFa ? "انصراف" : "Cancel"}
                </button>
                <button type="submit" className="px-4 py-1.5 rounded text-white font-semibold" style={{ background: "var(--bi-primary)" }}>
                  {isFa ? "پیش‌نمایش تغییر و ذخیره" : "Review & Save"}
                </button>
              </div>
            </form>
          </div>
        )}

        {/* MODAL: Effective Permissions Inspector */}
        {effectiveModalUser && (
          <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
            <div className="bi-card w-full max-w-2xl p-5 space-y-4 text-xs max-h-[88vh] flex flex-col">
              <div className="flex items-center justify-between border-b pb-2" style={{ borderColor: "var(--bi-border)" }}>
                <h3 className="text-sm font-bold flex items-center gap-2">
                  <Eye className="w-4 h-4 text-blue-500" />
                  {isFa ? `مجوزهای مؤثر کاربر: ${effectiveModalUser.username}` : `Effective Permissions: ${effectiveModalUser.username}`}
                </h3>
                <button type="button" onClick={() => setEffectiveModalUser(null)}>
                  <X className="w-4 h-4" />
                </button>
              </div>
              {effectiveLoading || !effectiveData ? (
                <div className="py-8 text-center opacity-70">{isFa ? "در حال محاسبه مجوزهای مؤثر..." : "Calculating..."}</div>
              ) : (
                <div className="space-y-3 overflow-y-auto flex-1">
                  <div className="flex flex-wrap gap-2">
                    <span className="px-2.5 py-1 rounded bg-blue-500/10 text-blue-500 font-semibold">
                      {isFa ? "نقش‌ها: " : "Roles: "}
                      {(effectiveData.roles || []).join("، ") || "—"}
                    </span>
                    <span className="px-2.5 py-1 rounded bg-emerald-500/10 text-emerald-500 font-semibold">
                      {isFa ? "گروه‌ها: " : "Groups: "}
                      {(effectiveData.groups || []).join("، ") || "—"}
                    </span>
                    {effectiveData.is_super_admin && (
                      <span className="px-2.5 py-1 rounded bg-purple-500/15 text-purple-500 font-bold">
                        {isFa ? "مدیر ارشد (دسترسی کل بدون محدودیت)" : "Super Admin (Full Access)"}
                      </span>
                    )}
                  </div>
                  <div className="space-y-1">
                    <div className="font-bold">{isFa ? "مجوزهای به ارث‌رسیده از نقش‌ها و گروه‌ها:" : "Role/Group Grants:"}</div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5 max-h-56 overflow-y-auto p-2 rounded border" style={{ borderColor: "var(--bi-border)" }}>
                      {(effectiveData.role_permissions || []).map((rp: any, i: number) => (
                        <div key={i} className="flex items-center justify-between gap-2 px-2 py-1 rounded bg-slate-500/5">
                          <span>
                            {formatScopeLabel(rp.resource_type, isFa)} ← {formatActionLabel(rp.action, isFa)}
                          </span>
                          <span className="text-emerald-500 font-bold">{rp.effect === "allow" ? "مجاز" : "مسدود"}</span>
                        </div>
                      ))}
                      {(effectiveData.role_permissions || []).length === 0 && !effectiveData.is_super_admin && (
                        <div className="opacity-65">{isFa ? "مجوز پایه‌ای وجود ندارد." : "None"}</div>
                      )}
                    </div>
                  </div>
                  <div className="space-y-1">
                    <div className="font-bold">{isFa ? "استثنائات و قوانین سطح منبع (Resource Overrides):" : "Resource Overrides:"}</div>
                    {(effectiveData.resource_permissions || []).length === 0 ? (
                      <div className="text-[11px] opacity-65">{isFa ? "قانون اختصاصی سطح منبع ثبت نشده است." : "No direct resource overrides."}</div>
                    ) : (
                      <div className="space-y-1">
                        {effectiveData.resource_permissions.map((resP: any, i: number) => (
                          <div key={i} className="flex items-center justify-between px-2.5 py-1.5 rounded border" style={{ borderColor: "var(--bi-border)" }}>
                            <span>
                              {formatScopeLabel(resP.resource_type, isFa)} ({resP.resource_id}) — {formatActionLabel(resP.action, isFa)}
                            </span>
                            <span className={resP.effect === "deny" ? "text-red-500 font-bold" : "text-emerald-500 font-bold"}>
                              {resP.effect === "deny" ? (isFa ? "مسدود صریح (اولویت بالا)" : "DENY") : isFa ? "مجاز" : "ALLOW"}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* MODAL: Sensitive Change Diff & Audit Reason Confirmation */}
        {pendingConfirm && (
          <div className="fixed inset-0 bg-black/65 z-50 flex items-center justify-center p-4">
            <form onSubmit={handleConfirmExecute} className="bi-card w-full max-w-md p-5 space-y-4 text-xs">
              <div className="flex items-start justify-between gap-2 border-b pb-2" style={{ borderColor: "var(--bi-border)" }}>
                <div className="flex items-center gap-2 font-bold text-sm">
                  <AlertTriangle className={`w-4 h-4 ${pendingConfirm.danger ? "text-red-500" : "text-amber-500"}`} />
                  {pendingConfirm.title}
                </div>
                <button type="button" onClick={() => setPendingConfirm(null)}>
                  <X className="w-4 h-4" />
                </button>
              </div>
              <p className="opacity-85 leading-relaxed">{pendingConfirm.summary}</p>
              <div className="p-3 rounded-lg border space-y-1.5 font-mono text-[11px]" style={{ borderColor: "var(--bi-border)", background: "rgba(100,116,139,0.06)" }}>
                <div className="text-amber-500">
                  <b>{isFa ? "مقدار فعلی:" : "Before:"}</b> {pendingConfirm.beforeText}
                </div>
                <div className="text-emerald-500">
                  <b>{isFa ? "مقدار جدید:" : "After:"}</b> {pendingConfirm.afterText}
                </div>
              </div>
              <div>
                <label className="block mb-1 font-semibold">{isFa ? "دلیل تغییر (ثبت در تاریخچه ممیزی):" : "Audit Reason (Optional):"}</label>
                <input
                  value={confirmReason}
                  onChange={(e) => setConfirmReason(e.target.value)}
                  placeholder={isFa ? "مثلاً درخواست واحد مالی یا تغییر سمت کارمند..." : "Reason for change..."}
                  className="w-full px-3 py-1.5 rounded border bg-transparent"
                  style={{ borderColor: "var(--bi-border)" }}
                />
              </div>
              <div className="flex justify-end gap-2">
                <button type="button" onClick={() => setPendingConfirm(null)} className="px-4 py-1.5 rounded border" style={{ borderColor: "var(--bi-border)" }}>
                  {isFa ? "انصراف" : "Cancel"}
                </button>
                <button
                  type="submit"
                  disabled={confirmBusy}
                  className={`px-4 py-1.5 rounded text-white font-semibold ${pendingConfirm.danger ? "bg-red-600" : ""}`}
                  style={pendingConfirm.danger ? undefined : { background: "var(--bi-primary)" }}
                >
                  {confirmBusy ? "..." : isFa ? "تأیید و اعمال تغییر" : "Confirm & Apply"}
                </button>
              </div>
            </form>
          </div>
        )}
      </div>
    </AppShell>
  );
}
