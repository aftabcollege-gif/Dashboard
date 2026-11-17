/**
 * NexusBI Desktop Runtime Web Gateway
 *
 * Bridges the Next.js standalone production server and the local FastAPI/DuckDB backend
 * over dynamic loopback ports (127.0.0.1), enabling conflict-free port allocation and
 * zero build-time hardcoded backend URLs.
 */

import http from "node:http";
import path from "node:path";
import { spawn } from "node:child_process";

const UI_PORT = parseInt(process.env.PORT || "18000", 10);
const NEXT_PORT = parseInt(process.env.NEXT_INTERNAL_PORT || "18001", 10);
const HOSTNAME = process.env.HOSTNAME || "127.0.0.1";
const BACKEND_URL = new URL(process.env.BACKEND_INTERNAL_URL || "http://127.0.0.1:18080");
const STANDALONE_DIR = path.resolve(process.env.STANDALONE_DIR || process.cwd());
const GATEWAY_CONTROL_TOKEN = process.env.GATEWAY_CONTROL_TOKEN || "";
const SERVER_JS = path.join(STANDALONE_DIR, "server.js");

const nextChild = spawn(process.execPath, [SERVER_JS], {
  cwd: STANDALONE_DIR,
  env: {
    ...process.env,
    NODE_ENV: "production",
    HOSTNAME: "127.0.0.1",
    PORT: String(NEXT_PORT),
  },
  stdio: ["ignore", "pipe", "pipe"],
  windowsHide: true,
});

nextChild.stdout.on("data", (chunk) => process.stdout.write(`[next] ${chunk}`));
nextChild.stderr.on("data", (chunk) => process.stderr.write(`[next] ${chunk}`));

let shuttingDown = false;
nextChild.on("exit", (code, signal) => {
  if (!shuttingDown) {
    console.error(`[gateway] Next.js standalone exited unexpectedly (code=${code}, signal=${signal})`);
    process.exit(code || 1);
  }
});

function proxyRequest(req, res, targetHost, targetPort) {
  const headers = { ...req.headers };
  headers["x-forwarded-host"] = req.headers.host || `${HOSTNAME}:${UI_PORT}`;
  headers["x-forwarded-proto"] = "http";
  headers["host"] = `${targetHost}:${targetPort}`;

  const proxyReq = http.request(
    {
      hostname: targetHost,
      port: targetPort,
      path: req.url,
      method: req.method,
      headers,
    },
    (proxyRes) => {
      res.writeHead(proxyRes.statusCode || 502, proxyRes.headers);
      proxyRes.pipe(res, { end: true });
    }
  );

  proxyReq.on("error", (err) => {
    if (!res.headersSent) {
      res.writeHead(502, { "Content-Type": "application/json; charset=utf-8" });
      res.end(JSON.stringify({ detail: `Upstream service unavailable: ${err.message}` }));
    } else {
      res.end();
    }
  });

  req.pipe(proxyReq, { end: true });
}

const server = http.createServer((req, res) => {
  const urlPath = req.url || "/";
  if (urlPath === "/__gateway/health") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ status: "ok", ui_port: UI_PORT, next_port: NEXT_PORT }));
    return;
  }

  if (urlPath === "/__gateway/shutdown" && req.method === "POST") {
    const provided = String(req.headers["x-gateway-token"] || "");
    if (!GATEWAY_CONTROL_TOKEN || provided !== GATEWAY_CONTROL_TOKEN) {
      res.writeHead(403, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ detail: "Forbidden" }));
      return;
    }
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ status: "shutting_down" }));
    setTimeout(shutdown, 80);
    return;
  }

  if (urlPath.startsWith("/api/") || urlPath === "/api" || urlPath === "/health") {
    proxyRequest(req, res, BACKEND_URL.hostname, parseInt(BACKEND_URL.port || "80", 10));
    return;
  }

  proxyRequest(req, res, "127.0.0.1", NEXT_PORT);
});

server.requestTimeout = 120_000;
server.headersTimeout = 60_000;

server.listen(UI_PORT, HOSTNAME, () => {
  console.log(`[gateway] NexusBI Desktop Web Gateway listening on http://${HOSTNAME}:${UI_PORT} (next=${NEXT_PORT}, backend=${BACKEND_URL.origin})`);
});

function shutdown() {
  if (shuttingDown) return;
  shuttingDown = true;
  server.close(() => {
    try {
      nextChild.kill("SIGTERM");
    } catch {
      // ignore
    }
    process.exit(0);
  });
  setTimeout(() => {
    try {
      nextChild.kill("SIGKILL");
    } catch {
      // ignore
    }
    process.exit(0);
  }, 4000).unref();
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
