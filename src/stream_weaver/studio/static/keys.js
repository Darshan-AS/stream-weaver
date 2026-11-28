// Tab 5 — Keys, Nuvio & Stremio Sign-In & Secrets Vault

import { KEY_HELP, validateKeyFormat } from "./wizard.js";

let showAllEnv = false;
let activeEnvGroup = "all";
const revealedKeys = new Set();

const ENV_GROUP_LABELS = {
  all: "All Keys",
  instances: "Instances & URLs",
  debrid: "Debrid & Usenet",
  metadata: "Metadata & AI",
  personal: "Trackers & Alerts",
  subtitles: "Subtitles",
  aiomanager: "AIOManager",
};

export function renderKeysTab(root, ctx) {
  const stack = document.createElement("div");
  stack.className = "section-stack";

  stack.appendChild(renderNuvioSignInCard(ctx));
  stack.appendChild(renderStremioSignInCard(ctx));
  stack.appendChild(renderEnvVaultCard(ctx));

  root.appendChild(stack);
}

// ---------------------------------------------------------------------------
// Nuvio Sign-In & Profile Binding
// ---------------------------------------------------------------------------
function renderNuvioSignInCard(ctx) {
  const { escapeHtml, icon } = ctx.ui;
  const p = ctx.currentProfile();
  const card = document.createElement("div");
  card.className = "card";

  if (!p || !p.nuvio) {
    card.innerHTML = `<div class="card-eyebrow">${icon("lock", 12)} Client Auth</div><h2>Nuvio Cloud Account</h2><p class="sub">Nuvio client sync is not enabled on this profile.</p>`;
    return card;
  }

  const ready = Boolean(p.nuvio.loginReady);
  card.innerHTML = `
    <div class="row-between" style="flex-wrap:wrap">
      <div>
        <div class="card-eyebrow">${icon("lock", 12)} Client Auth</div>
        <h2>Nuvio Cloud Sign-In &amp; Profile Binding (Login: <code>${escapeHtml(p.nuvio.login)}</code>)</h2>
        <p class="sub" style="margin-bottom:0">Sign in once on each Nuvio device (TV / Desktop / Mobile) to initialize its cloud blob, then authenticate here to sync collections, badges, and settings.</p>
      </div>
      <span class="pill ${ready ? "ok" : "warn"}">${ready ? `Signed In ${escapeHtml(p.nuvio.email ? "· " + p.nuvio.email : "")}` : "Not Signed In Yet"}</span>
    </div>
  `;

  const form = document.createElement("div");
  form.style.display = "grid";
  form.style.gridTemplateColumns = "repeat(auto-fit, minmax(210px, 1fr))";
  form.style.gap = "10px";
  form.style.marginTop = "14px";
  form.style.alignItems = "end";

  const emLbl = document.createElement("label");
  emLbl.className = "field"; emLbl.textContent = "Nuvio Account Email";
  const emIn = document.createElement("input");
  emIn.type = "email";
  emIn.placeholder = p.nuvio.email || "you@example.com";
  emLbl.appendChild(emIn);

  const pwLbl = document.createElement("label");
  pwLbl.className = "field"; pwLbl.textContent = "Nuvio Account Password";
  const pwIn = document.createElement("input");
  pwIn.type = "password";
  pwIn.placeholder = "••••••••";
  pwLbl.appendChild(pwIn);

  const profLbl = document.createElement("label");
  profLbl.className = "field"; profLbl.textContent = "Target Nuvio Sub-Profile";
  const profSel = document.createElement("select");
  const defOpt = document.createElement("option");
  defOpt.value = "";
  defOpt.textContent = p.nuvio.profileId
    ? `UUID ${p.nuvio.profileId.slice(0, 8)}…`
    : (p.nuvio.profileName ? `Match Name: ${p.nuvio.profileName}` : "Profile #1 (Default)");
  profSel.appendChild(defOpt);
  (ctx.nuvioProfilesCache || []).forEach(np => {
    const o = document.createElement("option");
    o.value = np.id;
    o.textContent = `#${np.index || ""} · ${np.name} (${np.id.slice(0, 8)}…)`;
    if (p.nuvio.profileId === np.id) o.selected = true;
    profSel.appendChild(o);
  });
  profSel.onchange = () => {
    p.nuvio.profileId = profSel.value;
    p.nuvio.profileName = "";
    ctx.saveSection("profile", true);
  };
  profLbl.appendChild(profSel);

  const loginBtn = document.createElement("button");
  loginBtn.className = "btn accent-outline";
  loginBtn.innerHTML = `${icon(ready ? "refresh" : "lock", 13)} <span>${ready ? "Sign In / Refresh Profiles" : "Sign In to Nuvio"}</span>`;
  loginBtn.onclick = async () => {
    loginBtn.disabled = true;
    try {
      const res = await ctx.api("/api/nuvio-login", {
        login: p.nuvio.login,
        email: emIn.value,
        password: pwIn.value,
      });
      const found = (res.nuvio && res.nuvio.profiles) || [];
      ctx.setNuvioProfiles(found);
      pwIn.value = "";
      ctx.showLog(`Authenticated Nuvio (${found.length} profile(s) available).`, []);
      ctx.render();
    } catch (err) {
      ctx.showLog(err.message, [], true);
    } finally {
      loginBtn.disabled = false;
    }
  };

  form.append(emLbl, pwLbl, profLbl, loginBtn);
  card.appendChild(form);

  // Target platforms selector
  const platRow = document.createElement("div");
  platRow.className = "row-wrap";
  platRow.style.marginTop = "12px";
  platRow.innerHTML = `<span style="font-size:12px;color:var(--muted);font-weight:500">Synced Nuvio Platforms:</span>`;
  const curPlats = Array.isArray(p.nuvio.platforms) ? p.nuvio.platforms : ["tv", "desktop", "mobile"];
  ["tv", "desktop", "mobile"].forEach(plat => {
    const lbl = document.createElement("label");
    lbl.className = "switch chip" + (curPlats.includes(plat) ? " active" : "");
    const chk = document.createElement("input");
    chk.type = "checkbox";
    chk.checked = curPlats.includes(plat);
    chk.onchange = () => {
      const next = ["tv", "desktop", "mobile"].filter(x => (x === plat ? chk.checked : curPlats.includes(x)));
      if (!next.length) {
        chk.checked = true;
        return;
      }
      p.nuvio.platforms = next;
      ctx.saveSection("profile", true);
    };
    lbl.append(chk, document.createTextNode(plat.toUpperCase()));
    platRow.appendChild(lbl);
  });
  card.appendChild(platRow);

  return card;
}

// ---------------------------------------------------------------------------
// Stremio Sign-In & Dual-Sync Control
// ---------------------------------------------------------------------------
function renderStremioSignInCard(ctx) {
  const { escapeHtml, icon } = ctx.ui;
  const p = ctx.currentProfile();
  const st = ctx.state;
  const card = document.createElement("div");
  card.className = "card";
  if (!p) return card;

  const enabled = Boolean(p.stremioEnabled);
  const ready = Boolean(p.stremio?.loginReady || st.tenant?.stremioReady);
  const savedEmail = p.stremio?.email || st.tenant?.stremioEmail || "";

  const head = document.createElement("div");
  head.className = "row-between";
  head.style.flexWrap = "wrap";
  head.innerHTML = `
    <div>
      <div class="card-eyebrow">${icon("lock", 12)} Stremio Client Auth</div>
      <h2>Stremio Account Sign-In</h2>
      <p class="sub" style="margin-bottom:0">Optionally sync your household profile addons to Stremio alongside Nuvio.</p>
    </div>
    <span class="pill ${ready ? "ok" : (enabled ? "warn" : "")}">${ready ? `Signed In${savedEmail ? " · " + escapeHtml(savedEmail) : ""}` : (enabled ? "Sign-In Needed" : "Optional · Disabled")}</span>
  `;
  card.appendChild(head);

  const toggleWrap = document.createElement("div");
  toggleWrap.style.marginTop = "12px";
  toggleWrap.appendChild(
    ctx.ui.makeSwitch(`Sync Addons to Stremio (${p.displayName})`, enabled, (val) => {
      p.stremioEnabled = val;
      ctx.saveSection("profile", true);
    })
  );
  card.appendChild(toggleWrap);

  const form = document.createElement("div");
  form.style.cssText = "display:grid;grid-template-columns:repeat(auto-fit, minmax(210px, 1fr));gap:10px;margin-top:12px;align-items:end";

  const emL = document.createElement("label");
  emL.className = "field"; emL.textContent = "Stremio Account Email";
  const emIn = document.createElement("input");
  emIn.type = "email";
  emIn.placeholder = savedEmail || "you@example.com";
  emIn.value = savedEmail;
  emL.appendChild(emIn);

  const pwL = document.createElement("label");
  pwL.className = "field"; pwL.textContent = "Stremio Account Password";
  const pwIn = document.createElement("input");
  pwIn.type = "password";
  pwIn.placeholder = "••••••••";
  pwL.appendChild(pwIn);

  const loginBtn = document.createElement("button");
  loginBtn.className = "btn accent-outline";
  loginBtn.innerHTML = `${icon(ready ? "refresh" : "lock", 13)} <span>${ready ? "Re-Authenticate Stremio" : "Sign In to Stremio"}</span>`;
  loginBtn.onclick = async () => {
    loginBtn.disabled = true;
    try {
      const res = await ctx.api("/api/stremio-login", {
        login: p.stremio?.login || "main",
        email: emIn.value,
        password: pwIn.value,
      });
      pwIn.value = "";
      if (!p.stremioEnabled) {
        p.stremioEnabled = true;
        await ctx.saveSection("profile", true);
      } else {
        ctx.render();
      }
      ctx.showLog(`Authenticated Stremio (${res.stremio?.email || emIn.value}).`, []);
    } catch (err) {
      ctx.showLog(err.message, [], true);
    } finally {
      loginBtn.disabled = false;
    }
  };

  form.append(emL, pwL, loginBtn);
  card.appendChild(form);
  return card;
}

// ---------------------------------------------------------------------------
// Grouped Secret & Instance URL Vault
// ---------------------------------------------------------------------------
function renderEnvVaultCard(ctx) {
  const { escapeHtml, icon } = ctx.ui;
  const st = ctx.state;
  const envList = st.env || [];

  const card = document.createElement("div");
  card.className = "card";

  const topRow = document.createElement("div");
  topRow.className = "row-between";
  topRow.style.flexWrap = "wrap";
  topRow.style.marginBottom = "12px";
  topRow.innerHTML = `
    <div>
      <div class="card-eyebrow">${icon("key", 12)} Secrets Vault</div>
      <h2>Grouped API Keys, Passwords &amp; Instance URLs</h2>
      <p class="sub" style="margin-bottom:0">Tabs 1–4 auto-save to disk. After editing keys below, click <strong>Save Vault Keys</strong>. Leave a secret input blank to retain its saved value.</p>
    </div>
  `;

  const optionalCount = envList.filter(e => !e.required && !e.key.endsWith("_UUID")).length;
  const topActions = document.createElement("div");
  topActions.className = "row-wrap";
  topActions.appendChild(
    ctx.ui.makeSwitch(optionalCount > 0 ? `Show Optional Keys (+${optionalCount})` : "Show Optional Keys", showAllEnv, (v) => {
      showAllEnv = v;
      ctx.render();
    })
  );
  const provBtn = document.createElement("button");
  provBtn.className = "btn accent-outline";
  const allProv = st.tenant?.aiostreamsProvisioned && st.tenant?.aiometadataProvisioned;
  provBtn.innerHTML = allProv
    ? `${icon("check", 13)} <span>Instances Provisioned</span>`
    : `${icon("sparkles", 13)} <span>Provision New UUIDs</span>`;
  provBtn.onclick = async () => {
    provBtn.disabled = true;
    try {
      const res = await ctx.api("/api/provision", { mutation: ctx.buildMutationPayload() });
      ctx.clearPendingEdits();
      ctx.showLog("Provisioned instances:", res.steps || []);
      ctx.render();
    } catch (err) {
      ctx.showLog(err.message, [], true);
    } finally {
      provBtn.disabled = false;
    }
  };
  topActions.appendChild(provBtn);
  topRow.appendChild(topActions);
  card.appendChild(topRow);

  // Group Filter Segmented Tabs
  const groupTabs = document.createElement("div");
  groupTabs.className = "segmented";
  groupTabs.style.marginBottom = "14px";
  const groupsPresent = ["all", ...Array.from(new Set(envList.map(e => e.group || "other")))];
  groupsPresent.forEach(grp => {
    const b = document.createElement("button");
    b.className = "seg-btn" + (activeEnvGroup === grp ? " active" : "");
    b.textContent = ENV_GROUP_LABELS[grp] || grp;
    b.onclick = () => {
      activeEnvGroup = grp;
      if (grp !== "all" && !showAllEnv) {
        const hasVisibleRequired = envList.some(
          e => (e.group || "other") === grp && e.required
        );
        if (!hasVisibleRequired) showAllEnv = true;
      }
      ctx.render();
    };
    groupTabs.appendChild(b);
  });
  card.appendChild(groupTabs);

  const pendingKeyCount = Object.values(ctx.pendingEnvEdits || {}).filter(v => String(v || "").trim() !== "").length;
  const saveBar = document.createElement("div");
  saveBar.className = "vault-save-bar";
  saveBar.hidden = pendingKeyCount <= 0;
  saveBar.innerHTML = `
    <span style="font-size:12.5px;font-weight:600;color:#fbbf24">${pendingKeyCount} pending key edit(s) staged — save to write into <code>.env</code></span>
  `;
  const saveBarBtns = document.createElement("div");
  saveBarBtns.className = "row-wrap";
  const discBtn = document.createElement("button");
  discBtn.className = "btn ghost";
  discBtn.textContent = "Discard";
  discBtn.onclick = () => {
    if (typeof ctx.discardPendingEnv === "function") ctx.discardPendingEnv();
    ctx.render();
  };
  const saveVaultBtn = document.createElement("button");
  saveVaultBtn.className = "btn primary";
  saveVaultBtn.innerHTML = `${icon("save", 13)} <span>Save Vault Keys</span>`;
  saveVaultBtn.onclick = () => ctx.saveChanges(false);
  saveBarBtns.append(discBtn, saveVaultBtn);
  saveBar.appendChild(saveBarBtns);
  card.appendChild(saveBar);

  const visible = envList.filter(e => {
    if (!showAllEnv && !e.required && !e.key.endsWith("_UUID")) return false;
    if (activeEnvGroup !== "all" && (e.group || "other") !== activeEnvGroup) return false;
    return true;
  });

  const grid = document.createElement("div");
  grid.className = "env-grid";
  visible.forEach(item => {
    const box = document.createElement("div");
    box.className = "env-card";
    const pendingVal = ctx.pendingEnvEdits[item.key] || "";
    const ready = item.isSet || Boolean(pendingVal.trim());
    const pillCls = ready ? "ok" : (item.required ? "warn" : "");
    const pillTxt = ready ? "Ready" : (item.required ? "Required" : "Optional");
    const linkHtml = item.signupUrl
      ? `<a href="${escapeHtml(item.signupUrl)}" target="_blank" rel="noreferrer"><span>Get Key</span> ${icon("externalLink", 11)}</a>`
      : "";

    box.innerHTML = `
      <div class="row-between">
        <strong style="font-size:12.5px">${escapeHtml(item.label)}</strong>
        <span class="pill ${pillCls}">${pillTxt}</span>
      </div>
      <div class="row-between" style="font-size:11px;color:var(--muted)">
        <code>${escapeHtml(item.key)}</code>
        ${linkHtml}
      </div>
    `;

    const inputRow = document.createElement("div");
    inputRow.style.display = "flex";
    inputRow.style.gap = "6px";
    const inp = document.createElement("input");
    const isRev = revealedKeys.has(item.key);
    inp.type = item.isSecret && !isRev ? "password" : "text";
    inp.value = pendingVal || (item.isSecret ? "" : (item.value || ""));
    inp.placeholder = item.isSecret
      ? (item.isSet ? `${item.masked} (saved — type to replace)` : (item.hint || "Paste secret…"))
      : (item.hint || "Enter value…");
    const warnEl = document.createElement("div");
    warnEl.className = "key-format-warn";
    const syncVaultWarn = (val) => {
      const issue = validateKeyFormat(item.key, val);
      warnEl.hidden = !issue;
      warnEl.textContent = issue ? issue.msg : "";
      const stagedCount = Object.values(ctx.pendingEnvEdits || {}).filter(v => String(v || "").trim() !== "").length;
      saveBar.hidden = stagedCount <= 0;
      const barText = saveBar.querySelector("span");
      if (barText && stagedCount > 0) {
        barText.textContent = `${stagedCount} pending key edit(s) staged — save to write into .env`;
      }
    };
    inp.oninput = () => {
      ctx.setPendingEnv(item.key, inp.value);
      syncVaultWarn(inp.value);
    };
    inputRow.appendChild(inp);

    if (item.isSecret) {
      const eye = document.createElement("button");
      eye.className = "btn icon-btn";
      eye.title = "Toggle visibility";
      eye.setAttribute("aria-label", "Toggle secret visibility");
      eye.innerHTML = icon(isRev ? "eyeOff" : "eye", 14);
      eye.onclick = () => {
        if (revealedKeys.has(item.key)) revealedKeys.delete(item.key);
        else revealedKeys.add(item.key);
        ctx.render();
      };
      inputRow.appendChild(eye);
    }

    box.appendChild(inputRow);
    if (KEY_HELP[item.key]) {
      const helpEl = document.createElement("div");
      helpEl.className = "key-format-help";
      helpEl.textContent = KEY_HELP[item.key];
      box.appendChild(helpEl);
    }
    syncVaultWarn(pendingVal);
    box.appendChild(warnEl);
    grid.appendChild(box);
  });

  card.appendChild(grid);
  return card;
}
