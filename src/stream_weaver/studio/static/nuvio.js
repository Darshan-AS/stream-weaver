// Tab 4 — Nuvio Experience & Split Controls + Sticky Real-Time Nuvio Viewport

import { getViewportState, renderNuvioViewport, setViewportState } from "./nuvio_viewport.js";

const THEME_SWATCHES = [
  { id: "ocean", label: "Ocean", color: "#1E88E5" },
  { id: "crimson", label: "Crimson", color: "#E53935" },
  { id: "violet", label: "Violet", color: "#8E24AA" },
  { id: "emerald", label: "Emerald", color: "#43A047" },
  { id: "amber", label: "Amber", color: "#FB8C00" },
  { id: "rose", label: "Rose", color: "#D81B60" },
  { id: "gold", label: "Gold", color: "#FFD45C" },
  { id: "jade", label: "Jade", color: "#22D37C" },
  { id: "rose_gold", label: "Rose Gold", color: "#EC70A9" },
  { id: "arctic_blue", label: "Arctic Blue", color: "#3185F5" },
  { id: "graphite", label: "Graphite", color: "#AAB2BE" },
  { id: "white", label: "White", color: "#F5F5F5" },
];

export function renderNuvioTab(root, ctx) {
  const p = ctx.currentProfile();
  if (!p) return;

  const stack = document.createElement("div");
  stack.className = "section-stack";

  // Split Stage: Controls (Left) + Sticky Real-Time Nuvio Viewport (Right)
  const split = document.createElement("div");
  split.className = "studio-split-stage" + (getViewportState().theater ? " theater-mode" : "");

  const controlsStack = document.createElement("div");
  controlsStack.className = "section-stack";
  controlsStack.append(
    renderAppearanceAndSubtitlesCard(ctx, p),
    renderBadgesAndMetaCard(ctx, p)
  );

  const right = document.createElement("div");
  right.className = "card viewport-card sticky-stage";
  right.appendChild(renderNuvioViewport(ctx, {}));

  split.append(controlsStack, right);
  stack.appendChild(split);

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
    <h2>Nuvio Themes, Layout &amp; Subtitles</h2>
    <p class="sub">Changes appear immediately in the live TV, Desktop &amp; Mobile preview on the right.</p>
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
      setViewportState({ screen: "player", device: "tv" });
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
      setViewportState({ screen: "player", device: "desktop" });
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
    <h2>Stream Badges &amp; Details Ratings</h2>
    <p class="sub">Badge icons appear on stream cards and the video player; extra ratings and spoiler protection appear on movie/show details.</p>
  `;

  const curPlacement = String(
    p.badges?.with?.badgePlacement || p.compiledPreview?.badges?.placement || vp.badgePlacement || "BOTTOM"
  ).toUpperCase();
  const badgeRow = document.createElement("div");
  badgeRow.className = "row-between";
  badgeRow.style.cssText = "margin-bottom:12px;flex-wrap:wrap";
  badgeRow.appendChild(
    ctx.ui.makeSwitch("Enable Elite-Badges Pack", Boolean(p.badgesEnabled), (v) => {
      p.badgesEnabled = v;
      p.badges = {
        ref: p.badges?.ref || "default",
        with: { ...(p.badges?.with || {}) },
        enabled: v,
      };
      setViewportState({ screen: "streams" });
      ctx.saveSection("profile", true);
    })
  );

  const posTabs = document.createElement("div");
  posTabs.className = "segmented";
  [["BOTTOM", "Row: BOTTOM"], ["TOP", "Row: TOP"]].forEach(([pos, lbl]) => {
    const b = document.createElement("button");
    b.className = "seg-btn" + (curPlacement === pos ? " active" : "");
    b.textContent = lbl;
    b.onclick = () => {
      p.badgesEnabled = true;
      p.badges = {
        enabled: true,
        ref: p.badges?.ref || "default",
        with: { ...(p.badges?.with || {}), badgePlacement: pos },
      };
      setViewportState({ badgePlacement: pos, screen: "streams" });
      ctx.saveSection("profile", true);
    };
    posTabs.appendChild(b);
  });
  badgeRow.appendChild(posTabs);
  card.appendChild(badgeRow);

  card.appendChild(
    ctx.ui.makeSwitch("Enable 8-Source Details Ratings & Spoiler Protection", Boolean(p.meta?.enabled), (v) => {
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
