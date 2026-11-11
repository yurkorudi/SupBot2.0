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

for (const form of document.querySelectorAll("form[data-confirm]")) {
  form.addEventListener("submit", (event) => {
    if (!window.confirm(form.dataset.confirm)) {
      event.preventDefault();
      return;
    }
    if (event.submitter) {
      event.submitter.disabled = true;
      event.submitter.textContent = "Виконується…";
    }
  });
}

const sections = document.getElementById("server-sections");
if (sections) {
  const links = Array.from(sections.querySelectorAll('a[href^="#"]'));
  const panels = document.querySelectorAll("[data-server-section]");
  function showSection() {
    const active = links.find((link) => link.hash === window.location.hash) || links[0];
    for (const link of links) {
      if (link === active) link.setAttribute("aria-current", "location");
      else link.removeAttribute("aria-current");
    }
    for (const panel of panels) panel.hidden = "#" + panel.dataset.serverSection !== active.hash;
  }
  window.addEventListener("hashchange", showSection);
  showSection();
}

for (const button of document.querySelectorAll(".flash-dismiss")) {
  button.addEventListener("click", () => button.closest(".notice").remove());
}
