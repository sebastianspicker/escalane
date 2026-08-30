(() => {
  // Keep enhancement state local so the server-rendered forms remain the baseline.
  let opener = null;
  let formDirty = false;

  /** Mark static server-rendered content ready for deterministic capture. */
  function markInterfaceReady() {
    document.body.classList.add("motion-settled");
  }

  /** Pause background refreshes after an operator starts editing a form. */
  function markFormDirty(event) {
    if (event.target.closest("form")) {
      formDirty = true;
    }
  }

  /** Prevent duplicate submissions while preserving the browser's native form flow. */
  function handleSubmit(event) {
    const form = event.target;
    if (form instanceof HTMLFormElement && form.dataset.confirm) {
      if (!window.confirm(form.dataset.confirm)) {
        event.preventDefault();
        return;
      }
    }
    formDirty = false;
    const button = event.submitter;
    if (button instanceof HTMLButtonElement) {
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
    }
  }

  /** Require a reason only when the selected bulk action is cancellation. */
  function updateBulkReason() {
    const action = document.querySelector("#bulk-action");
    const reason = document.querySelector("#bulk-reason");
    if (!(action instanceof HTMLSelectElement) || !(reason instanceof HTMLInputElement)) {
      return;
    }
    reason.required = action.value === "cancel";
    reason.setAttribute("aria-required", String(reason.required));
  }

  /** Open and close native dialogs through declarative data attributes. */
  function handleDialogClick(event) {
    const openTrigger = event.target.closest("[data-dialog-open]");
    if (openTrigger) {
      const dialog = document.getElementById(openTrigger.dataset.dialogOpen);
      if (dialog instanceof HTMLDialogElement) {
        opener = openTrigger;
        dialog.showModal();
      }
      return;
    }
    const closeTrigger = event.target.closest("[data-dialog-close]");
    if (closeTrigger) {
      const dialog = closeTrigger.closest("dialog");
      if (dialog) {
        dialog.close();
      }
    }
  }

  /** Return keyboard focus to the control that opened a modal dialog. */
  function restoreFocus() {
    if (opener instanceof HTMLElement) {
      opener.focus();
    }
    opener = null;
  }

  /** Render server timestamps in the operator's local timezone. */
  function localizeTime(element) {
    if (element.hasAttribute("data-relative")) {
      return;
    }
    const value = new Date(element.dateTime);
    if (!Number.isNaN(value.valueOf())) {
      element.textContent = value.toLocaleString();
    }
  }

  /** Reveal a refresh notice when server state changes and the page is safe to update. */
  function pollNotice(notice) {
    if (document.hidden || formDirty || document.querySelector("dialog[open]")) {
      return;
    }
    const request = new XMLHttpRequest();
    request.open("GET", notice.dataset.pollUrl);
    request.setRequestHeader("X-Requested-With", "EscalaneUI");
    request.onload = function handlePollLoad() {
      if (request.status < 200 || request.status >= 300) {
        return;
      }
      let payload;
      try {
        payload = JSON.parse(request.responseText);
      } catch {
        return;
      }
      if (payload.revision && payload.revision !== notice.dataset.revision) {
        notice.hidden = false;
      }
    };
    request.send();
  }

  /** Collapse mobile navigation without changing the desktop sidebar default. */
  function initializeNavigation() {
    const adminNavigation = document.querySelector(".admin-nav");
    if (!(adminNavigation instanceof HTMLDetailsElement)) {
      return;
    }
    const mobile = window.matchMedia("(max-width: 48rem)");
    const sync = () => {
      if (mobile.matches) {
        adminNavigation.removeAttribute("open");
      } else {
        adminNavigation.setAttribute("open", "");
      }
    };
    sync();
    if (typeof mobile.addEventListener === "function") {
      mobile.addEventListener("change", sync);
    }
  }

  /** Submit language changes immediately while retaining the no-JS submit button. */
  function initializeLanguageSelection() {
    const selector = document.querySelector(".language-form select");
    if (selector instanceof HTMLSelectElement) {
      selector.addEventListener("change", () => selector.form?.requestSubmit());
    }
  }

  /** Show how old the rendered worklist is without implying live streaming. */
  function initializeFreshness() {
    const freshness = document.querySelector("[data-freshness]");
    if (!(freshness instanceof HTMLTimeElement)) {
      return;
    }
    const startedAt = Date.now();
    window.setInterval(() => {
      const seconds = Math.floor((Date.now() - startedAt) / 1000);
      if (seconds >= 5) {
        freshness.textContent = (freshness.dataset.freshnessTemplate || "{seconds}").replace(
          "{seconds}",
          String(seconds),
        );
      }
    }, 5000);
  }

  /** Keep the attached bulk-action command bar explicit about its selection state. */
  function initializeSelectionCount() {
    const output = document.querySelector("[data-selection-count]");
    const checkboxes = document.querySelectorAll(
      '.worklist-table input[type="checkbox"][name="alarm_id"]',
    );
    if (!(output instanceof HTMLElement) || checkboxes.length === 0) {
      return;
    }
    const update = () => {
      output.textContent = String(
        Array.from(checkboxes).filter((checkbox) => checkbox.checked).length,
      );
    };
    checkboxes.forEach((checkbox) => checkbox.addEventListener("change", update));
    update();
  }

  /** Reload only after an explicit operator action. */
  function reloadPage() {
    window.location.reload();
  }

  /** Start conservative revision polling without extending the admin session. */
  function initializePolling() {
    const notice = document.querySelector("[data-poll-url]");
    if (!notice?.dataset.pollUrl) {
      return;
    }
    const interval = Number(notice.dataset.pollInterval) || 0;
    if (interval < 5) {
      return;
    }
    window.setInterval(pollNotice, interval * 1000, notice);
    const refresh = notice.querySelector("[data-poll-refresh]");
    if (refresh) {
      refresh.addEventListener("click", reloadPage);
    }
  }

  markInterfaceReady();
  initializeNavigation();
  initializeLanguageSelection();
  initializeFreshness();
  initializeSelectionCount();
  updateBulkReason();
  document.querySelector("#bulk-action")?.addEventListener("change", updateBulkReason);
  document.addEventListener("input", markFormDirty);
  document.addEventListener("submit", handleSubmit);
  document.addEventListener("click", handleDialogClick);
  document
    .querySelectorAll("dialog")
    .forEach((dialog) => dialog.addEventListener("close", restoreFocus));
  document.querySelectorAll("time[datetime]").forEach(localizeTime);
  initializePolling();
})();
