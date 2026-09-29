const broker = document.querySelector("#broker");
const mode = document.querySelector("#mode");
const payload = document.querySelector("#payload");
const badge = document.querySelector("#connectionBadge");
const resultStatus = document.querySelector("#resultStatus");
const resultMeta = document.querySelector("#resultMeta");
const orders = document.querySelector("#orders");

const examples = {
  FIRST_TIME: {
    broker: "mock",
    mode: "FIRST_TIME",
    target_portfolio: [
      { symbol: "RELIANCE", exchange: "NSE", quantity: 10 },
      { symbol: "INFY", exchange: "NSE", quantity: 5 },
      { symbol: "RATEBANK", exchange: "NSE", quantity: 2 }
    ]
  },
  REBALANCE: {
    broker: "mock",
    mode: "REBALANCE",
    instructions: [
      { action: "ADJUST_SELL", symbol: "TCS", exchange: "NSE", quantity: 2 },
      { action: "BUY_NEW", symbol: "HDFCBANK", exchange: "NSE", quantity: 4 },
      { action: "BUY_NEW", symbol: "FAIL-DEMO", exchange: "NSE", quantity: 1 }
    ]
  }
};

function loadExample() {
  payload.value = JSON.stringify(examples[mode.value], null, 2);
}

async function connect() {
  const selected = broker.value;
  if (selected !== "mock") {
    alert("Use Swagger at /docs to submit the required live broker credentials. Live trading remains server-disabled by default.");
    return;
  }
  const response = await fetch("/api/v1/brokers/mock/connect", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      credentials: { holdings: [{ symbol: "TCS", exchange: "NSE", quantity: 8 }] },
      validate_connection: true
    })
  });
  if (!response.ok) throw new Error((await response.json()).detail || "Connection failed");
  badge.textContent = "Mock connected";
}

async function execute() {
  let body;
  try { body = JSON.parse(payload.value); } catch { alert("Payload is not valid JSON"); return; }
  const response = await fetch("/api/v1/executions", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() },
    body: JSON.stringify(body)
  });
  const data = await response.json();
  if (!response.ok) { alert(data.detail || "Execution failed"); return; }
  resultStatus.textContent = data.status;
  resultStatus.className = `badge ${data.status.includes("FAILED") ? "FAILED" : ""}`;
  resultMeta.textContent = `${data.execution_id} · ${data.orders.length} planned order(s)`;
  if (!data.orders.length) { orders.innerHTML = '<div class="empty">Portfolio already matches the target.</div>'; return; }
  orders.innerHTML = `<table><thead><tr><th>Symbol</th><th>Side</th><th>Quantity</th><th>Status</th><th>Attempts</th></tr></thead><tbody>${data.orders.map(order => `<tr><td>${order.exchange}:${order.symbol}</td><td class="${order.side}">${order.side}</td><td>${order.quantity}</td><td class="${order.status}">${order.status}</td><td>${order.attempts}</td></tr>`).join("")}</tbody></table>`;
}

document.querySelector("#connect").addEventListener("click", () => connect().catch(error => alert(error.message)));
document.querySelector("#execute").addEventListener("click", () => execute().catch(error => alert(error.message)));
document.querySelector("#reset").addEventListener("click", loadExample);
mode.addEventListener("change", loadExample);
loadExample();
