const DEVICE_DATA = [
  { name: "Air conditioner", icon: "❄", share: 0.36 },
  { name: "Water heater", icon: "♨", share: 0.22 },
  { name: "Refrigerator", icon: "▣", share: 0.14 },
  { name: "Washing machine", icon: "◉", share: 0.12 },
  { name: "Other devices", icon: "⌁", share: 0.16 },
];

const elements = {
  carbonChange: document.querySelector("#carbon-change"),
  carbonValue: document.querySelector("#carbon-value"),
  chart: document.querySelector("#usage-chart"),
  chartLabels: document.querySelector("#chart-labels"),
  chartNote: document.querySelector("#chart-note-text"),
  chartTotal: document.querySelector("#chart-total"),
  costChange: document.querySelector("#cost-change"),
  costValue: document.querySelector("#cost-value"),
  energyChange: document.querySelector("#energy-change"),
  energyValue: document.querySelector("#energy-value"),
  error: document.querySelector("#load-error"),
  periodSelect: document.querySelector("#period-select"),
  solarValue: document.querySelector("#solar-value"),
  todayLabel: document.querySelector("#today-label"),
  tooltip: document.querySelector("#chart-tooltip"),
  yAxisLabels: document.querySelector(".y-axis-labels"),
  deviceList: document.querySelector("#device-list"),
};

let dailyReadings = [];
let chartBars = [];

function sum(readings, key) {
  return readings.reduce((total, reading) => total + reading[key], 0);
}

function formatNumber(number, maximumFractionDigits = 1) {
  return new Intl.NumberFormat("en-IN", { maximumFractionDigits }).format(number);
}

function setChange(element, current, previous, suffix = "%") {
  if (previous === null) {
    element.textContent = "No baseline";
    element.classList.remove("down", "up");
    return;
  }
  const percentage = previous === 0 ? 0 : ((current - previous) / previous) * 100;
  const isDown = percentage <= 0;
  element.textContent = `${isDown ? "↓" : "↑"} ${Math.abs(percentage).toFixed(1)}${suffix}`;
  element.classList.toggle("down", isDown);
  element.classList.toggle("up", !isDown);
}

function renderMetrics() {
  if (!dailyReadings.length) {
    elements.energyValue.textContent = "—";
    elements.costValue.textContent = "—";
    elements.solarValue.textContent = "—";
    elements.carbonValue.textContent = "—";
    elements.chartTotal.textContent = "—";
    elements.energyChange.textContent = "No baseline";
    elements.costChange.textContent = "No baseline";
    elements.carbonChange.textContent = "No baseline";
    elements.chartNote.textContent = "Your energy use is being analyzed.";
    elements.yAxisLabels.innerHTML = "<span>0</span><span>0</span><span>0</span><span>0</span>";
    elements.chartLabels.innerHTML = "";
    chartBars = [];
    return;
  }

  const period = Number(elements.periodSelect.value);
  const readings = dailyReadings.slice(-period);
  const previous = dailyReadings.slice(-period * 2, -period);
  const usage = sum(readings, "usage");
  const cost = sum(readings, "cost");
  const solar = sum(readings, "solar");
  const carbon = sum(readings, "carbon");

  elements.energyValue.textContent = formatNumber(usage);
  elements.costValue.textContent = formatNumber(cost, 0);
  elements.solarValue.textContent = formatNumber(solar);
  elements.carbonValue.textContent = formatNumber(carbon);
  elements.chartTotal.textContent = formatNumber(usage);
  setChange(elements.energyChange, usage, previous.length ? sum(previous, "usage") : null);
  setChange(elements.costChange, cost, previous.length ? sum(previous, "cost") : null);
  setChange(elements.carbonChange, carbon, previous.length ? sum(previous, "carbon") : null);
  renderChart(readings);
  renderDevices(readings.at(-1)?.usage ?? 0);
}

function renderDevices(totalUsage) {
  elements.deviceList.innerHTML = DEVICE_DATA.map((device) => {
    const share = device.share * 100;
    const estimatedUsage = totalUsage * device.share;
    return `
      <div class="device-row">
        <span class="device-icon" aria-hidden="true">${device.icon}</span>
        <div class="device-info">
          <div class="device-name-line">
            <span class="device-name">${device.name}</span>
            <span class="device-usage">${formatNumber(estimatedUsage)} kWh</span>
          </div>
          <div class="device-track"><div class="device-bar" style="width: ${share.toFixed(1)}%"></div></div>
        </div>
        <span class="device-share">${share.toFixed(0)}%</span>
      </div>`;
  }).join("");

  elements.chartNote.textContent = totalUsage > 0
    ? "Illustrative device estimates based on today's total—not individual appliance meter readings."
    : "Appliance-level meter readings are not available for this account yet.";
}

function renderChart(readings) {
  if (!readings.length) {
    elements.yAxisLabels.innerHTML = "<span>0</span><span>0</span><span>0</span><span>0</span>";
    elements.chartLabels.innerHTML = "";
    elements.tooltip.style.display = "none";
    chartBars = [];
    return;
  }

  const canvas = elements.chart;
  const context = canvas.getContext("2d");
  const bounds = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  const width = bounds.width;
  const height = bounds.height;
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);

  const left = 2;
  const right = 4;
  const top = 7;
  const bottom = height - 5;
  const plotHeight = bottom - top;
  const maxValue = Math.max(10, Math.ceil(Math.max(...readings.map((item) => item.usage), 0) / 10) * 10);
  elements.yAxisLabels.innerHTML = [maxValue, (maxValue * 2) / 3, maxValue / 3, 0]
    .map((value) => `<span>${formatNumber(value, 0)}</span>`)
    .join("");
  const groupWidth = (width - left - right) / readings.length;
  const barWidth = Math.max(3, Math.min(15, groupWidth * 0.48));
  chartBars = [];

  for (let line = 0; line <= 3; line += 1) {
    const y = top + (plotHeight / 3) * line;
    context.beginPath();
    context.setLineDash([3, 5]);
    context.strokeStyle = "#edf0ed";
    context.lineWidth = 1;
    context.moveTo(left, y);
    context.lineTo(width - right, y);
    context.stroke();
  }
  context.setLineDash([]);

  readings.forEach((reading, index) => {
    const x = left + index * groupWidth + (groupWidth - barWidth) / 2;
    const barHeight = (reading.usage / maxValue) * plotHeight;
    const y = bottom - barHeight;
    context.fillStyle = index === readings.length - 1 ? "#315e49" : "#79a287";
    context.beginPath();
    context.roundRect(x, y, barWidth, barHeight, [3, 3, 0, 0]);
    context.fill();
    chartBars.push({ x, y, width: barWidth, height: barHeight, reading });
  });

  const labelIndexes = new Set([0, readings.length - 1]);
  const labelCount = Number(elements.periodSelect.value) === 7 ? 7 : 6;
  for (let label = 1; label < labelCount - 1; label += 1) {
    labelIndexes.add(Math.round((label * (readings.length - 1)) / (labelCount - 1)));
  }
  elements.chartLabels.innerHTML = [...labelIndexes]
    .filter((index) => Number.isInteger(index) && index >= 0 && index < readings.length)
    .sort((leftIndex, rightIndex) => leftIndex - rightIndex)
    .map((index) => `<span>${readings[index].date.toLocaleDateString("en-IN", { day: "numeric", month: "short" })}</span>`)
    .join("");
}

elements.chart.addEventListener("pointermove", (event) => {
  const bounds = elements.chart.getBoundingClientRect();
  const x = event.clientX - bounds.left;
  const bar = chartBars.find((item) => x >= item.x && x <= item.x + item.width);
  if (!bar) {
    elements.tooltip.style.display = "none";
    return;
  }
  elements.tooltip.textContent = `${bar.reading.date.toLocaleDateString("en-IN", { day: "numeric", month: "short" })}: ${formatNumber(bar.reading.usage)} kWh`;
  elements.tooltip.style.display = "block";
  elements.tooltip.style.left = `${Math.max(0, Math.min(bar.x, bounds.width - 105))}px`;
  elements.tooltip.style.top = `${Math.max(0, bar.y - 28)}px`;
});

elements.chart.addEventListener("pointerleave", () => {
  elements.tooltip.style.display = "none";
});

elements.periodSelect.addEventListener("change", renderMetrics);
window.addEventListener("resize", renderMetrics);

async function loadDashboard() {
  try {
    const user = await window.wattwiseAuth.currentUser();
    if (user.role !== "customer") {
      window.location.assign("/admin");
      return;
    }
    document.querySelector("#customer-name").textContent = user.name;
    document.querySelector("#customer-first-name").textContent = user.name.split(/\s+/)[0];
    const initials = user.name
      .split(/\s+/)
      .slice(0, 2)
      .map((part) => part[0])
      .join("")
      .toUpperCase();
    document.querySelector("#customer-avatar").textContent = initials;
    document.querySelector("#customer-top-avatar").textContent = initials;

    const result = await window.wattwiseAuth.request("/api/energy");
    dailyReadings = result.readings.map((reading) => ({
      ...reading,
      date: new Date(`${reading.date}T00:00:00`),
    }));
    if (dailyReadings.length < 2) {
      throw new Error("At least two days of energy data are required to show comparisons.");
    }

    const latestDate = dailyReadings.at(-1).date;
    elements.todayLabel.textContent = latestDate.toLocaleDateString("en-IN", {
      weekday: "short",
      day: "numeric",
      month: "short",
    });
    renderMetrics();
  } catch (error) {
    if (error.status === 401) {
      window.location.assign("/login");
      return;
    }
    elements.error.hidden = false;
    elements.error.textContent = error instanceof TypeError
      ? "Can't connect to Wattwise. Start run-website.bat and open http://127.0.0.1:8001."
      : error.message || "Unable to load your energy dashboard.";
    console.error("Unable to load the energy dashboard data.", error);
  }
}

loadDashboard();
