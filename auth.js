let csrfToken = "";

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request(url, options = {}) {
  const { csrf = false, ...fetchOptions } = options;
  const headers = new Headers(fetchOptions.headers || {});
  if (fetchOptions.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (csrf) {
    if (!csrfToken) {
      throw new Error("Your session has expired. Sign in again.");
    }
    headers.set("X-CSRF-Token", csrfToken);
  }

  const response = await fetch(url, {
    ...fetchOptions,
    credentials: "same-origin",
    headers,
  });
  if (!response.headers.get("content-type")?.includes("application/json")) {
    throw new ApiError(
      "The browser is not connected to the Wattwise server. Start run-website.bat and open http://127.0.0.1:8001.",
      response.status,
    );
  }
  const payload = await response.json();
  if (!response.ok) {
    throw new ApiError(payload.error || "The request could not be completed.", response.status);
  }
  return payload;
}

async function currentUser() {
  const result = await request("/api/me");
  csrfToken = result.csrfToken;
  return result.user;
}

async function signOut() {
  await request("/api/logout", { method: "POST", body: "{}", csrf: true });
  window.location.assign("/login");
}

document.querySelectorAll("[data-logout]").forEach((button) => {
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await signOut();
    } catch (error) {
      if (error.status === 401) {
        window.location.assign("/login");
        return;
      }
      button.disabled = false;
      console.error("Unable to sign out.", error);
    }
  });
});

window.wattwiseAuth = { ApiError, currentUser, request };
