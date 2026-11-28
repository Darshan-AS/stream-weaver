// Stream Weaver Studio v2 — Core State Store, NDJSON Stream Client, Command Palette & Global Modals

import { icon } from "./icons.js";
import { openSetupWizard } from "./wizard.js";
import { renderHomeTab } from "./home.js";
import { renderStreamsTab } from "./streams.js";
import { renderAioManagerTab } from "./aiomanager.js";
import { renderNuvioTab } from "./nuvio.js";
import { renderKeysTab } from "./keys.js";

const STORAGE_KEY = "stream_weaver_studio_bundle_v1";
const CSRF_TOKEN = document.querySelector('meta[name="sw-csrf-token"]')?.content || "";

const TAB_TITLES = {
  home: "Home & Simulator",
  streams: "AIOStreams & Metadata",
  aiomanager: "AIOManager & Addons",
  nuvio: "Nuvio & Visuals",
  keys: "Keys & Accounts",
};

let appMode = "local";
let bundle = null;
let state = null;
let activeTab = "home";
let activeProfileIdx = 0;
let dismissBanner = false;
let dirty = false;
let pendingEnvEdits = {};
let nuvioProfilesCache = [];
let mutationEpoch = 0;
let saveInFlight = false;
let saveQueued = false;
let saveQueuedQuiet = true;
let activeSavePromise = null;
let pendingPushCount = 0;

function pushStorageKey() {
  const rootKey = state?.tenant?.root || appMode || "default";
  return `sw_pending_push_${rootKey}`;
}

function loadPendingPushCount() {
  try {
    const raw = sessionStorage.getItem(pushStorageKey());
    const parsed = Number(raw);
    pendingPushCount = Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
  } catch (_) {
    pendingPushCount = 0;
  }
}

function setPendingPushCount(n) {
  pendingPushCount = Math.max(0, Number(n) || 0);
  try {
    sessionStorage.setItem(pushStorageKey(), String(pendingPushCount));
  } catch (_) {}
}

function createEmptyChangedSections() {
  return {
    debrid: false,
    debridServices: false,
    packs: false,
    customStreamAddons: false,
    streamFormatter: false,
    streamPreferences: false,
    advancedStreamFilters: false,
    dualSubtitlePairs: false,
    metadataPower: false,
    aiomanagerPower: false,
    profile: false,
  };
}

let changedSections = createEmptyChangedSections();

function loadSavedBundle() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (_) {
    return null;
  }
}

function persistBundle(nextBundle) {
  if (!nextBundle) return;
  bundle = nextBundle;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(nextBundle));
  } catch (_) {}
}

// ---------------------------------------------------------------------------
// UI Helpers
// ---------------------------------------------------------------------------
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

function makeSwitch(label, checked, onChange) {
  const lbl = document.createElement("label");
  lbl.className = "switch";
  const chk = document.createElement("input");
  chk.type = "checkbox";
  chk.checked = Boolean(checked);
  chk.onchange = () => onChange(chk.checked);
  lbl.append(chk, document.createTextNode(label));
  return lbl;
}

function makeParamInput(param, boundMap, onChange) {
  const cur = boundMap && param.name in boundMap ? boundMap[param.name] : param.default;
  if (param.type === "bool") {
    return makeSwitch(param.label || param.name, Boolean(cur), onChange);
  }
  const lbl = document.createElement("label");
  lbl.className = "field";
  lbl.textContent = param.label || param.name;
  if (Array.isArray(param.options) && param.options.length) {
    const sel = document.createElement("select");
    param.options.forEach((opt, i) => {
      const o = document.createElement("option");
      o.value = String(i);
      o.textContent = opt.label;
      if (opt.value === cur) o.selected = true;
      sel.appendChild(o);
    });
    sel.onchange = () => onChange(param.options[Number(sel.value)].value);
    lbl.appendChild(sel);
  } else if (param.type === "int") {
    const inp = document.createElement("input");
    inp.type = "number";
    inp.value = cur != null ? String(cur) : "";
    inp.onchange = () => onChange(Number(inp.value));
    lbl.appendChild(inp);
  } else {
    const inp = document.createElement("input");
    inp.type = "text";
    inp.value = cur != null ? String(cur) : "";
    inp.onchange = () => onChange(inp.value);
    lbl.appendChild(inp);
  }
  return lbl;
}

function renderColoredDiff(unifiedDiff) {
  const pre = document.createElement("div");
  pre.className = "diff-block";
  String(unifiedDiff || "").split("\n").forEach(line => {
    const div = document.createElement("div");
    if (line.startsWith("+") && !line.startsWith("+++")) div.className = "diff-add";
    else if (line.startsWith("-") && !line.startsWith("---")) div.className = "diff-del";
    else if (line.startsWith("@@")) div.className = "diff-hunk";
    div.textContent = line;
    pre.appendChild(div);
  });
  return pre;
}

function closeSidebarMobile() {
  document.getElementById("sidebar")?.classList.remove("open");
  const bd = document.getElementById("sidebar-backdrop");
  if (bd) bd.hidden = true;
}

// ---------------------------------------------------------------------------
// Log Drawer & Clipboard
// ---------------------------------------------------------------------------
function showLog(title, lines = [], isError = false) {
  const drawer = document.getElementById("log-drawer");
  const titleEl = document.getElementById("log-drawer-title");
  const stepsEl = document.getElementById("log-drawer-steps");
  drawer.hidden = false;
  drawer.classList.toggle("error", isError);
  titleEl.innerHTML = `${icon(isError ? "alert" : "check", 14)} <span>${escapeHtml(title)}</span>`;
  stepsEl.innerHTML = "";
  (lines || []).forEach(line => {
    const d = document.createElement("div");
    d.textContent = line;
    stepsEl.appendChild(d);
  });
}

function appendLogStep(stepText) {
  const drawer = document.getElementById("log-drawer");
  const stepsEl = document.getElementById("log-drawer-steps");
  drawer.hidden = false;
  const d = document.createElement("div");
  d.textContent = `> ${stepText}`;
  stepsEl.appendChild(d);
  stepsEl.scrollTop = stepsEl.scrollHeight;
}

function copyText(val, label) {
  if (!val) return;
  navigator.clipboard.writeText(val).then(
    () => showLog(`Copied ${label} to clipboard.`, []),
    () => showLog(`${label}: ${val}`, [])
  );
}

// ---------------------------------------------------------------------------
// API & NDJSON Streaming Client
// ---------------------------------------------------------------------------
function baseHeaders() {
  return CSRF_TOKEN ? { "X-Studio-Token": CSRF_TOKEN } : {};
}

function withStatelessBundle(bodyObj) {
  if (appMode === "stateless" && bundle) {
    return { ...bodyObj, bundle };
  }
  return bodyObj;
}

function ingestServerPayload(data) {
  if (!data || typeof data !== "object") return;
  if (data.mode) appMode = data.mode;
  if (data.bundle) persistBundle(data.bundle);
  if (data.state) {
    state = data.state;
    if (activeProfileIdx >= (state.profiles || []).length) {
      activeProfileIdx = 0;
    }
  }
}

async function api(path, bodyObj = null) {
  const opts = bodyObj === null
    ? { method: "GET", headers: baseHeaders() }
    : {
        method: "POST",
        headers: { ...baseHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify(withStatelessBundle(bodyObj)),
      };
  const res = await fetch(path, opts);
  const data = await res.json();
  if (!res.ok || data.ok === false) {
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  ingestServerPayload(data);
  return data;
}

async function streamPush(payload, onStep = null) {
  const res = await fetch("/api/push-stream", {
    method: "POST",
    headers: { ...baseHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify(withStatelessBundle(payload)),
  });

  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const errData = await res.json();
      if (errData && errData.error) msg = errData.error;
    } catch (_) {}
    throw new Error(msg);
  }

  if (!res.body || typeof res.body.getReader !== "function") {
    const rawText = await res.text();
    let finalDone = null;
    for (const line of rawText.split("\n")) {
      if (!line.trim()) continue;
      const evt = JSON.parse(line);
      if (evt.type === "step" && onStep) onStep(evt.step);
      else if (evt.type === "error") throw new Error(evt.error || "Push failed");
      else if (evt.type === "done") finalDone = evt;
    }
    if (finalDone) ingestServerPayload(finalDone);
    return finalDone;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let donePayload = null;

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let nlIdx;
    while ((nlIdx = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, nlIdx).trim();
      buffer = buffer.slice(nlIdx + 1);
      if (!line) continue;
      const evt = JSON.parse(line);
      if (evt.type === "step") {
        if (onStep) onStep(evt.step);
      } else if (evt.type === "error" || evt.ok === false) {
        throw new Error(evt.error || "Streaming push failed");
      } else if (evt.type === "done") {
        donePayload = evt;
        ingestServerPayload(evt);
      }
    }
  }
  const trailing = buffer.trim();
  if (trailing) {
    const evt = JSON.parse(trailing);
    if (evt.type === "error" || evt.ok === false) {
      throw new Error(evt.error || "Streaming push failed");
    }
    if (evt.type === "done") {
      donePayload = evt;
      ingestServerPayload(evt);
    }
  }
  return donePayload;
}

// ---------------------------------------------------------------------------
// Patch-Safe Mutation Builder
// ---------------------------------------------------------------------------
function currentProfile() {
  return state && state.profiles && state.profiles[activeProfileIdx]
    ? state.profiles[activeProfileIdx]
    : null;
}

function buildMutationPayload() {
  const p = currentProfile();
  const mut = {
    envUpdates: { ...pendingEnvEdits },
  };

  if (changedSections.debrid && state.debrid) {
    mut.debrid = state.debrid.active;
  }
  if (changedSections.debridServices && state.debridServices) {
    const list = Array.isArray(state.debridServices.available)
      ? state.debridServices.available
      : [];
    mut.debridServices = list.map((item, idx) => ({
      id: item.id,
      enabled: Boolean(item.enabled),
      priority: item.priority ?? (idx + 1),
    }));
  }
  if (changedSections.packs && Array.isArray(state.packs)) {
    mut.enabledPacks = state.packs.filter(pk => pk.enabled).map(pk => pk.id);
  }
  if (changedSections.customStreamAddons && Array.isArray(state.customStreamAddons)) {
    mut.customStreamAddons = state.customStreamAddons;
  }
  if (changedSections.streamFormatter && state.streamFormatter) {
    const fmtId = state.streamFormatter.preset || state.streamFormatter.active || state.streamFormatter.id;
    if (fmtId) mut.streamFormatter = fmtId;
  }
  if (changedSections.streamPreferences && state.streamPreferences) {
    mut.streamPreferences = {
      maxResolution: state.streamPreferences.maxResolution,
      cachedOnly: Boolean(state.streamPreferences.cachedOnly),
      audioLanguages: state.streamPreferences.audioLanguages || ["English"],
    };
  }
  if (changedSections.advancedStreamFilters && state.advancedStreamFilters) {
    mut.advancedStreamFilters = { ...state.advancedStreamFilters };
  }
  if (changedSections.dualSubtitlePairs && state.dualSubtitles && Array.isArray(state.dualSubtitles.pairs)) {
    mut.dualSubtitlePairs = state.dualSubtitles.pairs;
  }
  if (changedSections.metadataPower && state.metadataPower) {
    const mp = state.metadataPower;
    const overridesObj = mp.catalogOverrides || {};
    const mpMut = {
      movieProvider: mp.movieProvider,
      seriesProvider: mp.seriesProvider,
      animeProvider: mp.animeProvider,
      searchEngines: mp.searchEngines,
      aiSearchEnabled: Boolean(mp.aiSearchEnabled),
      aiSearchProvider: mp.aiSearchProvider,
      posterProvider: mp.posterProvider,
      catalogOverrides: Array.isArray(overridesObj) ? overridesObj : Object.values(overridesObj),
    };
    if (mp.animeIdProvider) {
      mpMut.animeIdProvider = mp.animeIdProvider;
    }
    if (mp.posterOptions && typeof mp.posterOptions === "object") {
      mpMut.posterOptions = { ...mp.posterOptions };
    }
    if (mp._rawPosterUrlEdited) {
      mpMut.customPosterUrlPattern = mp.customPosterUrlPattern || "";
    } else if (
      mp.posterProvider === "postersplus"
      && mp.posterCustomizations
      && typeof mp.posterCustomizations === "object"
    ) {
      mpMut.posterCustomizations = { ...mp.posterCustomizations };
    }
    mut.metadataPower = mpMut;
  }
  if (changedSections.aiomanagerPower && state.aiomanagerPower) {
    mut.aiomanagerPower = {
      autopilotEnabled: Boolean(state.aiomanagerPower.autopilotEnabled),
      syncIntervalMinutes: Number(state.aiomanagerPower.syncIntervalMinutes || 60),
    };
  }
  if (changedSections.profile && p) {
    mut.profile = {
      name: p.name,
      displayName: p.displayName,
      emoji: p.emoji,
      accentColor: p.accentColor,
      stremioEnabled: p.stremioEnabled,
      bingecatEnabled: p.bingecatEnabled,
      extraAddons: Array.isArray(p.extraAddons) ? p.extraAddons : undefined,
      nuvio: p.nuvio ? {
        profileId: p.nuvio.profileId || "",
        profileName: p.nuvio.profileName || "",
        platforms: p.nuvio.platforms,
      } : undefined,
      collections: (p.collections || []).map(c =>
        c.ref ? { ref: c.ref, with: c.with || {} } : { raw: c.raw }
      ),
      badgesEnabled: p.badgesEnabled,
      badges: p.badges,
      meta: p.meta,
      settings: p.settings,
    };
  }
  return mut;
}

function clearPendingEdits() {
  pendingEnvEdits = {};
  changedSections = createEmptyChangedSections();
  dirty = false;
}

async function saveChanges(quiet = false) {
  if (saveInFlight) {
    saveQueued = true;
    saveQueuedQuiet = saveQueuedQuiet && quiet;
    if (activeSavePromise) await activeSavePromise;
    return;
  }

  let nextQuiet = quiet;
  const runLoop = async () => {
    while (true) {
      saveInFlight = true;
      const sentEpoch = mutationEpoch;
      const sentEnv = { ...pendingEnvEdits };
      const sentSections = { ...changedSections };
      const mut = buildMutationPayload();
      const hasEnv = mut.envUpdates && Object.values(mut.envUpdates).some(v => String(v || "").trim() !== "");
      const hasOther = Object.keys(mut).some(k => k !== "envUpdates" && mut[k] !== undefined);
      clearPendingEdits();
      try {
        const res = await fetch("/api/mutate", {
          method: "POST",
          headers: { ...baseHeaders(), "Content-Type": "application/json" },
          body: JSON.stringify(withStatelessBundle({ mutation: mut })),
        });
        const data = await res.json();
        if (!res.ok || data.ok === false) {
          throw new Error(data.error || `HTTP ${res.status}`);
        }
        if (hasEnv || hasOther) {
          setPendingPushCount(pendingPushCount + 1);
        }
        if (data.mode) appMode = data.mode;
        if (data.bundle) persistBundle(data.bundle);
        if (data.state && mutationEpoch === sentEpoch && !saveQueued) {
          state = data.state;
          if (activeProfileIdx >= (state.profiles || []).length) {
            activeProfileIdx = 0;
          }
          render();
        }
        if (!nextQuiet) {
          showLog("Saved configuration, synchronized derived catalogs, and updated topology diagrams.", []);
        }
      } catch (err) {
        for (const [k, v] of Object.entries(sentEnv)) {
          if (!(k in pendingEnvEdits)) pendingEnvEdits[k] = v;
        }
        for (const [sec, flag] of Object.entries(sentSections)) {
          if (flag && sec in changedSections) changedSections[sec] = true;
        }
        dirty = true;
        renderHeader();
        showLog(err.message, [], true);
        saveQueued = false;
        saveQueuedQuiet = true;
        break;
      } finally {
        saveInFlight = false;
      }
      if (!saveQueued) break;
      nextQuiet = saveQueuedQuiet;
      saveQueued = false;
      saveQueuedQuiet = true;
    }
  };

  activeSavePromise = runLoop();
  try {
    await activeSavePromise;
  } finally {
    activeSavePromise = null;
  }
}

async function saveSection(section, quiet = true) {
  if (section && section in changedSections) {
    changedSections[section] = true;
  }
  mutationEpoch += 1;
  dirty = true;
  renderHeader();
  await saveChanges(quiet);
}

// ---------------------------------------------------------------------------
// Modal Host & Command Palette + Global Modals
// ---------------------------------------------------------------------------
function openModal(renderFn) {
  const backdrop = document.getElementById("modal-backdrop");
  const root = document.getElementById("modal-root");
  root.className = "modal-dialog";
  root.innerHTML = "";
  backdrop.hidden = false;
  renderFn(root);
}

function closeModal() {
  const backdrop = document.getElementById("modal-backdrop");
  const root = document.getElementById("modal-root");
  backdrop.hidden = true;
  root.innerHTML = "";
}

function openCommandPalette() {
  openModal((dialog) => {
    dialog.classList.add("modal-cmdk");
    let query = "";
    let selectedIdx = 0;

    const getItems = () => {
      const items = [
        { group: "Navigation", label: "Home & Nuvio Simulator", ic: "home", kbd: "1", run: () => { activeTab = "home"; render(); } },
        { group: "Navigation", label: "AIOStreams & AIOMetadata Power", ic: "zap", kbd: "2", run: () => { activeTab = "streams"; render(); } },
        { group: "Navigation", label: "AIOManager & Standalone Addons", ic: "cloud", kbd: "3", run: () => { activeTab = "aiomanager"; render(); } },
        { group: "Navigation", label: "Nuvio Themes, Badges & Operations", ic: "sliders", kbd: "4", run: () => { activeTab = "nuvio"; render(); } },
        { group: "Navigation", label: "Keys, Secrets & Account Logins", ic: "key", kbd: "5", run: () => { activeTab = "keys"; render(); } },
        { group: "Tools & Actions", label: "Launch Day-0 Setup Wizard", ic: "compass", run: () => openSetupWizard(makeCtx()) },
        { group: "Tools & Actions", label: "Run Live Health Doctor", ic: "activity", run: () => openDoctorModal() },
        { group: "Tools & Actions", label: "Inspect Local vs. Remote Drift Diff", ic: "gitDiff", run: () => openDiffModal() },
        { group: "Tools & Actions", label: "Open Raw JSON Escape Hatch", ic: "code", run: () => openRawJsonModal("aiostreams") },
        { group: "Tools & Actions", label: "Create Household Profile", ic: "users", run: () => openNewProfileModal() },
        { group: "Tools & Actions", label: "Save Workspace Changes", ic: "save", run: () => saveChanges(false) },
        { group: "Tools & Actions", label: "Save & Push Live to AIO + Nuvio", ic: "upload", run: () => document.getElementById("btn-push")?.click() },
        { group: "Tools & Actions", label: "Pull Live Configs into Workspace", ic: "download", run: () => document.getElementById("btn-pull")?.click() },
        { group: "Tools & Actions", label: "Export Portable GitOps Zip", ic: "package", run: () => document.getElementById("btn-export")?.click() },
      ];
      (state?.profiles || []).forEach((pr, idx) => {
        items.push({
          group: "Household Profiles",
          label: `Switch to ${pr.displayName} (${pr.name})`,
          ic: "users",
          kbd: idx === activeProfileIdx ? "Active" : "",
          run: () => { activeProfileIdx = idx; render(); },
        });
      });
      const q = query.trim().toLowerCase();
      return q ? items.filter(it => it.label.toLowerCase().includes(q) || it.group.toLowerCase().includes(q)) : items;
    };

    const head = document.createElement("div");
    head.className = "cmdk-head";
    head.innerHTML = icon("search", 15);
    const inp = document.createElement("input");
    inp.type = "text";
    inp.className = "cmdk-input";
    inp.placeholder = "Type a command, tab, tool, or household profile…";
    head.appendChild(inp);
    const escKbd = document.createElement("kbd");
    escKbd.textContent = "ESC";
    head.appendChild(escKbd);

    const listEl = document.createElement("div");
    listEl.className = "cmdk-list";
    dialog.append(head, listEl);

    const paintList = () => {
      const items = getItems();
      if (selectedIdx >= items.length) selectedIdx = Math.max(0, items.length - 1);
      listEl.innerHTML = "";
      if (!items.length) {
        listEl.innerHTML = `<div style="padding:18px;text-align:center;font-size:12.5px;color:var(--muted)">No matching commands.</div>`;
        return;
      }
      let lastGroup = "";
      items.forEach((it, idx) => {
        if (it.group !== lastGroup) {
          lastGroup = it.group;
          const gl = document.createElement("div");
          gl.className = "cmdk-group-lbl";
          gl.textContent = lastGroup;
          listEl.appendChild(gl);
        }
        const btn = document.createElement("button");
        btn.className = "cmdk-item" + (idx === selectedIdx ? " active" : "");
        btn.innerHTML = `
          <span class="cmdk-item-left">${icon(it.ic, 14)} <span>${escapeHtml(it.label)}</span></span>
          ${it.kbd ? `<kbd>${escapeHtml(it.kbd)}</kbd>` : ""}
        `;
        btn.onmouseenter = () => {
          selectedIdx = idx;
          Array.from(listEl.querySelectorAll(".cmdk-item")).forEach((el, j) => el.classList.toggle("active", j === selectedIdx));
        };
        btn.onclick = () => {
          closeModal();
          it.run();
        };
        listEl.appendChild(btn);
      });
    };

    inp.oninput = () => {
      query = inp.value;
      selectedIdx = 0;
      paintList();
    };
    inp.onkeydown = (e) => {
      const items = getItems();
      if (e.key === "ArrowDown" && items.length) {
        e.preventDefault();
        selectedIdx = (selectedIdx + 1) % items.length;
        paintList();
      } else if (e.key === "ArrowUp" && items.length) {
        e.preventDefault();
        selectedIdx = (selectedIdx - 1 + items.length) % items.length;
        paintList();
      } else if (e.key === "Enter" && items[selectedIdx]) {
        e.preventDefault();
        const chosen = items[selectedIdx];
        closeModal();
        chosen.run();
      }
    };

    paintList();
    setTimeout(() => inp.focus(), 10);
  });
}

function openNewProfileModal() {
  const emojis = ["🎬", "🍿", "🧸", "🎮", "🌙", "🎭", "🚀", "💎"];
  const colors = ["#818cf8", "#38bdf8", "#f43f5e", "#22c55e", "#f59e0b", "#ec4899"];
  const formState = {
    displayName: "",
    name: "",
    slugTouched: false,
    emoji: "🍿",
    accentColor: "#818cf8",
    preset: "standard",
    sourceProfile: (state?.profiles?.[0]?.name) || "main",
    nuvioProfileName: "",
    stremioEnabled: false,
    dedicatedMetadataInstance: false,
  };

  openModal((dialog) => {
    const paint = () => {
      dialog.innerHTML = "";
      const head = document.createElement("div");
      head.className = "row-between";
      head.innerHTML = `
        <div>
          <h2>${icon("users", 17)} Create Household Profile</h2>
          <p class="sub" style="margin:3px 0 0">Add a household member with dedicated shelves, Nuvio sub-profile, theme, and optional isolated AIOMetadata instance.</p>
        </div>
      `;
      const xBtn = document.createElement("button");
      xBtn.className = "btn ghost icon-btn";
      xBtn.innerHTML = icon("x", 15);
      xBtn.onclick = closeModal;
      head.appendChild(xBtn);
      dialog.appendChild(head);

      const grid = document.createElement("div");
      grid.className = "grid-equal";
      grid.style.marginTop = "14px";

      const dnLbl = document.createElement("label");
      dnLbl.className = "field"; dnLbl.textContent = "Display Name";
      const dnIn = document.createElement("input");
      dnIn.type = "text"; dnIn.placeholder = "e.g. Kids, Partner, Dad"; dnIn.value = formState.displayName;
      dnIn.oninput = () => {
        formState.displayName = dnIn.value;
        if (!formState.slugTouched) {
          formState.name = dnIn.value.toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 28);
          slugIn.value = formState.name;
        }
      };
      dnLbl.appendChild(dnIn);

      const slugLbl = document.createElement("label");
      slugLbl.className = "field"; slugLbl.textContent = "Profile Slug (a-z, 0-9, -)";
      const slugIn = document.createElement("input");
      slugIn.type = "text"; slugIn.placeholder = "e.g. kids"; slugIn.value = formState.name;
      slugIn.oninput = () => {
        formState.slugTouched = true;
        formState.name = slugIn.value.trim().toLowerCase();
      };
      slugLbl.appendChild(slugIn);
      grid.append(dnLbl, slugLbl);
      dialog.appendChild(grid);

      // Emoji & Accent Color
      const styleRow = document.createElement("div");
      styleRow.className = "grid-equal";
      styleRow.style.marginTop = "12px";

      const emBox = document.createElement("div");
      emBox.innerHTML = `<div style="font-size:12px;font-weight:500;color:var(--text-soft);margin-bottom:6px">Avatar Badge</div>`;
      const emRow = document.createElement("div");
      emRow.className = "chip-row";
      emojis.forEach(em => {
        const b = document.createElement("button");
        b.className = "btn icon-btn" + (formState.emoji === em ? " accent-outline" : "");
        b.textContent = em;
        b.onclick = () => { formState.emoji = em; paint(); };
        emRow.appendChild(b);
      });
      emBox.appendChild(emRow);

      const colBox = document.createElement("div");
      colBox.innerHTML = `<div style="font-size:12px;font-weight:500;color:var(--text-soft);margin-bottom:6px">Accent Color</div>`;
      const colRow = document.createElement("div");
      colRow.className = "chip-row";
      colors.forEach(clr => {
        const b = document.createElement("button");
        b.className = "btn icon-btn" + (formState.accentColor === clr ? " accent-outline" : "");
        b.innerHTML = `<span class="swatch-dot" style="background:${clr}"></span>`;
        b.onclick = () => { formState.accentColor = clr; paint(); };
        colRow.appendChild(b);
      });
      colBox.appendChild(colRow);
      styleRow.append(emBox, colBox);
      dialog.appendChild(styleRow);

      // Preset Picker
      const presetBox = document.createElement("div");
      presetBox.className = "rows-box";
      presetBox.innerHTML = `<div style="font-size:12.5px;font-weight:600;margin-bottom:8px">Starter Collection Preset</div>`;
      const pCards = document.createElement("div");
      pCards.className = "cards-grid-sm";
      [
        ["standard", "Standard", "All 7 curated discovery, studio & genre collections"],
        ["minimal", "Minimal", "Streamlined Discovery, Streaming Services & Genres"],
        ["kids", "Kids & Family", "Animation, Pixar/Ghibli & family-safe shelves (no horror)"],
        ["clone", "Clone Existing", "Copy shelves & settings from an existing household profile"],
      ].forEach(([pid, title, desc]) => {
        const c = document.createElement("div");
        c.className = "pack-card" + (formState.preset === pid ? " on" : "");
        c.style.cursor = "pointer";
        c.onclick = () => { formState.preset = pid; paint(); };
        c.innerHTML = `
          <div class="row-between">
            <strong>${escapeHtml(title)}</strong>
            ${formState.preset === pid ? `<span class="pill accent">Selected</span>` : ""}
          </div>
          <div style="font-size:11.5px;color:var(--muted)">${escapeHtml(desc)}</div>
        `;
        pCards.appendChild(c);
      });
      presetBox.appendChild(pCards);

      if (formState.preset === "clone") {
        const srcL = document.createElement("label");
        srcL.className = "field";
        srcL.style.marginTop = "10px";
        srcL.textContent = "Source Profile to Clone";
        const srcSel = document.createElement("select");
        (state.profiles || []).forEach(pr => {
          const o = document.createElement("option");
          o.value = pr.name;
          o.textContent = `${pr.displayName} (${pr.name})`;
          if (formState.sourceProfile === pr.name) o.selected = true;
          srcSel.appendChild(o);
        });
        srcSel.onchange = () => { formState.sourceProfile = srcSel.value; };
        srcL.appendChild(srcSel);
        presetBox.appendChild(srcL);
      }
      dialog.appendChild(presetBox);

      // Nuvio target & Dedicated AIOMetadata Instance
      const optsBox = document.createElement("div");
      optsBox.className = "rows-box";
      const nuvLbl = document.createElement("label");
      nuvLbl.className = "field";
      nuvLbl.style.marginBottom = "10px";
      nuvLbl.textContent = "Target Nuvio Profile Name (Optional — matches sub-profile on Nuvio account)";
      const nuvIn = document.createElement("input");
      nuvIn.type = "text";
      nuvIn.placeholder = "Defaults to Display Name or Profile #1";
      nuvIn.value = formState.nuvioProfileName;
      nuvIn.oninput = () => { formState.nuvioProfileName = nuvIn.value; };
      nuvLbl.appendChild(nuvIn);
      optsBox.appendChild(nuvLbl);

      const chkStack = document.createElement("div");
      chkStack.style.display = "flex";
      chkStack.style.flexDirection = "column";
      chkStack.style.gap = "9px";
      chkStack.appendChild(
        makeSwitch("Dedicated AIOMetadata Instance (creates isolated aiometadata.<profile>.json)", formState.dedicatedMetadataInstance, (v) => {
          formState.dedicatedMetadataInstance = v;
        })
      );
      chkStack.appendChild(
        makeSwitch("Also Sync Addons to Stremio", formState.stremioEnabled, (v) => {
          formState.stremioEnabled = v;
        })
      );
      optsBox.appendChild(chkStack);
      dialog.appendChild(optsBox);

      const foot = document.createElement("div");
      foot.className = "row-between";
      foot.style.marginTop = "16px";
      const cancelBtn = document.createElement("button");
      cancelBtn.className = "btn";
      cancelBtn.textContent = "Cancel";
      cancelBtn.onclick = closeModal;

      const createBtn = document.createElement("button");
      createBtn.className = "btn primary";
      createBtn.innerHTML = `${icon("plus", 14)} <span>Create Profile</span>`;
      createBtn.onclick = async () => {
        if (!formState.name.trim()) {
          showLog("Enter a profile name / slug.", [], true);
          return;
        }
        createBtn.disabled = true;
        try {
          await api("/api/profile", {
            action: "create",
            name: formState.name.trim(),
            displayName: formState.displayName.trim() || formState.name.trim(),
            preset: formState.preset,
            sourceProfile: formState.sourceProfile,
            dedicatedMetadataInstance: formState.dedicatedMetadataInstance,
            emoji: formState.emoji,
            accentColor: formState.accentColor,
            nuvioProfileName: formState.nuvioProfileName.trim(),
            stremioEnabled: formState.stremioEnabled,
          });
          const idx = (state.profiles || []).findIndex(p => p.name === formState.name.trim());
          if (idx >= 0) activeProfileIdx = idx;
          setPendingPushCount(pendingPushCount + 1);
          closeModal();
          render();
          showLog(`Created household profile "${formState.name.trim()}".`, []);
        } catch (err) {
          showLog(err.message, [], true);
          createBtn.disabled = false;
        }
      };
      foot.append(cancelBtn, createBtn);
      dialog.appendChild(foot);
    };

    paint();
  });
}

async function deleteProfile(profName) {
  if ((state?.profiles || []).length <= 1) return;
  if (!window.confirm(`Delete household profile "${profName}"?`)) return;
  try {
    await api("/api/profile", { action: "delete", name: profName });
    activeProfileIdx = 0;
    setPendingPushCount(pendingPushCount + 1);
    render();
    showLog(`Deleted profile "${profName}".`, []);
  } catch (err) {
    showLog(err.message, [], true);
  }
}

function openRawJsonModal(initialDocKey = "aiostreams") {
  let activeDoc = initialDocKey;
  let availableDocs = [
    "aiostreams",
    "aiometadata",
    "aiomanager",
    "tenant",
    ...(state?.profiles || []).map(p => `profile:${p.name}`),
  ];

  openModal((dialog) => {
    dialog.classList.add("modal-wide");

    const loadAndRender = async () => {
      dialog.innerHTML = `<div style="padding:20px;color:var(--muted)">Loading <code>${escapeHtml(activeDoc)}</code>…</div>`;
      try {
        const res = await api("/api/raw-doc", { action: "get", doc: activeDoc });
        if (Array.isArray(res.availableDocs) && res.availableDocs.length) {
          availableDocs = res.availableDocs.map(d => typeof d === "string" ? d : d.key || d.doc || d.id);
        }
        renderEditor(res.content || "{}");
      } catch (err) {
        dialog.innerHTML = `<div style="padding:20px;color:var(--danger)">Failed to load ${escapeHtml(activeDoc)}: ${escapeHtml(err.message)}</div>`;
      }
    };

    const renderEditor = (initialText) => {
      dialog.innerHTML = "";
      const head = document.createElement("div");
      head.className = "row-between";
      head.innerHTML = `
        <div>
          <h2>${icon("code", 17)} Raw JSON Escape Hatch</h2>
          <p class="sub" style="margin:3px 0 0">Validated against Stream Weaver schemas on save. Raw secrets pasted into service configs are automatically harvested into <code>.env</code> and scrubbed into <code>{{SECRET_*}}</code> placeholders.</p>
        </div>
      `;
      const xBtn = document.createElement("button");
      xBtn.className = "btn ghost icon-btn";
      xBtn.innerHTML = icon("x", 15);
      xBtn.onclick = closeModal;
      head.appendChild(xBtn);
      dialog.appendChild(head);

      const docTabs = document.createElement("div");
      docTabs.className = "segmented";
      docTabs.style.margin = "12px 0";
      availableDocs.forEach(dk => {
        const b = document.createElement("button");
        b.className = "seg-btn" + (dk === activeDoc ? " active" : "");
        b.textContent = dk;
        b.onclick = () => {
          activeDoc = dk;
          loadAndRender();
        };
        docTabs.appendChild(b);
      });
      dialog.appendChild(docTabs);

      const statusRow = document.createElement("div");
      statusRow.className = "row-between";
      statusRow.style.marginBottom = "8px";
      const syntaxPill = document.createElement("span");
      syntaxPill.className = "pill ok";
      syntaxPill.textContent = "Valid JSON";

      const fmtBtn = document.createElement("button");
      fmtBtn.className = "btn icon-btn";
      fmtBtn.innerHTML = `${icon("sparkles", 13)} <span>Format JSON</span>`;
      statusRow.append(syntaxPill, fmtBtn);
      dialog.appendChild(statusRow);

      const editor = document.createElement("textarea");
      editor.className = "code-editor";
      editor.spellcheck = false;
      editor.value = initialText;
      editor.oninput = () => {
        try {
          JSON.parse(editor.value);
          syntaxPill.className = "pill ok";
          syntaxPill.textContent = "Valid JSON";
        } catch (e) {
          syntaxPill.className = "pill danger";
          syntaxPill.textContent = `JSON Syntax Error: ${e.message}`;
        }
      };
      fmtBtn.onclick = () => {
        try {
          editor.value = JSON.stringify(JSON.parse(editor.value), null, 2) + "\n";
          syntaxPill.className = "pill ok";
          syntaxPill.textContent = "Formatted";
        } catch (e) {
          syntaxPill.className = "pill danger";
          syntaxPill.textContent = `Cannot format: ${e.message}`;
        }
      };
      dialog.appendChild(editor);

      const foot = document.createElement("div");
      foot.className = "row-between";
      foot.style.marginTop = "14px";
      const closeBtn = document.createElement("button");
      closeBtn.className = "btn";
      closeBtn.textContent = "Close";
      closeBtn.onclick = closeModal;

      const saveBtn = document.createElement("button");
      saveBtn.className = "btn primary";
      saveBtn.innerHTML = `${icon("save", 14)} <span>Validate, Scrub &amp; Save</span>`;
      saveBtn.onclick = async () => {
        saveBtn.disabled = true;
        try {
          const saved = await api("/api/raw-doc", {
            action: "save",
            doc: activeDoc,
            content: editor.value,
          });
          if (typeof saved.content === "string") {
            editor.value = saved.content;
          }
          setPendingPushCount(pendingPushCount + 1);
          syntaxPill.className = "pill ok";
          syntaxPill.textContent = "Saved & Scrubbed";
          render();
          showLog(`Saved raw document "${activeDoc}" (secrets scrubbed & derived files synced).`, []);
        } catch (err) {
          syntaxPill.className = "pill danger";
          syntaxPill.textContent = err.message;
        } finally {
          saveBtn.disabled = false;
        }
      };
      foot.append(closeBtn, saveBtn);
      dialog.appendChild(foot);
    };

    loadAndRender();
  });
}

function openDoctorModal() {
  openModal(async (dialog) => {
    dialog.innerHTML = `<div style="padding:20px;color:var(--muted)">Probing workspace health &amp; live API keys…</div>`;
    try {
      const res = await api("/api/doctor", { pendingEnv: pendingEnvEdits });
      const rep = res.report || res;
      const ov = rep.overall || "healthy";
      const sum = rep.summary || {};
      dialog.innerHTML = "";
      const head = document.createElement("div");
      head.className = "row-between";
      head.innerHTML = `
        <div>
          <h2>${icon("activity", 17)} Workspace Health Doctor</h2>
          <p class="sub" style="margin:3px 0 0">Live verification across required <code>.env</code> keys, AIOStreams/AIOMetadata manifests, Debrid tokens, and TMDB/MDBList.</p>
        </div>
        <span class="pill ${ov === "healthy" ? "ok" : (ov === "warn" ? "warn" : "danger")}">${escapeHtml(ov.toUpperCase())} (${sum.ok || 0} ok · ${sum.warn || 0} warn · ${sum.error || 0} err)</span>
      `;
      const xBtn = document.createElement("button");
      xBtn.className = "btn ghost icon-btn";
      xBtn.innerHTML = icon("x", 15);
      xBtn.onclick = closeModal;
      head.appendChild(xBtn);
      dialog.appendChild(head);

      const grid = document.createElement("div");
      grid.className = "cards-grid-sm";
      grid.style.marginTop = "14px";
      (rep.checks || []).forEach(chk => {
        const cls = chk.status === "ok" ? "ok" : (chk.status === "warn" ? "warn" : (chk.status === "error" ? "danger" : ""));
        const c = document.createElement("div");
        c.className = "env-card";
        c.innerHTML = `
          <div class="row-between">
            <strong style="font-size:12px">${escapeHtml(chk.label)}</strong>
            <span class="pill ${cls}">${escapeHtml(chk.status)}</span>
          </div>
          <div style="font-size:11px;color:var(--muted)">${escapeHtml(chk.detail)}</div>
        `;
        grid.appendChild(c);
      });
      dialog.appendChild(grid);
    } catch (err) {
      dialog.innerHTML = `<div style="padding:20px;color:var(--danger)">Doctor failed: ${escapeHtml(err.message)}</div>`;
    }
  });
}

function openDiffModal() {
  openModal(async (dialog) => {
    dialog.classList.add("modal-wide");
    dialog.innerHTML = `<div style="padding:20px;color:var(--muted)">Comparing local GitOps documents against live remote servers…</div>`;
    try {
      if (dirty) await saveChanges(true);
      const res = await api("/api/diff", { target: "all" });
      const outcomes = res.outcomes || [];
      dialog.innerHTML = "";
      const head = document.createElement("div");
      head.className = "row-between";
      head.innerHTML = `
        <div>
          <h2>${icon("gitDiff", 17)} Local vs. Remote Drift Diff</h2>
          <p class="sub" style="margin:3px 0 0">Normalized semantic diff across AIOStreams, AIOMetadata, AIOManager, and Nuvio Cloud.</p>
        </div>
      `;
      const xBtn = document.createElement("button");
      xBtn.className = "btn ghost icon-btn";
      xBtn.innerHTML = icon("x", 15);
      xBtn.onclick = closeModal;
      head.appendChild(xBtn);
      dialog.appendChild(head);

      const list = document.createElement("div");
      list.className = "section-stack";
      list.style.marginTop = "14px";
      if (!outcomes.length) {
        list.innerHTML = `<div class="rows-box">No remote instances configured yet. Provision instances first to inspect drift.</div>`;
      } else {
        outcomes.forEach(o => {
          const box = document.createElement("div");
          box.className = "rows-box";
          box.style.marginTop = "0";
          box.innerHTML = `
            <div class="row-between" style="margin-bottom:6px">
              <strong><code>${escapeHtml(o.target)}</code></strong>
              <span class="pill ${o.inSync ? "ok" : "warn"}">${o.inSync ? "In Sync" : `+${o.addedLines} / -${o.removedLines} (${(o.changedKeys || []).join(", ")})`}</span>
            </div>
          `;
          if (!o.inSync && o.unifiedDiff) {
            box.appendChild(renderColoredDiff(o.unifiedDiff));
          }
          list.appendChild(box);
        });
      }
      dialog.appendChild(list);
    } catch (err) {
      dialog.innerHTML = `<div style="padding:20px;color:var(--danger)">Diff failed: ${escapeHtml(err.message)}</div>`;
    }
  });
}

// ---------------------------------------------------------------------------
// Context Facade Passed to Tab Modules
// ---------------------------------------------------------------------------
function makeCtx() {
  return {
    get state() { return state; },
    get mode() { return appMode; },
    get activeProfileIdx() { return activeProfileIdx; },
    get pendingEnvEdits() { return pendingEnvEdits; },
    get pendingPushCount() { return pendingPushCount; },
    get nuvioProfilesCache() { return nuvioProfilesCache; },
    markPushed() {
      setPendingPushCount(0);
      renderHeader();
    },
    markUnpushed() {
      setPendingPushCount(pendingPushCount + 1);
      renderHeader();
    },
    discardPendingEnv() {
      pendingEnvEdits = {};
      dirty = Object.values(changedSections).some(Boolean);
      renderHeader();
    },
    currentProfile,
    selectProfile(idx) {
      activeProfileIdx = idx;
      render();
    },
    setNuvioProfiles(list) {
      nuvioProfilesCache = Array.isArray(list) ? list : [];
    },
    setPendingEnv(key, val) {
      pendingEnvEdits[key] = val;
      mutationEpoch += 1;
      dirty = true;
      renderHeader();
    },
    effectiveEnvValue(key) {
      if (key in pendingEnvEdits) return pendingEnvEdits[key];
      const spec = (state?.env || []).find(e => e.key === key);
      return spec && !spec.isSecret ? (spec.value || "") : "";
    },
    isEnvKeyReady(key) {
      if (pendingEnvEdits[key] && pendingEnvEdits[key].trim()) return true;
      const spec = (state?.env || []).find(e => e.key === key);
      return Boolean(spec && spec.isSet);
    },
    markChanged(section) {
      if (section in changedSections) changedSections[section] = true;
      mutationEpoch += 1;
      dirty = true;
      renderHeader();
    },
    clearPendingEdits,
    buildMutationPayload,
    api,
    streamPush,
    saveChanges,
    saveSection,
    render,
    switchTab(tab) {
      activeTab = tab;
      closeSidebarMobile();
      render();
    },
    showLog,
    copyText,
    openModal,
    closeModal,
    openNewProfileModal,
    openRawJsonModal,
    openDoctorModal,
    openDiffModal,
    ui: {
      icon,
      escapeHtml,
      makeSwitch,
      makeParamInput,
      renderColoredDiff,
    },
  };
}

// ---------------------------------------------------------------------------
// Header, Sidebar Profile List, Onboarding Banner & Main Render
// ---------------------------------------------------------------------------
function renderHeader() {
  if (!state) return;

  const titleEl = document.getElementById("view-title");
  if (titleEl) {
    titleEl.textContent = TAB_TITLES[activeTab] || "Home & Simulator";
  }

  document.getElementById("subtitle").textContent =
    appMode === "stateless"
      ? "Stateless Browser · Bundle & secrets stay in browser localStorage"
      : `${state.tenant.name} · ${state.tenant.root}`;

  const modePill = document.getElementById("mode-pill");
  modePill.textContent = appMode === "stateless" ? "Stateless" : "Local GitOps";
  modePill.className = "pill " + (appMode === "stateless" ? "warn" : "ok");

  const reqEnv = (state.env || []).filter(e => e.required);
  const readyCount = reqEnv.filter(
    e => e.isSet || (pendingEnvEdits[e.key] && pendingEnvEdits[e.key].trim())
  ).length;
  const pct = reqEnv.length ? Math.round((readyCount / reqEnv.length) * 100) : 100;

  const envPill = document.getElementById("env-ready-pill");
  envPill.textContent = `${readyCount}/${reqEnv.length}`;
  envPill.className = "pill " + (readyCount === reqEnv.length ? "ok" : "warn");

  const fillEl = document.getElementById("env-progress-fill");
  if (fillEl) {
    fillEl.style.width = `${pct}%`;
    fillEl.style.background = readyCount === reqEnv.length ? "var(--ok)" : "var(--warn)";
  }

  const dirtyPill = document.getElementById("dirty-pill");
  if (dirtyPill) dirtyPill.hidden = !dirty;

  const pushPill = document.getElementById("push-pending-pill");
  if (pushPill) {
    pushPill.hidden = pendingPushCount <= 0;
    pushPill.textContent = pendingPushCount === 1 ? "1 edit to push" : `${pendingPushCount} edits to push`;
  }

  // Household Profile Switcher in Sidebar
  const pBar = document.getElementById("profile-tabs");
  pBar.innerHTML = "";
  const canDelete = (state.profiles || []).length > 1;
  (state.profiles || []).forEach((p, idx) => {
    const wrap = document.createElement("div");
    wrap.className = "profile-pill-wrap" + (idx === activeProfileIdx ? " active" : "");
    wrap.style.setProperty("--profile-accent", p.accentColor || "#818cf8");

    const btn = document.createElement("button");
    btn.className = "profile-pill-btn";
    const metaInst = p.aiometadataInstance || p.instances?.aiometadata || "default";
    btn.innerHTML = `<span class="profile-dot" style="background:${escapeHtml(p.accentColor || "#818cf8")}"></span>
      <span class="profile-label">${escapeHtml(p.emoji || "🎬")} ${escapeHtml(p.displayName)}</span>
      ${metaInst !== "default" ? `<span class="profile-badge-mini">${escapeHtml(metaInst)}</span>` : ""}`;
    btn.onclick = () => {
      activeProfileIdx = idx;
      closeSidebarMobile();
      render();
    };
    wrap.appendChild(btn);

    if (canDelete) {
      const del = document.createElement("button");
      del.className = "profile-del-btn";
      del.title = `Delete profile ${p.displayName}`;
      del.setAttribute("aria-label", `Delete profile ${p.displayName}`);
      del.innerHTML = icon("x", 12);
      del.onclick = (e) => {
        e.stopPropagation();
        deleteProfile(p.name);
      };
      wrap.appendChild(del);
    }
    pBar.appendChild(wrap);
  });

  // Onboarding Banner & Wizard Pulse
  const banner = document.getElementById("onboarding-banner");
  const allProv = Boolean(state.tenant?.aiostreamsProvisioned && state.tenant?.aiometadataProvisioned);
  const prof = currentProfile();
  const nuvioReady = Boolean(prof?.nuvio?.loginReady);
  const setupComplete = allProv && nuvioReady && readyCount === reqEnv.length;

  const pulseDot = document.getElementById("wizard-pulse-dot");
  if (pulseDot) pulseDot.hidden = setupComplete;

  if (dismissBanner || setupComplete) {
    banner.hidden = true;
  } else {
    banner.hidden = false;
    banner.className = "card onboarding-banner";
    banner.innerHTML = "";
    const row = document.createElement("div");
    row.className = "row-between";
    row.style.flexWrap = "wrap";
    row.innerHTML = `
      <div>
        <div class="card-eyebrow">${icon("compass", 12)} Workspace Onboarding</div>
        <strong style="font-size:14px">Finish Day-0 Provisioning &amp; Sign-In</strong>
        <div style="font-size:12.5px;color:var(--muted);margin-top:3px">
          ${allProv ? "Instances provisioned" : "Instances pending"} · Vault keys ${readyCount}/${reqEnv.length} · Nuvio ${nuvioReady ? "connected" : "sign-in pending"}
        </div>
      </div>
    `;
    const btns = document.createElement("div");
    btns.className = "row-wrap";
    const wizBtn = document.createElement("button");
    wizBtn.className = "btn primary";
    wizBtn.innerHTML = `${icon("compass", 14)} <span>Launch Setup Wizard</span>`;
    wizBtn.onclick = () => openSetupWizard(makeCtx());
    const xBtn = document.createElement("button");
    xBtn.className = "btn ghost icon-btn";
    xBtn.setAttribute("aria-label", "Dismiss onboarding banner");
    xBtn.innerHTML = icon("x", 14);
    xBtn.onclick = () => { dismissBanner = true; renderHeader(); };
    btns.append(wizBtn, xBtn);
    row.appendChild(btns);
    banner.appendChild(row);
  }
}

function render() {
  if (!state) return;
  renderHeader();

  document.querySelectorAll("#nav-tabs .nav-item").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.tab === activeTab);
  });

  const root = document.getElementById("view-root");
  root.innerHTML = "";
  const ctx = makeCtx();
  if (activeTab === "home") renderHomeTab(root, ctx);
  else if (activeTab === "streams") renderStreamsTab(root, ctx);
  else if (activeTab === "aiomanager") renderAioManagerTab(root, ctx);
  else if (activeTab === "nuvio") renderNuvioTab(root, ctx);
  else if (activeTab === "keys") renderKeysTab(root, ctx);
}

// ---------------------------------------------------------------------------
// Wire Shell Event Listeners & Boot
// ---------------------------------------------------------------------------
document.querySelectorAll("#nav-tabs .nav-item").forEach(btn => {
  btn.onclick = () => {
    activeTab = btn.dataset.tab;
    closeSidebarMobile();
    render();
  };
});

document.getElementById("btn-mobile-menu").onclick = () => {
  const sb = document.getElementById("sidebar");
  const bd = document.getElementById("sidebar-backdrop");
  const next = !sb.classList.contains("open");
  sb.classList.toggle("open", next);
  if (bd) bd.hidden = !next;
};

document.getElementById("sidebar-backdrop").onclick = closeSidebarMobile;

document.getElementById("btn-close-log").onclick = () => {
  document.getElementById("log-drawer").hidden = true;
};

document.getElementById("modal-backdrop").onclick = (e) => {
  if (e.target.id === "modal-backdrop") closeModal();
};

document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    const backdrop = document.getElementById("modal-backdrop");
    if (backdrop && !backdrop.hidden && document.getElementById("modal-root")?.classList.contains("modal-cmdk")) {
      closeModal();
    } else {
      openCommandPalette();
    }
    return;
  }
  if (e.key === "Escape") closeModal();
});

document.getElementById("btn-cmd-k").onclick = () => openCommandPalette();
document.getElementById("btn-wizard").onclick = () => { closeSidebarMobile(); openSetupWizard(makeCtx()); };
document.getElementById("btn-new-profile").onclick = () => { closeSidebarMobile(); openNewProfileModal(); };
document.getElementById("btn-doctor").onclick = () => { closeSidebarMobile(); openDoctorModal(); };
document.getElementById("btn-diff").onclick = () => { closeSidebarMobile(); openDiffModal(); };
document.getElementById("btn-raw-json").onclick = () => { closeSidebarMobile(); openRawJsonModal("aiostreams"); };
document.getElementById("btn-save").onclick = () => saveChanges(false);

async function executePushNow(targetOverride = null) {
  const btn = document.getElementById("btn-push");
  const warmChecked = Boolean(document.getElementById("chk-push-warm")?.checked);
  const anyNuvio = (state?.profiles || []).some(p => p.nuvio?.loginReady);
  const anyStremio = (state?.profiles || []).some(p => p.stremioEnabled && p.stremio?.loginReady);
  const chosenTarget = targetOverride || (anyNuvio || anyStremio ? "all" : "instances");
  if (btn) btn.disabled = true;
  showLog(`Streaming workspace push (target: ${chosenTarget})…`, []);
  try {
    const done = await streamPush(
      {
        target: chosenTarget,
        warmCatalogs: warmChecked,
        mutation: buildMutationPayload(),
      },
      (step) => appendLogStep(step)
    );
    clearPendingEdits();
    setPendingPushCount(0);
    document.getElementById("log-drawer-title").innerHTML = `${icon("check", 14)} <span>Push complete</span>`;
    if (done && Array.isArray(done.steps) && !document.getElementById("log-drawer-steps").children.length) {
      showLog("Push complete", done.steps);
    }
    render();
  } catch (err) {
    showLog(err.message, [], true);
  } finally {
    if (btn) btn.disabled = false;
  }
}

function openPushConfirmModal() {
  if (!state) return;
  const t = state.tenant || {};
  const warmChecked = Boolean(document.getElementById("chk-push-warm")?.checked);
  const profs = state.profiles || [];
  const anyNuvio = profs.some(p => p.nuvio?.loginReady);
  const anyStremioOn = profs.some(p => p.stremioEnabled);
  const anyStremioReady = profs.some(p => p.stremioEnabled && p.stremio?.loginReady);
  const mgrReady = Boolean(state.aiomanagerPower?.syncConfigured);

  openModal((dialog) => {
    const head = document.createElement("div");
    head.className = "row-between";
    head.innerHTML = `
      <div>
        <h2>${icon("upload", 17)} Confirm Live Push</h2>
        <p class="sub" style="margin:3px 0 0">Review remote targets before pushing your local GitOps workspace live.</p>
      </div>
    `;
    const xBtn = document.createElement("button");
    xBtn.className = "btn ghost icon-btn";
    xBtn.innerHTML = icon("x", 15);
    xBtn.onclick = closeModal;
    head.appendChild(xBtn);
    dialog.appendChild(head);

    const targetsBox = document.createElement("div");
    targetsBox.className = "rows-box";
    targetsBox.innerHTML = `<div style="font-size:12.5px;font-weight:600;margin-bottom:8px">Push Targets</div>`;
    const chips = document.createElement("div");
    chips.className = "chip-row";
    [
      ["AIOStreams", t.aiostreamsProvisioned ? "Update live" : "Needs provisioning", t.aiostreamsProvisioned ? "ok" : "warn"],
      ["AIOMetadata", t.aiometadataProvisioned ? "Update live" : "Needs provisioning", t.aiometadataProvisioned ? "ok" : "warn"],
      ["Nuvio Cloud", anyNuvio ? "Push Home, themes & badges" : "Skipped (not signed in)", anyNuvio ? "ok" : "warn"],
      ...(anyStremioOn ? [["Stremio", anyStremioReady ? "Sync addons" : "Needs sign-in on Keys tab", anyStremioReady ? "ok" : "warn"]] : []),
      ...(mgrReady ? [["AIOManager", "Cloud Sync active", "ok"]] : []),
    ].forEach(([name, detail, cls]) => {
      const pill = document.createElement("span");
      pill.className = `pill ${cls}`;
      pill.textContent = `${name}: ${detail}`;
      chips.appendChild(pill);
    });
    targetsBox.appendChild(chips);

    const metaNote = document.createElement("div");
    metaNote.style.cssText = "font-size:12px;color:var(--muted);margin-top:10px";
    const notes = [];
    if (pendingPushCount > 0) notes.push(`${pendingPushCount} saved workspace edit(s) waiting to push`);
    if (dirty) notes.push("unsaved form edits will be saved first");
    if (warmChecked) notes.push("Home catalog rows will be pre-warmed");
    metaNote.textContent = notes.length ? notes.join(" · ") + "." : "Workspace is clean and ready to sync.";
    targetsBox.appendChild(metaNote);
    dialog.appendChild(targetsBox);

    const diffHost = document.createElement("div");
    dialog.appendChild(diffHost);

    const foot = document.createElement("div");
    foot.className = "row-between";
    foot.style.marginTop = "16px";

    const leftActs = document.createElement("div");
    leftActs.className = "row-wrap";
    const diffBtn = document.createElement("button");
    diffBtn.className = "btn";
    diffBtn.innerHTML = `${icon("gitDiff", 13)} <span>Check Drift Diff</span>`;
    diffBtn.onclick = async () => {
      diffBtn.disabled = true;
      diffHost.innerHTML = `<div class="rows-box" style="font-size:12px;color:var(--muted)">Checking remote drift…</div>`;
      try {
        if (dirty) await saveChanges(true);
        const res = await api("/api/diff", { target: "all" });
        const outs = res.outcomes || [];
        diffHost.innerHTML = "";
        const wrap = document.createElement("div");
        wrap.className = "rows-box";
        if (!outs.length) {
          wrap.innerHTML = `<div style="font-size:12px;color:var(--muted)">No provisioned instances yet to diff.</div>`;
        } else {
          outs.forEach(o => {
            const row = document.createElement("div");
            row.style.marginBottom = "8px";
            row.innerHTML = `
              <div class="row-between">
                <code>${escapeHtml(o.target)}</code>
                <span class="pill ${o.inSync ? "ok" : "warn"}">${o.inSync ? "In Sync" : `+${o.addedLines} / -${o.removedLines}`}</span>
              </div>
            `;
            if (!o.inSync && o.unifiedDiff) {
              row.appendChild(renderColoredDiff(o.unifiedDiff));
            }
            wrap.appendChild(row);
          });
        }
        diffHost.appendChild(wrap);
      } catch (err) {
        diffHost.innerHTML = `<div class="rows-box" style="color:var(--danger)">${escapeHtml(err.message)}</div>`;
      } finally {
        diffBtn.disabled = false;
      }
    };
    leftActs.appendChild(diffBtn);

    const rightActs = document.createElement("div");
    rightActs.className = "row-wrap";
    const cancelBtn = document.createElement("button");
    cancelBtn.className = "btn";
    cancelBtn.textContent = "Cancel";
    cancelBtn.onclick = closeModal;

    const pushBtn = document.createElement("button");
    pushBtn.className = "btn primary";
    pushBtn.innerHTML = `${icon("upload", 14)} <span>Push Live Now</span>`;
    pushBtn.onclick = () => {
      closeModal();
      executePushNow();
    };
    rightActs.append(cancelBtn, pushBtn);
    foot.append(leftActs, rightActs);
    dialog.appendChild(foot);
  });
}

document.getElementById("btn-push").onclick = () => openPushConfirmModal();
const pushPendingEl = document.getElementById("push-pending-pill");
if (pushPendingEl) {
  pushPendingEl.onclick = () => openPushConfirmModal();
}

document.getElementById("btn-pull").onclick = async () => {
  const btn = document.getElementById("btn-pull");
  btn.disabled = true;
  try {
    if (dirty) await saveChanges(true);
    showLog("Pulling live state from AIO instances & Nuvio…", []);
    const res = await api("/api/pull", { target: "all" });
    setPendingPushCount(0);
    showLog("Pulled live state & scrubbed secrets into .env", res.steps || []);
    render();
  } catch (err) {
    showLog(err.message, [], true);
  } finally {
    btn.disabled = false;
  }
};

document.getElementById("btn-export").onclick = async () => {
  try {
    if (dirty) await saveChanges(true);
    const opts = appMode === "stateless" && bundle
      ? { method: "POST", headers: { ...baseHeaders(), "Content-Type": "application/json" }, body: JSON.stringify({ bundle }) }
      : { method: "GET", headers: baseHeaders() };
    const res = await fetch("/api/export-zip", opts);
    if (!res.ok) throw new Error(`Export failed (HTTP ${res.status})`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${state?.tenant?.name || "stream-weaver"}-gitops.zip`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    showLog(err.message, [], true);
  }
};

(async function boot() {
  try {
    const saved = loadSavedBundle();
    const data = saved ? await api("/api/state", { bundle: saved }) : await api("/api/state");
    if (data.mode !== "stateless") bundle = null;
    loadPendingPushCount();
    render();

    // Auto-open Day-0 Setup Wizard on first unprovisioned visit unless dismissed
    const allProv = Boolean(state?.tenant?.aiostreamsProvisioned && state?.tenant?.aiometadataProvisioned);
    const onbDone = Boolean(state?.onboarding && state.onboarding.needsSetup === false);
    let wasDismissed = false;
    try { wasDismissed = sessionStorage.getItem("sw_wizard_dismissed") === "1"; } catch (_) {}
    if (!allProv && !onbDone && !wasDismissed) {
      openSetupWizard(makeCtx());
    }
  } catch (err) {
    showLog("Failed to initialize Studio: " + err.message, [], true);
  }
})();
