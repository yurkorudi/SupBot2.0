"use strict";

const charts = new Map();
const palette = { cpu: "#7c8aff", ram: "#3bd5ac", swap: "#e4b760", disk: "#76b6f5" };

function draw(id, labels, datasets) {
  const canvas = document.getElementById(id);
  if (!canvas) return;
  charts.get(id)?.destroy();
  charts.set(id, new Chart(canvas, {
    type: "line",
    data: {
      labels: labels.map(value => new Date(value).toLocaleString("uk-UA", {
        month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"
      })),
      datasets: datasets.map(data => ({ ...data, pointRadius: 0, pointHitRadius: 10,
        borderWidth: 2, tension: 0.25 }))
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { labels: { color: "#8995ac", usePointStyle: true, boxWidth: 6 } } },
      scales: {
        x: { ticks: { maxTicksLimit: 6, color: "#8995ac" }, grid: { display: false } },
        y: { min: 0, max: 100, ticks: { color: "#8995ac", callback: value => value + "%" },
          grid: { color: "rgba(130,145,170,0.12)" } }
      }
    }
  }));
}

let generation = 0;
async function refreshCharts() {
  const detail = document.getElementById("metric-charts");
  const serverId = detail?.dataset.serverId || document.getElementById("dashboard-server")?.value;
  if (!serverId) return;
  const period = document.getElementById("chart-period")?.value || "24h";
  const message = document.getElementById("chart-message");
  const current = ++generation;
  const loadingPanel = detail || document.getElementById("overview-chart")?.parentElement;
  loadingPanel?.setAttribute("aria-busy", "true");
  try {
    if (typeof Chart === "undefined") throw new Error("Chart.js не завантажено");
    const response = await fetch(`/api/analytics/${encodeURIComponent(serverId)}?period=${period}`);
    if (!response.ok) throw new Error("Запит не вдався");
    const data = await response.json();
    if (current !== generation) return;
    message.textContent = data.labels.length ? "" : "За обраний період даних ще немає.";
    if (detail) {
      for (const key of Object.keys(palette)) {
        draw("chart-" + key, data.labels, [
          { label: "Середнє", data: data[key], borderColor: palette[key] },
          ...(period === "24h" ? [] : [{ label: "Максимум", data: data[key + "_max"],
            borderColor: palette[key], borderDash: [4, 4] }])
        ]);
        document.getElementById("summary-" + key).textContent =
          `середнє ${data.summary[key].avg.toFixed(1)}% · макс. ${data.summary[key].max.toFixed(1)}%`;
      }
    } else {
      draw("overview-chart", data.labels, [
        { label: "CPU", data: data.cpu, borderColor: palette.cpu },
        { label: "RAM", data: data.ram, borderColor: palette.ram }
      ]);
    }
  } catch (_) {
    if (current === generation) message.textContent = "Не вдалося завантажити графіки.";
  } finally {
    if (current === generation) loadingPanel?.removeAttribute("aria-busy");
  }
}

document.getElementById("chart-period")?.addEventListener("change", refreshCharts);
document.getElementById("dashboard-server")?.addEventListener("change", refreshCharts);
refreshCharts();
