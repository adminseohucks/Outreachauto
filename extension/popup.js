// LinkedPilot Extension — Popup controller

let campaigns = [];
let runningCampaignId = null;

document.addEventListener("DOMContentLoaded", async () => {
  // Check current running state from background
  chrome.runtime.sendMessage({ type: "GET_STATE" }, (resp) => {
    if (resp && resp.running) {
      runningCampaignId = resp.campaignId;
    }
    loadCampaigns();
  });

  document.getElementById("openSettings").addEventListener("click", () => {
    chrome.runtime.openOptionsPage();
  });

  document.getElementById("toggleLog").addEventListener("click", () => {
    const panel = document.getElementById("logPanel");
    const link = document.getElementById("toggleLog");
    if (panel.classList.contains("visible")) {
      panel.classList.remove("visible");
      link.textContent = "Show Log";
    } else {
      panel.classList.add("visible");
      link.textContent = "Hide Log";
      loadLogs();
    }
  });

  // Listen for log updates from background
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.type === "LOG") {
      appendLog(msg.text, msg.level);
    }
    if (msg.type === "STATE_UPDATE") {
      runningCampaignId = msg.running ? msg.campaignId : null;
      updateUI();
    }
  });
});

async function loadCampaigns() {
  const settings = await getSettings();
  if (!settings.dashboardUrl) {
    document.getElementById("campaignList").innerHTML =
      '<div class="empty">Dashboard URL not set.<br><a id="goSettings">Open Settings</a></div>';
    document.getElementById("goSettings")?.addEventListener("click", () => {
      chrome.runtime.openOptionsPage();
    });
    return;
  }

  try {
    const resp = await fetch(`${settings.dashboardUrl}/api/ext/campaigns`);
    const data = await resp.json();
    campaigns = data.campaigns || [];
    updateUI();
  } catch (err) {
    document.getElementById("campaignList").innerHTML =
      `<div class="empty">Cannot connect to dashboard<br>${settings.dashboardUrl}<br><br>` +
      `<a id="goSettings">Check Settings</a></div>`;
    document.getElementById("goSettings")?.addEventListener("click", () => {
      chrome.runtime.openOptionsPage();
    });
    setStatus("error", "Dashboard unreachable");
  }
}

function updateUI() {
  const container = document.getElementById("campaignList");

  if (campaigns.length === 0) {
    container.innerHTML =
      '<div class="empty">No active comment campaigns found.<br>Create one in your dashboard first.</div>';
    setStatus("idle", "No campaigns available");
    return;
  }

  container.innerHTML = campaigns.map((c) => {
    const progress = c.total_leads > 0
      ? Math.round((c.processed / c.total_leads) * 100)
      : 0;
    const isRunning = runningCampaignId === c.id;
    const senderInfo = c.company_page_name
      ? `${c.sender_name} (as ${c.company_page_name})`
      : c.sender_name;

    return `
      <div class="campaign-card ${isRunning ? 'active' : ''}">
        <div class="campaign-name">${esc(c.name)}</div>
        <div class="campaign-info">
          <span>Sender: ${esc(senderInfo)}</span>
          <span>Pending: ${c.pending_count}</span>
        </div>
        <div class="progress-bar">
          <div class="progress-fill" style="width: ${progress}%"></div>
        </div>
        <div class="campaign-info">
          Done: ${c.successful} | Failed: ${c.failed} | Skipped: ${c.skipped} (${progress}%)
        </div>
        <div class="btn-row">
          ${isRunning
            ? `<button class="btn btn-stop" data-action="stop" data-id="${c.id}">Stop</button>`
            : c.pending_count > 0
              ? `<button class="btn btn-start" data-action="start" data-id="${c.id}">Start Auto-Comment</button>`
              : `<button class="btn btn-disabled" disabled>All Done</button>`
          }
        </div>
      </div>
    `;
  }).join("");

  // Bind buttons
  container.querySelectorAll("[data-action]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const action = btn.dataset.action;
      const id = parseInt(btn.dataset.id);
      if (action === "start") startCampaign(id);
      else if (action === "stop") stopCampaign(id);
    });
  });

  if (runningCampaignId) {
    const running = campaigns.find((c) => c.id === runningCampaignId);
    setStatus("running", `Running: ${running?.name || 'Campaign #' + runningCampaignId}`);
  } else {
    setStatus("idle", "Idle — select a campaign to start");
  }
}

function startCampaign(campaignId) {
  chrome.runtime.sendMessage(
    { type: "START_CAMPAIGN", campaignId },
    (resp) => {
      if (resp?.ok) {
        runningCampaignId = campaignId;
        updateUI();
      }
    }
  );
}

function stopCampaign(campaignId) {
  chrome.runtime.sendMessage(
    { type: "STOP_CAMPAIGN", campaignId },
    (resp) => {
      runningCampaignId = null;
      updateUI();
    }
  );
}

function setStatus(type, text) {
  document.getElementById("statusDot").className = "status-dot " + type;
  document.getElementById("statusText").textContent = text;
}

function appendLog(text, level = "info") {
  const panel = document.getElementById("logPanel");
  const entry = document.createElement("div");
  entry.className = `entry ${level}`;
  const time = new Date().toLocaleTimeString();
  entry.textContent = `[${time}] ${text}`;
  panel.appendChild(entry);
  panel.scrollTop = panel.scrollHeight;
  // Keep max 50 entries
  while (panel.children.length > 50) panel.removeChild(panel.firstChild);
}

function loadLogs() {
  chrome.runtime.sendMessage({ type: "GET_LOGS" }, (resp) => {
    if (resp?.logs) {
      const panel = document.getElementById("logPanel");
      panel.innerHTML = "";
      resp.logs.forEach((log) => appendLog(log.text, log.level));
    }
  });
}

function getSettings() {
  return new Promise((resolve) => {
    chrome.storage.local.get({
      dashboardUrl: "http://localhost:8080",
      vpsUrl: "",
      vpsApiKey: "",
      minDelay: 120,
      maxDelay: 480,
      commentTone: "professional and friendly",
      commentLang: "english",
    }, resolve);
  });
}

function esc(str) {
  const div = document.createElement("div");
  div.textContent = str || "";
  return div.innerHTML;
}
