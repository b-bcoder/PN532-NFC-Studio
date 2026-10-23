const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("pn532", {
  start: (message) => ipcRenderer.invoke("backend:start", message),
  confirm: () => ipcRenderer.invoke("backend:confirm"),
  cancel: () => ipcRenderer.invoke("backend:cancel"),
  openUrl: (url) => ipcRenderer.invoke("open-url", url),
  onEvent: (callback) => {
    const listener = (_event, message) => callback(message);
    ipcRenderer.on("backend:event", listener);
    return () => ipcRenderer.removeListener("backend:event", listener);
  },
});
