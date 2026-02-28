// LinkedPilot Extension — Content Script
// Runs on linkedin.com pages. Extracts post data and posts comments.

// ---------------------------------------------------------------------------
// Message handler from background script
// ---------------------------------------------------------------------------

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  switch (msg.type) {
    case "EXTRACT_DATA":
      extractPageData(msg.lead)
        .then((data) => {
          chrome.runtime.sendMessage({ type: "PAGE_DATA", data });
        })
        .catch((err) => {
          chrome.runtime.sendMessage({
            type: "CONTENT_ERROR",
            error: err.message,
          });
        });
      sendResponse({ ok: true });
      return true;

    case "POST_COMMENT":
      postComment(msg.commentText, msg.companyPageName)
        .then((result) => {
          chrome.runtime.sendMessage({ type: "COMMENT_POSTED", data: result });
        })
        .catch((err) => {
          chrome.runtime.sendMessage({
            type: "COMMENT_POSTED",
            data: { success: false, error: err.message },
          });
        });
      sendResponse({ ok: true });
      return true;
  }
});

// ---------------------------------------------------------------------------
// Extract post text + existing comments from the page
// ---------------------------------------------------------------------------

async function extractPageData(lead) {
  // Scroll down to load activity section
  await humanScroll();
  await sleep(2000);
  await humanScroll();
  await sleep(2000);

  // Find the latest post container
  const postContainer = findLatestPost();
  if (!postContainer) {
    return {
      postText: null,
      postAuthor: lead?.full_name || "",
      existingComments: [],
    };
  }

  // Extract post text
  const postText = extractPostText(postContainer);

  // Extract post author name
  const postAuthor = extractPostAuthor(postContainer) || lead?.full_name || "";

  // Extract existing comments (up to 5)
  const existingComments = await extractExistingComments(postContainer);

  return { postText, postAuthor, existingComments };
}

// ---------------------------------------------------------------------------
// Find latest post on profile/activity page
// ---------------------------------------------------------------------------

function findLatestPost() {
  const selectors = [
    "div.feed-shared-update-v2",
    "div.occludable-update",
    "li.profile-creator-shared-feed-update__container",
    'div[data-urn*="activity"]',
    ".feed-shared-update-v2",
  ];

  for (const sel of selectors) {
    const el = document.querySelector(sel);
    if (el) return el;
  }

  // Fallback: look for any post-like container
  const posts = document.querySelectorAll('[data-urn*="urn:li:activity"]');
  if (posts.length > 0) return posts[0];

  return null;
}

// ---------------------------------------------------------------------------
// Extract text content from a post
// ---------------------------------------------------------------------------

function extractPostText(postContainer) {
  const selectors = [
    "div.feed-shared-update-v2__description",
    "span.feed-shared-text__text-view",
    "div.feed-shared-text",
    "div.update-components-text",
    'span[dir="ltr"]',
    ".feed-shared-inline-show-more-text",
    ".break-words",
  ];

  for (const sel of selectors) {
    const el = postContainer.querySelector(sel);
    if (el) {
      const text = el.innerText?.trim();
      if (text && text.length > 10) return text;
    }
  }

  return null;
}

// ---------------------------------------------------------------------------
// Extract post author name
// ---------------------------------------------------------------------------

function extractPostAuthor(postContainer) {
  const selectors = [
    ".feed-shared-actor__name",
    ".update-components-actor__name",
    'span[dir="ltr"] > span[aria-hidden="true"]',
    ".feed-shared-actor__title",
  ];

  for (const sel of selectors) {
    const el = postContainer.querySelector(sel);
    if (el) {
      const text = el.innerText?.trim();
      if (text) return text;
    }
  }

  return null;
}

// ---------------------------------------------------------------------------
// Extract existing comments (load comment section if needed)
// ---------------------------------------------------------------------------

async function extractExistingComments(postContainer) {
  const comments = [];

  // First try to find existing visible comments
  let commentEls = postContainer.querySelectorAll(
    ".comments-comment-item__main-content, " +
    ".comments-comment-item .update-components-text, " +
    ".feed-shared-update-v2__comments-container .comments-comment-item"
  );

  // If no comments visible, click "Comment" button to open panel
  if (commentEls.length === 0) {
    const commentBtn = findCommentButton(postContainer);
    if (commentBtn) {
      commentBtn.click();
      await sleep(2000);
      // Re-query for comments
      commentEls = postContainer.querySelectorAll(
        ".comments-comment-item__main-content, " +
        ".comments-comment-item .update-components-text"
      );
    }
  }

  // Also try broader selectors
  if (commentEls.length === 0) {
    commentEls = document.querySelectorAll(
      '.comments-comments-list .comments-comment-item__main-content'
    );
  }

  commentEls.forEach((el) => {
    const text = el.innerText?.trim();
    if (text && text.length > 3 && comments.length < 5) {
      comments.push(text);
    }
  });

  return comments;
}

// ---------------------------------------------------------------------------
// Post a comment on the current post
// ---------------------------------------------------------------------------

async function postComment(commentText, companyPageName) {
  const postContainer = findLatestPost();
  if (!postContainer) {
    return { success: false, error: "Post container not found" };
  }

  // 1. Open comment box (click Comment button if not already open)
  const commentInput = findCommentInput();
  if (!commentInput) {
    const commentBtn = findCommentButton(postContainer);
    if (!commentBtn) {
      return { success: false, error: "Comment button not found" };
    }
    commentBtn.click();
    await sleep(2000);
  }

  // 2. Switch identity to company page if needed
  if (companyPageName) {
    await switchCommentIdentity(companyPageName);
    await sleep(1000);
  }

  // 3. Find the comment input box
  const input = findCommentInput();
  if (!input) {
    return { success: false, error: "Comment input box not found" };
  }

  // 4. Type comment with human-like timing
  await typeHumanLike(input, commentText);
  await sleep(1000);

  // 5. Click submit/post button
  const submitted = await clickSubmitButton();
  if (!submitted) {
    return { success: false, error: "Submit button not found or click failed" };
  }

  await sleep(3000);
  return { success: true, commentText };
}

// ---------------------------------------------------------------------------
// Find comment button on a post
// ---------------------------------------------------------------------------

function findCommentButton(postContainer) {
  const selectors = [
    'button[aria-label*="Comment"]',
    'button[aria-label*="comment"]',
    "button.comment-button",
    ".social-actions-button--comment",
  ];

  for (const sel of selectors) {
    // Try within the post first
    let btn = postContainer?.querySelector(sel);
    if (btn) return btn;
    // Try the whole page
    btn = document.querySelector(sel);
    if (btn) return btn;
  }

  // Fallback: look for button containing "Comment" text
  const buttons = postContainer?.querySelectorAll("button") || [];
  for (const btn of buttons) {
    if (btn.innerText?.toLowerCase().includes("comment")) return btn;
  }

  return null;
}

// ---------------------------------------------------------------------------
// Find comment input box
// ---------------------------------------------------------------------------

function findCommentInput() {
  const selectors = [
    'div.ql-editor[data-placeholder*="comment"]',
    'div.ql-editor[data-placeholder*="Comment"]',
    "div.ql-editor",
    'div[role="textbox"][aria-label*="comment"]',
    'div[role="textbox"][contenteditable="true"]',
    "div.comments-comment-box__form div[contenteditable]",
    'div[contenteditable="true"].ql-editor',
  ];

  for (const sel of selectors) {
    const el = document.querySelector(sel);
    if (el) return el;
  }

  return null;
}

// ---------------------------------------------------------------------------
// Switch comment identity to company page
// ---------------------------------------------------------------------------

async function switchCommentIdentity(pageName) {
  const pickerSelectors = [
    'button.comments-comment-box-comment-text-field__avatar',
    'button[aria-label*="Commenting as"]',
    'div.comments-comment-box__identity button',
    'form.comments-comment-box__form button[class*="avatar"]',
  ];

  let pickerBtn = null;
  for (const sel of pickerSelectors) {
    pickerBtn = document.querySelector(sel);
    if (pickerBtn) break;
  }

  if (!pickerBtn) return false;

  pickerBtn.click();
  await sleep(800);

  // Find the option matching the page name
  const optionSelectors = [
    'div[role="option"]',
    'li[role="option"]',
    ".comments-comment-box-identity-picker__item",
    'button[data-control-name*="identity"]',
  ];

  const target = pageName.toLowerCase().trim();

  for (const sel of optionSelectors) {
    const options = document.querySelectorAll(sel);
    for (const opt of options) {
      if (opt.innerText?.toLowerCase().includes(target)) {
        opt.click();
        await sleep(500);
        return true;
      }
    }
  }

  return false;
}

// ---------------------------------------------------------------------------
// Type text like a human (character by character with random delays)
// ---------------------------------------------------------------------------

async function typeHumanLike(element, text) {
  // Focus the element
  element.focus();
  element.click();
  await sleep(300);

  // Clear any existing content
  element.innerHTML = "";

  // Type character by character
  for (let i = 0; i < text.length; i++) {
    const char = text[i];

    // Simulate keyboard events
    element.dispatchEvent(
      new KeyboardEvent("keydown", { key: char, bubbles: true })
    );

    // Insert the character
    document.execCommand("insertText", false, char);

    element.dispatchEvent(
      new KeyboardEvent("keyup", { key: char, bubbles: true })
    );

    // Human-like typing speed: 30-120ms per character
    const delay = 30 + Math.random() * 90;
    await sleep(delay);

    // Occasional longer pause (simulating thinking)
    if (Math.random() < 0.05) {
      await sleep(200 + Math.random() * 400);
    }
  }

  // Trigger input and change events
  element.dispatchEvent(new Event("input", { bubbles: true }));
  element.dispatchEvent(new Event("change", { bubbles: true }));

  await sleep(500);
}

// ---------------------------------------------------------------------------
// Click submit/post button
// ---------------------------------------------------------------------------

async function clickSubmitButton() {
  const selectors = [
    "button.comments-comment-box__submit-button",
    'button[aria-label="Post comment"]',
    'button[type="submit"].comments-comment-box__submit-button',
    'form.comments-comment-box__form button[type="submit"]',
  ];

  for (const sel of selectors) {
    const btn = document.querySelector(sel);
    if (btn && !btn.disabled) {
      // Wait for button to be enabled
      await sleep(500);
      btn.click();
      return true;
    }
  }

  // Fallback: find submit button by text
  const allButtons = document.querySelectorAll(
    ".comments-comment-box__form button, .comments-comment-box button"
  );
  for (const btn of allButtons) {
    const text = btn.innerText?.toLowerCase() || "";
    const label = btn.getAttribute("aria-label")?.toLowerCase() || "";
    if (
      (text.includes("post") || text.includes("submit") ||
       label.includes("post") || label.includes("submit")) &&
      !btn.disabled
    ) {
      btn.click();
      return true;
    }
  }

  return false;
}

// ---------------------------------------------------------------------------
// Utility: human-like scrolling
// ---------------------------------------------------------------------------

async function humanScroll() {
  const scrollAmount = 300 + Math.random() * 500;
  const steps = 5 + Math.floor(Math.random() * 5);
  const stepSize = scrollAmount / steps;

  for (let i = 0; i < steps; i++) {
    window.scrollBy(0, stepSize);
    await sleep(50 + Math.random() * 100);
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
