// LinkedPilot Extension — Background Service Worker
// Manages the auto-comment loop: fetches leads, navigates tabs,
// coordinates with content script and VPS AI.

let state = {
  running: false,
  campaignId: null,
  currentLead: null,
  tabId: null,
  logs: [],
};

// ---------------------------------------------------------------------------
// Message handler
// ---------------------------------------------------------------------------

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  switch (msg.type) {
    case "GET_STATE":
      sendResponse({ running: state.running, campaignId: state.campaignId });
      return true;

    case "START_CAMPAIGN":
      startCampaign(msg.campaignId);
      sendResponse({ ok: true });
      return true;

    case "STOP_CAMPAIGN":
      stopCampaign();
      sendResponse({ ok: true });
      return true;

    case "GET_LOGS":
      sendResponse({ logs: state.logs.slice(-50) });
      return true;

    // Content script reports back with extracted data
    case "PAGE_DATA":
      handlePageData(msg.data);
      sendResponse({ ok: true });
      return true;

    // Content script confirms comment was posted
    case "COMMENT_POSTED":
      handleCommentPosted(msg.data);
      sendResponse({ ok: true });
      return true;

    // Content script reports an error
    case "CONTENT_ERROR":
      handleContentError(msg.error);
      sendResponse({ ok: true });
      return true;
  }
});

// ---------------------------------------------------------------------------
// Campaign control
// ---------------------------------------------------------------------------

async function startCampaign(campaignId) {
  state.running = true;
  state.campaignId = campaignId;
  log(`Campaign #${campaignId} started`, "info");
  broadcastState();
  processNextLead();
}

function stopCampaign() {
  log(`Campaign #${state.campaignId} stopped`, "info");
  state.running = false;
  state.campaignId = null;
  state.currentLead = null;
  broadcastState();
}

// ---------------------------------------------------------------------------
// Main loop: process next lead
// ---------------------------------------------------------------------------

async function processNextLead() {
  if (!state.running || !state.campaignId) return;

  const settings = await getSettings();

  try {
    // 1. Fetch next lead from dashboard
    const resp = await fetch(
      `${settings.dashboardUrl}/api/ext/campaigns/${state.campaignId}/next-lead`
    );
    const data = await resp.json();

    if (data.done) {
      log("All leads processed! Campaign complete.", "info");
      state.running = false;
      state.campaignId = null;
      broadcastState();
      return;
    }

    state.currentLead = {
      actionId: data.action_id,
      lead: data.lead,
      senderName: data.sender_name,
      companyPageName: data.company_page_name,
    };

    log(
      `Processing: ${data.lead.full_name} (${data.lead.company || "N/A"})`,
      "info"
    );

    // 2. Navigate to lead's LinkedIn profile in a tab
    const profileUrl = normalizeLinkedInUrl(data.lead.profile_url);
    log(`Navigating to ${profileUrl}`, "info");

    if (state.tabId) {
      // Reuse existing tab
      try {
        await chrome.tabs.update(state.tabId, { url: profileUrl });
      } catch {
        // Tab was closed, create new one
        const tab = await chrome.tabs.create({ url: profileUrl, active: false });
        state.tabId = tab.id;
      }
    } else {
      const tab = await chrome.tabs.create({ url: profileUrl, active: false });
      state.tabId = tab.id;
    }

    // 3. Wait for page to load, then inject content script command
    //    The content script will auto-detect it's on LinkedIn and
    //    wait for the EXTRACT_DATA command.
    waitForTabLoad(state.tabId, () => {
      // Give LinkedIn a moment to render dynamic content
      setTimeout(() => {
        if (!state.running) return;
        chrome.tabs.sendMessage(state.tabId, {
          type: "EXTRACT_DATA",
          lead: state.currentLead.lead,
        });
      }, 5000); // 5s for LinkedIn to render posts
    });
  } catch (err) {
    log(`Error fetching next lead: ${err.message}`, "error");
    // Retry after delay
    setTimeout(() => processNextLead(), 10000);
  }
}

// ---------------------------------------------------------------------------
// Handle data from content script
// ---------------------------------------------------------------------------

async function handlePageData(data) {
  // data = { postText, postAuthor, existingComments[], lead }
  if (!state.running || !state.currentLead) return;

  const settings = await getSettings();

  if (!data.postText) {
    log(`No posts found for ${state.currentLead.lead.full_name} — skipping`, "error");
    await reportAction(false, "", "No posts found on profile");
    scheduleNextLead(settings);
    return;
  }

  log(
    `Post found (${data.postText.length} chars), ${data.existingComments.length} existing comments`,
    "info"
  );

  // Call VPS AI to generate comment
  try {
    const comment = await generateAIComment(settings, data);
    log(`AI comment generated: "${comment.substring(0, 60)}..."`, "info");

    // Send comment back to content script to type and post
    chrome.tabs.sendMessage(state.tabId, {
      type: "POST_COMMENT",
      commentText: comment,
      companyPageName: state.currentLead.companyPageName,
    });
  } catch (err) {
    log(`AI comment generation failed: ${err.message}`, "error");
    await reportAction(false, "", `AI error: ${err.message}`);
    scheduleNextLead(settings);
  }
}

async function handleCommentPosted(data) {
  // data = { success, commentText }
  if (!state.running || !state.currentLead) return;

  const settings = await getSettings();

  if (data.success) {
    log(`Comment posted on ${state.currentLead.lead.full_name}'s post`, "info");
    await reportAction(true, data.commentText, "");
  } else {
    log(`Failed to post comment: ${data.error || "unknown error"}`, "error");
    await reportAction(false, "", data.error || "Comment posting failed");
  }

  scheduleNextLead(settings);
}

function handleContentError(error) {
  log(`Content script error: ${error}`, "error");
  if (state.running && state.currentLead) {
    getSettings().then((settings) => {
      reportAction(false, "", error);
      scheduleNextLead(settings);
    });
  }
}

// ---------------------------------------------------------------------------
// VPS AI integration
// ---------------------------------------------------------------------------

async function generateAIComment(settings, pageData) {
  // Get VPS config — either from settings override or from dashboard
  let vpsBaseUrl = settings.vpsUrl;
  let vpsApiKey = settings.vpsApiKey;

  if (!vpsBaseUrl || !vpsApiKey) {
    // Fetch from dashboard
    const resp = await fetch(`${settings.dashboardUrl}/api/ext/settings`);
    const dashSettings = await resp.json();
    vpsBaseUrl = vpsBaseUrl || dashSettings.vps_base_url;
    vpsApiKey = vpsApiKey || dashSettings.vps_api_key;
  }

  if (!vpsBaseUrl) throw new Error("VPS URL not configured");

  const timestamp = Math.floor(Date.now() / 1000).toString();
  const payload = {
    post_text: pageData.postText.substring(0, 5000),
    post_author: pageData.postAuthor || state.currentLead?.lead?.full_name || "",
    existing_comments: (pageData.existingComments || []).slice(0, 5),
    commenter_name: state.currentLead?.companyPageName || state.currentLead?.senderName || "",
    tone: settings.commentTone || "professional",
    language: settings.commentLang || "english",
  };

  const bodyStr = JSON.stringify(payload);
  const signature = await hmacSign(vpsApiKey, timestamp + bodyStr);

  const resp = await fetch(`${vpsBaseUrl}/api/generate-comment`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": vpsApiKey,
      "X-Request-Timestamp": timestamp,
      "X-Signature": signature,
    },
    body: bodyStr,
  });

  if (!resp.ok) {
    const errText = await resp.text();
    throw new Error(`VPS returned ${resp.status}: ${errText}`);
  }

  const result = await resp.json();
  return result.comment_text;
}

// ---------------------------------------------------------------------------
// HMAC-SHA256 signing (Web Crypto API)
// ---------------------------------------------------------------------------

async function hmacSign(key, message) {
  const encoder = new TextEncoder();
  const cryptoKey = await crypto.subtle.importKey(
    "raw",
    encoder.encode(key),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const sig = await crypto.subtle.sign("HMAC", cryptoKey, encoder.encode(message));
  return Array.from(new Uint8Array(sig))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

// ---------------------------------------------------------------------------
// Dashboard reporting
// ---------------------------------------------------------------------------

async function reportAction(success, commentText, error) {
  if (!state.currentLead || !state.campaignId) return;

  const settings = await getSettings();
  try {
    await fetch(
      `${settings.dashboardUrl}/api/ext/campaigns/${state.campaignId}/action/${state.currentLead.actionId}/complete`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ success, comment_text: commentText, error }),
      }
    );
  } catch (err) {
    log(`Failed to report to dashboard: ${err.message}`, "error");
  }
}

// ---------------------------------------------------------------------------
// Scheduling & helpers
// ---------------------------------------------------------------------------

function scheduleNextLead(settings) {
  if (!state.running) return;

  const minDelay = (settings.minDelay || 120) * 1000;
  const maxDelay = (settings.maxDelay || 480) * 1000;
  const delay = Math.floor(Math.random() * (maxDelay - minDelay)) + minDelay;
  const delaySec = Math.round(delay / 1000);

  log(`Waiting ${delaySec}s before next lead...`, "info");
  state.currentLead = null;

  setTimeout(() => {
    if (state.running) processNextLead();
  }, delay);
}

function waitForTabLoad(tabId, callback) {
  const listener = (updatedTabId, changeInfo) => {
    if (updatedTabId === tabId && changeInfo.status === "complete") {
      chrome.tabs.onUpdated.removeListener(listener);
      callback();
    }
  };
  chrome.tabs.onUpdated.addListener(listener);

  // Timeout safety — if page doesn't load in 30s, proceed anyway
  setTimeout(() => {
    chrome.tabs.onUpdated.removeListener(listener);
    callback();
  }, 30000);
}

function normalizeLinkedInUrl(url) {
  if (!url) return "https://www.linkedin.com";
  if (!url.startsWith("http")) url = "https://" + url;
  // Ensure it goes to the recent activity section
  if (!url.includes("/recent-activity/")) {
    url = url.replace(/\/$/, "") + "/recent-activity/all/";
  }
  return url;
}

function getSettings() {
  return new Promise((resolve) => {
    chrome.storage.local.get(
      {
        dashboardUrl: "http://localhost:8080",
        vpsUrl: "",
        vpsApiKey: "",
        minDelay: 120,
        maxDelay: 480,
        commentTone: "professional and friendly",
        commentLang: "english",
      },
      resolve
    );
  });
}

function log(text, level = "info") {
  const entry = { text, level, time: Date.now() };
  state.logs.push(entry);
  if (state.logs.length > 100) state.logs.shift();

  console.log(`[LinkedPilot] [${level}] ${text}`);

  // Broadcast to popup if open
  chrome.runtime.sendMessage({ type: "LOG", text, level }).catch(() => {});
}

function broadcastState() {
  chrome.runtime
    .sendMessage({
      type: "STATE_UPDATE",
      running: state.running,
      campaignId: state.campaignId,
    })
    .catch(() => {});
}
