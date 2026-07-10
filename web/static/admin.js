// Discount Bot — admin panel.
// Assumes the user has already logged in via /index.html, which stashes
// { jwt, role } in sessionStorage. If either is missing or role != admin,
// we show the auth warning and stop.

const tg = window.Telegram?.WebApp;
if (tg) {
  tg.ready();
  tg.expand();
}

const jwt = sessionStorage.getItem("jwt");
const role = sessionStorage.getItem("role");

const authWarning = document.getElementById("auth-warning");
const tbody = document.getElementById("store-tbody");
const form = document.getElementById("store-form");
const formTitle = document.getElementById("form-title");
const formError = document.getElementById("form-error");
const formSuccess = document.getElementById("form-success");
const resetBtn = document.getElementById("reset-btn");
const reloadBtn = document.getElementById("reload-btn");
const publishBtn = document.getElementById("publish-btn");
const actionResult = document.getElementById("action-result");

const DEFAULT_CONFIG = {
  list_url: "https://api.example.com/products?discount=true",
  method: "GET",
  response_items_path: "$.products[*]",
  fields: {
    product_id: "$.id",
    name: "$.title",
    url: "$.url",
    image_url: "$.image",
    price: "$.price.current",
    original_price: "$.price.was",
    discount_pct: "$.discountPercent",
    brand: "$.brand.name",
  },
  pagination: { param: "page", start: 1, max: 5 },
};

if (!jwt || role !== "admin") {
  authWarning.hidden = false;
  form.hidden = true;
  document.querySelector(".store-table").hidden = true;
  reloadBtn.disabled = true;
  publishBtn.disabled = true;
} else {
  form.querySelector('textarea[name="config"]').value = JSON.stringify(DEFAULT_CONFIG, null, 2);
  loadStores();
}

async function loadStores() {
  tbody.innerHTML = '<tr><td colspan="5">Loading…</td></tr>';
  try {
    const rows = await api("/api/admin/stores");
    if (rows.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5">No dynamic stores yet.</td></tr>';
      return;
    }
    tbody.innerHTML = "";
    for (const s of rows) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${escape(s.code)}</td>
        <td>${escape(s.display_name)}</td>
        <td>${escape(s.flow_type)}</td>
        <td>${escape(s.storefront_code)}</td>
        <td>
          <button data-act="edit" data-code="${escape(s.code)}">Edit</button>
          <button class="danger" data-act="delete" data-code="${escape(s.code)}">Delete</button>
        </td>`;
      tbody.appendChild(tr);
    }
    tbody.querySelectorAll("button").forEach((btn) => {
      btn.addEventListener("click", () => onRowAction(btn.dataset.act, btn.dataset.code, rows));
    });
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="5" class="error">${escape(err.message)}</td></tr>`;
  }
}

function onRowAction(act, code, rows) {
  const s = rows.find((r) => r.code === code);
  if (!s) return;
  if (act === "edit") {
    prefillForm(s);
    window.scrollTo({ top: form.offsetTop - 12, behavior: "smooth" });
  } else if (act === "delete") {
    if (!confirm(`Delete store "${code}"?`)) return;
    api(`/api/admin/stores/${encodeURIComponent(code)}`, { method: "DELETE" })
      .then(() => loadStores())
      .catch((err) => alert(err.message));
  }
}

function prefillForm(s) {
  formTitle.textContent = `Edit store — ${s.code}`;
  form.code.value = s.code;
  form.code.readOnly = true;
  form.display_name.value = s.display_name;
  form.base_url.value = s.base_url;
  form.currency.value = s.currency;
  form.storefront_code.value = s.storefront_code;
  form.querySelector(`input[name="flow_type"][value="${s.flow_type}"]`).checked = true;
  form.querySelector('textarea[name="config"]').value = JSON.stringify(s.config, null, 2);
  hideFormMessages();
}

resetBtn.addEventListener("click", () => {
  formTitle.textContent = "Add store";
  form.reset();
  form.code.readOnly = false;
  form.querySelector('textarea[name="config"]').value = JSON.stringify(DEFAULT_CONFIG, null, 2);
  form.querySelector('input[name="flow_type"][value="api"]').checked = true;
  hideFormMessages();
});

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  hideFormMessages();

  let configObj;
  try {
    configObj = JSON.parse(form.querySelector('textarea[name="config"]').value);
  } catch (err) {
    showFormError("Config isn't valid JSON: " + err.message);
    return;
  }

  const body = {
    code: form.code.value.trim(),
    display_name: form.display_name.value.trim(),
    flow_type: form.querySelector('input[name="flow_type"]:checked').value,
    base_url: form.base_url.value.trim(),
    currency: form.currency.value.trim(),
    storefront_code: form.storefront_code.value.trim(),
    config: configObj,
    enabled: true,
  };

  try {
    await api("/api/admin/stores", { method: "POST", body: JSON.stringify(body) });
    showFormSuccess("Saved. Registry reloaded.");
    await loadStores();
  } catch (err) {
    showFormError(err.message);
  }
});

reloadBtn.addEventListener("click", async () => {
  actionResult.hidden = true;
  try {
    const { loaded } = await api("/api/admin/reload", { method: "POST" });
    actionResult.textContent = `Reloaded — ${loaded} dynamic store(s) live.`;
    actionResult.hidden = false;
  } catch (err) {
    alert(err.message);
  }
});

publishBtn.addEventListener("click", async () => {
  if (!confirm("Post every pending ≥35% product to the channel?")) return;
  actionResult.hidden = true;
  publishBtn.disabled = true;
  publishBtn.textContent = "Publishing…";
  try {
    const { posted } = await api("/api/admin/publish", { method: "POST" });
    actionResult.textContent = `Posted ${posted} product(s) to the channel.`;
    actionResult.hidden = false;
  } catch (err) {
    alert(err.message);
  } finally {
    publishBtn.disabled = false;
    publishBtn.textContent = "Publish ≥35% to channel";
  }
});

async function api(path, opts = {}) {
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${jwt}`,
    ...(opts.headers || {}),
  };
  const res = await fetch(path, { ...opts, headers });
  if (res.status === 204) return null;
  const text = await res.text();
  const data = text ? JSON.parse(text) : {};
  if (!res.ok) {
    throw new Error(data.detail || `HTTP ${res.status}`);
  }
  return data;
}

function showFormError(msg) {
  formError.textContent = msg;
  formError.hidden = false;
  formSuccess.hidden = true;
}
function showFormSuccess(msg) {
  formSuccess.textContent = msg;
  formSuccess.hidden = false;
  formError.hidden = true;
}
function hideFormMessages() {
  formError.hidden = true;
  formSuccess.hidden = true;
}
function escape(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
