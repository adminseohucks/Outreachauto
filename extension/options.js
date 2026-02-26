// LinkedPilot Extension — Options page

const DEFAULTS = {
  dashboardUrl: "http://localhost:8080",
  vpsUrl: "",
  vpsApiKey: "",
  minDelay: 120,
  maxDelay: 480,
  commentTone: "professional and friendly",
  commentLang: "english",
};

document.addEventListener("DOMContentLoaded", () => {
  // Load saved settings
  chrome.storage.local.get(DEFAULTS, (items) => {
    document.getElementById("dashboardUrl").value = items.dashboardUrl;
    document.getElementById("vpsUrl").value = items.vpsUrl;
    document.getElementById("vpsApiKey").value = items.vpsApiKey;
    document.getElementById("minDelay").value = items.minDelay;
    document.getElementById("maxDelay").value = items.maxDelay;
    document.getElementById("commentTone").value = items.commentTone;
    document.getElementById("commentLang").value = items.commentLang;
  });

  document.getElementById("saveBtn").addEventListener("click", () => {
    const settings = {
      dashboardUrl: document.getElementById("dashboardUrl").value.replace(/\/+$/, ""),
      vpsUrl: document.getElementById("vpsUrl").value.replace(/\/+$/, ""),
      vpsApiKey: document.getElementById("vpsApiKey").value,
      minDelay: parseInt(document.getElementById("minDelay").value) || 120,
      maxDelay: parseInt(document.getElementById("maxDelay").value) || 480,
      commentTone: document.getElementById("commentTone").value || "professional",
      commentLang: document.getElementById("commentLang").value || "english",
    };

    chrome.storage.local.set(settings, () => {
      showStatus("Settings saved!", "success");
    });
  });
});

function showStatus(msg, type) {
  const el = document.getElementById("statusMsg");
  el.textContent = msg;
  el.className = "status " + type;
  setTimeout(() => { el.className = "status"; }, 3000);
}
