// Tab 3 — AIOManager Cloud Sync, Autopilot, Managed Household Accounts & Standalone Addons

let standaloneUrlDraft = "";
let inspectedStandalone = null;
let standaloneCustomName = "";

export function renderAioManagerTab(root, ctx) {
  const stack = document.createElement("div");
  stack.className = "section-stack";

  stack.appendChild(renderCloudSyncAndAutopilotCard(ctx));
  stack.appendChild(renderManagedAccountsCard(ctx));
  stack.appendChild(renderStandaloneAddonsCard(ctx));

  root.appendChild(stack);
}

// ---------------------------------------------------------------------------
// AIOManager Cloud Sync & Autopilot Panel
// ---------------------------------------------------------------------------
function renderCloudSyncAndAutopilotCard(ctx) {
  const { icon } = ctx.ui;
  const st = ctx.state;
  const mgr = st.aiomanagerPower || {
    present: true,
    syncConfigured: false,
    url: ctx.effectiveEnvValue("AIOMANAGER_URL"),
    uuid: ctx.effectiveEnvValue("AIOMANAGER_UUID"),
    autopilotEnabled: false,
    syncIntervalMinutes: 60,
    webhookLinked: false,
    vaultCount: 0,
    managedAccounts: [],
  };
  st.aiomanagerPower = mgr;

  const isSynced = Boolean(
    mgr.syncConfigured ||
    (ctx.isEnvKeyReady("AIOMANAGER_URL") && ctx.isEnvKeyReady("AIOMANAGER_UUID") && ctx.isEnvKeyReady("AIOMANAGER_SYNC_PASSWORD"))
  );

  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `
    <div class="row-between" style="flex-wrap:wrap">
      <div>
        <div class="card-eyebrow">${icon("cloud", 12)} Cloud Sync &amp; Autopilot</div>
        <h2>AIOManager Cloud Sync &amp; Failover</h2>
        <p class="sub" style="margin-bottom:0">Optional AES-256-GCM encrypted cloud reconciliation across Stremio &amp; Nuvio accounts, shared key vault, and Autopilot health failover. When left blank, Studio syncs directly to Nuvio Cloud.</p>
      </div>
      <div class="row-wrap">
        <span class="pill ${isSynced ? "ok" : ""}">${isSynced ? "Cloud Sync Active" : "Optional · Direct Nuvio Sync"}</span>
        <span class="pill ${mgr.webhookLinked ? "ok" : ""}">${mgr.webhookLinked ? "Webhook Linked" : "No Webhook"}</span>
        <span class="pill accent">Vault Keys: ${Number(mgr.vaultCount || 0)}</span>
      </div>
    </div>
  `;

  // Sync Env Inputs
  const envGrid = document.createElement("div");
  envGrid.className = "cards-grid";
  envGrid.style.marginTop = "14px";
  [
    ["AIOMANAGER_URL", "AIOManager Server URL", "https://aiomanager.elfhosted.com", false],
    ["AIOMANAGER_UUID", "AIOManager Cloud Sync UUID", "00000000-0000-4000-8000-000000000000", false],
    ["AIOMANAGER_SYNC_PASSWORD", "AIOManager Sync Encryption Password", "••••••••••••", true],
  ].forEach(([key, label, placeholder, secret]) => {
    const envSpec = (st.env || []).find(e => e.key === key);
    const l = document.createElement("label");
    l.className = "field";
    l.textContent = label;
    const inp = document.createElement("input");
    inp.type = secret ? "password" : "text";
    inp.value = ctx.pendingEnvEdits[key] ?? (!secret ? (envSpec?.value || mgr[key === "AIOMANAGER_URL" ? "url" : "uuid"] || "") : "");
    inp.placeholder = secret && envSpec?.isSet ? `${envSpec.masked} (saved)` : placeholder;
    inp.onchange = () => {
      ctx.setPendingEnv(key, inp.value);
      ctx.saveChanges(true);
    };
    l.appendChild(inp);
    envGrid.appendChild(l);
  });
  card.appendChild(envGrid);

  // Autopilot & Auto-Sync Interval Controls
  const autoBox = document.createElement("div");
  autoBox.className = "rows-box";
  const autoGrid = document.createElement("div");
  autoGrid.className = "grid-equal";
  autoGrid.style.alignItems = "center";

  const autoToggleWrap = document.createElement("div");
  autoToggleWrap.appendChild(
    ctx.ui.makeSwitch("Enable AIOManager Autopilot Failover & Auto-Sync", Boolean(mgr.autopilotEnabled), (v) => {
      mgr.autopilotEnabled = v;
      ctx.saveSection("aiomanagerPower", true);
    })
  );
  autoToggleWrap.insertAdjacentHTML(
    "beforeend",
    `<div style="font-size:11.5px;color:var(--muted);margin-top:5px">Monitors upstream addon health and automatically reconciles household client accounts on schedule.</div>`
  );

  const intLbl = document.createElement("label");
  intLbl.className = "field";
  intLbl.textContent = "Reconciliation Sync Interval";
  const intSel = document.createElement("select");
  const curInt = Number(mgr.syncIntervalMinutes || 60);
  const intOpts = [
    [15, "Every 15 minutes"],
    [30, "Every 30 minutes"],
    [60, "Every 1 hour (Recommended)"],
    [180, "Every 3 hours"],
    [360, "Every 6 hours"],
    [720, "Every 12 hours"],
    [1440, "Once daily (24 hours)"],
  ];
  if (!intOpts.some(([m]) => m === curInt) && curInt > 0) {
    intOpts.unshift([curInt, `Every ${curInt} minutes`]);
  }
  intOpts.forEach(([mins, txt]) => {
    const o = document.createElement("option");
    o.value = String(mins);
    o.textContent = txt;
    if (curInt === mins) o.selected = true;
    intSel.appendChild(o);
  });
  intSel.onchange = () => {
    mgr.syncIntervalMinutes = Number(intSel.value);
    ctx.saveSection("aiomanagerPower", true);
  };
  intLbl.appendChild(intSel);

  autoGrid.append(autoToggleWrap, intLbl);
  autoBox.appendChild(autoGrid);

  const mgrBase = (ctx.effectiveEnvValue("AIOMANAGER_URL") || mgr.url || "").trim().replace(/\/$/, "");
  if (mgrBase) {
    const linkRow = document.createElement("div");
    linkRow.className = "row-wrap";
    linkRow.style.marginTop = "12px";
    const openLink = document.createElement("a");
    openLink.className = "btn accent-outline";
    openLink.href = mgrBase;
    openLink.target = "_blank";
    openLink.rel = "noreferrer";
    openLink.innerHTML = `<span>Open AIOManager Dashboard</span> ${icon("externalLink", 12)}`;
    linkRow.appendChild(openLink);
    autoBox.appendChild(linkRow);
  }

  card.appendChild(autoBox);
  return card;
}

// ---------------------------------------------------------------------------
// Managed Household Accounts (Dual-Client Status per Profile)
// ---------------------------------------------------------------------------
function renderManagedAccountsCard(ctx) {
  const { escapeHtml, icon } = ctx.ui;
  const st = ctx.state;
  const managedBySlug = {};
  (st.aiomanagerPower?.managedAccounts || []).forEach(acc => {
    managedBySlug[(acc.name || acc.id || "").toLowerCase()] = acc;
  });

  const card = document.createElement("div");
  card.className = "card";
  const top = document.createElement("div");
  top.className = "row-between";
  top.style.marginBottom = "12px";
  top.innerHTML = `
    <div>
      <div class="card-eyebrow">${icon("users", 12)} Household Accounts</div>
      <h2>Managed Household Accounts (${(st.profiles || []).length})</h2>
      <p class="sub" style="margin-bottom:0">Each Stream Weaver profile compiles into a managed household account with dual Nuvio + Stremio client bindings.</p>
    </div>
  `;
  const newProfBtn = document.createElement("button");
  newProfBtn.className = "btn accent-outline";
  newProfBtn.innerHTML = `${icon("plus", 13)} <span>New Household Profile</span>`;
  newProfBtn.onclick = () => ctx.openNewProfileModal();
  top.appendChild(newProfBtn);
  card.appendChild(top);

  const grid = document.createElement("div");
  grid.className = "cards-grid";

  (st.profiles || []).forEach((prof, idx) => {
    const mAcc = managedBySlug[prof.name.toLowerCase()] || managedBySlug[(prof.displayName || "").toLowerCase()] || null;
    const nuvioOk = Boolean(prof.nuvio?.loginReady || mAcc?.nuvioConnected);
    const stremioOn = Boolean(prof.stremioEnabled);
    const addonsCount = mAcc?.addonsCount ?? (2 + (prof.extraAddons ? prof.extraAddons.filter(a => a.enabled !== false).length : 2));
    const metaInst = prof.aiometadataInstance || prof.instances?.aiometadata || "default";

    const box = document.createElement("div");
    box.className = "pack-card" + (idx === ctx.activeProfileIdx ? " on" : "");
    box.innerHTML = `
      <div class="row-between">
        <strong style="font-size:13.5px">${escapeHtml(prof.emoji || "🎬")} ${escapeHtml(prof.displayName)} <span style="color:var(--muted);font-weight:400">(${escapeHtml(prof.name)})</span></strong>
        <span class="pill accent">${addonsCount} Addons</span>
      </div>
      <div class="chip-row">
        <span class="pill ${nuvioOk ? "ok" : "warn"}">Nuvio: ${nuvioOk ? "Connected" : "Sign-In Needed"}</span>
        <span class="pill ${stremioOn ? "ok" : ""}">Stremio: ${stremioOn ? "Dual-Sync On" : "Off"}</span>
        <span class="pill">Metadata: <code>${escapeHtml(metaInst)}</code></span>
      </div>
      <div style="font-size:11.5px;color:var(--muted)">
        ${(prof.collections || []).length} Home collections · Platforms: <code>${escapeHtml((prof.nuvio?.platforms || ["tv", "desktop", "mobile"]).join(", "))}</code>
      </div>
    `;

    const actions = document.createElement("div");
    actions.className = "row-between";
    actions.style.marginTop = "4px";
    const stSwitch = ctx.ui.makeSwitch("Sync to Stremio", stremioOn, async (val) => {
      prof.stremioEnabled = val;
      ctx.selectProfile(idx);
      await ctx.saveSection("profile", true);
    });
    actions.appendChild(stSwitch);

    if (idx !== ctx.activeProfileIdx) {
      const editBtn = document.createElement("button");
      editBtn.className = "btn icon-btn";
      editBtn.textContent = "Select";
      editBtn.onclick = () => ctx.selectProfile(idx);
      actions.appendChild(editBtn);
    }
    box.appendChild(actions);
    grid.appendChild(box);
  });

  card.appendChild(grid);
  return card;
}

// ---------------------------------------------------------------------------
// Profile Standalone Addons Manager (`p.extraAddons`)
// ---------------------------------------------------------------------------
function renderStandaloneAddonsCard(ctx) {
  const { escapeHtml, icon } = ctx.ui;
  const p = ctx.currentProfile();
  const card = document.createElement("div");
  card.className = "card";
  if (!p) return card;

  if (!Array.isArray(p.extraAddons)) {
    p.extraAddons = [
      {
        name: "bingecat",
        transportUrl: "https://bingecat.com/stremio/{{SECRET_BINGECAT_TOKEN}}/manifest.json",
        customName: "BingeCat",
        enabled: Boolean(p.bingecatEnabled),
      },
      {
        name: "cinemeta",
        transportUrl: "https://v3-cinemeta.strem.io",
        customName: "Cinemeta",
        enabled: true,
      },
    ];
  }

  card.innerHTML = `
    <div class="card-eyebrow">${icon("package", 12)} Standalone Addons</div>
    <h2>Profile Standalone Addons — ${escapeHtml(p.emoji || "🎬")} ${escapeHtml(p.displayName)}</h2>
    <p class="sub">Install standalone Stremio/Nuvio addons (BingeCat, Cinemeta, StremThru, AnimeCatalogs, Contraband, etc.) directly onto <strong>${escapeHtml(p.displayName)}</strong> alongside AIOStreams &amp; AIOMetadata.</p>
  `;

  const instBox = document.createElement("div");
  instBox.className = "rows-box";
  instBox.style.marginTop = "0";
  instBox.style.borderColor = "var(--border-strong)";

  const urlRow = document.createElement("div");
  urlRow.style.display = "flex";
  urlRow.style.gap = "8px";
  urlRow.style.flexWrap = "wrap";

  const urlIn = document.createElement("input");
  urlIn.type = "text";
  urlIn.style.flex = "1";
  urlIn.style.minWidth = "250px";
  urlIn.placeholder = "Paste Stremio/Nuvio addon manifest.json URL or stremio:// link…";
  urlIn.value = standaloneUrlDraft;
  urlIn.oninput = () => { standaloneUrlDraft = urlIn.value; };

  const inspBtn = document.createElement("button");
  inspBtn.className = "btn accent-outline";
  inspBtn.innerHTML = `${icon("search", 13)} <span>Inspect Manifest</span>`;
  inspBtn.onclick = async () => {
    if (!urlIn.value.trim()) return;
    inspBtn.disabled = true;
    try {
      const res = await ctx.api("/api/inspect-manifest", { url: urlIn.value.trim() });
      inspectedStandalone = res.manifest || res;
      standaloneCustomName = inspectedStandalone.name || "";
      ctx.render();
    } catch (err) {
      ctx.showLog(err.message, [], true);
      inspBtn.disabled = false;
    }
  };

  const directAddBtn = document.createElement("button");
  directAddBtn.className = "btn";
  directAddBtn.innerHTML = `${icon("plus", 13)} <span>Add URL Directly</span>`;
  directAddBtn.onclick = () => {
    const raw = urlIn.value.trim();
    if (!raw) return;
    const slug = raw.replace(/^https?:\/\//, "").split("/")[0].replace(/[^a-z0-9_-]/gi, "-").toLowerCase().slice(0, 20) || "addon";
    p.extraAddons.push({
      name: `${slug}-${Date.now().toString(36).slice(-3)}`,
      transportUrl: raw,
      customName: slug,
      enabled: true,
    });
    standaloneUrlDraft = "";
    inspectedStandalone = null;
    ctx.saveSection("profile", true);
  };

  urlRow.append(urlIn, inspBtn, directAddBtn);
  instBox.appendChild(urlRow);

  if (inspectedStandalone) {
    const m = inspectedStandalone;
    const prev = document.createElement("div");
    prev.className = "pack-card on";
    prev.style.marginTop = "10px";
    prev.innerHTML = `
      <div class="row-between">
        <strong>${escapeHtml(m.name)} <span style="font-weight:400;color:var(--muted)">v${escapeHtml(m.version || "1.0")}</span></strong>
        <span class="pill ok"><code>${escapeHtml(m.id)}</code> · ${Number(m.catalogsCount || 0)} catalogs</span>
      </div>
      <div style="font-size:12px;color:var(--muted)">${escapeHtml(m.description || m.manifestUrl)}</div>
    `;
    const cRow = document.createElement("div");
    cRow.style.display = "flex";
    cRow.style.gap = "8px";
    cRow.style.alignItems = "end";
    cRow.style.flexWrap = "wrap";

    const cnL = document.createElement("label");
    cnL.className = "field";
    cnL.style.flex = "1";
    cnL.textContent = "Display Name in Nuvio / Stremio";
    const cnIn = document.createElement("input");
    cnIn.type = "text";
    cnIn.value = standaloneCustomName;
    cnIn.oninput = () => { standaloneCustomName = cnIn.value; };
    cnL.appendChild(cnIn);

    const addBtn = document.createElement("button");
    addBtn.className = "btn primary";
    addBtn.innerHTML = `${icon("plus", 14)} <span>Add to ${escapeHtml(p.displayName)}</span>`;
    addBtn.onclick = () => {
      const slug = (m.name || m.id || "addon").toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-|-$/g, "").slice(0, 24) || "addon";
      p.extraAddons.push({
        name: slug,
        transportUrl: m.manifestUrl || m.transportUrl,
        customName: standaloneCustomName.trim() || m.name,
        enabled: true,
      });
      standaloneUrlDraft = "";
      inspectedStandalone = null;
      ctx.saveSection("profile", true);
    };
    cRow.append(cnL, addBtn);
    prev.appendChild(cRow);
    instBox.appendChild(prev);
  }
  card.appendChild(instBox);

  const list = document.createElement("div");
  list.className = "col-list";
  list.style.marginTop = "14px";
  p.extraAddons.forEach((addon, idx) => {
    const en = addon.enabled !== false;
    const item = document.createElement("div");
    item.className = "col-item" + (en ? " selected" : "");
    item.style.cursor = "default";
    item.style.flexWrap = "wrap";

    const left = document.createElement("div");
    const sw = ctx.ui.makeSwitch(
      `${addon.customName || addon.name}`,
      en,
      (v) => {
        addon.enabled = v;
        if (addon.name === "bingecat") p.bingecatEnabled = v;
        ctx.saveSection("profile", true);
      }
    );
    const meta = document.createElement("div");
    meta.className = "col-item-meta";
    meta.innerHTML = `Slug: <code>${escapeHtml(addon.name)}</code> · URL: <code>${escapeHtml(addon.transportUrl)}</code>`;
    left.append(sw, meta);

    const right = document.createElement("div");
    right.className = "row-wrap";
    if (addon.metadata && Object.keys(addon.metadata).length) {
      const mPill = document.createElement("span");
      mPill.className = "pill";
      mPill.textContent = "Preserves metadata";
      right.appendChild(mPill);
    }
    if (addon.name !== "cinemeta") {
      const rm = document.createElement("button");
      rm.className = "btn ghost icon-btn danger";
      rm.innerHTML = `${icon("trash", 12)} <span>Remove</span>`;
      rm.onclick = () => {
        p.extraAddons.splice(idx, 1);
        ctx.saveSection("profile", true);
      };
      right.appendChild(rm);
    }

    item.append(left, right);
    list.appendChild(item);
  });

  card.appendChild(list);
  return card;
}
