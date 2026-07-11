(() => {
  let opener = null;
  let formDirty = false;

  function markFormDirty(event) {
    if (event.target.closest("form")) {
      formDirty = true;
    }
  }

  function disableSubmit(event) {
    formDirty = false;
    const button = event.submitter;
    if (button instanceof HTMLButtonElement) {
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
    }
  }

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

  function restoreFocus() {
    if (opener instanceof HTMLElement) {
      opener.focus();
    }
    opener = null;
  }

  function localizeTime(element) {
    const value = new Date(element.dateTime);
    if (!Number.isNaN(value.valueOf())) {
      element.textContent = value.toLocaleString();
    }
  }

  function pollNotice(notice) {
    if (document.hidden || formDirty || document.querySelector("dialog[open]")) {
      return;
    }
    const request = new XMLHttpRequest();
    request.open("GET", notice.dataset.pollUrl);
    request.setRequestHeader("X-Requested-With", "AlarmBrokerUI");
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

  function initializeNavigation() {
    const adminNavigation = document.querySelector(".admin-nav");
    if (
      adminNavigation instanceof HTMLDetailsElement &&
      window.matchMedia("(max-width: 48rem)").matches
    ) {
      adminNavigation.removeAttribute("open");
    }
  }

  function reloadPage() {
    window.location.reload();
  }

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

  initializeNavigation();
  document.addEventListener("input", markFormDirty);
  document.addEventListener("submit", disableSubmit);
  document.addEventListener("click", handleDialogClick);
  document
    .querySelectorAll("dialog")
    .forEach((dialog) => dialog.addEventListener("close", restoreFocus));
  document.querySelectorAll("time[datetime]").forEach(localizeTime);
  initializePolling();
})();
