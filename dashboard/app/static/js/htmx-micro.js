/**
 * htmx-micro.js — Tiny HTMX replacement (only features LinkedPilot uses).
 * Handles: hx-get, hx-delete, hx-confirm, hx-target, hx-swap, hx-trigger.
 * ~60 lines instead of 45KB HTMX library = instant load.
 */
(function () {
  "use strict";

  function swap(target, html, mode) {
    if (mode === "outerHTML") {
      target.outerHTML = html;
    } else {
      target.innerHTML = html;
    }
  }

  function resolveTarget(el) {
    var sel = el.getAttribute("hx-target");
    if (!sel) return el;
    if (sel.startsWith("closest ")) return el.closest(sel.slice(8));
    return document.querySelector(sel);
  }

  function handleElement(el) {
    var method = el.hasAttribute("hx-delete") ? "DELETE"
               : el.hasAttribute("hx-get") ? "GET"
               : el.hasAttribute("hx-post") ? "POST"
               : null;
    if (!method) return;

    var url = el.getAttribute("hx-" + method.toLowerCase());
    var trigger = el.getAttribute("hx-trigger") || "click";

    // "load" trigger — fire immediately
    if (trigger === "load") {
      doRequest(el, method, url);
      return;
    }

    // Periodic trigger: "every Xs"
    var every = trigger.match(/every\s+(\d+)s/);
    if (every) {
      setInterval(function () { doRequest(el, method, url); }, parseInt(every[1]) * 1000);
      return;
    }

    // Default: click trigger
    el.addEventListener("click", function (e) {
      e.preventDefault();
      var msg = el.getAttribute("hx-confirm");
      if (msg && !confirm(msg)) return;
      doRequest(el, method, url);
    });
  }

  function doRequest(el, method, url) {
    fetch(url, { method: method })
      .then(function (r) { return r.text(); })
      .then(function (html) {
        var target = resolveTarget(el);
        if (target) {
          swap(target, html, el.getAttribute("hx-swap") || "innerHTML");
        }
      })
      .catch(function () {});
  }

  // Process all hx-* elements on page load
  document.addEventListener("DOMContentLoaded", function () {
    var selectors = "[hx-get],[hx-post],[hx-delete]";
    document.querySelectorAll(selectors).forEach(handleElement);
  });

  // Expose for dynamic content (like htmx.process)
  window.htmx = {
    process: function (root) {
      var selectors = "[hx-get],[hx-post],[hx-delete]";
      root.querySelectorAll(selectors).forEach(handleElement);
      if (root.matches && root.matches(selectors)) handleElement(root);
    }
  };
})();
