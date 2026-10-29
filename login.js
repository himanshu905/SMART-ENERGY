const form = document.querySelector("#login-form");
const message = document.querySelector("#login-message");
const submitButton = document.querySelector("#login-submit");
const setupForm = document.querySelector("#setup-form");
const setupMessage = document.querySelector("#setup-message");
const setupSubmit = document.querySelector("#setup-submit");
const authHeading = document.querySelector("#auth-heading");
const authKicker = document.querySelector("#auth-kicker");
const authSubtitle = document.querySelector("#auth-subtitle");
const authFooter = document.querySelector("#auth-footer");
const roleNote = document.querySelector("#login-role-note");

function showSetup(status) {
  document.querySelector("#setup-admin-email").value = status.adminEmail;
  document.querySelector("#setup-customer-email").value = status.customerEmail;
  form.hidden = true;
  setupForm.hidden = false;
  roleNote.hidden = true;
  authKicker.textContent = "ONE-TIME LOCAL SETUP";
  authHeading.textContent = "Create your accounts";
  authSubtitle.textContent = "Choose a separate password for each account.";
  authFooter.textContent = "Passwords are saved securely on this computer.";
}

function showLogin() {
  setupForm.hidden = true;
  form.hidden = false;
  roleNote.hidden = false;
  authKicker.textContent = "WELCOME TO WATTWISE";
  authHeading.textContent = "Sign in to your account";
  authSubtitle.textContent = "Your energy dashboard is just one step away.";
  authFooter.textContent = "Need an account? Ask your Wattwise administrator.";
}

async function loadAuthPage() {
  try {
    const sessionResponse = await fetch("/api/me", { credentials: "same-origin" });
    if (sessionResponse.ok) {
      const session = await sessionResponse.json();
      window.location.replace(session.user.role === "admin" ? "/admin" : "/dashboard");
      return;
    }
    if (sessionResponse.status !== 401) {
      const sessionError = await sessionResponse.json();
      throw new Error(sessionError.error || "Unable to check your sign-in.");
    }

    const response = await fetch("/api/setup-status", { credentials: "same-origin" });
    if (!response.headers.get("content-type")?.includes("application/json")) {
      throw new Error(
        "This is the VS Code preview, not the Wattwise website. Double-click run-website.bat; it will open the correct page.",
      );
    }
    const status = await response.json();
    if (!response.ok) {
      throw new Error(status.error || "Unable to check the account setup.");
    }
    if (status.setupRequired) {
      showSetup(status);
    } else {
      showLogin();
      if (window.location.pathname === "/setup") {
        window.history.replaceState(null, "", "/login");
      }
    }
  } catch (error) {
    showLogin();
    message.textContent = error instanceof TypeError
      ? "Wattwise isn't running. Double-click run-website.bat, then visit http://127.0.0.1:8001."
      : error.message;
    message.hidden = false;
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  message.hidden = true;
  submitButton.disabled = true;
  submitButton.textContent = "Signing in…";

  try {
    const response = await fetch("/api/login", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: form.elements.email.value,
        password: form.elements.password.value,
      }),
    });
    if (!response.headers.get("content-type")?.includes("application/json")) {
      throw new Error("This is not the Wattwise sign-in server. Start run-website.bat and open http://127.0.0.1:8001.");
    }
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || "Unable to sign in.");
    }
    window.location.assign(result.role === "admin" ? "/admin" : "/dashboard");
  } catch (error) {
    message.textContent = error instanceof TypeError
      ? "Can't connect to Wattwise. Start run-website.bat and open http://127.0.0.1:8001."
      : error.message || "Unable to connect. Check that the Wattwise server is running.";
    message.hidden = false;
    submitButton.disabled = false;
    submitButton.innerHTML = 'Sign in <span aria-hidden="true">→</span>';
  }
});

setupForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setupMessage.hidden = true;
  const formData = new FormData(setupForm);
  const adminPassword = formData.get("adminPassword");
  const customerPassword = formData.get("customerPassword");

  if (adminPassword !== formData.get("adminPasswordConfirm")) {
    setupMessage.textContent = "The admin passwords do not match.";
    setupMessage.hidden = false;
    document.querySelector("#setup-admin-confirm").focus();
    return;
  }
  if (customerPassword !== formData.get("customerPasswordConfirm")) {
    setupMessage.textContent = "The customer passwords do not match.";
    setupMessage.hidden = false;
    document.querySelector("#setup-customer-confirm").focus();
    return;
  }
  if (adminPassword === customerPassword) {
    setupMessage.textContent = "Choose a different password for each account.";
    setupMessage.hidden = false;
    document.querySelector("#setup-customer-password").focus();
    return;
  }

  setupSubmit.disabled = true;
  setupSubmit.textContent = "Setting up accounts…";
  try {
    const response = await fetch("/api/setup", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ adminPassword, customerPassword }),
    });
    if (!response.headers.get("content-type")?.includes("application/json")) {
      throw new Error(
        "This is the VS Code preview, not the Wattwise website. Double-click run-website.bat; it will open the correct page.",
      );
    }
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || "Unable to set up the accounts.");
    }
    setupForm.reset();
    setupForm.hidden = true;
    form.hidden = false;
    roleNote.hidden = false;
    authKicker.textContent = "ACCOUNTS READY";
    authHeading.textContent = "Sign in to your account";
    authSubtitle.textContent = result.message;
    authFooter.textContent = "Choose your account's password to sign in.";
    message.classList.add("success-message");
    message.textContent = result.message;
    message.hidden = false;
    form.elements.email.focus();
  } catch (error) {
    setupMessage.textContent = error instanceof TypeError
      ? "Wattwise isn't running. Double-click run-website.bat, then visit http://127.0.0.1:8001."
      : error.message;
    setupMessage.hidden = false;
  } finally {
    setupSubmit.disabled = false;
    setupSubmit.innerHTML = 'Create accounts <span aria-hidden="true">→</span>';
  }
});

loadAuthPage();
