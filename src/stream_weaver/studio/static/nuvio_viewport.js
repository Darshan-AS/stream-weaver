// Clean-room MIT Real-Time Nuvio Viewport (TV / Desktop / Mobile + 4 Interactive Screens)

const vpState = {
  device: "tv",        // "tv" | "desktop" | "mobile"
  screen: "home",      // "home" | "collection" | "details" | "player"
  colIdx: 0,
  folderIdx: 0,
  selectedItem: null,
  selectedStreamIdx: 0,
  badgePlacement: "BOTTOM",
};

const catalogPreviewCache = new Map();

export function getViewportState() {
  return vpState;
}

export function setViewportState(patch) {
  Object.assign(vpState, patch);
}

export function invalidatePreviewCache() {
  catalogPreviewCache.clear();
}

const STREAM_SAMPLES = [
  {
    addon: "AIOStreams",
    source: "TorBox",
    quality: "2160p",
    remux: true,
    hdr: "Dolby Vision HDR10+",
    codec: "HEVC 10bit",
    audio: "TrueHD 7.1 Atmos",
    size: "68.4 GB",
    origin: "BluRay REMUX · FGT",
  },
  {
    addon: "AIOStreams",
    source: "TorBox",
    quality: "2160p",
    remux: false,
    hdr: "Dolby Vision",
    codec: "HEVC 10bit",
    audio: "DD+ 5.1 Atmos",
    size: "18.7 GB",
    origin: "NF WEB-DL · FLUX",
  },
  {
    addon: "AIOStreams",
    source: "TorBox",
    quality: "1080p",
    remux: false,
    hdr: "SDR",
    codec: "AVC",
    audio: "AAC 2.0",
    size: "4.2 GB",
    origin: "ATVP WEB-DL · NTb",
  },
];

function effectiveSettings(profile) {
  const compiled = profile?.compiledPreview?.settings || {};
  const live = profile?.settings?.with || {};
  return {
    theme: live.theme || compiled.theme || "ocean",
    amoledBlack: live.amoledBlack !== undefined ? Boolean(live.amoledBlack) : (compiled.amoledBlack !== false),
    homeLayout: live.homeLayout || compiled.homeLayout || "modern",
    desktopNavigation: compiled.desktopNavigation || "topBar",
    heroSection: compiled.heroSection !== false,
    sidebarCollapsed: Boolean(compiled.sidebarCollapsed),
    continueWatchingEnabled: compiled.continueWatchingEnabled !== false,
    skipIntro: compiled.skipIntro !== false,
    blurUnwatchedEpisodes:
      live.blurUnwatchedEpisodes !== undefined
        ? Boolean(live.blurUnwatchedEpisodes)
        : Boolean(compiled.blurUnwatchedEpisodes),
    posterWidthDp: Number(compiled.posterWidthDp || 126),
    posterCornerRadiusDp: Number(compiled.posterCornerRadiusDp || 16),
    sizeSp: Number(live.subtitleSizeSp ?? compiled.subtitleStyle?.sizeSp ?? 18),
    tvPercent: Number(live.tvSubtitleSizePercent ?? compiled.subtitleStyle?.tvSizePercent ?? 100),
    subtitlePreferred: live.subtitlePreferred || compiled.subtitleStyle?.preferredLanguage || "en",
    subtitleSecondary:
      live.subtitleSecondary !== undefined
        ? (live.subtitleSecondary || "")
        : (compiled.subtitleStyle?.secondaryLanguage || ""),
  };
}

function effectiveMeta(profile) {
  const compiled = profile?.compiledPreview?.meta || {};
  const live = profile?.meta || {};
  const enabled = live.enabled !== undefined ? Boolean(live.enabled) : Boolean(compiled.enabled);
  const providers = compiled.providers || {
    imdb: true,
    tmdb: true,
    tomatoes: true,
    audience: true,
    metacritic: true,
    trakt: true,
    letterboxd: true,
    mal: true,
  };
  const blurUnwatched =
    live.with?.blurUnwatchedEpisodes !== undefined
      ? Boolean(live.with.blurUnwatchedEpisodes)
      : Boolean(compiled.blurUnwatchedEpisodes);
  const sections = Array.isArray(compiled.sections) && compiled.sections.length
    ? compiled.sections
    : [
        { key: "ACTIONS", enabled: true },
        { key: "OVERVIEW", enabled: true },
        { key: "PRODUCTION", enabled: true },
        { key: "CAST", enabled: true },
        { key: "COMMENTS", enabled: true },
        { key: "TRAILERS", enabled: true },
        { key: "EPISODES", enabled: true },
        { key: "MORE_LIKE_THIS", enabled: true },
      ];
  return { enabled, providers, blurUnwatched, sections };
}

function effectiveBadges(profile) {
  const compiled = profile?.compiledPreview?.badges || {};
  const enabled =
    profile?.badgesEnabled !== undefined ? Boolean(profile.badgesEnabled) : Boolean(compiled.enabled);
  return {
    enabled,
    placement: vpState.badgePlacement || compiled.placement || "BOTTOM",
    showInPlayer: compiled.showInPlayer !== false,
    filters: Array.isArray(compiled.filters) ? compiled.filters : [],
  };
}

function matchStreamBadgeIds(sample, debridLabel) {
  const ids = [];
  if (sample.quality === "2160p") ids.push("4k-ultra-hd");
  if (sample.quality === "1080p") ids.push("1080p-full-hd");
  if (sample.remux) ids.push("remux");
  else if (sample.origin.includes("WEB-DL")) ids.push("webdl");
  if (sample.hdr.includes("Dolby Vision")) ids.push("dolby-vision");
  if (sample.hdr.includes("HDR10+")) ids.push("hdr10-plus");
  if (sample.codec.includes("HEVC")) ids.push("hevc", "10bit");
  else if (sample.codec.includes("AVC")) ids.push("avc");
  if (sample.audio.includes("Atmos")) ids.push("dolby-atmos");
  if (sample.audio.includes("TrueHD")) ids.push("truehd");
  if (sample.audio.includes("DD+")) ids.push("dolby-digital-plus");
  if (sample.audio.includes("7.1")) ids.push("71-audio");
  else if (sample.audio.includes("5.1")) ids.push("51-audio");
  if (sample.origin.startsWith("NF ")) ids.push("netflix");
  if (sample.origin.startsWith("ATVP ")) ids.push("apple-tv-plus");
  const dLow = String(debridLabel || "").toLowerCase();
  if (dLow.includes("torbox")) ids.push("torbox");
  return ids;
}

function renderBadgesStrip(ctx, badgeCfg, sample, debridLabel) {
  const { escapeHtml } = ctx.ui;
  if (!badgeCfg.enabled) return "";
  const ids = matchStreamBadgeIds(sample, debridLabel);
  const byId = new Map(badgeCfg.filters.map(f => [f.id, f]));
  const items = [];
  ids.forEach(id => {
    const flt = byId.get(id);
    if (flt && flt.imageUrl) {
      items.push(
        `<img class="nv-badge-img" src="${escapeHtml(flt.imageUrl)}" alt="${escapeHtml(flt.name)}" title="${escapeHtml(flt.name)}" loading="lazy" onerror="this.outerHTML='<span class=\\'nv-badge-fallback\\'>${escapeHtml(flt.name)}</span>'" />`
      );
    } else if (!badgeCfg.filters.length) {
      items.push(`<span class="nv-badge-fallback">${escapeHtml(id.toUpperCase())}</span>`);
    }
  });
  return items.length ? `<div class="nv-badges-row">${items.join("")}</div>` : "";
}

function formatSampleStream(sample, debridLabel, fmtPreset) {
  const preset = fmtPreset || "prism";
  const srcName = debridLabel || sample.source;
  if (preset === "minimal") {
    return {
      left: `${sample.quality}\n${srcName}`,
      desc: `${sample.origin} · ${sample.size}`,
    };
  }
  if (preset === "compact_tv") {
    return {
      left: `${sample.quality}\nCached`,
      desc: `${sample.hdr} · ${sample.audio}\n${srcName} · ${sample.size}`,
    };
  }
  return {
    left: `${sample.addon}\n${sample.quality} ⚡`,
    desc: `${sample.origin}\n${sample.hdr} · ${sample.codec} · ${sample.audio} · ${srcName} (${sample.size})`,
  };
}

function buildPreviewSpecForFolder(col, folderIdx, profile) {
  const folders = col?.folders || [];
  const activeFolder = folders[folderIdx] || folders[0] || {};
  const src0 =
    (activeFolder.sources && activeFolder.sources[0]) ||
    col?.raw?.folders?.[folderIdx]?.sources?.[0] ||
    col?.inlineTemplate?.folders?.[folderIdx]?.sources?.[0] ||
    {};
  return {
    ...src0,
    aiometadataInstance: profile?.aiometadataInstance || "default",
    folderId: activeFolder.id || src0.catalogId || "folder",
    folderTitle: activeFolder.title || src0.title || col?.title || "Featured",
    collectionTitle: col?.title || "Home",
    rowTitle: (activeFolder.rows && activeFolder.rows[0]) || activeFolder.title || "",
  };
}

async function loadFolderPreview(ctx, profile, col, folderIdx, force = false) {
  const spec = buildPreviewSpecForFolder(col, folderIdx, profile);
  const cacheKey = `${profile.name}::${col?.id || col?.title}::${folderIdx}::${JSON.stringify(spec)}`;
  if (!force && catalogPreviewCache.has(cacheKey)) {
    return catalogPreviewCache.get(cacheKey);
  }
  const res = await ctx.api("/api/preview-catalog", {
    spec,
    pendingEnv: ctx.pendingEnvEdits,
  });
  catalogPreviewCache.set(cacheKey, res);
  return res;
}

export function renderNuvioViewport(ctx, options = {}) {
  const { escapeHtml, icon } = ctx.ui;
  const st = ctx.state;
  const profile = ctx.currentProfile();
  if (!profile) return document.createElement("div");

  if (options.syncSelection) {
    if (typeof options.syncSelection.colIdx === "number") vpState.colIdx = options.syncSelection.colIdx;
    if (typeof options.syncSelection.folderIdx === "number") vpState.folderIdx = options.syncSelection.folderIdx;
  }

  const cols = profile.collections || [];
  if (vpState.colIdx >= cols.length) vpState.colIdx = Math.max(0, cols.length - 1);
  const selCol = cols[vpState.colIdx] || null;
  const folders = selCol?.folders || [];
  if (vpState.folderIdx >= folders.length) vpState.folderIdx = 0;

  const s = effectiveSettings(profile);
  const metaCfg = effectiveMeta(profile);
  const badgeCfg = effectiveBadges(profile);

  const shell = document.createElement("div");
  shell.className = "nv-shell";

  // Top Controls Bar: Screen Breadcrumbs + Device Switcher + Refresh
  const topbar = document.createElement("div");
  topbar.className = "nv-topbar";

  const crumbs = document.createElement("div");
  crumbs.className = "nv-breadcrumbs";
  [
    ["home", "Home"],
    ["collection", selCol ? selCol.title : "Collection"],
    ["details", "Details & Streams"],
    ["player", "Player OSD"],
  ].forEach(([scrId, label]) => {
    const b = document.createElement("button");
    b.className = "nv-crumb" + (vpState.screen === scrId ? " active" : "");
    b.textContent = label;
    b.onclick = () => {
      vpState.screen = scrId;
      ctx.render();
    };
    crumbs.appendChild(b);
  });

  const rightBar = document.createElement("div");
  rightBar.className = "nv-toolbar-right";

  const devSeg = document.createElement("div");
  devSeg.className = "segmented";
  [
    ["tv", "TV"],
    ["desktop", "Desktop"],
    ["mobile", "Mobile"],
  ].forEach(([d, lbl]) => {
    const b = document.createElement("button");
    b.className = "seg-btn" + (vpState.device === d ? " active" : "");
    b.textContent = lbl;
    b.onclick = () => {
      vpState.device = d;
      ctx.render();
    };
    devSeg.appendChild(b);
  });

  const refreshBtn = document.createElement("button");
  refreshBtn.className = "btn ghost icon-btn";
  refreshBtn.title = "Refresh live catalog posters";
  refreshBtn.innerHTML = `${icon("refresh", 12)}`;
  refreshBtn.onclick = () => {
    invalidatePreviewCache();
    ctx.render();
  };

  rightBar.append(devSeg, refreshBtn);
  topbar.append(crumbs, rightBar);
  shell.appendChild(topbar);

  // Nuvio Device Frame
  const frame = document.createElement("div");
  frame.className = `nv-device-frame nv-layout-${s.homeLayout}`;
  frame.dataset.nuvioDevice = vpState.device;
  frame.dataset.nuvioTheme = s.theme;
  frame.dataset.nuvioAmoled = String(s.amoledBlack);
  frame.dataset.desktopNav = s.desktopNavigation;
  frame.style.setProperty("--nv-card-w", `${Math.max(82, Math.min(132, Math.round(s.posterWidthDp * 0.86)))}px`);
  frame.style.setProperty("--nv-radius", `${Math.max(6, Math.min(20, s.posterCornerRadiusDp))}px`);

  // Frame Header Bar
  const devLabel = vpState.device === "tv" ? "NuvioTV" : (vpState.device === "desktop" ? "Nuvio Desktop" : "Nuvio Mobile");
  const chrome = document.createElement("div");
  chrome.className = "nv-device-chrome";
  chrome.innerHTML = `
    <span class="nv-device-dots"><span></span><span></span><span></span><strong style="margin-left:4px;color:var(--nv-text)">${devLabel}</strong></span>
    <span>${escapeHtml(profile.emoji || "🎬")} ${escapeHtml(profile.displayName)} · ${escapeHtml(s.theme)}${s.amoledBlack ? " · AMOLED" : ""} · ${escapeHtml(s.homeLayout)}</span>
  `;
  frame.appendChild(chrome);

  const stageBody = document.createElement("div");
  stageBody.className = "nv-stage-body";

  const navItems = [
    ["home", "Home", "home"],
    ["collection", "Browse", "layers"],
    ["details", "Streams", "zap"],
    ["player", "Player", "play"],
  ];

  // TV or Desktop Sidebar Rail
  if (vpState.device === "tv" || (vpState.device === "desktop" && s.desktopNavigation === "sidebar")) {
    const rail = document.createElement("aside");
    rail.className = "nv-rail" + (s.sidebarCollapsed && vpState.device === "tv" ? " collapsed" : "");
    rail.innerHTML = `
      <div>
        <div class="nv-rail-brand"><span class="nv-rail-dot"></span><span class="nv-nav-label">Nuvio</span></div>
        <div class="nv-rail-items"></div>
      </div>
      <div class="nv-nav-label" style="font-size:10px;color:var(--nv-muted);padding:0 6px">${escapeHtml(profile.displayName)}</div>
    `;
    const list = rail.querySelector(".nv-rail-items");
    navItems.forEach(([id, lbl, ic]) => {
      const btn = document.createElement("button");
      btn.className = "nv-nav-btn" + (vpState.screen === id ? " active" : "");
      btn.innerHTML = `${icon(ic, 13)}<span class="nv-nav-label">${lbl}</span>`;
      btn.onclick = () => {
        vpState.screen = id;
        ctx.render();
      };
      list.appendChild(btn);
    });
    stageBody.appendChild(rail);
  } else if (vpState.device === "desktop") {
    const bar = document.createElement("div");
    bar.className = "nv-desktop-bar";
    bar.innerHTML = `<div class="nv-rail-brand" style="padding:0"><span class="nv-rail-dot"></span><span>Nuvio</span></div><div class="nv-desktop-nav-links"></div><span style="font-size:11px;color:var(--nv-muted)">${escapeHtml(profile.emoji)} ${escapeHtml(profile.displayName)}</span>`;
    const links = bar.querySelector(".nv-desktop-nav-links");
    navItems.forEach(([id, lbl, ic]) => {
      const btn = document.createElement("button");
      btn.className = "nv-nav-btn" + (vpState.screen === id ? " active" : "");
      btn.innerHTML = `${icon(ic, 12)}<span>${lbl}</span>`;
      btn.onclick = () => {
        vpState.screen = id;
        ctx.render();
      };
      links.appendChild(btn);
    });
    stageBody.appendChild(bar);
  }

  const mainScroll = document.createElement("div");
  mainScroll.className = "nv-main-scroll";
  stageBody.appendChild(mainScroll);

  if (vpState.device === "mobile") {
    const mb = document.createElement("div");
    mb.className = "nv-mobile-tabs";
    navItems.forEach(([id, lbl, ic]) => {
      const b = document.createElement("button");
      b.className = "nv-mobile-tab" + (vpState.screen === id ? " active" : "");
      b.innerHTML = `${icon(ic, 14)}<span>${lbl}</span>`;
      b.onclick = () => {
        vpState.screen = id;
        ctx.render();
      };
      mb.appendChild(b);
    });
    stageBody.appendChild(mb);
  }

  frame.appendChild(stageBody);
  shell.appendChild(frame);

  // Render Active Screen Content + Async Live Catalog Hydration
  const activeCol = selCol || cols[0] || null;
  if (vpState.screen === "home") {
    renderHomeScreen(mainScroll, ctx, profile, cols, s, options, async () => {
      if (!activeCol) return null;
      return loadFolderPreview(ctx, profile, activeCol, 0);
    });
  } else if (vpState.screen === "collection") {
    renderCollectionScreen(mainScroll, ctx, profile, selCol, s, options);
  } else if (vpState.screen === "details") {
    renderDetailsScreen(mainScroll, ctx, profile, selCol, s, metaCfg, badgeCfg, st);
  } else {
    renderPlayerScreen(mainScroll, ctx, profile, s, badgeCfg, st);
  }

  return shell;
}

function defaultFeaturedItem() {
  return {
    id: "tt15239678",
    title: "Dune: Part Two",
    year: "2024",
    posterUrl: "https://image.tmdb.org/t/p/w500/1pdfLvkbY9ohJlCjQH2CZjjYVvJ.jpg",
    backdropUrl: "https://image.tmdb.org/t/p/w1280/xOMo8BRK7PfcJv9JCnx7s5hj0PX.jpg",
    overview: "Paul Atreides unites with Chani and the Fremen while on a warpath of revenge against the conspirators who destroyed his family.",
    genres: ["Sci-Fi", "Adventure"],
    rating: "8.5",
    type: "movie",
  };
}

function renderHomeScreen(root, ctx, profile, cols, s, options, fetchHeroFn) {
  const { escapeHtml, icon } = ctx.ui;
  const heroItem = vpState.selectedItem || defaultFeaturedItem();

  if (s.heroSection) {
    const hero = document.createElement("div");
    hero.className = "nv-hero";
    if (heroItem.backdropUrl) {
      hero.style.backgroundImage = `url('${heroItem.backdropUrl.replace(/'/g, "\\'")}')`;
    }
    hero.innerHTML = `
      <div class="nv-hero-content">
        <div class="nv-hero-kicker">${icon("sparkles", 11)} <span data-hero-kicker>Featured on Home</span></div>
        <h3 class="nv-hero-title" data-hero-title>${escapeHtml(heroItem.title)}</h3>
        <div class="nv-hero-meta" data-hero-meta>
          <span>★ ${escapeHtml(heroItem.rating || "8.5")}</span>
          <span>• ${escapeHtml(heroItem.year || "2024")}</span>
          <span>• ${escapeHtml((heroItem.genres || ["Featured"]).join(" / "))}</span>
        </div>
        <div class="nv-hero-overview" data-hero-ov>${escapeHtml(heroItem.overview || "")}</div>
        <div class="nv-hero-actions">
          <button class="nv-cta primary" data-hero-play>${icon("play", 11)} Play</button>
          <button class="nv-cta" data-hero-info>Details &amp; Streams</button>
        </div>
      </div>
    `;
    hero.querySelector("[data-hero-play]").onclick = () => {
      vpState.selectedItem = vpState.selectedItem || heroItem;
      vpState.screen = "player";
      ctx.render();
    };
    hero.querySelector("[data-hero-info]").onclick = () => {
      vpState.selectedItem = vpState.selectedItem || heroItem;
      vpState.screen = "details";
      ctx.render();
    };
    root.appendChild(hero);

    fetchHeroFn().then(data => {
      const first = data?.items?.find(it => it.backdropUrl || it.posterUrl) || data?.items?.[0];
      if (!first || !root.isConnected) return;
      if (!vpState.selectedItem) vpState.selectedItem = first;
      const bg = first.backdropUrl || first.posterUrl;
      if (bg) hero.style.backgroundImage = `url('${bg.replace(/'/g, "\\'")}')`;
      const tEl = hero.querySelector("[data-hero-title]");
      const mEl = hero.querySelector("[data-hero-meta]");
      const oEl = hero.querySelector("[data-hero-ov]");
      if (tEl) tEl.textContent = first.title || heroItem.title;
      if (mEl) {
        mEl.innerHTML = `${first.rating ? `<span>★ ${escapeHtml(first.rating)}</span>` : ""}<span>• ${escapeHtml(first.year || "")}</span><span>• ${escapeHtml((first.genres && first.genres.length ? first.genres : [first.type || "movie"]).join(" / "))}</span>`;
      }
      if (oEl && first.overview) oEl.textContent = first.overview;
    }).catch(() => {});
  }

  if (s.continueWatchingEnabled) {
    const cwRow = document.createElement("div");
    cwRow.className = "nv-row";
    cwRow.innerHTML = `<div class="nv-row-head"><span class="nv-row-title">${icon("play", 12)} Continue Watching</span><span class="nv-row-sub">Synced Progress</span></div>`;
    const hstrip = document.createElement("div");
    hstrip.className = "nv-hstrip";
    [
      {
        title: "Severance",
        ep: "S2 · E04 · 34m left",
        prog: 58,
        bg: "https://image.tmdb.org/t/p/w1280/ixgFmf1X59PUy2yE85gB2kG0v7m.jpg",
        item: { id: "tt11280740", title: "Severance", year: "2022", rating: "8.7", type: "series", posterUrl: "https://image.tmdb.org/t/p/w500/lFf6LLrQjYldcZItzOkGmMMigP7.jpg", backdropUrl: "https://image.tmdb.org/t/p/w1280/ixgFmf1X59PUy2yE85gB2kG0v7m.jpg", overview: "Mark leads a team of office workers whose memories have been surgically divided between work and personal lives.", genres: ["Sci-Fi", "Thriller"] },
      },
      {
        title: "Andor",
        ep: "S1 · E09 · 19m left",
        prog: 74,
        bg: "https://image.tmdb.org/t/p/w1280/kNt9jG8wW2D9t3y5oR2P6X2W3V8.jpg",
        item: { id: "tt9253866", title: "Andor", year: "2022", rating: "8.4", type: "series", posterUrl: "https://image.tmdb.org/t/p/w500/59SVNwLfoMnZPPB6ukW6dlPxAdI.jpg", backdropUrl: "https://image.tmdb.org/t/p/w1280/kNt9jG8wW2D9t3y5oR2P6X2W3V8.jpg", overview: "Cassian Andor embarks on a path destined to turn him into a rebel hero.", genres: ["Sci-Fi", "Action"] },
      },
    ].forEach(cw => {
      const card = document.createElement("div");
      card.className = "nv-cw-card";
      card.innerHTML = `
        <div class="nv-cw-thumb" style="background-image:url('${cw.bg}')">
          <div class="nv-cw-prog-track"><div class="nv-cw-prog-fill" style="width:${cw.prog}%"></div></div>
        </div>
        <div class="nv-cw-meta">
          <div class="nv-cw-title">${escapeHtml(cw.title)}</div>
          <div class="nv-cw-ep">${escapeHtml(cw.ep)}</div>
        </div>
      `;
      card.onclick = () => {
        vpState.selectedItem = cw.item;
        vpState.screen = "details";
        ctx.render();
      };
      hstrip.appendChild(card);
    });
    cwRow.appendChild(hstrip);
    root.appendChild(cwRow);
  }

  if (!cols.length) {
    const empty = document.createElement("div");
    empty.className = "nv-row-sub";
    empty.textContent = "Add a collection on the left to preview Nuvio Home shelves.";
    root.appendChild(empty);
    return;
  }

  // Stacked Home Collections in live top-to-bottom order
  cols.forEach((col, cIdx) => {
    const row = document.createElement("div");
    row.className = "nv-row";
    const head = document.createElement("div");
    head.className = "nv-row-head";
    head.innerHTML = `<span class="nv-row-title">${cIdx + 1}. ${escapeHtml(col.title)}</span><span class="nv-row-sub">${(col.folders || []).length} tabs</span>`;
    row.appendChild(head);

    const strip = document.createElement("div");
    strip.className = s.homeLayout === "grid" ? "nv-col-grid" : "nv-hstrip";
    (col.folders || []).forEach((fld, fIdx) => {
      const fc = document.createElement("div");
      const isFocused = cIdx === vpState.colIdx && fIdx === vpState.folderIdx;
      fc.className = "nv-folder-card" + (isFocused ? " focused" : "");
      const imgHtml = fld.coverUrl
        ? `<img class="nv-folder-img" src="${escapeHtml(fld.coverUrl)}" alt="${escapeHtml(fld.title)}" loading="lazy" />`
        : `<div class="nv-folder-fallback">${escapeHtml(fld.title)}</div>`;
      fc.innerHTML = `
        ${imgHtml}
        <div class="nv-folder-foot">
          <div class="nv-folder-name">${escapeHtml(fld.title)}</div>
          <div class="nv-folder-rows">${(fld.rows || []).length} row(s)</div>
        </div>
      `;
      fc.onclick = () => {
        vpState.colIdx = cIdx;
        vpState.folderIdx = fIdx;
        vpState.screen = "collection";
        if (options.onSelectCollection) options.onSelectCollection(cIdx, fIdx);
        ctx.render();
      };
      strip.appendChild(fc);
    });
    row.appendChild(strip);
    root.appendChild(row);
  });
}

function renderCollectionScreen(root, ctx, profile, col, s, options) {
  const { escapeHtml } = ctx.ui;
  if (!col) {
    root.innerHTML = `<div class="nv-row-sub">No collection selected.</div>`;
    return;
  }
  const folders = col.folders || [];
  const activeFolder = folders[vpState.folderIdx] || folders[0] || null;

  const banner = document.createElement("div");
  banner.className = "nv-col-banner";
  if (col.backdropUrl) {
    banner.style.backgroundImage = `url('${col.backdropUrl.replace(/'/g, "\\'")}')`;
  }
  banner.innerHTML = `
    <div>
      <div style="font-size:10px;text-transform:uppercase;letter-spacing:0.08em;color:var(--nv-accent);font-weight:700">Collection</div>
      <div style="font-size:16px;font-weight:700">${escapeHtml(col.title)}</div>
    </div>
    <span class="pill" data-col-source>Loading…</span>
  `;
  root.appendChild(banner);

  if (folders.length) {
    const tabs = document.createElement("div");
    tabs.className = "nv-tab-strip";
    folders.forEach((f, idx) => {
      const btn = document.createElement("button");
      btn.className = "nv-tab-pill" + (idx === vpState.folderIdx ? " active" : "");
      btn.textContent = f.title;
      btn.onclick = () => {
        vpState.folderIdx = idx;
        if (options.onSelectCollection) options.onSelectCollection(vpState.colIdx, idx);
        ctx.render();
      };
      tabs.appendChild(btn);
    });
    root.appendChild(tabs);
  }

  const grid = document.createElement("div");
  grid.className = "nv-posters-grid";
  grid.innerHTML = `<div class="nv-row-sub">Loading posters for ${escapeHtml(activeFolder?.title || col.title)}…</div>`;
  root.appendChild(grid);

  loadFolderPreview(ctx, profile, col, vpState.folderIdx).then(data => {
    if (!root.isConnected) return;
    const srcEl = banner.querySelector("[data-col-source]");
    const src = data?.source || "sample";
    if (srcEl) {
      srcEl.className = "pill " + (src === "sample" ? "warn" : "ok");
      srcEl.textContent = src === "aiometadata" ? "Live AIOMetadata" : (src === "tmdb" ? "Live TMDB" : "Sample Preview");
    }
    const items = data?.items || [];
    grid.innerHTML = "";
    if (!items.length) {
      grid.innerHTML = `<div class="nv-row-sub">No items returned.</div>`;
      return;
    }
    items.forEach(item => {
      const card = document.createElement("div");
      card.className = "nv-poster-tile";
      card.innerHTML = `
        <img src="${escapeHtml(item.posterUrl || "")}" alt="${escapeHtml(item.title)}" loading="lazy" />
        ${item.rating ? `<span class="nv-rating-chip">★ ${escapeHtml(item.rating)}</span>` : ""}
        <div class="nv-poster-caption">
          <div class="t">${escapeHtml(item.title)}</div>
          <div class="y">${escapeHtml(item.year || "")}</div>
        </div>
      `;
      card.onclick = () => {
        vpState.selectedItem = item;
        vpState.screen = "details";
        ctx.render();
      };
      grid.appendChild(card);
    });
  }).catch(err => {
    if (!root.isConnected) return;
    grid.innerHTML = `<div class="nv-row-sub" style="color:var(--danger)">${escapeHtml(err.message)}</div>`;
  });
}

function renderDetailsScreen(root, ctx, profile, _col, s, metaCfg, badgeCfg, st) {
  const { escapeHtml, icon } = ctx.ui;
  const item = vpState.selectedItem || defaultFeaturedItem();
  const isSeries = item.type === "series";
  const debridLabel = (st.debridProviders || []).find(d => d.id === st.debridProvider)?.label || st.debridProvider || "TorBox";
  const fmtPreset = st.streamFormatter?.preset || "prism";

  const hero = document.createElement("div");
  hero.className = "nv-details-hero";
  const bd = item.backdropUrl || item.posterUrl || "";
  if (bd) hero.style.backgroundImage = `url('${bd.replace(/'/g, "\\'")}')`;

  const providerScores = [
    ["imdb", "IMDb", item.rating || "8.5"],
    ["tmdb", "TMDB", "84%"],
    ["tomatoes", "RT", "93%"],
    ["audience", "Popcorn", "91%"],
    ["metacritic", "MC", "83"],
    ["trakt", "Trakt", "88%"],
    ["letterboxd", "LB", "4.3"],
    ["mal", "MAL", "8.9"],
  ];
  const ratingsHtml = metaCfg.enabled
    ? providerScores
        .filter(([k]) => metaCfg.providers[k] !== false)
        .map(([, lbl, val]) => `<span class="nv-rate-badge">${lbl} <strong>${escapeHtml(val)}</strong></span>`)
        .join("")
    : `<span class="nv-row-sub">MDBList 8-Source Ratings disabled</span>`;

  hero.innerHTML = `
    <img class="nv-details-poster" src="${escapeHtml(item.posterUrl || "")}" alt="${escapeHtml(item.title)}" />
    <div style="min-width:0;flex:1">
      <div style="font-size:16px;font-weight:700;line-height:1.18">${escapeHtml(item.title)}</div>
      <div style="font-size:11px;color:#cbd5e1;margin-top:3px">${escapeHtml(item.year || "2024")} · ${escapeHtml((item.genres || []).join(" · ") || item.type)}</div>
      <div style="font-size:11px;color:#cbd5e1;margin-top:6px;line-height:1.38">${escapeHtml(item.overview || "Enriched with TMDB credits, logos, and trailer previews.")}</div>
      <div class="nv-ratings-bar">${ratingsHtml}</div>
    </div>
  `;
  root.appendChild(hero);

  // MetaScreen Ordered Sections + Live Episode Spoiler Blur Preview
  if (metaCfg.enabled) {
    const enabledSecs = metaCfg.sections.filter(sec => sec.enabled).map(sec => sec.key);
    const secStrip = document.createElement("div");
    secStrip.className = "nv-sections-strip";
    enabledSecs.forEach(k => {
      const sp = document.createElement("span");
      sp.className = "nv-sec-pill";
      sp.textContent = k;
      secStrip.appendChild(sp);
    });
    root.appendChild(secStrip);

    const blurOn = metaCfg.blurUnwatched || s.blurUnwatchedEpisodes;
    if (isSeries || blurOn) {
      const epWrap = document.createElement("div");
      epWrap.className = "nv-row";
      epWrap.innerHTML = `<div class="nv-row-head"><span class="nv-row-title">Episodes (Spoiler Protection Preview)</span><span class="nv-row-sub">${blurOn ? "Unwatched blurred" : "Thumbnails visible"}</span></div>`;
      const epGrid = document.createElement("div");
      epGrid.className = "nv-ep-strip";
      [
        { n: "E01 · Watched", watched: true },
        { n: "E02 · Unwatched", watched: false },
        { n: "E03 · Unwatched", watched: false },
      ].forEach(ep => {
        const ec = document.createElement("div");
        ec.className = "nv-ep-card";
        ec.innerHTML = `
          <div class="nv-ep-thumb ${!ep.watched && blurOn ? "blurred" : ""}" ${bd ? `style="background-image:url('${bd.replace(/'/g, "\\'")}')"` : ""}></div>
          <div style="padding:4px 6px;font-size:10px;color:var(--nv-muted)">${ep.n}</div>
        `;
        epGrid.appendChild(ec);
      });
      epWrap.appendChild(epGrid);
      root.appendChild(epWrap);
    }
  }

  // Live Stream Picker (Formatter + Real Elite-Badges PNG placement TOP / BOTTOM)
  const streamRow = document.createElement("div");
  streamRow.className = "nv-row";
  streamRow.innerHTML = `<div class="nv-row-head"><span class="nv-row-title">${icon("zap", 12)} Available Streams (${escapeHtml(fmtPreset)} · ${escapeHtml(badgeCfg.placement)})</span><span class="nv-row-sub">Click stream to launch Player OSD</span></div>`;
  const list = document.createElement("div");
  list.className = "nv-stream-list";

  STREAM_SAMPLES.forEach((sample, idx) => {
    const formatted = formatSampleStream(sample, debridLabel, fmtPreset);
    const badgesHtml = renderBadgesStrip(ctx, badgeCfg, sample, debridLabel);
    const card = document.createElement("div");
    card.className = "nv-stream-item";
    const bodyHtml = `<div class="nv-stream-main"><div class="nv-stream-left">${escapeHtml(formatted.left)}</div><div class="nv-stream-desc">${escapeHtml(formatted.desc)}</div></div>`;
    card.innerHTML = badgeCfg.placement === "TOP" ? `${badgesHtml}${bodyHtml}` : `${bodyHtml}${badgesHtml}`;
    card.onclick = () => {
      vpState.selectedStreamIdx = idx;
      vpState.screen = "player";
      ctx.render();
    };
    list.appendChild(card);
  });

  streamRow.appendChild(list);
  root.appendChild(streamRow);
}

function renderPlayerScreen(root, ctx, _profile, s, badgeCfg, st) {
  const { escapeHtml, icon } = ctx.ui;
  const item = vpState.selectedItem || defaultFeaturedItem();
  const sample = STREAM_SAMPLES[vpState.selectedStreamIdx] || STREAM_SAMPLES[0];
  const debridLabel = (st.debridProviders || []).find(d => d.id === st.debridProvider)?.label || st.debridProvider || "TorBox";
  const subPx = vpState.device === "tv" ? Math.round(18 * (s.tvPercent / 100)) : Math.round(s.sizeSp * 0.95);

  const stage = document.createElement("div");
  stage.className = "nv-player-stage";
  const bd = item.backdropUrl || item.posterUrl || "";
  if (bd) stage.style.backgroundImage = `url('${bd.replace(/'/g, "\\'")}')`;

  const top = document.createElement("div");
  top.className = "nv-player-osd-top";
  top.innerHTML = `
    <span><strong>${escapeHtml(item.title)}</strong> · ${escapeHtml(sample.quality)} ${escapeHtml(sample.hdr)}</span>
    <span class="pill" style="border-color:var(--nv-accent);color:var(--nv-accent)">${vpState.device === "tv" ? `TV ${s.tvPercent}%` : `${s.sizeSp} sp`} · ${escapeHtml(s.subtitlePreferred.toUpperCase())}</span>
  `;

  const mid = document.createElement("div");
  mid.className = "nv-player-mid";
  if (s.skipIntro) {
    const sk = document.createElement("div");
    sk.className = "nv-skip-btn";
    sk.innerHTML = `${icon("play", 10)} Skip Intro`;
    mid.appendChild(sk);
  }
  const sub = document.createElement("div");
  sub.className = "nv-sub-box";
  sub.style.fontSize = `${subPx}px`;
  sub.innerHTML = `All those moments will be lost in time, like tears in rain.` +
    (s.subtitleSecondary ? `<span class="nv-sub-sec">[${escapeHtml(s.subtitleSecondary.toUpperCase())}] 二言語字幕プレビュー · Dual Subtitles</span>` : "");
  mid.appendChild(sub);

  const bottom = document.createElement("div");
  bottom.className = "nv-player-bottom";
  const playerBadges = badgeCfg.showInPlayer ? renderBadgesStrip(ctx, badgeCfg, sample, debridLabel) : "";
  bottom.innerHTML = `
    ${playerBadges}
    <div class="nv-row-between" style="display:flex;justify-content:space-between;font-size:11px;color:#cbd5e1">
      <span style="display:inline-flex;align-items:center;gap:8px">${icon("play", 12)} ${icon("volume", 12)} 01:14:22</span>
      <span style="display:inline-flex;align-items:center;gap:8px">${icon("subtitles", 12)} 02:18:10</span>
    </div>
    <div class="nv-seek-track"><div class="nv-seek-fill"></div></div>
  `;

  stage.append(top, mid, bottom);
  root.appendChild(stage);
}
