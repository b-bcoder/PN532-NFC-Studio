const tabs = [...document.querySelectorAll(".tab")];
const panels = {
  read: document.getElementById("read-panel"),
  write: document.getElementById("write-panel"),
  delete: document.getElementById("delete-panel"),
};
const connection = document.getElementById("connection");
const status = document.getElementById("status");
const toast = document.getElementById("toast");
const writeContent = document.getElementById("write-content");
const byteCount = document.getElementById("byte-count");
const readUrlActions = document.getElementById("read-url-actions");

let currentTab = "read";
let currentOperation = null;
let pendingConfirmation = false;
let completionMessage = "";
let completionColor = "#54d69a";
let completionError = null;
let toastTimer;
let contentKind = "url";

function setConnection(label, color) {
  connection.lastChild.textContent = ` ${label}`;
  connection.style.color = color;
}

function showError(message) {
  toast.textContent = message;
  toast.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.add("hidden"), 8000);
}

function clearReadUrlActions() {
  readUrlActions.replaceChildren();
  readUrlActions.classList.add("hidden");
}

function showReadUrlActions(urls) {
  clearReadUrlActions();
  const validUrls = new Map();
  for (const value of urls) {
    try {
      const url = new URL(value);
      if (["http:", "https:"].includes(url.protocol) && url.hostname) {
        validUrls.set(url.href, url);
      }
    } catch {
      continue;
    }
  }

  for (const url of validUrls.values()) {
    const button = document.createElement("button");
    button.className = "action primary";
    button.textContent = `Open website: ${url.hostname}`;
    button.title = url.href;
    button.addEventListener("click", async () => {
      try {
        await window.pn532.openUrl(url.href);
      } catch (error) {
        showError(`De URL kon niet worden geopend: ${error.message}`);
      }
    });
    readUrlActions.append(button);
  }

  if (validUrls.size > 0) readUrlActions.classList.remove("hidden");
}

function setBusy(busy) {
  document.querySelectorAll(".tab, .choice, #write-content").forEach((item) => {
    item.disabled = busy;
  });
  document.querySelectorAll(".action").forEach((item) => { item.disabled = busy; });
  if (busy) {
    setConnection("Verbinden met PN532", "#f1bd55");
  } else {
    document.querySelectorAll(".action, .tab, .choice, #write-content").forEach((item) => {
      item.disabled = false;
    });
    document.querySelectorAll('[id$="-confirm"]').forEach((item) => {
      item.classList.add("hidden");
    });
  }
}

function resetOperation(message, color = "#54d69a") {
  pendingConfirmation = false;
  currentOperation = null;
  setBusy(false);
  setConnection("Klaar", color);
  if (message) status.textContent = message;
  if (currentTab === "read") {
    document.getElementById("read-cancel").classList.add("hidden");
  } else {
    document.getElementById(`${currentTab}-cancel`).classList.add("hidden");
  }
}

function setTab(tab) {
  if (currentOperation) return;
  currentTab = tab;
  tabs.forEach((button) => button.classList.toggle("active", button.dataset.tab === tab));
  Object.entries(panels).forEach(([key, panel]) => {
    panel.classList.toggle("hidden", key !== tab);
  });
}

function getNdefLength(value, kind) {
  const bytes = new TextEncoder().encode(value);
  let payloadSize = bytes.length + 3;
  if (kind === "url") {
    const prefixes = ["https://www.", "http://www.", "https://", "http://"];
    const prefix = prefixes.find((candidate) => value.startsWith(candidate));
    const uriBytes = prefix
      ? new TextEncoder().encode(value.slice(prefix.length)).length
      : bytes.length;
    payloadSize = uriBytes + 1;
  }
  const recordSize = payloadSize + 4;
  return recordSize + 2 + (recordSize > 254 ? 3 : 1);
}

function updateByteCount() {
  const value = contentKind === "url" ? writeContent.value.trim() : writeContent.value;
  if (!value) {
    byteCount.textContent = "0 / 96 bytes";
    return;
  }
  const size = getNdefLength(value, contentKind);
  byteCount.textContent = size > 96 ? `Te lang (${size} / 96 bytes)` : `${size} / 96 bytes`;
  byteCount.style.color = size > 96 ? "#f1bd55" : "";
}

async function startOperation(operation, extra = {}) {
  if (currentOperation) return;
  currentOperation = operation;
  pendingConfirmation = false;
  completionMessage = "";
  completionColor = "#54d69a";
  completionError = null;
  setBusy(true);
  const cancel = document.getElementById(`${operation}-cancel`);
  cancel.classList.remove("hidden");
  cancel.disabled = false;
  status.textContent = "Verbinden met de PN532...";
  if (operation === "read") {
    document.getElementById("read-result").textContent = "Wachten op een NFC-tag...";
    clearReadUrlActions();
  }
  try {
    await window.pn532.start({ type: "start", operation, ...extra });
  } catch (error) {
    resetOperation();
    showError(`De NFC-backend kon niet worden gestart: ${error.message}`);
  }
}

async function cancelOperation() {
  if (!currentOperation) return;
  pendingConfirmation = false;
  setBusy(true);
  status.textContent = "Actie wordt geannuleerd...";
  try {
    await window.pn532.cancel();
  } catch (error) {
    showError(`Annuleren is mislukt: ${error.message}`);
  }
}

async function confirmOperation() {
  if (!pendingConfirmation) return;
  pendingConfirmation = false;
  setBusy(true);
  setConnection("Tag wordt bijgewerkt", "#f1bd55");
  status.textContent = "Laat de tag op de PN532 liggen.";
  try {
    await window.pn532.confirm();
  } catch (error) {
    resetOperation();
    showError(`Bevestigen is mislukt: ${error.message}`);
  }
}

function showReady(operation, data) {
  pendingConfirmation = true;
  setBusy(false);
  currentOperation = operation;
  if (operation === "write") {
    const details = document.getElementById("write-details");
    details.textContent =
      `Tag gedetecteerd · ${data.type}\nUID: ${data.uid}\n` +
      `Inhoud: ${data.content}\n` +
      `Te schrijven: ${data.size} / 96 bytes`;
    details.classList.remove("hidden");
  } else {
    const state = data.already_formatted ? "al NDEF-geformatteerd" : "lege fabrieks-tag";
    const details = document.getElementById("delete-details");
    details.textContent =
      `Tag gedetecteerd · ${data.type}\nUID: ${data.uid}  ·  Status: ${state}`;
    details.classList.remove("hidden");
  }
  document.getElementById(`${operation}-start`).disabled = true;
  document.getElementById(`${operation}-confirm`).classList.remove("hidden");
  document.getElementById(`${operation}-cancel`).classList.remove("hidden");
  setConnection("Tag gedetecteerd", "#54d69a");
  status.textContent = "Controleer de taggegevens. Bevestig alleen als dit de juiste tag is.";
}

function handleBackendEvent(message) {
  switch (message.event) {
    case "status":
      status.textContent = message.message;
      if (message.message.startsWith("Leg ")) setConnection("PN532 verbonden", "#54d69a");
      break;
    case "read-result":
      document.getElementById("read-result").textContent = message.content;
      showReadUrlActions(Array.isArray(message.urls) ? message.urls : []);
      completionMessage = "Tag gelezen.";
      break;
    case "write-ready":
      showReady("write", message);
      break;
    case "delete-ready":
      showReady("delete", message);
      break;
    case "complete":
      completionMessage = message.message;
      break;
    case "cancelled":
      completionMessage = message.message;
      completionColor = "#a5b3c7";
      break;
    case "error":
      completionMessage = message.message;
      completionColor = "#f17a83";
      completionError = message.message;
      break;
    case "finished":
      resetOperation(completionMessage, completionColor);
      if (completionError) showError(completionError);
      break;
  }
}

tabs.forEach((button) => {
  button.addEventListener("click", () => setTab(button.dataset.tab));
});

document.getElementById("read-start").addEventListener("click", () => startOperation("read"));
document.getElementById("write-start").addEventListener("click", () => {
  const content = writeContent.value;
  if (!content.trim()) {
    showError("Vul eerst tekst of een URL in.");
    return;
  }
  if (contentKind === "url" && !/^https?:\/\/.+/i.test(content.trim())) {
    showError("Voer een geldige URL in die begint met http:// of https://.");
    return;
  }
  const size = getNdefLength(contentKind === "url" ? content.trim() : content, contentKind);
  if (size > 96) {
    showError(`De invoer vraagt ${size} bytes; deze tag heeft maximaal 96 bytes.`);
    return;
  }
  document.getElementById("write-details").classList.add("hidden");
  startOperation("write", { content, kind: contentKind });
});
document.getElementById("delete-start").addEventListener("click", () => {
  document.getElementById("delete-details").classList.add("hidden");
  startOperation("delete");
});
document.getElementById("write-confirm").addEventListener("click", confirmOperation);
document.getElementById("delete-confirm").addEventListener("click", confirmOperation);
document.getElementById("write-cancel").addEventListener("click", cancelOperation);
document.getElementById("delete-cancel").addEventListener("click", cancelOperation);
document.getElementById("read-cancel").addEventListener("click", cancelOperation);

document.querySelectorAll(".choice").forEach((button) => {
  button.addEventListener("click", () => {
    contentKind = button.dataset.kind;
    document.querySelectorAll(".choice").forEach((choice) => {
      choice.classList.toggle("selected", choice === button);
    });
    writeContent.placeholder = contentKind === "url"
      ? "https://voorbeeld.nl"
      : "Typ hier je bericht...";
    updateByteCount();
  });
});
writeContent.addEventListener("input", updateByteCount);
window.pn532.onEvent(handleBackendEvent);
