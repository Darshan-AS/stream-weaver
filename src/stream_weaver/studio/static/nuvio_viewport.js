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
    scraper: "Torrentio",
    source: "TorBox",
    quality: "2160p",
    remux: true,
    cached: true,
    hdr: "DV HDR10+",
    hdrFull: "Dolby Vision HDR10+",
    codec: "HEVC 10bit",
    encode: "HEVC",
    audio: "TrueHD 7.1 Atmos",
    audioShort: "Atmos 7.1",
    size: "42.8 GB",
    bitrate: "58.4 Mbps",
    duration: "2h 46m",
    qualityType: "BluRay REMUX",
    releaseGroup: "FraMeSToR",
    languages: "English · Japanese",
    subs: "EN · JA",
    filename: "Dune.Part.Two.2024.2160p.UHD.BluRay.REMUX.DV.HDR10+.TrueHD.7.1.Atmos-FraMeSToR.mkv",
    origin: "BluRay REMUX · FraMeSToR",
    scoreStars: "◆◆◆◆◇",
  },
  {
    addon: "AIOStreams",
    scraper: "MediaFusion",
    source: "TorBox",
    quality: "2160p",
    remux: false,
    cached: true,
    hdr: "DV",
    hdrFull: "Dolby Vision",
    codec: "HEVC 10bit",
    encode: "HEVC",
    audio: "DD+ 5.1 Atmos",
    audioShort: "Atmos 5.1",
    size: "18.7 GB",
    bitrate: "16.2 Mbps",
    duration: "2h 46m",
    qualityType: "WEB-DL",
    releaseGroup: "FLUX",
    languages: "English",
    subs: "EN · ES",
    filename: "Dune.Part.Two.2024.2160p.NF.WEB-DL.DDP5.1.Atmos.DV.H.265-FLUX.mkv",
    origin: "NF WEB-DL · FLUX",
    scoreStars: "◆◆◆◆◇",
  },
  {
    addon: "AIOStreams",
    scraper: "Comet",
    source: "TorBox",
    quality: "1080p",
    remux: false,
    cached: true,
    hdr: "SDR",
    hdrFull: "SDR",
    codec: "AVC",
    encode: "AVC",
    audio: "DD+ 5.1",
    audioShort: "DD+ 5.1",
    size: "6.4 GB",
    bitrate: "5.4 Mbps",
    duration: "2h 46m",
    qualityType: "WEB-DL",
    releaseGroup: "NTb",
    languages: "English",
    subs: "EN",
    filename: "Dune.Part.Two.2024.1080p.ATVP.WEB-DL.DDP5.1.H.264-NTb.mkv",
    origin: "ATVP WEB-DL · NTb",
    scoreStars: "◆◆◆◇◇",
  },
];

const CONTINUE_WATCHING_SAMPLES = [
  {
    title: "Spirited Away",
    epCode: "",
    epName: "",
    pill: "12% watched",
    prog: 12,
    bg: "https://image.tmdb.org/t/p/w1280/bSXfU4dwZyBA1vMmXvejdRXBvuF.jpg",
    item: {
      id: "tt0245429",
      title: "Spirited Away",
      year: "2001",
      runtime: "2h 5m",
      certification: "PG",
      language: "JA",
      rating: "8.6",
      type: "movie",
      posterUrl: "https://image.tmdb.org/t/p/w500/39wmItIWsg5sZMyRUHLkWBcuVCM.jpg",
      backdropUrl: "https://image.tmdb.org/t/p/w1280/bSXfU4dwZyBA1vMmXvejdRXBvuF.jpg",
      overview: "During her family's move to the suburbs, a sullen 10-year-old girl wanders into a world ruled by gods, witches, and spirits.",
      genres: ["Animation", "Fantasy"],
    },
  },
  {
    title: "Shogun",
    epCode: "S1 E3",
    epName: "Tomorrow Is Tomorrow",
    pill: "Next Up",
    prog: 0,
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
    pill: "18% watched",
    prog: 18,
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
  {
    title: "Dune: Part Two",
    epCode: "",
    epName: "",
    pill: "64% watched",
    prog: 64,
    bg: "https://image.tmdb.org/t/p/w1280/xOMo8BRK7PfcJv9JCnx7s5hj0PX.jpg",
    item: {
      id: "tt15239678",
      title: "Dune: Part Two",
      year: "2024",
      runtime: "2h 46m",
      certification: "PG-13",
      language: "EN",
      rating: "8.5",
      type: "movie",
      posterUrl: "https://image.tmdb.org/t/p/w500/1pdfLvkbY9ohJlCjQH2CZjjYVvJ.jpg",
      backdropUrl: "https://image.tmdb.org/t/p/w1280/xOMo8BRK7PfcJv9JCnx7s5hj0PX.jpg",
      overview: "Paul Atreides unites with Chani and the Fremen while on a warpath of revenge against the conspirators who destroyed his family.",
      genres: ["Sci-Fi", "Adventure"],
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

const CAST_BY_ID = {
  tt15239678: [
    { name: "Timothée Chalamet", role: "Paul Atreides", init: "TC", bg: "#1e3a5f" },
    { name: "Zendaya", role: "Chani", init: "Z", bg: "#4c1d45" },
    { name: "Rebecca Ferguson", role: "Lady Jessica", init: "RF", bg: "#1f3d2f" },
    { name: "Javier Bardem", role: "Stilgar", init: "JB", bg: "#4a2c14" },
    { name: "Austin Butler", role: "Feyd-Rautha", init: "AB", bg: "#2d2a4a" },
  ],
  tt9362722: [
    { name: "Shameik Moore", role: "Miles Morales", init: "SM", bg: "#4c1d24" },
    { name: "Hailee Steinfeld", role: "Gwen Stacy", init: "HS", bg: "#3b1d4a" },
    { name: "Oscar Isaac", role: "Miguel O'Hara", init: "OI", bg: "#1e2e5c" },
    { name: "Jake Johnson", role: "Peter B. Parker", init: "JJ", bg: "#1f3d32" },
    { name: "Daniel Kaluuya", role: "Hobie Brown", init: "DK", bg: "#4a3014" },
  ],
  tt15398776: [
    { name: "Cillian Murphy", role: "J. Robert Oppenheimer", init: "CM", bg: "#3a2518" },
    { name: "Emily Blunt", role: "Kitty Oppenheimer", init: "EB", bg: "#2b2d42" },
    { name: "Matt Damon", role: "Leslie Groves", init: "MD", bg: "#1e3a3a" },
    { name: "Robert Downey Jr.", role: "Lewis Strauss", init: "RD", bg: "#3e2230" },
  ],
  tt2788316: [
    { name: "Hiroyuki Sanada", role: "Yoshii Toranaga", init: "HS", bg: "#402618" },
    { name: "Cosmo Jarvis", role: "John Blackthorne", init: "CJ", bg: "#1e334a" },
    { name: "Anna Sawai", role: "Toda Mariko", init: "AS", bg: "#422133" },
    { name: "Tadanobu Asano", role: "Kashigi Yabushige", init: "TA", bg: "#243b2a" },
  ],
  tt11126994: [
    { name: "Hailee Steinfeld", role: "Vi", init: "HS", bg: "#4c1d34" },
    { name: "Ella Purnell", role: "Jinx", init: "EP", bg: "#1c2d5a" },
    { name: "Kevin Alejandro", role: "Jayce Talis", init: "KA", bg: "#3f3016" },
    { name: "Katie Leung", role: "Caitlyn Kiramman", init: "KL", bg: "#1e3545" },
  ],
  tt0245429: [
    { name: "Rumi Hiiragi", role: "Chihiro Ogino", init: "RH", bg: "#3d2240" },
    { name: "Miyu Irino", role: "Haku", init: "MI", bg: "#193a3c" },
    { name: "Mari Natsuki", role: "Yubaba", init: "MN", bg: "#4a2420" },
    { name: "Bunta Sugawara", role: "Kamajii", init: "BS", bg: "#2b3322" },
  ],
};

function getCastForItem(item) {
  if (item && CAST_BY_ID[item.id]) return CAST_BY_ID[item.id];
  const t = String(item?.title || "Feature");
  return [
    { name: "Lead Performer", role: `${t} Cast`, init: "LP", bg: "#1e3a5f" },
    { name: "Co-Star", role: "Principal Role", init: "CS", bg: "#3d2245" },
    { name: "Supporting Cast", role: "Ensemble", init: "SC", bg: "#1f3d2f" },
    { name: "Director", role: "TMDB Credits", init: "DR", bg: "#4a2c14" },
  ];
}

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

function debridShortCode(debridIdOrLabel) {
  const s = String(debridIdOrLabel || "").toLowerCase();
  if (s.includes("torbox") || s === "tb") return "TB";
  if (s.includes("real") || s === "rd") return "RD";
  if (s.includes("alldebrid") || s === "ad") return "AD";
  if (s.includes("premiumize") || s === "pm") return "PM";
  if (s.includes("easynews") || s === "en") return "EN";
  if (s.includes("debrid-link") || s === "dl") return "DL";
  if (s.includes("offcloud") || s === "oc") return "OC";
  return "TB";
}

function matchStreamBadgeIds(sample, debridLabel) {
  const ids = [];
  if (sample.quality === "2160p") ids.push("4k-ultra-hd");
  if (sample.quality === "1080p") ids.push("1080p-full-hd");
  if (sample.remux) ids.push("remux");
  else if (sample.origin.includes("WEB-DL")) ids.push("webdl");
  const hdrTxt = sample.hdrFull || sample.hdr || "";
  if (hdrTxt.includes("Dolby Vision") || hdrTxt.includes("DV")) ids.push("dolby-vision");
  if (hdrTxt.includes("HDR10+")) ids.push("hdr10-plus");
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

export function hasZeroWidthBadgeEncoding(fmtState) {
  const nameDef = String(fmtState?.currentDefinition?.name || "");
  return /[\u200b\u200c\u200d\u2060\u2061\u2062\u2063\u2064]/.test(nameDef);
}

export function formatSampleStream(sample, debridLabel, fmtPreset, fmtState = null) {
  const preset = fmtPreset || fmtState?.preset || fmtState?.active || "prism";
  const shortCode = debridShortCode(debridLabel || sample.source);
  const scraper = sample.scraper || "Torrentio";
  const hdrPart = sample.hdr && sample.hdr !== "SDR" ? ` ${sample.hdr}` : "";
  const cachedIcon = sample.cached !== false ? "⚡ " : "⏳ ";

  if (preset === "custom") {
    const isZeroWidth = hasZeroWidthBadgeEncoding(fmtState);
    if (isZeroWidth && sample === STREAM_SAMPLES[0] && fmtState?.sampleName && fmtState?.sampleDescription) {
      return {
        name: fmtState.sampleName,
        desc: fmtState.sampleDescription,
        addon: sample.addon || "AIOStreams",
      };
    }
    return {
      name: `${cachedIcon}${shortCode !== "TB" ? `[${shortCode}] ` : ""}${scraper} · ${sample.scoreStars || "◆◆◆◆◇"}`,
      desc:
        `◧ ${sample.size} · ⟡ ${sample.bitrate || "24.0 Mbps"} · ◷ ${sample.duration || "2h 18m"}\n` +
        `⌗ ${sample.releaseGroup || "FLUX"} · ◈ ${sample.qualityType || "WEB-DL"}\n` +
        `♫ ${sample.languages || "English"}\n` +
        `▤ ${sample.filename || sample.origin}`,
      addon: sample.addon || "AIOStreams",
    };
  }
  if (preset === "minimal") {
    return {
      name: `${cachedIcon}${sample.quality}${sample.hdr && sample.hdr !== "SDR" ? ` ${sample.hdr.split(" ")[0]}` : ""}`,
      desc: `${sample.size} · ${sample.qualityType || sample.origin} · ${scraper}`,
      addon: sample.addon || "AIOStreams",
    };
  }
  if (preset === "compact_tv") {
    return {
      name: `${cachedIcon}[${shortCode}] ${sample.quality}${sample.hdr && sample.hdr !== "SDR" ? ` · ${sample.hdr}` : ""}`,
      desc:
        `${sample.size} · ${sample.qualityType || "WEB-DL"} · ${sample.encode || sample.codec} ${sample.audioShort || sample.audio}\n` +
        `${scraper} · ${(sample.languages || "English").replace(/ · /g, "/")} · ${sample.releaseGroup || "FLUX"}`,
      addon: sample.addon || "AIOStreams",
    };
  }
  return {
    name: `${cachedIcon}[${shortCode}] ${scraper} · ${sample.quality}${hdrPart} · ${sample.encode || sample.codec} ${sample.audioShort || sample.audio}`,
    desc:
      `◧ ${sample.size} · ⟡ ${sample.bitrate || "24.0 Mbps"} · ◷ ${sample.duration || "2h 18m"}\n` +
      `◈ ${sample.qualityType || "WEB-DL"} · ⌗ ${sample.releaseGroup || "FLUX"}\n` +
      `♫ ${sample.languages || "English"}${sample.subs ? ` · ☰ ${sample.subs}` : ""}\n` +
      `▤ ${sample.filename || sample.origin}`,
    addon: sample.addon || "AIOStreams",
  };
}

export function renderNuvioStreamCard(ctx, options = {}) {
  const { escapeHtml } = ctx.ui;
  const st = ctx.state;
  const profile = options.profile || ctx.currentProfile();
  const sample = options.sample || STREAM_SAMPLES[0];
  const debridLabel =
    options.debridLabel ||
    (st.debridServices?.available || []).find(d => d.enabled)?.label ||
    (st.debridProviders || []).find(d => d.id === st.debridProvider)?.label ||
    st.debridProvider ||
    "TorBox";
  const fmtState = st.streamFormatter || {};
  const fmtPreset = options.fmtPreset || fmtState.preset || fmtState.active || "prism";
  const badgeCfg = options.badgeCfg || effectiveBadges(profile);
  const showBadges = options.showBadges !== undefined ? Boolean(options.showBadges) : badgeCfg.enabled;
  const placement = options.placement || badgeCfg.placement || "BOTTOM";
  const clientMode = options.clientMode || "nuvio";

  const formatted = formatSampleStream(sample, debridLabel, fmtPreset, fmtState);

  if (clientMode === "stremio") {
    const el = document.createElement("div");
    el.className = "nv-stremio-card";
    el.innerHTML = `
      <div class="nv-stremio-name">${escapeHtml(formatted.name)}</div>
      <div class="nv-stremio-desc">${escapeHtml(formatted.desc)}</div>
    `;
    return el;
  }

  const badgesHtml = showBadges
    ? renderBadgesStrip(ctx, { ...badgeCfg, enabled: true }, sample, debridLabel)
    : "";

  const descHtml = String(formatted.desc || "")
    .split("\n")
    .filter(Boolean)
    .map(line => {
      const cls = line.startsWith("▤") ? "nv-stream-desc-line file-line" : "nv-stream-desc-line";
      return `<span class="${cls}">${escapeHtml(line)}</span>`;
    })
    .join("");

  const card = document.createElement("div");
  card.className = "nv-stream-card";
  const textHtml = `
    <div class="nv-stream-name">${escapeHtml(formatted.name)}</div>
    <div class="nv-stream-desc">${descHtml}</div>
  `;
  card.innerHTML = `
    <div class="nv-stream-body">
      ${placement === "TOP" ? `${badgesHtml}${textHtml}` : `${textHtml}${badgesHtml}`}
    </div>
    <div class="nv-stream-trailing">
      <span class="nv-addon-chip">${escapeHtml(formatted.addon || "AIOStreams")}</span>
    </div>
  `;
  return card;
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

const POSTER_RATING_META = {
  imdb: { short: "IMDb", color: "#F5C518", sample: sm => sm.rating || "8.5" },
  tomatoes: { short: "RT", color: "#FA320A", sample: () => "93%" },
  letterboxd: { short: "LB", color: "#00E054", sample: () => "4.3" },
  trakt: { short: "Trakt", color: "#ED1C24", sample: () => "88%" },
  myanimelist: { short: "MAL", color: "#2E51A2", sample: () => "8.9" },
  anilist: { short: "AL", color: "#02A9FF", sample: () => "87%" },
  tmdb: { short: "TMDB", color: "#01D277", sample: () => "84%" },
  metacritic: { short: "MC", color: "#66CC33", sample: () => "83" },
};

export function resolveCustomizedPosterUrl(item, metadataPower, shape = "poster") {
  const rawUrl = item?.posterUrl || "";
  if (!metadataPower || metadataPower.posterProvider !== "postersplus") return rawUrl;
  const pattern = String(metadataPower.customPosterUrlPattern || "").trim();
  if (!pattern) return rawUrl;
  const itemId = String(item?.id || "");
  const tmdbId = String(item?.tmdbId || (itemId.startsWith("tmdb:") ? itemId.slice(5) : ""));
  const imdbId = itemId.startsWith("tt") ? itemId : "";
  const mediaType = item?.type === "series" ? "series" : "movie";
  let url = pattern
    .replaceAll("{tmdb_id?}", encodeURIComponent(tmdbId))
    .replaceAll("{imdb_id?}", encodeURIComponent(imdbId))
    .replaceAll("{id}", encodeURIComponent(itemId || tmdbId))
    .replaceAll("{type}", encodeURIComponent(mediaType))
    .replaceAll("{shape}", encodeURIComponent(shape));
  url = url.replace(/([?&])(tmdb_id|imdb_id)=&/g, "$1").replace(/([?&])(tmdb_id|imdb_id)=$/, "");
  return url;
}

export function createPosterOverlayElement(item, metadataPower) {
  const prov = metadataPower?.posterProvider || "none";
  if (prov === "none") return null;

  const pc = metadataPower?.posterCustomizations || {};
  const statusMode = pc.statusMode || "notch";
  const maxBadges = Number(pc.maxBadges || 3);
  const badgeStyle = pc.badgeStyle || "frosted";
  const awardsMask = Number(pc.awardsMask ?? 7);
  const tokens = Array.isArray(pc.ratings) && pc.ratings.length
    ? pc.ratings
    : ["imdb", "tomatoes:mt", "letterboxd:m", "trakt:t"];

  const overlay = document.createElement("div");
  overlay.className = "nv-poster-overlay";

  // Top status banner (Upcoming / Airing / Next Watching)
  let statusHtml = "";
  if (prov === "postersplus" && statusMode !== "off" && statusMode !== "none") {
    const isSeries = item?.type === "series";
    let statusTxt = item?.statusLabel || "";
    let tone = "";
    if (!statusTxt) {
      if (isSeries && pc.tvStatusOverlay !== false) {
        statusTxt = "NEW EP";
        tone = "tone-upcoming";
      } else if (!isSeries && pc.showWatchingStatus !== false) {
        statusTxt = "RELEASED";
        tone = "tone-watching";
      }
    }
    if (statusTxt) {
      statusHtml = `<div class="nv-poster-status mode-${statusMode} ${tone}">${statusTxt}</div>`;
    }
  }

  // Awards & Accolades (Oscars / Emmys / IMDb Top 250)
  let awardHtml = "";
  if (prov === "postersplus" && awardsMask > 0) {
    const isSeries = item?.type === "series";
    let awardTxt = item?.awardLabel || "";
    if (!awardTxt) {
      if (isSeries && (awardsMask & 2)) awardTxt = "🏆 Emmy Winner";
      else if (!isSeries && (awardsMask & 1)) awardTxt = "🏆 Oscar Nom";
      else if (awardsMask & 4) awardTxt = "★ Top Rated";
    }
    if (awardTxt) {
      awardHtml = `<div class="nv-poster-mid-badges"><span class="nv-poster-award">${awardTxt}</span></div>`;
    }
  }

  // Bottom rating pills filtered by type (m=movie, t=series, a=anime)
  const isAnime =
    (item?.genres || []).some(g => /anime|animation/i.test(String(g))) ||
    String(item?.language || "").toUpperCase() === "JA";
  const isSeries = item?.type === "series";
  const picked = [];
  if (prov === "rpdb") {
    picked.push(
      { short: "IMDb", color: "#F5C518", val: item?.rating || "8.5" },
      { short: "RT", color: "#FA320A", val: "93%" }
    );
  } else if (prov === "topposters") {
    picked.push(
      { short: "IMDb", color: "#F5C518", val: item?.rating || "8.5" },
      { short: "TMDB", color: "#01D277", val: "84%" },
      { short: "RT", color: "#FA320A", val: "93%" }
    );
  } else {
    tokens.forEach(tok => {
      if (picked.length >= maxBadges) return;
      const [id, scope] = String(tok).split(":");
      if (scope) {
        const allowMovie = scope.includes("m") && !isSeries;
        const allowTv = scope.includes("t") && isSeries;
        const allowAnime = scope.includes("a") && isAnime;
        if (!allowMovie && !allowTv && !allowAnime) return;
      }
      const meta = POSTER_RATING_META[id];
      if (!meta) return;
      picked.push({ short: meta.short, color: meta.color, val: meta.sample(item || {}) });
    });
  }

  const ratingsHtml = picked.length
    ? `<div class="nv-poster-ratings">${picked
        .map(
          r =>
            `<span class="nv-pr-pill style-${badgeStyle}"><span style="color:${r.color}">${r.short}</span> ${r.val}</span>`
        )
        .join("")}</div>`
    : "";

  overlay.innerHTML = `${statusHtml}${awardHtml}${ratingsHtml}`;
  return overlay;
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

  // Dedicated Content Canvas (guarantees floating nav pill stays mathematically centered)
  const canvasArea = document.createElement("div");
  canvasArea.className = "nv-canvas-area";

  if (vpState.device === "mobile") {
    const notch = document.createElement("div");
    notch.className = "nv-mobile-notch";
    const status = document.createElement("div");
    status.className = "nv-mobile-statusbar";
    status.innerHTML = `<span>9:41</span><span>5G · 98%</span>`;
    canvasArea.append(notch, status);
  }

  // 2) Desktop Top Floating Glass Pill (shown cleanly on top-level Home screen, matching real Nuvio)
  if (vpState.screen === "home" && vpState.device === "desktop" && s.desktopNavigation !== "sidebar") {
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
    canvasArea.appendChild(floatNav);

    const cornerBtn = document.createElement("button");
    cornerBtn.className = "nv-corner-action";
    cornerBtn.title = vpState.theater ? "Collapse Theater View" : "Expand Theater View";
    cornerBtn.innerHTML = icon(vpState.theater ? "minimize" : "maximize", 14);
    cornerBtn.onclick = () => {
      vpState.theater = !vpState.theater;
      if (options.onToggleTheater) options.onToggleTheater(vpState.theater);
      ctx.render();
    };
    canvasArea.appendChild(cornerBtn);
  }

  const mainScroll = document.createElement("div");
  mainScroll.className = "nv-main-scroll";
  canvasArea.appendChild(mainScroll);

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
    canvasArea.appendChild(dock);
  }

  stageBody.appendChild(canvasArea);

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
      : `<div class="nv-hero-title" data-hero-title>${escapeHtml(heroItem.title)}</div>`;

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
      attachImgFallback(logoEl, `<div class="nv-hero-title">${escapeHtml(heroItem.title)}</div>`);
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
    cwSec.innerHTML = `<div class="nv-section-head"><div class="nv-section-title">Continue Watching</div></div>`;
    const strip = document.createElement("div");
    strip.className = "nv-hstrip";

    CONTINUE_WATCHING_SAMPLES.forEach(cw => {
      const card = document.createElement("div");
      card.className = "nv-cw-card";
      card.innerHTML = `
        <img class="nv-cw-bg-img" src="${escapeHtml(cw.bg)}" alt="${escapeHtml(cw.title)}" loading="lazy" onerror="this.remove()" />
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
    sec.innerHTML = `<div class="nv-section-head"><div class="nv-section-title">${escapeHtml(col.title)}</div></div>`;

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

  const titleEl = document.createElement("div");
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
        rowSec.innerHTML = `<div class="nv-section-head"><div class="nv-section-title">${escapeHtml(rTitle)}</div></div>`;
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
  const mp = ctx.state?.metadataPower || {};
  const prov = mp.posterProvider || "none";
  const card = document.createElement("div");
  card.className = "nv-poster-card";
  if (fixedWidth) card.style.flex = "0 0 var(--nv-card-w, 118px)";

  const wrap = document.createElement("div");
  wrap.className = "nv-poster-wrap";
  const fallbackHtml = `<div class="nv-poster-fallback">${escapeHtml(item.title)}</div>`;
  const customUrl = resolveCustomizedPosterUrl(item, mp, "poster");
  const rawUrl = item.posterUrl || "";

  if (customUrl || rawUrl) {
    const img = document.createElement("img");
    img.src = customUrl || rawUrl;
    img.alt = item.title;
    img.loading = "lazy";
    let triedRaw = !customUrl || customUrl === rawUrl;
    img.onerror = () => {
      if (!triedRaw && rawUrl) {
        triedRaw = true;
        img.src = rawUrl;
        const ov = createPosterOverlayElement(item, mp);
        if (ov && !wrap.querySelector(".nv-poster-overlay")) wrap.appendChild(ov);
        return;
      }
      const tmp = document.createElement("div");
      tmp.innerHTML = fallbackHtml;
      if (tmp.firstElementChild) img.replaceWith(tmp.firstElementChild);
    };
    wrap.appendChild(img);

    if (prov === "rpdb" || prov === "topposters") {
      const ov = createPosterOverlayElement(item, mp);
      if (ov) wrap.appendChild(ov);
    }
  } else {
    wrap.innerHTML = fallbackHtml;
  }

  if (item.rating && prov === "none") {
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

  const isAnimeLike =
    (item.genres || []).some(g => /anime|animation/i.test(String(g))) ||
    String(item.language || "").toUpperCase() === "JA";

  const ratingsSpec = [
    ["imdb", "IMDb", item.rating || "8.5", "#F5C518", "#000000"],
    ["tmdb", "TMDB", "84%", "#01D277", "#031b11"],
    ["tomatoes", "RT", "93%", "#FA320A", "#ffffff"],
    ["audience", "Pop", "91%", "#F59E0B", "#111111"],
    ["metacritic", "MC", "83", "#66CC33", "#071a04"],
    ["trakt", "Trakt", "88%", "#ED1C24", "#ffffff"],
    ["letterboxd", "LB", "4.3", "#00E054", "#041a0b"],
    ...(isAnimeLike ? [["mal", "MAL", "8.9", "#2E51A2", "#ffffff"]] : []),
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
    ${item.logoUrl ? `<img class="nv-hero-logo" src="${escapeHtml(item.logoUrl)}" alt="${escapeHtml(item.title)}" data-detail-logo />` : `<div class="nv-hero-title" style="font-size:28px">${escapeHtml(item.title)}</div>`}
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
  if (dLogo) attachImgFallback(dLogo, `<div class="nv-hero-title" style="font-size:28px">${escapeHtml(item.title)}</div>`);

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
        <div class="nv-section-title">Episodes · Season 1</div>
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
    cSec.innerHTML = `<div class="nv-section-head"><div class="nv-section-title">Cast &amp; Crew</div></div>`;
    const rail = document.createElement("div");
    rail.className = "nv-cast-rail";
    getCastForItem(item).forEach(c => {
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
    mSec.innerHTML = `<div class="nv-section-head"><div class="nv-section-title">More Like This</div></div>`;
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
function renderStreamsScreen(root, ctx, profile, _s, badgeCfg, st) {
  const { escapeHtml, icon } = ctx.ui;
  const item = vpState.selectedItem || DEFAULT_HERO_SLIDES[0];
  const bd = item.backdropUrl || item.posterUrl || "";
  const debridLabel =
    (st.debridServices?.available || []).find(d => d.enabled)?.label ||
    (st.debridProviders || []).find(d => d.id === st.debridProvider)?.label ||
    st.debridProvider ||
    "TorBox";
  const fmtPreset = st.streamFormatter?.preset || st.streamFormatter?.active || "prism";

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
    ["hdr", "DV / HDR"],
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
    const card = renderNuvioStreamCard(ctx, {
      profile,
      sample,
      debridLabel,
      fmtPreset,
      badgeCfg,
      clientMode: "nuvio",
    });
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
