"use strict";

const view = document.getElementById("terminal-view");
const statusLabel = document.getElementById("terminal-status");
const elapsed = document.getElementById("terminal-elapsed");
const commandTime = document.getElementById("terminal-command-time");
const connectButton = document.getElementById("terminal-connect");
const interruptButton = document.getElementById("terminal-interrupt");
const terminal = new Terminal({
  fontFamily: '"IBM Plex Mono", Consolas, monospace',
  fontSize: 13,
  cursorBlink: true,
  scrollback: 2500,
  theme: { background: "#101614", foreground: "#dcece3", cursor: "#54d4a0" },
});
const fit = new FitAddon.FitAddon();
terminal.loadAddon(fit);
terminal.open(view);
fit.fit();

let socket;
let connected = false;
let startedAt = 0;
let commandStartedAt = 0;
let requestedClose = false;
const encoder = new TextEncoder();

function duration(since) {
  const seconds = Math.floor((Date.now() - since) / 1000);
  return [Math.floor(seconds / 60), seconds % 60]
    .map((value) => String(value).padStart(2, "0")).join(":");
}

terminal.parser.registerOscHandler(133, (marker) => {
  if (marker === "C") {
    commandStartedAt = Date.now();
    commandTime.textContent = "Команда 00:00";
  } else if (marker === "D" && commandStartedAt) {
    commandTime.textContent = "Остання команда " + duration(commandStartedAt);
    commandStartedAt = 0;
  }
  return true;
});

function sendInput(input) {
  if (!connected || socket?.readyState !== WebSocket.OPEN) return;
  const bytes = encoder.encode(input);
  for (let offset = 0; offset < bytes.length; offset += 8192) {
    socket.send(bytes.subarray(offset, offset + 8192));
  }
}

function resize() {
  fit.fit();
  if (connected && socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ cols: terminal.cols, rows: terminal.rows }));
  }
}

new ResizeObserver(resize).observe(view);
terminal.onData(sendInput);
terminal.onBinary((input) => {
  if (connected && socket?.readyState === WebSocket.OPEN) {
    socket.send(Uint8Array.from(input, (character) => character.charCodeAt(0)));
  }
});
connectButton.addEventListener("click", () => {
  if (socket && socket.readyState < WebSocket.CLOSING) {
    requestedClose = true;
    connectButton.disabled = true;
    if (connected) socket.send(JSON.stringify({ close: true }));
    else socket.close();
    return;
  }
  const url = new URL(view.dataset.path, window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  socket = new WebSocket(url);
  requestedClose = false;
  socket.binaryType = "arraybuffer";
  let closeMessage = "";
  statusLabel.textContent = "Підключення SSH…";
  connectButton.textContent = "Скасувати";
  socket.addEventListener("message", (event) => {
    if (typeof event.data !== "string") {
      terminal.write(new Uint8Array(event.data));
      return;
    }
    const state = JSON.parse(event.data);
    if (state.state === "connected") {
      connected = true;
      startedAt = Date.now();
      statusLabel.textContent = "SSH підключено";
      connectButton.textContent = "Від’єднати";
      interruptButton.disabled = false;
      resize();
      terminal.focus();
    } else if (state.state === "closed") {
      closeMessage = state.message;
    }
  });
  socket.addEventListener("error", () => {
    closeMessage = "З’єднання відхилено або недоступне.";
  });
  socket.addEventListener("close", () => {
    connected = false;
    commandStartedAt = 0;
    statusLabel.textContent = requestedClose ? "Від’єднано" : closeMessage || "Від’єднано";
    connectButton.textContent = "Підключити";
    connectButton.disabled = false;
    interruptButton.disabled = true;
    terminal.writeln("");
  });
});
interruptButton.addEventListener("click", () => sendInput("\x03"));
document.getElementById("terminal-clear").addEventListener("click", () => terminal.reset());
window.addEventListener("pagehide", () => socket?.close());
window.setInterval(() => {
  if (!connected) return;
  elapsed.textContent = duration(startedAt);
  if (commandStartedAt) commandTime.textContent = "Команда " + duration(commandStartedAt);
}, 1000);
