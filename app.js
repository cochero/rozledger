const rupee = new Intl.NumberFormat("en-IN", {
  maximumFractionDigits: 0,
});

const state = {
  copiedTimer: null,
};

function byId(id) {
  return document.getElementById(id);
}

function money(value) {
  return `Rs ${rupee.format(Number.isFinite(value) ? value : 0)}`;
}

function encodeUpi(value) {
  return encodeURIComponent(value.trim()).replace(/%20/g, "+");
}

function setCopied(button, label) {
  window.clearTimeout(state.copiedTimer);
  const original = button.dataset.original || button.textContent;
  button.dataset.original = original;
  button.textContent = label;
  state.copiedTimer = window.setTimeout(() => {
    button.textContent = original;
  }, 1400);
}

function showStatus(id, message, isError = false) {
  const status = byId(id);
  status.textContent = message;
  status.style.color = isError ? "#9f2f22" : "#0a4a39";
}

function showStatusActions(id, message, actions = [], isError = false) {
  const status = byId(id);
  status.textContent = message;
  status.style.color = isError ? "#9f2f22" : "#0a4a39";

  if (!actions.length) return;

  const wrap = document.createElement("span");
  wrap.className = "status-actions";
  actions.forEach((action) => {
    const link = document.createElement("a");
    link.href = action.href;
    link.textContent = action.label;
    link.rel = "noopener";
    if (action.newTab) {
      link.target = "_blank";
    }
    wrap.appendChild(link);
  });
  status.appendChild(wrap);
}

function attributionPayload(source) {
  const params = new URLSearchParams(window.location.search);
  return {
    source,
    landing_path: `${window.location.pathname}${window.location.search}`,
    referrer: document.referrer,
    utm_source: params.get("utm_source") || "",
    utm_medium: params.get("utm_medium") || "",
    utm_campaign: params.get("utm_campaign") || "",
  };
}

function invoicePayload() {
  return {
    business_name: byId("bizName").value.trim(),
    owner_email: byId("ownerEmail").value.trim(),
    client_name: byId("clientName").value.trim(),
    service_name: byId("serviceName").value.trim(),
    amount_before_gst: Number(byId("amount").value) || 0,
    gst_rate: Number(byId("gstRate").value) || 0,
    due_days: Number(byId("dueDays").value) || 0,
    total_text: byId("totalValue").textContent,
    upi_link: byId("upiLink").value,
    invoice_text: byId("invoiceText").value,
  };
}

async function postJson(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const error = new Error(`Request failed with ${response.status}`);
    error.status = response.status;
    try {
      error.payload = await response.json();
    } catch {
      error.payload = {};
    }
    throw error;
  }

  return response.json();
}

function saveFallback(key, payload) {
  const existing = JSON.parse(localStorage.getItem(key) || "[]");
  existing.push({
    ...payload,
    saved_at: new Date().toISOString(),
  });
  localStorage.setItem(key, JSON.stringify(existing.slice(-50)));
}

async function copyText(text, button) {
  try {
    await navigator.clipboard.writeText(text);
    setCopied(button, "Copied");
  } catch {
    const input = document.createElement("textarea");
    input.value = text;
    document.body.append(input);
    input.select();
    document.execCommand("copy");
    input.remove();
    setCopied(button, "Copied");
  }
}

function updateInvoice() {
  const business = byId("bizName").value.trim() || "Your business";
  const client = byId("clientName").value.trim() || "Client";
  const service = byId("serviceName").value.trim() || "Professional service";
  const amount = Number(byId("amount").value) || 0;
  const gstRate = Number(byId("gstRate").value) || 0;
  const dueDays = Number(byId("dueDays").value) || 0;
  const gst = amount * (gstRate / 100);
  const total = amount + gst;
  const due = new Date();
  due.setDate(due.getDate() + dueDays);

  byId("gstValue").textContent = money(gst);
  byId("totalValue").textContent = money(total);
  byId("dueDate").textContent = due.toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
  byId("upiAmount").value = Math.round(total);
  byId("upiNote").value = service;

  byId("invoiceText").value = [
    `Invoice from ${business}`,
    `Bill to: ${client}`,
    `Service: ${service}`,
    `Amount: ${money(amount)}`,
    `GST (${gstRate}%): ${money(gst)}`,
    `Total payable: ${money(total)}`,
    `Due date: ${byId("dueDate").textContent}`,
    "",
    "Please share payment confirmation after transfer. Thank you.",
  ].join("\n");

  updateUpi();
  updateTarget();
}

function updateUpi() {
  const upiId = byId("upiId").value.trim();
  const payee = byId("payeeName").value.trim();
  const amount = Number(byId("upiAmount").value) || 0;
  const note = byId("upiNote").value.trim();
  const uri = `upi://pay?pa=${encodeUpi(upiId)}&pn=${encodeUpi(payee)}&am=${amount.toFixed(2)}&cu=INR&tn=${encodeUpi(note)}`;

  byId("upiLink").value = uri;
  renderQr(uri);
}

function renderQr(text) {
  const qr = byId("upiQr");
  qr.src = `https://api.qrserver.com/v1/create-qr-code/?size=220x220&data=${encodeURIComponent(text)}`;
}

function updateTarget() {
  const goal = Number(byId("monthlyGoal").value) || 0;
  const days = Math.max(Number(byId("workDays").value) || 1, 1);
  const collected = Number(byId("collected").value) || 0;
  const aov = Math.max(Number(byId("aov").value) || 1, 1);
  const daily = goal / days;
  const left = Math.max(daily - collected, 0);
  const orders = Math.ceil(left / aov);

  byId("dailyTarget").textContent = money(daily);
  byId("leftToday").textContent = money(left);
  byId("ordersNeeded").textContent = String(orders);
  byId("reminderText").value = [
    "Hi, gentle reminder for the pending payment.",
    `Amount due: ${byId("totalValue").textContent}`,
    `Payment link: ${byId("upiLink").value}`,
    "Please complete it today if possible. Thank you.",
  ].join("\n");
}

function switchTab(tabName) {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.tab === tabName);
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.panel === tabName);
  });
}

const hasTool = Boolean(byId("bizName"));

if (hasTool) {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => switchTab(tab.dataset.tab));
  });

  [
    "bizName",
    "clientName",
    "serviceName",
    "amount",
    "gstRate",
    "dueDays",
  ].forEach((id) => byId(id).addEventListener("input", updateInvoice));

  ["upiId", "payeeName", "upiAmount", "upiNote"].forEach((id) => {
    byId(id).addEventListener("input", updateUpi);
  });

  ["monthlyGoal", "workDays", "collected", "aov"].forEach((id) => {
    byId(id).addEventListener("input", updateTarget);
  });

  byId("copyInvoice").addEventListener("click", (event) => {
    copyText(byId("invoiceText").value, event.currentTarget);
  });

  byId("saveInvoice").addEventListener("click", async () => {
    const payload = invoicePayload();

    try {
      const result = await postJson("/api/invoices", payload);
      showStatusActions("invoiceStatus", "Saved. Your printable invoice is ready.", [
        { label: "Open invoice", href: result.print_url, newTab: true },
        { label: "Send on WhatsApp", href: result.whatsapp_url, newTab: true },
        { label: "Dashboard", href: result.dashboard_url },
      ]);
    } catch {
      saveFallback("rozledger_invoices", payload);
      showStatus("invoiceStatus", "Saved in this browser. Run the backend to save centrally.");
    }
  });

  byId("copyUpi").addEventListener("click", (event) => {
    copyText(byId("upiLink").value, event.currentTarget);
  });

  byId("copyReminder").addEventListener("click", (event) => {
    copyText(byId("reminderText").value, event.currentTarget);
  });

  byId("printInvoice").addEventListener("click", () => {
    const printWindow = window.open("", "invoice-print");
    printWindow.document.write(`
      <title>Invoice</title>
      <pre style="font: 16px/1.6 system-ui; white-space: pre-wrap;">${byId("invoiceText").value}</pre>
    `);
    printWindow.document.close();
    printWindow.print();
  });
}

if (hasTool) {
  updateInvoice();
}
