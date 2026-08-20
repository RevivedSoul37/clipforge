/* ClipForge web UI — campaign-scoped pipeline */
(function () {
  "use strict";

  const $ = (sel) => document.querySelector(sel);

  const els = {
    nav: $("#nav"),
    navCampaign: $("#navCampaign"),
    navPipeline: $("#navPipeline"),
    modelChip: $("#modelChip"),
    serverStatus: $("#serverStatus"),
    serverStatusText: $("#serverStatusText"),
    contextBar: $("#contextBar"),
    videoSelectTop: $("#videoSelectTop"),
    ctxStats: $("#ctxStats"),
    campaignGrid: $("#campaignGrid"),
    newCampaignForm: $("#newCampaignForm"),
    campName: $("#campName"),
    campPlatform: $("#campPlatform"),
    campPayout: $("#campPayout"),
    campDeadline: $("#campDeadline"),
    campDetailName: $("#campDetailName"),
    campDetailMeta: $("#campDetailMeta"),
    campStatus: $("#campStatus"),
    campStatRow: $("#campStatRow"),
    rulesEmpty: $("#rulesEmpty"),
    rulesFilled: $("#rulesFilled"),
    rulesSections: $("#rulesSections"),
    rulesInput: $("#rulesInput"),
    btnViewFullRules: $("#btnViewFullRules"),
    btnNewClip: $("#btnNewClip"),
    kanban: $("#kanban"),
    videoList: $("#videoList"),
    fileInput: $("#fileInput"),
    uploadBtn: $("#uploadBtn"),
    uploadProgress: $("#uploadProgress"),
    uploadFill: $("#uploadFill"),
    uploadPct: $("#uploadPct"),
    ctaAnalyze: $("#ctaAnalyze"),
    btnCtaAnalyze: $("#btnCtaAnalyze"),
    minScore: $("#minScore"),
    maxClips: $("#maxClips"),
    btnAnalyze: $("#btnAnalyze"),
    analyzeStatus: $("#analyzeStatus"),
    ctaReview: $("#ctaReview"),
    btnCtaReview: $("#btnCtaReview"),
    ctaReviewText: $("#ctaReviewText"),
    reviewCount: $("#reviewCount"),
    reviewList: $("#reviewList"),
    reviewEmpty: $("#reviewEmpty"),
    reviewHint: $("#reviewHint"),
    btnApproveAll: $("#btnApproveAll"),
    btnSaveReview: $("#btnSaveReview"),
    ctaExport: $("#ctaExport"),
    btnCtaExport: $("#btnCtaExport"),
    ctaExportText: $("#ctaExportText"),
    templateSelect: $("#templateSelect"),
    templateDesc: $("#templateDesc"),
    btnExport: $("#btnExport"),
    btnRunAll: $("#btnRunAll"),
    musicEnabled: $("#musicEnabled"),
    musicVolume: $("#musicVolume"),
    musicTrack: $("#musicTrack"),
    musicInput: $("#musicInput"),
    brollStatus: $("#brollStatus"),
    outputCount: $("#outputCount"),
    outputList: $("#outputList"),
    btnOpenFolder: $("#btnOpenFolder"),
    exportStatus: $("#exportStatus"),
    styleVideoSelect: $("#styleVideoSelect"),
    framesMode: $("#framesMode"),
    framesNum: $("#framesNum"),
    framesGrid: $("#framesGrid"),
    btnExtractFrames: $("#btnExtractFrames"),
    btnAnalyzeStyle: $("#btnAnalyzeStyle"),
    styleReport: $("#styleReport"),
    styleSheets: $("#styleSheets"),
    styleState: $("#styleState"),
    btnCtaStyleExport: $("#btnCtaStyleExport"),
    runbar: $("#runbar"),
    stageName: $("#stageName"),
    runMsg: $("#runMsg"),
    railFill: $("#railFill"),
    percent: $("#percent"),
    btnCancel: $("#btnCancel"),
    logToggle: $("#logToggle"),
    logConsole: $("#logConsole"),
    toast: $("#toast"),
  };

  const LABELS = {
    transcribe: "Transcribing audio",
    clean: "Fixing transcript typos",
    context: "Building context",
    select: "Finding highlights",
    broll: "Resolving b-roll",
    cut: "Cutting clips",
    render: "Rendering clips",
    frames: "Extracting frames",
    start: "Starting…",
    done: "Finished",
  };

  const PIPELINE_PAGES = ["source", "analyze", "review", "export", "style"];
  const ALL_PAGES = ["dashboard", "campaign"].concat(PIPELINE_PAGES);
  const CLIP_COLS = [
    { key: "analyzing", label: "Analyzing" },
    { key: "reviewing", label: "Reviewing" },
    { key: "exported", label: "Exported" },
    { key: "posted", label: "Posted" },
  ];

  let state = null;
  let campaigns = [];
  let currentCampaignId = null;
  let currentCampaign = null;
  let currentVideoId = null;
  let currentVideo = null;
  let dirty = false;
  let currentRun = null;
  let pollTimer = null;
  let pollFailures = 0;
  let shownLogs = 0;
  const openPreviews = new Map();

  /* ---------------- api ---------------- */
  async function apiGet(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error((await r.text()) || r.status);
    return r.json();
  }
  async function apiSend(url, method, body) {
    const r = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error((await r.text()) || r.status);
    return r.json();
  }
  async function apiPost(url, body) { return apiSend(url, "POST", body); }
  async function apiPatch(url, body) { return apiSend(url, "PATCH", body); }

  function campQ() {
    return currentCampaignId ? "?campaign_id=" + encodeURIComponent(currentCampaignId) : "";
  }

  function toast(msg, kind) {
    els.toast.hidden = false;
    els.toast.textContent = msg;
    els.toast.className = "toast" + (kind ? " " + kind : "");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { els.toast.hidden = true; }, 3200);
  }

  function fmt(sec) {
    sec = Math.max(0, Math.round(sec || 0));
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return m + ":" + String(s).padStart(2, "0");
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  function campaignName() {
    return (currentCampaign && currentCampaign.name) || "Campaign";
  }

  /* ---------------- router ---------------- */
  function parseHash() {
    const raw = (location.hash || "").replace(/^#\/?/, "").replace(/\/+$/, "");
    const parts = raw ? raw.split("/") : [];
    if (!parts.length || parts[0] === "dashboard") {
      return { page: "dashboard", campaignId: null };
    }
    if (parts[0] === "campaign" && parts[1]) {
      const sub = parts[2];
      if (PIPELINE_PAGES.includes(sub)) {
        return { page: sub, campaignId: parts[1] };
      }
      return { page: "campaign", campaignId: parts[1] };
    }
    if (PIPELINE_PAGES.includes(parts[0])) {
      return { page: parts[0], campaignId: currentCampaignId };
    }
    return { page: "dashboard", campaignId: null };
  }

  function currentPage() {
    return parseHash().page;
  }

  function hrefFor(page, campaignId) {
    const cid = campaignId || currentCampaignId;
    if (page === "dashboard" || !cid) return "#/dashboard";
    if (page === "campaign") return "#/campaign/" + encodeURIComponent(cid);
    return "#/campaign/" + encodeURIComponent(cid) + "/" + page;
  }

  function go(page, campaignId) {
    const next = hrefFor(page, campaignId);
    if (location.hash !== next) location.hash = next;
    else renderPage();
  }

  function updateCrumbs() {
    const name = "← " + campaignName();
    const href = hrefFor("campaign");
    document.querySelectorAll(".crumb").forEach((el) => {
      el.textContent = name;
      el.setAttribute("href", href);
    });
  }

  function updateNav() {
    const page = currentPage();
    const open = !!currentCampaignId;
    els.navPipeline.hidden = !open;
    if (open) {
      els.navCampaign.hidden = false;
      els.navCampaign.textContent = campaignName();
      els.navCampaign.href = hrefFor("campaign");
    } else {
      els.navCampaign.hidden = true;
    }
    els.nav.querySelectorAll("a[data-page]").forEach((a) => {
      const p = a.dataset.page;
      a.classList.toggle("active", p === page);
      if (PIPELINE_PAGES.includes(p) || p === "campaign") {
        a.href = hrefFor(p);
      }
    });
  }

  function renderPage() {
    const parsed = parseHash();
    if (parsed.campaignId && parsed.campaignId !== currentCampaignId) {
      openCampaign(parsed.campaignId, { silent: true }).then(() => renderPage());
      return;
    }
    if (!parsed.campaignId && PIPELINE_PAGES.includes(parsed.page)) {
      go("dashboard");
      return;
    }
    const page = parsed.page;
    for (const p of ALL_PAGES) {
      const el = $("#page-" + p);
      if (el) el.hidden = p !== page;
    }
    updateNav();
    updateCrumbs();
    if (page === "dashboard") renderDashboard();
    if (page === "campaign") renderCampaignDetail();
    if (page === "review") renderReview();
    if (page === "export") { renderOutputs(); loadBrollStatus(); }
    if (page === "style") refreshStyleState();
  }

  window.addEventListener("hashchange", renderPage);

  /* ---------------- campaigns ---------------- */
  async function loadCampaigns() {
    const r = await apiGet("/api/campaigns");
    campaigns = r.campaigns || [];
  }

  async function openCampaign(id, opts) {
    if (!id) return;
    currentCampaignId = id;
    try {
      currentCampaign = await apiGet("/api/campaigns/" + encodeURIComponent(id));
    } catch (e) {
      currentCampaign = null;
      currentCampaignId = null;
      if (!opts || !opts.silent) toast("Campaign not found.", "error");
      go("dashboard");
      return;
    }
    currentVideoId = null;
    currentVideo = null;
    await loadState();
    if (state.videos.length) await setVideo(state.videos[0].id);
    updateCrumbs();
    updateNav();
  }

  function renderDashboard() {
    if (!els.campaignGrid) return;
    els.campaignGrid.innerHTML = "";
    if (!campaigns.length) {
      els.campaignGrid.innerHTML = `<div class="empty" style="grid-column:1/-1">No campaigns yet — create one above.</div>`;
      return;
    }
    for (const c of campaigns) {
      const card = document.createElement("button");
      card.type = "button";
      card.className = "card campaign-card";
      const counts = c.clip_counts || {};
      const total = Object.values(counts).reduce((a, b) => a + (b || 0), 0);
      card.innerHTML =
        `<div class="card-head">` +
        `<span class="campaign-card-name">${escapeHtml(c.name)}</span>` +
        `<span class="status-badge status-${escapeHtml(c.status || "active")}">${escapeHtml(c.status || "active")}</span>` +
        `</div>` +
        `<div class="campaign-card-meta">` +
        `<span>${escapeHtml(c.platform || "—")}</span>` +
        `<span>${escapeHtml(c.payout_rate || "—")}</span>` +
        `<span>${c.deadline ? "Due " + escapeHtml(c.deadline) : "No deadline"}</span>` +
        `</div>` +
        `<div class="campaign-card-clips">${total} clip${total === 1 ? "" : "s"}</div>`;
      card.addEventListener("click", () => go("campaign", c.id));
      els.campaignGrid.appendChild(card);
    }
  }

  function renderCampaignDetail() {
    if (!currentCampaign) return;
    els.campDetailName.textContent = currentCampaign.name;
    const bits = [
      currentCampaign.platform,
      currentCampaign.payout_rate,
      currentCampaign.deadline ? "Due " + currentCampaign.deadline : "",
    ].filter(Boolean);
    els.campDetailMeta.textContent = bits.join(" · ");
    els.campStatus.value = currentCampaign.status || "active";
    renderCampStats();
    renderRules();
    renderKanban();
  }

  function renderCampStats() {
    const counts = (currentCampaign && currentCampaign.clip_counts) || {};
    els.campStatRow.innerHTML = CLIP_COLS.map((col) =>
      `<div class="stat-chip">` +
      `<span class="status-badge status-${col.key}">${col.label}</span>` +
      `<b>${counts[col.key] || 0}</b>` +
      `</div>`
    ).join("");
  }

  const RULE_SECTIONS = [
    { key: "content_criteria", label: "Content criteria", kind: "list", cls: "" },
    { key: "brand_safety", label: "Brand safety", kind: "list", cls: "rules-sec-safety" },
    { key: "editing_style", label: "Editing style", kind: "list", cls: "" },
    { key: "submission_requirements", label: "Submission requirements", kind: "text", cls: "rules-sec-submit" },
  ];

  function emptyRules() {
    return {
      content_criteria: [],
      brand_safety: [],
      editing_style: [],
      submission_requirements: "",
      submission_done: false,
    };
  }

  function campaignRules() {
    const raw = currentCampaign && currentCampaign.rules_summary;
    if (raw && typeof raw === "object" && !Array.isArray(raw)) {
      return Object.assign(emptyRules(), raw);
    }
    if (typeof raw === "string" && raw.trim()) {
      return Object.assign(emptyRules(), {
        content_criteria: raw.split("\n").map((s) => s.replace(/^[-•*]\s*/, "").trim()).filter(Boolean),
      });
    }
    return emptyRules();
  }

  function hasRulesContent(rules) {
    if (!rules) return false;
    if ((rules.content_criteria || []).length) return true;
    if ((rules.brand_safety || []).length) return true;
    if ((rules.editing_style || []).length) return true;
    if ((rules.submission_requirements || "").trim()) return true;
    return !!(currentCampaign && currentCampaign.rules_full);
  }

  function renderRules() {
    const rules = campaignRules();
    const has = hasRulesContent(rules);
    els.rulesEmpty.hidden = has;
    els.rulesFilled.hidden = !has;
    if (!has) return;
    els.rulesSections.innerHTML = "";
    for (const sec of RULE_SECTIONS) {
      els.rulesSections.appendChild(rulesSectionEl(sec, rules));
    }
    if (currentCampaign && currentCampaign.rules_full) {
      els.btnViewFullRules.hidden = false;
      els.btnViewFullRules.href = "/api/campaigns/" + encodeURIComponent(currentCampaignId) + "/rules/file";
    } else {
      els.btnViewFullRules.hidden = true;
    }
  }

  function rulesSectionEl(sec, rules) {
    const wrap = document.createElement("div");
    wrap.className = "rules-sec " + (sec.cls || "");
    const head = document.createElement("div");
    head.className = "rules-sec-head";
    const title = document.createElement("h4");
    title.textContent = (sec.key === "brand_safety" ? "⚠ " : "") + sec.label;
    const editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.className = "link";
    editBtn.textContent = "Edit";
    head.appendChild(title);
    head.appendChild(editBtn);
    wrap.appendChild(head);

    const view = document.createElement("div");
    view.className = "rules-sec-view";
    const editor = document.createElement("div");
    editor.className = "rules-sec-edit";
    editor.hidden = true;

    const ta = document.createElement("textarea");
    ta.className = "rules-input";
    ta.rows = sec.kind === "text" ? 4 : 5;
    const saveBtn = document.createElement("button");
    saveBtn.type = "button";
    saveBtn.className = "btn btn-small btn-primary";
    saveBtn.textContent = "Save";
    const cancelBtn = document.createElement("button");
    cancelBtn.type = "button";
    cancelBtn.className = "btn btn-small btn-ghost";
    cancelBtn.textContent = "Cancel";
    const actions = document.createElement("div");
    actions.className = "rules-actions";
    actions.appendChild(saveBtn);
    actions.appendChild(cancelBtn);
    editor.appendChild(ta);
    editor.appendChild(actions);

    function fillView() {
      view.innerHTML = "";
      if (sec.kind === "list") {
        const items = rules[sec.key] || [];
        if (!items.length) {
          view.innerHTML = `<p class="hint">None listed.</p>`;
        } else {
          const ul = document.createElement("ul");
          ul.className = "rules-bullets";
          for (const item of items) {
            const li = document.createElement("li");
            li.textContent = item;
            ul.appendChild(li);
          }
          view.appendChild(ul);
        }
      } else {
        const text = (rules.submission_requirements || "").trim();
        const p = document.createElement("p");
        p.className = "rules-submit-text";
        p.textContent = text || "No submission obligations found in the brief.";
        view.appendChild(p);
        const toggle = document.createElement("label");
        toggle.className = "toggle-row";
        toggle.innerHTML = `<input type="checkbox"${rules.submission_done ? " checked" : ""}><span>Marked submitted</span>`;
        toggle.querySelector("input").addEventListener("change", async (ev) => {
          try {
            const r = await apiPatch(
              "/api/campaigns/" + encodeURIComponent(currentCampaignId) + "/rules",
              { section: "submission_done", value: ev.target.checked }
            );
            currentCampaign.rules_summary = r.rules_summary;
            renderRules();
          } catch (e) {
            toast("Could not update: " + e.message, "error");
            ev.target.checked = !ev.target.checked;
          }
        });
        view.appendChild(toggle);
      }
    }

    fillView();
    editBtn.addEventListener("click", () => {
      const val = sec.kind === "list"
        ? (rules[sec.key] || []).join("\n")
        : (rules.submission_requirements || "");
      ta.value = val;
      view.hidden = true;
      editor.hidden = false;
      editBtn.hidden = true;
      ta.focus();
    });
    cancelBtn.addEventListener("click", () => {
      editor.hidden = true;
      view.hidden = false;
      editBtn.hidden = false;
    });
    saveBtn.addEventListener("click", async () => {
      const value = sec.kind === "list"
        ? ta.value.split("\n").map((s) => s.replace(/^[-•*]\s*/, "").trim()).filter(Boolean)
        : ta.value;
      try {
        const r = await apiPatch(
          "/api/campaigns/" + encodeURIComponent(currentCampaignId) + "/rules",
          { section: sec.key, value }
        );
        currentCampaign.rules_summary = r.rules_summary;
        renderRules();
        toast("Saved " + sec.label + ".", "ok");
      } catch (e) {
        toast("Could not save: " + e.message, "error");
      }
    });

    wrap.appendChild(view);
    wrap.appendChild(editor);
    return wrap;
  }

  function renderKanban() {
    const clips = (currentCampaign && currentCampaign.clips) || [];
    els.kanban.innerHTML = "";
    for (const col of CLIP_COLS) {
      const colEl = document.createElement("div");
      colEl.className = "kanban-col";
      const items = clips.filter((c) => c.status === col.key);
      colEl.innerHTML = `<h3 class="eyebrow">${col.label} <span class="count">${items.length}</span></h3>`;
      const list = document.createElement("div");
      list.className = "kanban-list";
      if (!items.length) {
        list.innerHTML = `<div class="empty">None</div>`;
      } else {
        for (const clip of items) {
          list.appendChild(kanbanCard(clip));
        }
      }
      colEl.appendChild(list);
      els.kanban.appendChild(colEl);
    }
  }

  function kanbanCard(clip) {
    const card = document.createElement("div");
    card.className = "card kanban-card";
    const dur = (clip.end || 0) - (clip.start || 0);
    const title = clip.title || "Untitled clip";
    const sel = CLIP_COLS.map((c) =>
      `<option value="${c.key}"${c.key === clip.status ? " selected" : ""}>${c.label}</option>`
    ).join("");
    card.innerHTML =
      `<div class="kanban-title">${escapeHtml(title)}</div>` +
      `<div class="kanban-meta">${escapeHtml(clip.video_id || "")}` +
      (dur > 0 ? ` · ${fmt(dur)}` : "") + `</div>` +
      `<div class="kanban-actions">` +
      `<div class="select-wrap"><select class="kanban-status">${sel}</select></div>` +
      `</div>`;
    const actions = card.querySelector(".kanban-actions");
    if (clip.status === "reviewing" && clip.video_id && !clip.placeholder) {
      const link = document.createElement("button");
      link.className = "link";
      link.textContent = "Open in Review";
      link.addEventListener("click", async () => {
        await setVideo(clip.video_id);
        go("review");
      });
      actions.appendChild(link);
    }
    if (clip.status === "exported") {
      const btn = document.createElement("button");
      btn.className = "btn btn-small btn-primary";
      btn.textContent = "Mark as posted";
      btn.addEventListener("click", () => patchClipStatus(clip.id, "posted"));
      actions.appendChild(btn);
    }
    card.querySelector(".kanban-status").addEventListener("change", (ev) => {
      patchClipStatus(clip.id, ev.target.value);
    });
    return card;
  }

  async function patchClipStatus(clipId, status) {
    try {
      await apiPatch(
        "/api/campaigns/" + encodeURIComponent(currentCampaignId) + "/clips/" + encodeURIComponent(clipId),
        { status }
      );
      currentCampaign = await apiGet("/api/campaigns/" + encodeURIComponent(currentCampaignId));
      await loadCampaigns();
      renderCampaignDetail();
    } catch (e) {
      toast("Could not update clip: " + e.message, "error");
    }
  }

  /* ---------------- state ---------------- */
  async function loadState() {
    state = await apiGet("/api/state" + campQ());
    els.modelChip.textContent = state.config.llm_model + " · " + state.config.whisper_model;
    els.minScore.value = state.config.min_score;
    els.maxClips.value = state.config.max_clips;
    renderVideoSelects();
    renderSourceList();
    renderTemplates();
    renderContextBar();
  }

  function renderVideoSelects() {
    const options = state && state.videos.length
      ? state.videos.map((v) => `<option value="${escapeHtml(v.id)}">${escapeHtml(v.name)}</option>`).join("")
      : `<option value="">No videos yet</option>`;
    for (const sel of [els.videoSelectTop, els.styleVideoSelect]) {
      sel.innerHTML = options;
      if (currentVideoId && state.videos.some((v) => v.id === currentVideoId)) {
        sel.value = currentVideoId;
      }
    }
  }

  function renderContextBar() {
    if (!currentCampaignId || !currentVideoId || !state || !state.videos.length) {
      els.contextBar.hidden = true;
      return;
    }
    if (currentPage() === "dashboard" || currentPage() === "campaign") {
      els.contextBar.hidden = true;
      return;
    }
    els.contextBar.hidden = false;
    const parts = [];
    if (currentVideo) {
      const cand = currentVideo.candidates;
      if (currentVideo.transcript_segments && currentVideo.transcript_segments.length) {
        parts.push("✓ analyzed");
      } else {
        parts.push("not analyzed");
      }
      if (cand && cand.clips && cand.clips.length) {
        const approved = cand.clips.filter((c) => c.status === "approved").length;
        parts.push(`${approved}/${cand.clips.length} approved`);
      }
      if (currentVideo.outputs && currentVideo.outputs.length) {
        parts.push(`${currentVideo.outputs.length} exported`);
      }
    }
    els.ctxStats.textContent = parts.join(" · ");
  }

  /* ---------------- source ---------------- */
  function renderSourceList() {
    els.videoList.innerHTML = "";
    if (!state || !state.videos.length) {
      els.videoList.innerHTML = `<div class="empty">No videos yet — upload one on the right.</div>`;
      els.ctaAnalyze.hidden = true;
      return;
    }
    for (const v of state.videos) {
      const item = document.createElement("div");
      item.className = "video-item" + (v.id === currentVideoId ? " selected" : "");
      const mb = (v.size / 1048576).toFixed(v.size > 104857600 ? 0 : 1);
      item.innerHTML =
        `<span class="v-name">${escapeHtml(v.name)}</span><span class="v-size">${mb} MB</span>` +
        `<button class="v-del" title="Delete source video" aria-label="Delete ${escapeHtml(v.name)}">&#10005;</button>`;
      item.addEventListener("click", () => setVideo(v.id));
      item.querySelector(".v-del").addEventListener("click", (ev) => {
        ev.stopPropagation();
        deleteVideo(v);
      });
      els.videoList.appendChild(item);
    }
    const hasVideo = !!currentVideoId && state.videos.some((v) => v.id === currentVideoId);
    els.ctaAnalyze.hidden = !hasVideo;
  }

  async function deleteVideo(v) {
    if (!confirm(`Delete source video "${v.name}" from this campaign?\n\n` +
                 `Only the source file is removed — transcripts, candidates ` +
                 `and already-exported clips are kept.`)) return;
    try {
      const r = await fetch("/api/video/" + encodeURIComponent(v.id) + campQ(), { method: "POST" });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.error || r.status);
      toast(`Deleted ${v.name}.`, "ok");
      if (currentVideoId === v.id) { currentVideoId = null; currentVideo = null; }
      await loadState();
      if (state.videos.length) await setVideo(state.videos[0].id);
    } catch (e) {
      toast("Delete failed: " + e.message, "error");
    }
  }

  async function setVideo(id) {
    if (!id) return;
    currentVideoId = id;
    for (const sel of [els.videoSelectTop, els.styleVideoSelect]) {
      if (Array.from(sel.options).some((o) => o.value === id)) sel.value = id;
    }
    dirty = false;
    openPreviews.clear();
    try {
      currentVideo = await apiGet("/api/video/" + encodeURIComponent(id) + campQ());
    } catch (e) {
      currentVideo = null;
      toast("Could not load video details: " + e.message, "error");
    }
    renderSourceList();
    renderReview();
    renderOutputs();
    renderContextBar();
    renderPipelineStatus();
    updateReviewHint();
  }

  /* ---------------- pipeline status ---------------- */
  function renderPipelineStatus() {
    if (els.analyzeStatus) {
      const analyzed = currentVideo && currentVideo.transcript_segments
        && currentVideo.transcript_segments.length;
      const cand = currentVideo && currentVideo.candidates;
      if (analyzed && cand && cand.clips && cand.clips.length) {
        const pending = cand.clips.filter((c) => c.status !== "approved" && c.status !== "rejected").length;
        els.analyzeStatus.hidden = false;
        els.analyzeStatus.className = "panel status-panel ok";
        els.analyzeStatus.innerHTML =
          `<b>✓ Analyzed</b> — ${cand.clips.length} candidate clip(s) found.` +
          (pending ? ` ${pending} still awaiting your decision.` : " All reviewed.");
      } else if (analyzed) {
        els.analyzeStatus.hidden = false;
        els.analyzeStatus.className = "panel status-panel warn";
        els.analyzeStatus.innerHTML = "<b>Analyzed</b> — but no candidates survived. Try lowering the min score.";
      } else {
        els.analyzeStatus.hidden = true;
      }
      const hasCand = cand && cand.clips && cand.clips.length;
      els.ctaReview.hidden = !hasCand;
      if (hasCand) {
        els.ctaReviewText.textContent =
          `${cand.clips.length} candidate clip(s) found — approve the ones you want.`;
      }
    }

    if (els.ctaExport) {
      const approved = approvedCount();
      els.ctaExport.hidden = approved === 0;
      if (approved > 0) {
        els.ctaExportText.textContent = `${approved} clip(s) approved — ready to export.`;
      }
    }

    if (els.exportStatus) {
      const approved = approvedCount();
      if (currentVideo && currentVideo.outputs && currentVideo.outputs.length) {
        els.exportStatus.hidden = false;
        els.exportStatus.className = "panel status-panel ok";
        els.exportStatus.innerHTML =
          `<b>✓ Exported</b> — ${currentVideo.outputs.length} clip(s) rendered.`;
      } else if (approved) {
        els.exportStatus.hidden = false;
        els.exportStatus.className = "panel status-panel info";
        els.exportStatus.innerHTML =
          `<b>Ready</b> — ${approved} approved clip(s) waiting to be exported below.`;
      } else {
        els.exportStatus.hidden = true;
      }
    }
  }

  /* ---------------- analyze ---------------- */
  async function startRun(mode, auto, extra) {
    if (!currentCampaignId) { toast("Open a campaign first.", "error"); go("dashboard"); return; }
    if (!currentVideoId) { toast("Pick or upload a video first (Source page).", "error"); go("source"); return; }
    if (dirty && !confirm("You have unsaved review decisions. A new run may overwrite them. Continue?")) return;
    dirty = false; updateReviewHint();

    const body = Object.assign({
      mode, video: currentVideoId,
      campaign_id: currentCampaignId,
      template: els.templateSelect.value,
      min_score: parseFloat(els.minScore.value),
      max_clips: parseInt(els.maxClips.value),
      auto: !!auto,
    }, extra || {});
    clearLogs();
    try {
      const res = await apiPost("/api/run", body);
      currentRun = res.run;
      setRunbar(true);
      updateProgress(0, "start");
      poll(() => { refreshAfterRun(); });
    } catch (e) {
      toast("Failed to start: " + e.message, "error");
    }
  }

  /* ---------------- review ---------------- */
  function updateReviewHint() {
    els.reviewHint.textContent = dirty
      ? "Unsaved changes — press Save decisions to keep them."
      : "Decisions stay unsaved until you press Save.";
    els.reviewHint.classList.toggle("dirty", dirty);
  }

  function approvedCount() {
    if (!currentVideo || !currentVideo.candidates) return 0;
    return currentVideo.candidates.clips.filter((c) => c.status === "approved").length;
  }

  function snippetFor(clip) {
    if (!currentVideo || !currentVideo.transcript_segments) return "No transcript available.";
    const parts = currentVideo.transcript_segments
      .filter((s) => s.end > clip.start && s.start < clip.end)
      .map((s) => "[" + fmt(s.start) + "] " + s.text);
    const text = parts.join("\n").trim();
    return text || "No speech in this range.";
  }

  function clipKey(clip) { return clip.start + ":" + clip.end; }

  function renderBrollCues(clip) {
    const wrap = document.createElement("div");
    wrap.className = "broll-cues";
    for (const c of clip.broll) {
      const pill = document.createElement("span");
      pill.className = "broll-cue-pill";
      pill.innerHTML =
        `<span class="cue-emo">${escapeHtml(c.emotion || "b-roll")}</span>` +
        `<span class="cue-time">${fmt(c.start)}–${fmt(c.end)}</span>`;
      wrap.appendChild(pill);
    }
    return wrap;
  }

  function renderReview() {
    const cand = currentVideo && currentVideo.candidates;
    els.reviewList.innerHTML = "";
    if (!cand || !cand.clips.length) {
      els.reviewCount.textContent = "";
      els.reviewEmpty.hidden = false;
      renderPipelineStatus();
      return;
    }
    els.reviewEmpty.hidden = true;
    const clips = cand.clips;
    const approved = clips.filter((c) => c.status === "approved").length;
    els.reviewCount.textContent = `(${clips.length} found · ${approved} approved)`;

    clips.forEach((clip, i) => {
      const card = document.createElement("div");
      card.className = "card " + (clip.status || "pending");

      const head = document.createElement("div");
      head.className = "card-head";
      head.innerHTML =
        `<span class="badge">#${i + 1}</span>` +
        `<span class="badge score">${clip.score.toFixed(2)}</span>` +
        `<span class="card-reason">${escapeHtml(clip.reason || "")}</span>`;
      card.appendChild(head);

      const times = document.createElement("div");
      times.className = "card-times";
      const rangeLabel = document.createTextNode(`→ ${fmt(clip.start)} – ${fmt(clip.end)}`);
      const mk = (label, key) => {
        const lab = document.createElement("label");
        lab.textContent = label + " ";
        const inp = document.createElement("input");
        inp.type = "number"; inp.step = "0.5"; inp.min = "0"; inp.value = clip[key];
        inp.addEventListener("change", () => {
          clip[key] = parseFloat(inp.value) || 0;
          rangeLabel.textContent = `→ ${fmt(clip.start)} – ${fmt(clip.end)}`;
          dirty = true; updateReviewHint();
        });
        lab.appendChild(inp);
        return lab;
      };
      times.appendChild(mk("Start (s)", "start"));
      times.appendChild(mk("End (s)", "end"));
      times.appendChild(rangeLabel);
      card.appendChild(times);

      const snip = document.createElement("pre");
      snip.className = "snippet";
      snip.textContent = snippetFor(clip);
      card.appendChild(snip);

      if (Array.isArray(clip.broll) && clip.broll.length) {
        card.appendChild(renderBrollCues(clip));
      }

      const hookRow = document.createElement("div");
      hookRow.className = "hook-row";
      const hookLab = document.createElement("label");
      hookLab.textContent = "Hook title (burned on top of the clip)";
      const hookInp = document.createElement("input");
      hookInp.type = "text";
      hookInp.value = clip.hook || "";
      hookInp.placeholder = "Leave empty for no hook line";
      hookInp.addEventListener("change", () => { clip.hook = hookInp.value; dirty = true; updateReviewHint(); });
      hookRow.appendChild(hookLab);
      hookRow.appendChild(hookInp);
      card.appendChild(hookRow);

      const actions = document.createElement("div");
      actions.className = "card-actions";
      const bApprove = document.createElement("button");
      bApprove.className = "btn-approve" + (clip.status === "approved" ? " on" : "");
      bApprove.textContent = clip.status === "approved" ? "✓ Approved" : "Approve";
      bApprove.addEventListener("click", () => {
        clip.status = clip.status === "approved" ? "pending" : "approved";
        dirty = true; renderReview();
      });
      const bReject = document.createElement("button");
      bReject.className = "btn-reject" + (clip.status === "rejected" ? " on" : "");
      bReject.textContent = clip.status === "rejected" ? "✕ Rejected" : "Reject";
      bReject.addEventListener("click", () => {
        clip.status = clip.status === "rejected" ? "pending" : "rejected";
        dirty = true; renderReview();
      });
      const bPreview = document.createElement("button");
      bPreview.className = "btn-preview";
      bPreview.textContent = "▶ Preview";
      bPreview.addEventListener("click", () => previewClip(clip, card, bPreview));
      actions.appendChild(bApprove);
      actions.appendChild(bReject);
      actions.appendChild(bPreview);
      card.appendChild(actions);

      const slot = document.createElement("div");
      slot.className = "preview-slot";
      slot.hidden = true;
      card.appendChild(slot);
      restorePreview(clip, card, bPreview);

      els.reviewList.appendChild(card);
    });
    renderPipelineStatus();
  }

  /* ---------------- preview ---------------- */
  function restorePreview(clip, card, button) {
    const slot = card.querySelector(".preview-slot");
    const url = openPreviews.get(clipKey(clip));
    if (!url || !slot) return;
    const vid = document.createElement("video");
    vid.controls = true; vid.preload = "metadata"; vid.src = url;
    slot.appendChild(vid);
    slot.hidden = false;
    button.textContent = "■ Hide preview";
  }

  async function previewClip(clip, card, button) {
    const slot = card.querySelector(".preview-slot");
    if (!currentVideoId) return;
    if (!slot.hidden) {
      slot.hidden = true;
      openPreviews.delete(clipKey(clip));
      const vid = slot.querySelector("video");
      if (vid) vid.pause();
      button.textContent = "▶ Preview";
      slot.innerHTML = "";
      return;
    }
    button.disabled = true;
    button.textContent = "Cutting preview…";
    try {
      const res = await apiPost("/api/preview", {
        video: currentVideoId, start: clip.start, end: clip.end,
        campaign_id: currentCampaignId,
      });
      openPreviews.set(clipKey(clip), res.url);
      slot.innerHTML = "";
      const vid = document.createElement("video");
      vid.controls = true; vid.preload = "metadata"; vid.src = res.url;
      slot.appendChild(vid);
      slot.hidden = false;
      vid.play().catch(() => {});
      button.textContent = "■ Hide preview";
    } catch (e) {
      toast("Preview failed: " + e.message, "error");
      button.textContent = "▶ Preview";
    } finally {
      button.disabled = false;
    }
  }

  /* ---------------- outputs ---------------- */
  function renderOutputs() {
    const outs = currentVideo ? currentVideo.outputs : [];
    els.outputList.innerHTML = "";
    els.outputCount.textContent = outs.length ? `(${outs.length})` : "";
    if (!outs.length) {
      els.outputList.innerHTML = `<div class="empty" style="grid-column:1/-1">Nothing exported yet for this video.</div>`;
      return;
    }
    for (const o of outs) {
      const card = document.createElement("div");
      card.className = "card output-card";
      card.innerHTML =
        `<video controls preload="none" src="${o.url}"></video>` +
        `<div class="output-meta">` +
        `<span class="output-name" title="${escapeHtml(o.name)}">${escapeHtml(o.name)}</span>` +
        `<a href="${o.url}" download>Download</a>` +
        `</div>`;
      els.outputList.appendChild(card);
    }
  }

  async function loadBrollStatus() {
    try {
      const b = await apiGet("/api/broll");
      els.brollStatus.innerHTML = "";
      for (const [emo, n] of Object.entries(b.emotions)) {
        const pill = document.createElement("span");
        pill.className = "broll-pill" + (n > 0 ? " has" : "");
        pill.textContent = `${emo} ${n}`;
        els.brollStatus.appendChild(pill);
      }
      const note = document.createElement("span");
      note.className = "broll-pill";
      note.textContent = b.total
        ? (b.providers.pexels || b.providers.pixabay ? "stock keys set" : "local library")
        : "empty — set PEXELS_API_KEY & run broll fetch";
      els.brollStatus.appendChild(note);
    } catch (e) { /* optional */ }
  }

  /* ---------------- runs ---------------- */
  function setRunbar(active) {
    els.runbar.hidden = !active;
    if (!active) els.logConsole.hidden = true;
  }

  function updateProgress(percent, stage) {
    const p = Math.max(0, Math.min(100, percent || 0));
    els.railFill.style.width = p + "%";
    els.percent.textContent = Math.round(p) + "%";
    els.stageName.textContent = LABELS[stage] || stage || "Working";
  }

  async function poll(onDone) {
    try {
      const r = await fetch("/api/run/" + currentRun + "?since=" + shownLogs);
      if (r.status === 404) {
        currentRun = null; pollTimer = null; pollFailures = 0;
        setRunbar(false);
        toast("Run lost - the backend restarted. Start the run again.", "error");
        return;
      }
      if (!r.ok) throw new Error((await r.text()) || r.status);
      const run = await r.json();
      pollFailures = 0;
      updateProgress(run.percent, run.stage);
      els.runMsg.textContent = run.message || "";
      if (run.logs && run.logs.length) {
        appendLogs(run.logs, run.log_dropped || 0);
        shownLogs = run.log_index;
      }
      if (run.status === "ok" || run.status === "error" || run.status === "cancelled") {
        if (onDone) onDone(run.status);
        currentRun = null;
        pollTimer = null;
        setRunbar(false);
        if (run.status === "ok") toast("Done.", "ok");
        else if (run.status === "error") toast("Run failed: " + (run.error || "see log"), "error");
        return;
      }
      pollTimer = setTimeout(poll, 1200, onDone);
    } catch (e) {
      pollFailures += 1;
      if (pollFailures > 5) {
        currentRun = null;
        setRunbar(false);
        toast("Lost connection to the run.", "error");
        return;
      }
      pollTimer = setTimeout(poll, 2000, onDone);
    }
  }

  async function refreshAfterRun() {
    try {
      await loadState();
      if (currentVideoId) await setVideo(currentVideoId);
      if (currentCampaignId) {
        currentCampaign = await apiGet("/api/campaigns/" + encodeURIComponent(currentCampaignId));
        await loadCampaigns();
      }
    } catch (e) { /* ignore */ }
  }

  /* ---------------- logs ---------------- */
  function appendLogs(logs, dropped) {
    if (dropped > 0) {
      const note = document.createElement("div");
      note.className = "hl";
      note.textContent = `[trimmed ${dropped} earlier line(s)]`;
      els.logConsole.appendChild(note);
    }
    for (const line of logs) {
      const span = document.createElement("div");
      span.textContent = line;
      if (/error|traceback|failed|exception|cannot|not found/i.test(line)) span.className = "err";
      els.logConsole.appendChild(span);
    }
    while (els.logConsole.children.length > 1200) els.logConsole.removeChild(els.logConsole.firstChild);
    els.logConsole.scrollTop = els.logConsole.scrollHeight;
  }

  function clearLogs() {
    els.logConsole.innerHTML = "";
    shownLogs = 0;
  }

  /* ---------------- upload ---------------- */
  function uploadFile(file) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/upload");
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
          const pct = Math.round((e.loaded / e.total) * 100);
          els.uploadFill.style.width = pct + "%";
          els.uploadPct.textContent = pct + "%";
        }
      };
      xhr.onload = () => {
        let data = null;
        try { data = JSON.parse(xhr.responseText); } catch (e) { /* not json */ }
        if (xhr.status >= 200 && xhr.status < 300) resolve(data);
        else reject(new Error((data && data.error) || xhr.responseText || xhr.status));
      };
      xhr.onerror = () => reject(new Error("Network error during upload."));
      const fd = new FormData();
      fd.append("file", file);
      if (currentCampaignId) fd.append("campaign_id", currentCampaignId);
      xhr.send(fd);
    });
  }

  /* ---------------- style lab ---------------- */
  function styleVideoId() {
    return els.styleVideoSelect.value || currentVideoId;
  }

  async function refreshStyleState() {
    const id = styleVideoId();
    if (!id) { els.btnAnalyzeStyle.disabled = true; return; }
    try {
      const r = await apiGet("/api/frames" + campQ());
      const set = r.frame_sets.find((s) => s.stem === id);
      const hasFrames = !!(set && set.frames);
      els.btnAnalyzeStyle.disabled = !hasFrames;
      els.styleState.textContent = hasFrames
        ? `${set.frames} frame(s) extracted (${set.mode || "uniform"} mode).`
        : "No frames yet — extract them in step 2.";
      renderStyleSheets(set);
      if (set && set.has_report) {
        await loadStyleReport(id);
        els.btnCtaStyleExport.hidden = false;
      } else {
        els.btnCtaStyleExport.hidden = true;
      }
    } catch (e) { /* frames api unavailable */ }
  }

  function renderStyleSheets(set) {
    els.styleSheets.innerHTML = "";
    if (!set || !set.sheets || !set.sheets.length) {
      els.styleSheets.innerHTML = `<div class="empty" style="grid-column:1/-1">Extract frames with a contact sheet to preview them here.</div>`;
      return;
    }
    for (const sheet of set.sheets) {
      const card = document.createElement("div");
      card.className = "card style-sheet-card";
      const q = campQ();
      const join = q ? "&" : "?";
      card.innerHTML =
        `<img src="/api/frames/${encodeURIComponent(set.stem)}/media${q}${join}file=${encodeURIComponent(sheet)}" alt="contact sheet">`;
      els.styleSheets.appendChild(card);
    }
  }

  async function loadStyleReport(stem) {
    try {
      const r = await apiGet("/api/frames/" + encodeURIComponent(stem) + "/style" + campQ());
      const rep = r.report || {};
      const row = (label, val) => `<div class="style-row"><span>${label}</span><b>${val || "—"}</b></div>`;
      const saved = r.template
        ? `<p class="hint ok-note">✓ Draft template saved to this campaign — select it as the Style on the Export page.</p>`
        : "";
      els.styleReport.innerHTML =
        `<p class="hint">Analyzed ${rep.frames_analyzed || 0} frames of <b>${escapeHtml(rep.stem || stem)}</b>.</p>` +
        row("Layout", rep.layout) +
        row("Band fill", rep.band_fill_median ? Math.round(rep.band_fill_median * 100) + "%" : null) +
        row("Hook color", (rep.hook || {}).median_hex) +
        row("Caption color", (rep.captions || {}).median_hex) +
        row("Keyword color", (rep.captions || {}).keyword_hex) +
        row("CTA color", (rep.cta || {}).median_hex) +
        saved;
    } catch (e) {
      els.styleReport.innerHTML = `<p class="hint">No analysis yet.</p>`;
    }
  }

  els.styleVideoSelect.addEventListener("change", refreshStyleState);
  els.btnExtractFrames.addEventListener("click", () => {
    const id = styleVideoId();
    if (!id) { toast("Pick a reference video first.", "error"); return; }
    startStyleRun("frames", id);
  });
  els.btnAnalyzeStyle.addEventListener("click", () => {
    const id = styleVideoId();
    if (!id) { toast("Pick a reference video first.", "error"); return; }
    startStyleRun("style", id);
  });

  async function startStyleRun(mode, id) {
    const body = {
      mode, video: id,
      campaign_id: currentCampaignId,
      frames_mode: els.framesMode.value,
      num: parseInt(els.framesNum.value) || 12,
      grid: els.framesGrid.value || undefined,
      name: id + "_style",
    };
    clearLogs();
    try {
      const res = await apiPost("/api/run", body);
      currentRun = res.run;
      setRunbar(true);
      updateProgress(0, "start");
      poll(() => { refreshStyleState(); loadState(); });
    } catch (e) {
      toast("Failed to start: " + e.message, "error");
    }
  }

  /* ---------------- templates ---------------- */
  function renderTemplates() {
    const tpls = (state && state.templates) || [];
    els.templateSelect.innerHTML = tpls.map((t) =>
      `<option value="${escapeHtml(t.name)}">${escapeHtml(t.label || t.name)}</option>`).join("");
    if (state && state.config && state.config.default_template) {
      const match = tpls.find((t) => t.name === state.config.default_template);
      if (match) els.templateSelect.value = match.name;
    }
    updateTemplateDesc();
  }

  function updateTemplateDesc() {
    const tpls = (state && state.templates) || [];
    const t = tpls.find((x) => x.name === els.templateSelect.value);
    els.templateDesc.textContent = t ? (t.description || "") : "";
  }

  /* ---------------- events ---------------- */
  els.videoSelectTop.addEventListener("change", () => setVideo(els.videoSelectTop.value));
  els.btnCtaAnalyze.addEventListener("click", () => { go("analyze"); });
  els.btnCtaReview.addEventListener("click", () => { go("review"); });
  els.btnCtaExport.addEventListener("click", () => { go("export"); });
  els.btnCtaStyleExport.addEventListener("click", () => { go("export"); });
  els.btnNewClip.addEventListener("click", () => { go("source"); });

  els.templateSelect.addEventListener("change", updateTemplateDesc);
  els.btnAnalyze.addEventListener("click", () => startRun("analyze", false));
  els.btnExport.addEventListener("click", () => startRun("export", false));
  els.btnRunAll.addEventListener("click", () => startRun("pipeline", true));

  els.btnCancel.addEventListener("click", async () => {
    if (!currentRun) return;
    try {
      await fetch("/api/run/" + currentRun + "/cancel", { method: "POST" });
      toast("Cancelling…");
    } catch (e) {
      toast("Couldn't reach the backend to cancel.", "error");
    }
  });

  els.logToggle.addEventListener("click", () => {
    els.logConsole.hidden = !els.logConsole.hidden;
    els.logToggle.textContent = els.logConsole.hidden ? "Log" : "Hide log";
  });

  els.btnApproveAll.addEventListener("click", () => {
    if (!currentVideo || !currentVideo.candidates) return;
    for (const c of currentVideo.candidates.clips) c.status = "approved";
    dirty = true;
    renderReview();
  });

  els.btnSaveReview.addEventListener("click", async () => {
    if (!currentVideo || !currentVideo.candidates) return;
    try {
      await apiPost("/api/candidates", {
        video_id: currentVideo.candidates.video_id,
        clips: currentVideo.candidates.clips,
        campaign_id: currentCampaignId,
      });
      dirty = false;
      updateReviewHint();
      toast("Decisions saved.", "ok");
    } catch (e) {
      toast("Save failed: " + e.message, "error");
    }
  });

  for (const el of [els.musicEnabled, els.musicVolume, els.musicTrack]) {
    el.addEventListener("change", async () => {
      try {
        await apiPost("/api/music", {
          enabled: els.musicEnabled.checked,
          volume: parseFloat(els.musicVolume.value),
          track: els.musicTrack.value,
        });
      } catch (e) {
        toast("Could not save music settings: " + e.message, "error");
      }
    });
  }

  els.musicInput.addEventListener("change", async () => {
    const file = els.musicInput.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    try {
      const r = await fetch("/api/music/upload", { method: "POST", body: fd });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || r.status);
      toast("Added " + data.name + ".", "ok");
      await loadMusic();
    } catch (e) {
      toast("Upload failed: " + e.message, "error");
    } finally {
      els.musicInput.value = "";
    }
  });

  els.btnOpenFolder.addEventListener("click", async () => {
    const dir = state ? state.config.output_dir : "";
    try {
      await fetch("/api/open-folder" + campQ() + (campQ() ? "&" : "?") + "dir=output", { method: "POST" });
    } catch (e) {
      toast("Output folder: " + dir);
      if (navigator.clipboard) navigator.clipboard.writeText(dir).catch(() => {});
    }
  });

  els.fileInput.addEventListener("change", async () => {
    const file = els.fileInput.files[0];
    if (!file) return;
    els.uploadProgress.hidden = false;
    els.uploadFill.style.width = "0%";
    els.uploadPct.textContent = "0%";
    try {
      const res = await uploadFile(file);
      els.uploadFill.style.width = "100%";
      els.uploadPct.textContent = "100%";
      await loadState();
      await setVideo(res.id);
      toast("Added " + res.name + ".", "ok");
    } catch (e) {
      toast("Upload failed: " + e.message, "error");
    } finally {
      els.fileInput.value = "";
      setTimeout(() => { els.uploadProgress.hidden = true; }, 1200);
    }
  });

  els.newCampaignForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const name = els.campName.value.trim();
    if (!name) return;
    try {
      const camp = await apiPost("/api/campaigns", {
        name,
        platform: els.campPlatform.value.trim(),
        payout_rate: els.campPayout.value.trim(),
        deadline: els.campDeadline.value,
      });
      els.newCampaignForm.reset();
      await loadCampaigns();
      toast("Campaign created.", "ok");
      go("campaign", camp.id);
    } catch (e) {
      toast("Could not create campaign: " + e.message, "error");
    }
  });

  els.campStatus.addEventListener("change", async () => {
    if (!currentCampaignId) return;
    try {
      currentCampaign = await apiPatch(
        "/api/campaigns/" + encodeURIComponent(currentCampaignId),
        { status: els.campStatus.value }
      );
      await loadCampaigns();
      renderCampaignDetail();
      toast("Status updated.", "ok");
    } catch (e) {
      toast("Could not update status: " + e.message, "error");
    }
  });

  els.rulesInput.addEventListener("change", async () => {
    const file = els.rulesInput.files[0];
    if (!file || !currentCampaignId) return;
    const fd = new FormData();
    fd.append("file", file);
    try {
      toast("Condensing brief…");
      const r = await fetch("/api/campaigns/" + encodeURIComponent(currentCampaignId) + "/rules", {
        method: "POST", body: fd,
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.error || r.status);
      currentCampaign = await apiGet("/api/campaigns/" + encodeURIComponent(currentCampaignId));
      renderRules();
      toast("Brief condensed.", "ok");
    } catch (e) {
      toast("Rules upload failed: " + e.message, "error");
    } finally {
      els.rulesInput.value = "";
    }
  });

  /* ---------------- boot ---------------- */
  async function loadMusic() {
    try {
      const m = await apiGet("/api/music");
      els.musicEnabled.checked = !!m.enabled;
      els.musicVolume.value = m.volume;
      els.musicTrack.innerHTML =
        `<option value="">Auto (rotate per clip)</option>` +
        m.tracks.map((t) =>
          `<option value="${escapeHtml(t)}" title="${escapeHtml(t)}">${escapeHtml(t)}</option>`
        ).join("");
      els.musicTrack.value = m.track || "";
    } catch (e) { /* optional */ }
  }

  async function boot() {
    try {
      await loadCampaigns();
      els.serverStatus.classList.add("online");
      els.serverStatusText.textContent = "ready";
      const parsed = parseHash();
      if (parsed.campaignId) {
        await openCampaign(parsed.campaignId, { silent: true });
      } else {
        els.modelChip.textContent = "ClipForge";
      }
    } catch (e) {
      els.serverStatus.classList.add("error");
      els.serverStatusText.textContent = "server offline";
      toast("Can't reach the backend. Is the server running?", "error");
    }
    renderPage();
  }

  boot();
})();
