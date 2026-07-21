"use strict";

/** @typedef {{host:string, path:string, orig_path:string|null, mode:'commit'|'gitignore', untracked:boolean}} Selection */

let machines = [];
/** @type {Map<string, Selection>} */
const selections = new Map();
/** @type {Set<string>} */
const expanded = new Set();
/** @type {Map<string, string>} */
const diffCache = new Map();
/** @type {Map<string, HTMLElement>} */
let groupElements = new Map();

const machinesListEl = document.getElementById("machinesList");
const machineGroupsEl = document.getElementById("machineGroups");
const refreshStatusEl = document.getElementById("refreshStatus");
const refreshBtn = document.getElementById("refreshBtn");
const selectAllBtn = document.getElementById("selectAllBtn");
const selectNoneBtn = document.getElementById("selectNoneBtn");
const selectionSummaryEl = document.getElementById("selectionSummary");
const commitMessageEl = document.getElementById("commitMessage");
const commitBtn = document.getElementById("commitBtn");
const commitResultsEl = document.getElementById("commitResults");

function key(host, path) {
  return host + " " + path;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatIsoLocal(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return (
    date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate()) +
    " " + pad(date.getHours()) + ":" + pad(date.getMinutes()) + ":" + pad(date.getSeconds())
  );
}

function classifyStatus(status) {
  if (status.includes("?")) return { label: "new", cls: "st-new" };
  if (status.includes("R")) return { label: "renamed", cls: "st-renamed" };
  if (status.includes("D")) return { label: "deleted", cls: "st-deleted" };
  if (status.includes("A")) return { label: "added", cls: "st-new" };
  if (status.includes("M")) return { label: "modified", cls: "st-modified" };
  return { label: status.trim() || "changed", cls: "st-modified" };
}

async function loadStatus() {
  refreshBtn.disabled = true;
  refreshStatusEl.textContent = "Refreshing…";
  try {
    const res = await fetch("/api/status");
    const data = await res.json();
    if (data.error) {
      refreshStatusEl.textContent = "Error: " + data.error;
      return;
    }
    machines = data.machines;
    diffCache.clear();
    pruneSelections();
    renderMachines();
    renderGroups();
    refreshStatusEl.textContent = "Updated " + formatIsoLocal(new Date());
  } catch (err) {
    refreshStatusEl.textContent = "Refresh failed: " + err;
  } finally {
    refreshBtn.disabled = false;
  }
}

function pruneSelections() {
  const valid = new Set();
  for (const m of machines) {
    for (const c of m.changes) valid.add(key(m.host, c.path));
  }
  for (const k of Array.from(selections.keys())) {
    if (!valid.has(k)) selections.delete(k);
  }
}

function machineHasGroup(m) {
  return (m.reachable && m.changes.length > 0) || (m.reachable && m.error) || !m.reachable;
}

function renderMachines() {
  machinesListEl.innerHTML = "";
  for (const m of machines) {
    const li = document.createElement("li");
    li.className = "machine-item" + (!m.reachable ? " unreachable" : m.changes.length === 0 ? " clean" : "");
    const displayName = m.hostname || m.host;
    let statusText;
    if (!m.reachable) {
      statusText = m.error || "unreachable";
    } else if (m.error) {
      statusText = m.error;
    } else if (m.changes.length === 0) {
      statusText = "clean";
    } else {
      statusText = m.changes.length + " change" + (m.changes.length === 1 ? "" : "s");
    }
    li.innerHTML =
      `<div class="m-name">${escapeHtml(displayName)}</div>` +
      `<div class="m-os">${escapeHtml(m.os || "")}</div>` +
      `<div class="m-status">${escapeHtml(statusText)}</div>`;
    if (machineHasGroup(m)) {
      li.classList.add("clickable");
      li.addEventListener("click", () => scrollToHost(m.host));
    }
    machinesListEl.appendChild(li);
  }
}

function scrollToHost(host) {
  const el = groupElements.get(host);
  if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
}

/** @typedef {{name:string, path:string, children:Map<string,TreeNode>, files:object[]}} TreeNode */

function makeTreeNode(name, path) {
  return { name, path, children: new Map(), files: [] };
}

/** Builds a directory tree from a flat list of changes (each with .path). */
function buildTree(changes) {
  const root = makeTreeNode("", "");
  for (const c of changes) {
    const parts = c.path.split("/").filter(Boolean);
    let node = root;
    for (let i = 0; i < parts.length - 1; i++) {
      const seg = parts[i];
      if (!node.children.has(seg)) {
        const childPath = node.path ? node.path + "/" + seg : seg;
        node.children.set(seg, makeTreeNode(seg, childPath));
      }
      node = node.children.get(seg);
    }
    node.files.push(c);
  }
  return root;
}

/** Collapses chains of directories that hold no files of their own and
 * have exactly one subdirectory, e.g. "ssl" -> "certs" becomes "ssl/certs". */
function simplifyTree(node) {
  const newChildren = new Map();
  for (const [k, child] of node.children) {
    newChildren.set(k, simplifyTree(child));
  }
  node.children = newChildren;
  if (node.files.length === 0 && node.children.size === 1) {
    const [onlyChild] = node.children.values();
    return {
      name: node.name ? node.name + "/" + onlyChild.name : onlyChild.name,
      path: onlyChild.path,
      children: onlyChild.children,
      files: onlyChild.files,
    };
  }
  return node;
}

/** All changes contained in this node and its descendant directories. */
function collectChanges(node) {
  let result = node.files.slice();
  for (const child of node.children.values()) {
    result = result.concat(collectChanges(child));
  }
  return result;
}

function toggleDir(m, node, checked) {
  for (const c of collectChanges(node)) {
    const k = key(m.host, c.path);
    if (checked) {
      if (!selections.has(k)) {
        selections.set(k, { host: m.host, path: c.path, untracked: c.status.includes("?"), mode: "commit" });
      }
    } else {
      selections.delete(k);
    }
  }
  renderGroups();
}

function renderDirRow(m, node, depth) {
  const row = document.createElement("div");
  row.className = "dir-row";
  row.style.paddingLeft = `${0.75 + depth * 1.25}rem`;
  const count = collectChanges(node).length;
  row.innerHTML =
    `<span class="dir-name">${escapeHtml(node.name)}/</span>` +
    `<button type="button" class="btn-link dir-select-all">Select all</button>` +
    `<button type="button" class="btn-link dir-select-none">Select none</button>` +
    `<span class="dir-count">${count} change${count === 1 ? "" : "s"}</span>`;
  row.querySelector(".dir-select-all").addEventListener("click", () => toggleDir(m, node, true));
  row.querySelector(".dir-select-none").addEventListener("click", () => toggleDir(m, node, false));
  return row;
}

function renderTreeInto(container, m, node, depth) {
  const dirs = Array.from(node.children.values()).sort((a, b) => a.name.localeCompare(b.name));
  const files = node.files.slice().sort((a, b) => a.path.localeCompare(b.path));

  for (const dir of dirs) {
    container.appendChild(renderDirRow(m, dir, depth));
    renderTreeInto(container, m, dir, depth + 1);
  }
  for (const c of files) {
    container.appendChild(renderChangeRow(m, c, depth));
    const k = key(m.host, c.path);
    if (expanded.has(k)) {
      container.appendChild(renderDiffRow(m, c, depth));
    }
  }
}

function renderGroups() {
  machineGroupsEl.innerHTML = "";
  groupElements = new Map();
  const groups = machines.filter(machineHasGroup);

  if (groups.length === 0) {
    machineGroupsEl.innerHTML = '<div class="empty-hint">No uncommitted changes on any machine.</div>';
    updateSelectionSummary();
    return;
  }

  for (const m of groups) {
    const group = document.createElement("div");
    group.className = "group";
    groupElements.set(m.host, group);

    const header = document.createElement("div");
    header.className = "group-header";
    const displayName = m.hostname || m.host;
    header.innerHTML =
      `<span class="g-host">${escapeHtml(displayName)}</span>` +
      (m.changes.length > 0
        ? `<button type="button" class="btn-link host-select-all">Select all</button>` +
          `<button type="button" class="btn-link host-select-none">Select none</button>`
        : "") +
      `<span class="g-os">${escapeHtml(m.os || "")}</span>` +
      (m.error ? `<span class="g-error">${escapeHtml(m.error)}</span>` : "") +
      `<span class="g-count">${m.changes.length} change${m.changes.length === 1 ? "" : "s"}</span>`;

    const hostSelectAll = header.querySelector(".host-select-all");
    if (hostSelectAll) hostSelectAll.addEventListener("click", () => toggleHost(m, true));
    const hostSelectNone = header.querySelector(".host-select-none");
    if (hostSelectNone) hostSelectNone.addEventListener("click", () => toggleHost(m, false));

    group.appendChild(header);

    if (m.changes.length > 0) {
      const tree = buildTree(m.changes);
      for (const [k, child] of tree.children) {
        tree.children.set(k, simplifyTree(child));
      }
      renderTreeInto(group, m, tree, 0);
    }

    machineGroupsEl.appendChild(group);
  }

  updateSelectionSummary();
}

function renderChangeRow(m, c, depth) {
  const k = key(m.host, c.path);
  const untracked = c.status.includes("?");
  const info = classifyStatus(c.status);
  const sel = selections.get(k);

  const row = document.createElement("div");
  row.className = "change-row";
  row.dataset.host = m.host;
  row.dataset.path = c.path;
  row.style.paddingLeft = `${0.75 + depth * 1.25}rem`;

  const baseName = c.path.slice(c.path.lastIndexOf("/") + 1);
  const pathHtml = c.orig_path
    ? `<span class="orig">${escapeHtml(c.orig_path)} →</span> ${escapeHtml(baseName)}`
    : escapeHtml(baseName);

  row.innerHTML =
    `<input type="checkbox" class="row-check" ${sel ? "checked" : ""}>` +
    `<button class="disclosure" data-action="toggle">${expanded.has(k) ? "▾" : "▸"}</button>` +
    `<span class="status-badge ${info.cls}">${info.label}</span>` +
    `<span class="path" title="${escapeHtml(c.path)}">${pathHtml}</span>` +
    (untracked
      ? `<select class="mode-select" data-action="mode">
           <option value="commit" ${!sel || sel.mode === "commit" ? "selected" : ""}>Commit</option>
           <option value="gitignore" ${sel && sel.mode === "gitignore" ? "selected" : ""}>Add to .gitignore</option>
         </select>`
      : "");

  row.querySelector(".row-check").addEventListener("change", (e) => {
    if (e.target.checked) {
      const modeSelect = row.querySelector(".mode-select");
      selections.set(k, {
        host: m.host,
        path: c.path,
        untracked,
        mode: modeSelect ? modeSelect.value : "commit",
      });
    } else {
      selections.delete(k);
    }
    updateSelectionSummary();
  });

  row.querySelector('[data-action="toggle"]').addEventListener("click", () => {
    if (expanded.has(k)) {
      expanded.delete(k);
    } else {
      expanded.add(k);
    }
    renderGroups();
  });

  const modeSelect = row.querySelector('[data-action="mode"]');
  if (modeSelect) {
    modeSelect.addEventListener("change", (e) => {
      const existing = selections.get(k);
      if (existing) {
        existing.mode = e.target.value;
      }
    });
  }

  return row;
}

function renderDiffRow(m, c, depth) {
  const k = key(m.host, c.path);
  const row = document.createElement("div");
  row.className = "diff-row";
  const pre = document.createElement("pre");
  pre.style.paddingLeft = `${2.3 + depth * 1.25}rem`;
  pre.textContent = "Loading diff…";
  row.appendChild(pre);

  if (diffCache.has(k)) {
    pre.innerHTML = formatDiff(diffCache.get(k));
  } else {
    fetchDiff(m.host, c.path, c.status.includes("?")).then((text) => {
      diffCache.set(k, text);
      if (expanded.has(k)) pre.innerHTML = formatDiff(text);
    });
  }

  return row;
}

async function fetchDiff(host, path, untracked) {
  try {
    const res = await fetch("/api/diff", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ host, path, untracked }),
    });
    const data = await res.json();
    if (data.error) return "Error fetching diff: " + data.error;
    return data.diff;
  } catch (err) {
    return "Error fetching diff: " + err;
  }
}

function formatDiff(text) {
  return text
    .split("\n")
    .map((line) => {
      let cls = "";
      if (line.startsWith("+++") || line.startsWith("---")) {
        cls = "";
      } else if (line.startsWith("+")) {
        cls = "diff-line-add";
      } else if (line.startsWith("-")) {
        cls = "diff-line-del";
      } else if (line.startsWith("@@")) {
        cls = "diff-line-hunk";
      }
      const escaped = escapeHtml(line);
      return cls ? `<span class="${cls}">${escaped}</span>` : escaped;
    })
    .join("\n");
}

function updateSelectionSummary() {
  const hosts = new Set();
  for (const s of selections.values()) hosts.add(s.host);
  selectionSummaryEl.textContent =
    selections.size === 0
      ? "No files selected"
      : `${selections.size} file${selections.size === 1 ? "" : "s"} selected across ${hosts.size} machine${hosts.size === 1 ? "" : "s"}`;
}

function selectAllVisible() {
  for (const m of machines) {
    for (const c of m.changes) {
      const k = key(m.host, c.path);
      if (!selections.has(k)) {
        selections.set(k, { host: m.host, path: c.path, untracked: c.status.includes("?"), mode: "commit" });
      }
    }
  }
  renderGroups();
}

function selectNone() {
  selections.clear();
  renderGroups();
}

function toggleHost(m, checked) {
  for (const c of m.changes) {
    const k = key(m.host, c.path);
    if (checked) {
      if (!selections.has(k)) {
        selections.set(k, { host: m.host, path: c.path, untracked: c.status.includes("?"), mode: "commit" });
      }
    } else {
      selections.delete(k);
    }
  }
  renderGroups();
}

async function submitCommit() {
  const message = commitMessageEl.value.trim();
  if (!message) {
    commitResultsEl.innerHTML = '<span class="cr-fail">Enter a commit message first.</span>';
    return;
  }
  if (selections.size === 0) {
    commitResultsEl.innerHTML = '<span class="cr-fail">Select at least one file.</span>';
    return;
  }

  /** @type {Record<string, {commit: string[], gitignore: string[]}>} */
  const byHost = {};
  for (const s of selections.values()) {
    if (!byHost[s.host]) byHost[s.host] = { commit: [], gitignore: [] };
    if (s.untracked && s.mode === "gitignore") {
      byHost[s.host].gitignore.push(s.path);
    } else {
      byHost[s.host].commit.push(s.path);
    }
  }

  commitBtn.disabled = true;
  commitResultsEl.textContent = "Committing…";
  try {
    const res = await fetch("/api/commit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, selections: byHost }),
    });
    const data = await res.json();
    if (data.error) {
      commitResultsEl.innerHTML = `<span class="cr-fail">${escapeHtml(data.error)}</span>`;
      return;
    }
    const lines = [];
    for (const [host, r] of Object.entries(data.results)) {
      if (r.ok) {
        lines.push(`<div class="cr-ok">✓ ${escapeHtml(host)}</div>`);
        for (const k of Array.from(selections.keys())) {
          if (k.startsWith(host + " ")) selections.delete(k);
        }
      } else {
        lines.push(`<div class="cr-fail">✗ ${escapeHtml(host)}: ${escapeHtml(r.error || "failed")}</div>`);
      }
    }
    commitResultsEl.innerHTML = lines.join("");
    if (Object.values(data.results).some((r) => r.ok)) {
      commitMessageEl.value = "";
    }
    await loadStatus();
  } catch (err) {
    commitResultsEl.innerHTML = `<span class="cr-fail">${escapeHtml(String(err))}</span>`;
  } finally {
    commitBtn.disabled = false;
  }
}

refreshBtn.addEventListener("click", loadStatus);
selectAllBtn.addEventListener("click", selectAllVisible);
selectNoneBtn.addEventListener("click", selectNone);
commitBtn.addEventListener("click", submitCommit);

loadStatus();
