// Guided 4-Step Day-0 Setup Wizard for Stream Weaver Studio v2

const DEBRID_WIZARD_OPTIONS = [
  { id: "torbox", label: "TorBox", keys: ["SECRET_TORBOX_API_KEY"], url: "https://torbox.app/settings" },
  { id: "realdebrid", label: "Real-Debrid", keys: ["SECRET_RD_API_KEY"], url: "https://real-debrid.com/apitoken" },
  { id: "alldebrid", label: "AllDebrid", keys: ["SECRET_ALLDEBRID_API_KEY"], url: "https://alldebrid.com/apikeys/" },
  { id: "premiumize", label: "Premiumize", keys: ["SECRET_PREMIUMIZE_API_KEY"], url: "https://www.premiumize.me/account" },
  { id: "easynews", label: "Easynews (Usenet)", keys: ["SECRET_EASYNEWS_USERNAME", "SECRET_EASYNEWS_PASSWORD"], url: "https://signup.easynews.com" },
];

const QUICK_DUAL_SUB_PAIRS = [
  { id: "", label: "Single-Language Subtitles Only" },
  { id: "kor:eng", label: "Korean + English (Strelingo Dual-Sub)" },
  { id: "jpn:eng", label: "Japanese + English (Strelingo Dual-Sub)" },
  { id: "spa:eng", label: "Spanish + English (Strelingo Dual-Sub)" },
  { id: "fra:eng", label: "French + English (Strelingo Dual-Sub)" },
  { id: "deu:eng", label: "German + English (Strelingo Dual-Sub)" },
  { id: "zho:eng", label: "Chinese + English (Strelingo Dual-Sub)" },
];

function randomPassword() {
  const chars = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const bytes = new Uint8Array(18);
  crypto.getRandomValues(bytes);
  return "sw-" + Array.from(bytes, b => chars[b % chars.length]).join("");
}

export function openSetupWizard(ctx) {
  const st = ctx.state;
  if (!st) return;
  const prof = ctx.currentProfile();

  const curStreamsUrl = ctx.effectiveEnvValue("AIOSTREAMS_URL") || "https://aiostreams.elfhosted.com";
  const curMetaUrl = ctx.effectiveEnvValue("AIOMETADATA_URL") || "https://aiometadata.elfhosted.com";
  const curMgrUrl = ctx.effectiveEnvValue("AIOMANAGER_URL") || "";
  const isElfDefault = curStreamsUrl.includes("elfhosted.com") && curMetaUrl.includes("elfhosted.com") && !curMgrUrl;

  const activeDebridFromServices = (() => {
    const ds = st.debridServices;
    if (ds && Array.isArray(ds.available)) {
      const firstOn = ds.available.find(x => x.enabled);
      if (firstOn) return firstOn.id;
    }
    return (st.debrid && st.debrid.active) || "torbox";
  })();

  const existingColRefs = new Set((prof?.collections || []).filter(c => c.ref && c.ref !== "regional-cinema").map(c => c.ref));
  const existingRegLangs = new Set(
    (prof?.collections || [])
      .filter(c => c.ref === "regional-cinema" && c.with && c.with.language)
      .map(c => c.with.language)
  );

  const wiz = {
    step: 1,
    hostingMode: isElfDefault ? "elfhosted" : "custom",
    debrid: activeDebridFromServices,
    selectedCols: existingColRefs.size ? existingColRefs : new Set(["col-for-you-discovery", "col-streaming-services", "col-genres-moods"]),
    selectedRegional: existingRegLangs,
    subtitlePref: (prof?.settings?.with?.subtitlePreferred) || "en",
    dualSubQuick: (st.dualSubtitles?.pairs?.length ? `${st.dualSubtitles.pairs[0][0]}:${st.dualSubtitles.pairs[0][1]}` : ""),
    nuvioEmail: prof?.nuvio?.email || "",
    nuvioPassword: "",
    nuvioProfileId: prof?.nuvio?.profileId || "",
    doctorReport: null,
    verifying: false,
    launching: false,
    launchSteps: [],
    launchDone: false,
    launchError: "",
  };

  if (wiz.hostingMode === "elfhosted") {
    if (!ctx.isEnvKeyReady("AIOSTREAMS_URL")) ctx.setPendingEnv("AIOSTREAMS_URL", "https://aiostreams.elfhosted.com");
    if (!ctx.isEnvKeyReady("AIOMETADATA_URL")) ctx.setPendingEnv("AIOMETADATA_URL", "https://aiometadata.elfhosted.com");
  }

  ctx.openModal((dialog) => {
    const { escapeHtml } = ctx.ui;
    const renderWizard = () => {
      dialog.innerHTML = "";
      dialog.classList.add("modal-wide");

      // Header
      const head = document.createElement("div");
      head.className = "row-between";
      head.innerHTML = `
        <div>
          <h2 style="margin:0;font-size:18px">🧭 Stream Weaver Day-0 Setup Wizard</h2>
          <p class="sub" style="margin:3px 0 0">Zero-visit provisioning: configure hosting, verify keys live, pick your shelves, and launch in minutes.</p>
        </div>
      `;
      const dismissBtn = document.createElement("button");
      dismissBtn.className = "btn";
      dismissBtn.textContent = "✕ Dismiss Wizard";
      dismissBtn.onclick = () => {
        try { sessionStorage.setItem("sw_wizard_dismissed", "1"); } catch (_) {}
        ctx.closeModal();
      };
      head.appendChild(dismissBtn);
      dialog.appendChild(head);

      // Stepper
      const stepper = document.createElement("div");
      stepper.className = "wizard-steps";
      [
        [1, "1. Hosting", "ElfHosted or Custom URLs"],
        [2, "2. Keys & Check", "Debrid, TMDB & Live Doctor"],
        [3, "3. Taste & Nuvio", "Shelves, Languages & Login"],
        [4, "4. Launch", "1-Click Provision & Push"],
      ].forEach(([num, title, desc]) => {
        const b = document.createElement("button");
        b.className = "wizard-step-pill" + (wiz.step === num ? " active" : (wiz.step > num ? " done" : ""));
        b.innerHTML = `<div>${wiz.step > num ? "✅ " : ""}${escapeHtml(title)}</div>
          <div style="font-size:10.5px;font-weight:400;opacity:0.8;margin-top:2px">${escapeHtml(desc)}</div>`;
        b.onclick = () => {
          if (!wiz.launching) {
            wiz.step = num;
            renderWizard();
          }
        };
        stepper.appendChild(b);
      });
      dialog.appendChild(stepper);

      const body = document.createElement("div");
      body.className = "section-stack";

      if (wiz.step === 1) {
        renderStep1Hosting(body, wiz, ctx, renderWizard);
      } else if (wiz.step === 2) {
        renderStep2Keys(body, wiz, ctx, renderWizard);
      } else if (wiz.step === 3) {
        renderStep3Taste(body, wiz, ctx, renderWizard);
      } else {
        renderStep4Launch(body, wiz, ctx, renderWizard);
      }
      dialog.appendChild(body);

      // Footer Navigation
      const foot = document.createElement("div");
      foot.className = "row-between";
      foot.style.marginTop = "20px";
      foot.style.paddingTop = "14px";
      foot.style.borderTop = "1px solid var(--border)";

      const backBtn = document.createElement("button");
      backBtn.className = "btn";
      backBtn.textContent = "← Back";
      backBtn.disabled = wiz.step === 1 || wiz.launching;
      backBtn.onclick = () => { wiz.step -= 1; renderWizard(); };

      const rightBtns = document.createElement("div");
      rightBtns.className = "row-wrap";
      if (wiz.step < 4) {
        const nextBtn = document.createElement("button");
        nextBtn.className = "btn primary";
        nextBtn.textContent = `Continue to Step ${wiz.step + 1} →`;
        nextBtn.onclick = () => { wiz.step += 1; renderWizard(); };
        rightBtns.appendChild(nextBtn);
      } else if (wiz.launchDone) {
        const finishBtn = document.createElement("button");
        finishBtn.className = "btn primary";
        finishBtn.textContent = "🎉 Open Studio Workspace";
        finishBtn.onclick = () => {
          try { sessionStorage.setItem("sw_wizard_dismissed", "1"); } catch (_) {}
          ctx.closeModal();
          ctx.render();
        };
        rightBtns.appendChild(finishBtn);
      }
      foot.append(backBtn, rightBtns);
      dialog.appendChild(foot);
    };

    renderWizard();
  });
}

function renderStep1Hosting(container, wiz, ctx, rerender) {
  const cards = document.createElement("div");
  cards.className = "grid-equal";

  const elfCard = document.createElement("div");
  elfCard.className = "pack-card" + (wiz.hostingMode === "elfhosted" ? " on" : "");
  elfCard.style.cursor = "pointer";
  elfCard.onclick = () => {
    wiz.hostingMode = "elfhosted";
    ctx.setPendingEnv("AIOSTREAMS_URL", "https://aiostreams.elfhosted.com");
    ctx.setPendingEnv("AIOMETADATA_URL", "https://aiometadata.elfhosted.com");
    rerender();
  };
  elfCard.innerHTML = `
    <div class="row-between">
      <strong>⚡ ElfHosted Public Free Instances</strong>
      <span class="pill ${wiz.hostingMode === "elfhosted" ? "ok" : ""}">1-Click Prefill</span>
    </div>
    <div style="font-size:12.5px;color:var(--muted)">
      Zero server maintenance. Uses community-hosted <code>aiostreams.elfhosted.com</code> and <code>aiometadata.elfhosted.com</code>—Studio provisions your private encrypted UUIDs automatically.
    </div>
  `;

  const customCard = document.createElement("div");
  customCard.className = "pack-card" + (wiz.hostingMode === "custom" ? " on" : "");
  customCard.style.cursor = "pointer";
  customCard.onclick = () => {
    wiz.hostingMode = "custom";
    rerender();
  };
  customCard.innerHTML = `
    <div class="row-between">
      <strong>🏠 Custom / Self-Hosted URLs</strong>
      <span class="pill ${wiz.hostingMode === "custom" ? "accent" : ""}">Docker / VPS</span>
    </div>
    <div style="font-size:12.5px;color:var(--muted)">
      Connect your own self-hosted AIOStreams, AIOMetadata, and optional AIOManager instances (via <code>docker/docker-compose.yml</code> or reverse-proxied domain).
    </div>
  `;
  cards.append(elfCard, customCard);
  container.appendChild(cards);

  const urlBox = document.createElement("div");
  urlBox.className = "rows-box";
  const topRow = document.createElement("div");
  topRow.className = "row-between";
  topRow.style.marginBottom = "10px";
  topRow.innerHTML = `<strong style="font-size:13px">Target Service Endpoints</strong>`;
  if (wiz.hostingMode === "custom") {
    const dockerBtn = document.createElement("button");
    dockerBtn.className = "btn icon-btn accent-outline";
    dockerBtn.textContent = "🐳 Prefill Local Docker (:3000 / :3232 / :1610)";
    dockerBtn.onclick = () => {
      ctx.setPendingEnv("AIOSTREAMS_URL", "http://localhost:3000");
      ctx.setPendingEnv("AIOMETADATA_URL", "http://localhost:3232");
      ctx.setPendingEnv("AIOMANAGER_URL", "http://localhost:1610");
      rerender();
    };
    topRow.appendChild(dockerBtn);
  }
  urlBox.appendChild(topRow);

  const grid = document.createElement("div");
  grid.className = "cards-grid";
  [
    ["AIOSTREAMS_URL", "AIOStreams Base URL", "https://aiostreams.elfhosted.com"],
    ["AIOMETADATA_URL", "AIOMetadata Base URL", "https://aiometadata.elfhosted.com"],
    ["AIOMANAGER_URL", "AIOManager URL (Optional)", "https://aiomanager.elfhosted.com"],
  ].forEach(([key, label, placeholder]) => {
    const lbl = document.createElement("label");
    lbl.className = "field";
    lbl.textContent = label;
    const inp = document.createElement("input");
    inp.type = "text";
    inp.placeholder = placeholder;
    inp.value = ctx.effectiveEnvValue(key);
    inp.oninput = () => {
      wiz.hostingMode = "custom";
      ctx.setPendingEnv(key, inp.value);
    };
    lbl.appendChild(inp);
    grid.appendChild(lbl);
  });
  urlBox.appendChild(grid);
  container.appendChild(urlBox);
}

function renderStep2Keys(container, wiz, ctx, rerender) {
  const { escapeHtml } = ctx.ui;

  // Primary Debrid selector
  const debBox = document.createElement("div");
  debBox.className = "rows-box";
  debBox.style.marginTop = "0";
  debBox.innerHTML = `<div style="font-size:13px;font-weight:700;margin-bottom:8px">1. Choose Primary Debrid / Usenet Provider</div>`;
  const pills = document.createElement("div");
  pills.className = "chip-row";
  DEBRID_WIZARD_OPTIONS.forEach(opt => {
    const b = document.createElement("button");
    b.className = "btn" + (wiz.debrid === opt.id ? " accent-outline" : "");
    b.textContent = (wiz.debrid === opt.id ? "✓ " : "") + opt.label;
    b.onclick = () => { wiz.debrid = opt.id; rerender(); };
    pills.appendChild(b);
  });
  debBox.appendChild(pills);

  const chosenDebrid = DEBRID_WIZARD_OPTIONS.find(d => d.id === wiz.debrid) || DEBRID_WIZARD_OPTIONS[0];
  const debKeyGrid = document.createElement("div");
  debKeyGrid.className = "cards-grid";
  debKeyGrid.style.marginTop = "12px";
  chosenDebrid.keys.forEach(k => {
    debKeyGrid.appendChild(makeWizardEnvInput(ctx, k, `${chosenDebrid.label} (${k})`, chosenDebrid.url));
  });
  debBox.appendChild(debKeyGrid);
  container.appendChild(debBox);

  // Instance Passwords + Metadata Keys
  const metaBox = document.createElement("div");
  metaBox.className = "rows-box";
  const pwHead = document.createElement("div");
  pwHead.className = "row-between";
  pwHead.style.marginBottom = "10px";
  pwHead.innerHTML = `<div style="font-size:13px;font-weight:700">2. Instance Passwords &amp; Metadata API Keys</div>`;
  const genBtn = document.createElement("button");
  genBtn.className = "btn icon-btn accent-outline";
  genBtn.textContent = "🎲 Generate Instance Passwords";
  genBtn.onclick = () => {
    const pw = randomPassword();
    ctx.setPendingEnv("AIOSTREAMS_PASSWORD", pw);
    ctx.setPendingEnv("AIOMETADATA_PASSWORD", pw);
    rerender();
  };
  pwHead.appendChild(genBtn);
  metaBox.appendChild(pwHead);

  const keyGrid = document.createElement("div");
  keyGrid.className = "cards-grid";
  [
    ["AIOSTREAMS_PASSWORD", "New AIOStreams Instance Password", ""],
    ["AIOMETADATA_PASSWORD", "New AIOMetadata Instance Password", ""],
    ["SECRET_TMDB_API_KEY", "TMDB v3 API Key (Required)", "https://www.themoviedb.org/settings/api"],
    ["SECRET_TMDB_ACCESS_TOKEN", "TMDB v4 Read Access Token (Required)", "https://www.themoviedb.org/settings/api"],
    ["SECRET_TVDB_API_KEY", "TheTVDB v4 API Key (Required)", "https://thetvdb.com/api-information"],
    ["SECRET_MDBLIST_API_KEY", "MDBList API Key (8-Source Ratings, Optional)", "https://mdblist.com/preferences"],
  ].forEach(([k, label, url]) => {
    keyGrid.appendChild(makeWizardEnvInput(ctx, k, label, url));
  });

  // RPDB with 1-click free tier helper
  const rpdbWrap = makeWizardEnvInput(ctx, "SECRET_RPDB_API_KEY", "RatingPosterDB Key (Optional)", "https://ratingposterdb.com");
  const freeRpdbBtn = document.createElement("button");
  freeRpdbBtn.className = "btn icon-btn";
  freeRpdbBtn.style.marginTop = "4px";
  freeRpdbBtn.textContent = "Use Free Tier (t0-free-rpdb)";
  freeRpdbBtn.onclick = () => {
    ctx.setPendingEnv("SECRET_RPDB_API_KEY", "t0-free-rpdb");
    rerender();
  };
  rpdbWrap.appendChild(freeRpdbBtn);
  keyGrid.appendChild(rpdbWrap);

  metaBox.appendChild(keyGrid);
  container.appendChild(metaBox);

  // Live Key Verification via Doctor
  const docBox = document.createElement("div");
  docBox.className = "rows-box";
  const docTop = document.createElement("div");
  docTop.className = "row-between";
  docTop.innerHTML = `
    <div>
      <strong style="font-size:13px">3. Live Connectivity &amp; Key Verification</strong>
      <div style="font-size:11.5px;color:var(--muted)">Probes AIOStreams, AIOMetadata, Debrid, TMDB, and MDBList directly before provisioning.</div>
    </div>
  `;
  const verifyBtn = document.createElement("button");
  verifyBtn.className = "btn accent-outline";
  verifyBtn.disabled = wiz.verifying;
  verifyBtn.textContent = wiz.verifying ? "🩺 Verifying…" : "🩺 Verify Keys Live";
  verifyBtn.onclick = async () => {
    wiz.verifying = true;
    rerender();
    try {
      const res = await ctx.api("/api/doctor", { pendingEnv: ctx.pendingEnvEdits });
      wiz.doctorReport = res.report || res;
    } catch (err) {
      wiz.doctorReport = { overall: "error", checks: [{ label: "Doctor Probe", status: "error", detail: err.message }] };
    } finally {
      wiz.verifying = false;
      rerender();
    }
  };
  docTop.appendChild(verifyBtn);
  docBox.appendChild(docTop);

  if (wiz.doctorReport && Array.isArray(wiz.doctorReport.checks)) {
    const checksWrap = document.createElement("div");
    checksWrap.className = "cards-grid-sm";
    checksWrap.style.marginTop = "12px";
    wiz.doctorReport.checks.forEach(chk => {
      const icon = chk.status === "ok" ? "✅" : (chk.status === "warn" ? "⚠️" : (chk.status === "error" ? "❌" : "⏭️"));
      const pillCls = chk.status === "ok" ? "ok" : (chk.status === "warn" ? "warn" : (chk.status === "error" ? "danger" : ""));
      const item = document.createElement("div");
      item.className = "env-card";
      item.innerHTML = `
        <div class="row-between">
          <strong style="font-size:12px">${icon} ${escapeHtml(chk.label)}</strong>
          <span class="pill ${pillCls}">${escapeHtml(chk.status)}</span>
        </div>
        <div style="font-size:11px;color:var(--muted)">${escapeHtml(chk.detail)}</div>
      `;
      checksWrap.appendChild(item);
    });
    docBox.appendChild(checksWrap);
  }
  container.appendChild(docBox);
}

function makeWizardEnvInput(ctx, key, label, signupUrl) {
  const { escapeHtml } = ctx.ui;
  const spec = (ctx.state.env || []).find(e => e.key === key);
  const isSecret = spec ? Boolean(spec.isSecret) : key.startsWith("SECRET_") || key.endsWith("_PASSWORD");
  const pending = ctx.pendingEnvEdits[key] || "";
  const isSet = Boolean((spec && spec.isSet) || pending.trim());

  const card = document.createElement("div");
  card.className = "env-card";
  card.innerHTML = `
    <div class="row-between">
      <strong style="font-size:12px">${escapeHtml(label)}</strong>
      <span class="pill ${isSet ? "ok" : "warn"}">${isSet ? "Ready ✓" : "Needed"}</span>
    </div>
    <div class="row-between" style="font-size:11px;color:var(--muted)">
      <code>${escapeHtml(key)}</code>
      ${signupUrl ? `<a href="${escapeHtml(signupUrl)}" target="_blank" rel="noreferrer">Get Key ↗</a>` : ""}
    </div>
  `;
  const inp = document.createElement("input");
  inp.type = isSecret ? "password" : "text";
  inp.value = pending || (!isSecret && spec ? spec.value || "" : "");
  inp.placeholder = isSecret && spec && spec.isSet ? `${spec.masked} (saved)` : "Paste value…";
  inp.oninput = () => {
    ctx.setPendingEnv(key, inp.value);
  };
  card.appendChild(inp);
  return card;
}

function renderStep3Taste(container, wiz, ctx, rerender) {
  const { escapeHtml } = ctx.ui;
  const st = ctx.state;
  const prof = ctx.currentProfile();

  // Starter Collections
  const colBox = document.createElement("div");
  colBox.className = "rows-box";
  colBox.style.marginTop = "0";
  colBox.innerHTML = `<div style="font-size:13px;font-weight:700;margin-bottom:8px">1. Pick Starter Home Collections</div>`;
  const colGrid = document.createElement("div");
  colGrid.className = "cards-grid-sm";
  (st.libraryCollections || [])
    .filter(lc => !lc.presets || lc.presets.length === 0)
    .forEach(lc => {
      const checked = wiz.selectedCols.has(lc.id);
      const card = document.createElement("label");
      card.className = "pack-card" + (checked ? " on" : "");
      card.style.cursor = "pointer";
      const chk = document.createElement("input");
      chk.type = "checkbox";
      chk.checked = checked;
      chk.onchange = () => {
        if (chk.checked) wiz.selectedCols.add(lc.id);
        else if (wiz.selectedCols.size > 1) wiz.selectedCols.delete(lc.id);
        else chk.checked = true;
        rerender();
      };
      const top = document.createElement("div");
      top.className = "switch";
      top.append(chk, document.createTextNode(lc.title));
      const sub = document.createElement("div");
      sub.style.fontSize = "11px";
      sub.style.color = "var(--muted)";
      sub.textContent = `${(lc.folders || []).length} folders`;
      card.append(top, sub);
      colGrid.appendChild(card);
    });
  colBox.appendChild(colGrid);

  // Regional Cinema pills
  const regRecipe = (st.libraryCollections || []).find(lc => lc.id === "regional-cinema");
  if (regRecipe && Array.isArray(regRecipe.presets) && regRecipe.presets.length) {
    const regTitle = document.createElement("div");
    regTitle.style.fontSize = "12.5px";
    regTitle.style.fontWeight = "600";
    regTitle.style.margin = "14px 0 6px";
    regTitle.textContent = "🌍 Optional Regional Cinema Shelves:";
    colBox.appendChild(regTitle);
    const regChips = document.createElement("div");
    regChips.className = "chip-row";
    regRecipe.presets.forEach(pr => {
      const code = pr.with.language;
      const active = wiz.selectedRegional.has(code);
      const b = document.createElement("button");
      b.className = "btn icon-btn" + (active ? " accent-outline" : "");
      b.textContent = `${active ? "✓ " : "+ "}${pr.label}`;
      b.onclick = () => {
        if (active) wiz.selectedRegional.delete(code);
        else wiz.selectedRegional.add(code);
        rerender();
      };
      regChips.appendChild(b);
    });
    colBox.appendChild(regChips);
  }
  container.appendChild(colBox);

  // Subtitle & Dual-Sub preferences
  const subBox = document.createElement("div");
  subBox.className = "rows-box";
  subBox.innerHTML = `<div style="font-size:13px;font-weight:700;margin-bottom:8px">2. Subtitles &amp; Bilingual Dual-Subtitles</div>`;
  const subGrid = document.createElement("div");
  subGrid.className = "grid-equal";

  const prefLbl = document.createElement("label");
  prefLbl.className = "field";
  prefLbl.textContent = "Preferred Subtitle Language";
  const prefSel = document.createElement("select");
  [
    ["en", "English"], ["es", "Spanish"], ["fr", "French"], ["de", "German"],
    ["ja", "Japanese"], ["ko", "Korean"], ["hi", "Hindi"], ["kn", "Kannada"],
    ["ta", "Tamil"], ["te", "Telugu"], ["ml", "Malayalam"],
  ].forEach(([code, label]) => {
    const o = document.createElement("option");
    o.value = code;
    o.textContent = label;
    if (wiz.subtitlePref === code) o.selected = true;
    prefSel.appendChild(o);
  });
  prefSel.onchange = () => { wiz.subtitlePref = prefSel.value; };
  prefLbl.appendChild(prefSel);

  const dualLbl = document.createElement("label");
  dualLbl.className = "field";
  dualLbl.textContent = "Strelingo Bilingual Dual-Sub Pair (Optional)";
  const dualSel = document.createElement("select");
  QUICK_DUAL_SUB_PAIRS.forEach(opt => {
    const o = document.createElement("option");
    o.value = opt.id;
    o.textContent = opt.label;
    if (wiz.dualSubQuick === opt.id) o.selected = true;
    dualSel.appendChild(o);
  });
  dualSel.onchange = () => {
    wiz.dualSubQuick = dualSel.value;
    rerender();
  };
  dualLbl.appendChild(dualSel);
  subGrid.append(prefLbl, dualLbl);
  subBox.appendChild(subGrid);
  if (wiz.dualSubQuick) {
    const subKeyWrap = document.createElement("div");
    subKeyWrap.style.marginTop = "10px";
    subKeyWrap.appendChild(makeWizardEnvInput(ctx, "SECRET_SUBSOURCE_API_KEY", "SubSource API Key (for Dual-Subs)", "https://subsource.net"));
    subBox.appendChild(subKeyWrap);
  }
  container.appendChild(subBox);

  // Nuvio Sign-In
  const nuvBox = document.createElement("div");
  nuvBox.className = "rows-box";
  const nuvioReady = Boolean(prof?.nuvio?.loginReady);
  nuvBox.innerHTML = `
    <div class="row-between" style="margin-bottom:8px">
      <strong style="font-size:13px">3. Connect Your Nuvio Account</strong>
      <span class="pill ${nuvioReady ? "ok" : "warn"}">${nuvioReady ? `Signed In ${escapeHtml(prof?.nuvio?.email || "")}` : "Sign in to sync Home & Settings"}</span>
    </div>
  `;
  const nuvGrid = document.createElement("div");
  nuvGrid.className = "cards-grid-sm";
  nuvGrid.style.alignItems = "end";

  const emL = document.createElement("label");
  emL.className = "field"; emL.textContent = "Nuvio Email";
  const emIn = document.createElement("input");
  emIn.type = "email"; emIn.placeholder = "you@example.com"; emIn.value = wiz.nuvioEmail;
  emIn.oninput = () => { wiz.nuvioEmail = emIn.value; };
  emL.appendChild(emIn);

  const pwL = document.createElement("label");
  pwL.className = "field"; pwL.textContent = "Nuvio Password";
  const pwIn = document.createElement("input");
  pwIn.type = "password"; pwIn.placeholder = "••••••••"; pwIn.value = wiz.nuvioPassword;
  pwIn.oninput = () => { wiz.nuvioPassword = pwIn.value; };
  pwL.appendChild(pwIn);

  const prL = document.createElement("label");
  prL.className = "field"; prL.textContent = "Target Nuvio Profile";
  const prSel = document.createElement("select");
  const defO = document.createElement("option");
  defO.value = "";
  defO.textContent = "Profile #1 (Default)";
  prSel.appendChild(defO);
  (ctx.nuvioProfilesCache || []).forEach(np => {
    const o = document.createElement("option");
    o.value = np.id;
    o.textContent = `#${np.index || ""} ${np.name}`;
    if (wiz.nuvioProfileId === np.id) o.selected = true;
    prSel.appendChild(o);
  });
  prSel.onchange = () => { wiz.nuvioProfileId = prSel.value; };
  prL.appendChild(prSel);

  const authBtn = document.createElement("button");
  authBtn.className = "btn accent-outline";
  authBtn.textContent = nuvioReady ? "🔄 Re-Authenticate" : "🔐 Sign In to Nuvio";
  authBtn.onclick = async () => {
    authBtn.disabled = true;
    try {
      const loginRes = await ctx.api("/api/nuvio-login", {
        login: prof?.nuvio?.login || "main",
        email: wiz.nuvioEmail,
        password: wiz.nuvioPassword,
      });
      ctx.setNuvioProfiles((loginRes.nuvio && loginRes.nuvio.profiles) || []);
      wiz.nuvioPassword = "";
      rerender();
    } catch (err) {
      ctx.showLog(err.message, [], true);
      authBtn.disabled = false;
    }
  };
  nuvGrid.append(emL, pwL, prL, authBtn);
  nuvBox.appendChild(nuvGrid);
  container.appendChild(nuvBox);
}

function renderStep4Launch(container, wiz, ctx, rerender) {
  const { escapeHtml } = ctx.ui;
  const st = ctx.state;
  const prof = ctx.currentProfile();
  const nuvioReady = Boolean(prof?.nuvio?.loginReady);

  const summaryGrid = document.createElement("div");
  summaryGrid.className = "cards-grid-sm";
  [
    ["Hosting", wiz.hostingMode === "elfhosted" ? "ElfHosted Public" : "Custom Self-Hosted"],
    ["Primary Provider", wiz.debrid.toUpperCase()],
    ["Home Shelves", `${wiz.selectedCols.size + wiz.selectedRegional.size} collections selected`],
    ["Nuvio Sync", nuvioReady ? "Authenticated & Ready ✅" : "Instances only (sign in anytime)"],
  ].forEach(([k, v]) => {
    const c = document.createElement("div");
    c.className = "env-card";
    c.innerHTML = `<span style="font-size:11px;color:var(--muted)">${escapeHtml(k)}</span><strong>${escapeHtml(v)}</strong>`;
    summaryGrid.appendChild(c);
  });
  container.appendChild(summaryGrid);

  const launchBox = document.createElement("div");
  launchBox.className = "rows-box";
  launchBox.style.borderColor = "var(--border-strong)";
  launchBox.innerHTML = `
    <div class="row-between" style="flex-wrap:wrap">
      <div>
        <strong style="font-size:14px">Ready to Provision &amp; Push</strong>
        <div style="font-size:12px;color:var(--muted);margin-top:2px">
          Creates encrypted AIOStreams &amp; AIOMetadata instances, derives catalogs, and syncs straight to Nuvio.
        </div>
      </div>
    </div>
  `;

  const launchBtn = document.createElement("button");
  launchBtn.className = "btn primary";
  launchBtn.style.marginTop = "12px";
  launchBtn.disabled = wiz.launching;
  launchBtn.textContent = wiz.launching
    ? "⏳ Provisioning & Streaming Push…"
    : "🚀 Provision Instances & Push to Nuvio";

  launchBtn.onclick = async () => {
    wiz.launching = true;
    wiz.launchError = "";
    wiz.launchSteps = ["Saving wizard preferences & keys…"];
    rerender();

    try {
      const regRecipe = (st.libraryCollections || []).find(lc => lc.id === "regional-cinema");
      const collectionsPayload = [
        ...Array.from(wiz.selectedCols).map(ref => ({ ref })),
      ];
      if (regRecipe && Array.isArray(regRecipe.presets)) {
        regRecipe.presets.forEach(pr => {
          if (wiz.selectedRegional.has(pr.with.language)) {
            collectionsPayload.push({ ref: "regional-cinema", with: { ...pr.with } });
          }
        });
      }

      const enabledPacks = (st.packs || [])
        .filter(pk => pk.enabled || (wiz.dualSubQuick && pk.id === "dual_subtitles"))
        .map(pk => pk.id);
      if (wiz.dualSubQuick && !enabledPacks.includes("dual_subtitles")) {
        enabledPacks.push("dual_subtitles");
      }

      const mut = {
        envUpdates: { ...ctx.pendingEnvEdits },
        enabledPacks,
        dualSubtitlePairs: wiz.dualSubQuick ? [wiz.dualSubQuick.split(":")] : (st.dualSubtitles?.pairs || []),
      };
      if (wiz.debrid === "easynews") {
        mut.debridServices = [{ id: "easynews", enabled: true, priority: 1 }];
      } else {
        mut.debrid = wiz.debrid;
        mut.debridServices = [{ id: wiz.debrid, enabled: true, priority: 1 }];
      }
      if (prof) {
        const hasMdb = ctx.isEnvKeyReady("SECRET_MDBLIST_API_KEY");
        mut.profile = {
          name: prof.name,
          collections: collectionsPayload,
          nuvio: prof.nuvio ? {
            profileId: wiz.nuvioProfileId || prof.nuvio.profileId || "",
            profileName: prof.nuvio.profileName || "",
            platforms: prof.nuvio.platforms,
          } : undefined,
          meta: { enabled: hasMdb, ref: "default", with: prof.meta?.with || {} },
          settings: {
            enabled: true,
            ref: "default",
            with: { ...(prof.settings?.with || {}), subtitlePreferred: wiz.subtitlePref },
          },
        };
      }

      await ctx.api("/api/mutate", { mutation: mut });
      ctx.clearPendingEdits();

      const alreadyProv = ctx.state.tenant?.aiostreamsProvisioned && ctx.state.tenant?.aiometadataProvisioned;
      if (!alreadyProv) {
        wiz.launchSteps.push("Provisioning AIOStreams & AIOMetadata UUIDs…");
        rerender();
        const provRes = await ctx.api("/api/provision", {});
        (provRes.steps || []).forEach(s => wiz.launchSteps.push(`✓ ${s}`));
        rerender();
      }

      const pushTarget = "all";
      wiz.launchSteps.push(`Streaming workspace push (target: ${pushTarget})…`);
      rerender();
      await ctx.streamPush({ target: pushTarget }, (stepLine) => {
        wiz.launchSteps.push(`✓ ${stepLine}`);
        rerender();
      });

      wiz.launchDone = true;
    } catch (err) {
      wiz.launchError = err.message || String(err);
    } finally {
      wiz.launching = false;
      rerender();
    }
  };
  launchBox.appendChild(launchBtn);

  if (wiz.launchSteps.length || wiz.launchError) {
    const logOut = document.createElement("div");
    logOut.className = "log-drawer";
    logOut.style.marginTop = "14px";
    logOut.style.marginBottom = "0";
    if (wiz.launchError) logOut.classList.add("error");
    logOut.innerHTML = wiz.launchSteps.map(s => `<div>${escapeHtml(s)}</div>`).join("") +
      (wiz.launchError ? `<div style="color:#f87171;margin-top:6px">❌ ${escapeHtml(wiz.launchError)}</div>` : "");
    launchBox.appendChild(logOut);
  }

  // Deep-links once provisioned
  const tInfo = ctx.state.tenant || {};
  if (tInfo.aiostreamsProvisioned || tInfo.aiometadataProvisioned) {
    const linksRow = document.createElement("div");
    linksRow.className = "row-wrap";
    linksRow.style.marginTop = "14px";
    if (tInfo.aiostreamsConfigureUrl) {
      const a1 = document.createElement("a");
      a1.className = "btn accent-outline";
      a1.href = tInfo.aiostreamsConfigureUrl;
      a1.target = "_blank";
      a1.rel = "noreferrer";
      a1.textContent = "🔧 Open AIOStreams Configure ↗";
      linksRow.appendChild(a1);
    }
    if (tInfo.aiometadataConfigureUrl) {
      const a2 = document.createElement("a");
      a2.className = "btn accent-outline";
      a2.href = tInfo.aiometadataConfigureUrl;
      a2.target = "_blank";
      a2.rel = "noreferrer";
      a2.textContent = "🎬 Open AIOMetadata Configure ↗";
      linksRow.appendChild(a2);
    }
    launchBox.appendChild(linksRow);
  }

  container.appendChild(launchBox);
}
