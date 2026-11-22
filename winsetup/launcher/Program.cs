using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace NexusBILauncher
{
    internal static class NativeMethods
    {
        public const int JobObjectExtendedLimitInformation = 9;
        public const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000;

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern IntPtr CreateJobObject(IntPtr lpJobAttributes, string lpName);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool SetInformationJobObject(IntPtr hJob, int infoType, IntPtr lpJobObjectInfo, uint cbJobObjectInfoLength);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool AssignProcessToJobObject(IntPtr hJob, IntPtr hProcess);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool CloseHandle(IntPtr hObject);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool GenerateConsoleCtrlEvent(uint dwCtrlEvent, uint dwProcessGroupId);

        [StructLayout(LayoutKind.Sequential)]
        public struct JOBOBJECT_BASIC_LIMIT_INFORMATION
        {
            public long PerProcessUserTimeLimit;
            public long PerJobUserTimeLimit;
            public uint LimitFlags;
            public UIntPtr MinimumWorkingSetSize;
            public UIntPtr MaximumWorkingSetSize;
            public uint ActiveProcessLimit;
            public UIntPtr Affinity;
            public uint PriorityClass;
            public uint SchedulingClass;
        }

        [StructLayout(LayoutKind.Sequential)]
        public struct IO_COUNTERS
        {
            public ulong ReadOperationCount;
            public ulong WriteOperationCount;
            public ulong OtherOperationCount;
            public ulong ReadTransferCount;
            public ulong WriteTransferCount;
            public ulong OtherTransferCount;
        }

        [StructLayout(LayoutKind.Sequential)]
        public struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        {
            public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
            public IO_COUNTERS IoInfo;
            public UIntPtr ProcessMemoryLimit;
            public UIntPtr JobMemoryLimit;
            public UIntPtr PeakProcessMemoryUsed;
            public UIntPtr PeakJobMemoryUsed;
        }
    }

    internal sealed class JobTracker : IDisposable
    {
        private IntPtr _jobHandle;

        public JobTracker()
        {
            _jobHandle = NativeMethods.CreateJobObject(IntPtr.Zero, null);
            if (_jobHandle == IntPtr.Zero) return;

            var info = new NativeMethods.JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
            info.BasicLimitInformation.LimitFlags = NativeMethods.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            int length = Marshal.SizeOf(typeof(NativeMethods.JOBOBJECT_EXTENDED_LIMIT_INFORMATION));
            IntPtr ptr = Marshal.AllocHGlobal(length);
            try
            {
                Marshal.StructureToPtr(info, ptr, false);
                NativeMethods.SetInformationJobObject(_jobHandle, NativeMethods.JobObjectExtendedLimitInformation, ptr, (uint)length);
            }
            finally
            {
                Marshal.FreeHGlobal(ptr);
            }
        }

        public void AddProcess(Process process)
        {
            if (_jobHandle != IntPtr.Zero && process != null && !process.HasExited)
            {
                try { NativeMethods.AssignProcessToJobObject(_jobHandle, process.Handle); }
                catch { }
            }
        }

        public void Dispose()
        {
            if (_jobHandle != IntPtr.Zero)
            {
                NativeMethods.CloseHandle(_jobHandle);
                _jobHandle = IntPtr.Zero;
            }
        }
    }

    internal sealed class SecretBundle
    {
        public string JwtSecret;
        public string CredKey;
    }

    internal sealed class SplashStatusForm : Form
    {
        private readonly Label _statusLabel;
        private readonly ProgressBar _progressBar;

        public SplashStatusForm()
        {
            Text = "NexusBI Enterprise";
            Size = new Size(460, 170);
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;
            BackColor = Color.FromArgb(15, 23, 42);
            ForeColor = Color.White;

            var title = new Label
            {
                Text = "NexusBI Enterprise Intelligence Platform",
                Font = new Font("Segoe UI", 12F, FontStyle.Bold),
                AutoSize = false,
                Bounds = new Rectangle(24, 20, 400, 28),
                ForeColor = Color.White
            };
            _statusLabel = new Label
            {
                Text = "Initializing local analytical engine and services...",
                Font = new Font("Segoe UI", 9.5F, FontStyle.Regular),
                AutoSize = false,
                Bounds = new Rectangle(24, 56, 400, 26),
                ForeColor = Color.FromArgb(203, 213, 225)
            };
            _progressBar = new ProgressBar
            {
                Style = ProgressBarStyle.Marquee,
                MarqueeAnimationSpeed = 28,
                Bounds = new Rectangle(24, 92, 396, 16)
            };

            Controls.Add(title);
            Controls.Add(_statusLabel);
            Controls.Add(_progressBar);
        }

        public void UpdateStatus(string text)
        {
            if (IsDisposed) return;
            if (InvokeRequired)
            {
                try { BeginInvoke(new Action<string>(UpdateStatus), text); }
                catch { }
                return;
            }
            _statusLabel.Text = text;
        }
    }

    internal sealed class NexusApplicationContext : ApplicationContext
    {
        private const string MutexName = @"Local\NexusBI_Desktop_Instance";
        private const string StopEventName = @"Local\NexusBI_Stop_Signal";

        private readonly string _appRoot;
        private readonly string _userRoot;
        private readonly string _dataDir;
        private readonly string _configDir;
        private readonly string _logsDir;
        private readonly string _runtimeDir;
        private readonly string _stateFile;
        private readonly string _setupTokenFile;
        private readonly SynchronizationContext _uiSync;

        private NotifyIcon _trayIcon;
        private SplashStatusForm _splash;
        private JobTracker _jobTracker;
        private Process _backendProc;
        private Process _gatewayProc;
        private StreamWriter _backendLogWriter;
        private StreamWriter _gatewayLogWriter;
        private EventWaitHandle _stopEvent;
        private Thread _stopListenerThread;
        private Thread _watchdogThread;

        private int _backendPort;
        private int _nextPort;
        private int _uiPort;
        private string _uiUrl = "";
        private string _ephemeralSetupToken = "";
        private string _controlToken = "";
        private volatile bool _exiting = false;
        private volatile bool _restarting = false;

        public NexusApplicationContext(string appRoot, string userRoot)
        {
            _appRoot = appRoot;
            _userRoot = userRoot;
            _dataDir = Path.Combine(_userRoot, "data");
            _configDir = Path.Combine(_userRoot, "config");
            _logsDir = Path.Combine(_userRoot, "logs");
            _runtimeDir = Path.Combine(_userRoot, "runtime");
            _stateFile = Path.Combine(_runtimeDir, "state.json");
            _setupTokenFile = Path.Combine(_configDir, "setup_token.txt");

            EnsureSecureUserDirectories();
            SetupTray();

            bool createdNew;
            _stopEvent = new EventWaitHandle(false, EventResetMode.ManualReset, StopEventName, out createdNew);
            if (!createdNew) _stopEvent.Reset();

            _stopListenerThread = new Thread(ListenForStopSignal) { IsBackground = true };
            _stopListenerThread.Start();

            _splash = new SplashStatusForm();
            _splash.Show();
            _uiSync = SynchronizationContext.Current ?? new WindowsFormsSynchronizationContext();

            ThreadPool.QueueUserWorkItem(delegate { StartAllAndLaunch(); });
        }

        private void EnsureSecureUserDirectories()
        {
            Directory.CreateDirectory(_userRoot);
            Directory.CreateDirectory(_dataDir);
            Directory.CreateDirectory(Path.Combine(_dataDir, "uploads"));
            Directory.CreateDirectory(Path.Combine(_dataDir, "exports"));
            Directory.CreateDirectory(_configDir);
            Directory.CreateDirectory(_logsDir);
            Directory.CreateDirectory(_runtimeDir);

            ApplyCurrentUserOnlyDirectoryAcl(_configDir);
            ApplyCurrentUserOnlyDirectoryAcl(_dataDir);
        }

        private static void ApplyCurrentUserOnlyDirectoryAcl(string dirPath)
        {
            try
            {
                var identity = WindowsIdentity.GetCurrent();
                if (identity == null || identity.User == null) return;
                var sec = new DirectorySecurity();
                sec.SetAccessRuleProtection(true, false);
                sec.AddAccessRule(new FileSystemAccessRule(
                    identity.User,
                    FileSystemRights.FullControl,
                    InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit,
                    PropagationFlags.None,
                    AccessControlType.Allow));
                sec.AddAccessRule(new FileSystemAccessRule(
                    new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null),
                    FileSystemRights.FullControl,
                    InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit,
                    PropagationFlags.None,
                    AccessControlType.Allow));
                Directory.SetAccessControl(dirPath, sec);
            }
            catch
            {
                // Best effort if restricted filesystem policy applies
            }
        }

        private static void ApplyCurrentUserOnlyFileAcl(string filePath)
        {
            try
            {
                var identity = WindowsIdentity.GetCurrent();
                if (identity == null || identity.User == null) return;
                var sec = new FileSecurity();
                sec.SetAccessRuleProtection(true, false);
                sec.AddAccessRule(new FileSystemAccessRule(identity.User, FileSystemRights.FullControl, AccessControlType.Allow));
                sec.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null), FileSystemRights.FullControl, AccessControlType.Allow));
                File.SetAccessControl(filePath, sec);
            }
            catch
            {
            }
        }

        private SecretBundle LoadOrGenerateDpapiSecrets()
        {
            string dpapiPath = Path.Combine(_configDir, "secrets.dpapi");
            if (File.Exists(dpapiPath))
            {
                try
                {
                    byte[] enc = File.ReadAllBytes(dpapiPath);
                    byte[] plain = ProtectedData.Unprotect(enc, null, DataProtectionScope.CurrentUser);
                    string text = Encoding.UTF8.GetString(plain);
                    string[] lines = text.Split(new[] { '\n', '\r' }, StringSplitOptions.RemoveEmptyEntries);
                    if (lines.Length >= 2 && lines[0].Length >= 32 && lines[1].Length >= 32)
                    {
                        return new SecretBundle { JwtSecret = lines[0], CredKey = lines[1] };
                    }
                }
                catch
                {
                    // Regenerate if unreadable
                }
            }

            string jwtSecret = GenerateUrlSafeBytes(48);
            string credKey = GenerateBase64Bytes(32);
            byte[] raw = Encoding.UTF8.GetBytes(jwtSecret + "\n" + credKey + "\n");
            byte[] encrypted = ProtectedData.Protect(raw, null, DataProtectionScope.CurrentUser);
            File.WriteAllBytes(dpapiPath, encrypted);
            ApplyCurrentUserOnlyFileAcl(dpapiPath);

            return new SecretBundle { JwtSecret = jwtSecret, CredKey = credKey };
        }

        private static string GenerateUrlSafeBytes(int count)
        {
            byte[] buf = new byte[count];
            using (var rng = new RNGCryptoServiceProvider())
            {
                rng.GetBytes(buf);
            }
            return Convert.ToBase64String(buf).Replace("+", "-").Replace("/", "_").TrimEnd('=');
        }

        private static string GenerateBase64Bytes(int count)
        {
            byte[] buf = new byte[count];
            using (var rng = new RNGCryptoServiceProvider())
            {
                rng.GetBytes(buf);
            }
            return Convert.ToBase64String(buf);
        }

        private static int FindFreeLoopbackPort(int preferredPort, HashSet<int> avoid)
        {
            if (!avoid.Contains(preferredPort) && IsPortFree(preferredPort))
            {
                avoid.Add(preferredPort);
                return preferredPort;
            }
            for (int attempt = 0; attempt < 10; attempt++)
            {
                var listener = new TcpListener(IPAddress.Loopback, 0);
                listener.Start();
                int port = ((IPEndPoint)listener.LocalEndpoint).Port;
                listener.Stop();
                if (!avoid.Contains(port))
                {
                    avoid.Add(port);
                    return port;
                }
            }
            throw new InvalidOperationException("Unable to find an available local TCP port.");
        }

        private static bool IsPortFree(int port)
        {
            TcpListener listener = null;
            try
            {
                listener = new TcpListener(IPAddress.Loopback, port);
                listener.Start();
                return true;
            }
            catch
            {
                return false;
            }
            finally
            {
                if (listener != null)
                {
                    try { listener.Stop(); } catch { }
                }
            }
        }

        private StreamWriter OpenRotatingLog(string filename)
        {
            string fullPath = Path.Combine(_logsDir, filename);
            try
            {
                if (File.Exists(fullPath) && new FileInfo(fullPath).Length > 5 * 1024 * 1024)
                {
                    string prev = fullPath + ".old";
                    if (File.Exists(prev)) File.Delete(prev);
                    File.Move(fullPath, prev);
                }
            }
            catch { }
            return new StreamWriter(new FileStream(fullPath, FileMode.Append, FileAccess.Write, FileShare.ReadWrite), Encoding.UTF8) { AutoFlush = true };
        }

        private void SetupTray()
        {
            var menu = new ContextMenuStrip();
            menu.Items.Add("Open NexusBI in Browser", null, delegate { OpenBrowser(false); });
            menu.Items.Add("Open NexusBI App Window", null, delegate { OpenBrowser(true); });
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add("Restart Local Services", null, delegate { RestartServicesAsync(); });
            menu.Items.Add("Open Data Folder", null, delegate { OpenFolder(_dataDir); });
            menu.Items.Add("Open Logs Folder", null, delegate { OpenFolder(_logsDir); });
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add("Exit & Stop Services", null, delegate { ShutdownAndExit(); });

            Icon appIcon = SystemIcons.Application;
            try
            {
                string exePath = Process.GetCurrentProcess().MainModule.FileName;
                Icon extracted = Icon.ExtractAssociatedIcon(exePath);
                if (extracted != null) appIcon = extracted;
            }
            catch { }

            _trayIcon = new NotifyIcon
            {
                Icon = appIcon,
                Text = "NexusBI Enterprise",
                ContextMenuStrip = menu,
                Visible = true
            };
            _trayIcon.DoubleClick += delegate { OpenBrowser(false); };
        }

        private void StartAllAndLaunch()
        {
            try
            {
                _splash.UpdateStatus("Preparing local security keys and ports...");
                SecretBundle secrets = LoadOrGenerateDpapiSecrets();
                _ephemeralSetupToken = GenerateUrlSafeBytes(24);
                _controlToken = GenerateUrlSafeBytes(24);

                var usedPorts = new HashSet<int>();
                _backendPort = FindFreeLoopbackPort(18080, usedPorts);
                _nextPort = FindFreeLoopbackPort(18001, usedPorts);
                _uiPort = FindFreeLoopbackPort(18000, usedPorts);
                _uiUrl = string.Format("http://127.0.0.1:{0}", _uiPort);

                string pythonExe = Path.Combine(_appRoot, "runtime", "python", "python.exe");
                string backendDir = Path.Combine(_appRoot, "backend");
                string nodeExe = Path.Combine(_appRoot, "runtime", "node", "node.exe");
                string gatewayJs = Path.Combine(_appRoot, "runtime", "web_gateway.mjs");
                string standaloneDir = Path.Combine(_appRoot, "frontend", ".next", "standalone");

                if (!File.Exists(pythonExe)) throw new FileNotFoundException("Bundled Python runtime missing", pythonExe);
                if (!File.Exists(nodeExe)) throw new FileNotFoundException("Bundled Node.js runtime missing", nodeExe);
                if (!File.Exists(gatewayJs)) throw new FileNotFoundException("Web gateway script missing", gatewayJs);
                if (!File.Exists(Path.Combine(standaloneDir, "server.js"))) throw new FileNotFoundException("Standalone Next.js server.js missing", standaloneDir);

                _jobTracker = new JobTracker();
                _backendLogWriter = OpenRotatingLog("backend.log");
                _gatewayLogWriter = OpenRotatingLog("frontend.log");

                _splash.UpdateStatus("Preparing analytical database schema...");
                var migInfo = new ProcessStartInfo
                {
                    FileName = pythonExe,
                    Arguments = "-m app.db_migrate",
                    WorkingDirectory = backendDir,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true
                };
                migInfo.EnvironmentVariables["ENVIRONMENT"] = "production";
                migInfo.EnvironmentVariables["BI_DATA_DIR"] = _dataDir;
                migInfo.EnvironmentVariables["BI_SEED_DEMO_USERS"] = "0";
                migInfo.EnvironmentVariables["BI_REQUIRE_GENERATED_SECRETS"] = "1";
                migInfo.EnvironmentVariables["BI_JWT_SECRET"] = secrets.JwtSecret;
                migInfo.EnvironmentVariables["BI_CRED_KEY"] = secrets.CredKey;
                migInfo.EnvironmentVariables["PYTHONNOUSERSITE"] = "1";
                migInfo.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1";
                migInfo.EnvironmentVariables["PYTHONUTF8"] = "1";
                migInfo.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";
                using (var migProc = Process.Start(migInfo))
                {
                    string migOut = migProc.StandardOutput.ReadToEnd() + "\n" + migProc.StandardError.ReadToEnd();
                    migProc.WaitForExit();
                    if (_backendLogWriter != null && !string.IsNullOrWhiteSpace(migOut))
                    {
                        _backendLogWriter.WriteLine(migOut.Trim());
                    }
                    if (migProc.ExitCode != 0)
                    {
                        throw new Exception("Database schema migration failed: " + migOut);
                    }
                }

                _splash.UpdateStatus("Starting analytical warehouse & BI API service...");
                var pyInfo = new ProcessStartInfo
                {
                    FileName = pythonExe,
                    Arguments = string.Format("-m uvicorn app.main:app --host 127.0.0.1 --port {0}", _backendPort),
                    WorkingDirectory = backendDir,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true
                };
                pyInfo.EnvironmentVariables["ENVIRONMENT"] = "production";
                pyInfo.EnvironmentVariables["BI_DATA_DIR"] = _dataDir;
                pyInfo.EnvironmentVariables["BI_SEED_DEMO_USERS"] = "0";
                pyInfo.EnvironmentVariables["BI_REQUIRE_GENERATED_SECRETS"] = "1";
                pyInfo.EnvironmentVariables["BI_JWT_SECRET"] = secrets.JwtSecret;
                pyInfo.EnvironmentVariables["BI_CRED_KEY"] = secrets.CredKey;
                pyInfo.EnvironmentVariables["BI_INITIAL_SETUP_TOKEN"] = _ephemeralSetupToken;
                pyInfo.EnvironmentVariables["BI_DESKTOP_CONTROL_TOKEN"] = _controlToken;
                pyInfo.EnvironmentVariables["PYTHONNOUSERSITE"] = "1";
                pyInfo.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1";
                pyInfo.EnvironmentVariables["PYTHONUTF8"] = "1";
                pyInfo.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";

                _backendProc = new Process { StartInfo = pyInfo, EnableRaisingEvents = true };
                _backendProc.OutputDataReceived += delegate(object s, DataReceivedEventArgs e)
                {
                    if (e.Data != null && _backendLogWriter != null) _backendLogWriter.WriteLine(e.Data);
                };
                _backendProc.ErrorDataReceived += delegate(object s, DataReceivedEventArgs e)
                {
                    if (e.Data != null && _backendLogWriter != null) _backendLogWriter.WriteLine(e.Data);
                };
                _backendProc.Start();
                _backendProc.BeginOutputReadLine();
                _backendProc.BeginErrorReadLine();
                _jobTracker.AddProcess(_backendProc);

                _splash.UpdateStatus("Starting BI web interface...");
                var nodeInfo = new ProcessStartInfo
                {
                    FileName = nodeExe,
                    Arguments = string.Format("\"{0}\"", gatewayJs),
                    WorkingDirectory = standaloneDir,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true
                };
                nodeInfo.EnvironmentVariables["NODE_ENV"] = "production";
                nodeInfo.EnvironmentVariables["HOSTNAME"] = "127.0.0.1";
                nodeInfo.EnvironmentVariables["PORT"] = _uiPort.ToString();
                nodeInfo.EnvironmentVariables["NEXT_INTERNAL_PORT"] = _nextPort.ToString();
                nodeInfo.EnvironmentVariables["BACKEND_INTERNAL_URL"] = string.Format("http://127.0.0.1:{0}", _backendPort);
                nodeInfo.EnvironmentVariables["STANDALONE_DIR"] = standaloneDir;
                nodeInfo.EnvironmentVariables["GATEWAY_CONTROL_TOKEN"] = _controlToken;

                _gatewayProc = new Process { StartInfo = nodeInfo, EnableRaisingEvents = true };
                _gatewayProc.OutputDataReceived += delegate(object s, DataReceivedEventArgs e)
                {
                    if (e.Data != null && _gatewayLogWriter != null) _gatewayLogWriter.WriteLine(e.Data);
                };
                _gatewayProc.ErrorDataReceived += delegate(object s, DataReceivedEventArgs e)
                {
                    if (e.Data != null && _gatewayLogWriter != null) _gatewayLogWriter.WriteLine(e.Data);
                };
                _gatewayProc.Start();
                _gatewayProc.BeginOutputReadLine();
                _gatewayProc.BeginErrorReadLine();
                _jobTracker.AddProcess(_gatewayProc);

                _splash.UpdateStatus("Waiting for health checks...");
                if (!WaitUntilHealthy(60))
                {
                    ShowStartupFailure("One or more local NexusBI services failed to start within 60 seconds.");
                    return;
                }

                WriteRuntimeState();

                string targetUrl = _uiUrl;
                bool needsSetup = QueryNeedsInitialSetup();
                if (needsSetup)
                {
                    File.WriteAllText(_setupTokenFile, _ephemeralSetupToken, Encoding.UTF8);
                    ApplyCurrentUserOnlyFileAcl(_setupTokenFile);
                    targetUrl = _uiUrl + "/?setup_token=" + Uri.EscapeDataString(_ephemeralSetupToken);
                }
                else
                {
                    try { if (File.Exists(_setupTokenFile)) File.Delete(_setupTokenFile); } catch { }
                }

                HideSplash();
                _trayIcon.ShowBalloonTip(3000, "NexusBI Ready", "NexusBI is running locally on " + _uiUrl, ToolTipIcon.Info);
                LaunchUrl(targetUrl, false);

                _watchdogThread = new Thread(WatchChildProcesses) { IsBackground = true };
                _watchdogThread.Start();
            }
            catch (Exception ex)
            {
                ShowStartupFailure("Failed to start NexusBI: " + ex.Message);
            }
        }

        private bool WaitUntilHealthy(int timeoutSeconds)
        {
            DateTime deadline = DateTime.UtcNow.AddSeconds(timeoutSeconds);
            while (DateTime.UtcNow < deadline && !_exiting)
            {
                if (_backendProc == null || _backendProc.HasExited) return false;
                if (_gatewayProc == null || _gatewayProc.HasExited) return false;

                if (HttpGetOk(string.Format("http://127.0.0.1:{0}/__gateway/health", _uiPort)) &&
                    HttpGetOk(string.Format("http://127.0.0.1:{0}/api/v1/health", _uiPort)))
                {
                    return true;
                }
                Thread.Sleep(400);
            }
            return false;
        }

        private static bool HttpGetOk(string url)
        {
            try
            {
                var req = (HttpWebRequest)WebRequest.Create(url);
                req.Method = "GET";
                req.Timeout = 1500;
                req.Proxy = null;
                using (var resp = (HttpWebResponse)req.GetResponse())
                {
                    return ((int)resp.StatusCode >= 200 && (int)resp.StatusCode < 300);
                }
            }
            catch
            {
                return false;
            }
        }

        private bool QueryNeedsInitialSetup()
        {
            try
            {
                var req = (HttpWebRequest)WebRequest.Create(string.Format("http://127.0.0.1:{0}/api/v1/auth/status", _uiPort));
                req.Method = "GET";
                req.Timeout = 3000;
                req.Proxy = null;
                using (var resp = (HttpWebResponse)req.GetResponse())
                using (var sr = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                {
                    string json = sr.ReadToEnd();
                    return json.IndexOf("\"needs_initial_setup\":true", StringComparison.OrdinalIgnoreCase) >= 0 ||
                           json.IndexOf("\"needs_initial_setup\": true", StringComparison.OrdinalIgnoreCase) >= 0;
                }
            }
            catch
            {
                return false;
            }
        }

        private void WriteRuntimeState()
        {
            try
            {
                string json = string.Format(
                    "{{\"ui_url\":\"{0}\",\"ui_port\":{1},\"backend_port\":{2},\"pid\":{3}}}",
                    _uiUrl, _uiPort, _backendPort, Process.GetCurrentProcess().Id);
                File.WriteAllText(_stateFile, json, Encoding.UTF8);
            }
            catch { }
        }

        private void HideSplash()
        {
            if (_splash == null || _splash.IsDisposed) return;
            try
            {
                _splash.BeginInvoke(new Action(delegate { _splash.Hide(); }));
            }
            catch { }
        }

        private void ShowStartupFailure(string message)
        {
            HideSplash();
            string logTail = ReadLogTail("backend.log", 20) + "\n" + ReadLogTail("frontend.log", 12);
            string full = message + "\n\nLogs location: " + _logsDir + "\n\n" + logTail;
            DialogResult res = MessageBox.Show(
                full + "\n\nClick Retry to attempt restart, or Cancel to open the Logs folder and exit.",
                "NexusBI Startup Error",
                MessageBoxButtons.RetryCancel,
                MessageBoxIcon.Error);
            if (res == DialogResult.Retry)
            {
                RestartServicesAsync();
            }
            else
            {
                OpenFolder(_logsDir);
                ShutdownAndExit();
            }
        }

        private string ReadLogTail(string name, int maxLines)
        {
            try
            {
                string p = Path.Combine(_logsDir, name);
                if (!File.Exists(p)) return "";
                string[] lines = File.ReadAllLines(p);
                int start = Math.Max(0, lines.Length - maxLines);
                var sb = new StringBuilder();
                sb.AppendLine("--- " + name + " ---");
                for (int i = start; i < lines.Length; i++) sb.AppendLine(lines[i]);
                return sb.ToString();
            }
            catch
            {
                return "";
            }
        }

        private void WatchChildProcesses()
        {
            while (!_exiting && !_restarting)
            {
                Thread.Sleep(1500);
                if (_exiting || _restarting) break;
                bool bDead = _backendProc != null && _backendProc.HasExited;
                bool gDead = _gatewayProc != null && _gatewayProc.HasExited;
                if (bDead || gDead)
                {
                    if (_exiting || _restarting) break;
                    if (_trayIcon != null)
                    {
                        _trayIcon.ShowBalloonTip(4000, "NexusBI Service Stopped",
                            "A local service stopped unexpectedly. Check logs or restart from tray icon.", ToolTipIcon.Warning);
                    }
                    break;
                }
            }
        }

        private void OpenBrowser(bool preferEdgeApp)
        {
            if (string.IsNullOrEmpty(_uiUrl)) return;
            string url = _uiUrl;
            if (QueryNeedsInitialSetup() && !string.IsNullOrEmpty(_ephemeralSetupToken))
            {
                url = _uiUrl + "/?setup_token=" + Uri.EscapeDataString(_ephemeralSetupToken);
            }
            LaunchUrl(url, preferEdgeApp);
        }

        private static void LaunchUrl(string url, bool preferEdgeApp)
        {
            try
            {
                if (preferEdgeApp)
                {
                    string[] edgePaths = new[]
                    {
                        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86), @"Microsoft\Edge\Application\msedge.exe"),
                        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), @"Microsoft\Edge\Application\msedge.exe")
                    };
                    foreach (string edge in edgePaths)
                    {
                        if (File.Exists(edge))
                        {
                            Process.Start(edge, "--app=" + url);
                            return;
                        }
                    }
                }
                Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
            }
            catch { }
        }

        private static void OpenFolder(string path)
        {
            try { Process.Start("explorer.exe", "\"" + path + "\""); } catch { }
        }

        private void RestartServicesAsync()
        {
            if (_restarting) return;
            _restarting = true;
            ThreadPool.QueueUserWorkItem(delegate
            {
                StopChildProcesses();
                _restarting = false;
                if (_splash != null && !_splash.IsDisposed)
                {
                    try { _splash.BeginInvoke(new Action(delegate { _splash.Show(); })); } catch { }
                }
                StartAllAndLaunch();
            });
        }

        private void ListenForStopSignal()
        {
            try
            {
                _stopEvent.WaitOne();
                if (!_exiting) ShutdownAndExit();
            }
            catch { }
        }

        private void StopChildProcesses()
        {
            if (_gatewayProc != null && !_gatewayProc.HasExited && _uiPort > 0 && !string.IsNullOrEmpty(_controlToken))
            {
                SendGracefulShutdown(string.Format("http://127.0.0.1:{0}/__gateway/shutdown", _uiPort), "X-Gateway-Token", _controlToken);
            }
            if (_backendProc != null && !_backendProc.HasExited && _backendPort > 0 && !string.IsNullOrEmpty(_controlToken))
            {
                SendGracefulShutdown(string.Format("http://127.0.0.1:{0}/_internal/shutdown", _backendPort), "X-Desktop-Control-Token", _controlToken);
            }

            TerminateProcessCleanly(_gatewayProc);
            _gatewayProc = null;
            TerminateProcessCleanly(_backendProc);
            _backendProc = null;

            try { if (_gatewayLogWriter != null) _gatewayLogWriter.Dispose(); } catch { }
            try { if (_backendLogWriter != null) _backendLogWriter.Dispose(); } catch { }
            _gatewayLogWriter = null;
            _backendLogWriter = null;

            if (_jobTracker != null)
            {
                _jobTracker.Dispose();
                _jobTracker = null;
            }
        }

        private static void SendGracefulShutdown(string url, string headerName, string token)
        {
            try
            {
                var req = (HttpWebRequest)WebRequest.Create(url);
                req.Method = "POST";
                req.Timeout = 1500;
                req.ContentLength = 0;
                req.Proxy = null;
                req.Headers[headerName] = token;
                using (var resp = (HttpWebResponse)req.GetResponse()) { }
            }
            catch { }
        }

        private static void TerminateProcessCleanly(Process proc)
        {
            if (proc == null) return;
            try
            {
                if (!proc.HasExited)
                {
                    if (!proc.WaitForExit(5000))
                    {
                        try
                        {
                            var killer = Process.Start(new ProcessStartInfo
                            {
                                FileName = "taskkill",
                                Arguments = string.Format("/PID {0} /T /F", proc.Id),
                                UseShellExecute = false,
                                CreateNoWindow = true
                            });
                            if (killer != null) killer.WaitForExit(3000);
                        }
                        catch { }
                        if (!proc.HasExited)
                        {
                            proc.Kill();
                            proc.WaitForExit(2000);
                        }
                    }
                }
            }
            catch { }
            finally
            {
                try { proc.Dispose(); } catch { }
            }
        }

        private void ShutdownAndExit()
        {
            if (_exiting) return;
            _exiting = true;
            StopChildProcesses();
            try { if (File.Exists(_stateFile)) File.Delete(_stateFile); } catch { }
            try { if (File.Exists(_setupTokenFile)) File.Delete(_setupTokenFile); } catch { }
            _uiSync.Post(delegate
            {
                if (_trayIcon != null)
                {
                    _trayIcon.Visible = false;
                    _trayIcon.Dispose();
                }
                if (_splash != null && !_splash.IsDisposed)
                {
                    try { _splash.Close(); } catch { }
                }
                try { ExitThread(); } catch { Application.Exit(); }
            }, null);
        }

        public static int RunMain(string[] args)
        {
            if (!Environment.Is64BitOperatingSystem)
            {
                MessageBox.Show(
                    "NexusBI Enterprise requires a 64-bit (x64) edition of Windows 10 or Windows 11.",
                    "Unsupported Windows Architecture",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
                return 1;
            }

            if (args.Length > 0 && args[0].Equals("--stop", StringComparison.OrdinalIgnoreCase))
            {
                return StopRunningInstance();
            }

            bool createdNew;
            using (var mutex = new Mutex(true, MutexName, out createdNew))
            {
                if (!createdNew)
                {
                    FocusExistingInstance();
                    return 0;
                }

                string appRoot = Path.GetFullPath(AppDomain.CurrentDomain.BaseDirectory);
                string userRoot = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "NexusBI");

                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                var ctx = new NexusApplicationContext(appRoot, userRoot);
                Application.Run(ctx);
                return 0;
            }
        }

        private static void FocusExistingInstance()
        {
            try
            {
                string stateFile = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "NexusBI", "runtime", "state.json");
                if (File.Exists(stateFile))
                {
                    string json = File.ReadAllText(stateFile, Encoding.UTF8);
                    int idx = json.IndexOf("\"ui_url\":\"", StringComparison.Ordinal);
                    if (idx >= 0)
                    {
                        int start = idx + 10;
                        int end = json.IndexOf("\"", start, StringComparison.Ordinal);
                        if (end > start)
                        {
                            string url = json.Substring(start, end - start);
                            Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
                            return;
                        }
                    }
                }
            }
            catch { }
            MessageBox.Show("NexusBI is already running in the system tray.", "NexusBI Enterprise", MessageBoxButtons.OK, MessageBoxIcon.Information);
        }

        private static int StopRunningInstance()
        {
            try
            {
                EventWaitHandle ev = EventWaitHandle.OpenExisting(StopEventName);
                ev.Set();
                ev.Dispose();
            }
            catch (WaitHandleCannotBeOpenedException)
            {
                return 0;
            }

            DateTime deadline = DateTime.UtcNow.AddSeconds(12);
            while (DateTime.UtcNow < deadline)
            {
                bool free;
                using (var m = new Mutex(false, MutexName, out free))
                {
                    if (free) return 0;
                }
                Thread.Sleep(300);
            }
            return 0;
        }
    }

    internal static class Program
    {
        [STAThread]
        private static int Main(string[] args)
        {
            return NexusApplicationContext.RunMain(args);
        }
    }
}
