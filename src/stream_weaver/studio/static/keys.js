// Tab 5 — Keys, Nuvio Cloud Sign-In, Provisioning & Raw JSON Escape Hatch

let showAllEnv = true;
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
  stack.appendChild(renderEnvVaultCard(ctx));
  stack.appendChild(renderRawEscapeHatchCard(ctx));

  root.appendChild(stack);
}

// ---------------------------------------------------------------------------
// 1. Nuvio Sign-In & Profile Binding
// ---------------------------------------------------------------------------
function renderNuvioSignInCard(ctx) {
  const { escapeHtml } = ctx.ui;
  const p = ctx.currentProfile();
  const card = document.createElement("div");
  card.className = "card";

  if (!p || !p.nuvio) {
    card.innerHTML = `<h2>1. Nuvio Cloud Account</h2><p class="sub">Nuvio client sync is not enabled on this profile.</p>`;
    return card;
  }

  const ready = Boolean(p.nuvio.loginReady);
  card.innerHTML = `
    <div class="row-between" style="flex-wrap:wrap">
      <div>
        <h2>1. Nuvio Cloud Sign-In &amp; Profile Binding (Login: <code>${escapeHtml(p.nuvio.login)}</code>)</h2>
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
  loginBtn.textContent = ready ? "🔄 Sign In / Refresh Profiles" : "🔐 Sign In to Nuvio";
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
  platRow.innerHTML = `<span style="font-size:12px;color:var(--muted);font-weight:600">Synced Nuvio Platforms:</span>`;
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
// 2. Grouped Secret & Instance URL Vault
// ---------------------------------------------------------------------------
function renderEnvVaultCard(ctx) {
  const { escapeHtml } = ctx.ui;
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
      <h2>2. Grouped API Keys, Passwords &amp; Instance URLs</h2>
      <p class="sub" style="margin-bottom:0">Secrets remain masked in the UI and scrubbed from GitOps JSON files. Leave a secret input blank to retain its saved value.</p>
    </div>
  `;

  const topActions = document.createElement("div");
  topActions.className = "row-wrap";
  topActions.appendChild(
    ctx.ui.makeSwitch("Show Optional Keys", showAllEnv, (v) => {
      showAllEnv = v;
      ctx.render();
    })
  );
  const provBtn = document.createElement("button");
  provBtn.className = "btn accent-outline";
  const allProv = st.tenant?.aiostreamsProvisioned && st.tenant?.aiometadataProvisioned;
  provBtn.textContent = allProv ? "✅ Instances Provisioned" : "✨ Provision New UUIDs";
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

  // Group Filter Tabs
  const groupTabs = document.createElement("div");
  groupTabs.className = "tabs";
  groupTabs.style.marginBottom = "14px";
  const groupsPresent = ["all", ...Array.from(new Set(envList.map(e => e.group || "other")))];
  groupsPresent.forEach(grp => {
    const b = document.createElement("button");
    b.className = "tab" + (activeEnvGroup === grp ? " active" : "");
    b.textContent = ENV_GROUP_LABELS[grp] || grp;
    b.onclick = () => {
      activeEnvGroup = grp;
      ctx.render();
    };
    groupTabs.appendChild(b);
  });
  card.appendChild(groupTabs);

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
    const pillTxt = ready ? "Ready ✓" : (item.required ? "Required" : "Optional");
    const linkHtml = item.signupUrl
      ? `<a href="${escapeHtml(item.signupUrl)}" target="_blank" rel="noreferrer">Get Key ↗</a>`
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
    inp.oninput = () => {
      ctx.setPendingEnv(item.key, inp.value);
    };
    inputRow.appendChild(inp);

    if (item.isSecret) {
      const eye = document.createElement("button");
      eye.className = "btn icon-btn";
      eye.title = "Toggle visibility";
      eye.textContent = isRev ? "🙈" : "👁️";
      eye.onclick = () => {
        if (revealedKeys.has(item.key)) revealedKeys.delete(item.key);
        else revealedKeys.add(item.key);
        ctx.render();
      };
      inputRow.appendChild(eye);
    }

    box.appendChild(inputRow);
    grid.appendChild(box);
  });

  card.appendChild(grid);
  return card;
}

// ---------------------------------------------------------------------------
// 3. Raw JSON Escape Hatch Launcher Card
// ---------------------------------------------------------------------------
function renderRawEscapeHatchCard(ctx) {
  const { escapeHtml } = ctx.ui;
  const p = ctx.currentProfile();
  const profKey = p ? `profile:${p.name}` : "profile:main";

  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `
    <div class="row-between" style="flex-wrap:wrap">
      <div>
        <h2>3. 🛠️ Raw JSON Escape Hatch (Zero Lock-In)</h2>
        <p class="sub" style="margin-bottom:0">Inspect or hand-edit any workspace JSON document directly in Studio. Service configs are schema-checked and auto-scrubbed via SecretLens so raw API tokens never hit disk.</p>
      </div>
    </div>
  `;

  const docsGrid = document.createElement("div");
  docsGrid.className = "cards-grid-sm";
  docsGrid.style.marginTop = "14px";
  [
    ["aiostreams", "aiostreams.json", "Scrapers, SEL expressions, groups & formatters"],
    ["aiometadata", "aiometadata.json", "Routers, search engines & derived catalogs"],
    ["aiomanager", "aiomanager.json", "Managed household accounts, Autopilot & vault"],
    ["tenant", "tenant.json", "Instances, logins, profiles & library roots"],
    [profKey, `${p ? p.name : "main"}/profile.json`, "Active profile shelves, badges, meta & settings"],
  ].forEach(([docKey, fileName, desc]) => {
    const box = document.createElement("div");
    box.className = "pack-card";
    box.innerHTML = `
      <div class="row-between">
        <code>${escapeHtml(fileName)}</code>
      </div>
      <div style="font-size:11.5px;color:var(--muted)">${escapeHtml(desc)}</div>
    `;
    const btn = document.createElement("button");
    btn.className = "btn accent-outline";
    btn.textContent = "🛠️ Edit Raw JSON →";
    btn.onclick = () => ctx.openRawJsonModal(docKey);
    box.appendChild(btn);
    docsGrid.appendChild(box);
  });

  card.appendChild(docsGrid);
  return card;
}
