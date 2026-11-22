# Deliverable 05 — Active Directory / LDAP / LDAPS Integration Guide

Implemented in `backend/app/engines/ad_ldap.py` and `backend/app/api/users_rbac.py`.

---

## 1. Security Architecture & Password Policy

- **Zero AD Password Persistence**: Active Directory credentials entered at login are authenticated directly against the LDAP/LDAPS server (or corporate `mock_ad` engine during isolated testing) and immediately discarded after token issuance. No AD user password hashes are ever stored in the `users` table (`password_hash = NULL`, `auth_source = "active_directory"`).
- **Encrypted Bind Credentials**: The service account `bind_password` stored in `directory_connections` is encrypted at rest using Fernet (`AES-128-CBC + HMAC-SHA256`) and never serialized in API responses.
- **Disabled Account Enforcement**: If an AD account is disabled (`userAccountControl` / `enabled = False`) in Active Directory, both **Login** and **Sync** immediately block/deactivate the user inside the BI platform (`is_active = False`), revoking active access.

---

## 2. Supported Connection Modes

| Mode | Protocol | Details |
|---|---|---|
| `ldap` / `ldaps` | Live TCP 389 / SSL 636 via `ldap3` | Real corporate Windows Active Directory / OpenLDAP bind, subtree search (`objectClass=user`, `objectClass=group`), and group membership sync |
| `mock_ad` | Built-in test corporate domain (`corp.enterprise.local`) | Pre-configured for automated CI/CD verification & local demos without external domain controller dependency |

---

## 3. Configuration Parameters (`POST /api/v1/rbac/ad/connections`)

- `domain_name`: e.g. `corp.enterprise.local`
- `server_url`: e.g. `ldaps://dc01.corp.enterprise.local:636`
- `use_ldaps`: `true` / `false`
- `base_dn`: e.g. `DC=corp,DC=enterprise,DC=local`
- `bind_dn`: Service account distinguished name (e.g. `CN=bi-svc,OU=ServiceAccounts,DC=corp,DC=enterprise,DC=local`)
- `bind_password`: Service bind password (encrypted before DB storage)
- `sync_policy`: `manual` | `on_login` | `scheduled`
- `selected_groups`: Optional list of AD security group CNs/DNs to restrict sync scope

---

## 4. Group → BI Role Mapping & Sync Lifecycle

1. **Test Bind**: `POST /api/v1/rbac/ad/connections/{id}/test` verifies bind DN authentication and base DN reachability.
2. **Search Directory**: `GET /api/v1/rbac/ad/connections/{id}/search?q=...` previews live AD users (`sAMAccountName`, `mail`, `displayName`, `department`, `region`) and groups (`cn`, `dn`).
3. **Sync Now (`POST /api/v1/rbac/ad/connections/{id}/sync`)**:
   - Creates `User` (`auth_source="active_directory"`), `ExternalIdentity` (`objectGUID`), and `UserProfile` (`region`, `department`, `title`).
   - Creates/updates `Group` records from AD groups and assigns `GroupMember` memberships.
   - Maps AD groups (`AD-Executive-Board` -> `executive_viewer`, `AD-Sales-Analysts` -> `data_analyst`, `AD-Finance-Controllers` -> `dashboard_designer`) into `GroupRole` and recalculates `PermissionResolver` cache versions.
   - Records `DirectorySyncJob` statistics (`users_imported`, `users_updated`, `users_deactivated`, `groups_synced`).
4. **Domain Login (`POST /api/v1/auth/login` with `auth_mode="active_directory"`)**:
   - Authenticates user against AD using `domain` + `username` + `password`.
   - Runs `on_login` upsert if user is new or profile changed.
   - Issues platform JWT access/refresh tokens with effective RBAC & AD group-mapped permissions.
