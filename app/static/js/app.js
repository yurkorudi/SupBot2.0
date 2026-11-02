"use strict";

const root = document.documentElement;
try {
  root.dataset.theme = localStorage.getItem("serverwatch-theme") || "dark";
} catch (_) { /* Storage may be disabled by the browser. */ }

document.getElementById("theme-toggle")?.addEventListener("click", () => {
  root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
  try { localStorage.setItem("serverwatch-theme", root.dataset.theme); } catch (_) {}
});

document.getElementById("copy-code")?.addEventListener("click", async (event) => {
  try {
    await navigator.clipboard.writeText(document.getElementById("authorization-code").textContent);
    event.target.textContent = "Скопійовано ✓";
  } catch (_) {
    event.target.textContent = "Виділіть і скопіюйте код вручну";
  }
});
