#!/usr/bin/env node

import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const env = Object.fromEntries(
  readFileSync(join(root, ".env"), "utf8")
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("#") && line.includes("="))
    .map((line) => {
      const split = line.indexOf("=");
      return [line.slice(0, split), line.slice(split + 1)];
    }),
);

const browserPath = "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser";
const profile = mkdtempSync(join(tmpdir(), "ai-router-dashboard-"));
const outputDir = join(root, ".impeccable", "review");
mkdirSync(outputDir, { recursive: true });

const browser = spawn(
  browserPath,
  [
    "--headless=new",
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    "--remote-debugging-port=0",
    `--user-data-dir=${profile}`,
    "about:blank",
  ],
  { stdio: ["ignore", "ignore", "pipe"] },
);

const debugUrl = await new Promise((resolveUrl, reject) => {
  let stderr = "";
  const timer = setTimeout(() => reject(new Error(`Brave did not start: ${stderr}`)), 15000);
  browser.stderr.on("data", (chunk) => {
    stderr += chunk.toString();
    const match = stderr.match(/DevTools listening on (ws:\/\/[^\s]+)/);
    if (match) {
      clearTimeout(timer);
      resolveUrl(match[1]);
    }
  });
  browser.on("exit", (code) => reject(new Error(`Brave exited early with ${code}`)));
});

const socket = new WebSocket(debugUrl);
await new Promise((resolveOpen, reject) => {
  socket.addEventListener("open", resolveOpen, { once: true });
  socket.addEventListener("error", reject, { once: true });
});

let nextId = 1;
const pending = new Map();
socket.addEventListener("message", (event) => {
  const message = JSON.parse(event.data);
  if (!message.id || !pending.has(message.id)) return;
  const { resolve: resolveCall, reject } = pending.get(message.id);
  pending.delete(message.id);
  if (message.error) reject(new Error(message.error.message));
  else resolveCall(message.result);
});

function call(method, params = {}, sessionId = undefined) {
  const id = nextId++;
  socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  return new Promise((resolveCall, reject) => pending.set(id, { resolve: resolveCall, reject }));
}

const { targetId } = await call("Target.createTarget", { url: "about:blank" });
const { sessionId } = await call("Target.attachToTarget", { targetId, flatten: true });
await call("Page.enable", {}, sessionId);
await call("Runtime.enable", {}, sessionId);

async function navigateAndWait(url) {
  await call("Page.navigate", { url }, sessionId);
  await new Promise((resolveWait) => setTimeout(resolveWait, 750));
}

async function capture(name, width, height, mobile) {
  await call(
    "Emulation.setDeviceMetricsOverride",
    { width, height, deviceScaleFactor: 1, mobile },
    sessionId,
  );
  await navigateAndWait("http://127.0.0.1:8088/");
  await call(
    "Runtime.evaluate",
    {
      expression: `sessionStorage.setItem("ai-router-admin-token", ${JSON.stringify(env.AI_ROUTER_ADMIN_TOKEN)})`,
    },
    sessionId,
  );
  await call("Page.reload", {}, sessionId);
  await new Promise((resolveWait) => setTimeout(resolveWait, 3000));
  const { data } = await call("Page.captureScreenshot", { format: "png" }, sessionId);
  const output = join(outputDir, name);
  writeFileSync(output, Buffer.from(data, "base64"));
  console.log(output);
}

try {
  await capture("dashboard-desktop.png", 1440, 1000, false);
  await capture("dashboard-mobile.png", 390, 844, true);
} finally {
  socket.close();
  browser.kill("SIGTERM");
  rmSync(profile, { recursive: true, force: true });
}
