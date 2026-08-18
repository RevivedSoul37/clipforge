/* ClipForge web UI — paged edition */
(function () {
  "use strict";

  const $ = (sel) => document.querySelector(sel);

  const els = {
    nav: $("#nav"),
    modelChip: $("#modelChip"),
    serverStatus: $("#serverStatus"),
    serverStatusText: $("#serverStatusText"),
    videoList: $("#videoList"),
    fileInput: $("#fileInput"),
    uploadBtn: $("#uploadBtn"),
    uploadProgress: $("#uploadProgress"),
    uploadFill: $("#uploadFill"),
    uploadPct: $("#uploadPct"),
    videoSelectA: $("#videoSelectA"),
    videoSelectR: $("#videoSelectR"),
    videoSelectE: $("#videoSelectE"),
    minScore: $("#minScore"),
    maxClips: $("#maxClips"),
    rulesText: $("#rulesText"),
    btnSaveRules: $("#btnSaveRules"),
    btnAnalyze: $("#btnAnalyze"),
    reviewCount: $("#reviewCount"),
    reviewList: $("#reviewList"),
    reviewEmpty: $("#reviewEmpty"),
    reviewHint: $("#reviewHint"),
    btnApproveAll: $("#btnApproveAll"),
    btnSaveReview: $("#btnSaveReview"),
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
    context: "Building context",
    select: "Finding highlights",
    broll: "Resolving b-roll",
    cut: "Cutting clips",
    render: "Rendering clips",
    start: "Starting…",
    done: "Finished",
  };

  let state = null;
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
  async function apiPost(url, body) {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error((await r.text()) || r.status);
    return r.json();
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

  /* ---------------- router ---------------- */
  const PAGES = ["source", "analyze", "review", "export"];

  function currentPage() {
    const h = (location.hash || "").replace(/^#\/?/, "");
    return PAGES.includes(h) ? h : "source";
  }

  function go(page) {
    if (currentPage() !== page) location.hash = "#/" + page;
    renderPage();
  }

  function renderPage() {
    const page = currentPage();
    for (const p of PAGES) {
      $("#page-" + p).hidden = p !== page;
    }
    els.nav.querySelectorAll("a").forEach((a) => {
      a.classList.toggle("active", a.dataset.page === page);
    });
    if (page === "review") renderReview();
    if (page === "export") { renderOutputs(); loadBrollStatus(); }
  }

  window.addEventListener("hashchange", renderPage);

  /* ---------------- state ---------------- */
  async function loadState() {
    state = await apiGet("/api/state");
    els.modelChip.textContent = state.config.llm_model + " · " + state.config.whisper_model;
    els.minScore.value = state.config.min_score;
    els.maxClips.value = state.config.max_clips;
    renderVideoSelects();
    renderSourceList();
    renderTemplates();
  }

  function renderVideoSelects() {
    const options = state.videos.length
      ? state.videos.map((v) => `<option value="${escapeHtml(v.id)}">${escapeHtml(v.name)}</option>`).join("")
      : `<option value="">No videos yet</option>`;
    for (const sel of [els.videoSelectA, els.videoSelectR, els.videoSelectE]) {
      sel.innerHTML = options;
      if (currentVideoId && state.videos.some((v) => v.id === currentVideoId)) {
        sel.value = currentVideoId;
      }
    }
  }

  function renderSourceList() {
    els.videoList.innerHTML = "";
    if (!state.videos.length) {
      els.videoList.innerHTML = `<div class="empty">No videos yet — upload one on the right.</div>`;
      return;
    }
    for (const v of state.videos) {
      const item = document.createElement("div");
      item.className = "video-item" + (v.id === currentVideoId ? " selected" : "");
      const mb = (v.size / 1048576).toFixed(v.size > 104857600 ? 0 : 1);
      item.innerHTML =
        `<span class="v-name">${escapeHtml(v.name)}</span><span class="v-size">${mb} MB</span>`;
      item.addEventListener("click", () => setVideo(v.id));
      els.videoList.appendChild(item);
    }
  }

  function renderTemplates() {
    els.templateSelect.innerHTML = state.templates
      .map((t) => `<option value="${escapeHtml(t.name)}">${escapeHtml(t.name)}</option>`)
      .join("");
    els.templateSelect.value = state.config.default_template;
    updateTemplateDesc();
  }

  function updateTemplateDesc() {
    const t = (state.templates || []).find((x) => x.name === els.templateSelect.value);
    els.templateDesc.textContent = t ? t.description : "";
  }

  async function setVideo(id) {
    if (!id) return;
    currentVideoId = id;
    for (const sel of [els.videoSelectA, els.videoSelectR, els.videoSelectE]) {
      if (Array.from(sel.options).some((o) => o.value === id)) sel.value = id;
    }
    dirty = false;
    openPreviews.clear();
    try {
      currentVideo = await apiGet("/api/video/" + id);
    } catch (e) {
      currentVideo = null;
      toast("Could not load video details: " + e.message, "error");
    }
    renderSourceList();
    renderReview();
    renderOutputs();
    updateReviewHint();
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

  function renderReview() {
    const cand = currentVideo && currentVideo.candidates;
    els.reviewList.innerHTML = "";
    if (!cand || !cand.clips.length) {
      els.reviewCount.textContent = "";
      els.reviewEmpty.hidden = false;
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
        const br = document.createElement("div");
        br.className = "broll-cues";
        br.textContent = "B-roll: " + clip.broll
          .map((c) => `${c.emotion} @ ${fmt(c.start)}–${fmt(c.end)}`)
          .join(" · ");
        card.appendChild(br);
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
      const res = await apiPost("/api/preview", { video: currentVideoId, start: clip.start, end: clip.end });
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
      card.className = "output-card";
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

  async function startRun(mode, auto) {
    if (!currentVideoId) { toast("Pick or upload a video first (Source page).", "error"); go("source"); return; }
    if (dirty && !confirm("You have unsaved review decisions. A new run may overwrite them. Continue?")) return;
    dirty = false; updateReviewHint();

    const body = {
      mode, video: currentVideoId,
      template: els.templateSelect.value,
      min_score: parseFloat(els.minScore.value),
      max_clips: parseInt(els.maxClips.value),
      auto: !!auto,
    };
    clearLogs();
    try {
      const res = await apiPost("/api/run", body);
      currentRun = res.run;
      setRunbar(true);
      updateProgress(0, "start");
      poll();
    } catch (e) {
      toast("Failed to start: " + e.message, "error");
    }
  }

  function updateProgress(percent, stage) {
    const p = Math.max(0, Math.min(100, percent || 0));
    els.railFill.style.width = p + "%";
    els.percent.textContent = Math.round(p) + "%";
    els.stageName.textContent = LABELS[stage] || stage || "Working";
  }

  async function poll() {
    try {
      const run = await apiGet("/api/run/" + currentRun + "?since=" + shownLogs);
      pollFailures = 0;
      updateProgress(run.percent, run.stage);
      els.runMsg.textContent = run.message || "";
      if (run.logs && run.logs.length) {
        appendLogs(run.logs, run.log_dropped || 0);
        shownLogs = run.log_index;
      }
      if (run.status === "ok" || run.status === "error" || run.status === "cancelled") {
        currentRun = null;
        pollTimer = null;
        updateProgress(run.status === "ok" ? 100 : run.percent, run.status === "ok" ? "done" : run.stage);
        if (run.status === "error") {
          toast(run.error || "Pipeline failed — see the log.", "error");
        } else if (run.status === "cancelled") {
          toast("Run cancelled.");
        } else {
          toast("Finished — clips are ready.", "ok");
        }
        setTimeout(() => { if (!currentRun) setRunbar(false); }, 4000);
        await refreshAfterRun();
        return;
      }
    } catch (e) {
      pollFailures++;
      if (pollFailures >= 8) {
        currentRun = null; pollTimer = null; pollFailures = 0;
        setRunbar(false);
        els.serverStatus.classList.add("error");
        els.serverStatusText.textContent = "server offline";
        toast("Lost connection to the backend.", "error");
        return;
      }
    }
    pollTimer = setTimeout(poll, 500);
  }

  async function refreshAfterRun() {
    try {
      await loadState();
      if (currentVideoId) await setVideo(currentVideoId);
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
      xhr.send(fd);
    });
  }

  /* ---------------- events ---------------- */
  for (const sel of [els.videoSelectA, els.videoSelectR, els.videoSelectE]) {
    sel.addEventListener("change", () => setVideo(sel.value));
  }
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

  els.btnSaveRules.addEventListener("click", async () => {
    try {
      await apiPost("/api/rules", { rules: els.rulesText.value });
      toast("Selection rules saved.", "ok");
    } catch (e) {
      toast("Could not save rules: " + e.message, "error");
    }
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
      await apiPost("/api/open-folder", { dir: "output" });
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

  /* ---------------- boot ---------------- */
  async function loadRules() {
    try {
      const r = await apiGet("/api/rules");
      els.rulesText.value = r.rules || "";
    } catch (e) { /* optional */ }
  }

  async function loadMusic() {
    try {
      const m = await apiGet("/api/music");
      els.musicEnabled.checked = !!m.enabled;
      els.musicVolume.value = m.volume;
      els.musicTrack.innerHTML =
        `<option value="">Auto (rotate per clip)</option>` +
        m.tracks.map((t) => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`).join("");
      els.musicTrack.value = m.track || "";
    } catch (e) { /* optional */ }
  }

  async function boot() {
    try {
      await loadState();
      await loadRules();
      await loadMusic();
      if (state.videos.length) await setVideo(state.videos[0].id);
      els.serverStatus.classList.add("online");
      els.serverStatusText.textContent = "ready";
    } catch (e) {
      els.serverStatus.classList.add("error");
      els.serverStatusText.textContent = "server offline";
      toast("Can't reach the backend. Is the server running?", "error");
    }
    renderPage();
  }

  boot();
})();
