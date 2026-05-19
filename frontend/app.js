const loginForm = document.getElementById("login-form");
const loginMessage = document.getElementById("login-message");
const accountCard = document.getElementById("account-card");
const loginCard = document.getElementById("login-card");
const accountName = document.getElementById("account-name");
const accountEmail = document.getElementById("account-email");
const accountRole = document.getElementById("account-role");
const accountNote = document.getElementById("account-note");
const logoutButton = document.getElementById("logout-button");
const userGrid = document.getElementById("user-grid");
const feedbackForm = document.getElementById("feedback-form");
const formMessage = document.getElementById("form-message");
const myFeedbackCard = document.getElementById("my-feedback-card");
const myFeedbackList = document.getElementById("my-feedback-list");
const adminCard = document.getElementById("admin-card");
const feedbackList = document.getElementById("feedback-list");
const refreshButton = document.getElementById("refresh-button");
const statusFilter = document.getElementById("status-filter");
const statsCard = document.getElementById("stats-card");
const stats = document.getElementById("stats");

let accessToken = localStorage.getItem("feedback_access_token") || "";
let currentUser = null;

function authHeaders(extra = {}) {
    const headers = { ...extra };
    if (accessToken) {
        headers.Authorization = `Bearer ${accessToken}`;
    }
    return headers;
}

async function apiFetch(url, options = {}) {
    const response = await fetch(url, {
        ...options,
        headers: authHeaders(options.headers || {}),
    });

    let data = {};
    try {
        data = await response.json();
    } catch (error) {
        data = {};
    }

    if (response.status === 401) {
        clearSession();
        renderAuthState();
    }

    return { response, data };
}

function clearSession() {
    accessToken = "";
    currentUser = null;
    localStorage.removeItem("feedback_access_token");
}

function renderFeedbackItems(items, container, allowAdminActions = false) {
    if (!items.length) {
        container.innerHTML = "<div class='message'>No feedback available yet.</div>";
        return;
    }

    container.innerHTML = items.map((item) => `
        <article class="feedback-item">
            <div class="meta">
                <span class="badge">${item.category}</span>
                <span class="badge">Rating ${item.rating}</span>
                <span class="${item.status === "RESOLVED" ? "status-resolved" : item.status === "NEW" ? "status-new" : ""}">${item.status}</span>
            </div>
            <h3>${item.customer_name}</h3>
            <div class="meta">${item.email}</div>
            <p>${item.message}</p>
            <div class="meta">Updated: ${new Date(item.updated_at).toLocaleString()}</div>
            ${item.admin_comment ? `<div class="meta">Admin note: ${item.admin_comment}</div>` : ""}
            ${allowAdminActions ? `
                <label>
                    Change status
                    <select data-id="${item.id}" class="status-select">
                        <option value="">Choose next status</option>
                        <option value="IN_REVIEW">IN_REVIEW</option>
                        <option value="RESOLVED">RESOLVED</option>
                    </select>
                </label>
            ` : ""}
        </article>
    `).join("");

    if (!allowAdminActions) {
        return;
    }

    document.querySelectorAll(".status-select").forEach((select) => {
        select.addEventListener("change", async (event) => {
            if (!event.target.value) {
                return;
            }

            const adminComment = window.prompt("Optional admin comment:");
            const { response, data } = await apiFetch(`/api/commands/feedback/${event.target.dataset.id}/status`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    status: event.target.value,
                    admin_comment: adminComment || null
                })
            });

            if (!response.ok) {
                window.alert(data.error || "Unable to update status");
                return;
            }

            await refresh();
        });
    });
}

function renderAuthState() {
    const isLoggedIn = Boolean(currentUser);
    loginCard.classList.toggle("hidden", isLoggedIn);
    accountCard.classList.toggle("hidden", !isLoggedIn);
    userGrid.classList.toggle("hidden", !isLoggedIn);
    myFeedbackCard.classList.toggle("hidden", !isLoggedIn);
    adminCard.classList.toggle("hidden", !(isLoggedIn && currentUser.role === "ADMIN"));
    statsCard.classList.toggle("hidden", !(isLoggedIn && currentUser.role === "ADMIN"));

    if (!isLoggedIn) {
        myFeedbackList.innerHTML = "";
        feedbackList.innerHTML = "";
        stats.innerHTML = "";
        return;
    }

    accountName.textContent = currentUser.full_name;
    accountEmail.textContent = currentUser.email;
    accountRole.textContent = currentUser.role;
    accountNote.textContent = currentUser.role === "ADMIN"
        ? "You can view all feedback, inspect stats, and change statuses."
        : "You can submit feedback and track only the items you created.";
}

async function restoreSession() {
    if (!accessToken) {
        renderAuthState();
        return;
    }

    const { response, data } = await apiFetch("/api/auth/me");
    if (!response.ok) {
        clearSession();
    } else {
        currentUser = data.user;
    }
    renderAuthState();
}

async function loadMyFeedback() {
    const { response, data } = await apiFetch("/api/queries/feedback/mine");
    if (response.ok) {
        renderFeedbackItems(data.items || [], myFeedbackList, false);
    }
}

async function loadStats() {
    if (!currentUser || currentUser.role !== "ADMIN") {
        return;
    }

    const { response, data } = await apiFetch("/api/queries/stats");
    if (!response.ok) {
        stats.innerHTML = "<div class='message'>Unable to load stats.</div>";
        return;
    }

    stats.innerHTML = `
        <div class="stat">Total<strong>${data.total_feedback}</strong></div>
        <div class="stat">Avg Rating<strong>${data.average_rating}</strong></div>
        <div class="stat">Open<strong>${(data.counts_by_status.NEW || 0) + (data.counts_by_status.IN_REVIEW || 0)}</strong></div>
    `;
}

async function loadAdminFeedback() {
    if (!currentUser || currentUser.role !== "ADMIN") {
        return;
    }

    const params = new URLSearchParams();
    if (statusFilter.value) {
        params.set("status", statusFilter.value);
    }

    const { response, data } = await apiFetch(`/api/queries/feedback?${params.toString()}`);
    if (response.ok) {
        renderFeedbackItems(data.items || [], feedbackList, true);
    }
}

async function refresh() {
    if (!currentUser) {
        return;
    }

    await Promise.all([
        loadMyFeedback(),
        loadStats(),
        loadAdminFeedback(),
    ]);
}

loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const formData = new FormData(loginForm);
    const payload = Object.fromEntries(formData.entries());

    const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
    });
    const data = await response.json();

    if (!response.ok) {
        loginMessage.textContent = data.error || "Login failed";
        return;
    }

    accessToken = data.token;
    currentUser = data.user;
    localStorage.setItem("feedback_access_token", accessToken);
    loginMessage.textContent = "";
    loginForm.reset();
    renderAuthState();
    await refresh();
});

feedbackForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const formData = new FormData(feedbackForm);
    const payload = Object.fromEntries(formData.entries());

    const { response, data } = await apiFetch("/api/commands/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
    });

    if (!response.ok) {
        formMessage.textContent = data.error || "Submission failed";
        return;
    }

    feedbackForm.reset();
    formMessage.textContent = "Feedback submitted and projected into the read model.";
    await refresh();
});

logoutButton.addEventListener("click", () => {
    clearSession();
    renderAuthState();
});

refreshButton.addEventListener("click", refresh);
statusFilter.addEventListener("change", loadAdminFeedback);

restoreSession().then(refresh);
