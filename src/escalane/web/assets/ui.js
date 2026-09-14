(() => {
  let dialogOpener = null;
  let drawerOpener = null;
  let formDirty = false;

  function markInterfaceReady() {
    document.body.classList.add("motion-settled", "js-ready");
  }

  function markFormDirty(event) {
    if (event.target.closest("form")) formDirty = true;
  }

  function handleSubmit(event) {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) {
      event.preventDefault();
      return;
    }
    formDirty = false;
    if (event.submitter instanceof HTMLButtonElement) {
      const button = event.submitter;
      button.setAttribute("aria-busy", "true");
      window.setTimeout(() => { button.disabled = true; }, 0);
    }
  }

  function handleDialogClick(event) {
    const openTrigger = event.target.closest("[data-dialog-open]");
    if (openTrigger) {
      const dialog = document.getElementById(openTrigger.dataset.dialogOpen);
      if (dialog instanceof HTMLDialogElement) {
        dialogOpener = openTrigger;
        dialog.showModal();
      }
      return;
    }
    const closeTrigger = event.target.closest("[data-dialog-close]");
    if (closeTrigger) closeTrigger.closest("dialog")?.close();
  }

  function restoreDialogFocus() {
    if (dialogOpener instanceof HTMLElement) dialogOpener.focus();
    dialogOpener = null;
  }

  function localizeTime(element) {
    if (element.hasAttribute("data-relative")) return;
    const value = new Date(element.dateTime);
    if (!Number.isNaN(value.valueOf())) element.textContent = value.toLocaleString();
  }

  function pollNotice(notice) {
    if (document.hidden || formDirty || document.querySelector("dialog[open]")) return;
    const request = new XMLHttpRequest();
    request.open("GET", notice.dataset.pollUrl);
    request.setRequestHeader("X-Requested-With", "EscalaneUI");
    request.onload = () => {
      if (request.status < 200 || request.status >= 300) return;
      try {
        const payload = JSON.parse(request.responseText);
        if (payload.revision && payload.revision !== notice.dataset.revision) notice.hidden = false;
      } catch { /* A malformed polling response must not disrupt active work. */ }
    };
    request.send();
  }

  function initializeNavigation() {
    const navigation = document.querySelector(".admin-nav");
    if (!(navigation instanceof HTMLDetailsElement)) return;
    const mobile = window.matchMedia("(max-width: 48rem)");
    const sync = () => mobile.matches ? navigation.removeAttribute("open") : navigation.setAttribute("open", "");
    sync();
    mobile.addEventListener?.("change", sync);
  }

  function initializeLanguageSelection() {
    const selector = document.querySelector(".language-form select");
    if (selector instanceof HTMLSelectElement) selector.addEventListener("change", () => selector.form?.requestSubmit());
  }

  function initializeTheme() {
    const toggles = document.querySelectorAll("[data-theme-toggle]");
    if (!toggles.length) return;
    const stored = window.localStorage.getItem("escalane-theme");
    if (stored === "light" || stored === "dark") document.documentElement.dataset.theme = stored;
    const render = () => {
      const theme = document.documentElement.dataset.theme || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
      const nextIsDark = theme !== "dark";
      toggles.forEach((toggle) => {
        if (!(toggle instanceof HTMLButtonElement)) return;
        const label = nextIsDark ? toggle.dataset.themeDarkLabel : toggle.dataset.themeLightLabel;
        toggle.setAttribute("aria-label", label || "Theme");
        toggle.title = label || "Theme";
        toggle.setAttribute("aria-pressed", String(theme === "dark"));
      });
    };
    render();
    toggles.forEach((toggle) => {
      toggle.addEventListener("click", () => {
        const isDark = document.documentElement.dataset.theme === "dark" || (!document.documentElement.dataset.theme && window.matchMedia("(prefers-color-scheme: dark)").matches);
        const next = isDark ? "light" : "dark";
        document.documentElement.dataset.theme = next;
        window.localStorage.setItem("escalane-theme", next);
        render();
      });
    });
  }

  function initializeSearchShortcut() {
    const search = document.querySelector("[data-search-shortcut]");
    if (!(search instanceof HTMLInputElement)) return;
    document.addEventListener("keydown", (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        search.focus();
        search.select();
      }
    });
  }

  function initializeFreshness() {
    const freshness = document.querySelector("[data-freshness]");
    if (!(freshness instanceof HTMLTimeElement)) return;
    const startedAt = Date.now();
    window.setInterval(() => {
      const seconds = Math.floor((Date.now() - startedAt) / 1000);
      if (seconds >= 5) freshness.textContent = (freshness.dataset.freshnessTemplate || "{seconds}").replace("{seconds}", String(seconds));
    }, 5000);
  }

  function initializeSelectionBar() {
    const bar = document.querySelector("[data-selection-bar]");
    const output = document.querySelector("[data-selection-count]");
    const checkboxes = document.querySelectorAll('.worklist-table input[type="checkbox"][name="alarm_id"]');
    if (!(bar instanceof HTMLFieldSetElement) || !(output instanceof HTMLElement) || !checkboxes.length) return;
    const update = () => {
      const selected = Array.from(checkboxes).filter((checkbox) => checkbox.checked);
      output.textContent = String(selected.length);
      bar.hidden = selected.length === 0;
      document.querySelectorAll(".alarm-row").forEach((row) => row.classList.toggle("selected", Boolean(row.querySelector('input[name="alarm_id"]:checked'))));
    };
    checkboxes.forEach((checkbox) => checkbox.addEventListener("change", update));
    document.querySelector("[data-clear-selection]")?.addEventListener("click", () => {
      checkboxes.forEach((checkbox) => { checkbox.checked = false; });
      update();
    });
    update();
  }

  function initializeFilterEnhancement() {
    const form = document.querySelector("[data-worklist-filters]");
    if (!(form instanceof HTMLFormElement)) return;
    form.querySelectorAll("[data-auto-submit]").forEach((control) => control.addEventListener("change", () => form.requestSubmit()));
  }

  function focusDrawerContent(drawer) {
    drawer.querySelector("h2[tabindex]")?.focus();
  }

  function openDrawer(trigger) {
    const url = trigger.dataset.drawerUrl;
    const drawer = document.querySelector("#alarm-drawer");
    const content = drawer?.querySelector("[data-drawer-content]");
    if (!url || !(drawer instanceof HTMLDialogElement) || !(content instanceof HTMLElement)) return;
    drawerOpener = trigger;
    content.replaceChildren();
    drawer.showModal();
    drawer.setAttribute("aria-busy", "true");
    fetch(url, { credentials: "same-origin", headers: { "X-Requested-With": "EscalaneUI" } })
      .then((response) => {
        if (!response.ok) throw new Error("drawer response failed");
        return response.text();
      })
      .then((fragment) => {
        content.innerHTML = fragment;
        drawer.removeAttribute("aria-busy");
        content.querySelectorAll("time[datetime]").forEach(localizeTime);
        focusDrawerContent(drawer);
      })
      .catch(() => window.location.assign(url.replace("/drawer", "")));
  }

  function loadOlderActivity(link) {
    const history = link.closest("[data-history-fragment]");
    const list = history?.querySelector("[data-history-list]");
    const status = history?.querySelector("[data-history-status]");
    if (!history || !list || !status || history.hasAttribute("aria-busy")) return;
    history.setAttribute("aria-busy", "true");
    link.setAttribute("aria-disabled", "true");
    status.textContent = status.dataset.loading;
    fetch(link.dataset.historyUrl, { credentials: "same-origin", headers: { "X-Requested-With": "EscalaneUI" } })
      .then((response) => {
        if (!response.ok) throw new Error("history response failed");
        return response.text();
      })
      .then((fragment) => {
        if (!history.isConnected) return;
        const parsed = new DOMParser().parseFromString(fragment, "text/html");
        const incoming = parsed.querySelector("[data-history-fragment]");
        if (!incoming) throw new Error("history fragment missing");
        const existing = new Set(Array.from(list.querySelectorAll("[data-history-event]"), (item) => item.dataset.historyEvent));
        const events = Array.from(incoming.querySelectorAll("[data-history-event]")).filter((item) => !existing.has(item.dataset.historyEvent));
        list.querySelector("[data-history-empty]")?.remove();
        events.forEach((item) => item.querySelectorAll("time[datetime]").forEach(localizeTime));
        list.prepend(...events);
        const navigation = history.querySelector("[data-history-navigation]");
        const next = incoming.querySelector("[data-older-activity]");
        navigation.replaceChildren(...(next ? [next] : []));
        status.textContent = events.length ? "" : status.dataset.empty;
        (events[0] || next || status).focus();
      })
      .catch(() => {
        status.textContent = status.dataset.error;
        link.focus();
      })
      .finally(() => {
        history.removeAttribute("aria-busy");
        link.removeAttribute("aria-disabled");
      });
  }

  function initializeDrawer() {
    const drawer = document.querySelector("#alarm-drawer");
    if (!(drawer instanceof HTMLDialogElement)) return;
    document.querySelectorAll(".alarm-row[data-drawer-url]").forEach((row) => {
      row.addEventListener("click", (event) => {
        if (event.target.closest("input, label, button, select, textarea")) return;
        if (event.target.closest("a")) event.preventDefault();
        openDrawer(row);
      });
      row.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.target.closest("input, button, a")) {
          event.preventDefault();
          openDrawer(row);
        }
      });
    });
    drawer.addEventListener("click", (event) => {
      if (event.target.closest("[data-drawer-close]")) drawer.close();
      const older = event.target.closest("[data-older-activity]");
      if (older && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) {
        event.preventDefault();
        loadOlderActivity(older);
      }
    });
    drawer.addEventListener("close", () => {
      drawer.querySelector("[data-drawer-content]")?.replaceChildren();
      if (drawerOpener instanceof HTMLElement) drawerOpener.focus();
      drawerOpener = null;
    });
  }

  function initializePolling() {
    const notice = document.querySelector("[data-poll-url]");
    if (!notice?.dataset.pollUrl) return;
    const interval = Number(notice.dataset.pollInterval) || 0;
    if (interval >= 5) window.setInterval(pollNotice, interval * 1000, notice);
    notice.querySelector("[data-poll-refresh]")?.addEventListener("click", () => window.location.reload());
  }

  markInterfaceReady();
  initializeNavigation();
  initializeLanguageSelection();
  initializeTheme();
  initializeSearchShortcut();
  initializeFreshness();
  initializeSelectionBar();
  initializeFilterEnhancement();
  document.addEventListener("input", markFormDirty);
  document.addEventListener("submit", handleSubmit);
  document.addEventListener("click", handleDialogClick);
  document.querySelectorAll("dialog:not(.alarm-drawer)").forEach((dialog) => dialog.addEventListener("close", restoreDialogFocus));
  document.querySelectorAll("time[datetime]").forEach(localizeTime);
  initializeDrawer();
  initializePolling();
})();
