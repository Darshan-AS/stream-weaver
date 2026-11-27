// Tab 4 — Nuvio Experience, Split Controls + Sticky Real-Time Nuvio Viewport, and Operations Panel

import { getViewportState, renderNuvioViewport, setViewportState } from "./nuvio_viewport.js";

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
  const { icon } = ctx.ui;
  const p = ctx.currentProfile();
  if (!p) return;

  const stack = document.createElement("div");
  stack.className = "section-stack";

  // Split Stage: Controls (Left) + Sticky Real-Time Nuvio Viewport (Right)
  const split = document.createElement("div");
  split.className = "studio-split-stage";

  const controlsStack = document.createElement("div");
  controlsStack.className = "section-stack";
  controlsStack.append(
    renderAppearanceAndSubtitlesCard(ctx, p),
    renderBadgesAndMetaCard(ctx, p)
  );

  const right = document.createElement("div");
  right.className = "card sticky-stage";
  right.innerHTML = `
    <div class="row-between" style="margin-bottom:8px;flex-wrap:wrap">
      <div>
        <div class="card-eyebrow">${icon("tv", 12)} Live Simulator</div>
        <h2 style="margin-bottom:2px">Real-Time Nuvio Experience Preview</h2>
      </div>
      <span class="pill">Updates live on every edit</span>
    </div>
  `;
  right.appendChild(renderNuvioViewport(ctx, {}));

  split.append(controlsStack, right);
  stack.appendChild(split);

  // Operations Panel (Doctor, Drift Diff, Pre-Warm Home Rows)
  stack.appendChild(renderOperationsPanel(ctx));

  root.appendChild(stack);
}

// ---------------------------------------------------------------------------
// Card 1 — Appearance, Themes, Layout & Subtitle Scale
// ---------------------------------------------------------------------------
function renderAppearanceAndSubtitlesCard(ctx, p) {
  const { icon } = ctx.ui;
  const st = ctx.state;
  const sWith = p.settings?.with || {};
  const curTheme = sWith.theme || "ocean";
  const curAmoled = sWith.amoledBlack !== false;
  let sizeSp = Number(sWith.subtitleSizeSp ?? 18);
  let tvPercent = Number(sWith.tvSubtitleSizePercent ?? 100);

  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `
    <div class="card-eyebrow">${icon("sliders", 12)} Appearance &amp; Player</div>
    <h2>Nuvio Themes, Layout &amp; Subtitles (<code>nuvio_settings</code>)</h2>
    <p class="sub">Changes reflect immediately in the live TV/Desktop/Mobile viewport.</p>
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
      ctx.ui.makeSwitch("AMOLED Black", curAmoled, (v) => {
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

    const subBox = document.createElement("div");
    subBox.className = "rows-box";
    subBox.innerHTML = `
      <div class="row-between" style="margin-bottom:8px">
        <strong style="font-size:12.5px">Subtitle Scale</strong>
        <button class="btn ghost icon-btn" data-jump-player>${icon("play", 11)} <span>Open Player OSD</span></button>
      </div>
    `;
    subBox.querySelector("[data-jump-player]").onclick = () => {
      setViewportState({ screen: "player" });
      ctx.render();
    };

    const slidersGrid = document.createElement("div");
    slidersGrid.className = "grid-equal";

    const updateLiveSub = (targetDev, px) => {
      const vp = getViewportState();
      if (vp.screen !== "player") {
        setViewportState({ screen: "player", device: targetDev });
        ctx.render();
        return;
      }
      const subEl = document.querySelector(".nv-sub-box");
      if (subEl && vp.device === targetDev) {
        subEl.style.fontSize = `${px}px`;
      }
    };

    const tvLbl = document.createElement("label");
    tvLbl.className = "field";
    const tvValSpan = document.createElement("span");
    tvValSpan.className = "tabular";
    tvValSpan.textContent = `TV Scale: ${tvPercent}%`;
    const tvRange = document.createElement("input");
    tvRange.type = "range";
    tvRange.min = "60";
    tvRange.max = "180";
    tvRange.step = "5";
    tvRange.value = String(tvPercent);
    tvRange.oninput = () => {
      tvPercent = Number(tvRange.value);
      tvValSpan.textContent = `TV Scale: ${tvPercent}%`;
      p.settings.with = { ...(p.settings.with || {}), tvSubtitleSizePercent: tvPercent };
      updateLiveSub("tv", Math.round(18 * (tvPercent / 100)));
    };
    tvRange.onchange = () => {
      p.settings.enabled = true;
      p.settings.with = { ...(p.settings.with || {}), tvSubtitleSizePercent: tvPercent };
      ctx.saveSection("profile", true);
    };
    tvLbl.append(tvValSpan, tvRange);

    const spLbl = document.createElement("label");
    spLbl.className = "field";
    const spValSpan = document.createElement("span");
    spValSpan.className = "tabular";
    spValSpan.textContent = `Desktop/Mobile: ${sizeSp} sp`;
    const spRange = document.createElement("input");
    spRange.type = "range";
    spRange.min = "12";
    spRange.max = "36";
    spRange.value = String(sizeSp);
    spRange.oninput = () => {
      sizeSp = Number(spRange.value);
      spValSpan.textContent = `Desktop/Mobile: ${sizeSp} sp`;
      p.settings.with = { ...(p.settings.with || {}), subtitleSizeSp: sizeSp };
      updateLiveSub("desktop", Math.round(sizeSp * 0.95));
    };
    spRange.onchange = () => {
      p.settings.enabled = true;
      p.settings.with = { ...(p.settings.with || {}), subtitleSizeSp: sizeSp };
      ctx.saveSection("profile", true);
    };
    spLbl.append(spValSpan, spRange);

    slidersGrid.append(tvLbl, spLbl);
    subBox.appendChild(slidersGrid);
    card.appendChild(subBox);

    const skipNames = new Set(["theme", "amoledBlack", "subtitleSizeSp", "tvSubtitleSizePercent"]);
    const fields = document.createElement("div");
    fields.style.cssText = "display:grid;grid-template-columns:1fr;gap:10px;margin-top:12px";

    (st.settingsParams || [])
      .filter(pr => !skipNames.has(pr.name))
      .forEach(param => {
        fields.appendChild(
          ctx.ui.makeParamInput(param, p.settings.with, (val) => {
            p.settings.with = { ...(p.settings.with || {}), [param.name]: val };
            if (param.name === "homeLayout") setViewportState({ screen: "home" });
            else if (param.name === "blurUnwatchedEpisodes") setViewportState({ screen: "details" });
            else if (param.name === "subtitlePreferred" || param.name === "subtitleSecondary") {
              setViewportState({ screen: "player" });
            }
            ctx.saveSection("profile", true);
          })
        );
      });
    card.appendChild(fields);
  }

  return card;
}

// ---------------------------------------------------------------------------
// Card 2 — Elite-Badges & Details-Page Enrichment (`nuvio_badges` + `nuvio_meta`)
// ---------------------------------------------------------------------------
function renderBadgesAndMetaCard(ctx, p) {
  const { icon } = ctx.ui;
  const st = ctx.state;
  const vp = getViewportState();
  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `
    <div class="card-eyebrow">${icon("shield", 12)} Stream Badges &amp; Details Page</div>
    <h2>Elite-Badges &amp; 8-Source Ratings</h2>
    <p class="sub">Real badge icons render on Streams &amp; Player OSD; MDBList + spoiler controls update Details.</p>
  `;

  const badgeRow = document.createElement("div");
  badgeRow.className = "row-between";
  badgeRow.style.cssText = "margin-bottom:12px;flex-wrap:wrap";
  badgeRow.appendChild(
    ctx.ui.makeSwitch("Enable Elite-Badges Pack", Boolean(p.badgesEnabled), (v) => {
      p.badgesEnabled = v;
      setViewportState({ screen: "details" });
      ctx.saveSection("profile", true);
    })
  );

  const posTabs = document.createElement("div");
  posTabs.className = "segmented";
  [["BOTTOM", "Row: BOTTOM"], ["TOP", "Row: TOP"]].forEach(([pos, lbl]) => {
    const b = document.createElement("button");
    b.className = "seg-btn" + (vp.badgePlacement === pos ? " active" : "");
    b.textContent = lbl;
    b.onclick = () => {
      setViewportState({ badgePlacement: pos, screen: "details" });
      ctx.render();
    };
    posTabs.appendChild(b);
  });
  badgeRow.appendChild(posTabs);
  card.appendChild(badgeRow);

  card.appendChild(
    ctx.ui.makeSwitch("Enable Details Enrichment & 8-Source Ratings (nuvio_meta)", Boolean(p.meta?.enabled), (v) => {
      p.meta.enabled = v;
      setViewportState({ screen: "details" });
      ctx.saveSection("profile", true);
    })
  );

  if (p.meta?.enabled && Array.isArray(st.metaParams) && st.metaParams.length) {
    const fields = document.createElement("div");
    fields.style.cssText = "display:grid;grid-template-columns:1fr;gap:10px;margin-top:12px";
    st.metaParams.forEach(param => {
      fields.appendChild(
        ctx.ui.makeParamInput(param, p.meta.with, (val) => {
          p.meta.with = { ...(p.meta.with || {}), [param.name]: val };
          setViewportState({ screen: "details" });
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
