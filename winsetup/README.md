# NexusBI Enterprise — Standalone Windows Installer (`winsetup`)

This directory provides a complete, reproducible pipeline to package the **NexusBI Enterprise BI Platform** into a single standalone offline Windows x64 installer (`winsetup/dist/setup.exe`).

End users install and run the full BI platform by double-clicking **`setup.exe`**—with **no user prerequisites** (no Python, Node.js, npm, Docker, PostgreSQL, Redis, or Git required, and no internet connection required after `setup.exe` is built).

---

## 1. Architecture & Embedded Stack Mapping

| Component | Cloud / Docker Stack | Standalone Windows x64 Stack | Bundled Location |
| :--- | :--- | :--- | :--- |
| **Web UI** | Next.js 14 (`next start`) | Next.js 14 `output: "standalone"` + bundled Node.js `v22` (`node.exe`) + dynamic loopback proxy (`web_gateway.mjs`) | `{app}\frontend\.next\standalone` & `{app}\runtime\node\node.exe` |
| **API & ETL Engine** | FastAPI + Uvicorn + APScheduler | FastAPI + Uvicorn + APScheduler running inside relocatable Python `3.11.9` (`win_amd64`) with pre-installed binary wheels | `{app}\backend` & `{app}\runtime\python\python.exe` |
| **Layer 1 App Metadata DB** | PostgreSQL / Neon | **Embedded SQLite (WAL mode)** (`app_metadata.db`) | `%LocalAppData%\NexusBI\data\app_metadata.db` |
| **Layer 2 OLAP Warehouse** | DuckDB | **Embedded DuckDB** (`warehouse.duckdb`) | `%LocalAppData%\NexusBI\data\warehouse.duckdb` |
| **Layer 3 Cache** | Redis + In-Memory LRU | **In-process thread-safe LRU cache** (`SmartCacheManager`, `REDIS_URL=None`) | In-process memory |
| **Desktop Supervisor** | Docker Compose / Systemd | Native `.NET` WinForms + System Tray launcher (`NexusBI.exe`) with Win32 Job Object lifecycle management | `{app}\NexusBI.exe` |
| **Local Secrets** | Env vars / Cloud secrets | CSPRNG keys (`BI_JWT_SECRET`, `BI_CRED_KEY`) encrypted at rest via **Windows DPAPI (`CurrentUser`)** | `%LocalAppData%\NexusBI\config\secrets.dpapi` |

### Why No External Ports or Build-Time Proxies Conflict
`Next.js` static `rewrites()` normally bakes `BACKEND_INTERNAL_URL` at build time. For desktop packaging, `winsetup/build.ps1` sets `NEXT_OUTPUT_MODE=standalone` and `NEXT_DISABLE_BUILD_REWRITES=1`. At runtime, `NexusBI.exe` allocates three free loopback (`127.0.0.1`) ports dynamically and launches `winsetup/runtime/web_gateway.mjs`, which serves Next.js UI traffic and proxies `/api/*` directly to the live backend port—avoiding conflicts even if ports `3000` or `8000` are already in use.

---

## 2. Target Compatibility & System Requirements

- **Supported Operating Systems:** Windows 10 (version 1809 / build 17763 or later) and Windows 11.
- **Supported CPU Architecture:** **64-bit x64 (`x86_64` / `amd64`) ONLY**.
  - Both `NexusBI.iss` (`ArchitecturesAllowed=x64compatible`) and `NexusBI.exe` (`Environment.Is64BitOperatingSystem`) explicitly block 32-bit x86 Windows installations.
  - Native ARM64 Windows is **not** a native target (may run only under Windows 11 x64 emulation if VC++ x64 runtime is present; officially unsupported).
- **Runtime Prerequisites for End User:** **None.** Only `setup.exe` is required.
  - Offline `vc_redist.x64.exe` is bundled inside `setup.exe` for admin installs when missing, and `vcruntime140.dll`, `vcruntime140_1.dll`, `msvcp140.dll` are also staged alongside `python.exe` and `node.exe` for non-admin installs.
  - Bundled `@fontsource/inter` and `@fontsource/vazirmatn` fonts are self-hosted in Next.js assets (no Google Fonts or external CDN requests).

---

## 3. Building `setup.exe` (Windows Build Host or CI)

### Prerequisites on the **Build Machine Only** (NOT on user PCs)
1. Windows 10/11 **x64** (or GitHub Actions `windows-latest` runner).
2. **Node.js 20/22 LTS + npm** (used only to run `next build`; not needed by end users).
3. **Inno Setup 6** (`ISCC.exe`) — e.g., `winget install JRSoftware.InnoSetup` or `choco install innosetup -y`.
4. Built-in Windows `.NET Framework 4.x` (`csc.exe` at `C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe`, pre-installed on Windows 10/11).
5. Internet access on the **build machine** during `build.ps1` to download official signed portable Python `3.11.9` NuGet package, Python wheels from PyPI, official Node.js `v22.14.0-win-x64.zip` (SHA-256 verified), and `vc_redist.x64.exe`.

### Build Command
From PowerShell in the repository root:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\winsetup\build.ps1
```

### Expected Output
- Final single-file offline installer: **`winsetup\dist\setup.exe`**
- Staging directory (intermediate): `winsetup\stage\` (cleanable via `.\winsetup\clean.ps1`)
- Download cache (optional reuse): `winsetup\.cache\` (cleanable via `.\winsetup\clean.ps1 -IncludeCache`)

### GitHub Actions Automated Build & Verification
A ready-to-run workflow is included at `.github/workflows/windows-installer.yml`. It builds `winsetup/dist/setup.exe` on `windows-latest`, runs `winsetup\tests\verify_windows_bundle.ps1` with global Python/Node/Docker stripped from `PATH`, and uploads `setup.exe` as a workflow artifact.

---

## 4. Offline Installation & First-Run Security

1. Copy **`setup.exe`** to the target offline Windows x64 machine and run it.
2. Choose **Install for me** (`%LocalAppData%\Programs\NexusBI`, no admin UAC required) or **Install for all users** (`C:\Program Files\NexusBI`, requires admin UAC).
3. Shortcuts created:
   - Start Menu: **NexusBI Enterprise**, **NexusBI Data Folder**, **Uninstall NexusBI**
   - Desktop (optional task, checked by default): **NexusBI Enterprise**
4. After setup finishes, **`NexusBI.exe`** launches automatically:
   - Generates random CSPRNG `BI_JWT_SECRET` (384-bit) and `BI_CRED_KEY` (256-bit Fernet key), protects them via **Windows DPAPI (`CurrentUser`)**, and writes `%LocalAppData%\NexusBI\config\secrets.dpapi` with user-only NTFS ACLs.
   - Starts FastAPI/DuckDB (`ENVIRONMENT=production`, `BI_SEED_DEMO_USERS=0`, `BI_REQUIRE_GENERATED_SECRETS=1`) and Next.js standalone on free loopback (`127.0.0.1`) ports.
   - Opens your browser to the **Initial Administrator Setup** screen (`/?setup_token=...`).
5. **Security Policy (Zero Active Default Admin Passwords):**
   - No pre-seeded `admin / Admin!2026` or demo user accounts exist in the installed desktop app.
   - No `mock_ad` directory connection is active by default.
   - On first run, you create your own Super Administrator username, email, and strong password (>= 10 chars, mixed case + number/symbol), optionally loading sample BI dashboards and datasets owned by your new administrator account.
   - Once the administrator account is created, `/api/v1/auth/bootstrap-admin` permanently locks (`403`).

---

## 5. Writable Data Paths (Outside Protected Install Dir)

Application binaries in `C:\Program Files\NexusBI` (or `%LocalAppData%\Programs\NexusBI`) are treated as **read-only**. All mutable user data lives inside **`%LocalAppData%\NexusBI\`**:

| Path | Contents |
| :--- | :--- |
| `%LocalAppData%\NexusBI\data\app_metadata.db` | SQLite WAL metadata DB (users, roles, dashboards, ETL history, audit logs) |
| `%LocalAppData%\NexusBI\data\warehouse.duckdb` | DuckDB analytical OLAP data warehouse |
| `%LocalAppData%\NexusBI\data\sample_erp_source.sqlite` | Sample operational ERP SQLite database (if sample data selected) |
| `%LocalAppData%\NexusBI\data\uploads\` | Raw Excel/CSV/JSON/XML files uploaded by users |
| `%LocalAppData%\NexusBI\data\exports\` | Generated scheduled PDF/XLSX/CSV report exports |
| `%LocalAppData%\NexusBI\config\secrets.dpapi` | Windows DPAPI-protected JWT & credential encryption keys |
| `%LocalAppData%\NexusBI\logs\` | Rotating `backend.log` and `frontend.log` |
| `%LocalAppData%\NexusBI\runtime\state.json` | Ephemeral active port & PID state (deleted on clean exit) |

---

## 6. Service Lifecycle, Tray Controls & Clean Uninstall

- **System Tray Icon (`NexusBI.exe`):**
  - **Open NexusBI in Browser** / **Open NexusBI App Window** (Microsoft Edge `--app` mode if installed)
  - **Restart Local Services**
  - **Open Data Folder** / **Open Logs Folder**
  - **Exit & Stop Services** (sends authenticated local shutdown to `/__gateway/shutdown` and `/_internal/shutdown` so DuckDB and APScheduler flush and close cleanly, backed by a Win32 Job Object `KILL_ON_JOB_CLOSE`)
- **Uninstall Behavior:**
  - Use Windows **Installed Apps / Programs and Features** or the Start Menu **Uninstall NexusBI** shortcut.
  - The uninstaller calls `NexusBI.exe --stop` to gracefully release `.duckdb` and `.db` file locks before deleting `{app}` binaries.
  - **User data preservation:** Uninstalling removes **only** application binaries and shortcuts. **`%LocalAppData%\NexusBI\` is NEVER automatically deleted.** To permanently remove user data after uninstall, manually delete `%LocalAppData%\NexusBI`.

---

## 7. Backup & Restore

### Backup
1. Right-click the **NexusBI** system tray icon and choose **Exit & Stop Services** (ensures SQLite WAL & DuckDB checkpoints are flushed with zero open locks).
2. Copy the entire folder **`%LocalAppData%\NexusBI\`** (`data\` and `config\`) to your backup location.
   > **Note on DPAPI (`secrets.dpapi`):** DPAPI keys are bound to the current Windows user account (`CurrentUser`). Backing up on the same Windows user account allows seamless restore; if restoring under a different Windows user/machine, remove `secrets.dpapi` (new keys will be generated automatically, though stored external DB connector passwords in `data_source_credentials` would need to be re-entered).

### Restore
1. Stop NexusBI (`Exit & Stop Services` or `NexusBI.exe --stop`).
2. Restore the `data\` and `config\` folders into `%LocalAppData%\NexusBI\`.
3. Launch **NexusBI Enterprise** from the Start Menu or Desktop shortcut.

---

## 8. Limitations & Troubleshooting

### Known Scope & Limitations
1. **Windows x64 Only:** 32-bit Windows is not supported; native ARM64 builds are not generated.
2. **External MSSQL / Oracle Connectors:** In `backend/app/engines/connectors.py`, PostgreSQL (`psycopg2-binary`), MySQL/MariaDB (`pymysql`), MongoDB (`pymongo`), SQLite, REST/GraphQL, and LDAP/AD (`ldap3`) include full bundled client drivers. MSSQL and Oracle connectors require proprietary ODBC/Oracle Instant Client drivers not bundled in `setup.exe` (test connection verifies TCP reachability only).
3. **Code Signing:** `setup.exe` and `NexusBI.exe` are unsigned unless your build environment signs them with an Authenticode certificate (`signtool.exe`). Windows SmartScreen may show an "Unknown Publisher" prompt for unsigned builds.
4. **Linux Cross-Build Limitation:** Inno Setup (`ISCC.exe`), `.NET csc.exe`, and `win_amd64` native runtime packaging require a Windows x64 host or `windows-latest` CI runner (`winsetup/build.ps1` cannot produce a verified `setup.exe` natively on Linux).

### Troubleshooting
- **Startup timeout or error dialog:** Click **Open Logs** in the error dialog (or browse `%LocalAppData%\NexusBI\logs\`) and inspect `backend.log` and `frontend.log`.
- **Database file locked during upgrade/manual move:** Run `"<install_dir>\NexusBI.exe" --stop` or stop via System Tray **Exit & Stop Services** before copying `.duckdb` or `.db` files.
- **Port conflict:** `NexusBI.exe` automatically searches for free loopback ports if defaults `18000`/`18001`/`18080` are busy; no manual port configuration is required.
- **Forgotten local admin password:** Because no default recovery password exists, stop NexusBI, delete or rename `%LocalAppData%\NexusBI\data\app_metadata.db` (if starting fresh) or manage users from an existing admin session.
