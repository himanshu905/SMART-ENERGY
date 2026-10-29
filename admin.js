const adminElements = {
  avatar: document.querySelector("#admin-avatar"),
  count: document.querySelector("#customer-count"),
  createAdminForm: document.querySelector("#create-admin-form"),
  createAdminSubmit: document.querySelector("#create-admin-submit"),
  adminAccountMessage: document.querySelector("#admin-account-message"),
  createForm: document.querySelector("#create-form"),
  createSubmit: document.querySelector("#create-submit"),
  cost: document.querySelector("#total-cost"),
  customerName: document.querySelector("#admin-name"),
  customerRows: document.querySelector("#customer-rows"),
  loadError: document.querySelector("#load-error"),
  message: document.querySelector("#admin-message"),
  readingsDialog: document.querySelector("#readings-dialog"),
  readingRows: document.querySelector("#reading-rows"),
  readingsHeading: document.querySelector("#readings-heading"),
  readingsEmail: document.querySelector("#readings-email"),
  resetCustomerId: document.querySelector("#reset-customer-id"),
  resetCustomerLabel: document.querySelector("#reset-customer-label"),
  resetDialog: document.querySelector("#reset-dialog"),
  resetForm: document.querySelector("#reset-form"),
  resetSubmit: document.querySelector("#reset-submit"),
  solar: document.querySelector("#total-solar"),
  usage: document.querySelector("#total-usage"),
};

function formatNumber(number, digits = 1) {
  return new Intl.NumberFormat("en-IN", { maximumFractionDigits: digits }).format(number);
}

function setMessage(text, isError = false) {
  adminElements.message.textContent = text;
  adminElements.message.hidden = !text;
  adminElements.message.classList.toggle("error-message", isError);
}

function handleAccessError(error) {
  if (error.status === 401) {
    window.location.assign("/login");
    return true;
  }
  return false;
}

function createCell(text, className = "") {
  const cell = document.createElement("td");
  cell.textContent = text;
  if (className) {
    cell.className = className;
  }
  return cell;
}

function createActionButton(label, action, customerId, className = "") {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  button.dataset.action = action;
  button.dataset.customerId = String(customerId);
  button.className = className;
  return button;
}

function renderCustomers(customers) {
  adminElements.customerRows.replaceChildren();
  if (!customers.length) {
    const row = document.createElement("tr");
    row.append(createCell("No customer accounts yet. Add a customer to get started.", "table-empty"));
    row.firstChild.colSpan = 5;
    adminElements.customerRows.append(row);
    return;
  }

  for (const customer of customers) {
    const row = document.createElement("tr");
    const identity = document.createElement("td");
    identity.className = "customer-identity";
    const name = document.createElement("strong");
    name.textContent = customer.name;
    const email = document.createElement("span");
    email.textContent = customer.email;
    identity.append(name, email);

    const actions = document.createElement("td");
    actions.className = "customer-actions";
    actions.append(
      createActionButton("Readings", "readings", customer.id, "table-action"),
      createActionButton("Reset password", "reset", customer.id, "table-action"),
      createActionButton("Remove", "delete", customer.id, "table-action danger-action"),
    );

    row.append(
      identity,
      createCell(`${formatNumber(customer.usageKwh)} kWh`),
      createCell(`₹${formatNumber(customer.costInr, 0)}`),
      createCell(`${formatNumber(customer.solarKwh)} kWh`),
      actions,
    );
    adminElements.customerRows.append(row);
  }
}

async function loadCustomers() {
  const result = await window.wattwiseAuth.request("/api/admin/customers");
  const customers = result.customers;
  adminElements.count.textContent = formatNumber(customers.length, 0);
  adminElements.usage.textContent = formatNumber(
    customers.reduce((total, customer) => total + customer.usageKwh, 0),
  );
  adminElements.cost.textContent = formatNumber(
    customers.reduce((total, customer) => total + customer.costInr, 0),
    0,
  );
  adminElements.solar.textContent = formatNumber(
    customers.reduce((total, customer) => total + customer.solarKwh, 0),
  );
  renderCustomers(customers);
}

async function loadAdmin() {
  try {
    const user = await window.wattwiseAuth.currentUser();
    if (user.role !== "admin") {
      window.location.assign("/dashboard");
      return;
    }
    adminElements.customerName.textContent = user.name;
    adminElements.avatar.textContent = user.name
      .split(/\s+/)
      .slice(0, 2)
      .map((part) => part[0])
      .join("")
      .toUpperCase();
    await loadCustomers();
  } catch (error) {
    if (handleAccessError(error)) {
      return;
    }
    adminElements.loadError.hidden = false;
    adminElements.loadError.textContent = error.message || "Unable to load customer accounts.";
    console.error("Unable to load Wattwise admin data.", error);
  }
}

adminElements.createForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setMessage("");
  adminElements.createSubmit.disabled = true;
  try {
    const formData = new FormData(adminElements.createForm);
    const result = await window.wattwiseAuth.request("/api/admin/customers", {
      method: "POST",
      csrf: true,
      body: JSON.stringify({
        name: formData.get("name"),
        email: formData.get("email"),
        password: formData.get("password"),
      }),
    });
    adminElements.createForm.reset();
    setMessage(`Customer account created for ${result.name}. Share their temporary password securely.`);
    await loadCustomers();
  } catch (error) {
    if (!handleAccessError(error)) {
      setMessage(error.message || "Unable to create the customer account.", true);
    }
  } finally {
    adminElements.createSubmit.disabled = false;
  }
});

adminElements.createAdminForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  adminElements.adminAccountMessage.textContent = "";
  adminElements.adminAccountMessage.hidden = true;
  adminElements.adminAccountMessage.classList.remove("error-message");
  adminElements.createAdminSubmit.disabled = true;
  try {
    const formData = new FormData(adminElements.createAdminForm);
    const result = await window.wattwiseAuth.request("/api/admin/admins", {
      method: "POST",
      csrf: true,
      body: JSON.stringify({
        email: formData.get("email"),
        name: formData.get("name"),
        password: formData.get("password"),
      }),
    });
    adminElements.createAdminForm.reset();
    adminElements.adminAccountMessage.textContent =
      `Admin access created for ${result.email}. Share the temporary password privately.`;
    adminElements.adminAccountMessage.hidden = false;
  } catch (error) {
    if (handleAccessError(error)) {
      return;
    }
    adminElements.adminAccountMessage.textContent =
      error.message || "Unable to create the admin account.";
    adminElements.adminAccountMessage.classList.add("error-message");
    adminElements.adminAccountMessage.hidden = false;
  } finally {
    adminElements.createAdminSubmit.disabled = false;
  }
});

adminElements.customerRows.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) {
    return;
  }

  const customerId = Number(button.dataset.customerId);
  const row = button.closest("tr");
  const customerName = row.querySelector(".customer-identity strong").textContent;
  const customerEmail = row.querySelector(".customer-identity span").textContent;
  setMessage("");

  if (button.dataset.action === "reset") {
    adminElements.resetForm.reset();
    adminElements.resetCustomerId.value = String(customerId);
    adminElements.resetCustomerLabel.textContent = `${customerName} · ${customerEmail}`;
    adminElements.resetDialog.showModal();
    return;
  }

  if (button.dataset.action === "readings") {
    try {
      const result = await window.wattwiseAuth.request(
        `/api/admin/readings?customer_id=${encodeURIComponent(customerId)}`,
      );
      adminElements.readingsHeading.textContent = result.customer.name;
      adminElements.readingsEmail.textContent = result.customer.email;
      adminElements.readingRows.replaceChildren();
      for (const reading of result.readings) {
        const row = document.createElement("tr");
        const date = new Date(`${reading.reading_date}T00:00:00`).toLocaleDateString("en-IN", {
          day: "numeric",
          month: "short",
          year: "numeric",
        });
        row.append(
          createCell(date),
          createCell(`${formatNumber(reading.usage_kwh)} kWh`),
          createCell(`₹${formatNumber(reading.cost_inr, 0)}`),
          createCell(`${formatNumber(reading.solar_kwh)} kWh`),
          createCell(`${formatNumber(reading.carbon_kg)} kg`),
        );
        adminElements.readingRows.append(row);
      }
      adminElements.readingsDialog.showModal();
    } catch (error) {
      if (!handleAccessError(error)) {
        setMessage(error.message || "Unable to load the customer readings.", true);
      }
    }
    return;
  }

  if (button.dataset.action === "delete") {
    if (!window.confirm(`Remove ${customerName}'s account and its readings? This cannot be undone.`)) {
      return;
    }
    button.disabled = true;
    try {
      await window.wattwiseAuth.request(`/api/admin/customers/${customerId}`, {
        method: "DELETE",
        csrf: true,
      });
      setMessage(`${customerName}'s account was removed.`);
      await loadCustomers();
    } catch (error) {
      button.disabled = false;
      if (!handleAccessError(error)) {
        setMessage(error.message || "Unable to remove the customer account.", true);
      }
    }
  }
});

adminElements.resetForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  adminElements.resetSubmit.disabled = true;
  const customerId = adminElements.resetCustomerId.value;
  try {
    const formData = new FormData(adminElements.resetForm);
    await window.wattwiseAuth.request(`/api/admin/customers/${customerId}/password`, {
      method: "POST",
      csrf: true,
      body: JSON.stringify({ password: formData.get("password") }),
    });
    adminElements.resetDialog.close();
    adminElements.resetForm.reset();
    setMessage("Customer password was reset. Their existing sessions have been signed out.");
  } catch (error) {
    if (!handleAccessError(error)) {
      setMessage(error.message || "Unable to reset the customer password.", true);
    }
  } finally {
    adminElements.resetSubmit.disabled = false;
  }
});

document.querySelector("#close-reset").addEventListener("click", () => adminElements.resetDialog.close());
adminElements.resetDialog.addEventListener("close", () => adminElements.resetForm.reset());
document.querySelector("#close-readings").addEventListener("click", () => adminElements.readingsDialog.close());
document.querySelector("#show-create").addEventListener("click", () => {
  document.querySelector("#customer-name").focus();
});

loadAdmin();
