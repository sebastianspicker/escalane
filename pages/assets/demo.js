(() => {
  "use strict";

  const alarms = [
    { id: "FX-4821", person: "Morgan Ellis", room: "Room 204 · North wing", source: "Desk handset", severity: "P0", status: "triggered", owner: "Unassigned", age: "02:14", minutes: 2, created: "10:12", timeline: [["10:12", "Fixture alarm created"], ["10:12", "Pager connector accepted the fixture"]] },
    { id: "FX-4820", person: "Harper Bell", room: "Room 118 · East wing", source: "Pull cord", severity: "P0", status: "triggered", owner: "Unassigned", age: "06:41", minutes: 6, created: "10:07", timeline: [["10:07", "Fixture alarm created"], ["10:07", "SMS connector accepted the fixture"]] },
    { id: "FX-4819", person: "Avery Quinn", room: "Room 231 · North wing", source: "Desk handset", severity: "P1", status: "triggered", owner: "Unassigned", age: "11:03", minutes: 11, created: "10:02", timeline: [["10:02", "Fixture alarm created"], ["10:02", "Signal connector accepted the fixture"]] },
    { id: "FX-4817", person: "Casey Rivera", room: "Room 102 · East wing", source: "Door sensor", severity: "P1", status: "acknowledged", owner: "Demo Operator", age: "24:38", minutes: 24, created: "09:48", timeline: [["09:48", "Fixture alarm created"], ["09:50", "Acknowledged by Demo Operator"]] },
    { id: "FX-4816", person: "Jordan Lin", room: "Room 215 · North wing", source: "Desk handset", severity: "P2", status: "acknowledged", owner: "Demo Operator", age: "31:12", minutes: 31, created: "09:41", timeline: [["09:41", "Fixture alarm created"], ["09:45", "Acknowledged by Demo Operator"]] },
    { id: "FX-4815", person: "Riley Stone", room: "Room 126 · East wing", source: "Pull cord", severity: "P2", status: "resolved", owner: "Alex Park", age: "1h 04m", minutes: 64, created: "09:08", timeline: [["09:08", "Fixture alarm created"], ["09:26", "Resolved after fixture response"]] },
    { id: "FX-4814", person: "Taylor Moss", room: "Room 208 · North wing", source: "Desk handset", severity: "P2", status: "resolved", owner: "Demo Operator", age: "1h 22m", minutes: 82, created: "08:50", timeline: [["08:50", "Fixture alarm created"], ["09:18", "Resolved after fixture response"]] },
    { id: "FX-4813", person: "Parker West", room: "Room 114 · East wing", source: "Door sensor", severity: "P1", status: "cancelled", owner: "Alex Park", age: "2h 10m", minutes: 130, created: "08:02", timeline: [["08:02", "Fixture alarm created"], ["08:06", "Cancelled as fixture test"]] }
  ];
  const state = { status: "", severity: "", query: "", sort: "newest", selected: new Set(), activeId: null, opener: null };
  const feedback = document.querySelector("[data-demo-feedback]");
  const statusNames = { triggered: "Triggered", acknowledged: "Acknowledged", resolved: "Resolved", cancelled: "Cancelled" };

  function announce(message) { if (!feedback) return; feedback.textContent = message; feedback.hidden = false; window.clearTimeout(announce.timeout); announce.timeout = window.setTimeout(() => { feedback.hidden = true; }, 4200); }
  function alarmById(id) { return alarms.find((alarm) => alarm.id === id); }
  function compareAlarms(left, right) { const rank = { P0: 0, P1: 1, P2: 2 }; if (state.sort === "oldest") return right.minutes - left.minutes; if (state.sort === "severity") return rank[left.severity] - rank[right.severity] || left.minutes - right.minutes; if (state.sort === "status") return left.status.localeCompare(right.status) || left.minutes - right.minutes; return left.minutes - right.minutes; }
  function matches(alarm) { const haystack = [alarm.id, alarm.person, alarm.room, alarm.source, alarm.owner, alarm.status, alarm.severity].join(" ").toLowerCase(); return (!state.status || alarm.status === state.status) && (!state.severity || alarm.severity === state.severity) && (!state.query || haystack.includes(state.query)); }
  function statusMarkup(status) { return `<span class="status status-${status}"><span class="status-dot" aria-hidden="true"></span>${statusNames[status]}</span>`; }
  function severityMarkup(severity) { return `<span class="severity severity-${severity.toLowerCase()}">${severity}</span>`; }
  function renderRows() {
    const target = document.querySelector("[data-demo-rows]"); if (!target) return;
    const visible = alarms.filter(matches).sort(compareAlarms); target.replaceChildren();
    visible.forEach((alarm) => { const row = document.createElement("tr"); row.className = `alarm-row alarm-row-${alarm.status}${state.selected.has(alarm.id) ? " selected" : ""}`; row.tabIndex = 0; row.setAttribute("role", "row"); row.dataset.alarmId = alarm.id; row.innerHTML = `<td role="cell" class="cell-select"><input type="checkbox" data-alarm-select aria-label="Select ${alarm.id}" ${state.selected.has(alarm.id) ? "checked" : ""}></td><td role="cell" class="cell-status">${statusMarkup(alarm.status)}</td><td role="cell" class="cell-age"><time>${alarm.age}</time></td><td role="cell" class="who-cell"><strong>${alarm.person}</strong><span>${alarm.room}</span></td><td role="cell" class="cell-id"><a class="alarm-id" href="alarm.html">${alarm.id}</a></td><td role="cell" class="cell-source">${alarm.source}</td><td role="cell" class="cell-severity">${severityMarkup(alarm.severity)}</td><td role="cell" class="cell-owner${alarm.owner === "Unassigned" ? " is-unassigned" : ""}">${alarm.owner}</td><td role="cell" class="chevron-cell"><span class="row-link" aria-hidden="true">›</span></td>`; row.classList.toggle("is-selected", state.selected.has(alarm.id)); target.append(row); });
    document.querySelector("[data-demo-empty]")?.toggleAttribute("hidden", visible.length !== 0);
  }
  function renderCounts() { Object.keys(statusNames).forEach((status) => { const count = alarms.filter((alarm) => alarm.status === status).length; document.querySelectorAll(`[data-count="${status}"]`).forEach((node) => { node.textContent = String(count); }); document.querySelectorAll(`[data-status="${status}"]`).forEach((node) => node.classList.toggle("is-lit", count > 0 && (status === "triggered" || status === "acknowledged"))); if (status === "triggered") document.querySelectorAll("[data-triggered-nav-count]").forEach((node) => { node.textContent = String(count); }); }); }
  function renderSelection() { const bar = document.querySelector("[data-demo-selection-bar]"); document.querySelectorAll("[data-selection-count]").forEach((node) => { node.textContent = String(state.selected.size); }); if (bar) bar.hidden = state.selected.size === 0; }
  function render() { renderRows(); renderCounts(); renderSelection(); }
  function exportFixture() {
    const columns = ["id", "person", "room", "source", "severity", "status", "owner", "age"];
    const quote = (value) => `"${String(value).replaceAll('"', '""')}"`;
    const rows = [columns, ...alarms.map((alarm) => columns.map((column) => alarm[column]))];
    const blob = new Blob([rows.map((row) => row.map(quote).join(",")).join("\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "escalane-fixture-alarms.csv";
    link.click();
    URL.revokeObjectURL(url);
    announce("Sanitized fixture CSV prepared locally. No production data was accessed.");
  }
  function appendTimeline(alarm, detail) { alarm.timeline.unshift(["Demo now", detail]); }
  function transition(alarm, status, detail) { if (!alarm || alarm.status === status) return; alarm.status = status; alarm.owner = status === "acknowledged" ? "Demo Operator" : alarm.owner; appendTimeline(alarm, detail || `${statusNames[status]} in this browser only`); render(); if (state.activeId === alarm.id) renderDrawer(alarm); }
  function escalationSteps(alarm) {
    if (alarm.status === "triggered") return [["complete", "Fixture intake", `${alarm.source} signal accepted`], ["active", "Primary responder", "Current simulated step"], ["pending", "Duty coordinator", "Waiting in fixture queue"]];
    if (alarm.status === "acknowledged") return [["complete", "Fixture intake", `${alarm.source} signal accepted`], ["complete", "Primary responder", `Acknowledged by ${alarm.owner}`], ["pending", "Duty coordinator", "Not required yet"]];
    return [["complete", "Fixture intake", `${alarm.source} signal accepted`], ["complete", "Primary responder", "Fixture response recorded"], ["inactive", "Duty coordinator", `${statusNames[alarm.status]} before this step`]];
  }
  function renderDrawer(alarm) {
    const drawer = document.querySelector("[data-demo-drawer]"); if (!drawer || !alarm) return;
    drawer.querySelector("[data-drawer-id]").textContent = alarm.id; drawer.querySelector("[data-drawer-title]").textContent = `${alarm.person} · ${alarm.room}`; drawer.querySelector("[data-drawer-age]").textContent = alarm.age;
    const status = drawer.querySelector("[data-drawer-status]"); status.className = `status status-${alarm.status}`; status.replaceChildren(); const dot = document.createElement("span"); dot.className = "status-dot"; dot.setAttribute("aria-hidden", "true"); status.append(dot, document.createTextNode(statusNames[alarm.status]));
    const severity = drawer.querySelector("[data-drawer-severity]"); severity.className = `severity severity-${alarm.severity.toLowerCase()}`; severity.textContent = alarm.severity;
    const context = drawer.querySelector("[data-drawer-context]"); context.replaceChildren(); [["Created", alarm.created], ["Source", alarm.source], ["Severity", alarm.severity], ["Owner", alarm.owner], ["Fixture scope", "Sanitized fictional data"]].forEach(([label, value]) => { const item = document.createElement("div"); const term = document.createElement("dt"); const definition = document.createElement("dd"); term.textContent = label; definition.textContent = value; item.append(term, definition); context.append(item); });
    const escalation = drawer.querySelector("[data-drawer-escalation]"); escalation.replaceChildren(); escalationSteps(alarm).forEach(([stepState, title, detail]) => { const item = document.createElement("li"); item.className = `is-${stepState}`; const heading = document.createElement("strong"); const description = document.createElement("span"); heading.textContent = title; description.textContent = detail; item.append(heading, description); escalation.append(item); });
    const timeline = drawer.querySelector("[data-drawer-timeline]"); timeline.replaceChildren(); alarm.timeline.forEach(([time, detail]) => { const item = document.createElement("li"); const when = document.createElement("time"); const description = document.createElement("span"); when.textContent = time; description.textContent = detail; item.append(when, description); timeline.append(item); });
  }
  function openDrawer(id, opener) { const drawer = document.querySelector("[data-demo-drawer]"); const alarm = alarmById(id); if (!drawer || !alarm) return; state.activeId = id; state.opener = opener; renderDrawer(alarm); if (!drawer.open) drawer.showModal(); drawer.querySelector("[data-drawer-title]").focus(); }
  function closeDrawer() { const drawer = document.querySelector("[data-demo-drawer]"); if (drawer?.open) drawer.close(); }
  function initializeWorklist() {
    if (!document.querySelector("[data-demo-rows]")) return; render();
    document.querySelector("[data-demo-search]")?.addEventListener("input", (event) => { state.query = event.currentTarget.value.trim().toLowerCase(); renderRows(); });
    document.querySelector("[data-demo-sort]")?.addEventListener("change", (event) => { state.sort = event.currentTarget.value; renderRows(); });
    document.querySelector("[data-demo-export]")?.addEventListener("click", exportFixture);
    document.querySelector("[data-status-filter]")?.addEventListener("click", (event) => { const button = event.target.closest("button[data-status]"); if (!button) return; state.status = state.status === button.dataset.status ? "" : button.dataset.status; document.querySelectorAll("[data-status]").forEach((node) => node.setAttribute("aria-pressed", String(node.dataset.status === state.status && Boolean(state.status)))); renderRows(); });
    document.querySelector("[data-severity-filter]")?.addEventListener("change", (event) => { if (!(event.target instanceof HTMLInputElement)) return; state.severity = event.target.value; renderRows(); });
    document.querySelector("[data-demo-rows]").addEventListener("change", (event) => { const checkbox = event.target.closest("[data-alarm-select]"); if (!checkbox) return; const id = checkbox.closest("tr").dataset.alarmId; checkbox.checked ? state.selected.add(id) : state.selected.delete(id); render(); });
    document.querySelector("[data-demo-rows]").addEventListener("click", (event) => { if (event.target.closest("input")) return; const row = event.target.closest("tr[data-alarm-id]"); if (!row) return; event.preventDefault(); openDrawer(row.dataset.alarmId, row); });
    document.querySelector("[data-demo-rows]").addEventListener("keydown", (event) => { const row = event.target.closest("tr[data-alarm-id]"); if (row && event.key === "Enter" && !event.target.matches("input")) { event.preventDefault(); openDrawer(row.dataset.alarmId, row); } });
    document.querySelectorAll("[data-bulk-transition]").forEach((button) => button.addEventListener("click", () => { const selected = [...state.selected]; if (!selected.length) return; selected.forEach((id) => transition(alarmById(id), button.dataset.bulkTransition, `${statusNames[button.dataset.bulkTransition]} with local bulk fixture action`)); state.selected.clear(); render(); announce(`${statusNames[button.dataset.bulkTransition]} ${selected.length} fixture alarm${selected.length === 1 ? "" : "s"} locally.`); }));
    document.querySelector("[data-clear-selection]")?.addEventListener("click", () => { state.selected.clear(); render(); });
    const drawer = document.querySelector("[data-demo-drawer]"); drawer?.addEventListener("click", (event) => { if (event.target === drawer || event.target.closest("[data-drawer-close]")) closeDrawer(); }); drawer?.addEventListener("close", () => {
      const fallback = state.activeId ? document.querySelector(`[data-alarm-id="${state.activeId}"]`) : null;
      const focusTarget = state.opener?.isConnected ? state.opener : fallback;
      state.activeId = null;
      state.opener = null;
      focusTarget?.focus();
    });
    document.querySelectorAll("[data-drawer-transition]").forEach((button) => button.addEventListener("click", () => { const alarm = alarmById(state.activeId); transition(alarm, button.dataset.drawerTransition); announce(`${statusNames[button.dataset.drawerTransition]} was simulated locally. No request was sent.`); }));
    document.querySelector("[data-drawer-note]")?.addEventListener("submit", (event) => { event.preventDefault(); const note = event.currentTarget.elements.note.value.trim(); const alarm = alarmById(state.activeId); if (!note || !alarm) return; appendTimeline(alarm, `Fixture note: ${note}`); event.currentTarget.reset(); renderDrawer(alarm); announce("Fixture note added in this browser only."); });
  }
  function initializeSharedPages() { document.addEventListener("keydown", (event) => { if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { const search = document.querySelector("[data-demo-search]"); if (search) { event.preventDefault(); search.focus(); search.select(); } } }); }
  function initializeDetail() { const detail = document.querySelector("[data-demo-detail]"); if (!detail) return; document.querySelectorAll("[data-detail-transition]").forEach((button) => button.addEventListener("click", () => { const status = button.dataset.detailTransition; const badge = detail.querySelector("[data-detail-status]"); detail.className = `detail-heading alarm-state-${status}`; badge.className = `status status-${status}`; badge.querySelector("[data-status-text]").textContent = statusNames[status]; const item = document.createElement("li"); const when = document.createElement("time"); const description = document.createElement("span"); when.textContent = "Demo now"; description.textContent = `${statusNames[status]} in this browser only`; item.append(when, description); document.querySelector("[data-demo-timeline]").prepend(item); announce(`${statusNames[status]} was simulated locally. No Escalane API command was sent.`); })); document.querySelector("[data-demo-note]")?.addEventListener("submit", (event) => { event.preventDefault(); const note = event.currentTarget.elements.note.value.trim(); if (!note) return; const item = document.createElement("li"); const when = document.createElement("time"); const description = document.createElement("span"); when.textContent = "Demo now"; description.textContent = `Fixture note: ${note}`; item.append(when, description); document.querySelector("[data-demo-timeline]").prepend(item); event.currentTarget.reset(); announce("Fixture note added in this browser only."); }); }
  function initializeResponder() { const form = document.querySelector("[data-demo-responder]"); if (!form) return; form.addEventListener("submit", (event) => { event.preventDefault(); form.classList.add("is-hidden"); document.querySelector("[data-responder-complete]").classList.remove("is-hidden"); const card = document.querySelector("[data-demo-responder-card]"); const lamp = document.querySelector("[data-responder-status]"); if (card && lamp) { card.classList.replace("alarm-state-triggered", "alarm-state-acknowledged"); lamp.className = "status status-acknowledged"; lamp.querySelector("[data-status-text]").textContent = statusNames.acknowledged; } announce("Acknowledgement simulated in this browser. No capability token or command was sent."); }); }
  function initializeSimulation() { document.querySelector("[data-demo-clear]")?.addEventListener("click", () => { document.querySelectorAll("[data-simulation-row]").forEach((row) => row.remove()); document.querySelector("[data-simulation-empty]").classList.remove("is-hidden"); announce("The fixture delivery list was cleared in this browser only."); }); }
  initializeSharedPages(); initializeWorklist(); initializeDetail(); initializeResponder(); initializeSimulation();
})();
