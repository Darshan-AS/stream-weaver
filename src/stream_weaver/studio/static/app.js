// Stream Weaver Studio v2 — Core State Store, NDJSON Stream Client & Global Modals

import { openSetupWizard } from "./wizard.js";
import { renderHomeTab } from "./home.js";
import { renderStreamsTab } from "./streams.js";
import { renderAioManagerTab } from "./aiomanager.js";
import { renderNuvioTab } from "./nuvio.js";
import { renderKeysTab } from "./keys.js";

const STORAGE_KEY = "stream_weaver_studio_bundle_v1";
const CSRF_TOKEN = document.querySelector('meta[name="sw-csrf-token"]')?.content || "";

let appMode = "local";
let bundle = null;
let state = null;
let activeTab = "home";
let activeProfileIdx = 0;
let dismissBanner = false;
let dirty = false;
let pendingEnvEdits = {};
let nuvioProfilesCache = [];

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

// ---------------------------------------------------------------------------
// Log Drawer & Clipboard
// ---------------------------------------------------------------------------
function showLog(title, lines = [], isError = false) {
  const drawer = document.getElementById("log-drawer");
  const titleEl = document.getElementById("log-drawer-title");
  const stepsEl = document.getElementById("log-drawer-steps");
  drawer.hidden = false;
  drawer.classList.toggle("error", isError);
  titleEl.textContent = `${isError ? "❌" : "✅"} ${title}`;
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
  d.textContent = `✓ ${stepText}`;
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
    mut.metadataPower = {
      movieProvider: mp.movieProvider,
      seriesProvider: mp.seriesProvider,
      animeProvider: mp.animeProvider,
      searchEngines: mp.searchEngines,
      aiSearchEnabled: Boolean(mp.aiSearchEnabled),
      aiSearchProvider: mp.aiSearchProvider,
      posterProvider: mp.posterProvider,
      catalogOverrides: Array.isArray(overridesObj) ? overridesObj : Object.values(overridesObj),
    };
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
  try {
    const mut = buildMutationPayload();
    await api("/api/mutate", { mutation: mut });
    clearPendingEdits();
    if (!quiet) {
      showLog("Saved configuration, synchronized derived catalogs, and updated topology diagrams.", []);
    }
    render();
  } catch (err) {
    showLog(err.message, [], true);
  }
}

async function saveSection(section, quiet = true) {
  if (section && section in changedSections) {
    changedSections[section] = true;
  }
  await saveChanges(quiet);
}

// ---------------------------------------------------------------------------
// Modal Host & Global Modals (New Profile, Raw JSON, Doctor, Diff)
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

function openNewProfileModal() {
  const emojis = ["🎬", "🍿", "🧸", "🎮", "🌙", "🎭", "🚀", "💎"];
  const colors = ["#38bdf8", "#ef4444", "#a855f7", "#10b981", "#f59e0b", "#ec4899"];
  const formState = {
    displayName: "",
    name: "",
    slugTouched: false,
    emoji: "🍿",
    accentColor: "#a855f7",
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
          <h2 style="margin:0;font-size:17px">👨‍👩‍👧‍👦 Create Household Profile</h2>
          <p class="sub" style="margin:3px 0 0">Add a family member with their own shelves, Nuvio sub-profile, theme, and optional dedicated AIOMetadata instance.</p>
        </div>
      `;
      const xBtn = document.createElement("button");
      xBtn.className = "btn icon-btn";
      xBtn.textContent = "✕";
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
      emBox.innerHTML = `<div style="font-size:12px;font-weight:600;margin-bottom:6px">Avatar Emoji</div>`;
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
      colBox.innerHTML = `<div style="font-size:12px;font-weight:600;margin-bottom:6px">Accent Color</div>`;
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
      presetBox.innerHTML = `<div style="font-size:12.5px;font-weight:700;margin-bottom:8px">Starter Collection Preset</div>`;
      const pCards = document.createElement("div");
      pCards.className = "cards-grid-sm";
      [
        ["standard", "🎬 Standard", "All 7 curated discovery, studio & genre collections"],
        ["minimal", "⚡ Minimal", "Streamlined Discovery, Streaming Services & Genres"],
        ["kids", "🧸 Kids & Family", "Animation, Pixar/Ghibli & family-safe shelves (no horror)"],
        ["clone", "🧬 Clone Existing", "Copy shelves & settings from an existing household profile"],
      ].forEach(([pid, title, desc]) => {
        const c = document.createElement("div");
        c.className = "pack-card" + (formState.preset === pid ? " on" : "");
        c.style.cursor = "pointer";
        c.onclick = () => { formState.preset = pid; paint(); };
        c.innerHTML = `<strong>${escapeHtml(title)}</strong><div style="font-size:11.5px;color:var(--muted)">${escapeHtml(desc)}</div>`;
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
          o.textContent = `${pr.emoji || "🎬"} ${pr.displayName} (${pr.name})`;
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
      chkStack.style.gap = "8px";
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
      createBtn.textContent = "+ Create Household Profile";
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
        dialog.innerHTML = `<div style="padding:20px;color:#f87171">Failed to load ${escapeHtml(activeDoc)}: ${escapeHtml(err.message)}</div>`;
      }
    };

    const renderEditor = (initialText) => {
      dialog.innerHTML = "";
      const head = document.createElement("div");
      head.className = "row-between";
      head.innerHTML = `
        <div>
          <h2 style="margin:0;font-size:17px">🛠️ Raw JSON Escape Hatch</h2>
          <p class="sub" style="margin:3px 0 0">Validated against Stream Weaver schemas on save. Any raw secrets pasted into service configs are automatically harvested into <code>.env</code> and scrubbed into <code>{{SECRET_*}}</code> placeholders.</p>
        </div>
      `;
      const xBtn = document.createElement("button");
      xBtn.className = "btn icon-btn";
      xBtn.textContent = "✕";
      xBtn.onclick = closeModal;
      head.appendChild(xBtn);
      dialog.appendChild(head);

      const docTabs = document.createElement("div");
      docTabs.className = "tabs";
      docTabs.style.margin = "12px 0";
      availableDocs.forEach(dk => {
        const b = document.createElement("button");
        b.className = "tab" + (dk === activeDoc ? " active" : "");
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
      syntaxPill.textContent = "Valid JSON ✓";

      const fmtBtn = document.createElement("button");
      fmtBtn.className = "btn icon-btn";
      fmtBtn.textContent = "✨ Format Pretty JSON";
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
          syntaxPill.textContent = "Valid JSON ✓";
        } catch (e) {
          syntaxPill.className = "pill danger";
          syntaxPill.textContent = `JSON Syntax Error: ${e.message}`;
        }
      };
      fmtBtn.onclick = () => {
        try {
          editor.value = JSON.stringify(JSON.parse(editor.value), null, 2) + "\n";
          syntaxPill.className = "pill ok";
          syntaxPill.textContent = "Formatted ✓";
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
      saveBtn.textContent = "💾 Validate, Scrub Secrets & Save";
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
          syntaxPill.className = "pill ok";
          syntaxPill.textContent = "Saved & Scrubbed ✓";
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
    dialog.innerHTML = `<div style="padding:20px;color:var(--muted)">🩺 Probing workspace health &amp; live API keys…</div>`;
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
          <h2 style="margin:0;font-size:17px">🩺 Workspace Health Doctor</h2>
          <p class="sub" style="margin:3px 0 0">Live verification across required <code>.env</code> keys, AIOStreams/AIOMetadata manifests, Debrid tokens, and TMDB/MDBList.</p>
        </div>
        <span class="pill ${ov === "healthy" ? "ok" : (ov === "warn" ? "warn" : "danger")}">${escapeHtml(ov.toUpperCase())} (${sum.ok || 0} ok · ${sum.warn || 0} warn · ${sum.error || 0} err)</span>
      `;
      const xBtn = document.createElement("button");
      xBtn.className = "btn icon-btn";
      xBtn.textContent = "✕";
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
      dialog.innerHTML = `<div style="padding:20px;color:#f87171">Doctor failed: ${escapeHtml(err.message)}</div>`;
    }
  });
}

function openDiffModal() {
  openModal(async (dialog) => {
    dialog.classList.add("modal-wide");
    dialog.innerHTML = `<div style="padding:20px;color:var(--muted)">🔍 Comparing local GitOps documents against live remote servers…</div>`;
    try {
      if (dirty) await saveChanges(true);
      const res = await api("/api/diff", { target: "all" });
      const outcomes = res.outcomes || [];
      dialog.innerHTML = "";
      const head = document.createElement("div");
      head.className = "row-between";
      head.innerHTML = `
        <div>
          <h2 style="margin:0;font-size:17px">🔍 Local vs. Remote Drift Diff</h2>
          <p class="sub" style="margin:3px 0 0">Normalized semantic diff across AIOStreams, AIOMetadata, AIOManager, and Nuvio Cloud.</p>
        </div>
      `;
      const xBtn = document.createElement("button");
      xBtn.className = "btn icon-btn";
      xBtn.textContent = "✕";
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
              <span class="pill ${o.inSync ? "ok" : "warn"}">${o.inSync ? "In Sync ✓" : `+${o.addedLines} / -${o.removedLines} (${(o.changedKeys || []).join(", ")})`}</span>
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
      dialog.innerHTML = `<div style="padding:20px;color:#f87171">Diff failed: ${escapeHtml(err.message)}</div>`;
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
    get nuvioProfilesCache() { return nuvioProfilesCache; },
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
      escapeHtml,
      makeSwitch,
      makeParamInput,
      renderColoredDiff,
    },
  };
}

// ---------------------------------------------------------------------------
// Header, Profile Pills, Onboarding Banner & Main Render
// ---------------------------------------------------------------------------
function renderHeader() {
  if (!state) return;

  document.getElementById("subtitle").textContent =
    appMode === "stateless"
      ? "Stateless Browser Studio · Bundle & keys stay in browser localStorage"
      : `Tenant: ${state.tenant.name} · ${state.tenant.root}`;

  const modePill = document.getElementById("mode-pill");
  modePill.textContent = appMode === "stateless" ? "Stateless Browser" : "Local GitOps";
  modePill.className = "pill " + (appMode === "stateless" ? "warn" : "ok");

  const reqEnv = (state.env || []).filter(e => e.required);
  const readyCount = reqEnv.filter(
    e => e.isSet || (pendingEnvEdits[e.key] && pendingEnvEdits[e.key].trim())
  ).length;
  const envPill = document.getElementById("env-ready-pill");
  envPill.textContent = `Keys: ${readyCount}/${reqEnv.length}` + (dirty ? " · Unsaved" : "");
  envPill.className = "pill " + (readyCount === reqEnv.length && !dirty ? "ok" : "warn");

  // Household Profile Switcher Pills
  const pBar = document.getElementById("profile-tabs");
  pBar.innerHTML = "";
  const canDelete = (state.profiles || []).length > 1;
  (state.profiles || []).forEach((p, idx) => {
    const wrap = document.createElement("div");
    wrap.className = "profile-pill-wrap" + (idx === activeProfileIdx ? " active" : "");
    wrap.style.setProperty("--profile-accent", p.accentColor || "#38bdf8");

    const btn = document.createElement("button");
    btn.className = "profile-pill-btn";
    btn.innerHTML = `<span class="profile-dot" style="background:${escapeHtml(p.accentColor || "#38bdf8")}"></span>
      <span>${escapeHtml(p.emoji || "🎬")} ${escapeHtml(p.displayName)}</span>`;
    btn.onclick = () => {
      activeProfileIdx = idx;
      render();
    };
    wrap.appendChild(btn);

    if (canDelete) {
      const del = document.createElement("button");
      del.className = "profile-del-btn";
      del.title = `Delete profile ${p.displayName}`;
      del.textContent = "✕";
      del.onclick = (e) => {
        e.stopPropagation();
        deleteProfile(p.name);
      };
      wrap.appendChild(del);
    }
    pBar.appendChild(wrap);
  });

  // Onboarding Banner
  const banner = document.getElementById("onboarding-banner");
  const allProv = Boolean(state.tenant?.aiostreamsProvisioned && state.tenant?.aiometadataProvisioned);
  const prof = currentProfile();
  const nuvioReady = Boolean(prof?.nuvio?.loginReady);
  if (dismissBanner || (allProv && nuvioReady && readyCount === reqEnv.length)) {
    banner.hidden = true;
  } else {
    banner.hidden = false;
    banner.className = "card onboarding-banner";
    banner.style.borderColor = "var(--border-strong)";
    banner.innerHTML = "";
    const row = document.createElement("div");
    row.className = "row-between";
    row.style.flexWrap = "wrap";
    row.innerHTML = `
      <div>
        <strong style="font-size:14.5px">🧭 First-Time Workspace Setup</strong>
        <div style="font-size:12.5px;color:var(--muted);margin-top:2px">
          ${allProv ? "Instances provisioned ✓" : "Instances unprovisioned"} · Keys ${readyCount}/${reqEnv.length} · Nuvio ${nuvioReady ? "Signed In ✓" : "Sign-in pending"}
        </div>
      </div>
    `;
    const btns = document.createElement("div");
    btns.className = "row-wrap";
    const wizBtn = document.createElement("button");
    wizBtn.className = "btn primary";
    wizBtn.textContent = "🧭 Launch Guided Setup Wizard";
    wizBtn.onclick = () => openSetupWizard(makeCtx());
    const xBtn = document.createElement("button");
    xBtn.className = "btn icon-btn";
    xBtn.textContent = "✕";
    xBtn.onclick = () => { dismissBanner = true; renderHeader(); };
    btns.append(wizBtn, xBtn);
    row.appendChild(btns);
    banner.appendChild(row);
  }
}

function render() {
  if (!state) return;
  renderHeader();

  document.querySelectorAll("#nav-tabs .tab").forEach(btn => {
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
// Wire Top-Bar Event Listeners & Boot
// ---------------------------------------------------------------------------
document.querySelectorAll("#nav-tabs .tab").forEach(btn => {
  btn.onclick = () => {
    activeTab = btn.dataset.tab;
    render();
  };
});

document.getElementById("btn-close-log").onclick = () => {
  document.getElementById("log-drawer").hidden = true;
};

document.getElementById("modal-backdrop").onclick = (e) => {
  if (e.target.id === "modal-backdrop") closeModal();
};

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeModal();
});

document.getElementById("btn-wizard").onclick = () => openSetupWizard(makeCtx());
document.getElementById("btn-new-profile").onclick = () => openNewProfileModal();
document.getElementById("btn-doctor").onclick = () => openDoctorModal();
document.getElementById("btn-diff").onclick = () => openDiffModal();
document.getElementById("btn-raw-json").onclick = () => openRawJsonModal("aiostreams");
document.getElementById("btn-save").onclick = () => saveChanges(false);

document.getElementById("btn-push").onclick = async () => {
  const btn = document.getElementById("btn-push");
  const warmChecked = Boolean(document.getElementById("chk-push-warm")?.checked);
  btn.disabled = true;
  showLog("Streaming workspace push…", []);
  try {
    const done = await streamPush(
      {
        target: "all",
        warmCatalogs: warmChecked,
        mutation: buildMutationPayload(),
      },
      (step) => appendLogStep(step)
    );
    clearPendingEdits();
    document.getElementById("log-drawer-title").textContent = "✅ Push complete!";
    if (done && Array.isArray(done.steps) && !document.getElementById("log-drawer-steps").children.length) {
      showLog("Push complete!", done.steps);
    }
    render();
  } catch (err) {
    showLog(err.message, [], true);
  } finally {
    btn.disabled = false;
  }
};

document.getElementById("btn-pull").onclick = async () => {
  const btn = document.getElementById("btn-pull");
  btn.disabled = true;
  try {
    if (dirty) await saveChanges(true);
    showLog("Pulling live state from AIO instances & Nuvio…", []);
    const res = await api("/api/pull", { target: "all" });
    showLog("Pulled live state & scrubbed secrets into .env:", res.steps || []);
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
