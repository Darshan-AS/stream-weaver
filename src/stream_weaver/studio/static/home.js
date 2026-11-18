// Tab 1 — Home Screen, Collection/Folder Reordering, DIY Builder & Live Poster Strip Simulator

let selectedColIdx = 0;
let selectedFolderIdx = 0;
let diyMode = "tmdb";
let catalogSearchQuery = "";
const posterPreviewCache = new Map();

const CATEGORY_LABELS = {
  personal: "Personal & Trackers",
  platforms: "Streaming Platforms",
  discovery: "Genres, Moods & Franchises",
  cinema: "Studios & Cinema Classics",
  regional: "K-Drama, Global & Regional",
  anime: "Anime",
};

function ensureInlineCollection(col) {
  if (!col.ref && col.raw) return col.raw;
  const tpl = col.inlineTemplate || {
    id: `col-${col.id || "custom"}`,
    title: col.title || "Custom Collection",
    folders: (col.folders || []).map(f => ({
      id: f.id || "fld-item",
      title: f.title || "Folder",
      sources: [],
    })),
  };
  col.raw = JSON.parse(JSON.stringify(tpl));
  col.ref = null;
  col.with = {};
  return col.raw;
}

function buildPreviewSpec(selCol, folderIdx, activeFolder) {
  const inlineSource =
    selCol?.raw?.folders?.[folderIdx]?.sources?.[0] ||
    selCol?.inlineTemplate?.folders?.[folderIdx]?.sources?.[0] ||
    null;
  const base = inlineSource ? { ...inlineSource } : {};
  return {
    ...base,
    folderId: activeFolder?.id || base.catalogId || "folder",
    folderTitle: activeFolder?.title || base.title || "Featured",
    collectionTitle: selCol?.title || "Home",
    rowTitle: (activeFolder?.rows && activeFolder.rows[0]) || activeFolder?.title || "",
  };
}

export function renderHomeTab(root, ctx) {
  const { escapeHtml } = ctx.ui;
  const st = ctx.state;
  const p = ctx.currentProfile();
  if (!p) return;

  if (selectedColIdx >= p.collections.length) {
    selectedColIdx = Math.max(0, p.collections.length - 1);
  }
  const selCol = p.collections[selectedColIdx] || null;

  const wrap = document.createElement("div");
  wrap.className = "grid-2";

  // =========================================================================
  // LEFT COLUMN — Active Shelves + DIY Modal Trigger + Curated Catalogs
  // =========================================================================
  const left = document.createElement("div");
  left.className = "card";
  left.innerHTML = `
    <div class="row-between" style="margin-bottom:4px">
      <h2>Active Home Collections (${p.collections.length})</h2>
      <span class="pill accent">${escapeHtml(p.emoji || "🎬")} ${escapeHtml(p.displayName)}</span>
    </div>
    <p class="sub">Top-to-bottom order on Nuvio Home. Adding or removing shelves automatically derives AIOMetadata catalogs.</p>
  `;

  const list = document.createElement("div");
  list.className = "col-list";

  p.collections.forEach((col, idx) => {
    const item = document.createElement("div");
    item.className = "col-item" + (idx === selectedColIdx ? " selected" : "");
    item.onclick = () => {
      selectedColIdx = idx;
      selectedFolderIdx = 0;
      ctx.render();
    };

    const info = document.createElement("div");
    info.innerHTML = `
      <div class="col-item-title">${idx + 1}. ${escapeHtml(col.title)}</div>
      <div class="col-item-meta">${(col.folders || []).length} folder tab(s) · <code>${escapeHtml(col.ref || "diy-inline")}</code></div>
    `;

    const ctrls = document.createElement("div");
    ctrls.className = "col-controls";

    const up = document.createElement("button");
    up.className = "btn icon-btn";
    up.title = "Move collection up";
    up.textContent = "↑";
    up.disabled = idx === 0;
    up.onclick = (e) => {
      e.stopPropagation();
      [p.collections[idx - 1], p.collections[idx]] = [p.collections[idx], p.collections[idx - 1]];
      selectedColIdx = idx - 1;
      ctx.saveSection("profile", true);
    };

    const dn = document.createElement("button");
    dn.className = "btn icon-btn";
    dn.title = "Move collection down";
    dn.textContent = "↓";
    dn.disabled = idx === p.collections.length - 1;
    dn.onclick = (e) => {
      e.stopPropagation();
      [p.collections[idx + 1], p.collections[idx]] = [p.collections[idx], p.collections[idx + 1]];
      selectedColIdx = idx + 1;
      ctx.saveSection("profile", true);
    };

    const rm = document.createElement("button");
    rm.className = "btn icon-btn danger";
    rm.title = "Remove collection from profile";
    rm.textContent = "✕";
    rm.onclick = (e) => {
      e.stopPropagation();
      p.collections.splice(idx, 1);
      selectedColIdx = Math.max(0, selectedColIdx - 1);
      ctx.saveSection("profile", true);
    };

    ctrls.append(up, dn, rm);
    item.append(info, ctrls);
    list.appendChild(item);
  });
  left.appendChild(list);

  // DIY Modal Launch Button
  const diyBtns = document.createElement("div");
  diyBtns.style.marginTop = "14px";
  diyBtns.style.display = "flex";
  diyBtns.style.gap = "8px";
  diyBtns.style.flexWrap = "wrap";

  const modalDiyBtn = document.createElement("button");
  modalDiyBtn.className = "btn accent-outline";
  modalDiyBtn.style.width = "100%";
  modalDiyBtn.style.justifyContent = "center";
  modalDiyBtn.textContent = "✨ + DIY Collection / Folder Builder (TMDB · MDBList · Addons)";
  modalDiyBtn.onclick = () => openDiyBuilderModal(ctx, p, selCol);
  diyBtns.appendChild(modalDiyBtn);
  left.appendChild(diyBtns);

  // Curated Starter Collections
  const addSec = document.createElement("div");
  addSec.style.marginTop = "16px";
  addSec.style.borderTop = "1px solid var(--border)";
  addSec.style.paddingTop = "14px";
  addSec.innerHTML = `<h3>➕ Curated Recipe Collections</h3>`;

  const usedRefs = new Set(p.collections.filter(c => c.ref).map(c => c.ref));
  const standardCols = (st.libraryCollections || []).filter(
    lc => (!lc.presets || lc.presets.length === 0) && !usedRefs.has(lc.id)
  );
  const grouped = {};
  standardCols.forEach(lc => {
    const cat = lc.category || "other";
    (grouped[cat] = grouped[cat] || []).push(lc);
  });

  Object.entries(grouped).forEach(([cat, cols]) => {
    const grpLbl = document.createElement("div");
    grpLbl.style.fontSize = "11px";
    grpLbl.style.fontWeight = "600";
    grpLbl.style.textTransform = "uppercase";
    grpLbl.style.letterSpacing = "0.04em";
    grpLbl.style.color = "var(--muted)";
    grpLbl.style.margin = "10px 0 6px";
    grpLbl.textContent = CATEGORY_LABELS[cat] || "More Collections";
    addSec.appendChild(grpLbl);

    const addChips = document.createElement("div");
    addChips.className = "chip-row";
    cols.forEach(lc => {
      const btn = document.createElement("button");
      btn.className = "btn icon-btn";
      btn.textContent = `+ ${lc.title}`;
      btn.title = lc.description || lc.id;
      btn.onclick = () => {
        p.collections.push({ ref: lc.id, with: {}, title: lc.title, folders: lc.folders });
        selectedColIdx = p.collections.length - 1;
        selectedFolderIdx = 0;
        ctx.saveSection("profile", true);
      };
      addChips.appendChild(btn);
    });
    addSec.appendChild(addChips);
  });

  // Regional Cinema Builder
  const regRecipe = (st.libraryCollections || []).find(lc => lc.id === "regional-cinema");
  if (regRecipe && Array.isArray(regRecipe.presets) && regRecipe.presets.length) {
    const regHead = document.createElement("div");
    regHead.style.fontSize = "11px";
    regHead.style.fontWeight = "600";
    regHead.style.textTransform = "uppercase";
    regHead.style.letterSpacing = "0.04em";
    regHead.style.color = "var(--muted)";
    regHead.style.margin = "12px 0 6px";
    regHead.textContent = "Regional Cinema Builder";
    addSec.appendChild(regHead);

    const regRow = document.createElement("div");
    regRow.style.display = "flex";
    regRow.style.gap = "8px";
    const sel = document.createElement("select");
    regRecipe.presets.forEach((pr, idx) => {
      const opt = document.createElement("option");
      opt.value = String(idx);
      opt.textContent = `🌍 ${pr.label} (${pr.with.language})`;
      sel.appendChild(opt);
    });
    const addRegBtn = document.createElement("button");
    addRegBtn.className = "btn accent-outline";
    addRegBtn.style.whiteSpace = "nowrap";
    addRegBtn.textContent = "+ Add Language";
    addRegBtn.onclick = () => {
      const chosen = regRecipe.presets[Number(sel.value)];
      p.collections.push({ ref: "regional-cinema", with: { ...chosen.with }, title: chosen.label, folders: [] });
      selectedColIdx = p.collections.length - 1;
      selectedFolderIdx = 0;
      ctx.saveSection("profile", true);
    };
    regRow.append(sel, addRegBtn);
    addSec.appendChild(regRow);
  }
  left.appendChild(addSec);

  // =========================================================================
  // RIGHT COLUMN — Interactive Nuvio TV Simulator + Live Poster Strip
  // =========================================================================
  const right = document.createElement("div");
  right.className = "card";

  if (!selCol) {
    right.innerHTML = `<h2>Interactive Nuvio TV Home Simulator</h2>
      <p class="sub">Select or add a collection on the left to preview its folders and live catalog posters.</p>`;
  } else {
    const libMeta = (st.libraryCollections || []).find(lc => lc.id === selCol.ref);
    const isInline = !selCol.ref && Boolean(selCol.raw);

    // Hero Banner
    const hero = document.createElement("div");
    hero.className = "hero-preview";
    if (selCol.backdropUrl) {
      hero.style.backgroundImage = `url('${selCol.backdropUrl.replace(/'/g, "\\'")}')`;
    }
    const heroTop = document.createElement("div");
    heroTop.className = "row-between";
    heroTop.style.marginBottom = "8px";
    heroTop.style.flexWrap = "wrap";
    heroTop.innerHTML = `<span class="pill ${isInline ? "accent" : ""}">${isInline ? "DIY Custom Collection" : "Curated Recipe Collection"}</span>`;

    const heroActions = document.createElement("div");
    heroActions.className = "row-wrap";
    const addFolderModalBtn = document.createElement("button");
    addFolderModalBtn.className = "btn icon-btn accent-outline";
    addFolderModalBtn.textContent = "+ Add Folder Tab";
    addFolderModalBtn.onclick = () => openDiyBuilderModal(ctx, p, selCol);
    heroActions.appendChild(addFolderModalBtn);

    if (!isInline && selCol.inlineTemplate) {
      const unlockBtn = document.createElement("button");
      unlockBtn.className = "btn icon-btn";
      unlockBtn.textContent = "✏️ Unlock to Customize Art / Tabs";
      unlockBtn.onclick = () => {
        ensureInlineCollection(selCol);
        ctx.saveSection("profile", true);
      };
      heroActions.appendChild(unlockBtn);
    }
    heroTop.appendChild(heroActions);
    hero.appendChild(heroTop);

    const hTitle = document.createElement("h3");
    hTitle.textContent = selCol.title;
    const hMeta = document.createElement("div");
    hMeta.className = "meta";
    hMeta.textContent = (libMeta && libMeta.description) || `${(selCol.folders || []).length} tabbed folder(s) configured`;
    hero.append(hTitle, hMeta);
    right.appendChild(hero);

    // Inline Collection Metadata Editor
    if (isInline && selCol.raw) {
      const metaBar = document.createElement("div");
      metaBar.className = "rows-box";
      metaBar.style.marginTop = "0";
      metaBar.style.marginBottom = "14px";
      metaBar.style.display = "grid";
      metaBar.style.gridTemplateColumns = "repeat(auto-fit, minmax(220px, 1fr))";
      metaBar.style.gap = "10px";

      const cTitleLbl = document.createElement("label");
      cTitleLbl.className = "field";
      cTitleLbl.textContent = "Collection Shelf Title";
      const cTitleIn = document.createElement("input");
      cTitleIn.type = "text";
      cTitleIn.value = selCol.raw.title || "";
      cTitleIn.onchange = () => {
        selCol.raw.title = cTitleIn.value.trim() || "Custom Collection";
        ctx.saveSection("profile", true);
      };
      cTitleLbl.appendChild(cTitleIn);

      const cBgLbl = document.createElement("label");
      cBgLbl.className = "field";
      cBgLbl.textContent = "Collection Backdrop Artwork URL";
      const cBgIn = document.createElement("input");
      cBgIn.type = "text";
      cBgIn.placeholder = "https://image.tmdb.org/t/p/original/…";
      cBgIn.value = selCol.raw.backdropImageUrl || "";
      cBgIn.onchange = () => {
        const v = cBgIn.value.trim();
        if (v) selCol.raw.backdropImageUrl = v;
        else delete selCol.raw.backdropImageUrl;
        ctx.saveSection("profile", true);
      };
      cBgLbl.appendChild(cBgIn);
      metaBar.append(cTitleLbl, cBgLbl);
      right.appendChild(metaBar);
    }

    // Recipe Parameter Checkboxes (e.g., Streaming Services OTT filter)
    if (!isInline && libMeta && Array.isArray(libMeta.params)) {
      const listParam = libMeta.params.find(pr => pr.type === "list" && pr.options && pr.options.length > 0);
      if (listParam) {
        const paramBox = document.createElement("div");
        paramBox.className = "rows-box";
        paramBox.style.marginTop = "0";
        paramBox.style.marginBottom = "14px";
        const currentVals = Array.isArray((selCol.with || {})[listParam.name])
          ? selCol.with[listParam.name]
          : (listParam.default || []);
        paramBox.innerHTML = `<div style="font-size:12.5px;font-weight:600;margin-bottom:8px">Customize ${escapeHtml(listParam.label)}:</div>`;
        const chkWrap = document.createElement("div");
        chkWrap.className = "chip-row";
        listParam.options.forEach(opt => {
          const lbl = document.createElement("label");
          lbl.className = "switch chip" + (currentVals.includes(opt.value) ? " active" : "");
          const chk = document.createElement("input");
          chk.type = "checkbox";
          chk.checked = currentVals.includes(opt.value);
          chk.onchange = () => {
            const next = listParam.options
              .map(o => o.value)
              .filter(v => (v === opt.value ? chk.checked : currentVals.includes(v)));
            if (!next.length) {
              chk.checked = true;
              return;
            }
            selCol.with = { ...(selCol.with || {}), [listParam.name]: next };
            ctx.saveSection("profile", true);
          };
          lbl.append(chk, document.createTextNode(opt.label));
          chkWrap.appendChild(lbl);
        });
        paramBox.appendChild(chkWrap);
        right.appendChild(paramBox);
      }
    }

    // Folder Tabs Strip
    const folders = selCol.folders || [];
    if (selectedFolderIdx >= folders.length) selectedFolderIdx = 0;

    const folderStripHead = document.createElement("div");
    folderStripHead.className = "row-between";
    folderStripHead.style.marginBottom = "10px";
    folderStripHead.innerHTML = `<strong style="font-size:13px">Folder Tabs (${folders.length}) — Click a folder to simulate live Nuvio posters</strong>`;
    right.appendChild(folderStripHead);

    const grid = document.createElement("div");
    grid.className = "folders-strip";
    folders.forEach((f, fIdx) => {
      const fc = document.createElement("div");
      fc.className = "folder-card" + (fIdx === selectedFolderIdx ? " active" : "");
      fc.onclick = () => {
        selectedFolderIdx = fIdx;
        ctx.render();
      };
      const artHtml = f.coverUrl
        ? `<img class="folder-art" src="${escapeHtml(f.coverUrl)}" alt="${escapeHtml(f.title)}" loading="lazy" />`
        : `<div class="folder-art-fallback">${escapeHtml(f.title)}</div>`;
      fc.innerHTML = `
        ${artHtml}
        <div class="folder-body">
          <div style="min-width:0">
            <div class="folder-title">${escapeHtml(f.title)}</div>
            <div class="folder-count">${(f.rows || []).length} catalog row(s)</div>
          </div>
        </div>
      `;
      grid.appendChild(fc);
    });
    right.appendChild(grid);

    // Selected Folder Controls (↑ / ↓ Reorder + Artwork + Catalog Rows + Live Poster Strip!)
    const activeFolder = folders[selectedFolderIdx];
    if (activeFolder) {
      const rbox = document.createElement("div");
      rbox.className = "rows-box";
      const rHead = document.createElement("div");
      rHead.className = "row-between";
      rHead.style.flexWrap = "wrap";
      rHead.innerHTML = `<div style="font-size:13.5px;font-weight:700">📂 Folder "${escapeHtml(activeFolder.title)}"</div>`;

      const fCtrls = document.createElement("div");
      fCtrls.className = "col-controls";

      const fUp = document.createElement("button");
      fUp.className = "btn icon-btn";
      fUp.title = "Move folder earlier (↑ / ←)";
      fUp.textContent = "↑";
      fUp.disabled = selectedFolderIdx === 0;
      fUp.onclick = () => {
        const rawDoc = ensureInlineCollection(selCol);
        if (Array.isArray(rawDoc.folders) && rawDoc.folders[selectedFolderIdx]) {
          const rf = rawDoc.folders;
          [rf[selectedFolderIdx - 1], rf[selectedFolderIdx]] = [rf[selectedFolderIdx], rf[selectedFolderIdx - 1]];
          selectedFolderIdx -= 1;
          ctx.saveSection("profile", true);
        }
      };

      const fDown = document.createElement("button");
      fDown.className = "btn icon-btn";
      fDown.title = "Move folder later (↓ / →)";
      fDown.textContent = "↓";
      fDown.disabled = selectedFolderIdx >= folders.length - 1;
      fDown.onclick = () => {
        const rawDoc = ensureInlineCollection(selCol);
        if (Array.isArray(rawDoc.folders) && rawDoc.folders[selectedFolderIdx]) {
          const rf = rawDoc.folders;
          [rf[selectedFolderIdx + 1], rf[selectedFolderIdx]] = [rf[selectedFolderIdx], rf[selectedFolderIdx + 1]];
          selectedFolderIdx += 1;
          ctx.saveSection("profile", true);
        }
      };

      const fDel = document.createElement("button");
      fDel.className = "btn icon-btn danger";
      fDel.textContent = "🗑 Remove Folder";
      fDel.disabled = folders.length <= 1;
      fDel.onclick = () => {
        const rawDoc = ensureInlineCollection(selCol);
        if (Array.isArray(rawDoc.folders) && rawDoc.folders.length > 1) {
          rawDoc.folders.splice(selectedFolderIdx, 1);
          selectedFolderIdx = Math.max(0, selectedFolderIdx - 1);
          ctx.saveSection("profile", true);
        }
      };

      fCtrls.append(fUp, fDown, fDel);
      rHead.appendChild(fCtrls);
      rbox.appendChild(rHead);

      if (isInline && selCol.raw?.folders?.[selectedFolderIdx]) {
        const rawF = selCol.raw.folders[selectedFolderIdx];
        const fEditGrid = document.createElement("div");
        fEditGrid.style.display = "grid";
        fEditGrid.style.gridTemplateColumns = "repeat(auto-fit, minmax(210px, 1fr))";
        fEditGrid.style.gap = "10px";
        fEditGrid.style.marginTop = "10px";

        const ftLbl = document.createElement("label");
        ftLbl.className = "field";
        ftLbl.textContent = "Folder Tab Name";
        const ftIn = document.createElement("input");
        ftIn.type = "text";
        ftIn.value = rawF.title || "";
        ftIn.onchange = () => {
          rawF.title = ftIn.value.trim() || "Folder";
          ctx.saveSection("profile", true);
        };
        ftLbl.appendChild(ftIn);

        const fcLbl = document.createElement("label");
        fcLbl.className = "field";
        fcLbl.textContent = "Folder Cover Artwork URL";
        const fcIn = document.createElement("input");
        fcIn.type = "text";
        fcIn.placeholder = "https://image.tmdb.org/t/p/w780/…";
        fcIn.value = rawF.coverImageUrl || "";
        fcIn.onchange = () => {
          const v = fcIn.value.trim();
          if (v) rawF.coverImageUrl = v;
          else delete rawF.coverImageUrl;
          ctx.saveSection("profile", true);
        };
        fcLbl.appendChild(fcIn);
        fEditGrid.append(ftLbl, fcLbl);
        rbox.appendChild(fEditGrid);
      }

      const chips = document.createElement("div");
      chips.className = "chip-row";
      chips.style.marginTop = "10px";
      (activeFolder.rows || []).forEach(r => {
        const c = document.createElement("span");
        c.className = "chip";
        c.textContent = r;
        chips.appendChild(c);
      });
      rbox.appendChild(chips);
      right.appendChild(rbox);

      // LIVE POSTER STRIP SIMULATOR (`POST /api/preview-catalog`)
      right.appendChild(renderPosterSimulator(ctx, p, selCol, selectedFolderIdx, activeFolder));
    }
  }

  wrap.append(left, right);
  root.appendChild(wrap);
}

function renderPosterSimulator(ctx, profile, selCol, folderIdx, activeFolder) {
  const { escapeHtml } = ctx.ui;
  const spec = buildPreviewSpec(selCol, folderIdx, activeFolder);
  const cacheKey = `${profile.name}::${selCol.id}::${folderIdx}::${JSON.stringify(spec)}`;

  const simBox = document.createElement("div");
  simBox.className = "rows-box";
  simBox.style.borderColor = "var(--border-strong)";

  const head = document.createElement("div");
  head.className = "row-between";
  head.style.marginBottom = "10px";

  const titleWrap = document.createElement("div");
  titleWrap.innerHTML = `<strong style="font-size:13.5px">🎞️ Live Nuvio Poster Strip Simulator — "${escapeHtml(activeFolder.title)}"</strong>`;

  const rightControls = document.createElement("div");
  rightControls.className = "row-wrap";
  const srcPill = document.createElement("span");
  srcPill.className = "pill";
  srcPill.textContent = "Loading…";
  const refreshBtn = document.createElement("button");
  refreshBtn.className = "btn icon-btn";
  refreshBtn.textContent = "🔄 Refresh";
  rightControls.append(srcPill, refreshBtn);
  head.append(titleWrap, rightControls);
  simBox.appendChild(head);

  const shelf = document.createElement("div");
  shelf.className = "poster-shelf";
  simBox.appendChild(shelf);

  const paintItems = (data) => {
    const items = (data && Array.isArray(data.items)) ? data.items : [];
    const src = (data && data.source) || "sample";
    srcPill.className = "pill " + (src === "sample" ? "warn" : "ok");
    srcPill.textContent = src === "aiometadata"
      ? "Live AIOMetadata"
      : (src === "tmdb" ? "Live TMDB" : "Sample Preview");

    shelf.innerHTML = "";
    if (!items.length) {
      shelf.innerHTML = `<div style="font-size:12px;color:var(--muted);padding:10px">No poster items returned for this catalog.</div>`;
      return;
    }
    items.forEach(item => {
      const card = document.createElement("div");
      card.className = "poster-card";
      const ratingText = item.rating ? `★ ${escapeHtml(String(item.rating))}` : "";
      const artHtml = item.posterUrl
        ? `<img class="poster-img" src="${escapeHtml(item.posterUrl)}" alt="${escapeHtml(item.title)}" loading="lazy" />`
        : `<div class="poster-placeholder"><span>${escapeHtml(item.title)}</span></div>`;
      card.innerHTML = `
        ${artHtml}
        ${ratingText ? `<span class="poster-rating">${ratingText}</span>` : ""}
        <div class="poster-meta">
          <div class="poster-title" title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</div>
          <div class="poster-year">${escapeHtml(String(item.year || ""))} · ${escapeHtml(item.type || "movie")}</div>
        </div>
      `;
      shelf.appendChild(card);
    });
  };

  const fetchPosters = async (force = false) => {
    if (!force && posterPreviewCache.has(cacheKey)) {
      paintItems(posterPreviewCache.get(cacheKey));
      return;
    }
    shelf.innerHTML = `<div style="font-size:12px;color:var(--muted);padding:10px">Fetching poster strip…</div>`;
    try {
      const res = await ctx.api("/api/preview-catalog", {
        spec,
        pendingEnv: ctx.pendingEnvEdits,
      });
      posterPreviewCache.set(cacheKey, res);
      paintItems(res);
    } catch (err) {
      srcPill.className = "pill danger";
      srcPill.textContent = "Preview Error";
      shelf.innerHTML = `<div style="font-size:12px;color:#f87171;padding:10px">${escapeHtml(err.message)}</div>`;
    }
  };

  refreshBtn.onclick = () => fetchPosters(true);
  fetchPosters(false);

  return simBox;
}

function openDiyBuilderModal(ctx, profile, currentCol) {
  let targetMode = currentCol ? "existing" : "new";
  let newColTitle = "My Custom Shelf";
  let newColBackdrop = "";

  ctx.openModal((dialog) => {
    const { escapeHtml } = ctx.ui;
    const st = ctx.state;

    const renderModal = () => {
      dialog.innerHTML = "";
      const head = document.createElement("div");
      head.className = "row-between";
      head.innerHTML = `
        <div>
          <h2 style="margin:0;font-size:17px">✨ DIY Collection &amp; Folder Builder</h2>
          <p class="sub" style="margin:3px 0 0">Creates both the AIOMetadata catalog definition and the Nuvio Home folder in a single step.</p>
        </div>
      `;
      const closeBtn = document.createElement("button");
      closeBtn.className = "btn icon-btn";
      closeBtn.textContent = "✕";
      closeBtn.onclick = () => ctx.closeModal();
      head.appendChild(closeBtn);
      dialog.appendChild(head);

      // Destination selector (Add to current collection vs Create new DIY collection)
      const destBox = document.createElement("div");
      destBox.className = "rows-box";
      const destTabs = document.createElement("div");
      destTabs.className = "tabs";
      if (currentCol) {
        const bExist = document.createElement("button");
        bExist.className = "tab" + (targetMode === "existing" ? " active" : "");
        bExist.textContent = `📂 Add Tab to "${currentCol.title}"`;
        bExist.onclick = () => { targetMode = "existing"; renderModal(); };
        destTabs.appendChild(bExist);
      }
      const bNew = document.createElement("button");
      bNew.className = "tab" + (targetMode === "new" ? " active" : "");
      bNew.textContent = "✨ Create Brand-New DIY Collection";
      bNew.onclick = () => { targetMode = "new"; renderModal(); };
      destTabs.appendChild(bNew);
      destBox.appendChild(destTabs);

      if (targetMode === "new") {
        const newGrid = document.createElement("div");
        newGrid.className = "grid-equal";
        newGrid.style.marginTop = "10px";
        const tL = document.createElement("label");
        tL.className = "field"; tL.textContent = "New Collection Shelf Title";
        const tIn = document.createElement("input");
        tIn.type = "text"; tIn.value = newColTitle;
        tIn.oninput = () => { newColTitle = tIn.value; };
        tL.appendChild(tIn);

        const bgL = document.createElement("label");
        bgL.className = "field"; bgL.textContent = "Collection Backdrop Artwork URL (Optional)";
        const bgIn = document.createElement("input");
        bgIn.type = "text"; bgIn.placeholder = "https://image.tmdb.org/t/p/original/…"; bgIn.value = newColBackdrop;
        bgIn.oninput = () => { newColBackdrop = bgIn.value; };
        bgL.appendChild(bgIn);
        newGrid.append(tL, bgL);
        destBox.appendChild(newGrid);
      }
      dialog.appendChild(destBox);

      // Source Type Tabs
      const srcBox = document.createElement("div");
      srcBox.className = "rows-box";
      const modeTabs = document.createElement("div");
      modeTabs.className = "tabs";
      [
        ["tmdb", "🎬 TMDB Discover Filter"],
        ["mdblist", "📋 MDBList URL / ID"],
        ["catalog", "🔌 Catalog Picker"],
      ].forEach(([mId, mLbl]) => {
        const b = document.createElement("button");
        b.className = "tab" + (diyMode === mId ? " active" : "");
        b.textContent = mLbl;
        b.onclick = () => { diyMode = mId; renderModal(); };
        modeTabs.appendChild(b);
      });
      srcBox.appendChild(modeTabs);

      const form = document.createElement("div");
      form.style.display = "grid";
      form.style.gridTemplateColumns = "repeat(auto-fit, minmax(210px, 1fr))";
      form.style.gap = "10px";
      form.style.marginTop = "14px";
      form.style.alignItems = "end";

      const makeIn = (lbl, ph, val = "") => {
        const l = document.createElement("label"); l.className = "field"; l.textContent = lbl;
        const inp = document.createElement("input"); inp.type = "text"; inp.placeholder = ph; inp.value = val;
        l.appendChild(inp);
        return { l, inp };
      };
      const makeSel = (lbl, opts) => {
        const l = document.createElement("label"); l.className = "field"; l.textContent = lbl;
        const sel = document.createElement("select");
        opts.forEach(([v, txt]) => {
          const o = document.createElement("option"); o.value = v; o.textContent = txt;
          sel.appendChild(o);
        });
        l.appendChild(sel);
        return { l, sel };
      };

      const commitBuiltFolder = async (folderTitle, coverUrl, sources, appendRow = false) => {
        const uid = Date.now().toString(36).slice(-5);
        if (targetMode === "new" || !currentCol) {
          const rawCol = {
            id: `col-custom-${uid}`,
            title: newColTitle.trim() || "My Custom Shelf",
            folders: [
              {
                id: `fld-custom-${uid}`,
                title: folderTitle || "Featured",
                sources,
                ...(coverUrl && coverUrl.trim() ? { coverImageUrl: coverUrl.trim() } : {}),
              },
            ],
            ...(newColBackdrop.trim() ? { backdropImageUrl: newColBackdrop.trim() } : {}),
          };
          profile.collections.push({ ref: null, raw: rawCol, title: rawCol.title, folders: [] });
          selectedColIdx = profile.collections.length - 1;
          selectedFolderIdx = 0;
        } else {
          const rawDoc = ensureInlineCollection(currentCol);
          if (!Array.isArray(rawDoc.folders)) rawDoc.folders = [];
          if (appendRow && rawDoc.folders[selectedFolderIdx]) {
            rawDoc.folders[selectedFolderIdx].sources = [
              ...(rawDoc.folders[selectedFolderIdx].sources || []),
              ...sources,
            ];
          } else {
            const fObj = { id: `fld-custom-${uid}`, title: folderTitle || "Custom Tab", sources };
            if (coverUrl && coverUrl.trim()) fObj.coverImageUrl = coverUrl.trim();
            rawDoc.folders.push(fObj);
            selectedFolderIdx = rawDoc.folders.length - 1;
          }
        }
        ctx.closeModal();
        await ctx.saveSection("profile", true);
      };

      if (diyMode === "tmdb") {
        const meta = st.customBuilderMeta || { genres: [], languages: [], decades: [], sorts: [] };
        const titleCtrl = makeIn("Folder Tab Title", "e.g. 90s Sci-Fi Thrillers", "90s Sci-Fi Thrillers");
        const mediaCtrl = makeSel("Media Type", [["both", "Movies + Series"], ["movie", "Movies Only"], ["series", "Series Only"]]);
        const genreCtrl = makeSel("Genre", [["", "Any Genre"], ...(meta.genres || []).map(g => [g.slug, g.label])]);
        const langCtrl = makeSel("Original Language", [["", "Any Language"], ...(meta.languages || []).map(l => [l.code, l.label])]);
        const decCtrl = makeSel("Decade / Era", (meta.decades || []).map(d => [d.id, d.label]));
        const sortCtrl = makeSel("Sort Order", (meta.sorts || []).map(s => [s.id, s.label]));
        const coverCtrl = makeIn("Folder Cover Artwork URL (Optional)", "https://image.tmdb.org/t/p/w780/…");

        const addBtn = document.createElement("button");
        addBtn.className = "btn primary";
        addBtn.textContent = "+ Build & Save TMDB Folder";
        addBtn.onclick = async () => {
          addBtn.disabled = true;
          try {
            const built = await ctx.api("/api/build-row", {
              spec: {
                kind: "tmdb",
                title: titleCtrl.inp.value,
                media: mediaCtrl.sel.value,
                genre: genreCtrl.sel.value,
                language: langCtrl.sel.value,
                decade: decCtrl.sel.value,
                sort: sortCtrl.sel.value,
              },
              pendingEnv: ctx.pendingEnvEdits,
            });
            await commitBuiltFolder(built.title, coverCtrl.inp.value, built.sources, false);
          } catch (err) {
            ctx.showLog(err.message, [], true);
            addBtn.disabled = false;
          }
        };
        form.append(titleCtrl.l, mediaCtrl.l, genreCtrl.l, langCtrl.l, decCtrl.l, sortCtrl.l, coverCtrl.l, addBtn);
      } else if (diyMode === "mdblist") {
        const urlCtrl = makeIn("MDBList URL or Numeric ID", "https://mdblist.com/lists/user/list-name  or  88434");
        const titleCtrl = makeIn("Folder Title (Optional)", "Auto-resolved from MDBList");
        const mediaCtrl = makeSel("Media Type (for numeric IDs)", [["movie", "Movies"], ["series", "Series"]]);
        const coverCtrl = makeIn("Folder Cover Artwork URL (Optional)", "https://image.tmdb.org/t/p/w780/…");
        const addBtn = document.createElement("button");
        addBtn.className = "btn primary";
        addBtn.textContent = "+ Resolve & Save MDBList Folder";
        addBtn.onclick = async () => {
          addBtn.disabled = true;
          try {
            const built = await ctx.api("/api/build-row", {
              spec: {
                kind: "mdblist",
                input: urlCtrl.inp.value,
                title: titleCtrl.inp.value,
                media: mediaCtrl.sel.value,
              },
              pendingEnv: ctx.pendingEnvEdits,
            });
            await commitBuiltFolder(built.title, coverCtrl.inp.value, built.sources, false);
          } catch (err) {
            ctx.showLog(err.message, [], true);
            addBtn.disabled = false;
          }
        };
        form.append(urlCtrl.l, titleCtrl.l, mediaCtrl.l, coverCtrl.l, addBtn);
      } else {
        const allCats = st.availableCatalogs || [];
        const filterCtrl = makeIn("Search Available Catalogs", "Type trakt, mal, bingecat, netflix…", catalogSearchQuery);
        const getFiltered = () => {
          const ql = catalogSearchQuery.trim().toLowerCase();
          return ql
            ? allCats.filter(c => c.label.toLowerCase().includes(ql) || c.key.toLowerCase().includes(ql))
            : allCats;
        };
        const initialList = getFiltered();
        const catCtrl = makeSel(
          `Available Catalog (${initialList.length})`,
          initialList.length ? initialList.map((c, i) => [String(i), c.label]) : [["", "No matching catalogs"]]
        );
        filterCtrl.inp.oninput = () => {
          catalogSearchQuery = filterCtrl.inp.value;
          const next = getFiltered();
          catCtrl.sel.innerHTML = "";
          next.forEach((c, i) => {
            const o = document.createElement("option");
            o.value = String(i);
            o.textContent = c.label;
            catCtrl.sel.appendChild(o);
          });
          catCtrl.l.firstChild.textContent = `Available Catalog (${next.length})`;
        };
        const titleCtrl = makeIn("Folder Title (Optional)", "Defaults to catalog name");
        const coverCtrl = makeIn("Folder Cover Artwork URL (Optional)", "https://image.tmdb.org/t/p/w780/…");
        const btns = document.createElement("div");
        btns.className = "row-wrap";

        const newFBtn = document.createElement("button");
        newFBtn.className = "btn primary";
        newFBtn.textContent = "+ Save as New Folder";
        newFBtn.onclick = async () => {
          const picked = getFiltered()[Number(catCtrl.sel.value)];
          if (!picked) return;
          const src = JSON.parse(JSON.stringify(picked.sourceSpec));
          await commitBuiltFolder(titleCtrl.inp.value.trim() || picked.name, coverCtrl.inp.value, [src], false);
        };
        btns.appendChild(newFBtn);

        if (targetMode === "existing" && currentCol) {
          const rowBtn = document.createElement("button");
          rowBtn.className = "btn accent-outline";
          rowBtn.textContent = "+ Append Row to Active Folder";
          rowBtn.onclick = async () => {
            const picked = getFiltered()[Number(catCtrl.sel.value)];
            if (!picked) return;
            const src = JSON.parse(JSON.stringify(picked.sourceSpec));
            await commitBuiltFolder(picked.name, "", [src], true);
          };
          btns.appendChild(rowBtn);
        }
        form.append(filterCtrl.l, catCtrl.l, titleCtrl.l, coverCtrl.l, btns);
      }

      srcBox.appendChild(form);
      dialog.appendChild(srcBox);
    };

    renderModal();
  });
}
