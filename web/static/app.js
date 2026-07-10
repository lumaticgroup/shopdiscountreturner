// Discount Bot Mini App — login/signup + Telegram chat link.
//
// Flow:
//   1. User picks Log in or Sign up tab.
//   2. Submit → POST /api/auth/login (or /signup) → get JWT.
//   3. Store JWT in sessionStorage.
//   4. POST /api/auth/telegram-link with Telegram.WebApp.initData → backend
//      links chat_id → user_id.
//   5. Show success. If admin, offer /admin.html link. Otherwise call
//      Telegram.WebApp.close() after a short delay.

const tg = window.Telegram?.WebApp;
if (tg) {
  tg.ready();
  tg.expand();
}

const form = document.getElementById("form");
const submitBtn = document.getElementById("submit-btn");
const errorEl = document.getElementById("error");
const successEl = document.getElementById("success");
const adminLink = document.getElementById("admin-link");
const subtitle = document.getElementById("subtitle");
const tabs = document.querySelectorAll(".tab");

let mode = "login"; // or "signup"

for (const tab of tabs) {
  tab.addEventListener("click", () => {
    tabs.forEach((t) => t.classList.remove("is-active"));
    tab.classList.add("is-active");
    mode = tab.dataset.tab;
    submitBtn.textContent = mode === "signup" ? "Sign up" : "Log in";
    subtitle.textContent = mode === "signup"
      ? "Create a new account"
      : "Sign in to continue";
    hideError();
  });
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  hideError();

  const email = form.email.value.trim();
  const password = form.password.value;

  submitBtn.disabled = true;
  submitBtn.textContent = mode === "signup" ? "Creating…" : "Logging in…";

  try {
    const endpoint = mode === "signup" ? "/api/auth/signup" : "/api/auth/login";
    const res = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${res.status}`);
    }
    const { access_token, role } = await res.json();
    sessionStorage.setItem("jwt", access_token);
    sessionStorage.setItem("role", role);

    await linkTelegramChat(access_token);
    showSuccess(role);
  } catch (err) {
    showError(err.message || "Something went wrong.");
    submitBtn.disabled = false;
    submitBtn.textContent = mode === "signup" ? "Sign up" : "Log in";
  }
});

async function linkTelegramChat(jwt) {
  const initData = tg?.initData;
  if (!initData) {
    // App opened outside Telegram (e.g. dev browser). Skip linking — user
    // logged in but can't be tied to a chat.
    console.warn("no Telegram.WebApp.initData — running outside Telegram?");
    return;
  }
  const res = await fetch("/api/auth/telegram-link", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${jwt}`,
    },
    body: JSON.stringify({ init_data: initData }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `link failed: HTTP ${res.status}`);
  }
}

function showError(msg) {
  errorEl.textContent = msg;
  errorEl.hidden = false;
}

function hideError() {
  errorEl.hidden = true;
}

function showSuccess(role) {
  form.hidden = true;
  document.querySelector(".tabs").hidden = true;
  successEl.hidden = false;
  if (role === "admin") {
    adminLink.hidden = false;
    // Admins may want to go straight to store management, so we don't auto-close.
  } else {
    setTimeout(() => tg?.close?.(), 1400);
  }
}
