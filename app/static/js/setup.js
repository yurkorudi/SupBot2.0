"use strict";

const wizard = document.getElementById("setup-wizard");
const code = document.getElementById("installation-script");

if (code) {
  const source = code.textContent;
  const tokens = /#[^\n]*|'[^']*'|"[^"\n]*"|\b(?:if|then|else|fi|set|exit|function)\b|\$[A-Za-z_][A-Za-z0-9_]*/g;
  const fragment = document.createDocumentFragment();
  let start = 0;
  for (const match of source.matchAll(tokens)) {
    fragment.append(document.createTextNode(source.slice(start, match.index)));
    const span = document.createElement("span");
    span.className = match[0].startsWith("#") ? "bash-comment" : "bash-token";
    span.textContent = match[0];
    fragment.append(span);
    start = match.index + match[0].length;
  }
  fragment.append(document.createTextNode(source.slice(start)));
  code.replaceChildren(fragment);
  document.getElementById("copy-installation")?.addEventListener("click", async (event) => {
    try {
      await navigator.clipboard.writeText(source);
      event.target.textContent = "Скопійовано ✓";
    } catch (_) {
      event.target.textContent = "Скористайтеся завантаженням .sh";
    }
  });
  document.getElementById("download-installation")?.addEventListener("click", () => {
    const url = URL.createObjectURL(new Blob([source], { type: "text/x-shellscript;charset=utf-8" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `serverwatch-${wizard.dataset.serverId}.sh`;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
}

const messages = {
  PENDING_SETUP: "Очікуємо виконання скрипта на клієнтському сервері.",
  INSTALLING: "Скрипт встановлює публічний ключ та обмежений SSH-доступ.",
  VERIFYING: "Worker перевіряє SSH і фактичний збір метрик. Це може тривати кілька хвилин.",
  READY: "SSH і збір метрик підтверджено. Відкриваємо підключення Telegram…",
  ACTIVE: "Сервер і Telegram-група підключені.",
  INSTALLATION_FAILED: "Встановлення не завершене. Можна видати новий скрипт.",
  CONNECTION_FAILED: "SSH-перевірка не пройшла. Перевірте порт і доступність сервера.",
  DISABLED: "Сервер відключено адміністратором."
};

const labels = {
  PENDING_SETUP: "Очікує встановлення", INSTALLING: "Встановлення", VERIFYING: "Перевірка SSH",
  READY: "Готовий до Telegram", ACTIVE: "Підключено", INSTALLATION_FAILED: "Помилка встановлення",
  CONNECTION_FAILED: "SSH недоступний", DISABLED: "Вимкнено"
};
let submitting = false;
async function checkSetup() {
  if (!wizard || submitting) return;
  try {
    const response = await fetch(`/api/servers/${wizard.dataset.serverId}/setup-status`);
    if (!response.ok) throw new Error();
    const data = await response.json();
    document.getElementById("setup-message").textContent = data.error || messages[data.status] || data.status;
    const badge = document.createElement("span");
    badge.className = `badge ${data.status}`;
    badge.textContent = labels[data.status] || data.status;
    document.getElementById("setup-badge").replaceChildren(badge);
    document.getElementById("setup-retry").hidden = !["INSTALLATION_FAILED", "CONNECTION_FAILED", "DISABLED"].includes(data.status) && !data.expired;
    if (data.status === "READY") {
      submitting = true;
      document.querySelector("#setup-telegram button").disabled = false;
      document.getElementById("setup-telegram").requestSubmit();
      return;
    }
    if (data.status === "ACTIVE") {
      location.assign(`/servers/${wizard.dataset.serverId}`);
      return;
    }
  } catch (_) {
    document.getElementById("setup-message").textContent = "Не вдалося оновити стан. Перевірте з’єднання та сеанс входу.";
  }
  setTimeout(checkSetup, 4000);
}
if (wizard) checkSetup();
