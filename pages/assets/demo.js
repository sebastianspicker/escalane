(() => {
  "use strict";

  const feedback = document.querySelector("[data-demo-feedback]");

  function announce(message) {
    if (!feedback) return;
    feedback.textContent = message;
    feedback.hidden = false;
    window.clearTimeout(announce.timeout);
    announce.timeout = window.setTimeout(() => {
      feedback.hidden = true;
    }, 4200);
  }

  document.querySelectorAll("[data-demo-unavailable]").forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      announce("This static demo keeps the click-through focused on alarms, acknowledgement, and simulated delivery. No command was sent.");
    });
  });

  document.querySelectorAll("[data-simulated-action]").forEach((control) => {
    const form = control.closest("form");
    if (form) {
      form.addEventListener("submit", (event) => event.preventDefault());
    }
    if (control.matches("[data-demo-generic]")) {
      control.addEventListener("click", () => {
        announce("This command was simulated locally. No session or server state changed.");
      });
    }
  });

  const filters = document.querySelector("[data-demo-filters]");
  if (filters) {
    filters.addEventListener("submit", (event) => {
      event.preventDefault();
      const query = filters.elements.search.value.trim().toLowerCase();
      const status = filters.elements.status.value;
      document.querySelectorAll("[data-alarm-row]").forEach((row) => {
        const matchesQuery = !query || row.textContent.toLowerCase().includes(query);
        const matchesStatus = !status || row.dataset.status === status;
        row.classList.toggle("is-hidden", !(matchesQuery && matchesStatus));
      });
      announce("Simulated filter applied to the sanitized fixture rows. No server request was made.");
    });
  }

  const selectionCount = document.querySelector("[data-selection-count]");
  const updateSelection = () => {
    if (!selectionCount) return;
    selectionCount.textContent = String(document.querySelectorAll("[data-alarm-select]:checked").length);
  };
  document.querySelectorAll("[data-alarm-select]").forEach((checkbox) => {
    checkbox.addEventListener("change", updateSelection);
  });

  const bulkForm = document.querySelector("[data-demo-bulk]");
  if (bulkForm) {
    bulkForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const selected = document.querySelectorAll("[data-alarm-select]:checked");
      announce(selected.length
        ? `Simulated ${bulkForm.elements.action.value} for ${selected.length} fixture alarm${selected.length === 1 ? "" : "s"}. No state left this browser.`
        : "Select at least one fixture alarm to run the simulated bulk action.");
    });
  }

  document.querySelectorAll("[data-demo-export]").forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      announce("Simulated export prepared from sanitized fixture data. No production data was accessed.");
    });
  });

  const detail = document.querySelector("[data-demo-detail]");
  if (detail) {
    document.querySelectorAll("[data-detail-transition]").forEach((button) => {
      button.addEventListener("click", () => {
        const status = button.dataset.detailTransition;
        const label = status.charAt(0).toUpperCase() + status.slice(1);
        const badge = detail.querySelector("[data-detail-status]");
        detail.className = `detail-heading alarm-state-${status}`;
        badge.className = `status status-${status}`;
        badge.querySelector("[data-status-text]").textContent = label;
        const entry = document.createElement("li");
        entry.innerHTML = `<time>Demo now</time><span>${label} in this browser only</span>`;
        document.querySelector("[data-demo-timeline]").prepend(entry);
        announce(`${label} was simulated locally. No Escalane API command was sent.`);
      });
    });
  }

  const noteForm = document.querySelector("[data-demo-note]");
  if (noteForm) {
    noteForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const note = noteForm.elements.note.value.trim();
      if (!note) return;
      const entry = document.createElement("li");
      entry.innerHTML = `<time>Demo now</time><span>Fixture note: ${note.replace(/[<>]/g, "")}</span>`;
      document.querySelector("[data-demo-timeline]").prepend(entry);
      noteForm.reset();
      announce("Note added to this browser-only fixture. Nothing was stored or transmitted.");
    });
  }

  const responderForm = document.querySelector("[data-demo-responder]");
  if (responderForm) {
    responderForm.addEventListener("submit", (event) => {
      event.preventDefault();
      responderForm.classList.add("is-hidden");
      document.querySelector("[data-responder-complete]").classList.remove("is-hidden");
      const card = document.querySelector(".responder-card");
      card.className = "responder-card alarm-state-acknowledged";
      const badge = card.querySelector(".status");
      badge.className = "status status-acknowledged";
      badge.querySelector("[data-status-text]").textContent = "Acknowledged";
      announce("Acknowledgement simulated in this browser. No capability token or command was sent.");
    });
  }

  const clearSimulation = document.querySelector("[data-demo-clear]");
  if (clearSimulation) {
    clearSimulation.addEventListener("click", () => {
      document.querySelectorAll("[data-simulation-row]").forEach((row) => row.remove());
      document.querySelector("[data-simulation-empty]").classList.remove("is-hidden");
      announce("The fixture delivery list was cleared in this browser only.");
    });
  }
})();
