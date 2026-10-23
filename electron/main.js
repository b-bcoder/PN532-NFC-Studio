const { app, BrowserWindow, Menu, ipcMain, shell } = require("electron");
const { spawn } = require("node:child_process");
const path = require("node:path");
const readline = require("node:readline");

let mainWindow;
let backend;

function sendEvent(message) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send("backend:event", message);
  }
}

function startBackend() {
  if (backend && backend.exitCode === null) return;

  let executable;
  let args;
  let cwd;
  if (app.isPackaged) {
    executable = path.join(
      process.resourcesPath,
      "backend",
      "PN532_Backend.exe"
    );
    args = [];
    cwd = process.resourcesPath;
  } else {
    const projectDir = path.resolve(__dirname, "..");
    executable = process.env.PYTHON || "python";
    args = ["-u", path.join(projectDir, "PN532_Backend.py")];
    cwd = projectDir;
  }

  backend = spawn(executable, args, {
    cwd,
    stdio: ["pipe", "pipe", "pipe"],
    windowsHide: true,
  });

  const output = readline.createInterface({ input: backend.stdout });
  output.on("line", (line) => {
    try {
      sendEvent(JSON.parse(line));
    } catch {
      sendEvent({
        event: "error",
        message: "De NFC-backend stuurde een ongeldig antwoord.",
      });
    }
  });

  let stderr = "";
  backend.stderr.on("data", (chunk) => {
    stderr = (stderr + chunk.toString()).slice(-4000);
  });
  backend.on("error", (error) => {
    sendEvent({
      event: "error",
      message: `De NFC-backend kon niet starten: ${error.message}`,
    });
    sendEvent({ event: "finished" });
  });
  backend.on("exit", (code) => {
    backend = undefined;
    if (code !== 0 && code !== null) {
      sendEvent({
        event: "error",
        message: `De NFC-backend is gestopt (${code}). ${stderr.trim()}`,
      });
      sendEvent({ event: "finished" });
    }
  });
}

function sendBackendMessage(message) {
  if (!backend || backend.exitCode !== null || !backend.stdin.writable) {
    startBackend();
  }
  if (!backend || !backend.stdin.writable) {
    throw new Error("De NFC-backend is niet beschikbaar.");
  }
  backend.stdin.write(`${JSON.stringify(message)}\n`);
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1000,
    height: 800,
    minWidth: 760,
    minHeight: 650,
    backgroundColor: "#0b1220",
    title: "PN532 NFC Studio",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  mainWindow.webContents.on("before-input-event", (event, input) => {
    if (input.type === "keyDown" && input.key === "F11") {
      event.preventDefault();
      mainWindow.setFullScreen(!mainWindow.isFullScreen());
    }
  });
  mainWindow.loadFile(path.join(__dirname, "renderer", "index.html"));
  mainWindow.on("closed", () => {
    mainWindow = undefined;
  });
}

ipcMain.handle("backend:start", (_event, message) => {
  sendBackendMessage(message);
});
ipcMain.handle("backend:confirm", () => sendBackendMessage({ type: "confirm" }));
ipcMain.handle("backend:cancel", () => sendBackendMessage({ type: "cancel" }));
ipcMain.handle("open-url", async (_event, value) => {
  if (typeof value !== "string") {
    throw new Error("De URL is ongeldig.");
  }
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new Error("De URL is ongeldig.");
  }
  if (!["http:", "https:"].includes(url.protocol) || !url.hostname) {
    throw new Error("Alleen geldige HTTP- en HTTPS-URL's kunnen worden geopend.");
  }
  await shell.openExternal(url.href);
});

app.whenReady().then(() => {
  Menu.setApplicationMenu(null);
  createWindow();
});
app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
app.on("before-quit", () => {
  if (backend && backend.exitCode === null) backend.kill();
});
app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
