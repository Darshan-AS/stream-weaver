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

export const KEY_HELP = {
  SECRET_TMDB_API_KEY: "32-char hex v3 API Key (TMDB → Settings → API)",
  SECRET_TMDB_ACCESS_TOKEN: "Long JWT Read Access Token starting with eyJ… (TMDB → Settings → API)",
  SECRET_TVDB_API_KEY: "Project API Key (UUID) from thetvdb.com/api-information",
  SECRET_TORBOX_API_KEY: "TorBox Settings → API Key",
  SECRET_RD_API_KEY: "Private API Token from real-debrid.com/apitoken",
  SECRET_ALLDEBRID_API_KEY: "API Key created at alldebrid.com/apikeys",
  SECRET_PREMIUMIZE_API_KEY: "API Key from premiumize.me/account",
};

export function validateKeyFormat(key, rawVal) {
  const val = String(rawVal || "").trim();
  if (!val) return null;
  if (key === "SECRET_TMDB_API_KEY") {
    if (val.startsWith("eyJ") || val.length > 64) {
      return {
        level: "warn",
        msg: "Looks like a TMDB v4 Read Access Token (JWT) — paste it into SECRET_TMDB_ACCESS_TOKEN instead.",
      };
    }
    if (!/^[a-f0-9]{32}$/i.test(val)) {
      return {
        level: "warn",
        msg: "TMDB v3 API Keys are usually 32 hex characters.",
      };
    }
  } else if (key === "SECRET_TMDB_ACCESS_TOKEN") {
    if (/^[a-f0-9]{32}$/i.test(val)) {
      return {
        level: "warn",
        msg: "Looks like a TMDB v3 API Key — paste it into SECRET_TMDB_API_KEY instead.",
      };
    }
  }
  return null;
}

function randomPassword() {
  const chars = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const bytes = new Uint8Array(18);
  crypto.getRandomValues(bytes);
  return "sw-" + Array.from(bytes, b => chars[b % chars.length]).join("");
}

function effectiveWizardEnv(ctx, wiz) {
  const out = { ...ctx.pendingEnvEdits };
  if (wiz.hostingMode === "elfhosted") {
    if (!ctx.isEnvKeyReady("AIOSTREAMS_URL") && !("AIOSTREAMS_URL" in out)) {
      out.AIOSTREAMS_URL = "https://aiostreams.elfhosted.com";
    }
    if (!ctx.isEnvKeyReady("AIOMETADATA_URL") && !("AIOMETADATA_URL" in out)) {
      out.AIOMETADATA_URL = "https://aiometadata.elfhosted.com";
    }
  }
  if (wiz.setupMode === "fresh") {
    if (wiz.autoStreamsPw && !ctx.isEnvKeyReady("AIOSTREAMS_PASSWORD") && !("AIOSTREAMS_PASSWORD" in out)) {
      out.AIOSTREAMS_PASSWORD = wiz.autoStreamsPw;
    }
    if (wiz.autoMetaPw && !ctx.isEnvKeyReady("AIOMETADATA_PASSWORD") && !("AIOMETADATA_PASSWORD" in out)) {
      out.AIOMETADATA_PASSWORD = wiz.autoMetaPw;
    }
  }
  return out;
}

function isWizardKeyReady(ctx, wiz, key) {
  if (ctx.isEnvKeyReady(key)) return true;
  if (wiz.hostingMode === "elfhosted" && (key === "AIOSTREAMS_URL" || key === "AIOMETADATA_URL")) {
    return true;
  }
  if (wiz.setupMode === "fresh") {
    if (key === "AIOSTREAMS_PASSWORD" && wiz.autoStreamsPw) return true;
    if (key === "AIOMETADATA_PASSWORD" && wiz.autoMetaPw) return true;
  }
  return false;
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
    setupMode: "fresh", // "fresh" | "adopt"
    hostingMode: isElfDefault ? "elfhosted" : "custom",
    debrid: activeDebridFromServices,
    autoStreamsPw: ctx.isEnvKeyReady("AIOSTREAMS_PASSWORD") ? "" : randomPassword(),
    autoMetaPw: ctx.isEnvKeyReady("AIOMETADATA_PASSWORD") ? "" : randomPassword(),
    adoptPulling: false,
    adoptSteps: [],
    adoptError: "",
    adoptDone: false,
    selectedCols: existingColRefs.size ? existingColRefs : new Set(["col-for-you-discovery", "col-streaming-services", "col-genres-moods"]),
    selectedRegional: existingRegLangs,
    subtitlePref: (prof?.settings?.with?.subtitlePreferred) || "en",
    dualSubQuick: (st.dualSubtitles?.pairs?.length ? `${st.dualSubtitles.pairs[0][0]}:${st.dualSubtitles.pairs[0][1]}` : ""),
    nuvioEmail: prof?.nuvio?.email || "",
    nuvioPassword: "",
    nuvioProfileId: prof?.nuvio?.profileId || "",
    wantStremio: Boolean(prof?.stremioEnabled),
    stremioEmail: prof?.stremio?.email || st.tenant?.stremioEmail || "",
    stremioPassword: "",
    stremioSigningIn: false,
    doctorReport: null,
    verifying: false,
    launching: false,
    launchSteps: [],
    launchDone: false,
    launchError: "",
  };

  ctx.openModal((dialog) => {
    const { escapeHtml, icon } = ctx.ui;
    const renderWizard = () => {
      dialog.innerHTML = "";
      dialog.classList.add("modal-wide");

      // Header
      const head = document.createElement("div");
      head.className = "row-between";
      head.innerHTML = `
        <div>
          <h2>${icon("compass", 17)} Stream Weaver Day-0 Setup Wizard</h2>
          <p class="sub" style="margin:3px 0 0">Zero-visit provisioning: configure hosting, verify keys live, pick your shelves, and launch in minutes.</p>
        </div>
      `;
      const dismissBtn = document.createElement("button");
      dismissBtn.className = "btn ghost";
      dismissBtn.innerHTML = `${icon("x", 14)} <span>Dismiss</span>`;
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
        [1, "1. Hosting & Mode", wiz.setupMode === "adopt" ? "Adopt Existing UUIDs" : "ElfHosted or Custom"],
        [2, "2. Keys & Check", "Debrid, TMDB & Live Doctor"],
        [3, "3. Taste & Logins", "Shelves, Nuvio & Stremio"],
        [4, "4. Launch", "Provision, Push & Devices"],
      ].forEach(([num, title, desc]) => {
        const b = document.createElement("button");
        b.className = "wizard-step-pill" + (wiz.step === num ? " active" : (wiz.step > num ? " done" : ""));
        b.innerHTML = `<div style="display:inline-flex;align-items:center;gap:5px">${wiz.step > num ? icon("check", 12) : ""}${escapeHtml(title)}</div>
          <div style="font-size:10.5px;font-weight:400;opacity:0.8">${escapeHtml(desc)}</div>`;
        b.onclick = () => {
          if (!wiz.launching && !wiz.adoptPulling) {
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
      backBtn.disabled = wiz.step === 1 || wiz.launching || wiz.adoptPulling;
      backBtn.onclick = () => { wiz.step -= 1; renderWizard(); };

      const rightBtns = document.createElement("div");
      rightBtns.className = "row-wrap";
      if (wiz.step < 4) {
        const nextBtn = document.createElement("button");
        nextBtn.className = "btn primary";
        nextBtn.disabled = wiz.adoptPulling;
        nextBtn.textContent = `Continue to Step ${wiz.step + 1} →`;
        nextBtn.onclick = () => { wiz.step += 1; renderWizard(); };
        rightBtns.appendChild(nextBtn);
      } else if (wiz.launchDone) {
        const finishBtn = document.createElement("button");
        finishBtn.className = "btn primary";
        finishBtn.innerHTML = `${icon("check", 14)} <span>Open Studio Workspace</span>`;
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
  const { escapeHtml, icon } = ctx.ui;

  // Mode Switcher: Fresh Setup vs Adopt Existing Stack
  const modeBox = document.createElement("div");
  modeBox.className = "rows-box";
  modeBox.style.marginTop = "0";
  modeBox.innerHTML = `<div style="font-size:13px;font-weight:600;margin-bottom:8px">Setup Path</div>`;
  const modeSeg = document.createElement("div");
  modeSeg.className = "segmented";
  [
    ["fresh", "Fresh Setup (Provision New UUIDs)"],
    ["adopt", "Adopt Existing Stack (Paste UUID + Password & Pull)"],
  ].forEach(([mId, lbl]) => {
    const b = document.createElement("button");
    b.className = "seg-btn" + (wiz.setupMode === mId ? " active" : "");
    b.textContent = lbl;
    b.onclick = () => {
      wiz.setupMode = mId;
      rerender();
    };
    modeSeg.appendChild(b);
  });
  modeBox.appendChild(modeSeg);
  container.appendChild(modeBox);

  if (wiz.setupMode === "adopt") {
    const adoptCard = document.createElement("div");
    adoptCard.className = "rows-box";
    adoptCard.innerHTML = `
      <strong style="font-size:13px">Import Existing AIOStreams / AIOMetadata / AIOManager</strong>
      <div style="font-size:12px;color:var(--muted);margin:3px 0 12px">
        Paste your current instance URL, UUID, and Configure Password. Studio will pull your live setup, harvest secrets into <code>.env</code>, and scrub the JSON files.
      </div>
    `;

    const grid = document.createElement("div");
    grid.className = "cards-grid";
    [
      ["AIOSTREAMS_URL", "AIOStreams URL", ""],
      ["AIOSTREAMS_UUID", "AIOStreams UUID", ""],
      ["AIOSTREAMS_PASSWORD", "AIOStreams Password", ""],
      ["AIOMETADATA_URL", "AIOMetadata URL", ""],
      ["AIOMETADATA_UUID", "AIOMetadata UUID", ""],
      ["AIOMETADATA_PASSWORD", "AIOMetadata Password", ""],
      ["AIOMANAGER_URL", "AIOManager URL (Optional)", ""],
      ["AIOMANAGER_UUID", "AIOManager UUID (Optional)", ""],
      ["AIOMANAGER_SYNC_PASSWORD", "AIOManager Sync Password (Optional)", ""],
    ].forEach(([k, label, url]) => {
      const defUrl = k === "AIOSTREAMS_URL"
        ? "https://aiostreams.elfhosted.com"
        : (k === "AIOMETADATA_URL" ? "https://aiometadata.elfhosted.com" : "");
      grid.appendChild(
        makeWizardEnvInput(ctx, k, label, url, {
          prefillValue: !ctx.isEnvKeyReady(k) && !(k in ctx.pendingEnvEdits) ? defUrl : "",
        })
      );
    });
    adoptCard.appendChild(grid);

    const actionRow = document.createElement("div");
    actionRow.className = "row-wrap";
    actionRow.style.marginTop = "12px";
    const pullBtn = document.createElement("button");
    pullBtn.className = "btn primary";
    pullBtn.disabled = wiz.adoptPulling;
    pullBtn.innerHTML = `${icon("download", 14)} <span>${wiz.adoptPulling ? "Pulling Existing Stack…" : "Save Credentials & Pull Existing Stack"}</span>`;
    pullBtn.onclick = async () => {
      wiz.adoptPulling = true;
      wiz.adoptError = "";
      wiz.adoptSteps = ["> Saving instance credentials…"];
      rerender();
      try {
        const envUpdates = { ...ctx.pendingEnvEdits };
        if (!ctx.isEnvKeyReady("AIOSTREAMS_URL") && !envUpdates.AIOSTREAMS_URL) {
          envUpdates.AIOSTREAMS_URL = "https://aiostreams.elfhosted.com";
        }
        if (!ctx.isEnvKeyReady("AIOMETADATA_URL") && !envUpdates.AIOMETADATA_URL) {
          envUpdates.AIOMETADATA_URL = "https://aiometadata.elfhosted.com";
        }
        if (Object.keys(envUpdates).length) {
          await ctx.api("/api/mutate", { mutation: { envUpdates } });
          ctx.clearPendingEdits();
        }
        wiz.adoptSteps.push("> Pulling remote configurations & harvesting secrets…");
        rerender();
        const pullRes = await ctx.api("/api/pull", { target: "all" });
        (pullRes.steps || []).forEach(s => wiz.adoptSteps.push(`+ ${s}`));
        wiz.adoptSteps.push("+ Done! Continue to Step 2 to review keys and verify connectivity.");
        wiz.adoptDone = true;
      } catch (err) {
        wiz.adoptError = err.message || String(err);
      } finally {
        wiz.adoptPulling = false;
        rerender();
      }
    };
    actionRow.appendChild(pullBtn);
    adoptCard.appendChild(actionRow);

    if (wiz.adoptSteps.length || wiz.adoptError) {
      const logOut = document.createElement("div");
      logOut.className = "log-drawer";
      logOut.style.marginTop = "12px";
      logOut.style.marginBottom = "0";
      if (wiz.adoptError) logOut.classList.add("error");
      logOut.innerHTML = wiz.adoptSteps.map(s => `<div>${escapeHtml(s)}</div>`).join("") +
        (wiz.adoptError ? `<div style="color:var(--danger);margin-top:6px">ERROR: ${escapeHtml(wiz.adoptError)}</div>` : "");
      adoptCard.appendChild(logOut);
    }

    container.appendChild(adoptCard);
    return;
  }

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
      <strong style="display:inline-flex;align-items:center;gap:6px">${icon("zap", 14)} ElfHosted Public Free Instances</strong>
      <span class="pill ${wiz.hostingMode === "elfhosted" ? "ok" : ""}">1-Click Prefill</span>
    </div>
    <div style="font-size:12.5px;color:var(--muted)">
      Zero server maintenance. Uses community-hosted <code>aiostreams.elfhosted.com</code> and <code>aiometadata.elfhosted.com</code> — Studio provisions your private encrypted UUIDs automatically.
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
      <strong style="display:inline-flex;align-items:center;gap:6px">${icon("home", 14)} Custom / Self-Hosted URLs</strong>
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
    dockerBtn.textContent = "Prefill Local Docker (:3000 / :3232 / :1610)";
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
    inp.value = ctx.effectiveEnvValue(key) || (wiz.hostingMode === "elfhosted" && key !== "AIOMANAGER_URL" ? placeholder : "");
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
  const { escapeHtml, icon } = ctx.ui;

  // Primary Debrid selector
  const debBox = document.createElement("div");
  debBox.className = "rows-box";
  debBox.style.marginTop = "0";
  debBox.innerHTML = `<div style="font-size:13px;font-weight:600;margin-bottom:8px">1. Choose Primary Debrid / Usenet Provider</div>`;
  const pills = document.createElement("div");
  pills.className = "chip-row";
  DEBRID_WIZARD_OPTIONS.forEach(opt => {
    const b = document.createElement("button");
    b.className = "btn" + (wiz.debrid === opt.id ? " accent-outline" : "");
    b.innerHTML = `${wiz.debrid === opt.id ? icon("check", 12) : ""} <span>${escapeHtml(opt.label)}</span>`;
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
  pwHead.innerHTML = `<div style="font-size:13px;font-weight:600">2. Instance Passwords &amp; Metadata API Keys</div>`;
  if (wiz.setupMode === "fresh") {
    const genBtn = document.createElement("button");
    genBtn.className = "btn icon-btn accent-outline";
    genBtn.innerHTML = `${icon("wand", 12)} <span>Regenerate Passwords</span>`;
    genBtn.onclick = () => {
      wiz.autoStreamsPw = randomPassword();
      wiz.autoMetaPw = randomPassword();
      ctx.setPendingEnv("AIOSTREAMS_PASSWORD", wiz.autoStreamsPw);
      ctx.setPendingEnv("AIOMETADATA_PASSWORD", wiz.autoMetaPw);
      rerender();
    };
    pwHead.appendChild(genBtn);
  }
  metaBox.appendChild(pwHead);

  const keyGrid = document.createElement("div");
  keyGrid.className = "cards-grid";
  const showAutoStreams = wiz.setupMode === "fresh" && !ctx.isEnvKeyReady("AIOSTREAMS_PASSWORD") && wiz.autoStreamsPw;
  const showAutoMeta = wiz.setupMode === "fresh" && !ctx.isEnvKeyReady("AIOMETADATA_PASSWORD") && wiz.autoMetaPw;
  keyGrid.appendChild(
    makeWizardEnvInput(ctx, "AIOSTREAMS_PASSWORD", "AIOStreams Instance Password", "", {
      prefillValue: showAutoStreams ? wiz.autoStreamsPw : "",
      autoGenerated: Boolean(showAutoStreams),
      onValueChange: () => { wiz.autoStreamsPw = ""; },
    })
  );
  keyGrid.appendChild(
    makeWizardEnvInput(ctx, "AIOMETADATA_PASSWORD", "AIOMetadata Instance Password", "", {
      prefillValue: showAutoMeta ? wiz.autoMetaPw : "",
      autoGenerated: Boolean(showAutoMeta),
      onValueChange: () => { wiz.autoMetaPw = ""; },
    })
  );

  [
    ["SECRET_TMDB_API_KEY", "TMDB v3 API Key (Required)", "https://www.themoviedb.org/settings/api"],
    ["SECRET_TMDB_ACCESS_TOKEN", "TMDB v4 Read Access Token (Required)", "https://www.themoviedb.org/settings/api"],
    ["SECRET_TVDB_API_KEY", "TheTVDB v4 API Key (Required)", "https://thetvdb.com/api-information"],
    ["SECRET_MDBLIST_API_KEY", "MDBList API Key (8-Source Ratings, Optional)", "https://mdblist.com/preferences"],
  ].forEach(([k, label, url]) => {
    keyGrid.appendChild(makeWizardEnvInput(ctx, k, label, url));
  });

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
  verifyBtn.innerHTML = `${icon("activity", 13)} <span>${wiz.verifying ? "Verifying…" : "Verify Keys Live"}</span>`;
  verifyBtn.onclick = async () => {
    wiz.verifying = true;
    rerender();
    try {
      const res = await ctx.api("/api/doctor", { pendingEnv: effectiveWizardEnv(ctx, wiz) });
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
      const pillCls = chk.status === "ok" ? "ok" : (chk.status === "warn" ? "warn" : (chk.status === "error" ? "danger" : ""));
      const item = document.createElement("div");
      item.className = "env-card";
      item.innerHTML = `
        <div class="row-between">
          <strong style="font-size:12px">${escapeHtml(chk.label)}</strong>
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

function makeWizardEnvInput(ctx, key, label, signupUrl, opts = {}) {
  const { escapeHtml, icon } = ctx.ui;
  const spec = (ctx.state.env || []).find(e => e.key === key);
  const isSecret = spec
    ? Boolean(spec.isSecret)
    : key.startsWith("SECRET_") || key.endsWith("_PASSWORD");
  const pending = key in ctx.pendingEnvEdits ? ctx.pendingEnvEdits[key] : "";
  const effectiveShown = pending || (!isSecret && spec ? spec.value || "" : "") || opts.prefillValue || "";
  const isSet = Boolean((spec && spec.isSet) || String(pending || opts.prefillValue || "").trim());

  const card = document.createElement("div");
  card.className = "env-card";

  const top = document.createElement("div");
  top.className = "row-between";
  const lblEl = document.createElement("strong");
  lblEl.style.fontSize = "12px";
  lblEl.textContent = label;
  const badgeRow = document.createElement("div");
  badgeRow.className = "row-wrap";
  badgeRow.style.gap = "5px";
  if (opts.autoGenerated) {
    const agPill = document.createElement("span");
    agPill.className = "pill accent";
    agPill.textContent = "Auto-generated";
    badgeRow.appendChild(agPill);
  }
  const statusPill = document.createElement("span");
  statusPill.className = `pill ${isSet ? "ok" : "warn"}`;
  statusPill.textContent = isSet ? "Ready" : "Needed";
  badgeRow.appendChild(statusPill);
  top.append(lblEl, badgeRow);

  const subRow = document.createElement("div");
  subRow.className = "row-between";
  subRow.style.cssText = "font-size:11px;color:var(--muted)";
  subRow.innerHTML = `<code>${escapeHtml(key)}</code>` +
    (signupUrl ? `<a href="${escapeHtml(signupUrl)}" target="_blank" rel="noreferrer"><span>Get Key</span> ${icon("externalLink", 10)}</a>` : "");

  const inp = document.createElement("input");
  inp.type = isSecret && !opts.autoGenerated ? "password" : "text";
  inp.value = effectiveShown;
  inp.placeholder = isSecret && spec && spec.isSet ? `${spec.masked} (saved)` : "Paste value…";

  const helpText = KEY_HELP[key] || "";
  const helpEl = helpText ? document.createElement("div") : null;
  if (helpEl) {
    helpEl.className = "key-format-help";
    helpEl.textContent = helpText;
  }

  const warnEl = document.createElement("div");
  warnEl.className = "key-format-warn";
  const syncValidation = (val) => {
    const issue = validateKeyFormat(key, val);
    if (issue) {
      warnEl.hidden = false;
      warnEl.textContent = issue.msg;
    } else {
      warnEl.hidden = true;
      warnEl.textContent = "";
    }
    const readyNow = Boolean((spec && spec.isSet) || String(val || "").trim());
    statusPill.className = `pill ${readyNow ? "ok" : "warn"}`;
    statusPill.textContent = readyNow ? "Ready" : "Needed";
  };

  inp.oninput = () => {
    if (opts.onValueChange) opts.onValueChange(inp.value);
    ctx.setPendingEnv(key, inp.value);
    syncValidation(inp.value);
  };
  syncValidation(pending || opts.prefillValue || "");

  card.append(top, subRow, inp);
  if (helpEl) card.appendChild(helpEl);
  card.appendChild(warnEl);
  return card;
}

function renderStep3Taste(container, wiz, ctx, rerender) {
  const { escapeHtml, icon } = ctx.ui;
  const st = ctx.state;
  const prof = ctx.currentProfile();

  // Starter Collections
  const colBox = document.createElement("div");
  colBox.className = "rows-box";
  colBox.style.marginTop = "0";
  colBox.innerHTML = `<div style="font-size:13px;font-weight:600;margin-bottom:8px">1. Pick Starter Home Collections</div>`;
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
      top.className = "switch chip" + (checked ? " active" : "");
      top.style.border = "none";
      top.style.background = "transparent";
      top.style.padding = "0";
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
    regTitle.style.fontSize = "12px";
    regTitle.style.fontWeight = "600";
    regTitle.style.margin = "14px 0 6px";
    regTitle.textContent = "Optional Regional Cinema Shelves";
    colBox.appendChild(regTitle);
    const regChips = document.createElement("div");
    regChips.className = "chip-row";
    regRecipe.presets.forEach(pr => {
      const code = pr.with.language;
      const active = wiz.selectedRegional.has(code);
      const b = document.createElement("button");
      b.className = "btn icon-btn" + (active ? " accent-outline" : "");
      b.innerHTML = `${icon(active ? "check" : "plus", 12)} <span>${escapeHtml(pr.label)}</span>`;
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
  subBox.innerHTML = `<div style="font-size:13px;font-weight:600;margin-bottom:8px">2. Subtitles &amp; Bilingual Dual-Subtitles</div>`;
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
    <div class="row-between" style="margin-bottom:6px">
      <strong style="font-size:13px">3. Connect Your Nuvio Account (Optional now)</strong>
      <span class="pill ${nuvioReady ? "ok" : "warn"}">${nuvioReady ? `Signed In ${escapeHtml(prof?.nuvio?.email || "")}` : "Optional · Connect Anytime"}</span>
    </div>
    <div style="font-size:11.5px;color:var(--muted);margin-bottom:10px">
      First time using Nuvio? Sign in once inside the Nuvio app on each device (TV / Desktop / Mobile) before pushing so its cloud slot exists.
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
  authBtn.innerHTML = `${icon(nuvioReady ? "refresh" : "lock", 13)} <span>${nuvioReady ? "Re-Authenticate" : "Sign In to Nuvio"}</span>`;
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

  // Stremio Optional Dual-Sync Sign-In
  const stBox = document.createElement("div");
  stBox.className = "rows-box";
  const stReady = Boolean(prof?.stremio?.loginReady || st.tenant?.stremioReady);
  const stHead = document.createElement("div");
  stHead.className = "row-between";
  stHead.style.marginBottom = "8px";
  stHead.innerHTML = `
    <strong style="font-size:13px">4. Connect Stremio Account (Optional Dual-Sync)</strong>
    <span class="pill ${stReady ? "ok" : ""}">${stReady ? `Signed In ${escapeHtml(prof?.stremio?.email || st.tenant?.stremioEmail || "")}` : "Optional"}</span>
  `;
  stBox.appendChild(stHead);
  stBox.appendChild(
    ctx.ui.makeSwitch("Also Sync Addons to Stremio", wiz.wantStremio, (v) => {
      wiz.wantStremio = v;
      rerender();
    })
  );
  if (wiz.wantStremio) {
    const stGrid = document.createElement("div");
    stGrid.className = "cards-grid-sm";
    stGrid.style.cssText = "align-items:end;margin-top:10px";

    const sEmL = document.createElement("label");
    sEmL.className = "field"; sEmL.textContent = "Stremio Email";
    const sEmIn = document.createElement("input");
    sEmIn.type = "email"; sEmIn.placeholder = "you@example.com"; sEmIn.value = wiz.stremioEmail;
    sEmIn.oninput = () => { wiz.stremioEmail = sEmIn.value; };
    sEmL.appendChild(sEmIn);

    const sPwL = document.createElement("label");
    sPwL.className = "field"; sPwL.textContent = "Stremio Password";
    const sPwIn = document.createElement("input");
    sPwIn.type = "password"; sPwIn.placeholder = "••••••••"; sPwIn.value = wiz.stremioPassword;
    sPwIn.oninput = () => { wiz.stremioPassword = sPwIn.value; };
    sPwL.appendChild(sPwIn);

    const stBtn = document.createElement("button");
    stBtn.className = "btn accent-outline";
    stBtn.disabled = wiz.stremioSigningIn;
    stBtn.innerHTML = `${icon(stReady ? "refresh" : "lock", 13)} <span>${stReady ? "Re-Authenticate Stremio" : "Sign In to Stremio"}</span>`;
    stBtn.onclick = async () => {
      wiz.stremioSigningIn = true;
      rerender();
      try {
        await ctx.api("/api/stremio-login", {
          login: prof?.stremio?.login || "main",
          email: wiz.stremioEmail,
          password: wiz.stremioPassword,
        });
        wiz.stremioPassword = "";
      } catch (err) {
        ctx.showLog(err.message, [], true);
      } finally {
        wiz.stremioSigningIn = false;
        rerender();
      }
    };
    stGrid.append(sEmL, sPwL, stBtn);
    stBox.appendChild(stGrid);
  }
  container.appendChild(stBox);
}

function renderStep4Launch(container, wiz, ctx, rerender) {
  const { escapeHtml, icon } = ctx.ui;
  const st = ctx.state;
  const prof = ctx.currentProfile();
  const nuvioReady = Boolean(prof?.nuvio?.loginReady);
  const stremioReady = Boolean(prof?.stremio?.loginReady || st.tenant?.stremioReady);
  const chosenDebrid = DEBRID_WIZARD_OPTIONS.find(d => d.id === wiz.debrid) || DEBRID_WIZARD_OPTIONS[0];

  const hasStreamsPw = wiz.setupMode === "adopt"
    ? (isWizardKeyReady(ctx, wiz, "AIOSTREAMS_PASSWORD") || ctx.isEnvKeyReady("AIOSTREAMS_UUID"))
    : isWizardKeyReady(ctx, wiz, "AIOSTREAMS_PASSWORD");
  const hasMetaPw = wiz.setupMode === "adopt"
    ? (isWizardKeyReady(ctx, wiz, "AIOMETADATA_PASSWORD") || ctx.isEnvKeyReady("AIOMETADATA_UUID"))
    : isWizardKeyReady(ctx, wiz, "AIOMETADATA_PASSWORD");

  const checklist = [
    { label: "Hosting URLs", ok: isWizardKeyReady(ctx, wiz, "AIOSTREAMS_URL") && isWizardKeyReady(ctx, wiz, "AIOMETADATA_URL") },
    { label: "Instance Passwords", ok: hasStreamsPw && hasMetaPw },
    { label: `${chosenDebrid.label} Key`, ok: chosenDebrid.keys.every(k => isWizardKeyReady(ctx, wiz, k)) },
    { label: "TMDB v3 API Key", ok: isWizardKeyReady(ctx, wiz, "SECRET_TMDB_API_KEY") },
    { label: "TMDB v4 Token", ok: isWizardKeyReady(ctx, wiz, "SECRET_TMDB_ACCESS_TOKEN") },
    { label: "TVDB API Key", ok: isWizardKeyReady(ctx, wiz, "SECRET_TVDB_API_KEY") },
  ];
  const allReady = checklist.every(c => c.ok);

  const summaryGrid = document.createElement("div");
  summaryGrid.className = "cards-grid-sm";
  [
    ["Setup Path", wiz.setupMode === "adopt" ? "Adopt Existing Stack" : (wiz.hostingMode === "elfhosted" ? "ElfHosted Public" : "Custom Self-Hosted")],
    ["Primary Provider", wiz.debrid.toUpperCase()],
    ["Home Shelves", `${wiz.selectedCols.size + wiz.selectedRegional.size} collections selected`],
    ["Client Sync", nuvioReady || (wiz.wantStremio && stremioReady) ? "Ready to sync clients" : "Instances only (sign in anytime)"],
  ].forEach(([k, v]) => {
    const c = document.createElement("div");
    c.className = "env-card";
    c.innerHTML = `<span style="font-size:11px;color:var(--muted)">${escapeHtml(k)}</span><strong>${escapeHtml(v)}</strong>`;
    summaryGrid.appendChild(c);
  });
  container.appendChild(summaryGrid);

  // Pre-Flight Readiness Checklist
  const readyBox = document.createElement("div");
  readyBox.className = "rows-box";
  readyBox.innerHTML = `<div style="font-size:12.5px;font-weight:600;margin-bottom:8px">Pre-Flight Key Readiness</div>`;
  const chipRow = document.createElement("div");
  chipRow.className = "chip-row";
  checklist.forEach(item => {
    const pill = document.createElement("span");
    pill.className = `pill ${item.ok ? "ok" : "warn"}`;
    pill.textContent = `${item.ok ? "✓" : "Missing:"} ${item.label}`;
    chipRow.appendChild(pill);
  });
  readyBox.appendChild(chipRow);
  if (!allReady) {
    const fixRow = document.createElement("div");
    fixRow.className = "row-between";
    fixRow.style.cssText = "margin-top:10px;gap:8px;flex-wrap:wrap";
    fixRow.innerHTML = `<span style="font-size:12px;color:#fbbf24">Fill in the missing keys above before provisioning so your instances don't fail on launch.</span>`;
    const jumpBtn = document.createElement("button");
    jumpBtn.className = "btn accent-outline";
    jumpBtn.textContent = "Fix Missing Keys in Step 2 →";
    jumpBtn.onclick = () => { wiz.step = 2; rerender(); };
    fixRow.appendChild(jumpBtn);
    readyBox.appendChild(fixRow);
  }
  container.appendChild(readyBox);

  const launchBox = document.createElement("div");
  launchBox.className = "rows-box";
  launchBox.style.borderColor = "var(--border-strong)";
  launchBox.innerHTML = `
    <div class="row-between" style="flex-wrap:wrap">
      <div>
        <strong style="font-size:14px">${nuvioReady ? "Ready to Provision &amp; Push" : "Ready to Provision &amp; Push Instances"}</strong>
        <div style="font-size:12px;color:var(--muted);margin-top:2px">
          Creates or updates encrypted AIOStreams &amp; AIOMetadata instances, derives catalogs, and syncs connected clients.
        </div>
      </div>
    </div>
  `;

  const launchBtn = document.createElement("button");
  launchBtn.className = "btn primary";
  launchBtn.style.marginTop = "12px";
  const alreadyProv = Boolean(st.tenant?.aiostreamsProvisioned && st.tenant?.aiometadataProvisioned);
  launchBtn.disabled = wiz.launching || (!allReady && !alreadyProv);
  launchBtn.innerHTML = wiz.launching
    ? `<span>Provisioning &amp; Streaming Push…</span>`
    : `${icon("upload", 14)} <span>${alreadyProv ? "Save &amp; Push Stack Live" : "Provision Instances &amp; Push"}</span>`;

  launchBtn.onclick = async () => {
    wiz.launching = true;
    wiz.launchError = "";
    wiz.launchSteps = ["> Saving wizard preferences & keys…"];
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
        .filter(pk => {
          if (pk.id === "dual_subtitles") {
            return Boolean(wiz.dualSubQuick) || ctx.isEnvKeyReady("SECRET_SUBSOURCE_API_KEY");
          }
          if (Array.isArray(pk.requiredSecrets) && pk.requiredSecrets.length) {
            return pk.enabled && pk.requiredSecrets.every(k => isWizardKeyReady(ctx, wiz, k));
          }
          return pk.enabled;
        })
        .map(pk => pk.id);

      const envUpdates = effectiveWizardEnv(ctx, wiz);
      const mut = {
        envUpdates,
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
        const hasMdb = isWizardKeyReady(ctx, wiz, "SECRET_MDBLIST_API_KEY");
        mut.profile = {
          name: prof.name,
          collections: collectionsPayload,
          stremioEnabled: wiz.wantStremio,
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

      const provNow = ctx.state.tenant?.aiostreamsProvisioned && ctx.state.tenant?.aiometadataProvisioned;
      if (!provNow) {
        wiz.launchSteps.push("> Provisioning AIOStreams & AIOMetadata UUIDs…");
        rerender();
        const provRes = await ctx.api("/api/provision", {});
        (provRes.steps || []).forEach(s => wiz.launchSteps.push(`+ ${s}`));
        rerender();
      }

      const curProf = ctx.currentProfile();
      const canPushClients = Boolean(
        curProf?.nuvio?.loginReady || (curProf?.stremioEnabled && curProf?.stremio?.loginReady)
      );
      const pushTarget = canPushClients ? "all" : "instances";
      wiz.launchSteps.push(`> Streaming workspace push (target: ${pushTarget})…`);
      rerender();
      await ctx.streamPush({ target: pushTarget }, (stepLine) => {
        wiz.launchSteps.push(`+ ${stepLine}`);
        rerender();
      });
      if (pushTarget === "instances") {
        wiz.launchSteps.push("+ Skipped Nuvio/Stremio client sync (not signed in yet — connect anytime in Keys & Accounts).");
      }
      if (typeof ctx.markPushed === "function") ctx.markPushed();

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
      (wiz.launchError ? `<div style="color:var(--danger);margin-top:6px">ERROR: ${escapeHtml(wiz.launchError)}</div>` : "");
    launchBox.appendChild(logOut);
  }

  container.appendChild(launchBox);

  // Post-Launch "Connect Your Devices" Handoff Card
  const tInfo = ctx.state.tenant || {};
  if (wiz.launchDone || tInfo.aiostreamsProvisioned || tInfo.aiometadataProvisioned) {
    const handoff = document.createElement("div");
    handoff.className = "rows-box";
    handoff.innerHTML = `
      <strong style="font-size:13.5px;display:inline-flex;align-items:center;gap:6px">${icon("tv", 14)} Connect Your Devices</strong>
      <div style="font-size:12px;color:var(--muted);margin:3px 0 10px">
        Your instances and catalogs are ready. Follow the steps below on your TV, phone, or Stremio app:
      </div>
    `;

    const devGrid = document.createElement("div");
    devGrid.className = "grid-equal";

    const nuvStep = document.createElement("div");
    nuvStep.className = "env-card";
    nuvStep.innerHTML = `
      <strong style="font-size:12.5px">1. Nuvio (TV / Desktop / Mobile)</strong>
      <div style="font-size:11.5px;color:var(--muted);line-height:1.45">
        Open Nuvio → sign in with your account → relaunch the app once on each platform to sync shelves, themes, and Elite-Badges.
      </div>
    `;
    if (!nuvioReady) {
      const goKeys = document.createElement("button");
      goKeys.className = "btn accent-outline";
      goKeys.innerHTML = `${icon("lock", 12)} <span>Connect Nuvio in Keys &amp; Accounts →</span>`;
      goKeys.onclick = () => {
        try { sessionStorage.setItem("sw_wizard_dismissed", "1"); } catch (_) {}
        ctx.closeModal();
        ctx.switchTab("keys");
      };
      nuvStep.appendChild(goKeys);
    }

    const stmStep = document.createElement("div");
    stmStep.className = "env-card";
    stmStep.innerHTML = `
      <strong style="font-size:12.5px">2. Stremio / Direct Manifest Install</strong>
      <div style="font-size:11.5px;color:var(--muted);line-height:1.45">
        Install AIOMetadata catalogs or inspect your AIOStreams addon configuration directly:
      </div>
    `;
    const linkBtns = document.createElement("div");
    linkBtns.className = "row-wrap";
    if (tInfo.aiometadataManifestUrl) {
      const cpManifest = document.createElement("button");
      cpManifest.className = "btn icon-btn";
      cpManifest.innerHTML = `${icon("copy", 12)} <span>Copy Metadata Manifest URL</span>`;
      cpManifest.onclick = () => ctx.copyText(tInfo.aiometadataManifestUrl, "AIOMetadata Manifest URL");
      linkBtns.appendChild(cpManifest);
    }
    if (tInfo.aiometadataStremioUrl) {
      const openSt = document.createElement("a");
      openSt.className = "btn accent-outline";
      openSt.href = tInfo.aiometadataStremioUrl;
      openSt.innerHTML = `<span>Open in Stremio</span> ${icon("externalLink", 12)}`;
      linkBtns.appendChild(openSt);
    }
    if (tInfo.aiostreamsConfigureUrl) {
      const a1 = document.createElement("a");
      a1.className = "btn icon-btn";
      a1.href = tInfo.aiostreamsConfigureUrl;
      a1.target = "_blank";
      a1.rel = "noreferrer";
      a1.innerHTML = `<span>AIOStreams Configure</span> ${icon("externalLink", 11)}`;
      linkBtns.appendChild(a1);
    }
    if (tInfo.aiometadataConfigureUrl) {
      const a2 = document.createElement("a");
      a2.className = "btn icon-btn";
      a2.href = tInfo.aiometadataConfigureUrl;
      a2.target = "_blank";
      a2.rel = "noreferrer";
      a2.innerHTML = `<span>AIOMetadata Configure</span> ${icon("externalLink", 11)}`;
      linkBtns.appendChild(a2);
    }
    stmStep.appendChild(linkBtns);
    devGrid.append(nuvStep, stmStep);
    handoff.appendChild(devGrid);

    const backupRow = document.createElement("div");
    backupRow.className = "row-wrap";
    backupRow.style.marginTop = "10px";
    const cpUuidBtn = document.createElement("button");
    cpUuidBtn.className = "btn icon-btn";
    cpUuidBtn.innerHTML = `${icon("copy", 12)} <span>Copy Instance UUIDs</span>`;
    cpUuidBtn.onclick = () => {
      const summary = [
        `AIOStreams UUID: ${tInfo.aiostreamsUuid || "(unprovisioned)"}`,
        `AIOStreams Configure: ${tInfo.aiostreamsConfigureUrl || ""}`,
        `AIOMetadata UUID: ${tInfo.aiometadataUuid || "(unprovisioned)"}`,
        `AIOMetadata Configure: ${tInfo.aiometadataConfigureUrl || ""}`,
      ].join("\n");
      ctx.copyText(summary, "Instance UUIDs");
    };
    const zipBtn = document.createElement("button");
    zipBtn.className = "btn icon-btn";
    zipBtn.innerHTML = `${icon("package", 12)} <span>Download GitOps Zip</span>`;
    zipBtn.onclick = () => document.getElementById("btn-export")?.click();
    backupRow.append(cpUuidBtn, zipBtn);
    handoff.appendChild(backupRow);

    container.appendChild(handoff);
  }
}
