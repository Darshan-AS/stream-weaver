// Tab 4 — Nuvio Experience, Visual Elite-Badges & 16:9 Subtitle Simulators, and Operations Panel

let badgePlacementPreview = "BOTTOM";
let playerPreviewTarget = "tv";
let inlineDoctorReport = null;
let inlineDiffOutcomes = null;
let inlineWarmResult = null;

const THEME_SWATCHES = [
  { id: "ocean", label: "Ocean", color: "#38bdf8" },
  { id: "crimson", label: "Crimson", color: "#ef4444" },
  { id: "violet", label: "Violet", color: "#8b5cf6" },
  { id: "emerald", label: "Emerald", color: "#10b981" },
  { id: "amber", label: "Amber", color: "#f59e0b" },
  { id: "rose", label: "Rose", color: "#f43f5e" },
  { id: "gold", label: "Gold (TV)", color: "#eab308" },
  { id: "jade", label: "Jade (TV)", color: "#14b8a6" },
  { id: "rose_gold", label: "Rose Gold", color: "#fb7185" },
  { id: "arctic_blue", label: "Arctic Blue", color: "#7dd3fc" },
  { id: "graphite", label: "Graphite", color: "#94a3b8" },
  { id: "white", label: "White", color: "#f8fafc" },
];

export function renderNuvioTab(root, ctx) {
  const p = ctx.currentProfile();
  if (!p) return;

  const stack = document.createElement("div");
  stack.className = "section-stack";

  // Row 1: Visual Simulators (Elite-Badges Card Preview + 16:9 Player Subtitle Scale Simulator)
  const simGrid = document.createElement("div");
  simGrid.className = "grid-equal";
  simGrid.append(
    renderEliteBadgesSimulatorCard(ctx, p),
    renderSubtitleScaleSimulatorCard(ctx, p)
  );
  stack.appendChild(simGrid);

  // Row 2: 12 Nuvio Themes + Layout + Playback + Spoiler Blur & 8-Source Ratings
  const cfgGrid = document.createElement("div");
  cfgGrid.className = "grid-equal";
  cfgGrid.append(
    renderAppearanceAndPlaybackCard(ctx, p),
    renderMetaEnrichmentCard(ctx, p)
  );
  stack.appendChild(cfgGrid);

  // Row 3: Live Operations Panel (Doctor, Drift Diff, Pre-Warm Home Rows)
  stack.appendChild(renderOperationsPanel(ctx));

  root.appendChild(stack);
}

// ---------------------------------------------------------------------------
// Visual Elite-Badges Card Preview (TOP / BOTTOM)
// ---------------------------------------------------------------------------
function renderEliteBadgesSimulatorCard(ctx, p) {
  const { icon } = ctx.ui;
  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `
    <div class="card-eyebrow">${icon("layers", 12)} Badges Preview</div>
    <h2>Elite-Badges Stream Card Simulator</h2>
    <p class="sub">Regex-matched 4K, Dolby Vision, HDR10+, Atmos, and Debrid badges across TV, Desktop, and Mobile.</p>
  `;

  const ctrlRow = document.createElement("div");
  ctrlRow.className = "row-between";
  ctrlRow.style.marginBottom = "14px";
  ctrlRow.style.flexWrap = "wrap";

  ctrlRow.appendChild(
    ctx.ui.makeSwitch("Enable Elite-Badges Pack", Boolean(p.badgesEnabled), (v) => {
      p.badgesEnabled = v;
      ctx.saveSection("profile", true);
    })
  );

  const posTabs = document.createElement("div");
  posTabs.className = "segmented";
  [["BOTTOM", "Row: BOTTOM"], ["TOP", "Row: TOP"]].forEach(([pos, lbl]) => {
    const b = document.createElement("button");
    b.className = "seg-btn" + (badgePlacementPreview === pos ? " active" : "");
    b.textContent = lbl;
    b.onclick = () => {
      badgePlacementPreview = pos;
      ctx.render();
    };
    posTabs.appendChild(b);
  });
  ctrlRow.appendChild(posTabs);
  card.appendChild(ctrlRow);

  const badgeStripHtml = p.badgesEnabled
    ? `<div class="elite-badges-strip">
        <span class="elite-badge b-4k">4K UHD</span>
        <span class="elite-badge b-dv">Dolby Vision</span>
        <span class="elite-badge b-hdr">HDR10+</span>
        <span class="elite-badge b-codec">HEVC 10bit</span>
        <span class="elite-badge b-atmos">Dolby Atmos</span>
        <span class="elite-badge b-cached">Cached Instant</span>
      </div>`
    : `<div style="font-size:11.5px;color:var(--muted)">Elite-Badges disabled on this profile (plain text stream card only).</div>`;

  const simCard = document.createElement("div");
  simCard.className = "stream-card-sim";
  const textBlock = `
    <div class="stream-sim-row">
      <div class="stream-sim-name">AIOStreams\nInstant</div>
      <div class="stream-sim-desc"><strong>Oppenheimer.2023.2160p.UHD.BluRay.REMUX.DV.HDR10Plus.TrueHD.7.1.Atmos-FGT</strong>\n74.2 GB · IMAX Edition · TorBox Cloud</div>
    </div>
  `;
  simCard.innerHTML = badgePlacementPreview === "TOP"
    ? `${badgeStripHtml}${textBlock}`
    : `${textBlock}${badgeStripHtml}`;

  card.appendChild(simCard);
  return card;
}

// ---------------------------------------------------------------------------
// Interactive 16:9 Cinema Player Subtitle Scale Simulator
// ---------------------------------------------------------------------------
function renderSubtitleScaleSimulatorCard(ctx, p) {
  const { icon } = ctx.ui;
  const sWith = p.settings?.with || {};
  const themeId = sWith.theme || "ocean";
  const swatch = THEME_SWATCHES.find(t => t.id === themeId) || THEME_SWATCHES[0];
  const amoled = sWith.amoledBlack !== false;
  let sizeSp = Number(sWith.subtitleSizeSp ?? 18);
  let tvPercent = Number(sWith.tvSubtitleSizePercent ?? 100);
  const secLang = sWith.subtitleSecondary || "";

  const card = document.createElement("div");
  card.className = "card";

  const head = document.createElement("div");
  head.className = "row-between";
  head.style.marginBottom = "12px";
  head.style.flexWrap = "wrap";
  head.innerHTML = `
    <div>
      <div class="card-eyebrow">${icon("subtitles", 12)} Player Stage</div>
      <h2>16:9 Subtitle Scale Simulator</h2>
      <p class="sub" style="margin-bottom:0">Drag sliders to preview live subtitle legibility before pushing to devices.</p>
    </div>
  `;

  const modeSwitch = document.createElement("div");
  modeSwitch.className = "segmented";
  [["tv", "TV Scale (%)"], ["desktop", "Desktop (sp)"]].forEach(([m, lbl]) => {
    const b = document.createElement("button");
    b.className = "seg-btn" + (playerPreviewTarget === m ? " active" : "");
    b.textContent = lbl;
    b.onclick = () => {
      playerPreviewTarget = m;
      ctx.render();
    };
    modeSwitch.appendChild(b);
  });
  head.appendChild(modeSwitch);
  card.appendChild(head);

  // 16:9 Cinema Stage
  const stage = document.createElement("div");
  stage.className = "player-stage" + (amoled ? " amoled" : "");
  stage.style.setProperty("--player-accent", swatch.color);

  const osdTop = document.createElement("div");
  osdTop.className = "player-osd-top";
  osdTop.innerHTML = `
    <span><strong>Blade Runner 2049</strong> · 2160p DV Atmos</span>
    <span class="pill" style="border-color:${swatch.color};color:${swatch.color}">${swatch.label}${amoled ? " · AMOLED" : ""}</span>
  `;

  const subOverlay = document.createElement("div");
  subOverlay.className = "player-sub-overlay";
  const computePx = () => {
    if (playerPreviewTarget === "tv") {
      return Math.round(18 * (tvPercent / 100));
    }
    return Math.round(sizeSp * 0.95);
  };
  subOverlay.style.fontSize = `${computePx()}px`;
  subOverlay.innerHTML = `All those moments will be lost in time, like tears in rain.` +
    (secLang ? `<span class="player-sub-secondary">[${secLang.toUpperCase()}] すべての瞬間はいずれ時の中に消えていく…</span>` : "");

  const seekbar = document.createElement("div");
  seekbar.className = "player-seekbar";
  seekbar.innerHTML = `
    <div class="player-controls-row">
      <div class="player-icons">${icon("play", 12)} ${icon("volume", 12)} <span>01:42:18</span></div>
      <div class="player-icons">${icon("subtitles", 12)} <span>02:43:47</span></div>
    </div>
    <div class="player-progress-track"><div class="player-progress-fill"></div></div>
  `;

  stage.append(osdTop, subOverlay, seekbar);
  card.appendChild(stage);

  // Sliders
  const slidersGrid = document.createElement("div");
  slidersGrid.className = "grid-equal";
  slidersGrid.style.marginTop = "12px";

  const spLbl = document.createElement("label");
  spLbl.className = "field";
  const spValSpan = document.createElement("span");
  spValSpan.className = "tabular";
  spValSpan.textContent = `Desktop / Mobile Size: ${sizeSp} sp`;
  const spRange = document.createElement("input");
  spRange.type = "range";
  spRange.min = "12";
  spRange.max = "36";
  spRange.value = String(sizeSp);
  spRange.oninput = () => {
    sizeSp = Number(spRange.value);
    spValSpan.textContent = `Desktop / Mobile Size: ${sizeSp} sp`;
    if (playerPreviewTarget === "desktop") {
      subOverlay.style.fontSize = `${computePx()}px`;
    }
  };
  spRange.onchange = () => {
    p.settings.enabled = true;
    p.settings.with = { ...(p.settings.with || {}), subtitleSizeSp: sizeSp };
    ctx.saveSection("profile", true);
  };
  spLbl.append(spValSpan, spRange);

  const tvLbl = document.createElement("label");
  tvLbl.className = "field";
  const tvValSpan = document.createElement("span");
  tvValSpan.className = "tabular";
  tvValSpan.textContent = `TV Subtitle Scale: ${tvPercent}%`;
  const tvRange = document.createElement("input");
  tvRange.type = "range";
  tvRange.min = "60";
  tvRange.max = "180";
  tvRange.step = "5";
  tvRange.value = String(tvPercent);
  tvRange.oninput = () => {
    tvPercent = Number(tvRange.value);
    tvValSpan.textContent = `TV Subtitle Scale: ${tvPercent}%`;
    if (playerPreviewTarget === "tv") {
      subOverlay.style.fontSize = `${computePx()}px`;
    }
  };
  tvRange.onchange = () => {
    p.settings.enabled = true;
    p.settings.with = { ...(p.settings.with || {}), tvSubtitleSizePercent: tvPercent };
    ctx.saveSection("profile", true);
  };
  tvLbl.append(tvValSpan, tvRange);

  slidersGrid.append(tvLbl, spLbl);
  card.appendChild(slidersGrid);

  return card;
}

// ---------------------------------------------------------------------------
// 12 Nuvio Themes + AMOLED + Layout + Playback & Languages
// ---------------------------------------------------------------------------
function renderAppearanceAndPlaybackCard(ctx, p) {
  const { icon } = ctx.ui;
  const st = ctx.state;
  const sWith = p.settings?.with || {};
  const curTheme = sWith.theme || "ocean";
  const curAmoled = sWith.amoledBlack !== false;

  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `
    <div class="card-eyebrow">${icon("sliders", 12)} Appearance &amp; Playback</div>
    <h2>Nuvio Themes, TV Layout &amp; Playback (<code>nuvio_settings</code>)</h2>
    <p class="sub">Read-modify-write sync across TV, Desktop, and Mobile — device decoders and buffers remain untouched.</p>
  `;

  card.appendChild(
    ctx.ui.makeSwitch("Manage Portable Nuvio Settings", Boolean(p.settings?.enabled), (v) => {
      p.settings.enabled = v;
      ctx.saveSection("profile", true);
    })
  );

  if (p.settings?.enabled) {
    const swatchBox = document.createElement("div");
    swatchBox.className = "rows-box";
    const swTop = document.createElement("div");
    swTop.className = "row-between";
    swTop.style.marginBottom = "10px";
    swTop.innerHTML = `<strong style="font-size:12.5px">12 Nuvio Accent Themes</strong>`;
    swTop.appendChild(
      ctx.ui.makeSwitch("AMOLED True Black", curAmoled, (v) => {
        p.settings.with = { ...(p.settings.with || {}), amoledBlack: v };
        ctx.saveSection("profile", true);
      })
    );
    swatchBox.appendChild(swTop);

    const swatchGrid = document.createElement("div");
    swatchGrid.className = "swatch-grid";
    THEME_SWATCHES.forEach(t => {
      const btn = document.createElement("button");
      btn.className = "btn swatch-btn" + (curTheme === t.id ? " active" : "");
      btn.innerHTML = `<span class="swatch-dot" style="background:${t.color}"></span><span>${ctx.ui.escapeHtml(t.label)}</span>`;
      btn.onclick = () => {
        p.settings.with = { ...(p.settings.with || {}), theme: t.id };
        ctx.saveSection("profile", true);
      };
      swatchGrid.appendChild(btn);
    });
    swatchBox.appendChild(swatchGrid);
    card.appendChild(swatchBox);

    const skipNames = new Set(["theme", "amoledBlack", "subtitleSizeSp", "tvSubtitleSizePercent"]);
    const fields = document.createElement("div");
    fields.style.display = "grid";
    fields.style.gridTemplateColumns = "repeat(auto-fit, minmax(210px, 1fr))";
    fields.style.gap = "10px";
    fields.style.marginTop = "14px";

    (st.settingsParams || [])
      .filter(pr => !skipNames.has(pr.name))
      .forEach(param => {
        fields.appendChild(
          ctx.ui.makeParamInput(param, p.settings.with, (val) => {
            p.settings.with = { ...(p.settings.with || {}), [param.name]: val };
            ctx.saveSection("profile", true);
          })
        );
      });
    card.appendChild(fields);
  }

  return card;
}

// ---------------------------------------------------------------------------
// Details-Page Enrichment & MDBList 8-Source Ratings (`nuvio_meta`)
// ---------------------------------------------------------------------------
function renderMetaEnrichmentCard(ctx, p) {
  const { icon } = ctx.ui;
  const st = ctx.state;
  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `
    <div class="card-eyebrow">${icon("shield", 12)} Details &amp; Spoilers</div>
    <h2>8-Source MDBList Ratings &amp; Spoiler Protection (<code>nuvio_meta</code>)</h2>
    <p class="sub">Syncs IMDb · TMDB · Rotten Tomatoes · Audience · Metacritic · Letterboxd · Trakt · MAL ratings bar, TMDB hero trailers, and spoiler blur.</p>
  `;

  card.appendChild(
    ctx.ui.makeSwitch("Enable Details-Page Enrichment & 8-Source Ratings", Boolean(p.meta?.enabled), (v) => {
      p.meta.enabled = v;
      ctx.saveSection("profile", true);
    })
  );

  const ratingPreview = document.createElement("div");
  ratingPreview.className = "chip-row";
  ratingPreview.style.marginTop = "12px";
  [
    ["IMDb", "8.6"], ["TMDB", "84%"], ["RT Critics", "93%"], ["RT Audience", "91%"],
    ["Metacritic", "83"], ["Letterboxd", "4.3"], ["Trakt", "88%"], ["MAL", "8.9"],
  ].forEach(([src, val]) => {
    const c = document.createElement("span");
    c.className = "chip" + (p.meta?.enabled ? " active" : "");
    c.textContent = `${src} ${val}`;
    ratingPreview.appendChild(c);
  });
  card.appendChild(ratingPreview);

  if (p.meta?.enabled && Array.isArray(st.metaParams) && st.metaParams.length) {
    const fields = document.createElement("div");
    fields.style.display = "grid";
    fields.style.gridTemplateColumns = "repeat(auto-fit, minmax(210px, 1fr))";
    fields.style.gap = "10px";
    fields.style.marginTop = "14px";
    st.metaParams.forEach(param => {
      fields.appendChild(
        ctx.ui.makeParamInput(param, p.meta.with, (val) => {
          p.meta.with = { ...(p.meta.with || {}), [param.name]: val };
          ctx.saveSection("profile", true);
        })
      );
    });
    card.appendChild(fields);
  }

  return card;
}

// ---------------------------------------------------------------------------
// Operations Panel (Doctor, Drift Diff, Pre-Warm Home Rows)
// ---------------------------------------------------------------------------
function renderOperationsPanel(ctx) {
  const { escapeHtml, icon } = ctx.ui;
  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `
    <div class="row-between" style="flex-wrap:wrap">
      <div>
        <div class="card-eyebrow">${icon("activity", 12)} Diagnostics</div>
        <h2>Live Workspace Operations &amp; Diagnostics</h2>
        <p class="sub" style="margin-bottom:0">Verify API keys &amp; instance reachability, inspect live vs. local drift, or pre-warm Home catalog caches.</p>
      </div>
    </div>
  `;

  const btnRow = document.createElement("div");
  btnRow.className = "row-wrap";
  btnRow.style.marginTop = "12px";

  const docBtn = document.createElement("button");
  docBtn.className = "btn accent-outline";
  docBtn.innerHTML = `${icon("activity", 13)} <span>Run Live Health Doctor</span>`;
  docBtn.onclick = async () => {
    docBtn.disabled = true;
    try {
      const res = await ctx.api("/api/doctor", { pendingEnv: ctx.pendingEnvEdits });
      inlineDoctorReport = res.report || res;
      ctx.render();
    } catch (err) {
      ctx.showLog(err.message, [], true);
      docBtn.disabled = false;
    }
  };

  const diffBtn = document.createElement("button");
  diffBtn.className = "btn";
  diffBtn.innerHTML = `${icon("gitDiff", 13)} <span>Check Live Drift Diff</span>`;
  diffBtn.onclick = async () => {
    diffBtn.disabled = true;
    try {
      const res = await ctx.api("/api/diff", { target: "all" });
      inlineDiffOutcomes = res.outcomes || [];
      ctx.render();
    } catch (err) {
      ctx.showLog(err.message, [], true);
      diffBtn.disabled = false;
    }
  };

  const warmBtn = document.createElement("button");
  warmBtn.className = "btn";
  warmBtn.innerHTML = `${icon("flame", 13)} <span>Pre-Warm Home Rows</span>`;
  warmBtn.onclick = async () => {
    warmBtn.disabled = true;
    try {
      const res = await ctx.api("/api/warm", { workers: 8 });
      inlineWarmResult = res;
      ctx.showLog(`Pre-warmed ${res.warmed ?? 0}/${res.total ?? 0} Home catalog rows.`, []);
      ctx.render();
    } catch (err) {
      ctx.showLog(err.message, [], true);
      warmBtn.disabled = false;
    }
  };

  btnRow.append(docBtn, diffBtn, warmBtn);
  card.appendChild(btnRow);

  if (inlineWarmResult) {
    const wBox = document.createElement("div");
    wBox.className = "rows-box";
    wBox.innerHTML = `<span class="pill ok">Warm Complete</span> Warmed <strong>${Number(inlineWarmResult.warmed ?? 0)} / ${Number(inlineWarmResult.total ?? 0)}</strong> Home catalog endpoints.`;
    card.appendChild(wBox);
  }

  if (inlineDoctorReport && Array.isArray(inlineDoctorReport.checks)) {
    const dBox = document.createElement("div");
    dBox.className = "rows-box";
    const ov = inlineDoctorReport.overall || "healthy";
    const sum = inlineDoctorReport.summary || {};
    dBox.innerHTML = `
      <div class="row-between" style="margin-bottom:10px">
        <strong>Health Doctor Report</strong>
        <span class="pill ${ov === "healthy" ? "ok" : (ov === "warn" ? "warn" : "danger")}">Overall: ${escapeHtml(ov.toUpperCase())} (${sum.ok || 0} ok · ${sum.warn || 0} warn · ${sum.error || 0} err)</span>
      </div>
    `;
    const grid = document.createElement("div");
    grid.className = "cards-grid-sm";
    inlineDoctorReport.checks.forEach(chk => {
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
    dBox.appendChild(grid);
    card.appendChild(dBox);
  }

  if (inlineDiffOutcomes) {
    const dfBox = document.createElement("div");
    dfBox.className = "rows-box";
    dfBox.innerHTML = `<strong style="font-size:12.5px;display:block;margin-bottom:8px">Local vs. Remote Drift Diff</strong>`;
    if (!inlineDiffOutcomes.length) {
      dfBox.insertAdjacentHTML("beforeend", `<div style="font-size:12px;color:var(--muted)">No provisioned remote targets to diff yet.</div>`);
    } else {
      inlineDiffOutcomes.forEach(o => {
        const item = document.createElement("div");
        item.style.marginBottom = "10px";
        item.innerHTML = `
          <div class="row-between" style="margin-bottom:4px">
            <strong><code>${escapeHtml(o.target)}</code></strong>
            <span class="pill ${o.inSync ? "ok" : "warn"}">${o.inSync ? "In Sync" : `+${o.addedLines} / -${o.removedLines}`}</span>
          </div>
        `;
        if (!o.inSync && o.unifiedDiff) {
          item.appendChild(ctx.ui.renderColoredDiff(o.unifiedDiff));
        }
        dfBox.appendChild(item);
      });
    }
    card.appendChild(dfBox);
  }

  return card;
}
