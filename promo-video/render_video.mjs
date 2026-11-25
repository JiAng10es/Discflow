import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import WebSocket from "ws";
import ffmpegPath from "ffmpeg-static";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CHROME_PATH = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const FPS = 60;
const TOTAL_FRAMES = 2400; // 40.0s * 60fps
const WIDTH = 1920;
const HEIGHT = 1080;

const KEYFRAME_SECONDS = [2.5, 8.5, 14.5, 17.5, 22.8, 30.0, 35.0, 38.2];
const MIME_TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".mjs": "application/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".svg": "image/svg+xml",
  ".json": "application/json; charset=utf-8",
};

function startStaticServer(rootDir) {
  return new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      const reqUrl = new URL(req.url, "http://127.0.0.1");
      let relPath = decodeURIComponent(reqUrl.pathname);
      if (relPath === "/") relPath = "/promo.html";
      const filePath = path.normalize(path.join(rootDir, relPath));
      if (!filePath.startsWith(path.normalize(rootDir))) {
        res.writeHead(403);
        res.end("Forbidden");
        return;
      }
      fs.readFile(filePath, (err, buf) => {
        if (err) {
          res.writeHead(404);
          res.end("Not found");
          return;
        }
        const ext = path.extname(filePath).toLowerCase();
        res.writeHead(200, { "Content-Type": MIME_TYPES[ext] || "application/octet-stream" });
        res.end(buf);
      });
    });
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const addr = server.address();
      resolve({ server, port: addr.port });
    });
  });
}

function launchChrome(userDataDir) {
  return new Promise((resolve, reject) => {
    const args = [
      "--headless=new",
      "--remote-debugging-port=0",
      `--user-data-dir=${userDataDir}`,
      `--window-size=${WIDTH},${HEIGHT}`,
      "--force-device-scale-factor=1",
      "--hide-scrollbars",
      "--disable-gpu-vsync",
      "--run-all-compositor-stages-before-draw",
      "--disable-background-timer-throttling",
      "--disable-renderer-backgrounding",
      "--no-first-run",
      "--no-default-browser-check",
      "--autoplay-policy=no-user-gesture-required",
      "about:blank",
    ];
    const proc = spawn(CHROME_PATH, args, { stdio: ["ignore", "pipe", "pipe"] });
    let stderrBuf = "";
    const timer = setTimeout(() => {
      reject(new Error(`Chrome launch timeout. stderr:\n${stderrBuf}`));
    }, 15000);

    proc.stderr.on("data", (chunk) => {
      stderrBuf += chunk.toString("utf8");
      const m = stderrBuf.match(/DevTools listening on (ws:\/\/[^\s]+)/);
      if (m) {
        clearTimeout(timer);
        const wsUrl = m[1];
        const port = new URL(wsUrl).port;
        resolve({ proc, wsUrl, port });
      }
    });

    proc.on("exit", (code) => {
      clearTimeout(timer);
      reject(new Error(`Chrome exited prematurely (${code}). stderr:\n${stderrBuf}`));
    });
  });
}

class CDPClient {
  constructor(wsUrl) {
    this.ws = new WebSocket(wsUrl, { maxPayload: 64 * 1024 * 1024 });
    this.nextId = 1;
    this.pending = new Map();
    this.listeners = new Map();
  }

  connect() {
    return new Promise((resolve, reject) => {
      this.ws.once("open", resolve);
      this.ws.once("error", reject);
      this.ws.on("message", (raw) => {
        const msg = JSON.parse(raw.toString("utf8"));
        if (msg.id !== undefined && this.pending.has(msg.id)) {
          const { resolve: res, reject: rej } = this.pending.get(msg.id);
          this.pending.delete(msg.id);
          if (msg.error) rej(new Error(`CDP ${msg.error.code}: ${msg.error.message}`));
          else res(msg.result);
        } else if (msg.method && this.listeners.has(msg.method)) {
          for (const cb of this.listeners.get(msg.method)) cb(msg.params);
        }
      });
    });
  }

  send(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = this.nextId++;
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  onceEvent(method) {
    return new Promise((resolve) => {
      const arr = this.listeners.get(method) || [];
      const wrapper = (params) => {
        const cur = this.listeners.get(method) || [];
        this.listeners.set(method, cur.filter((f) => f !== wrapper));
        resolve(params);
      };
      arr.push(wrapper);
      this.listeners.set(method, arr);
    });
  }

  close() {
    try {
      this.ws.close();
    } catch {}
  }
}

async function getPageWsUrl(debugPort) {
  const res = await fetch(`http://127.0.0.1:${debugPort}/json/list`);
  const list = await res.json();
  const page = list.find((t) => t.type === "page") || list[0];
  if (!page || !page.webSocketDebuggerUrl) {
    throw new Error("No debuggable Chrome page target found");
  }
  return page.webSocketDebuggerUrl;
}

async function renderSingleFrame(cdp, frameIndex) {
  await cdp.send("Runtime.evaluate", {
    expression: `new Promise((resolve) => {
      window.__renderFrame(${frameIndex}, ${FPS});
      requestAnimationFrame(() => requestAnimationFrame(resolve));
    })`,
    awaitPromise: true,
  });
  const { data } = await cdp.send("Page.captureScreenshot", {
    format: "png",
    fromSurface: true,
  });
  return Buffer.from(data, "base64");
}

function writePipe(stream, buf) {
  return new Promise((resolve, reject) => {
    const ok = stream.write(buf, (err) => {
      if (err) reject(err);
    });
    if (ok) resolve();
    else stream.once("drain", resolve);
  });
}

async function main() {
  const keyframesOnly = process.argv.includes("--keyframes-only");
  const outDir = path.join(__dirname, "out");
  const kfDir = path.join(outDir, "keyframes");
  const bgmPath = path.join(outDir, "discflow_promo_bgm.wav");
  const mp4Path = path.join(outDir, "Discflow_Promo_v2.4.1_1080p60.mp4");

  if (!fs.existsSync(CHROME_PATH)) {
    throw new Error(`Chrome not found at ${CHROME_PATH}`);
  }
  if (!keyframesOnly && !fs.existsSync(bgmPath)) {
    throw new Error(`Audio master missing: ${bgmPath}`);
  }

  fs.mkdirSync(outDir, { recursive: true });
  if (keyframesOnly) fs.mkdirSync(kfDir, { recursive: true });

  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), "discflow-cdp-"));
  let server = null;
  let chromeProc = null;
  let cdp = null;
  let ffmpegProc = null;

  try {
    const srv = await startStaticServer(__dirname);
    server = srv.server;
    const pageUrl = `http://127.0.0.1:${srv.port}/promo.html?capture=1`;
    console.log(`[server] Listening on ${pageUrl}`);

    const chrome = await launchChrome(userDataDir);
    chromeProc = chrome.proc;
    const pageWsUrl = await getPageWsUrl(chrome.port);

    cdp = new CDPClient(pageWsUrl);
    await cdp.connect();
    await cdp.send("Page.enable");
    await cdp.send("Runtime.enable");
    await cdp.send("Emulation.setDeviceMetricsOverride", {
      width: WIDTH,
      height: HEIGHT,
      deviceScaleFactor: 1,
      mobile: false,
    });

    const loadDone = cdp.onceEvent("Page.loadEventFired");
    await cdp.send("Page.navigate", { url: pageUrl });
    await loadDone;

    // 等待全部本地图片与字体完全就绪
    await cdp.send("Runtime.evaluate", {
      expression: `Promise.all([
        document.fonts.ready,
        ...Array.from(document.images).map(img => img.complete ? Promise.resolve() : new Promise(r => { img.onload = r; img.onerror = r; }))
      ])`,
      awaitPromise: true,
    });

    if (keyframesOnly) {
      console.log(`[keyframes] Capturing ${KEYFRAME_SECONDS.length} keyframes...`);
      for (let i = 0; i < KEYFRAME_SECONDS.length; i++) {
        const sec = KEYFRAME_SECONDS[i];
        const frame = Math.round(sec * FPS);
        const png = await renderSingleFrame(cdp, frame);
        const fname = `kf_${String(i + 1).padStart(2, "0")}_${sec.toFixed(1).padStart(4, "0")}s.png`;
        const fpath = path.join(kfDir, fname);
        fs.writeFileSync(fpath, png);
        console.log(`  [${i + 1}/${KEYFRAME_SECONDS.length}] ${fname} (frame ${frame}, ${png.length} bytes)`);
      }
      console.log(`[keyframes] Saved to ${kfDir}`);
    } else {
      console.log(`[render] Spawning ffmpeg -> ${mp4Path}`);
      const ffArgs = [
        "-y",
        "-f", "image2pipe",
        "-vcodec", "png",
        "-r", String(FPS),
        "-i", "pipe:0",
        "-i", bgmPath,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "libx264",
        "-profile:v", "high",
        "-pix_fmt", "yuv420p",
        "-crf", "16",
        "-preset", "slow",
        "-c:a", "aac",
        "-b:a", "320k",
        "-ar", "48000",
        "-movflags", "+faststart",
        "-shortest",
        mp4Path,
      ];
      ffmpegProc = spawn(ffmpegPath, ffArgs, { stdio: ["pipe", "ignore", "pipe"] });
      let ffErr = "";
      ffmpegProc.stderr.on("data", (d) => {
        ffErr += d.toString("utf8");
      });

      const ffExit = new Promise((resolve, reject) => {
        ffmpegProc.on("close", (code) => {
          if (code === 0) resolve();
          else reject(new Error(`ffmpeg exited with code ${code}:\n${ffErr.slice(-2000)}`));
        });
        ffmpegProc.on("error", reject);
      });

      const t0 = Date.now();
      for (let frame = 0; frame < TOTAL_FRAMES; frame++) {
        const png = await renderSingleFrame(cdp, frame);
        await writePipe(ffmpegProc.stdin, png);
        if ((frame + 1) % 120 === 0 || frame === TOTAL_FRAMES - 1) {
          const elapsed = ((Date.now() - t0) / 1000).toFixed(1);
          const pct = (((frame + 1) / TOTAL_FRAMES) * 100).toFixed(1);
          console.log(`[render] ${frame + 1}/${TOTAL_FRAMES} (${pct}%) elapsed=${elapsed}s`);
        }
      }
      ffmpegProc.stdin.end();
      await ffExit;
      const stat = fs.statSync(mp4Path);
      console.log(`[render] Complete! ${mp4Path} (${stat.size} bytes)`);
    }
  } finally {
    if (cdp) {
      try {
        await Promise.race([
          cdp.send("Browser.close"),
          new Promise((r) => setTimeout(r, 1000)),
        ]);
      } catch {}
      cdp.close();
    }
    if (chromeProc) {
      if (!chromeProc.killed) {
        try {
          chromeProc.kill("SIGKILL");
        } catch {}
      }
      await new Promise((r) => {
        if (chromeProc.exitCode !== null || chromeProc.signalCode !== null) return r();
        chromeProc.once("exit", r);
        setTimeout(r, 1500);
      });
    }
    if (server) {
      await new Promise((r) => server.close(() => r()));
    }
    try {
      fs.rmSync(userDataDir, { recursive: true, force: true, maxRetries: 5, retryDelay: 120 });
    } catch {}
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
