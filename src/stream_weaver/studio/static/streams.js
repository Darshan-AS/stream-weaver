// Tab 2 — AIOStreams & AIOMetadata Power Studio

let inspectedStreamAddon = null;
let customManifestUrlInput = "";
let customGroupChoice = "primary";
let customTimeoutChoice = 10000;
let catalogOverrideFilter = "";
let keywordDraft = "";

const DEFAULT_FORMATTER_PRESETS = [
  {
    id: "prism",
    label: "Prism Tech Badges",
    description: "Multi-line technical breakdown with HDR/DV, audio codecs, bit-depth, seeders, and file size.",
    sampleName: "⚡ TorBox · 4K DV\n💎 Group 1",
    sampleDescription: "🎬 Dune: Part Two (2024) REMUX\n📺 2160p · Dolby Vision · HDR10+ · HEVC 10bit\n🔊 TrueHD Atmos 7.1 · 📦 58.4 GB · 🌱 312",
  },
  {
    id: "compact_tv",
    label: "Compact TV 2-Line",
    description: "Clean sofa-readable 2-line card tailored for 10-foot Android TV & Apple TV screens.",
    sampleName: "⚡ 4K DV · TB",
    sampleDescription: "Dune Part Two · TrueHD Atmos 7.1 · REMUX · 58.4 GB",
  },
  {
    id: "minimal",
    label: "Fast Minimalist",
    description: "Ultra-concise single-glance stream row showing resolution, cache status, and size.",
    sampleName: "⚡ 2160p",
    sampleDescription: "DV · Atmos · 58.4 GB (TorBox)",
  },
];

const EXCLUDE_QUALITY_CHOICES = ["CAM", "TS", "TC", "SCR", "3D", "DVDRip", "HDTV", "HDRip"];
const CODEC_CHOICES = ["AV1", "HEVC", "AVC", "XviD", "DivX"];

function getDebridList(state) {
  const ds = state.debridServices;
  if (ds && Array.isArray(ds.available) && ds.available.length) {
    return ds.available.map((item, i) => ({
      id: item.id,
      label: item.label || item.id,
      enabled: Boolean(item.enabled),
      priority: item.priority ?? (i + 1),
      secretKeys: Array.isArray(item.secretKeys) ? item.secretKeys : (item.secretKey ? [item.secretKey] : []),
      configured: Boolean(item.configured),
    }));
  }
  const activeSingle = state.debrid?.active || "torbox";
  return (state.debrid?.options || []).map((opt, i) => ({
    id: opt.id,
    label: opt.label,
    enabled: opt.id === activeSingle,
    priority: i + 1,
    secretKeys: opt.secretKey ? [opt.secretKey] : [],
    configured: false,
  }));
}

function getFormatterPresets(fmtState) {
  if (!fmtState) return DEFAULT_FORMATTER_PRESETS;
  if (Array.isArray(fmtState.presets) && fmtState.presets.length) {
    return fmtState.presets.map(p => ({
      id: p.id,
      label: p.label || p.title || p.id,
      description: p.description || "",
      sampleName: p.sampleName || "⚡ 4K",
      sampleDescription: p.sampleDescription || "",
    }));
  }
  if (fmtState.presets && typeof fmtState.presets === "object") {
    return Object.entries(fmtState.presets).map(([id, p]) => ({
      id,
      label: p.label || p.title || id,
      description: p.description || "",
      sampleName: p.sampleName || "⚡ 4K",
      sampleDescription: p.sampleDescription || "",
    }));
  }
  return DEFAULT_FORMATTER_PRESETS;
}

export function renderStreamsTab(root, ctx) {
  const stack = document.createElement("div");
  stack.className = "section-stack";

  stack.appendChild(renderMultiDebridSection(ctx));
  stack.appendChild(renderFormatterSection(ctx));
  stack.appendChild(renderAdvancedFiltersSection(ctx));
  stack.appendChild(renderPacksAndCustomAddonsSection(ctx));
  stack.appendChild(renderDualSubtitlesSection(ctx));
  stack.appendChild(renderMetadataPowerSection(ctx));
  stack.appendChild(renderUpstreamDeepLinksSection(ctx));

  root.appendChild(stack);
}

// ---------------------------------------------------------------------------
// 1. Multi-Debrid & Usenet Priority Stacker
// ---------------------------------------------------------------------------
function renderMultiDebridSection(ctx) {
  const { escapeHtml } = ctx.ui;
  const st = ctx.state;
  const providers = getDebridList(st);

  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `
    <h2>1. Multi-Debrid &amp; Usenet Priority Stacker</h2>
    <p class="sub">Enable one or more cached Debrid &amp; Usenet backends simultaneously. AIOStreams queries enabled services in top-to-bottom priority order.</p>
  `;

  const list = document.createElement("div");
  list.className = "col-list";

  const commitDebridList = (nextProviders) => {
    const normalized = nextProviders.map((item, idx) => ({
      ...item,
      priority: idx + 1,
    }));
    if (!st.debridServices) st.debridServices = {};
    st.debridServices.available = normalized;
    st.debridServices.active = normalized.filter(x => x.enabled).map(x => x.id);
    const primaryStandard = normalized.find(x => x.enabled && x.id !== "easynews");
    if (primaryStandard && st.debrid) {
      st.debrid.active = primaryStandard.id;
      ctx.markChanged("debrid");
    }
    ctx.saveSection("debridServices", true);
  };

  providers.forEach((prov, idx) => {
    const keysReady = prov.configured || (prov.secretKeys.length > 0 && prov.secretKeys.every(k => ctx.isEnvKeyReady(k)));
    const row = document.createElement("div");
    row.className = "col-item" + (prov.enabled ? " selected" : "");
    row.style.cursor = "default";
    row.style.flexWrap = "wrap";

    const left = document.createElement("div");
    left.className = "row-wrap";
    const sw = ctx.ui.makeSwitch(
      `${prov.label}`,
      prov.enabled,
      (checked) => {
        const enabledCount = providers.filter(p => p.enabled).length;
        if (!checked && enabledCount <= 1 && prov.enabled) {
          ctx.showLog("Keep at least one Debrid or Usenet provider enabled.", [], true);
          ctx.render();
          return;
        }
        providers[idx].enabled = checked;
        commitDebridList(providers);
      }
    );
    const rankPill = document.createElement("span");
    rankPill.className = "pill " + (prov.enabled ? "accent" : "");
    rankPill.textContent = prov.enabled ? `Priority #${idx + 1}` : "Disabled";

    const keyPill = document.createElement("span");
    keyPill.className = "pill " + (keysReady ? "ok" : (prov.enabled ? "warn" : ""));
    keyPill.textContent = keysReady ? "Key Ready ✓" : `Needs ${prov.secretKeys.join(" + ")}`;

    left.append(sw, rankPill, keyPill);

    const ctrls = document.createElement("div");
    ctrls.className = "col-controls";
    const up = document.createElement("button");
    up.className = "btn icon-btn";
    up.textContent = "↑";
    up.title = "Raise provider priority";
    up.disabled = idx === 0;
    up.onclick = () => {
      [providers[idx - 1], providers[idx]] = [providers[idx], providers[idx - 1]];
      commitDebridList(providers);
    };
    const dn = document.createElement("button");
    dn.className = "btn icon-btn";
    dn.textContent = "↓";
    dn.title = "Lower provider priority";
    dn.disabled = idx === providers.length - 1;
    dn.onclick = () => {
      [providers[idx + 1], providers[idx]] = [providers[idx], providers[idx + 1]];
      commitDebridList(providers);
    };
    ctrls.append(up, dn);
    row.append(left, ctrls);

    if (prov.enabled && !keysReady && prov.secretKeys.length) {
      const quickKeys = document.createElement("div");
      quickKeys.style.width = "100%";
      quickKeys.style.display = "grid";
      quickKeys.style.gridTemplateColumns = "repeat(auto-fit, minmax(220px, 1fr))";
      quickKeys.style.gap = "8px";
      quickKeys.style.marginTop = "6px";
      prov.secretKeys.forEach(sKey => {
        const inp = document.createElement("input");
        inp.type = "password";
        inp.placeholder = `Paste ${sKey}…`;
        inp.value = ctx.pendingEnvEdits[sKey] || "";
        inp.onchange = () => {
          ctx.setPendingEnv(sKey, inp.value);
          ctx.saveChanges(true);
        };
        quickKeys.appendChild(inp);
      });
      row.appendChild(quickKeys);
    }

    list.appendChild(row);
  });

  card.appendChild(list);
  return card;
}

// ---------------------------------------------------------------------------
// 2. Stream Formatter & Live Stream Card Simulator
// ---------------------------------------------------------------------------
function renderFormatterSection(ctx) {
  const { escapeHtml } = ctx.ui;
  const st = ctx.state;
  const fmt = st.streamFormatter || { preset: "prism", active: "prism" };
  const activeId = fmt.preset || fmt.active || fmt.id || "prism";
  const presets = getFormatterPresets(fmt);

  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `
    <div class="row-between">
      <div>
        <h2>2. Stream Result Formatter &amp; Live Card Preview</h2>
        <p class="sub">Choose how AIOStreams renders stream cards inside Nuvio &amp; Stremio.</p>
      </div>
      ${activeId === "custom" ? `<span class="pill warn">Custom Formatter Preserved</span>` : ""}
    </div>
  `;

  const layout = document.createElement("div");
  layout.className = "grid-equal";

  const choices = document.createElement("div");
  choices.className = "col-list";
  presets.forEach(pr => {
    const isAct = activeId === pr.id;
    const item = document.createElement("div");
    item.className = "pack-card" + (isAct ? " on" : "");
    item.style.cursor = "pointer";
    item.onclick = () => {
      if (!st.streamFormatter) st.streamFormatter = {};
      st.streamFormatter.preset = pr.id;
      st.streamFormatter.active = pr.id;
      st.streamFormatter.id = pr.id;
      ctx.saveSection("streamFormatter", true);
    };
    item.innerHTML = `
      <div class="row-between">
        <strong>${escapeHtml(pr.label)}</strong>
        <span class="pill ${isAct ? "ok" : ""}">${isAct ? "Active" : pr.id}</span>
      </div>
      <div style="font-size:12px;color:var(--muted)">${escapeHtml(pr.description)}</div>
    `;
    choices.appendChild(item);
  });

  const activePreset = presets.find(p => p.id === activeId) || presets[0];
  const sampleName = fmt.sampleName && activeId === "custom" ? fmt.sampleName : activePreset.sampleName;
  const sampleDesc = fmt.sampleDescription && activeId === "custom" ? fmt.sampleDescription : activePreset.sampleDescription;

  const previewWrap = document.createElement("div");
  previewWrap.className = "stream-card-sim";
  previewWrap.innerHTML = `
    <div class="row-between">
      <span style="font-size:11.5px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:0.05em">Live Nuvio Stream Picker Preview</span>
      <span class="pill accent">${escapeHtml(activePreset ? activePreset.label : activeId)}</span>
    </div>
    <div class="stream-sim-row">
      <div class="stream-sim-name">${escapeHtml(sampleName)}</div>
      <div class="stream-sim-desc">${escapeHtml(sampleDesc)}</div>
    </div>
  `;

  layout.append(choices, previewWrap);
  card.appendChild(layout);
  return card;
}

// ---------------------------------------------------------------------------
// 3. Advanced Stream Filters & Global Audio Languages
// ---------------------------------------------------------------------------
function renderAdvancedFiltersSection(ctx) {
  const { escapeHtml } = ctx.ui;
  const st = ctx.state;
  const sp = st.streamPreferences || { maxResolution: "2160p", cachedOnly: true, audioLanguages: ["English"] };
  const adv = st.advancedStreamFilters || {
    maxSizeGb: null,
    minSeeders: 0,
    hdrPolicy: "any",
    preferredCodecs: ["AV1", "HEVC", "AVC"],
    excludeQualities: ["CAM", "TS", "SCR", "3D"],
    excludeKeywords: [],
    rpdbPostersEnabled: false,
  };
  st.advancedStreamFilters = adv;

  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `
    <h2>3. Advanced Stream Filters, Codecs &amp; Global Audio Policy</h2>
    <p class="sub">Fine-tune resolution caps, instant-play cache enforcement, file size limits, HDR/DV priority, video codecs, excluded release tags, and 21-language audio filters.</p>
  `;

  const topGrid = document.createElement("div");
  topGrid.className = "cards-grid";

  // Max Resolution
  const resLbl = document.createElement("label");
  resLbl.className = "field";
  resLbl.textContent = "Max Stream Resolution";
  const resSel = document.createElement("select");
  const resLabels = {
    "2160p": "4K UHD + HDR/DV (2160p)",
    "1080p": "Full HD Cap (1080p)",
    "720p": "Data-Saver HD (720p)",
  };
  (sp.resolutionOptions || ["2160p", "1080p", "720p"]).forEach(v => {
    const o = document.createElement("option");
    o.value = v;
    o.textContent = resLabels[v] || v;
    if (sp.maxResolution === v) o.selected = true;
    resSel.appendChild(o);
  });
  resSel.onchange = () => {
    sp.maxResolution = resSel.value;
    ctx.saveSection("streamPreferences", true);
  };
  resLbl.appendChild(resSel);

  // HDR / DV Policy
  const hdrLbl = document.createElement("label");
  hdrLbl.className = "field";
  hdrLbl.textContent = "HDR / Dolby Vision Policy";
  const hdrSel = document.createElement("select");
  [
    ["any", "Allow All (DV + HDR10+ + SDR)"],
    ["prefer_dv_hdr", "Prioritize Dolby Vision & HDR10+"],
    ["sdr_only", "SDR Only (Exclude DV / HDR)"],
  ].forEach(([val, txt]) => {
    const o = document.createElement("option");
    o.value = val;
    o.textContent = txt;
    if ((adv.hdrPolicy || "any") === val) o.selected = true;
    hdrSel.appendChild(o);
  });
  hdrSel.onchange = () => {
    adv.hdrPolicy = hdrSel.value;
    ctx.saveSection("advancedStreamFilters", true);
  };
  hdrLbl.appendChild(hdrSel);

  // Max File Size (GB)
  const sizeLbl = document.createElement("label");
  sizeLbl.className = "field";
  sizeLbl.textContent = "Max File Size (GB — 0 or blank = Unlimited)";
  const sizeIn = document.createElement("input");
  sizeIn.type = "number";
  sizeIn.min = "0";
  sizeIn.max = "250";
  sizeIn.placeholder = "Unlimited";
  sizeIn.value = adv.maxSizeGb ? String(adv.maxSizeGb) : "";
  sizeIn.onchange = () => {
    const num = Number(sizeIn.value);
    adv.maxSizeGb = num > 0 ? Math.round(num) : null;
    ctx.saveSection("advancedStreamFilters", true);
  };
  sizeLbl.appendChild(sizeIn);

  // Min Seeders
  const seedLbl = document.createElement("label");
  seedLbl.className = "field";
  seedLbl.textContent = "Minimum P2P Seeders";
  const seedIn = document.createElement("input");
  seedIn.type = "number";
  seedIn.min = "0";
  seedIn.max = "500";
  seedIn.value = String(adv.minSeeders ?? 0);
  seedIn.onchange = () => {
    adv.minSeeders = Math.max(0, Math.round(Number(seedIn.value) || 0));
    ctx.saveSection("advancedStreamFilters", true);
  };
  seedLbl.appendChild(seedIn);

  topGrid.append(resLbl, hdrLbl, sizeLbl, seedLbl);
  card.appendChild(topGrid);

  // Toggles Row: Cached-Only + RPDB Posters in Streams
  const toggleGrid = document.createElement("div");
  toggleGrid.className = "grid-equal";
  toggleGrid.style.marginTop = "12px";

  const cacheCard = document.createElement("div");
  cacheCard.className = "pack-card" + (sp.cachedOnly ? " on" : "");
  cacheCard.appendChild(
    ctx.ui.makeSwitch("Instant-Play Cached Streams Only", Boolean(sp.cachedOnly), (v) => {
      sp.cachedOnly = v;
      ctx.saveSection("streamPreferences", true);
    })
  );
  cacheCard.insertAdjacentHTML("beforeend", `<div style="font-size:11.5px;color:var(--muted)">Hides uncached magnet torrents so every listed stream starts immediately.</div>`);

  const rpdbCard = document.createElement("div");
  rpdbCard.className = "pack-card" + (adv.rpdbPostersEnabled ? " on" : "");
  rpdbCard.appendChild(
    ctx.ui.makeSwitch("RPDB Rating Posters in AIOStreams Catalogs", Boolean(adv.rpdbPostersEnabled), (v) => {
      adv.rpdbPostersEnabled = v;
      ctx.saveSection("advancedStreamFilters", true);
    })
  );
  rpdbCard.insertAdjacentHTML("beforeend", `<div style="font-size:11.5px;color:var(--muted)">Wires <code>{{SECRET_RPDB_API_KEY}}</code> into AIOStreams catalog poster rendering.</div>`);

  toggleGrid.append(cacheCard, rpdbCard);
  card.appendChild(toggleGrid);

  // Codecs & Excluded Qualities
  const chipsGrid = document.createElement("div");
  chipsGrid.className = "grid-equal";

  const codecBox = document.createElement("div");
  codecBox.className = "rows-box";
  codecBox.innerHTML = `<div style="font-size:12.5px;font-weight:700;margin-bottom:8px">🎞️ Preferred Video Codecs</div>`;
  const codecRow = document.createElement("div");
  codecRow.className = "chip-row";
  const codecChoices = Array.isArray(adv.codecOptions) && adv.codecOptions.length ? adv.codecOptions : CODEC_CHOICES;
  const curCodecs = Array.isArray(adv.preferredCodecs) ? adv.preferredCodecs : ["AV1", "HEVC", "AVC"];
  codecChoices.forEach(codec => {
    const lbl = document.createElement("label");
    lbl.className = "switch chip" + (curCodecs.includes(codec) ? " active" : "");
    const chk = document.createElement("input");
    chk.type = "checkbox";
    chk.checked = curCodecs.includes(codec);
    chk.onchange = () => {
      const next = codecChoices.filter(c => (c === codec ? chk.checked : curCodecs.includes(c)));
      adv.preferredCodecs = next.length ? next : ["HEVC", "AVC"];
      ctx.saveSection("advancedStreamFilters", true);
    };
    lbl.append(chk, document.createTextNode(codec));
    codecRow.appendChild(lbl);
  });
  codecBox.appendChild(codecRow);

  const qualBox = document.createElement("div");
  qualBox.className = "rows-box";
  qualBox.innerHTML = `<div style="font-size:12.5px;font-weight:700;margin-bottom:8px">🚫 Excluded Low-Quality / Theatrical Tags</div>`;
  const qualRow = document.createElement("div");
  qualRow.className = "chip-row";
  const qualChoices = Array.isArray(adv.qualityOptions) && adv.qualityOptions.length ? adv.qualityOptions : EXCLUDE_QUALITY_CHOICES;
  const curExQual = Array.isArray(adv.excludeQualities) ? adv.excludeQualities : [];
  qualChoices.forEach(q => {
    const lbl = document.createElement("label");
    lbl.className = "switch chip" + (curExQual.includes(q) ? " active" : "");
    const chk = document.createElement("input");
    chk.type = "checkbox";
    chk.checked = curExQual.includes(q);
    chk.onchange = () => {
      const set = new Set(curExQual);
      if (chk.checked) set.add(q);
      else set.delete(q);
      adv.excludeQualities = Array.from(set);
      ctx.saveSection("advancedStreamFilters", true);
    };
    lbl.append(chk, document.createTextNode(q));
    qualRow.appendChild(lbl);
  });
  qualBox.appendChild(qualRow);
  chipsGrid.append(codecBox, qualBox);
  card.appendChild(chipsGrid);

  // Excluded Keywords Tag Builder
  const kwBox = document.createElement("div");
  kwBox.className = "rows-box";
  kwBox.innerHTML = `<div style="font-size:12.5px;font-weight:700;margin-bottom:8px">🔇 Excluded Release Keywords</div>`;
  const kwChips = document.createElement("div");
  kwChips.className = "chip-row";
  const curKw = Array.isArray(adv.excludeKeywords) ? adv.excludeKeywords : [];
  curKw.forEach((kw, idx) => {
    const pill = document.createElement("span");
    pill.className = "chip";
    pill.innerHTML = `<code>${escapeHtml(kw)}</code>`;
    const rm = document.createElement("button");
    rm.className = "btn icon-btn danger";
    rm.textContent = "✕";
    rm.onclick = () => {
      adv.excludeKeywords.splice(idx, 1);
      ctx.saveSection("advancedStreamFilters", true);
    };
    pill.appendChild(rm);
    kwChips.appendChild(pill);
  });
  const kwForm = document.createElement("div");
  kwForm.style.display = "flex";
  kwForm.style.gap = "8px";
  kwForm.style.marginTop = curKw.length ? "8px" : "0";
  const kwIn = document.createElement("input");
  kwIn.type = "text";
  kwIn.placeholder = "Add keyword to exclude (e.g. hardcoded, sample, telesync)…";
  kwIn.value = keywordDraft;
  kwIn.oninput = () => { keywordDraft = kwIn.value; };
  const kwAdd = document.createElement("button");
  kwAdd.className = "btn accent-outline";
  kwAdd.style.whiteSpace = "nowrap";
  kwAdd.textContent = "+ Exclude Keyword";
  kwAdd.onclick = () => {
    const val = kwIn.value.trim();
    if (!val) return;
    if (!Array.isArray(adv.excludeKeywords)) adv.excludeKeywords = [];
    if (!adv.excludeKeywords.includes(val)) {
      adv.excludeKeywords.push(val);
      keywordDraft = "";
      ctx.saveSection("advancedStreamFilters", true);
    }
  };
  kwForm.append(kwIn, kwAdd);
  kwBox.append(kwChips, kwForm);
  card.appendChild(kwBox);

  // Global Wanted Audio Languages (21 langs)
  if (Array.isArray(sp.audioLanguageOptions) && sp.audioLanguageOptions.length) {
    const audioBox = document.createElement("div");
    audioBox.className = "rows-box";
    const curAudio = sp.audioLanguages || ["English"];
    audioBox.innerHTML = `
      <div style="font-size:12.5px;font-weight:700;margin-bottom:4px">🔊 Global Stream Audio Languages (21 Languages — Original &amp; Multi Always Allowed)</div>
      <div style="font-size:11.5px;color:var(--muted);margin-bottom:8px">Select every dubbed/audio language AIOStreams should accept and rank.</div>
    `;
    const aChips = document.createElement("div");
    aChips.className = "chip-row";
    sp.audioLanguageOptions.forEach(lang => {
      const lbl = document.createElement("label");
      lbl.className = "switch chip" + (curAudio.includes(lang) ? " active" : "");
      const chk = document.createElement("input");
      chk.type = "checkbox";
      chk.checked = curAudio.includes(lang);
      chk.onchange = () => {
        const next = sp.audioLanguageOptions.filter(l => (l === lang ? chk.checked : curAudio.includes(l)));
        if (!next.length) {
          chk.checked = true;
          return;
        }
        sp.audioLanguages = next;
        ctx.saveSection("streamPreferences", true);
      };
      lbl.append(chk, document.createTextNode(lang));
      aChips.appendChild(lbl);
    });
    audioBox.appendChild(aChips);
    card.appendChild(audioBox);
  }

  return card;
}

// ---------------------------------------------------------------------------
// 4. Modular Addon Packs & Universal Custom `manifest.json` Addon Installer
// ---------------------------------------------------------------------------
function renderPacksAndCustomAddonsSection(ctx) {
  const { escapeHtml } = ctx.ui;
  const st = ctx.state;

  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `
    <h2>4. Modular Addon Packs &amp; Universal Custom Addon Installer</h2>
    <p class="sub">Toggle built-in scraper packs or paste ANY Stremio <code>manifest.json</code> URL to inspect and wire directly into AIOStreams Group 1 (Primary), Group 2 (Fallback), or Subtitles.</p>
  `;

  const pGrid = document.createElement("div");
  pGrid.className = "cards-grid";
  (st.packs || []).forEach(pk => {
    const c = document.createElement("div");
    c.className = "pack-card" + (pk.enabled ? " on" : "");
    const sw = ctx.ui.makeSwitch(pk.title, pk.enabled, (v) => {
      pk.enabled = v;
      ctx.saveSection("packs", true);
    });
    const desc = document.createElement("div");
    desc.style.fontSize = "12px";
    desc.style.color = "var(--muted)";
    desc.textContent = pk.description;
    const foot = document.createElement("div");
    foot.style.fontSize = "11px";
    foot.style.color = "var(--muted)";
    foot.innerHTML = `Presets: <code>${escapeHtml((pk.presets || []).join(", ") || "—")}</code>` +
      (pk.requiredSecrets?.length ? `<br/>Keys: <code>${escapeHtml(pk.requiredSecrets.join(", "))}</code>` : "");
    c.append(sw, desc, foot);
    pGrid.appendChild(c);
  });
  card.appendChild(pGrid);

  // Universal Custom Addon Installer
  const customBox = document.createElement("div");
  customBox.className = "rows-box";
  customBox.style.borderColor = "var(--border-strong)";
  customBox.innerHTML = `
    <div style="font-size:13.5px;font-weight:700">🔌 Universal Custom Stremio Addon Installer</div>
    <div style="font-size:12px;color:var(--muted);margin-top:2px">Paste any Stremio addon URL or <code>stremio://…/manifest.json</code> link, inspect its live manifest, and assign its execution group.</div>
  `;

  const urlRow = document.createElement("div");
  urlRow.style.display = "flex";
  urlRow.style.gap = "8px";
  urlRow.style.marginTop = "10px";
  urlRow.style.flexWrap = "wrap";

  const urlIn = document.createElement("input");
  urlIn.type = "text";
  urlIn.style.flex = "1";
  urlIn.style.minWidth = "260px";
  urlIn.placeholder = "https://addon.example.com/manifest.json  or  stremio://…";
  urlIn.value = customManifestUrlInput;
  urlIn.oninput = () => { customManifestUrlInput = urlIn.value; };

  const inspectBtn = document.createElement("button");
  inspectBtn.className = "btn accent-outline";
  inspectBtn.textContent = "🔎 Inspect Manifest";
  inspectBtn.onclick = async () => {
    if (!urlIn.value.trim()) return;
    inspectBtn.disabled = true;
    try {
      const res = await ctx.api("/api/inspect-manifest", { url: urlIn.value.trim() });
      inspectedStreamAddon = res.manifest || res;
      if (Array.isArray(inspectedStreamAddon.resources)) {
        if (inspectedStreamAddon.resources.includes("subtitles") && !inspectedStreamAddon.resources.includes("stream")) {
          customGroupChoice = "subtitles";
        } else {
          customGroupChoice = "primary";
        }
      }
      ctx.render();
    } catch (err) {
      ctx.showLog(err.message, [], true);
      inspectBtn.disabled = false;
    }
  };
  urlRow.append(urlIn, inspectBtn);
  customBox.appendChild(urlRow);

  if (inspectedStreamAddon) {
    const m = inspectedStreamAddon;
    const prev = document.createElement("div");
    prev.className = "pack-card on";
    prev.style.marginTop = "12px";
    prev.innerHTML = `
      <div class="row-between">
        <strong>${escapeHtml(m.name)} <span style="font-weight:400;color:var(--muted)">v${escapeHtml(m.version || "1.0")}</span></strong>
        <span class="pill ok"><code>${escapeHtml(m.id)}</code></span>
      </div>
      <div style="font-size:12px;color:var(--muted)">${escapeHtml(m.description || m.manifestUrl)}</div>
      <div class="chip-row">
        <span class="chip">Resources: <code>${escapeHtml((m.resources || []).join(", ") || "stream")}</code></span>
        <span class="chip">Types: <code>${escapeHtml((m.types || []).join(", ") || "movie, series")}</code></span>
        <span class="chip">Catalogs: ${Number(m.catalogsCount || 0)}</span>
      </div>
    `;
    const assignRow = document.createElement("div");
    assignRow.style.display = "grid";
    assignRow.style.gridTemplateColumns = "repeat(auto-fit, minmax(180px, 1fr))";
    assignRow.style.gap = "10px";
    assignRow.style.alignItems = "end";
    assignRow.style.marginTop = "6px";

    const grpL = document.createElement("label");
    grpL.className = "field"; grpL.textContent = "AIOStreams Execution Group";
    const grpSel = document.createElement("select");
    [
      ["primary", "Group 1 — Primary (Instant / Tier 1)"],
      ["fallback", "Group 2 — Fallback (Executed on Miss)"],
      ["subtitles", "Subtitles / Untiered"],
    ].forEach(([val, txt]) => {
      const o = document.createElement("option");
      o.value = val; o.textContent = txt;
      if (customGroupChoice === val) o.selected = true;
      grpSel.appendChild(o);
    });
    grpSel.onchange = () => { customGroupChoice = grpSel.value; };
    grpL.appendChild(grpSel);

    const toL = document.createElement("label");
    toL.className = "field"; toL.textContent = "Timeout (ms)";
    const toIn = document.createElement("input");
    toIn.type = "number"; toIn.min = "1000"; toIn.max = "120000"; toIn.value = String(customTimeoutChoice);
    toIn.onchange = () => { customTimeoutChoice = Math.min(120000, Math.max(1000, Number(toIn.value) || 10000)); };
    toL.appendChild(toIn);

    const installBtn = document.createElement("button");
    installBtn.className = "btn primary";
    installBtn.textContent = "+ Install into AIOStreams";
    installBtn.onclick = () => {
      if (!Array.isArray(st.customStreamAddons)) st.customStreamAddons = [];
      const slug = m.id.replace(/[^a-zA-Z0-9]/g, "").slice(0, 8).toLowerCase() || "cst";
      const instanceId = `cst_${slug}_${Date.now().toString(36).slice(-3)}`;
      st.customStreamAddons.push({
        instanceId,
        name: m.name,
        manifestUrl: m.manifestUrl,
        enabled: true,
        group: customGroupChoice,
        resources: Array.isArray(m.resources) && m.resources.length ? m.resources : ["stream"],
        timeout: customTimeoutChoice,
      });
      inspectedStreamAddon = null;
      customManifestUrlInput = "";
      ctx.saveSection("customStreamAddons", true);
    };

    assignRow.append(grpL, toL, installBtn);
    prev.appendChild(assignRow);
    customBox.appendChild(prev);
  }

  // Installed Custom Addons list
  const customList = Array.isArray(st.customStreamAddons) ? st.customStreamAddons : [];
  if (customList.length) {
    const installedWrap = document.createElement("div");
    installedWrap.className = "col-list";
    installedWrap.style.marginTop = "12px";
    customList.forEach((addon, idx) => {
      const item = document.createElement("div");
      item.className = "col-item" + (addon.enabled ? " selected" : "");
      item.style.cursor = "default";
      item.style.flexWrap = "wrap";

      const left = document.createElement("div");
      const sw = ctx.ui.makeSwitch(addon.name || addon.instanceId, Boolean(addon.enabled), (v) => {
        addon.enabled = v;
        ctx.saveSection("customStreamAddons", true);
      });
      const sub = document.createElement("div");
      sub.className = "col-item-meta";
      sub.innerHTML = `<code>${escapeHtml(addon.instanceId)}</code> · ${escapeHtml(addon.manifestUrl)}`;
      left.append(sw, sub);

      const right = document.createElement("div");
      right.className = "row-wrap";
      const gSel = document.createElement("select");
      gSel.style.width = "auto";
      [
        ["primary", "Primary (Group 1)"],
        ["fallback", "Fallback (Group 2)"],
        ["subtitles", "Subtitles"],
      ].forEach(([v, txt]) => {
        const o = document.createElement("option");
        o.value = v; o.textContent = txt;
        if ((addon.group || "primary") === v) o.selected = true;
        gSel.appendChild(o);
      });
      gSel.onchange = () => {
        addon.group = gSel.value;
        ctx.saveSection("customStreamAddons", true);
      };

      const rmBtn = document.createElement("button");
      rmBtn.className = "btn icon-btn danger";
      rmBtn.textContent = "✕";
      rmBtn.onclick = () => {
        st.customStreamAddons.splice(idx, 1);
        ctx.saveSection("customStreamAddons", true);
      };
      right.append(gSel, rmBtn);
      item.append(left, right);
      installedWrap.appendChild(item);
    });
    customBox.appendChild(installedWrap);
  }

  card.appendChild(customBox);
  return card;
}

// ---------------------------------------------------------------------------
// 5. Strelingo 19-Language Dual-Subtitle Pair Builder
// ---------------------------------------------------------------------------
function renderDualSubtitlesSection(ctx) {
  const { escapeHtml } = ctx.ui;
  const st = ctx.state;
  const ds = st.dualSubtitles;
  const card = document.createElement("div");
  card.className = "card";
  if (!ds || !Array.isArray(ds.languages)) {
    card.innerHTML = `<h2>5. Strelingo Bilingual Dual-Subtitles</h2><p class="sub">Dual-subtitle configuration unavailable.</p>`;
    return card;
  }

  const dualPack = (st.packs || []).find(pk => pk.id === "dual_subtitles");
  card.innerHTML = `
    <h2>5. Strelingo 19-Language Dual-Subtitle Pair Builder</h2>
    <p class="sub">Render two simultaneous subtitle tracks (spoken language on top, translation underneath) powered by Strelingo + SubSource.</p>
  `;

  if (dualPack && !dualPack.enabled) {
    const notice = document.createElement("div");
    notice.className = "rows-box";
    notice.style.marginTop = "0";
    notice.style.marginBottom = "12px";
    notice.innerHTML = `<div style="font-size:12px;color:var(--muted);margin-bottom:8px">The <strong>SubSource &amp; Strelingo Dual-Subtitles</strong> pack is currently off.</div>`;
    const btn = document.createElement("button");
    btn.className = "btn accent-outline";
    btn.textContent = "Enable Dual-Subtitles Pack";
    btn.onclick = () => {
      dualPack.enabled = true;
      ctx.saveSection("packs", true);
    };
    notice.appendChild(btn);
    card.appendChild(notice);
  }

  const langByCode = {};
  ds.languages.forEach(l => { langByCode[l.code] = l; });

  const activeChips = document.createElement("div");
  activeChips.className = "chip-row";
  activeChips.style.marginBottom = "12px";
  (ds.pairs || []).forEach((pair, idx) => {
    const m = langByCode[pair[0]] || { label: pair[0], short: pair[0] };
    const t = langByCode[pair[1]] || { label: pair[1], short: pair[1] };
    const pill = document.createElement("div");
    pill.className = "chip";
    pill.innerHTML = `<strong>${escapeHtml(m.label)} (${escapeHtml(m.short)}) → ${escapeHtml(t.label)} (${escapeHtml(t.short)})</strong>`;
    const rmBtn = document.createElement("button");
    rmBtn.className = "btn icon-btn danger";
    rmBtn.textContent = "✕";
    rmBtn.onclick = () => {
      ds.pairs.splice(idx, 1);
      ctx.saveSection("dualSubtitlePairs", true);
    };
    pill.appendChild(rmBtn);
    activeChips.appendChild(pill);
  });
  card.appendChild(activeChips);

  const builderRow = document.createElement("div");
  builderRow.style.display = "grid";
  builderRow.style.gridTemplateColumns = "repeat(auto-fit, minmax(190px, 1fr))";
  builderRow.style.gap = "10px";
  builderRow.style.alignItems = "end";

  const makeLangSelect = (title, defCode) => {
    const l = document.createElement("label");
    l.className = "field"; l.textContent = title;
    const sel = document.createElement("select");
    ds.languages.forEach(lang => {
      const o = document.createElement("option");
      o.value = lang.code;
      o.textContent = `${lang.label} (${lang.short})`;
      if (lang.code === defCode) o.selected = true;
      sel.appendChild(o);
    });
    l.appendChild(sel);
    return { l, sel };
  };

  const mainCtrl = makeLangSelect("Primary Subtitle Language", "kor");
  const transCtrl = makeLangSelect("Secondary Translation Language", "eng");
  const addBtn = document.createElement("button");
  addBtn.className = "btn accent-outline";
  addBtn.textContent = "+ Add Dual-Sub Pair";
  addBtn.onclick = () => {
    if (mainCtrl.sel.value === transCtrl.sel.value) {
      ctx.showLog("Primary and translation subtitle languages must differ.", [], true);
      return;
    }
    ds.pairs.push([mainCtrl.sel.value, transCtrl.sel.value]);
    ctx.saveSection("dualSubtitlePairs", true);
  };
  builderRow.append(mainCtrl.l, transCtrl.l, addBtn);
  card.appendChild(builderRow);
  return card;
}

// ---------------------------------------------------------------------------
// 6. AIOMetadata Power Controls
// ---------------------------------------------------------------------------
function renderMetadataPowerSection(ctx) {
  const { escapeHtml } = ctx.ui;
  const st = ctx.state;
  const mp = st.metadataPower || {
    movieProvider: "tmdb",
    seriesProvider: "tvdb",
    animeProvider: "mal",
    searchEngines: { tmdb: true, tvdb: true, mal: true },
    aiSearchEnabled: false,
    aiSearchProvider: "gemini",
    posterProvider: "rpdb",
    catalogs: [],
  };
  st.metadataPower = mp;
  if (!mp.catalogOverrides) mp.catalogOverrides = {};

  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `
    <h2>6. AIOMetadata Power Controls</h2>
    <p class="sub">Configure metadata routers per media type, natural-language AI Search (Gemini / OpenRouter), poster rating overlays, and per-catalog overrides.</p>
  `;

  // Metadata Routers
  const routerGrid = document.createElement("div");
  routerGrid.className = "cards-grid";
  const makeRouterSelect = (label, key, opts) => {
    const l = document.createElement("label");
    l.className = "field"; l.textContent = label;
    const sel = document.createElement("select");
    const cur = mp[key] || opts[0][0];
    const seen = new Set(opts.map(o => o[0]));
    if (cur && !seen.has(cur)) opts = [[cur, cur], ...opts];
    opts.forEach(([val, txt]) => {
      const o = document.createElement("option");
      o.value = val; o.textContent = txt;
      if (cur === val) o.selected = true;
      sel.appendChild(o);
    });
    sel.onchange = () => {
      mp[key] = sel.value;
      ctx.saveSection("metadataPower", true);
    };
    l.appendChild(sel);
    return l;
  };
  routerGrid.append(
    makeRouterSelect("Movie Metadata Router", "movieProvider", [["tmdb", "TMDB (Recommended)"], ["tvdb", "TheTVDB"], ["imdb", "IMDb / Cinemeta"]]),
    makeRouterSelect("Series Metadata Router", "seriesProvider", [["tvdb", "TheTVDB v4 (Recommended)"], ["tmdb", "TMDB"], ["tvmaze", "TVMaze"]]),
    makeRouterSelect("Anime Metadata Router", "animeProvider", [["mal", "MyAnimeList (MAL)"], ["anilist", "AniList"], ["kitsu", "Kitsu"], ["tvdb", "TheTVDB"]])
  );
  card.appendChild(routerGrid);

  // Poster Rating Engine + AI Search
  const midGrid = document.createElement("div");
  midGrid.className = "grid-equal";

  const posterBox = document.createElement("div");
  posterBox.className = "rows-box";
  posterBox.innerHTML = `<div style="font-size:13px;font-weight:700;margin-bottom:8px">🖼️ Poster Rating Badge Engine</div>`;
  const posterGrid = document.createElement("div");
  posterGrid.className = "cards-grid-sm";
  [
    ["rpdb", "RatingPosterDB (RPDB)", "SECRET_RPDB_API_KEY"],
    ["topposters", "TopPosters", "SECRET_TOP_POSTER_API_KEY"],
    ["postersplus", "Posters+", ""],
    ["none", "Clean Posters (No Badges)", ""],
  ].forEach(([pid, plabel, reqKey]) => {
    const isAct = (mp.posterProvider || "none") === pid;
    const pc = document.createElement("div");
    pc.className = "pack-card" + (isAct ? " on" : "");
    pc.style.cursor = "pointer";
    pc.onclick = () => {
      mp.posterProvider = pid;
      ctx.saveSection("metadataPower", true);
    };
    pc.innerHTML = `
      <div class="row-between">
        <strong style="font-size:12px">${escapeHtml(plabel)}</strong>
        <span class="pill ${isAct ? "ok" : ""}">${isAct ? "Active" : pid}</span>
      </div>
      ${reqKey ? `<div style="font-size:10.5px;color:var(--muted)"><code>${escapeHtml(reqKey)}</code></div>` : `<div style="font-size:10.5px;color:var(--muted)">Zero key needed</div>`}
    `;
    posterGrid.appendChild(pc);
  });
  posterBox.appendChild(posterGrid);

  const aiBox = document.createElement("div");
  aiBox.className = "rows-box";
  aiBox.innerHTML = `<div style="font-size:13px;font-weight:700;margin-bottom:8px">🔍 Search Engines &amp; AI Natural-Language Search</div>`;
  const seRow = document.createElement("div");
  seRow.className = "chip-row";
  seRow.style.marginBottom = "10px";
  const engines = mp.searchEngines || {};
  Object.keys(engines).forEach(engKey => {
    const lbl = document.createElement("label");
    lbl.className = "switch chip" + (engines[engKey] ? " active" : "");
    const chk = document.createElement("input");
    chk.type = "checkbox";
    chk.checked = Boolean(engines[engKey]);
    chk.onchange = () => {
      mp.searchEngines[engKey] = chk.checked;
      ctx.saveSection("metadataPower", true);
    };
    lbl.append(chk, document.createTextNode(engKey));
    seRow.appendChild(lbl);
  });
  aiBox.appendChild(seRow);

  aiBox.appendChild(
    ctx.ui.makeSwitch("✨ Enable AI Natural-Language Search", Boolean(mp.aiSearchEnabled), (v) => {
      mp.aiSearchEnabled = v;
      ctx.saveSection("metadataPower", true);
    })
  );
  if (mp.aiSearchEnabled) {
    const aiCfg = document.createElement("div");
    aiCfg.style.display = "grid";
    aiCfg.style.gridTemplateColumns = "1fr 1fr";
    aiCfg.style.gap = "8px";
    aiCfg.style.marginTop = "10px";
    const provSel = document.createElement("select");
    [["gemini", "Google Gemini"], ["openrouter", "OpenRouter"]].forEach(([v, txt]) => {
      const o = document.createElement("option");
      o.value = v; o.textContent = txt;
      if ((mp.aiSearchProvider || "gemini") === v) o.selected = true;
      provSel.appendChild(o);
    });
    provSel.onchange = () => {
      mp.aiSearchProvider = provSel.value;
      ctx.saveSection("metadataPower", true);
    };
    const keyName = (mp.aiSearchProvider || "gemini") === "openrouter" ? "SECRET_OPENROUTER_API_KEY" : "SECRET_GEMINI_API_KEY";
    const keyIn = document.createElement("input");
    keyIn.type = "password";
    keyIn.placeholder = ctx.isEnvKeyReady(keyName) ? `${keyName} saved ✓` : `Paste ${keyName}…`;
    keyIn.value = ctx.pendingEnvEdits[keyName] || "";
    keyIn.onchange = () => {
      ctx.setPendingEnv(keyName, keyIn.value);
      ctx.saveChanges(true);
    };
    aiCfg.append(provSel, keyIn);
    aiBox.appendChild(aiCfg);
  }

  midGrid.append(posterBox, aiBox);
  card.appendChild(midGrid);

  // Catalog Overrides Table
  const cats = Array.isArray(mp.catalogs) ? mp.catalogs : [];
  if (cats.length) {
    const catBox = document.createElement("div");
    catBox.className = "rows-box";
    const catHead = document.createElement("div");
    catHead.className = "row-between";
    catHead.style.marginBottom = "10px";
    catHead.style.flexWrap = "wrap";
    catHead.innerHTML = `<strong style="font-size:13px">📋 AIOMetadata Catalog Overrides (${cats.length} catalogs)</strong>`;

    const searchIn = document.createElement("input");
    searchIn.type = "text";
    searchIn.style.maxWidth = "280px";
    searchIn.placeholder = "Filter catalogs by name, id, or source…";
    searchIn.value = catalogOverrideFilter;
    catHead.appendChild(searchIn);
    catBox.appendChild(catHead);

    const tableWrap = document.createElement("div");
    tableWrap.className = "data-table-wrap";
    const renderRows = () => {
      const q = catalogOverrideFilter.trim().toLowerCase();
      const filtered = q
        ? cats.filter(c =>
            String(c.name || "").toLowerCase().includes(q) ||
            String(c.id || "").toLowerCase().includes(q) ||
            String(c.source || "").toLowerCase().includes(q)
          )
        : cats;
      const tbl = document.createElement("table");
      tbl.className = "data-table";
      tbl.innerHTML = `
        <thead>
          <tr>
            <th>Catalog Name</th>
            <th>ID &amp; Type</th>
            <th>Source</th>
            <th>Enabled</th>
            <th>Show in Home</th>
          </tr>
        </thead>
      `;
      const tbody = document.createElement("tbody");
      filtered.slice(0, 100).forEach(cat => {
        const tr = document.createElement("tr");
        const ovKey = `${cat.id}::${cat.type}`;

        const tdName = document.createElement("td");
        const nameIn = document.createElement("input");
        nameIn.type = "text";
        nameIn.value = cat.name || cat.id;
        nameIn.style.padding = "4px 8px";
        nameIn.style.fontSize = "12px";
        nameIn.onchange = () => {
          cat.name = nameIn.value.trim() || cat.id;
          mp.catalogOverrides[ovKey] = {
            id: cat.id,
            type: cat.type,
            name: cat.name,
            enabled: Boolean(cat.enabled),
            showInHome: Boolean(cat.showInHome),
          };
          ctx.saveSection("metadataPower", true);
        };
        tdName.appendChild(nameIn);

        const tdId = document.createElement("td");
        tdId.innerHTML = `<code>${escapeHtml(cat.id)}</code> <span class="pill">${escapeHtml(cat.type)}</span>`;

        const tdSrc = document.createElement("td");
        tdSrc.textContent = cat.source || "aiometadata";

        const tdEn = document.createElement("td");
        const enChk = document.createElement("input");
        enChk.type = "checkbox";
        enChk.checked = Boolean(cat.enabled);
        enChk.onchange = () => {
          cat.enabled = enChk.checked;
          mp.catalogOverrides[ovKey] = {
            id: cat.id,
            type: cat.type,
            name: cat.name,
            enabled: Boolean(cat.enabled),
            showInHome: Boolean(cat.showInHome),
          };
          ctx.saveSection("metadataPower", true);
        };
        tdEn.appendChild(enChk);

        const tdHome = document.createElement("td");
        const hmChk = document.createElement("input");
        hmChk.type = "checkbox";
        hmChk.checked = Boolean(cat.showInHome);
        hmChk.onchange = () => {
          cat.showInHome = hmChk.checked;
          mp.catalogOverrides[ovKey] = {
            id: cat.id,
            type: cat.type,
            name: cat.name,
            enabled: Boolean(cat.enabled),
            showInHome: Boolean(cat.showInHome),
          };
          ctx.saveSection("metadataPower", true);
        };
        tdHome.appendChild(hmChk);

        tr.append(tdName, tdId, tdSrc, tdEn, tdHome);
        tbody.appendChild(tr);
      });
      tbl.appendChild(tbody);
      tableWrap.innerHTML = "";
      tableWrap.appendChild(tbl);
    };

    searchIn.oninput = () => {
      catalogOverrideFilter = searchIn.value;
      renderRows();
    };
    renderRows();
    catBox.appendChild(tableWrap);
    card.appendChild(catBox);
  }

  return card;
}

// ---------------------------------------------------------------------------
// 7. Upstream Instance Deep-Links
// ---------------------------------------------------------------------------
function renderUpstreamDeepLinksSection(ctx) {
  const { escapeHtml } = ctx.ui;
  const t = ctx.state.tenant || {};
  const sUuid = t.aiostreamsUuid || "";
  const sUrl = t.aiostreamsConfigureUrl || "https://aiostreams.elfhosted.com/stremio/configure";
  const mUuid = t.aiometadataUuid || "";
  const mUrl = t.aiometadataConfigureUrl || "https://aiometadata.elfhosted.com/configure";

  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `
    <h2>7. Upstream Instance Editors &amp; OAuth Portals</h2>
    <p class="sub">Open your live AIOStreams or AIOMetadata Configure UI for one-time Trakt/Simkl OAuth or Torznab indexers, then click <strong>📥 Pull</strong> in the top bar.</p>
  `;

  const grid = document.createElement("div");
  grid.className = "grid-equal";
  [
    ["AIOStreams Editor", sUuid, sUrl, "Custom SEL expressions, MediaFlow Proxy, or Torznab/Prowlarr indexers."],
    ["AIOMetadata Editor", mUuid, mUrl, "Authenticate Trakt / Simkl / AniList OAuth to generate your token ID."],
  ].forEach(([title, uuid, url, desc]) => {
    const box = document.createElement("div");
    box.className = "pack-card" + (uuid ? " on" : "");
    box.innerHTML = `
      <div class="row-between">
        <strong>${escapeHtml(title)}</strong>
        <span class="pill ${uuid ? "ok" : "warn"}">${uuid ? `UUID ${escapeHtml(uuid.slice(0, 8))}…` : "Unprovisioned"}</span>
      </div>
      <div style="font-size:12px;color:var(--muted)">${escapeHtml(desc)}</div>
    `;
    const btns = document.createElement("div");
    btns.className = "row-wrap";
    if (uuid) {
      const cp = document.createElement("button");
      cp.className = "btn icon-btn";
      cp.textContent = "📋 Copy UUID";
      cp.onclick = () => ctx.copyText(uuid, `${title} UUID`);
      btns.appendChild(cp);
    }
    const a = document.createElement("a");
    a.className = "btn accent-outline";
    a.href = url;
    a.target = "_blank";
    a.rel = "noreferrer";
    a.textContent = `Open ${title} ↗`;
    btns.appendChild(a);
    box.appendChild(btns);
    grid.appendChild(box);
  });

  card.appendChild(grid);
  return card;
}
