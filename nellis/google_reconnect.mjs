/**
 * Reconnect a signed-out Nellis session through Google's own sign-in form.
 * NELLIS_USERNAME and NELLIS_PASSWORD are the Google account. They are read
 * from the environment and never written to stdout, stderr, or error text.
 */
import fs from "node:fs";
import { spawn } from "node:child_process";

const START =
  "https://www.nellisauction.com/oauth/google?intent=login&redirect=%2Fdashboard%2Fauctions%2Fwatchlist";
const username = process.env.NELLIS_USERNAME || "";
const password = process.env.NELLIS_PASSWORD || "";
const profile = process.argv[2];
const cookieOut = process.argv[3];
let chrome;

function finish(payload, code) {
  if (chrome) {
    try {
      chrome.kill("SIGKILL");
    } catch {
      /* already exited */
    }
  }
  process.stdout.write(`${JSON.stringify(payload)}\n`);
  process.exit(code);
}

if (!username || !password || !profile || !cookieOut) {
  finish(
    {
      ok: false,
      error:
        "Nellis is signed out and cannot reconnect. NELLIS_USERNAME and NELLIS_PASSWORD are not set in the environment. Those are the Google account for Nellis sign-in, not a Nellis username and password.",
    },
    3,
  );
}

const chromeBin = process.env.CHROME_BIN || "google-chrome";
chrome = spawn(
  chromeBin,
  [
    "--headless=new",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    `--user-data-dir=${profile}`,
    "--remote-debugging-port=0",
    "--remote-allow-origins=*",
    START,
  ],
  { stdio: "ignore" },
);

let ws;
const pending = new Map();
let seq = 0;

function send(method, params = {}) {
  const id = ++seq;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
}

async function portOf(dir) {
  const file = `${dir}/DevToolsActivePort`;
  for (let i = 0; i < 50; i += 1) {
    if (fs.existsSync(file)) {
      const port = fs.readFileSync(file, "utf8").split("\n")[0].trim();
      if (port) return port;
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error("Chrome debugging port did not open");
}

async function pageSocket(port) {
  for (let i = 0; i < 25; i += 1) {
    const response = await fetch(`http://127.0.0.1:${port}/json/list`);
    const targets = await response.json();
    const page = targets.find((target) => target.type === "page" && target.webSocketDebuggerUrl);
    if (page) return page.webSocketDebuggerUrl;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error("Chrome did not open a page");
}

function stateExpression() {
  return `(() => {
    const url = location.href;
    const text = (document.body && document.body.innerText || "").slice(0, 2500);
    if (url.includes("nellisauction.com") && !url.includes("accounts.google.com")) return "nellis";
    if (/verify it.?s you|2-step verification|captcha|couldn.?t sign you in|could not sign you in|try again later/i.test(text)) return "challenge";
    if (document.querySelector('input[type="password"]')) return "password";
    if (document.querySelector('input[type="email"], input[name="identifier"]')) return "email";
    return "unknown";
  })()`;
}

async function pageState() {
  const result = await send("Runtime.evaluate", {
    expression: stateExpression(),
    returnByValue: true,
  });
  return result.result?.value || "unknown";
}

async function focus(selector) {
  const result = await send("Runtime.evaluate", {
    expression: `(() => { const el = document.querySelector(${JSON.stringify(selector)}); if (!el) return false; el.focus(); return true; })()`,
    returnByValue: true,
  });
  return result.result?.value === true;
}

async function click(selector) {
  const result = await send("Runtime.evaluate", {
    expression: `(() => { const el = document.querySelector(${JSON.stringify(selector)}); if (!el) return false; el.click(); return true; })()`,
    returnByValue: true,
  });
  return result.result?.value === true;
}

async function waitFor(wanted, timeoutMs) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const state = await pageState();
    if (state === "challenge") return "challenge";
    if (wanted.includes(state)) return state;
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  return "timeout";
}

async function run() {
  const port = await portOf(profile);
  ws = new WebSocket(await pageSocket(port));
  await new Promise((resolve, reject) => {
    ws.addEventListener("open", resolve);
    ws.addEventListener("error", () => reject(new Error("Could not attach to Chrome")));
  });
  ws.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (!message.id || !pending.has(message.id)) return;
    const waiter = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) waiter.reject(new Error("The browser command failed"));
    else waiter.resolve(message.result || {});
  });
  await send("Page.enable");
  await send("Runtime.enable");
  await send("Network.enable");

  let state = await waitFor(["email", "password", "nellis"], 30000);
  if (state === "challenge") {
    finish({ ok: false, error: "Google asked for an extra check. Reconnect stopped without bypassing it." }, 2);
  }
  if (state === "email") {
    if (!(await focus('input[type="email"], input[name="identifier"]'))) {
      finish({ ok: false, error: "Google's email field was not available." }, 2);
    }
    await send("Input.insertText", { text: username });
    await click("#identifierNext button, #identifierNext");
    state = await waitFor(["password", "nellis"], 30000);
  }
  if (state === "challenge") {
    finish({ ok: false, error: "Google asked for an extra check. Reconnect stopped without bypassing it." }, 2);
  }
  if (state === "password") {
    if (!(await focus('input[type="password"]'))) {
      finish({ ok: false, error: "Google's password field was not available." }, 2);
    }
    await send("Input.insertText", { text: password });
    await click("#passwordNext button, #passwordNext");
    state = await waitFor(["nellis"], 40000);
  }
  if (state === "challenge") {
    finish({ ok: false, error: "Google asked for an extra check. Reconnect stopped without bypassing it." }, 2);
  }
  if (state !== "nellis") {
    finish({ ok: false, error: "Nellis Google sign-in did not return to the watch list." }, 2);
  }
  const cookies = await send("Network.getCookies", {
    urls: ["https://www.nellisauction.com/"],
  });
  const session = (cookies.cookies || []).find((cookie) => cookie.name === "__session");
  if (!session || !session.value) {
    finish({ ok: false, error: "Nellis did not start a signed-in session after Google sign-in." }, 2);
  }
  fs.writeFileSync(cookieOut, session.value, { mode: 0o600 });
  finish({ ok: true }, 0);
}

run()
  .catch((error) => {
    finish({ ok: false, error: error.message || "Google reconnect failed" }, 1);
  })
  .finally(() => {
    chrome.kill("SIGKILL");
  });
