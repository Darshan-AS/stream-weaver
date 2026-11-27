// Tab 1 — Home Screen, Drag-and-Drop Reordering, DIY Builder & Persistent Real-Time Nuvio Viewport

import { renderNuvioViewport, setViewportState } from "./nuvio_viewport.js";

let selectedColIdx = 0;
let selectedFolderIdx = 0;
let diyMode = "tmdb";
let catalogSearchQuery = "";

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
      sources: f.sources || [],
    })),
  };
  col.raw = JSON.parse(JSON.stringify(tpl));
  col.ref = null;
  col.with = {};
  return col.raw;
}

function attachDragReorder(el, idx, getDragIdx, setDragIdx, onDropReorder) {
  el.draggable = true;
  el.ondragstart = (e) => {
    setDragIdx(idx);
    el.classList.add("dragging");
    if (e.dataTransfer) {
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", String(idx));
    }
  };
  el.ondragend = () => {
    setDragIdx(-1);
    el.classList.remove("dragging");
  };
  el.ondragover = (e) => {
    const src = getDragIdx();
    if (src >= 0 && src !== idx) {
      e.preventDefault();
      el.classList.add("drag-over");
    }
  };
  el.ondragleave = () => {
    el.classList.remove("drag-over");
  };
  el.ondrop = (e) => {
    e.preventDefault();
    el.classList.remove("drag-over");
    const src = getDragIdx();
    if (src >= 0 && src !== idx) {
      onDropReorder(src, idx);
    }
  };
}

export function renderHomeTab(root, ctx) {
  const { escapeHtml, icon } = ctx.ui;
  const st = ctx.state;
  const p = ctx.currentProfile();
  if (!p) return;

  if (selectedColIdx >= p.collections.length) {
    selectedColIdx = Math.max(0, p.collections.length - 1);
  }
  const selCol = p.collections[selectedColIdx] || null;
  const folders = selCol?.folders || [];
  if (selectedFolderIdx >= folders.length) selectedFolderIdx = 0;

  const wrap = document.createElement("div");
  wrap.className = "studio-split-stage";

  // =========================================================================
  // LEFT COLUMN — Drag-and-Drop Shelves + Folder Tabs Editor + Recipe Catalog
  // =========================================================================
  const leftStack = document.createElement("div");
  leftStack.className = "section-stack";

  const left = document.createElement("div");
  left.className = "card";
  left.innerHTML = `
    <div class="row-between" style="margin-bottom:4px">
      <h2>${icon("layers", 15)} Home Shelves (${p.collections.length})</h2>
      <span class="pill accent">${escapeHtml(p.emoji || "🎬")} ${escapeHtml(p.displayName)}</span>
    </div>
    <p class="sub">Drag shelves to reorder Nuvio Home. Adding or removing shelves updates AIOMetadata catalogs automatically.</p>
  `;

  let dragColIdx = -1;
  const list = document.createElement("div");
  list.className = "col-list";

  p.collections.forEach((col, idx) => {
    const item = document.createElement("div");
    item.className = "col-item" + (idx === selectedColIdx ? " selected" : "");
    item.onclick = () => {
      selectedColIdx = idx;
      selectedFolderIdx = 0;
      setViewportState({ colIdx: idx, folderIdx: 0 });
      ctx.render();
    };

    attachDragReorder(
      item,
      idx,
      () => dragColIdx,
      (v) => { dragColIdx = v; },
      (from, to) => {
        const [moved] = p.collections.splice(from, 1);
        p.collections.splice(to, 0, moved);
        selectedColIdx = to;
        selectedFolderIdx = 0;
        setViewportState({ colIdx: to, folderIdx: 0 });
        ctx.saveSection("profile", true);
      }
    );

    const info = document.createElement("div");
    info.style.cssText = "display:flex;align-items:center;gap:6px;min-width:0";
    info.innerHTML = `
      <span class="drag-handle" title="Drag to reorder">⋮⋮</span>
      <div style="min-width:0">
        <div class="col-item-title">${idx + 1}. ${escapeHtml(col.title)}</div>
        <div class="col-item-meta">${(col.folders || []).length} tab(s) · <code>${escapeHtml(col.ref || "diy-inline")}</code></div>
      </div>
    `;

    const ctrls = document.createElement("div");
    ctrls.className = "col-controls";

    const up = document.createElement("button");
    up.className = "btn ghost icon-btn";
    up.title = "Move collection up";
    up.innerHTML = icon("chevronUp", 13);
    up.disabled = idx === 0;
    up.onclick = (e) => {
      e.stopPropagation();
      [p.collections[idx - 1], p.collections[idx]] = [p.collections[idx], p.collections[idx - 1]];
      selectedColIdx = idx - 1;
      setViewportState({ colIdx: selectedColIdx, folderIdx: 0 });
      ctx.saveSection("profile", true);
    };

    const dn = document.createElement("button");
    dn.className = "btn ghost icon-btn";
    dn.title = "Move collection down";
    dn.innerHTML = icon("chevronDown", 13);
    dn.disabled = idx === p.collections.length - 1;
    dn.onclick = (e) => {
      e.stopPropagation();
      [p.collections[idx + 1], p.collections[idx]] = [p.collections[idx], p.collections[idx + 1]];
      selectedColIdx = idx + 1;
      setViewportState({ colIdx: selectedColIdx, folderIdx: 0 });
      ctx.saveSection("profile", true);
    };

    const rm = document.createElement("button");
    rm.className = "btn ghost icon-btn danger";
    rm.title = "Remove collection from profile";
    rm.innerHTML = icon("x", 13);
    rm.onclick = (e) => {
      e.stopPropagation();
      p.collections.splice(idx, 1);
      selectedColIdx = Math.max(0, selectedColIdx - 1);
      setViewportState({ colIdx: selectedColIdx, folderIdx: 0 });
      ctx.saveSection("profile", true);
    };

    ctrls.append(up, dn, rm);
    item.append(info, ctrls);
    list.appendChild(item);
  });
  left.appendChild(list);

  if (selCol) {
    const libMeta = (st.libraryCollections || []).find(lc => lc.id === selCol.ref);
    const isInline = !selCol.ref && Boolean(selCol.raw);

    const detailBox = document.createElement("div");
    detailBox.className = "rows-box";
    detailBox.style.marginTop = "14px";

    const dHead = document.createElement("div");
    dHead.className = "row-between";
    dHead.style.cssText = "margin-bottom:10px;flex-wrap:wrap";
    dHead.innerHTML = `<strong>${escapeHtml(selCol.title)}</strong>`;

    const dActs = document.createElement("div");
    dActs.className = "row-wrap";

    const openPreviewBtn = document.createElement("button");
    openPreviewBtn.className = "btn ghost icon-btn";
    openPreviewBtn.innerHTML = `${icon("tv", 12)} <span>Inspect Posters</span>`;
    openPreviewBtn.onclick = () => {
      setViewportState({ colIdx: selectedColIdx, folderIdx: selectedFolderIdx, screen: "collection" });
      ctx.render();
    };
    dActs.appendChild(openPreviewBtn);

    if (!isInline && selCol.inlineTemplate) {
      const unlockBtn = document.createElement("button");
      unlockBtn.className = "btn icon-btn";
      unlockBtn.innerHTML = `${icon("wand", 12)} <span>Unlock Tabs &amp; Art</span>`;
      unlockBtn.onclick = () => {
        ensureInlineCollection(selCol);
        ctx.saveSection("profile", true);
      };
      dActs.appendChild(unlockBtn);
    }
    dHead.appendChild(dActs);
    detailBox.appendChild(dHead);

    if (isInline && selCol.raw) {
      const metaGrid = document.createElement("div");
      metaGrid.style.cssText = "display:grid;grid-template-columns:1fr;gap:8px;margin-bottom:10px";

      const cTitleLbl = document.createElement("label");
      cTitleLbl.className = "field";
      cTitleLbl.textContent = "Collection Title";
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
      metaGrid.append(cTitleLbl, cBgLbl);
      detailBox.appendChild(metaGrid);
    }

    if (!isInline && libMeta && Array.isArray(libMeta.params)) {
      const listParam = libMeta.params.find(pr => pr.type === "list" && pr.options && pr.options.length > 0);
      if (listParam) {
        const currentVals = Array.isArray((selCol.with || {})[listParam.name])
          ? selCol.with[listParam.name]
          : (listParam.default || []);
        const pLbl = document.createElement("div");
        pLbl.style.cssText = "font-size:11.5px;font-weight:600;margin:4px 0 6px;color:var(--text-soft)";
        pLbl.textContent = `Customize ${listParam.label}`;
        detailBox.appendChild(pLbl);
        const chkWrap = document.createElement("div");
        chkWrap.className = "chip-row";
        chkWrap.style.marginBottom = "10px";
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
        detailBox.appendChild(chkWrap);
      }
    }

    let dragFolderIdx = -1;
    const fList = document.createElement("div");
    fList.className = "col-list";
    fList.style.marginTop = "6px";
    folders.forEach((f, fIdx) => {
      const row = document.createElement("div");
      row.className = "dnd-row" + (fIdx === selectedFolderIdx ? " active" : "");
      row.onclick = () => {
        selectedFolderIdx = fIdx;
        setViewportState({ colIdx: selectedColIdx, folderIdx: fIdx, screen: "collection" });
        ctx.render();
      };

      attachDragReorder(
        row,
        fIdx,
        () => dragFolderIdx,
        (v) => { dragFolderIdx = v; },
        (from, to) => {
          const rawDoc = ensureInlineCollection(selCol);
          if (Array.isArray(rawDoc.folders)) {
            const [moved] = rawDoc.folders.splice(from, 1);
            rawDoc.folders.splice(to, 0, moved);
            selectedFolderIdx = to;
            setViewportState({ colIdx: selectedColIdx, folderIdx: to });
            ctx.saveSection("profile", true);
          }
        }
      );

      const leftInfo = document.createElement("div");
      leftInfo.style.cssText = "display:flex;align-items:center;gap:6px;min-width:0;font-size:12px";
      leftInfo.innerHTML = `<span class="drag-handle">⋮⋮</span><span style="font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(f.title)}</span>`;

      const fBtns = document.createElement("div");
      fBtns.className = "col-controls";

      const fDel = document.createElement("button");
      fDel.className = "btn ghost icon-btn danger";
      fDel.title = "Remove folder tab";
      fDel.innerHTML = icon("x", 12);
      fDel.disabled = folders.length <= 1;
      fDel.onclick = (e) => {
        e.stopPropagation();
        const rawDoc = ensureInlineCollection(selCol);
        if (Array.isArray(rawDoc.folders) && rawDoc.folders.length > 1) {
          rawDoc.folders.splice(fIdx, 1);
          selectedFolderIdx = Math.max(0, selectedFolderIdx - 1);
          setViewportState({ colIdx: selectedColIdx, folderIdx: selectedFolderIdx });
          ctx.saveSection("profile", true);
        }
      };

      fBtns.appendChild(fDel);
      row.append(leftInfo, fBtns);
      fList.appendChild(row);
    });
    detailBox.appendChild(fList);

    if (isInline && selCol.raw?.folders?.[selectedFolderIdx]) {
      const rawF = selCol.raw.folders[selectedFolderIdx];
      const fEdit = document.createElement("div");
      fEdit.style.cssText = "display:grid;grid-template-columns:1fr;gap:8px;margin-top:10px";

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
      fEdit.append(ftLbl, fcLbl);
      detailBox.appendChild(fEdit);
    }

    left.appendChild(detailBox);
  }

  const diyBtns = document.createElement("div");
  diyBtns.style.marginTop = "12px";
  const modalDiyBtn = document.createElement("button");
  modalDiyBtn.className = "btn accent-outline full-width";
  modalDiyBtn.innerHTML = `${icon("sparkles", 14)} <span>DIY Collection / Folder Builder</span>`;
  modalDiyBtn.onclick = () => openDiyBuilderModal(ctx, p, selCol);
  diyBtns.appendChild(modalDiyBtn);
  left.appendChild(diyBtns);

  leftStack.appendChild(left);

  // Card 2: Curated Starter & Regional Cinema Recipes
  const addCard = document.createElement("div");
  addCard.className = "card";
  addCard.innerHTML = `<h2>${icon("plus", 15)} Add Curated &amp; Regional Shelves</h2>`;

  const usedRefs = new Set(p.collections.filter(c => c.ref).map(c => c.ref));
  const standardCols = (st.libraryCollections || []).filter(
    lc => (!lc.presets || lc.presets.length === 0) && !usedRefs.has(lc.id)
  );
  const grouped = {};
  standardCols.forEach(lc => {
    const cat = lc.category || "other";
    (grouped[cat] = grouped[cat] || []).push(lc);
  });

  if (!Object.keys(grouped).length) {
    addCard.insertAdjacentHTML("beforeend", `<p class="sub" style="margin-bottom:8px">All standard starter recipes are already on your Home screen.</p>`);
  }

  Object.entries(grouped).forEach(([cat, cols]) => {
    const grpLbl = document.createElement("div");
    grpLbl.className = "eyebrow";
    grpLbl.style.cssText = "padding:0;margin:10px 0 6px";
    grpLbl.textContent = CATEGORY_LABELS[cat] || "More Collections";
    addCard.appendChild(grpLbl);

    const addChips = document.createElement("div");
    addChips.className = "chip-row";
    cols.forEach(lc => {
      const btn = document.createElement("button");
      btn.className = "btn icon-btn";
      btn.innerHTML = `${icon("plus", 12)} <span>${escapeHtml(lc.title)}</span>`;
      btn.title = lc.description || lc.id;
      btn.onclick = () => {
        p.collections.push({ ref: lc.id, with: {}, title: lc.title, folders: lc.folders });
        selectedColIdx = p.collections.length - 1;
        selectedFolderIdx = 0;
        setViewportState({ colIdx: selectedColIdx, folderIdx: 0, screen: "home" });
        ctx.saveSection("profile", true);
      };
      addChips.appendChild(btn);
    });
    addCard.appendChild(addChips);
  });

  const regRecipe = (st.libraryCollections || []).find(lc => lc.id === "regional-cinema");
  if (regRecipe && Array.isArray(regRecipe.presets) && regRecipe.presets.length) {
    const regHead = document.createElement("div");
    regHead.className = "eyebrow";
    regHead.style.cssText = "padding:0;margin:12px 0 6px";
    regHead.textContent = "Regional Cinema Builder";
    addCard.appendChild(regHead);

    const regRow = document.createElement("div");
    regRow.style.cssText = "display:flex;gap:8px";
    const sel = document.createElement("select");
    regRecipe.presets.forEach((pr, idx) => {
      const opt = document.createElement("option");
      opt.value = String(idx);
      opt.textContent = `${pr.label} (${pr.with.language})`;
      sel.appendChild(opt);
    });
    const addRegBtn = document.createElement("button");
    addRegBtn.className = "btn accent-outline";
    addRegBtn.style.whiteSpace = "nowrap";
    addRegBtn.innerHTML = `${icon("plus", 13)} <span>Add Language</span>`;
    addRegBtn.onclick = () => {
      const chosen = regRecipe.presets[Number(sel.value)];
      p.collections.push({ ref: "regional-cinema", with: { ...chosen.with }, title: chosen.label, folders: [] });
      selectedColIdx = p.collections.length - 1;
      selectedFolderIdx = 0;
      setViewportState({ colIdx: selectedColIdx, folderIdx: 0, screen: "home" });
      ctx.saveSection("profile", true);
    };
    regRow.append(sel, addRegBtn);
    addCard.appendChild(regRow);
  }
  leftStack.appendChild(addCard);

  // =========================================================================
  // RIGHT COLUMN — Persistent Real-Time Nuvio Viewport Showcase
  // =========================================================================
  const right = document.createElement("div");
  right.className = "card sticky-stage";
  right.innerHTML = `
    <div class="row-between" style="margin-bottom:8px;flex-wrap:wrap">
      <div>
        <div class="card-eyebrow">${icon("tv", 12)} Real-Time Showcase</div>
        <h2 style="margin-bottom:2px">Live Nuvio Layout Preview</h2>
      </div>
      <span class="pill">Click any shelf, poster, or stream</span>
    </div>
  `;

  right.appendChild(
    renderNuvioViewport(ctx, {
      syncSelection: { colIdx: selectedColIdx, folderIdx: selectedFolderIdx },
      onSelectCollection: (cIdx, fIdx) => {
        selectedColIdx = cIdx;
        selectedFolderIdx = fIdx;
      },
    })
  );

  right.tabIndex = 0;
  right.onkeydown = (e) => {
    if (e.target && (e.target.tagName === "INPUT" || e.target.tagName === "SELECT")) return;
    if (e.key === "ArrowRight" && folders.length > 1) {
      selectedFolderIdx = (selectedFolderIdx + 1) % folders.length;
      setViewportState({ colIdx: selectedColIdx, folderIdx: selectedFolderIdx });
      ctx.render();
    } else if (e.key === "ArrowLeft" && folders.length > 1) {
      selectedFolderIdx = (selectedFolderIdx - 1 + folders.length) % folders.length;
      setViewportState({ colIdx: selectedColIdx, folderIdx: selectedFolderIdx });
      ctx.render();
    }
  };

  wrap.append(leftStack, right);
  root.appendChild(wrap);
}

function openDiyBuilderModal(ctx, profile, currentCol) {
  let targetMode = currentCol ? "existing" : "new";
  let newColTitle = "My Custom Shelf";
  let newColBackdrop = "";

  ctx.openModal((dialog) => {
    const { icon } = ctx.ui;
    const st = ctx.state;

    const renderModal = () => {
      dialog.innerHTML = "";
      const head = document.createElement("div");
      head.className = "row-between";
      head.innerHTML = `
        <div>
          <h2>${icon("sparkles", 17)} DIY Collection &amp; Folder Builder</h2>
          <p class="sub" style="margin:3px 0 0">Creates both the AIOMetadata catalog definition and the Nuvio Home folder in one step.</p>
        </div>
      `;
      const closeBtn = document.createElement("button");
      closeBtn.className = "btn ghost icon-btn";
      closeBtn.innerHTML = icon("x", 15);
      closeBtn.onclick = () => ctx.closeModal();
      head.appendChild(closeBtn);
      dialog.appendChild(head);

      const destBox = document.createElement("div");
      destBox.className = "rows-box";
      const destTabs = document.createElement("div");
      destTabs.className = "segmented";
      if (currentCol) {
        const bExist = document.createElement("button");
        bExist.className = "seg-btn" + (targetMode === "existing" ? " active" : "");
        bExist.textContent = `Add Tab to "${currentCol.title}"`;
        bExist.onclick = () => { targetMode = "existing"; renderModal(); };
        destTabs.appendChild(bExist);
      }
      const bNew = document.createElement("button");
      bNew.className = "seg-btn" + (targetMode === "new" ? " active" : "");
      bNew.textContent = "Create Brand-New Shelf";
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

      const srcBox = document.createElement("div");
      srcBox.className = "rows-box";
      const modeTabs = document.createElement("div");
      modeTabs.className = "segmented";
      [
        ["tmdb", "TMDB Discover Filter"],
        ["mdblist", "MDBList URL / ID"],
        ["catalog", "Catalog Picker"],
      ].forEach(([mId, mLbl]) => {
        const b = document.createElement("button");
        b.className = "seg-btn" + (diyMode === mId ? " active" : "");
        b.textContent = mLbl;
        b.onclick = () => { diyMode = mId; renderModal(); };
        modeTabs.appendChild(b);
      });
      srcBox.appendChild(modeTabs);

      const form = document.createElement("div");
      form.style.cssText = "display:grid;grid-template-columns:repeat(auto-fit, minmax(210px, 1fr));gap:10px;margin-top:14px;align-items:end";

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
        setViewportState({ colIdx: selectedColIdx, folderIdx: selectedFolderIdx, screen: "collection" });
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
        addBtn.innerHTML = `${icon("plus", 14)} <span>Build &amp; Save TMDB Folder</span>`;
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
        addBtn.innerHTML = `${icon("plus", 14)} <span>Resolve &amp; Save MDBList</span>`;
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
        newFBtn.innerHTML = `${icon("plus", 14)} <span>Save as New Folder</span>`;
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
          rowBtn.innerHTML = `${icon("plus", 13)} <span>Append Row to Active Folder</span>`;
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
