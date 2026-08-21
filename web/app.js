/* ClipForge web UI — campaign workspace */
(function () {
  "use strict";

  const $ = (sel) => document.querySelector(sel);

  const els = {
    nav: $("#nav"),
    navCampaign: $("#navCampaign"),
    sideNav: $("#sideNav"),
    workspace: $("#workspace"),
    modelChip: $("#modelChip"),
    serverStatus: $("#serverStatus"),
    serverStatusText: $("#serverStatusText"),
    campaignGrid: $("#campaignGrid"),
    newCampaignForm: $("#newCampaignForm"),
    campName: $("#campName"),
    campDetailName: $("#campDetailName"),
    campDetailMeta: $("#campDetailMeta"),
    funnel: $("#funnel"),
    overviewEmpty: $("#overviewEmpty"),
    btnAddSources: $("#btnAddSources"),
    sourceList: $("#sourceList"),
    fileInput: $("#fileInput"),
    uploadBtn: $("#uploadBtn"),
    uploadProgress: $("#uploadProgress"),
    uploadFill: $("#uploadFill"),
    uploadPct: $("#uploadPct"),
    minScore: $("#minScore"),
    maxClips: $("#maxClips"),
    localHighlights: $("#localHighlights"),
    reviewCount: $("#reviewCount"),
    reviewList: $("#reviewList"),
    reviewEmpty: $("#reviewEmpty"),
    reviewHint: $("#reviewHint"),
    btnApproveAll: $("#btnApproveAll"),
    btnSaveReview: $("#btnSaveReview"),
    approvedCount: $("#approvedCount"),
    approvedList: $("#approvedList"),
    approvedEmpty: $("#approvedEmpty"),
    templateSelect: $("#templateSelect"),
    goldenStyles: $("#goldenStyles"),
    templateDesc: $("#templateDesc"),
    musicEnabled: $("#musicEnabled"),
    musicVolume: $("#musicVolume"),
    musicTrack: $("#musicTrack"),
    musicInput: $("#musicInput"),
    outputCount: $("#outputCount"),
    outputList: $("#outputList"),
    btnOpenFolder: $("#btnOpenFolder"),
    rulesEmpty: $("#rulesEmpty"),
    rulesFilled: $("#rulesFilled"),
    rulesSections: $("#rulesSections"),
    rulesInput: $("#rulesInput"),
    btnViewFullRules: $("#btnViewFullRules"),
    styleVideoSelect: $("#styleVideoSelect"),
    framesMode: $("#framesMode"),
    framesNum: $("#framesNum"),
    framesGrid: $("#framesGrid"),
    btnExtractFrames: $("#btnExtractFrames"),
    btnAnalyzeStyle: $("#btnAnalyzeStyle"),
    styleReport: $("#styleReport"),
    styleSheets: $("#styleSheets"),
    styleState: $("#styleState"),
    runbar: $("#runbar"),
    stageName: $("#stageName"),
    runMsg: $("#runMsg"),
    railFill: $("#railFill"),
    percent: $("#percent"),
    btnCancel: $("#btnCancel"),
    logToggle: $("#logToggle"),
    logConsole: $("#logConsole"),
    toast: $("#toast"),
    bellBtn: $("#bellBtn"),
    bellBadge: $("#bellBadge"),
    bellDropdown: $("#bellDropdown"),
    bellList: $("#bellList"),
    bellClear: $("#bellClear"),
    approvalCount: $("#approvalCount"),
    approvalList: $("#approvalList"),
    approvalEmpty: $("#approvalEmpty"),
    btnEmailCheck: $("#btnEmailCheck"),
    transcriptModal: $("#transcriptModal"),
    transcriptVideoName: $("#transcriptVideoName"),
    transcriptBody: $("#transcriptBody"),
    transcriptClose: $("#transcriptClose"),
    btnDeleteCampaign: $("#btnDeleteCampaign"),
    highlightToggle: $("#highlightToggle"),
    highlightToggleHint: $("#highlightToggleHint"),
    styleBrief: $("#styleBrief"),
    exploreVideoSelect: $("#exploreVideoSelect"),
    exploreVariants: $("#exploreVariants"),
    btnExploreStyles: $("#btnExploreStyles"),
    exploreResults: $("#exploreResults"),
    editInstructions: $("#editInstructions"),
    exportConfigModal: $("#exportConfigModal"),
    exportConfigVideo: $("#exportConfigVideo"),
    exportConfigTemplate: $("#exportConfigTemplate"),
    exportConfigInstructions: $("#exportConfigInstructions"),
    exportConfigHint: $("#exportConfigHint"),
    exportConfigCancel: $("#exportConfigCancel"),
    exportConfigStart: $("#exportConfigStart"),
    exportConfigClose: $("#exportConfigClose"),
  };

  const LABELS = {
    transcribe: "Transcribing audio",
    vad: "Tightening silence",
    clean: "Fixing transcript typos",
    context: "Building context",
    select: "Finding highlights",
    awaiting: "Waiting for the email reply",
    cut: "Cutting clips",
    render: "Rendering clips",
    frames: "Extracting frames",
    "explore-cut": "Cutting probe variants",
    "explore-variants": "Generating style variants",
    "explore-render": "Rendering style previews",
    "explore-judge": "Vision judge scoring",
    start: "Starting…",
    done: "Finished",
  };

  const WORKSPACE_PAGES = ["overview", "sources", "approval", "candidates", "approved", "exports", "settings"];
  const ALL_PAGES = ["dashboard"].concat(WORKSPACE_PAGES);
  const FUNNEL_STEPS = [
    { key: "sources", label: "Sources", page: "sources" },
    { key: "transcribed", label: "Transcribed", page: "sources" },
    { key: "analysed", label: "Analysed", page: "sources" },
    { key: "candidates", label: "Candidates", page: "candidates" },
    { key: "approved", label: "Approved", page: "approved" },
    { key: "exported", label: "Exported", page: "exports" },
  ];
  const STAGE_LABELS = {
    none: "Empty",
    uploaded: "Uploaded",
    transcribed: "Transcribed",
    analysing: "Transcribing…",
    awaiting: "Awaiting highlights",
    analysed: "Analysed",
    has_approved: "Approved",
    exported: "Exported",
  };

  let state = null;
  let campaigns = [];
  let currentCampaignId = null;
  let currentCampaign = null;
  let sources = [];
  let candidateGroups = [];
  let exportGroups = [];
  let dirty = false;
  let currentRun = null;
  let runningVideoId = null;
  let pollTimer = null;
  let pollFailures = 0;
  let shownLogs = 0;
  const openPreviews = new Map();
  let settingsTimer = null;

  // --- notifications / events ------------------------------------------- //
  let notifications = [];
  let lastEventSeq = 0;
  let eventPolling = false;
  const NOTIF_ICON = "✉";

  function ensureNotifyPermission() {
    if (!("Notification" in window)) return;
    if (Notification.permission === "default") {
      Notification.requestPermission().catch(() => {});
    }
  }

  function desktopNotify(title, body) {
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    try {
      const n = new Notification(title, { body: body || "", tag: "clipforge" });
      n.onclick = () => { window.focus(); n.close(); };
      setTimeout(() => n.close(), 6000);
    } catch (e) { /* ignore */ }
  }

  function addNotification(kind, title, body, campaignId, icon) {
    notifications.unshift({
      kind, title: title, body: body || "",
      campaignId: campaignId || null,
      icon: icon || NOTIF_ICON,
      ts: Date.now(),
    });
    if (notifications.length > 60) notifications.length = 60;
    renderNotifications();
  }

  function renderNotifications() {
    const unread = notifications.filter((n) => !n.read).length;
    els.bellBadge.textContent = unread;
    els.bellBadge.hidden = unread === 0;
    els.bellList.innerHTML = "";
    if (!notifications.length) {
      els.bellList.innerHTML = `<p class="empty">No notifications yet.</p>`;
      return;
    }
    for (const n of notifications) {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "bell-item" + (n.read ? "" : " unread");
      item.innerHTML =
        `<span class="bell-icon">${escapeHtml(n.icon || NOTIF_ICON)}</span>` +
        `<span class="bell-body">` +
        `<span class="bell-title">${escapeHtml(n.title)}</span>` +
        `<span class="bell-text">${escapeHtml(n.body || "")}</span>` +
        `<span class="bell-time">${escapeHtml(relTime(new Date(n.ts).toISOString()))}</span>` +
        `</span>`;
      item.addEventListener("click", () => {
        n.read = true;
        renderNotifications();
        if (n.campaignId && n.campaignId !== currentCampaignId) go("approval", n.campaignId);
        else if (n.campaignId) go("approval");
      });
      els.bellList.appendChild(item);
    }
  }

  function notifyTranscriptSent(videoId, recipients, ok) {
    const title = ok ? "Transcript emailed" : "Transcript email failed";
    const list = (recipients || []).join(", ");
    const body = ok ? `Sent to ${list}. Waiting for the highlight reply for “${videoId}”.`
                    : `Could not email the transcript for “${videoId}”. Check email settings.`;
    toast(title, ok ? "ok" : "error");
    addNotification(ok ? "sent" : "error", title, body, currentCampaignId);
    desktopNotify("ClipForge — " + title, body);
  }

  function notifyHighlightsReceived(videoId, count) {
    const title = "Highlights received";
    const body = `${count} highlight${count === 1 ? "" : "s"} arrived for “${videoId}”. Review and start clipping.`;
    toast(title + " — " + videoId, "ok");
    addNotification("received", title, body, currentCampaignId);
    desktopNotify("ClipForge — " + title, body);
    // Live-refresh the campaign so the approval page populates without a reload.
    refreshCampaignData().catch(() => {});
  }

  async function pollEvents() {
    if (eventPolling || !els.serverStatus) return;
    eventPolling = true;
    try {
      const r = await fetch("/api/events?since=" + lastEventSeq + "&timeout=25");
      if (r.ok) {
        const data = await r.json();
        for (const ev of data.events || []) {
          lastEventSeq = ev.seq;
          handleEvent(ev);
        }
      }
    } catch (e) { /* backend offline; retry on next tick */ }
    eventPolling = false;
    // keep long-polling continuously
    setTimeout(pollEvents, 400);
  }

  function handleEvent(ev) {
    const d = ev.data || {};
    if (ev.kind === "transcript_sent") {
      notifyTranscriptSent(d.video_id, d.recipients, !!d.sent);
    } else if (ev.kind === "highlights_received") {
      notifyHighlightsReceived(d.video_id, d.clip_count || 0);
    }
  }

  // --- transcript modal -------------------------------------------------- //
  function openTranscriptModal(videoId) {
    els.transcriptVideoName.textContent = videoId;
    els.transcriptBody.textContent = "Loading…";
    els.transcriptModal.hidden = false;
    fetch("/api/transcript/" + encodeURIComponent(videoId) + campQ())
      .then((r) => r.json())
      .then((d) => {
        els.transcriptBody.textContent = d.body || "No transcript available.";
      })
      .catch(() => {
        els.transcriptBody.textContent = "Could not load the transcript.";
      });
  }

  function closeTranscriptModal() {
    els.transcriptModal.hidden = true;
    els.transcriptBody.textContent = "";
  }

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

  function campaignSettings() {
    return (currentCampaign && currentCampaign.settings) || {};
  }

  function relTime(iso) {
    if (!iso) return "No activity";
    const t = Date.parse(iso);
    if (Number.isNaN(t)) return iso;
    const sec = Math.max(0, Math.round((Date.now() - t) / 1000));
    if (sec < 45) return "Just now";
    if (sec < 3600) return Math.round(sec / 60) + "m ago";
    if (sec < 86400) return Math.round(sec / 3600) + "h ago";
    if (sec < 604800) return Math.round(sec / 86400) + "d ago";
    return new Date(t).toLocaleDateString();
  }

  function parseHash() {
    const raw = (location.hash || "").replace(/^#\/?/, "").replace(/\/+$/, "");
    const parts = raw ? raw.split("/") : [];
    if (!parts.length || parts[0] === "dashboard") {
      return { page: "dashboard", campaignId: null };
    }
    if (parts[0] === "campaign" && parts[1]) {
      const sub = parts[2];
      if (WORKSPACE_PAGES.includes(sub)) {
        return { page: sub, campaignId: parts[1] };
      }
      return { page: "overview", campaignId: parts[1] };
    }
    if (WORKSPACE_PAGES.includes(parts[0])) {
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
    if (page === "overview" || page === "campaign") return "#/campaign/" + encodeURIComponent(cid);
    return "#/campaign/" + encodeURIComponent(cid) + "/" + page;
  }

  function go(page, campaignId) {
    const next = hrefFor(page, campaignId);
    if (location.hash !== next) location.hash = next;
    else renderPage();
  }

  function updateNav() {
    const page = currentPage();
    const open = !!currentCampaignId && page !== "dashboard";
    els.sideNav.hidden = !open;
    els.workspace.classList.toggle("is-workspace", open);
    if (currentCampaignId) {
      els.navCampaign.hidden = false;
      els.navCampaign.textContent = campaignName();
      els.navCampaign.href = hrefFor("overview");
    } else {
      els.navCampaign.hidden = true;
    }
    els.nav.querySelectorAll("a[data-page]").forEach((a) => {
      const p = a.dataset.page;
      a.classList.toggle("active", p === page);
      if (p === "overview") a.href = hrefFor("overview");
    });
    els.sideNav.querySelectorAll("a[data-page]").forEach((a) => {
      const p = a.dataset.page;
      a.classList.toggle("active", p === page);
      a.href = hrefFor(p);
    });
  }

  function renderPage() {
    const parsed = parseHash();
    if (parsed.campaignId && parsed.campaignId !== currentCampaignId) {
      openCampaign(parsed.campaignId, { silent: true }).then(() => renderPage());
      return;
    }
    if (!parsed.campaignId && WORKSPACE_PAGES.includes(parsed.page)) {
      go("dashboard");
      return;
    }
    const page = parsed.page;
    for (const p of ALL_PAGES) {
      const el = $("#page-" + p);
      if (el) el.hidden = p !== page;
    }
    updateNav();
    if (page === "dashboard") renderDashboard();
    if (page === "overview") renderOverview();
    if (page === "sources") renderSources();
    if (page === "approval") renderApproval();
    if (page === "candidates") renderCandidates();
    if (page === "approved") renderApproved();
    if (page === "exports") {
      renderExports();
      renderExploreControls();
    }
    if (page === "settings") {
      renderRules();
      renderTemplates();
      refreshStyleState();
    }
  }

  window.addEventListener("hashchange", renderPage);

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
    dirty = false;
    openPreviews.clear();
    await loadState();
    await Promise.all([loadSources(), loadCandidates(), loadExports()]);
    applyCampaignSettings();
    updateNav();
  }

  async function loadSources() {
    if (!currentCampaignId) { sources = []; return; }
    const r = await apiGet("/api/campaigns/" + encodeURIComponent(currentCampaignId) + "/sources");
    sources = r.sources || [];
  }

  async function loadCandidates() {
    if (!currentCampaignId) { candidateGroups = []; return; }
    const r = await apiGet("/api/campaigns/" + encodeURIComponent(currentCampaignId) + "/candidates");
    candidateGroups = r.groups || [];
    dirty = false;
    openPreviews.clear();
  }

  async function loadExports() {
    if (!currentCampaignId) { exportGroups = []; return; }
    const r = await apiGet("/api/campaigns/" + encodeURIComponent(currentCampaignId) + "/exports");
    exportGroups = r.groups || [];
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
      const f = c.funnel || {};
      const fill = Math.max(0, Math.min(100, 100 * (f.exported || 0) / Math.max(f.sources || 0, 1)));
      card.innerHTML =
        `<span class="film-frame" aria-hidden="true"><span class="film-bar" style="height:${fill}%"></span></span>` +
        `<span class="campaign-card-body">` +
        `<span class="campaign-card-name">${escapeHtml(c.name)}</span>` +
        `<span class="campaign-card-meta">${escapeHtml(relTime(c.updated_at))}</span>` +
        `<span class="campaign-card-clips">${f.sources || 0} videos · ${f.candidates || 0} candidates · ${f.approved || 0} approved · ${f.exported || 0} exported</span>` +
        `</span>`;
      card.addEventListener("click", () => go("overview", c.id));
      els.campaignGrid.appendChild(card);
    }
  }

  function renderOverview() {
    if (!currentCampaign) return;
    const f = currentCampaign.funnel || {};
    els.campDetailName.textContent = currentCampaign.name;
    els.campDetailMeta.textContent = relTime(currentCampaign.updated_at);
    renderHighlightToggle();
    els.funnel.innerHTML = "";
    FUNNEL_STEPS.forEach((step, i) => {
      const n = f[step.key] || 0;
      const done = n > 0;
      const li = document.createElement("li");
      li.className = "funnel-step" + (done ? " done" : "");
      li.innerHTML =
        `<button type="button" class="funnel-link">` +
        `<span class="funnel-tick" aria-hidden="true"></span>` +
        `<span class="funnel-label">${escapeHtml(step.label)}</span>` +
        `<span class="funnel-n">${n}</span>` +
        `</button>`;
      li.querySelector("button").addEventListener("click", () => go(step.page));
      els.funnel.appendChild(li);
      if (i < FUNNEL_STEPS.length - 1) {
        const rail = document.createElement("li");
        rail.className = "funnel-rail" + (done ? " done" : "");
        rail.setAttribute("aria-hidden", "true");
        els.funnel.appendChild(rail);
      }
    });
    const empty = !(f.sources);
    els.overviewEmpty.hidden = !empty;
  }

  function renderHighlightToggle() {
    const local = !!campaignSettings().local_highlights;
    els.highlightToggle.querySelectorAll(".seg").forEach((btn) => {
      btn.classList.toggle("on",
        (btn.dataset.mode === "local") === local);
    });
    els.highlightToggleHint.textContent = local
      ? "Highlights picked locally by Ollama (" + (state ? state.config.llm_model : "Gemma") + ")."
      : "Transcript is emailed out; the AI replies with highlight picks you approve on the Approval page.";
  }

  async function deleteCampaign() {
    if (!currentCampaign || !currentCampaignId) return;
    const name = currentCampaign.name;
    const msg = `Delete campaign "${name}"?\n\n` +
      "This removes EVERYTHING in it: source videos, transcripts, " +
      "candidates, approved clips, style template and all exported clips. " +
      "This cannot be undone.";
    if (!confirm(msg)) return;
    if (!confirm(`Final confirmation — permanently delete "${name}" and all its files?`)) return;
    try {
      const r = await fetch("/api/campaigns/" + encodeURIComponent(currentCampaignId),
                            { method: "DELETE" });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.error || r.status);
      toast(`Deleted campaign "${name}".`, "ok");
      currentCampaign = null;
      currentCampaignId = null;
      sources = []; candidateGroups = []; exportGroups = [];
      await loadCampaigns();
      go("dashboard");
    } catch (e) {
      toast("Delete failed: " + e.message, "error");
    }
  }

  function sourceBusy(id) {
    return !!currentRun && (!runningVideoId || runningVideoId === id);
  }

  function renderSources() {
    els.sourceList.innerHTML = "";
    if (!sources.length) {
      els.sourceList.innerHTML = `<div class="empty">No videos yet — add one on the right.</div>`;
      return;
    }
    for (const v of sources) {
      const row = document.createElement("div");
      row.className = "source-row";
      const mb = (v.size / 1048576).toFixed(v.size > 104857600 ? 0 : 1);
      const busy = sourceBusy(v.id) || v.running;
      const canExport = (v.approved || 0) > 0 && !busy;
      row.innerHTML =
        `<div class="source-main">` +
        `<span class="v-name">${escapeHtml(v.name)}</span>` +
        `<span class="v-size">${mb} MB</span>` +
        `<span class="stage-chip stage-${escapeHtml(v.stage || "uploaded")}">${escapeHtml(STAGE_LABELS[v.stage] || v.stage || "")}</span>` +
        `<span class="v-counts">${v.candidates || 0} cand · ${v.approved || 0} appr</span>` +
        `</div>` +
        `<div class="source-actions">` +
        `<button class="btn btn-small btn-primary btn-analyze" ${busy ? "disabled" : ""}>Find highlights</button>` +
        `<button class="btn btn-small btn-ghost btn-export" ${canExport ? "" : "disabled"}>Export approved</button>` +
        `<button class="v-del" title="Delete source video" aria-label="Delete ${escapeHtml(v.name)}">✕</button>` +
        `</div>`;
      row.querySelector(".btn-analyze").addEventListener("click", () => startRun("analyze", v.id, false));
      row.querySelector(".btn-export").addEventListener("click", () => openExportConfig("export", v.id, false));
      row.querySelector(".v-del").addEventListener("click", () => deleteVideo(v));
      els.sourceList.appendChild(row);
    }
  }

  async function deleteVideo(v) {
    if (!confirm(`Delete source video "${v.name}" from this campaign?\n\n` +
                 `Only the source file is removed — transcripts, candidates ` +
                 `and already-exported clips are kept.`)) return;
    try {
      const r = await fetch("/api/video/" + encodeURIComponent(v.id) + campQ(), { method: "POST" });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.error || r.status);
      toast("Deleted " + v.name + ".", "ok");
      await refreshCampaignData();
    } catch (e) {
      toast("Delete failed: " + e.message, "error");
    }
  }

  function allClips() {
    const out = [];
    for (const g of candidateGroups) {
      for (const c of g.clips || []) out.push(c);
    }
    return out;
  }

  function updateReviewHint() {
    if (!els.reviewHint) return;
    els.reviewHint.textContent = dirty
      ? "Unsaved changes — press Save decisions to keep them."
      : "Decisions stay unsaved until you press Save decisions.";
    els.reviewHint.classList.toggle("dirty", dirty);
  }

  function renderCandidates() {
    const groups = candidateGroups.filter((g) => g.clips && g.clips.length);
    els.reviewList.innerHTML = "";
    if (!groups.length) {
      els.reviewCount.textContent = "";
      els.reviewEmpty.hidden = false;
      return;
    }
    els.reviewEmpty.hidden = true;
    const clips = allClips();
    const approved = clips.filter((c) => c.status === "approved").length;
    els.reviewCount.textContent = `(${clips.length} found · ${approved} approved)`;
    for (const g of groups) {
      els.reviewList.appendChild(sourceGroupEl(g, g.clips, { actions: true }));
    }
    updateReviewHint();
  }

  // --- Approval page ----------------------------------------------------- //
  // Shows email-mode sources: waiting for the reply, or received highlights
  // ready for approve/reject → Start clipping.
  function approvalGroups() {
    return candidateGroups.filter((g) =>
      g.highlights_from === "email" || g.highlights_from === "pending");
  }

  function renderApproval() {
    els.approvalList.innerHTML = "";
    const groups = approvalGroups();
    els.approvalCount.textContent = groups.length ? `(${groups.length})` : "";
    if (!groups.length) {
      els.approvalEmpty.hidden = false;
      return;
    }
    els.approvalEmpty.hidden = true;
    for (const g of groups) {
      els.approvalList.appendChild(approvalGroupEl(g));
    }
  }

  function approvalGroupEl(group) {
    const wrap = document.createElement("div");
    wrap.className = "source-group approval-group";

    const clips = group.clips || [];
    const waiting = group.highlights_from === "pending" && !clips.length;
    const es = group.email_status || {};
    const sentAt = es.sent_at ? relTime(es.sent_at) : null;
    const recipients = (es.recipients || []).join(", ");

    const head = document.createElement("div");
    head.className = "source-group-head approval-head";
    head.innerHTML = `<h3 class="eyebrow">${escapeHtml(group.source_name || group.source_id)}</h3>`;
    wrap.appendChild(head);

    if (waiting) {
      const card = document.createElement("div");
      card.className = "card approval-waiting";
      card.innerHTML =
        `<div class="approval-status">` +
        `<span class="pill pill-await">Awaiting highlights</span>` +
        `<span class="approval-meta">Transcript emailed${recipients ? " to " + escapeHtml(recipients) : ""}` +
        `${sentAt ? " · " + escapeHtml(sentAt) : ""}</span>` +
        `</div>` +
        `<p class="hint">The transcript has been sent. When the AI replies with highlight picks, they'll appear here for approval.</p>` +
        `<div class="card-actions">` +
        `<button class="btn btn-small btn-ghost btn-view-transcript">View transcript</button>` +
        `<button class="btn btn-small btn-ghost btn-check-now">Check inbox now</button>` +
        `</div>`;
      card.querySelector(".btn-view-transcript").addEventListener("click",
        () => openTranscriptModal(group.source_id));
      card.querySelector(".btn-check-now").addEventListener("click", () => checkInboxNow());
      wrap.appendChild(card);
      return wrap;
    }

    // Received highlights: show clips with approve/reject + Start clipping.
    const approved = clips.filter((c) => c.status === "approved").length;
    const statusRow = document.createElement("div");
    statusRow.className = "approval-status";
    statusRow.innerHTML =
      `<span class="pill pill-ok">Highlights received</span>` +
      `<span class="approval-meta">${clips.length} pick${clips.length === 1 ? "" : "s"} · ${approved} approved</span>`;
    wrap.appendChild(statusRow);

    const cardGrid = document.createElement("div");
    cardGrid.className = "cards";
    clips.forEach((clip, i) => cardGrid.appendChild(approvalClipCard(clip, i, group)));
    wrap.appendChild(cardGrid);

    const actions = document.createElement("div");
    actions.className = "approval-actions";
    actions.innerHTML =
      `<button class="btn btn-small btn-ghost btn-view-transcript">View transcript</button>` +
      `<span class="approval-spacer"></span>` +
      `<button class="btn btn-small btn-primary btn-start-clip" ${approved ? "" : "disabled"}>▶ Start clipping (${approved})</button>`;
    const viewBtn = actions.querySelector(".btn-view-transcript");
    const startBtn = actions.querySelector(".btn-start-clip");
    viewBtn.addEventListener("click", () => openTranscriptModal(group.source_id));
    startBtn.addEventListener("click", () => startClipping(group));
    // reflect approval count on the button as it changes
    actions.dataset.start = "1";
    wrap.appendChild(actions);
    group._startBtn = startBtn;
    return wrap;
  }

  function updateApprovalStartBtn(group) {
    if (!group._startBtn) return;
    const approved = (group.clips || []).filter((c) => c.status === "approved").length;
    group._startBtn.disabled = approved === 0;
    group._startBtn.textContent = `▶ Start clipping (${approved})`;
  }

  function approvalClipCard(clip, i, group) {
    const card = document.createElement("div");
    card.className = "card " + (clip.status || "pending");

    const head = document.createElement("div");
    head.className = "card-head";
    head.innerHTML =
      `<span class="badge">#${i + 1}</span>` +
      `<span class="badge score">${Number(clip.score || 0).toFixed(2)}</span>` +
      `<span class="card-reason">${escapeHtml(clip.reason || "")}</span>`;
    card.appendChild(head);

    const times = document.createElement("div");
    times.className = "card-times";
    const rangeLabel = document.createTextNode(`→ ${fmt(clip.start)} – ${fmt(clip.end)}`);
    times.appendChild(rangeLabel);
    card.appendChild(times);

    const snip = document.createElement("pre");
    snip.className = "snippet";
    snip.textContent = snippetFor(clip);
    card.appendChild(snip);

    if (clip.hook) {
      const hook = document.createElement("div");
      hook.className = "approval-hook";
      hook.innerHTML = `<span class="field-label">Hook</span> ${escapeHtml(clip.hook)}`;
      card.appendChild(hook);
    }

    const actions = document.createElement("div");
    actions.className = "card-actions";
    const bApprove = document.createElement("button");
    bApprove.className = "btn-approve" + (clip.status === "approved" ? " on" : "");
    bApprove.textContent = clip.status === "approved" ? "✓ Approved" : "Approve";
    bApprove.addEventListener("click", () => {
      clip.status = clip.status === "approved" ? "pending" : "approved";
      card.classList.toggle("approved", clip.status === "approved");
      bApprove.classList.toggle("on", clip.status === "approved");
      bApprove.textContent = clip.status === "approved" ? "✓ Approved" : "Approve";
      updateApprovalStartBtn(group);
    });
    const bReject = document.createElement("button");
    bReject.className = "btn-reject" + (clip.status === "rejected" ? " on" : "");
    bReject.textContent = clip.status === "rejected" ? "✕ Rejected" : "Reject";
    bReject.addEventListener("click", () => {
      clip.status = clip.status === "rejected" ? "pending" : "rejected";
      card.classList.toggle("rejected", clip.status === "rejected");
      bReject.classList.toggle("on", clip.status === "rejected");
      bReject.textContent = clip.status === "rejected" ? "✕ Rejected" : "Reject";
    });
    const bPreview = document.createElement("button");
    bPreview.className = "btn-preview";
    bPreview.textContent = "▶ Preview";
    bPreview.addEventListener("click", () => previewClip(clip, card, bPreview, group.source_id));
    actions.appendChild(bApprove);
    actions.appendChild(bReject);
    actions.appendChild(bPreview);
    card.appendChild(actions);

    const slot = document.createElement("div");
    slot.className = "preview-slot";
    slot.hidden = true;
    card.appendChild(slot);
    restorePreview(clip, card, bPreview);
    return card;
  }

  async function checkInboxNow() {
    try {
      toast("Checking inbox…");
      const r = await apiPost("/api/email/check", {});
      if (r.ingested > 0) {
        toast(`Ingested ${r.ingested} highlight repl${r.ingested === 1 ? "y" : "ies"}.`, "ok");
      } else {
        toast("No new highlight replies yet.");
      }
      await refreshCampaignData();
    } catch (e) {
      toast("Inbox check failed: " + e.message, "error");
    }
  }

  async function startClipping(group) {
    const approved = (group.clips || []).filter((c) => c.status === "approved");
    if (!approved.length) {
      toast("Approve at least one highlight first.", "error");
      return;
    }
    try {
      await apiPost("/api/candidates", {
        video_id: group.video_id || group.source_id,
        clips: group.clips,
        campaign_id: currentCampaignId,
      });
      dirty = false;
    } catch (e) {
      toast("Could not save approvals: " + e.message, "error");
      return;
    }
    openExportConfig("export", group.source_id, false);
  }

  function renderApproved() {
    els.approvedList.innerHTML = "";
    const groups = candidateGroups.map((g) => ({
      ...g,
      clips: (g.clips || []).filter((c) => c.status === "approved"),
    })).filter((g) => g.clips.length);
    const n = groups.reduce((a, g) => a + g.clips.length, 0);
    els.approvedCount.textContent = n ? `(${n})` : "";
    if (!groups.length) {
      els.approvedEmpty.hidden = false;
      return;
    }
    els.approvedEmpty.hidden = true;
    for (const g of groups) {
      const wrap = sourceGroupEl(g, g.clips, { preview: true, exportBtn: true });
      els.approvedList.appendChild(wrap);
    }
  }

  function sourceGroupEl(group, clips, opts) {
    const wrap = document.createElement("div");
    wrap.className = "source-group";
    const head = document.createElement("div");
    head.className = "source-group-head";
    const title = document.createElement("h3");
    title.className = "eyebrow";
    title.textContent = group.source_name || group.source_id;
    head.appendChild(title);
    if (opts && opts.exportBtn) {
      const busy = sourceBusy(group.source_id);
      const btn = document.createElement("button");
      btn.className = "btn btn-small btn-primary";
      btn.textContent = "Export approved";
      btn.disabled = busy;
      btn.addEventListener("click", () => openExportConfig("export", group.source_id, false));
      head.appendChild(btn);
    }
    wrap.appendChild(head);
    const list = document.createElement("div");
    list.className = "cards";
    clips.forEach((clip, i) => list.appendChild(clipCard(clip, i, group, opts)));
    wrap.appendChild(list);
    return wrap;
  }

  function snippetFor(clip) {
    if (clip.snippet) return clip.snippet;
    return "No transcript available.";
  }

  function clipKey(clip) {
    return (clip.source_id || "") + ":" + clip.start + ":" + clip.end;
  }

  function clipCard(clip, i, group, opts) {
    const card = document.createElement("div");
    card.className = "card " + (clip.status || "pending");

    const head = document.createElement("div");
    head.className = "card-head";
    head.innerHTML =
      `<span class="badge">#${i + 1}</span>` +
      `<span class="badge score">${Number(clip.score || 0).toFixed(2)}</span>` +
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

    if (opts && (opts.actions || opts.preview)) {
      const actions = document.createElement("div");
      actions.className = "card-actions";
      if (opts.actions) {
        const bApprove = document.createElement("button");
        bApprove.className = "btn-approve" + (clip.status === "approved" ? " on" : "");
        bApprove.textContent = clip.status === "approved" ? "✓ Approved" : "Approve";
        bApprove.addEventListener("click", () => {
          clip.status = clip.status === "approved" ? "pending" : "approved";
          dirty = true;
          renderCandidates();
        });
        const bReject = document.createElement("button");
        bReject.className = "btn-reject" + (clip.status === "rejected" ? " on" : "");
        bReject.textContent = clip.status === "rejected" ? "✕ Rejected" : "Reject";
        bReject.addEventListener("click", () => {
          clip.status = clip.status === "rejected" ? "pending" : "rejected";
          dirty = true;
          renderCandidates();
        });
        actions.appendChild(bApprove);
        actions.appendChild(bReject);
      }
      const bPreview = document.createElement("button");
      bPreview.className = "btn-preview";
      bPreview.textContent = "▶ Preview";
      bPreview.addEventListener("click", () => previewClip(clip, card, bPreview, group.source_id));
      actions.appendChild(bPreview);
      card.appendChild(actions);

      const slot = document.createElement("div");
      slot.className = "preview-slot";
      slot.hidden = true;
      card.appendChild(slot);
      restorePreview(clip, card, bPreview);
    }
    return card;
  }

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

  async function previewClip(clip, card, button, videoId) {
    const slot = card.querySelector(".preview-slot");
    if (!videoId) return;
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
        video: videoId, start: clip.start, end: clip.end,
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

  function renderExports() {
    els.outputList.innerHTML = "";
    const groups = (exportGroups || []).filter((g) => g.outputs && g.outputs.length);
    const n = groups.reduce((a, g) => a + g.outputs.length, 0);
    els.outputCount.textContent = n ? `(${n})` : "";
    if (!groups.length) {
      els.outputList.innerHTML = `<div class="empty">Nothing exported yet.</div>`;
      return;
    }
    for (const g of groups) {
      const wrap = document.createElement("div");
      wrap.className = "source-group";
      wrap.innerHTML = `<div class="source-group-head"><h3 class="eyebrow">${escapeHtml(g.source_name || g.source_id)}</h3></div>`;
      const grid = document.createElement("div");
      grid.className = "cards output-cards";
      for (const o of g.outputs) {
        const card = document.createElement("div");
        card.className = "card output-card";
        card.innerHTML =
          `<video controls preload="none" src="${o.url}"></video>` +
          `<div class="output-meta">` +
          `<span class="output-name" title="${escapeHtml(o.name)}">${escapeHtml(o.name)}</span>` +
          `<a href="${o.url}" download>Download</a>` +
          `</div>`;
        grid.appendChild(card);
      }
      wrap.appendChild(grid);
      els.outputList.appendChild(wrap);
    }
  }

  function renderExploreControls() {
    if (!els.exploreVideoSelect) return;
    const groups = candidateGroups.filter((g) => (g.clips || []).length);
    const prev = els.exploreVideoSelect.value;
    els.exploreVideoSelect.innerHTML = groups.length
      ? groups.map((g) =>
          `<option value="${escapeHtml(g.source_id)}">${escapeHtml(g.source_name || g.source_id)}</option>`
        ).join("")
      : `<option value="">No candidates yet — analyze first</option>`;
    if (prev && groups.some((g) => g.source_id === prev)) {
      els.exploreVideoSelect.value = prev;
    }
    els.btnExploreStyles.disabled = !groups.length || !!currentRun;
    const sel = els.exploreVideoSelect.value;
    if (sel) loadExploration(sel);
  }

  async function loadExploration(videoId) {
    if (!els.exploreResults) return;
    let report = null;
    try {
      report = await apiGet("/api/exploration/" + encodeURIComponent(videoId) + campQ());
    } catch (e) { /* no exploration yet */ }
    if (!report || !(report.variants || []).length) {
      els.exploreResults.innerHTML =
        `<p class="hint">No exploration yet for this video.</p>`;
      return;
    }
    const winner = report.winner;
    let html = `<div class="cards explore-cards">`;
    for (const v of report.variants) {
      const isWin = v.name === winner;
      const total = (typeof v.total === "number") ? v.total.toFixed(1) : "—";
      html +=
        `<div class="card explore-card${isWin ? " explore-winner" : ""}">` +
        (v.preview_url
          ? `<video controls preload="none" muted src="${v.preview_url}"></video>`
          : `<div class="empty">no preview</div>`) +
        `<div class="explore-meta">` +
        `<span class="explore-name" title="${escapeHtml(v.summary)}">${escapeHtml(v.name)}</span>` +
        `<span class="explore-score">score ${total}</span>` +
        `<p class="explore-verdict">${escapeHtml(v.verdict || "")}</p>` +
        `</div></div>`;
    }
    html += `</div>`;
    html += `<div class="explore-foot">` +
      `<p class="hint">Winner: <b>${escapeHtml(winner || "none")}</b> — re-export to apply it to all approved clips.</p>` +
      `<button class="btn btn-primary btn-save-style" ${winner && report.winner_template ? "" : "disabled"}>Save as campaign style</button>` +
      `</div>`;
    els.exploreResults.innerHTML = html;
    const saveBtn = els.exploreResults.querySelector(".btn-save-style");
    if (saveBtn && winner) {
      saveBtn.addEventListener("click", async () => {
        try {
          await apiPost("/api/exploration/" + encodeURIComponent(videoId) +
                        "/save-to-campaign" + campQ(), {});
          toast("Winner saved as campaign style. Next export uses it.", "ok");
          await refreshCampaignData();
        } catch (e) {
          toast("Save failed: " + e.message, "error");
        }
      });
    }
  }

  // --- Pre-generation config: template + edit instructions (every export) - //
  let exportConfig = null;

  function openExportConfig(mode, videoId, auto) {
    if (!videoId) { toast("Pick a source first.", "error"); return; }
    if (currentRun) { toast("A run is already in progress.", "error"); return; }
    exportConfig = { mode, videoId, auto: !!auto };
    const tpls = (state && state.templates) || [];
    const settings = campaignSettings();
    const winnerHint = tpls.some((t) => t.name === videoId + "_winner");
    els.exportConfigTemplate.innerHTML = tpls.length
      ? tpls.map((t) =>
          `<option value="${escapeHtml(t.name)}">${escapeHtml(t.label || t.name)}</option>`
        ).join("")
      : `<option value="">No templates available</option>`;
    const prev = winnerHint ? videoId + "_winner"
      : (settings.default_template || (els.templateSelect && els.templateSelect.value));
    if (prev && tpls.some((t) => t.name === prev)) {
      els.exportConfigTemplate.value = prev;
    }
    els.exportConfigVideo.textContent = videoId;
    els.exportConfigInstructions.value = settings.edit_instructions || "";
    els.exportConfigHint.textContent =
      "Instructions are saved to the campaign and logged at export time." +
      (winnerHint && mode !== "explore-style"
        ? " A Style Explorer winner exists for this video — leaving the template on its default will re-export with it."
        : "");
    els.exportConfigModal.hidden = false;
  }

  function closeExportConfig() {
    els.exportConfigModal.hidden = true;
    exportConfig = null;
  }

  async function confirmExportConfig() {
    if (!exportConfig) return;
    const cfg = exportConfig;
    const template = els.exportConfigTemplate.value || undefined;
    const instructions = els.exportConfigInstructions.value || "";
    if (instructions !== (campaignSettings().edit_instructions || "")) {
      try {
        await saveCampaignSettings({ edit_instructions: instructions });
      } catch (e) { /* settings save is best-effort; the run still starts */ }
    }
    closeExportConfig();
    startRun(cfg.mode, cfg.videoId, cfg.auto, { template });
  }

  async function startRun(mode, videoId, auto, extra) {
    if (!currentCampaignId) { toast("Open a campaign first.", "error"); go("dashboard"); return; }
    if (!videoId) { toast("Pick a source first.", "error"); go("sources"); return; }
    if (currentRun) { toast("A run is already in progress.", "error"); return; }
    if (dirty && !confirm("You have unsaved review decisions. A new run may overwrite them. Continue?")) return;

    const settings = campaignSettings();
    if (mode === "export" || mode === "pipeline") {
      try {
        await apiPost("/api/music", {
          enabled: settings.music_enabled,
          volume: settings.music_volume,
          track: settings.music_track || "",
        });
      } catch (e) { /* template music is best-effort */ }
    }
    const body = Object.assign({
      mode, video: videoId,
      campaign_id: currentCampaignId,
      template: (els.templateSelect && els.templateSelect.value) || settings.default_template,
      min_score: parseFloat(els.minScore && els.minScore.value) || settings.min_score,
      max_clips: parseInt(els.maxClips && els.maxClips.value, 10) || settings.max_clips,
      auto: !!auto,
    }, extra || {});
    clearLogs();
    try {
      const res = await apiPost("/api/run", body);
      currentRun = res.run;
      runningVideoId = videoId;
      setRunbar(true);
      updateProgress(0, "start");
      renderSources();
      poll(async (status) => {
        await refreshAfterRun();
        if (status === "ok") {
          if (mode === "analyze") {
            const s = campaignSettings();
            if (s.local_highlights === false || extra && extra.email) {
              go("approval");
              openTranscriptModal(videoId);
            } else {
              go("candidates");
            }
          }
          else if (mode === "export" || mode === "pipeline") go("exports");
          else if (mode === "explore-style") {
            go("exports");
            renderExploreControls();
            loadExploration(videoId);
          }
        }
      });
    } catch (e) {
      toast("Failed to start: " + e.message, "error");
    }
  }

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
        currentRun = null; runningVideoId = null; pollTimer = null; pollFailures = 0;
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
        currentRun = null;
        runningVideoId = null;
        pollTimer = null;
        setRunbar(false);
        if (run.status === "ok") toast("Done.", "ok");
        else if (run.status === "error") toast("Run failed: " + (run.error || "see log"), "error");
        if (onDone) await onDone(run.status);
        return;
      }
      pollTimer = setTimeout(poll, 1200, onDone);
    } catch (e) {
      pollFailures += 1;
      if (pollFailures > 5) {
        currentRun = null;
        runningVideoId = null;
        setRunbar(false);
        toast("Lost connection to the run.", "error");
        return;
      }
      pollTimer = setTimeout(poll, 2000, onDone);
    }
  }

  async function refreshCampaignData() {
    if (!currentCampaignId) return;
    currentCampaign = await apiGet("/api/campaigns/" + encodeURIComponent(currentCampaignId));
    await Promise.all([loadSources(), loadCandidates(), loadExports(), loadCampaigns()]);
    applyCampaignSettings();
    renderPage();
  }

  async function refreshAfterRun() {
    try {
      await loadState();
      await refreshCampaignData();
    } catch (e) { /* ignore */ }
  }

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

  async function loadState() {
    state = await apiGet("/api/state" + campQ());
    els.modelChip.textContent = state.config.llm_model + " · " + state.config.whisper_model;
    renderStyleVideoSelect();
    renderTemplates();
  }

  function renderStyleVideoSelect() {
    if (!els.styleVideoSelect) return;
    const vids = (state && state.videos) || [];
    els.styleVideoSelect.innerHTML = vids.length
      ? vids.map((v) => `<option value="${escapeHtml(v.id)}">${escapeHtml(v.name)}</option>`).join("")
      : `<option value="">No videos yet</option>`;
  }

  function applyCampaignSettings() {
    const s = campaignSettings();
    if (els.minScore && s.min_score != null) els.minScore.value = s.min_score;
    if (els.maxClips && s.max_clips != null) els.maxClips.value = s.max_clips;
    if (els.localHighlights) els.localHighlights.checked = !!s.local_highlights;
    if (els.musicEnabled) els.musicEnabled.checked = !!s.music_enabled;
    if (els.musicVolume && s.music_volume != null) els.musicVolume.value = s.music_volume;
    if (els.styleBrief) els.styleBrief.value = s.style_brief || "";
    if (els.editInstructions) els.editInstructions.value = s.edit_instructions || "";
    renderTemplates();
  }

  function scheduleSettingsSave() {
    clearTimeout(settingsTimer);
    settingsTimer = setTimeout(saveCampaignSettings, 400);
  }

  async function saveCampaignSettings(extra) {
    if (!currentCampaignId) return;
    const settings = Object.assign({
      min_score: parseFloat(els.minScore.value),
      max_clips: parseInt(els.maxClips.value, 10),
      local_highlights: els.localHighlights ? els.localHighlights.checked : true,
      default_template: els.templateSelect.value,
      music_enabled: els.musicEnabled.checked,
      music_track: els.musicTrack.value,
      music_volume: parseFloat(els.musicVolume.value),
      style_brief: els.styleBrief ? els.styleBrief.value : "",
      edit_instructions: els.editInstructions ? els.editInstructions.value : "",
    }, extra || {});
    try {
      currentCampaign = await apiPatch(
        "/api/campaigns/" + encodeURIComponent(currentCampaignId),
        { settings }
      );
      try {
        await apiPost("/api/music", {
          enabled: settings.music_enabled,
          volume: settings.music_volume,
          track: settings.music_track,
        });
      } catch (e) { /* template music is best-effort */ }
    } catch (e) {
      toast("Could not save settings: " + e.message, "error");
    }
  }

  function styleVideoId() {
    return els.styleVideoSelect.value;
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
        : "No frames yet — extract them first.";
      renderStyleSheets(set);
      if (set && set.has_report) {
        await loadStyleReport(id);
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
        ? `<p class="hint ok-note">✓ Draft template saved to this campaign.</p>`
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
      runningVideoId = id;
      setRunbar(true);
      updateProgress(0, "start");
      poll(() => { refreshStyleState(); loadState(); });
    } catch (e) {
      toast("Failed to start: " + e.message, "error");
    }
  }

  function goldenTemplates() {
    const tpls = (state && state.templates) || [];
    const gold = tpls.filter((t) => t.golden);
    return gold.length ? gold : tpls.slice(0, 2);
  }

  function renderTemplates() {
    if (!els.templateSelect) return;
    const tpls = goldenTemplates();
    const settings = campaignSettings();
    const prev = els.templateSelect.value || settings.default_template;
    els.templateSelect.innerHTML = tpls.map((t) =>
      `<option value="${escapeHtml(t.name)}">${escapeHtml(t.label || t.name)}</option>`).join("");
    const names = tpls.map((t) => t.name);
    if (prev && names.includes(prev)) {
      els.templateSelect.value = prev;
    } else if (state && state.config && names.includes(state.config.default_template)) {
      els.templateSelect.value = state.config.default_template;
    } else if (tpls.length) {
      els.templateSelect.value = tpls[0].name;
    }
    if (els.goldenStyles) {
      els.goldenStyles.innerHTML = "";
      tpls.forEach((t) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "golden-style" + (t.name === els.templateSelect.value ? " on" : "");
        btn.textContent = t.label || t.name;
        btn.addEventListener("click", () => {
          els.templateSelect.value = t.name;
          if (els.goldenStyles) {
            els.goldenStyles.querySelectorAll(".golden-style").forEach((el, i) => {
              el.classList.toggle("on", tpls[i] && tpls[i].name === t.name);
            });
          }
          updateTemplateDesc();
          saveCampaignSettings({ default_template: t.name });
        });
        els.goldenStyles.appendChild(btn);
      });
    }
    updateTemplateDesc();
  }

  function updateTemplateDesc() {
    const tpls = (state && state.templates) || [];
    const t = tpls.find((x) => x.name === els.templateSelect.value);
    if (els.templateDesc) els.templateDesc.textContent = t ? (t.description || "") : "";
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
    if (!els.rulesEmpty) return;
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

  els.templateSelect.addEventListener("change", () => {
    updateTemplateDesc();
    saveCampaignSettings();
  });

  if (els.highlightToggle) {
    els.highlightToggle.querySelectorAll(".seg").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const isLocal = btn.dataset.mode === "local";
        if (els.localHighlights) els.localHighlights.checked = isLocal;
        await saveCampaignSettings({ local_highlights: isLocal });
        renderHighlightToggle();
      });
    });
  }
  if (els.btnDeleteCampaign) {
    els.btnDeleteCampaign.addEventListener("click", deleteCampaign);
  }
  els.minScore.addEventListener("change", scheduleSettingsSave);
  els.maxClips.addEventListener("change", scheduleSettingsSave);
  if (els.localHighlights) els.localHighlights.addEventListener("change", scheduleSettingsSave);
  if (els.styleBrief) {
    els.styleBrief.addEventListener("change", scheduleSettingsSave);
    els.styleBrief.addEventListener("blur", scheduleSettingsSave);
  }
  if (els.exploreVideoSelect) {
    els.exploreVideoSelect.addEventListener("change", () => {
      const id = els.exploreVideoSelect.value;
      if (id) loadExploration(id);
    });
  }
  if (els.btnExploreStyles) {
    els.btnExploreStyles.addEventListener("click", () => {
      const id = els.exploreVideoSelect.value;
      if (!id) return;
      startRun("explore-style", id, false, {
        variants: parseInt(els.exploreVariants.value, 10) || undefined,
      });
    });
  }
  if (els.exportConfigCancel) els.exportConfigCancel.addEventListener("click", closeExportConfig);
  if (els.exportConfigClose) els.exportConfigClose.addEventListener("click", closeExportConfig);
  if (els.exportConfigStart) els.exportConfigStart.addEventListener("click", confirmExportConfig);
  if (els.exportConfigModal) {
    els.exportConfigModal.addEventListener("click", (e) => {
      if (e.target === els.exportConfigModal) closeExportConfig();
    });
  }
  if (els.editInstructions) {
    els.editInstructions.addEventListener("change", scheduleSettingsSave);
    els.editInstructions.addEventListener("blur", scheduleSettingsSave);
  }

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
    for (const c of allClips()) c.status = "approved";
    dirty = true;
    renderCandidates();
  });

  els.btnSaveReview.addEventListener("click", async () => {
    const groups = candidateGroups.filter((g) => g.clips && g.clips.length);
    if (!groups.length) return;
    try {
      for (const g of groups) {
        await apiPost("/api/candidates", {
          video_id: g.video_id || g.source_id,
          clips: g.clips,
          campaign_id: currentCampaignId,
        });
      }
      dirty = false;
      updateReviewHint();
      toast("Decisions saved.", "ok");
      await refreshCampaignData();
    } catch (e) {
      toast("Save failed: " + e.message, "error");
    }
  });

  for (const el of [els.musicEnabled, els.musicVolume, els.musicTrack]) {
    el.addEventListener("change", () => saveCampaignSettings());
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
      await refreshCampaignData();
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
      const camp = await apiPost("/api/campaigns", { name });
      els.newCampaignForm.reset();
      await loadCampaigns();
      toast("Campaign created.", "ok");
      go("overview", camp.id);
    } catch (e) {
      toast("Could not create campaign: " + e.message, "error");
    }
  });

  els.btnAddSources.addEventListener("click", () => go("sources"));

  els.bellBtn.addEventListener("click", (ev) => {
    ev.stopPropagation();
    els.bellDropdown.hidden = !els.bellDropdown.hidden;
  });
  document.addEventListener("click", (ev) => {
    if (!els.bellDropdown.hidden && !els.bellDropdown.contains(ev.target)) {
      els.bellDropdown.hidden = true;
    }
  });
  els.bellClear.addEventListener("click", () => {
    notifications = [];
    renderNotifications();
  });
  els.btnEmailCheck.addEventListener("click", () => checkInboxNow());
  els.transcriptClose.addEventListener("click", closeTranscriptModal);
  els.transcriptModal.addEventListener("click", (ev) => {
    if (ev.target === els.transcriptModal) closeTranscriptModal();
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && !els.transcriptModal.hidden) closeTranscriptModal();
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

  async function loadMusic() {
    try {
      const m = await apiGet("/api/music");
      const s = campaignSettings();
      els.musicEnabled.checked = s.music_enabled != null ? !!s.music_enabled : !!m.enabled;
      els.musicVolume.value = s.music_volume != null ? s.music_volume : m.volume;
      els.musicTrack.innerHTML =
        `<option value="">Auto (rotate per clip)</option>` +
        m.tracks.map((t) =>
          `<option value="${escapeHtml(t)}" title="${escapeHtml(t)}">${escapeHtml(t)}</option>`
        ).join("");
      els.musicTrack.value = s.music_track || m.track || "";
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
      await loadMusic();
    } catch (e) {
      els.serverStatus.classList.add("error");
      els.serverStatusText.textContent = "server offline";
      toast("Can't reach the backend. Is the server running?", "error");
    }
    renderPage();
    ensureNotifyPermission();
    renderNotifications();
    pollEvents();
  }

  boot();
})();
