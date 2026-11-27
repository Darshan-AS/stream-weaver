// Clean-room MIT High-Fidelity Real-Time Nuvio Viewport (Desktop / TV / Mobile + 5 Interactive Screens)

const vpState = {
  device: "desktop",   // "desktop" | "tv" | "mobile"
  screen: "home",      // "home" | "collection" | "details" | "streams" | "player"
  colIdx: 0,
  folderIdx: 0,        // -1 means "All" tab when showAllTab is enabled
  heroSlideIdx: 0,
  heroItems: [],
  selectedItem: null,
  selectedStreamIdx: 0,
  streamFilter: "all", // "all" | "4k" | "hdr" | "cached"
  badgePlacement: "BOTTOM",
  theater: false,
  inLibrary: false,
  watched: false,
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
  vpState.heroItems = [];
}

const THEME_ACCENT_HEX = {
  ocean: "#1E88E5",
  crimson: "#E53935",
  violet: "#8E24AA",
  emerald: "#43A047",
  amber: "#FB8C00",
  rose: "#D81B60",
  gold: "#FFD45C",
  jade: "#22D37C",
  rose_gold: "#EC70A9",
  arctic_blue: "#3185F5",
  graphite: "#AAB2BE",
  white: "#F5F5F5",
};

const STREAM_SAMPLES = [
  {
    addon: "AIOStreams",
    source: "TorBox",
    quality: "2160p",
    remux: true,
    cached: true,
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
    cached: true,
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
    cached: true,
    hdr: "SDR",
    codec: "AVC",
    audio: "AAC 2.0",
    size: "4.2 GB",
    origin: "ATVP WEB-DL · NTb",
  },
];

const CONTINUE_WATCHING_SAMPLES = [
  {
    title: "Princess Mononoke",
    epCode: "",
    epName: "",
    pill: "12% watched",
    prog: 12,
    bg: "https://image.tmdb.org/t/p/w1280/gl0jzn4BupSbL2qmveqrjKkF9Js.jpg",
    item: {
      id: "tt0119698",
      title: "Princess Mononoke",
      year: "1997",
      runtime: "2h 14m",
      certification: "PG-13",
      language: "JA",
      rating: "8.4",
      type: "movie",
      posterUrl: "https://image.tmdb.org/t/p/w500/cMYCDADoLKLbB83g4WnJegaZimC.jpg",
      backdropUrl: "https://image.tmdb.org/t/p/w1280/gl0jzn4BupSbL2qmveqrjKkF9Js.jpg",
      overview: "On a journey to find the cure for a Tatarigami's curse, Ashitaka finds himself in the middle of a war between the forest gods and a mining colony.",
      genres: ["Animation", "Fantasy"],
    },
  },
  {
    title: "Severance",
    epCode: "S1 E4",
    epName: "The You You Are",
    pill: "Next Up",
    prog: 0,
    bg: "https://image.tmdb.org/t/p/w1280/ixgFmf1X59PUy2yE85gB2kG0v7m.jpg",
    item: {
      id: "tt11280740",
      title: "Severance",
      year: "2022",
      runtime: "55m",
      certification: "TV-MA",
      language: "EN",
      rating: "8.7",
      type: "series",
      posterUrl: "https://image.tmdb.org/t/p/w500/lFf6LLrQjYldcZItzOkGmMMigP7.jpg",
      backdropUrl: "https://image.tmdb.org/t/p/w1280/ixgFmf1X59PUy2yE85gB2kG0v7m.jpg",
      overview: "Mark leads a team of office workers whose memories have been surgically divided between their work and personal lives.",
      genres: ["Sci-Fi", "Thriller"],
    },
  },
  {
    title: "Shogun",
    epCode: "S1 E3",
    epName: "Tomorrow Is Tomorrow",
    pill: "18% watched",
    prog: 18,
    bg: "https://image.tmdb.org/t/p/w1280/5zmiBoMzeeVdQ62no55JOJMY498.jpg",
    item: {
      id: "tt2788316",
      title: "Shogun",
      year: "2024",
      runtime: "1h 0m",
      certification: "TV-MA",
      language: "JA",
      rating: "8.6",
      type: "series",
      posterUrl: "https://image.tmdb.org/t/p/w500/7O4iVfOMQmdCSxhOg1WnzG1AgYT.jpg",
      backdropUrl: "https://image.tmdb.org/t/p/w1280/5zmiBoMzeeVdQ62no55JOJMY498.jpg",
      overview: "When a mysterious European ship is found marooned in a nearby fishing village, Lord Toranaga uncovers secrets that could tip the scales of power.",
      genres: ["Drama", "History"],
    },
  },
  {
    title: "Arcane",
    epCode: "S2 E2",
    epName: "Watch It All Burn",
    pill: "64% watched",
    prog: 64,
    bg: "https://image.tmdb.org/t/p/w1280/rkB4LyZHo1NHXFEDHl9vSD9r1lI.jpg",
    item: {
      id: "tt11126994",
      title: "Arcane",
      year: "2021",
      runtime: "42m",
      certification: "TV-14",
      language: "EN",
      rating: "9.0",
      type: "series",
      posterUrl: "https://image.tmdb.org/t/p/w500/fqldf2t8ztc9aiwn3k6mlX3tvRT.jpg",
      backdropUrl: "https://image.tmdb.org/t/p/w1280/rkB4LyZHo1NHXFEDHl9vSD9r1lI.jpg",
      overview: "Amid the stark discord of twin cities Piltover and Zaun, two sisters fight on rival sides of a war between magic technologies and clashing convictions.",
      genres: ["Animation", "Sci-Fi"],
    },
  },
];

const DEFAULT_HERO_SLIDES = [
  {
    id: "tt15239678",
    title: "Dune: Part Two",
    year: "2024",
    runtime: "2h 46m",
    certification: "PG-13",
    language: "EN",
    posterUrl: "https://image.tmdb.org/t/p/w500/1pdfLvkbY9ohJlCjQH2CZjjYVvJ.jpg",
    backdropUrl: "https://image.tmdb.org/t/p/w1280/xOMo8BRK7PfcJv9JCnx7s5hj0PX.jpg",
    logoUrl: "",
    overview: "Paul Atreides unites with Chani and the Fremen while on a warpath of revenge against the conspirators who destroyed his family. Facing a choice between the love of his life and the fate of the universe.",
    genres: ["Sci-Fi", "Adventure"],
    rating: "8.5",
    type: "movie",
  },
  {
    id: "tt15398776",
    title: "Oppenheimer",
    year: "2023",
    runtime: "3h 0m",
    certification: "R",
    language: "EN",
    posterUrl: "https://image.tmdb.org/t/p/w500/8Gxv8gSFCU0XGDykEGv7zR1n2ua.jpg",
    backdropUrl: "https://image.tmdb.org/t/p/w1280/fm6KqXpk3M2HVveHwCrBSSBaO0V.jpg",
    logoUrl: "",
    overview: "The story of J. Robert Oppenheimer's role in the development of the atomic bomb during World War II.",
    genres: ["Drama", "History"],
    rating: "8.3",
    type: "movie",
  },
  {
    id: "tt1856101",
    title: "Blade Runner 2049",
    year: "2017",
    runtime: "2h 44m",
    certification: "R",
    language: "EN",
    posterUrl: "https://image.tmdb.org/t/p/w500/gajva2L0rPYkEWjzgFlBXCAVBE5.jpg",
    backdropUrl: "https://image.tmdb.org/t/p/w1280/ilRyazdMJwN05exqhwK4tMKBYZs.jpg",
    logoUrl: "",
    overview: "Thirty years after the events of the first film, a new blade runner unearths a long-buried secret with the potential to plunge society into chaos.",
    genres: ["Sci-Fi", "Mystery"],
    rating: "8.0",
    type: "movie",
  },
];

const CAST_SAMPLES = [
  { name: "Timothée Chalamet", role: "Paul Atreides", init: "TC", bg: "#1e3a5f" },
  { name: "Zendaya", role: "Chani", init: "Z", bg: "#4c1d45" },
  { name: "Rebecca Ferguson", role: "Lady Jessica", init: "RF", bg: "#1f3d2f" },
  { name: "Javier Bardem", role: "Stilgar", init: "JB", bg: "#4a2c14" },
  { name: "Austin Butler", role: "Feyd-Rautha", init: "AB", bg: "#2d2a4a" },
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
    posterCornerRadiusDp: Number(compiled.posterCornerRadiusDp || 14),
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
        { key: "CAST", enabled: true },
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
  const idx = folderIdx < 0 ? 0 : folderIdx;
  const activeFolder = folders[idx] || folders[0] || {};
  const src0 =
    (activeFolder.sources && activeFolder.sources[0]) ||
    col?.raw?.folders?.[idx]?.sources?.[0] ||
    col?.inlineTemplate?.folders?.[idx]?.sources?.[0] ||
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

function safeBgCss(url) {
  if (!url) return "";
  return `url('${String(url).replace(/'/g, "\\'")}')`;
}

function normalizeShape(rawShape) {
  const s = String(rawShape || "LANDSCAPE").toUpperCase();
  if (s === "SQUARE") return "square";
  if (s === "POSTER" || s === "PORTRAIT") return "poster";
  return "landscape";
}

function attachImgFallback(imgEl, fallbackHtml) {
  if (!imgEl) return;
  imgEl.onerror = () => {
    const wrap = document.createElement("div");
    wrap.innerHTML = fallbackHtml;
    if (wrap.firstElementChild) imgEl.replaceWith(wrap.firstElementChild);
  };
}

// =========================================================================
// Main Public Renderer
// =========================================================================
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
  shell.className = "nv-shell" + (vpState.theater ? " theater" : "");

  // -----------------------------------------------------------------------
  // Studio Viewport Toolbar (cleanly outside the frameless Nuvio app canvas)
  // -----------------------------------------------------------------------
  const topbar = document.createElement("div");
  topbar.className = "nv-topbar";

  const crumbs = document.createElement("div");
  crumbs.className = "nv-breadcrumbs";
  [
    ["home", "Home"],
    ["collection", selCol ? selCol.title : "Collection"],
    ["details", "Details"],
    ["streams", "Streams"],
    ["player", "Player"],
  ].forEach(([scrId, label]) => {
    const b = document.createElement("button");
    b.className = "nv-crumb" + (vpState.screen === scrId ? " active" : "");
    b.textContent = label;
    b.title = label;
    b.onclick = () => {
      vpState.screen = scrId;
      ctx.render();
    };
    crumbs.appendChild(b);
  });

  const rightBar = document.createElement("div");
  rightBar.className = "nv-toolbar-right";

  const accentDotHex = THEME_ACCENT_HEX[s.theme] || "#1E88E5";
  const statusChip = document.createElement("span");
  statusChip.className = "nv-status-chip";
  statusChip.innerHTML = `<span class="nv-status-dot" style="background:${accentDotHex}"></span>${escapeHtml(s.theme)}${s.amoledBlack ? " · AMOLED" : ""}`;

  const devSeg = document.createElement("div");
  devSeg.className = "segmented";
  [
    ["desktop", "Desktop"],
    ["tv", "TV"],
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

  const theaterBtn = document.createElement("button");
  theaterBtn.className = "btn ghost icon-btn" + (vpState.theater ? " accent-outline" : "");
  theaterBtn.title = vpState.theater ? "Exit Theater Mode" : "Expand Nuvio Viewport (Theater Mode)";
  theaterBtn.innerHTML = icon(vpState.theater ? "minimize" : "maximize", 12);
  theaterBtn.onclick = () => {
    vpState.theater = !vpState.theater;
    if (options.onToggleTheater) options.onToggleTheater(vpState.theater);
    ctx.render();
  };

  const refreshBtn = document.createElement("button");
  refreshBtn.className = "btn ghost icon-btn";
  refreshBtn.title = "Refresh live catalog posters";
  refreshBtn.innerHTML = icon("refresh", 12);
  refreshBtn.onclick = () => {
    invalidatePreviewCache();
    ctx.render();
  };

  rightBar.append(statusChip, devSeg, theaterBtn, refreshBtn);
  topbar.append(crumbs, rightBar);
  shell.appendChild(topbar);

  // -----------------------------------------------------------------------
  // Frameless Real-Time Nuvio Device Frame
  // -----------------------------------------------------------------------
  const frame = document.createElement("div");
  frame.className = `nv-device-frame nv-layout-${s.homeLayout}`;
  frame.dataset.nuvioDevice = vpState.device;
  frame.dataset.nuvioTheme = s.theme;
  frame.dataset.nuvioAmoled = String(s.amoledBlack);
  frame.dataset.desktopNav = s.desktopNavigation;
  frame.style.setProperty("--nv-card-w", `${Math.max(90, Math.min(136, Math.round(s.posterWidthDp * 0.92)))}px`);
  frame.style.setProperty("--nv-radius", `${Math.max(8, Math.min(18, s.posterCornerRadiusDp))}px`);

  const stageBody = document.createElement("div");
  stageBody.className = "nv-stage-body";

  if (vpState.device === "mobile") {
    const notch = document.createElement("div");
    notch.className = "nv-mobile-notch";
    stageBody.appendChild(notch);
  }

  const isPlayer = vpState.screen === "player";

  // 1) TV or Desktop Sidebar Left Rail (hidden during full-screen Player OSD)
  if (!isPlayer && (vpState.device === "tv" || (vpState.device === "desktop" && s.desktopNavigation === "sidebar"))) {
    const rail = document.createElement("aside");
    rail.className = "nv-rail" + (s.sidebarCollapsed && vpState.device === "tv" ? " collapsed" : "");
    rail.innerHTML = `
      <div>
        <div class="nv-rail-logo"><span class="nv-rail-mark">N</span><span class="nv-nav-label">Nuvio</span></div>
        <div class="nv-rail-items"></div>
      </div>
      <div class="nv-rail-profile" title="${escapeHtml(profile.displayName)}">
        <span>${escapeHtml(profile.emoji || "🎬")}</span>
        <span class="nv-nav-label" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(profile.displayName)}</span>
      </div>
    `;
    const list = rail.querySelector(".nv-rail-items");
    [
      ["home", "Home", "home"],
      ["collection", "Library", "library"],
      ["details", "Details", "film"],
      ["streams", "Streams", "zap"],
    ].forEach(([id, lbl, ic]) => {
      const btn = document.createElement("button");
      btn.className = "nv-nav-btn" + (vpState.screen === id ? " active" : "");
      btn.innerHTML = `${icon(ic, 15)}<span class="nv-nav-label">${lbl}</span>`;
      btn.onclick = () => {
        vpState.screen = id;
        ctx.render();
      };
      list.appendChild(btn);
    });
    stageBody.appendChild(rail);
  }

  // 2) Desktop Top Floating Glass Pill (exact match to real Nuvio Desktop topBar)
  if (!isPlayer && vpState.device === "desktop" && s.desktopNavigation !== "sidebar") {
    const floatNav = document.createElement("nav");
    floatNav.className = "nv-floating-nav";
    floatNav.setAttribute("aria-label", "Nuvio Desktop Navigation");

    [
      ["home", "Home", "home"],
      ["details", "Search / Details", "search"],
      ["collection", "Library / Collections", "library"],
    ].forEach(([id, title, ic]) => {
      const btn = document.createElement("button");
      btn.className = "nv-float-btn" + (vpState.screen === id ? " active" : "");
      btn.title = title;
      btn.innerHTML = icon(ic, 15);
      btn.onclick = () => {
        vpState.screen = id;
        ctx.render();
      };
      floatNav.appendChild(btn);
    });

    const avatarBtn = document.createElement("button");
    avatarBtn.className = "nv-avatar-pill";
    avatarBtn.title = `${profile.displayName} · Open Streams`;
    avatarBtn.textContent = profile.emoji || "🎬";
    avatarBtn.onclick = () => {
      vpState.screen = "streams";
      ctx.render();
    };
    floatNav.appendChild(avatarBtn);
    stageBody.appendChild(floatNav);

    const cornerBtn = document.createElement("button");
    cornerBtn.className = "nv-corner-action";
    cornerBtn.title = vpState.theater ? "Collapse Theater View" : "Expand Theater View";
    cornerBtn.innerHTML = icon(vpState.theater ? "minimize" : "maximize", 14);
    cornerBtn.onclick = () => {
      vpState.theater = !vpState.theater;
      if (options.onToggleTheater) options.onToggleTheater(vpState.theater);
      ctx.render();
    };
    stageBody.appendChild(cornerBtn);
  }

  const mainScroll = document.createElement("div");
  mainScroll.className = "nv-main-scroll";
  stageBody.appendChild(mainScroll);

  // 3) Mobile Floating Frosted Jelly Bottom Navigation Dock
  if (!isPlayer && vpState.device === "mobile") {
    const dock = document.createElement("nav");
    dock.className = "nv-mobile-dock";
    [
      ["home", "Home", "home"],
      ["details", "Search", "search"],
      ["collection", "Library", "library"],
      ["streams", "Streams", "zap"],
    ].forEach(([id, lbl, ic]) => {
      const b = document.createElement("button");
      b.className = "nv-mobile-tab" + (vpState.screen === id ? " active" : "");
      b.innerHTML = `${icon(ic, 15)}<span>${lbl}</span>`;
      b.onclick = () => {
        vpState.screen = id;
        ctx.render();
      };
      dock.appendChild(b);
    });
    stageBody.appendChild(dock);
  }

  frame.appendChild(stageBody);
  shell.appendChild(frame);

  // Render Active Screen Content
  const activeCol = selCol || cols[0] || null;
  if (vpState.screen === "home") {
    renderHomeScreen(mainScroll, ctx, profile, cols, s, options, async () => {
      if (!activeCol) return null;
      return loadFolderPreview(ctx, profile, activeCol, 0);
    });
  } else if (vpState.screen === "collection") {
    renderCollectionScreen(mainScroll, ctx, profile, selCol, s, options);
  } else if (vpState.screen === "details") {
    renderDetailsScreen(mainScroll, ctx, profile, selCol, s, metaCfg, st);
  } else if (vpState.screen === "streams") {
    renderStreamsScreen(mainScroll, ctx, profile, s, badgeCfg, st);
  } else {
    renderPlayerScreen(mainScroll, ctx, profile, s, badgeCfg, st);
  }

  return shell;
}

// =========================================================================
// Screen 1 — Home (Full-Bleed Hero + Continue Watching + Collection Rows)
// =========================================================================
function renderHomeScreen(root, ctx, _profile, cols, s, options, fetchHeroFn) {
  const { escapeHtml } = ctx.ui;
  const slides = vpState.heroItems.length ? vpState.heroItems : DEFAULT_HERO_SLIDES;
  if (vpState.heroSlideIdx >= slides.length) vpState.heroSlideIdx = 0;
  const heroItem = slides[vpState.heroSlideIdx] || DEFAULT_HERO_SLIDES[0];

  if (s.heroSection) {
    const hero = document.createElement("section");
    hero.className = "nv-hero";
    const bg = heroItem.backdropUrl || heroItem.posterUrl;
    if (bg) hero.style.backgroundImage = safeBgCss(bg);

    const mediaLbl = heroItem.type === "series" ? "Series" : "Movie";
    const genreLbl = (heroItem.genres && heroItem.genres[0]) || (heroItem.type === "series" ? "Drama" : "Sci-Fi");
    const runtimeLbl = heroItem.runtime || (heroItem.type === "series" ? "52m" : "2h 18m");
    const certLbl = heroItem.certification || (heroItem.type === "series" ? "TV-MA" : "PG-13");
    const langLbl = heroItem.language || "EN";

    const titleVisual = heroItem.logoUrl
      ? `<img class="nv-hero-logo" src="${escapeHtml(heroItem.logoUrl)}" alt="${escapeHtml(heroItem.title)}" data-hero-logo />`
      : `<h2 class="nv-hero-title" data-hero-title>${escapeHtml(heroItem.title)}</h2>`;

    hero.innerHTML = `
      <div class="nv-hero-content">
        ${titleVisual}
        <div class="nv-hero-meta-line" data-hero-meta>
          <span>${escapeHtml(mediaLbl)}</span>
          <span class="nv-meta-sep">•</span>
          <span>${escapeHtml(genreLbl)}</span>
          <span class="nv-meta-sep">•</span>
          <span>${escapeHtml(runtimeLbl)}</span>
          ${heroItem.year ? `<span class="nv-meta-sep">•</span><span>${escapeHtml(heroItem.year)}</span>` : ""}
        </div>
        <div class="nv-hero-badges" data-hero-badges>
          <span class="nv-cert-split"><span>${escapeHtml(certLbl)}</span><span>RELEASED</span></span>
          <span class="nv-meta-sep">•</span>
          <span class="nv-imdb-tag"><span class="nv-imdb-pill">IMDb</span> ${escapeHtml(heroItem.rating || "8.5")}</span>
          <span class="nv-meta-sep">•</span>
          <span>${escapeHtml(langLbl)}</span>
        </div>
        <div class="nv-hero-overview" data-hero-ov>${escapeHtml(heroItem.overview || "")}</div>
      </div>
      <div class="nv-hero-foot">
        <button class="nv-btn-white" data-hero-details>View Details</button>
        <div class="nv-hero-dots" data-hero-dots></div>
      </div>
    `;

    const logoEl = hero.querySelector("[data-hero-logo]");
    if (logoEl) {
      attachImgFallback(logoEl, `<h2 class="nv-hero-title">${escapeHtml(heroItem.title)}</h2>`);
    }

    hero.querySelector("[data-hero-details]").onclick = () => {
      vpState.selectedItem = heroItem;
      vpState.screen = "details";
      ctx.render();
    };

    const dotsWrap = hero.querySelector("[data-hero-dots]");
    slides.slice(0, 7).forEach((_, i) => {
      const dot = document.createElement("button");
      dot.className = "nv-hero-dot" + (i === vpState.heroSlideIdx ? " active" : "");
      dot.title = `Featured #${i + 1}`;
      dot.onclick = () => {
        vpState.heroSlideIdx = i;
        ctx.render();
      };
      dotsWrap.appendChild(dot);
    });

    root.appendChild(hero);

    if (!vpState.heroItems.length) {
      fetchHeroFn().then(data => {
        const fetched = (data?.items || []).filter(it => it.backdropUrl || it.posterUrl).slice(0, 7);
        if (!fetched.length || !root.isConnected) return;
        vpState.heroItems = fetched;
        const first = fetched[0];
        if (!vpState.selectedItem) vpState.selectedItem = first;
        if (first.backdropUrl || first.posterUrl) {
          hero.style.backgroundImage = safeBgCss(first.backdropUrl || first.posterUrl);
        }
        const tEl = hero.querySelector("[data-hero-title]");
        if (tEl && first.title) tEl.textContent = first.title;
        const oEl = hero.querySelector("[data-hero-ov]");
        if (oEl && first.overview) oEl.textContent = first.overview;
      }).catch(() => {});
    }
  }

  const rowsWrap = document.createElement("div");
  rowsWrap.className = "nv-home-rows";

  // Continue Watching Row (16:9 edge-to-edge cards matching real Nuvio)
  if (s.continueWatchingEnabled) {
    const cwSec = document.createElement("section");
    cwSec.innerHTML = `<div class="nv-section-head"><h3 class="nv-section-title">Continue Watching</h3></div>`;
    const strip = document.createElement("div");
    strip.className = "nv-hstrip";

    CONTINUE_WATCHING_SAMPLES.forEach(cw => {
      const card = document.createElement("div");
      card.className = "nv-cw-card";
      card.style.backgroundImage = safeBgCss(cw.bg);
      card.innerHTML = `
        <span class="nv-cw-pill">${escapeHtml(cw.pill)}</span>
        <div class="nv-cw-overlay">
          ${cw.epCode ? `<div class="nv-cw-ep-code">${escapeHtml(cw.epCode)}</div>` : ""}
          <div class="nv-cw-title">${escapeHtml(cw.title)}</div>
          ${cw.epName ? `<div class="nv-cw-ep-name">${escapeHtml(cw.epName)}</div>` : ""}
        </div>
        ${cw.prog > 0 ? `<div class="nv-cw-prog-track"><div class="nv-cw-prog-fill" style="width:${cw.prog}%"></div></div>` : ""}
      `;
      card.onclick = () => {
        vpState.selectedItem = cw.item;
        vpState.screen = "details";
        ctx.render();
      };
      strip.appendChild(card);
    });

    cwSec.appendChild(strip);
    rowsWrap.appendChild(cwSec);
  }

  if (!cols.length) {
    const empty = document.createElement("div");
    empty.style.cssText = "font-size:12px;color:var(--nv-muted)";
    empty.textContent = "Add a collection on the left to preview Nuvio Home shelves.";
    rowsWrap.appendChild(empty);
    root.appendChild(rowsWrap);
    return;
  }

  // Stacked Home Collection Rows respecting tileShape (LANDSCAPE/SQUARE/POSTER), hideTitle & focusGifUrl
  cols.forEach((col, cIdx) => {
    const sec = document.createElement("section");
    sec.innerHTML = `<div class="nv-section-head"><h3 class="nv-section-title">${escapeHtml(col.title)}</h3></div>`;

    const strip = document.createElement("div");
    strip.className = s.homeLayout === "grid" ? "nv-col-grid" : "nv-hstrip";
    const glowEnabled = col.focusGlowEnabled !== false;

    (col.folders || []).forEach((fld, fIdx) => {
      const shape = normalizeShape(fld.tileShape);
      const isFocused = cIdx === vpState.colIdx && fIdx === vpState.folderIdx;
      const wrap = document.createElement("div");
      wrap.className = `nv-folder-wrap shape-${shape}` + (glowEnabled ? " glow-on" : "") + (isFocused ? " focused" : "");

      const tile = document.createElement("div");
      tile.className = "nv-folder-tile";

      const fallbackHtml = `<div class="nv-folder-fallback">${fld.coverEmoji ? `<span style="font-size:20px">${escapeHtml(fld.coverEmoji)}</span>` : ""}<span>${escapeHtml(fld.title)}</span></div>`;

      if (fld.coverUrl) {
        const img = document.createElement("img");
        img.className = "nv-folder-img";
        img.src = fld.coverUrl;
        img.alt = fld.title;
        img.loading = "lazy";
        attachImgFallback(img, fallbackHtml);
        tile.appendChild(img);

        if (fld.focusGifEnabled && fld.focusGifUrl) {
          const gifBadge = document.createElement("span");
          gifBadge.className = "nv-folder-gif-dot";
          gifBadge.textContent = "LIVE";
          tile.appendChild(gifBadge);

          wrap.onmouseenter = () => {
            if (img.isConnected) img.src = fld.focusGifUrl;
          };
          wrap.onmouseleave = () => {
            if (img.isConnected) img.src = fld.coverUrl;
          };
        }
      } else {
        tile.innerHTML = fallbackHtml;
      }

      wrap.appendChild(tile);

      if (!fld.hideTitle) {
        const lbl = document.createElement("div");
        lbl.className = "nv-folder-label";
        lbl.textContent = fld.title;
        wrap.appendChild(lbl);
      }

      wrap.onclick = () => {
        vpState.colIdx = cIdx;
        vpState.folderIdx = fIdx;
        vpState.screen = "collection";
        if (options.onSelectCollection) options.onSelectCollection(cIdx, fIdx);
        ctx.render();
      };

      strip.appendChild(wrap);
    });

    sec.appendChild(strip);
    rowsWrap.appendChild(sec);
  });

  root.appendChild(rowsWrap);
}

// =========================================================================
// Screen 2 — Collection / Folder Detail (Backdrop + All/Folder Tabs + Grid/Rows)
// =========================================================================
function renderCollectionScreen(root, ctx, profile, col, _s, options) {
  const { escapeHtml, icon } = ctx.ui;
  if (!col) {
    root.innerHTML = `<div style="padding:70px 24px;font-size:12px;color:var(--nv-muted)">No collection selected.</div>`;
    return;
  }
  const folders = col.folders || [];
  const showAll = Boolean(col.showAllTab) && folders.length > 1;
  const activeIdx = vpState.folderIdx;
  const activeFolder = activeIdx >= 0 ? (folders[activeIdx] || folders[0]) : null;
  const heroBg = activeFolder?.heroBackdropUrl || col.backdropUrl || "";

  const header = document.createElement("header");
  header.className = "nv-col-header";
  if (heroBg) header.style.backgroundImage = safeBgCss(heroBg);

  const headTop = document.createElement("div");
  headTop.className = "nv-col-head-top";

  const headLeft = document.createElement("div");
  headLeft.className = "nv-col-head-left";

  const backBtn = document.createElement("button");
  backBtn.className = "nv-back-circle";
  backBtn.title = "Back to Home";
  backBtn.innerHTML = icon("arrowLeft", 15);
  backBtn.onclick = () => {
    vpState.screen = "home";
    ctx.render();
  };

  const titleEl = document.createElement("h2");
  titleEl.className = "nv-col-title";
  titleEl.textContent = col.title;

  headLeft.append(backBtn, titleEl);

  const srcPill = document.createElement("span");
  srcPill.className = "pill";
  srcPill.dataset.colSource = "1";
  srcPill.textContent = "Loading…";

  headTop.append(headLeft, srcPill);
  header.appendChild(headTop);

  if (folders.length) {
    const tabs = document.createElement("div");
    tabs.className = "nv-tab-strip";

    if (showAll) {
      const allBtn = document.createElement("button");
      allBtn.className = "nv-tab-pill" + (activeIdx === -1 ? " active" : "");
      allBtn.textContent = "All";
      allBtn.onclick = () => {
        vpState.folderIdx = -1;
        ctx.render();
      };
      tabs.appendChild(allBtn);
    }

    folders.forEach((f, idx) => {
      const btn = document.createElement("button");
      btn.className = "nv-tab-pill" + (idx === activeIdx ? " active" : "");
      btn.textContent = f.title;
      btn.onclick = () => {
        vpState.folderIdx = idx;
        if (options.onSelectCollection) options.onSelectCollection(vpState.colIdx, idx);
        ctx.render();
      };
      tabs.appendChild(btn);
    });
    header.appendChild(tabs);
  }
  root.appendChild(header);

  const body = document.createElement("div");
  body.className = "nv-col-body";
  const grid = document.createElement("div");
  grid.className = "nv-posters-grid";
  grid.innerHTML = `<div style="font-size:12px;color:var(--nv-muted);grid-column:1/-1">Loading ${escapeHtml(activeFolder?.title || col.title)}…</div>`;
  body.appendChild(grid);
  root.appendChild(body);

  const fetchTargetIdx = activeIdx < 0 ? 0 : activeIdx;
  loadFolderPreview(ctx, profile, col, fetchTargetIdx).then(data => {
    if (!root.isConnected) return;
    const src = data?.source || "sample";
    srcPill.className = "pill " + (src === "sample" ? "warn" : "ok");
    srcPill.textContent = src === "aiometadata" ? "Live AIOMetadata" : (src === "tmdb" ? "Live TMDB" : "Sample Preview");

    const items = data?.items || [];
    grid.innerHTML = "";
    if (!items.length) {
      grid.innerHTML = `<div style="font-size:12px;color:var(--nv-muted);grid-column:1/-1">No items returned for this folder.</div>`;
      return;
    }

    if (String(col.viewMode || "").toUpperCase() === "ROWS" || activeIdx === -1) {
      grid.className = "";
      const rowLabels = activeIdx === -1
        ? folders.slice(0, 3).map(f => f.title)
        : (activeFolder?.rows?.length ? activeFolder.rows.slice(0, 3) : [activeFolder?.title || col.title]);
      rowLabels.forEach((rTitle, rIdx) => {
        const rowSec = document.createElement("section");
        rowSec.style.marginBottom = "18px";
        rowSec.innerHTML = `<div class="nv-section-head"><h3 class="nv-section-title">${escapeHtml(rTitle)}</h3></div>`;
        const hstrip = document.createElement("div");
        hstrip.className = "nv-hstrip";
        const rotated = [...items.slice(rIdx * 2), ...items.slice(0, rIdx * 2)];
        rotated.forEach(item => {
          const card = buildPosterCard(ctx, item, true);
          hstrip.appendChild(card);
        });
        rowSec.appendChild(hstrip);
        grid.appendChild(rowSec);
      });
    } else {
      items.forEach(item => {
        grid.appendChild(buildPosterCard(ctx, item, false));
      });
    }
  }).catch(err => {
    if (!root.isConnected) return;
    grid.innerHTML = `<div style="font-size:12px;color:var(--danger);grid-column:1/-1">${escapeHtml(err.message)}</div>`;
  });
}

function buildPosterCard(ctx, item, fixedWidth) {
  const { escapeHtml } = ctx.ui;
  const card = document.createElement("div");
  card.className = "nv-poster-card";
  if (fixedWidth) card.style.flex = "0 0 var(--nv-card-w, 118px)";

  const wrap = document.createElement("div");
  wrap.className = "nv-poster-wrap";
  const fallbackHtml = `<div class="nv-poster-fallback">${escapeHtml(item.title)}</div>`;
  if (item.posterUrl) {
    const img = document.createElement("img");
    img.src = item.posterUrl;
    img.alt = item.title;
    img.loading = "lazy";
    attachImgFallback(img, fallbackHtml);
    wrap.appendChild(img);
  } else {
    wrap.innerHTML = fallbackHtml;
  }

  if (item.rating) {
    const chip = document.createElement("span");
    chip.className = "nv-rating-chip";
    chip.textContent = `★ ${item.rating}`;
    wrap.appendChild(chip);
  }

  card.appendChild(wrap);
  card.insertAdjacentHTML(
    "beforeend",
    `<div class="nv-poster-title">${escapeHtml(item.title)}</div>${item.year ? `<div class="nv-poster-year">${escapeHtml(item.year)}</div>` : ""}`
  );

  card.onclick = () => {
    vpState.selectedItem = item;
    vpState.screen = "details";
    ctx.render();
  };
  return card;
}

// =========================================================================
// Screen 3 — Details Page (Full-Bleed Fanart + Actions + 8-Source Ratings + Sections)
// =========================================================================
function renderDetailsScreen(root, ctx, _profile, _col, s, metaCfg, _st) {
  const { escapeHtml, icon } = ctx.ui;
  const item = vpState.selectedItem || DEFAULT_HERO_SLIDES[0];
  const bd = item.backdropUrl || item.posterUrl || "";
  const isSeries = item.type === "series";
  const blurOn = metaCfg.blurUnwatched || s.blurUnwatchedEpisodes;

  const hero = document.createElement("section");
  hero.className = "nv-detail-hero";
  if (bd) hero.style.backgroundImage = safeBgCss(bd);

  const backBtn = document.createElement("button");
  backBtn.className = "nv-back-circle nv-detail-back";
  backBtn.title = "Back";
  backBtn.innerHTML = icon("arrowLeft", 15);
  backBtn.onclick = () => {
    vpState.screen = "home";
    ctx.render();
  };
  hero.appendChild(backBtn);

  const mediaLbl = isSeries ? "Series" : "Movie";
  const genresTxt = (item.genres && item.genres.length ? item.genres : [isSeries ? "Drama" : "Sci-Fi"]).join(" • ");
  const runtimeLbl = item.runtime || (isSeries ? "52m" : "2h 18m");
  const certLbl = item.certification || (isSeries ? "TV-MA" : "PG-13");

  const ratingsSpec = [
    ["imdb", "IMDb", item.rating || "8.5", "#F5C518", "#000000"],
    ["tmdb", "TMDB", "84%", "#01D277", "#031b11"],
    ["tomatoes", "RT", "93%", "#FA320A", "#ffffff"],
    ["audience", "Pop", "91%", "#F59E0B", "#111111"],
    ["metacritic", "MC", "83", "#66CC33", "#071a04"],
    ["trakt", "Trakt", "88%", "#ED1C24", "#ffffff"],
    ["letterboxd", "LB", "4.3", "#00E054", "#041a0b"],
    ["mal", "MAL", "8.9", "#2E51A2", "#ffffff"],
  ];

  const ratingsHtml = metaCfg.enabled
    ? ratingsSpec
        .filter(([k]) => metaCfg.providers[k] !== false)
        .map(
          ([, lbl, val, bg, fg]) =>
            `<span class="nv-rate-chip"><span class="nv-rate-dot" style="background:${bg};color:${fg}">${lbl}</span>${escapeHtml(val)}</span>`
        )
        .join("")
    : `<span style="font-size:11px;color:var(--nv-muted)">8-Source MDBList Ratings disabled in Nuvio Meta</span>`;

  const inner = document.createElement("div");
  inner.className = "nv-detail-inner";
  inner.innerHTML = `
    ${item.logoUrl ? `<img class="nv-hero-logo" src="${escapeHtml(item.logoUrl)}" alt="${escapeHtml(item.title)}" data-detail-logo />` : `<h2 class="nv-hero-title" style="font-size:28px">${escapeHtml(item.title)}</h2>`}
    <div class="nv-hero-meta-line">
      <span>${escapeHtml(mediaLbl)}</span>
      <span class="nv-meta-sep">•</span>
      <span>${escapeHtml(genresTxt)}</span>
      <span class="nv-meta-sep">•</span>
      <span>${escapeHtml(runtimeLbl)}</span>
      ${item.year ? `<span class="nv-meta-sep">•</span><span>${escapeHtml(item.year)}</span>` : ""}
      <span class="nv-meta-sep">•</span>
      <span>${escapeHtml(certLbl)}</span>
    </div>
    <div class="nv-hero-overview" style="margin-bottom:13px">${escapeHtml(item.overview || "Enriched with TMDB credits, clearlogos, trailer previews, and 8-source ratings.")}</div>
    <div class="nv-detail-actions">
      <button class="nv-btn-white" data-open-streams>${icon("play", 13)} Play / Streams</button>
      <button class="nv-circle-action ${vpState.inLibrary ? "active" : ""}" data-toggle-lib title="Toggle Library">${icon("bookmark", 15)}</button>
      <button class="nv-circle-action ${vpState.watched ? "active" : ""}" data-toggle-watched title="Mark Watched">${icon("check", 15)}</button>
      <button class="nv-circle-action" data-open-player title="Watch Trailer / Player OSD">${icon("film", 15)}</button>
    </div>
    <div class="nv-ratings-bar">${ratingsHtml}</div>
  `;

  const dLogo = inner.querySelector("[data-detail-logo]");
  if (dLogo) attachImgFallback(dLogo, `<h2 class="nv-hero-title" style="font-size:28px">${escapeHtml(item.title)}</h2>`);

  inner.querySelector("[data-open-streams]").onclick = () => {
    vpState.screen = "streams";
    ctx.render();
  };
  inner.querySelector("[data-toggle-lib]").onclick = () => {
    vpState.inLibrary = !vpState.inLibrary;
    ctx.render();
  };
  inner.querySelector("[data-toggle-watched]").onclick = () => {
    vpState.watched = !vpState.watched;
    ctx.render();
  };
  inner.querySelector("[data-open-player]").onclick = () => {
    vpState.screen = "player";
    ctx.render();
  };

  hero.appendChild(inner);
  root.appendChild(hero);

  // Render real visual sections following configured metaScreen.sections order
  const sectionsWrap = document.createElement("div");
  sectionsWrap.className = "nv-detail-sections";

  const orderedKeys = metaCfg.enabled
    ? metaCfg.sections.filter(sec => sec.enabled).map(sec => sec.key)
    : ["EPISODES", "CAST", "MORE_LIKE_THIS"];

  const rendered = new Set();
  const renderEpisodes = () => {
    if (rendered.has("EPISODES")) return;
    rendered.add("EPISODES");
    const epSec = document.createElement("section");
    epSec.innerHTML = `
      <div class="nv-section-head">
        <h3 class="nv-section-title">Episodes · Season 1</h3>
        <span style="font-size:11px;color:var(--nv-muted)">${blurOn ? "Spoiler Blur Active" : "Thumbnails Visible"}</span>
      </div>
    `;
    const rail = document.createElement("div");
    rail.className = "nv-ep-rail";
    [
      { code: "E1", title: "Good News About Hell", meta: "57m · ★ 8.4", watched: true },
      { code: "E2", title: "Half Loop", meta: "55m · ★ 8.6", watched: false },
      { code: "E3", title: "In Perpetuity", meta: "56m · ★ 8.8", watched: false },
    ].forEach(ep => {
      const card = document.createElement("div");
      card.className = "nv-ep-card";
      const isBlur = !ep.watched && blurOn;
      card.innerHTML = `
        <div class="nv-ep-thumb ${isBlur ? "blurred" : ""}">
          <div class="nv-ep-thumb-bg" ${bd ? `style="background-image:${safeBgCss(bd)}"` : ""}></div>
          ${isBlur ? `<div class="nv-ep-spoiler-pill">Unwatched · Spoiler Hidden</div>` : ""}
          <span class="nv-ep-badge">${ep.code}</span>
        </div>
        <div class="nv-ep-title">${escapeHtml(ep.title)}</div>
        <div class="nv-ep-sub">${escapeHtml(ep.meta)}</div>
      `;
      card.onclick = () => {
        vpState.screen = "streams";
        ctx.render();
      };
      rail.appendChild(card);
    });
    epSec.appendChild(rail);
    sectionsWrap.appendChild(epSec);
  };

  const renderCast = () => {
    if (rendered.has("CAST")) return;
    rendered.add("CAST");
    const cSec = document.createElement("section");
    cSec.innerHTML = `<div class="nv-section-head"><h3 class="nv-section-title">Cast &amp; Crew</h3></div>`;
    const rail = document.createElement("div");
    rail.className = "nv-cast-rail";
    CAST_SAMPLES.forEach(c => {
      const card = document.createElement("div");
      card.className = "nv-cast-card";
      card.innerHTML = `
        <div class="nv-cast-avatar" style="background:${c.bg}">${escapeHtml(c.init)}</div>
        <div class="nv-cast-name">${escapeHtml(c.name)}</div>
        <div class="nv-cast-role">${escapeHtml(c.role)}</div>
      `;
      rail.appendChild(card);
    });
    cSec.appendChild(rail);
    sectionsWrap.appendChild(cSec);
  };

  const renderMoreLikeThis = () => {
    if (rendered.has("MORE_LIKE_THIS")) return;
    rendered.add("MORE_LIKE_THIS");
    const mSec = document.createElement("section");
    mSec.innerHTML = `<div class="nv-section-head"><h3 class="nv-section-title">More Like This</h3></div>`;
    const rail = document.createElement("div");
    rail.className = "nv-hstrip";
    DEFAULT_HERO_SLIDES.concat(CONTINUE_WATCHING_SAMPLES.map(c => c.item)).forEach(rec => {
      if (rec.id !== item.id) rail.appendChild(buildPosterCard(ctx, rec, true));
    });
    mSec.appendChild(rail);
    sectionsWrap.appendChild(mSec);
  };

  orderedKeys.forEach(k => {
    if (k === "EPISODES" && (isSeries || blurOn)) renderEpisodes();
    else if (k === "CAST") renderCast();
    else if (k === "MORE_LIKE_THIS") renderMoreLikeThis();
  });
  if ((isSeries || blurOn) && !rendered.has("EPISODES")) renderEpisodes();

  root.appendChild(sectionsWrap);
}

// =========================================================================
// Screen 4 — Dedicated Streams Picker (Desktop/TV Split Pane + Elite-Badges)
// =========================================================================
function renderStreamsScreen(root, ctx, _profile, _s, badgeCfg, st) {
  const { escapeHtml, icon } = ctx.ui;
  const item = vpState.selectedItem || DEFAULT_HERO_SLIDES[0];
  const bd = item.backdropUrl || item.posterUrl || "";
  const debridLabel = (st.debridProviders || []).find(d => d.id === st.debridProvider)?.label || st.debridProvider || "TorBox";
  const fmtPreset = st.streamFormatter?.preset || "prism";

  const wrap = document.createElement("div");
  wrap.className = "nv-streams-root";

  const bgLayer = document.createElement("div");
  bgLayer.className = "nv-streams-bg";
  if (bd) bgLayer.style.backgroundImage = safeBgCss(bd);
  const scrim = document.createElement("div");
  scrim.className = "nv-streams-scrim";
  wrap.append(bgLayer, scrim);

  const split = document.createElement("div");
  split.className = "nv-streams-split";

  // Left Info Panel (TabletStreamsLayout / Desktop Streams)
  const infoPanel = document.createElement("div");
  infoPanel.className = "nv-streams-info";

  const backRow = document.createElement("div");
  backRow.style.cssText = "display:flex;align-items:center;gap:10px";
  const backBtn = document.createElement("button");
  backBtn.className = "nv-back-circle";
  backBtn.title = "Back to Details";
  backBtn.innerHTML = icon("arrowLeft", 15);
  backBtn.onclick = () => {
    vpState.screen = "details";
    ctx.render();
  };
  const bLabel = document.createElement("span");
  bLabel.style.cssText = "font-size:12px;font-weight:600;color:var(--nv-muted)";
  bLabel.textContent = "Select Stream";
  backRow.append(backBtn, bLabel);
  infoPanel.appendChild(backRow);

  const posterRow = document.createElement("div");
  posterRow.className = "nv-streams-poster-row";
  if (item.posterUrl) {
    const pImg = document.createElement("img");
    pImg.className = "nv-streams-poster";
    pImg.src = item.posterUrl;
    pImg.alt = item.title;
    attachImgFallback(pImg, `<div class="nv-streams-poster nv-poster-fallback">${escapeHtml(item.title)}</div>`);
    posterRow.appendChild(pImg);
  }
  const metaDiv = document.createElement("div");
  metaDiv.style.minWidth = "0";
  metaDiv.innerHTML = `
    <div style="font-size:17px;font-weight:800;line-height:1.15;color:#fff">${escapeHtml(item.title)}</div>
    <div style="font-size:11px;color:var(--nv-muted);margin-top:4px">${escapeHtml(item.year || "2024")} · ${escapeHtml(item.runtime || "2h 18m")} · ★ ${escapeHtml(item.rating || "8.5")}</div>
  `;
  posterRow.appendChild(metaDiv);
  infoPanel.appendChild(posterRow);

  if (item.overview) {
    const ov = document.createElement("div");
    ov.style.cssText = "font-size:11.5px;line-height:1.45;color:var(--nv-text-soft);display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden";
    ov.textContent = item.overview;
    infoPanel.appendChild(ov);
  }

  const statusBlock = document.createElement("div");
  statusBlock.className = "nv-scraper-status";
  statusBlock.innerHTML = `
    <span style="display:inline-flex;align-items:center;gap:7px"><span class="nv-scraper-dot"></span><strong>AIOStreams</strong> · ${escapeHtml(debridLabel)}</span>
    <span style="font-size:10px;color:var(--nv-muted)">${escapeHtml(fmtPreset)} · ${escapeHtml(badgeCfg.placement)}</span>
  `;
  infoPanel.appendChild(statusBlock);

  // Right Streams List Panel
  const rightPanel = document.createElement("div");

  const filterBar = document.createElement("div");
  filterBar.className = "nv-stream-filters";
  [
    ["all", "All Streams"],
    ["4k", "4K UHD"],
    ["hdr", "Dolby Vision / HDR"],
    ["cached", `${debridLabel} Cached`],
  ].forEach(([fid, lbl]) => {
    const b = document.createElement("button");
    b.className = "nv-tab-pill" + (vpState.streamFilter === fid ? " active" : "");
    b.textContent = lbl;
    b.onclick = () => {
      vpState.streamFilter = fid;
      ctx.render();
    };
    filterBar.appendChild(b);
  });
  rightPanel.appendChild(filterBar);

  const filtered = STREAM_SAMPLES.filter(sm => {
    if (vpState.streamFilter === "4k") return sm.quality === "2160p";
    if (vpState.streamFilter === "hdr") return sm.hdr !== "SDR";
    if (vpState.streamFilter === "cached") return sm.cached;
    return true;
  });

  const list = document.createElement("div");
  list.className = "nv-stream-list";
  filtered.forEach((sample, idx) => {
    const formatted = formatSampleStream(sample, debridLabel, fmtPreset);
    const badgesHtml = renderBadgesStrip(ctx, badgeCfg, sample, debridLabel);
    const card = document.createElement("div");
    card.className = "nv-stream-card";
    const bodyHtml = `<div class="nv-stream-main"><div class="nv-stream-left">${escapeHtml(formatted.left)}</div><div class="nv-stream-desc">${escapeHtml(formatted.desc)}</div></div>`;
    card.innerHTML = badgeCfg.placement === "TOP" ? `${badgesHtml}${bodyHtml}` : `${bodyHtml}${badgesHtml}`;
    card.onclick = () => {
      vpState.selectedStreamIdx = idx;
      vpState.screen = "player";
      ctx.render();
    };
    list.appendChild(card);
  });
  rightPanel.appendChild(list);

  split.append(infoPanel, rightPanel);
  wrap.appendChild(split);
  root.appendChild(wrap);
}

// =========================================================================
// Screen 5 — Player OSD (FoldControls + Live Scaled Subtitles + Skip Intro)
// =========================================================================
function renderPlayerScreen(root, ctx, _profile, s, badgeCfg, st) {
  const { escapeHtml, icon } = ctx.ui;
  const item = vpState.selectedItem || DEFAULT_HERO_SLIDES[0];
  const sample = STREAM_SAMPLES[vpState.selectedStreamIdx] || STREAM_SAMPLES[0];
  const debridLabel = (st.debridProviders || []).find(d => d.id === st.debridProvider)?.label || st.debridProvider || "TorBox";
  const subPx = vpState.device === "tv" ? Math.round(18 * (s.tvPercent / 100)) : Math.round(s.sizeSp * 0.96);

  const stage = document.createElement("div");
  stage.className = "nv-player-stage";
  const bd = item.backdropUrl || item.posterUrl || "";
  if (bd) stage.style.backgroundImage = safeBgCss(bd);

  // Top OSD Bar
  const top = document.createElement("div");
  top.className = "nv-player-top";

  const titleWrap = document.createElement("div");
  titleWrap.className = "nv-player-title-wrap";
  const backBtn = document.createElement("button");
  backBtn.className = "nv-back-circle";
  backBtn.title = "Back to Streams";
  backBtn.innerHTML = icon("arrowLeft", 15);
  backBtn.onclick = () => {
    vpState.screen = "streams";
    ctx.render();
  };
  const titleText = document.createElement("div");
  titleText.innerHTML = `
    <div class="nv-player-title">${escapeHtml(item.title)}</div>
    <div class="nv-player-subtitle">${escapeHtml(sample.quality)} · ${escapeHtml(sample.hdr)} · ${escapeHtml(sample.audio)} · ${escapeHtml(debridLabel)}</div>
  `;
  titleWrap.append(backBtn, titleText);

  const tiles = document.createElement("div");
  tiles.className = "nv-player-tiles";
  [
    ["volume", "Audio"],
    ["subtitles", `${s.subtitlePreferred.toUpperCase()}${s.subtitleSecondary ? `+${s.subtitleSecondary.toUpperCase()}` : ""}`],
    ["zap", "Source"],
    ["aspectRatio", "Fit"],
  ].forEach(([ic, lbl]) => {
    const t = document.createElement("button");
    t.className = "nv-glass-tile";
    t.innerHTML = `${icon(ic, 12)}<span>${escapeHtml(lbl)}</span>`;
    if (ic === "zap") {
      t.onclick = () => {
        vpState.screen = "streams";
        ctx.render();
      };
    }
    tiles.appendChild(t);
  });

  top.append(titleWrap, tiles);

  // Center Transport + Subtitle Stage + Skip Intro
  const center = document.createElement("div");
  center.className = "nv-player-center";

  const transport = document.createElement("div");
  transport.className = "nv-transport";
  transport.innerHTML = `
    <button class="nv-transport-btn" title="Seek back 10s">${icon("rewind", 17)}</button>
    <button class="nv-transport-btn primary" title="Play / Pause">${icon("pause", 22)}</button>
    <button class="nv-transport-btn" title="Seek forward 10s">${icon("fastForward", 17)}</button>
  `;
  center.appendChild(transport);

  const lowerMid = document.createElement("div");
  lowerMid.className = "nv-player-lower-mid";

  if (s.skipIntro) {
    const skipRow = document.createElement("div");
    skipRow.className = "nv-skip-row";
    skipRow.innerHTML = `<div class="nv-skip-btn">${icon("fastForward", 11)} Skip Intro</div>`;
    lowerMid.appendChild(skipRow);
  }

  const sub = document.createElement("div");
  sub.className = "nv-sub-box";
  sub.style.fontSize = `${subPx}px`;
  sub.innerHTML =
    `All those moments will be lost in time, like tears in rain.` +
    (s.subtitleSecondary
      ? `<span class="nv-sub-sec">[${escapeHtml(s.subtitleSecondary.toUpperCase())}] 二言語字幕プレビュー · Dual Subtitles</span>`
      : "");
  lowerMid.appendChild(sub);
  center.appendChild(lowerMid);

  // Bottom Scrubber + Player Badges
  const bottom = document.createElement("div");
  bottom.className = "nv-player-bottom";
  if (badgeCfg.enabled && badgeCfg.showInPlayer) {
    bottom.insertAdjacentHTML("beforeend", renderBadgesStrip(ctx, badgeCfg, sample, debridLabel));
  }
  bottom.insertAdjacentHTML(
    "beforeend",
    `
      <div class="nv-timeline-row">
        <span>42:18</span>
        <div class="nv-seek-track"><div class="nv-seek-fill"><div class="nv-seek-thumb"></div></div></div>
        <span>-1:12:42</span>
      </div>
    `
  );

  stage.append(top, center, bottom);
  root.appendChild(stage);
}
