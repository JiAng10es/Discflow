

  // ================= 自定义功能：歌词行间距/字间距、导航栏长短/透明度/模糊度与主题持久化 =================
  const DEFAULT_CUSTOM_PREFS = {
    lyricLineHeight: 1.4,
    lyricLetterSpacing: 0,
    dockWidth: 720,
    dockOpacity: 5,
    dockBlur: 2,
    topbarOpacity: 5,
    topbarBlur: 2,
    themeMode: "dark",
    autoPlay: true,
    closeToTray: true,
    showTranslation: true
  };

  let currentCustomPrefs = { ...DEFAULT_CUSTOM_PREFS };

  function loadCustomPrefs() {
    try {
      const saved = localStorage.getItem("discflow_custom_prefs");
      if (saved) {
        currentCustomPrefs = { ...DEFAULT_CUSTOM_PREFS, ...JSON.parse(saved) };
      }
    } catch (e) {}
    applyCustomPrefs(currentCustomPrefs);

    try {
      const savedTheme = localStorage.getItem("discflow_theme");
      const fallbackTheme = currentCustomPrefs.themeMode ||
        (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
      const initTheme = (savedTheme === "light" || savedTheme === "dark") ? savedTheme : fallbackTheme;
      applyTheme(initTheme, false);
    } catch (e) {}
  }

  function saveCustomPrefs() {
    try {
      localStorage.setItem("discflow_custom_prefs", JSON.stringify(currentCustomPrefs));
    } catch (e) {}
    if (window.pywebview && window.pywebview.api && window.pywebview.api.save_preferences) {
      window.pywebview.api.save_preferences(currentCustomPrefs).catch(() => {});
    }
  }

  async function syncPreferencesFromBackend() {
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.get_preferences) return;
    try {
      const prefs = await window.pywebview.api.get_preferences();
      if (prefs && typeof prefs === "object") {
        currentCustomPrefs = { ...DEFAULT_CUSTOM_PREFS, ...currentCustomPrefs, ...prefs };
        applyCustomPrefs(currentCustomPrefs);
        if (prefs.themeMode === "light" || prefs.themeMode === "dark") {
          applyTheme(prefs.themeMode, false);
        }
        if (typeof prefs.autoPlay === "boolean") {
          if (typeof appState !== "undefined") appState.autoPlayDisc = prefs.autoPlay;
          const label = document.getElementById("autoPlayLabel");
          if (label) label.textContent = prefs.autoPlay ? "已开启" : "已关闭";
          const apSwitch = document.getElementById("autoPlaySwitch");
          if (apSwitch) apSwitch.checked = prefs.autoPlay;
          if (label) label.textContent = prefs.autoPlay ? "已开启" : "已关闭";
        }
      }
    } catch (e) {
      console.warn("syncPreferencesFromBackend error:", e);
    }
  }

  // 页面脚本初始化时立即应用本地已保存的自定义外观设置
  loadCustomPrefs();

  function applyCustomPrefs(prefs) {
    const root = document.documentElement;
    root.style.setProperty("--lyrics-line-height", prefs.lyricLineHeight);
    const mb = Math.round(6 + (prefs.lyricLineHeight - 1.0) * 8);
    root.style.setProperty("--lyrics-margin-bottom", `${mb}px`);
    root.style.setProperty("--lyrics-letter-spacing", `${prefs.lyricLetterSpacing}px`);
    root.style.setProperty("--dock-custom-width", `${prefs.dockWidth}px`);
    const op = prefs.dockOpacity !== undefined ? Number(prefs.dockOpacity) : 5;
    const blurVal = prefs.dockBlur !== undefined ? Number(prefs.dockBlur) : 2;
    root.style.setProperty("--dock-custom-opacity", (op / 100).toFixed(2));
    root.style.setProperty("--dock-custom-blur", `${blurVal}px`);

    // 顶部栏三个功能圆钮与底部导航栏保持同一透明度与模糊度
    root.style.setProperty("--topbar-icon-opacity", (op / 100).toFixed(2));
    root.style.setProperty("--topbar-icon-blur", `${blurVal}px`);

    const dockEl = document.getElementById("bottomDockPlayer");
    if (dockEl) {
      dockEl.classList.toggle("is-fully-transparent", op === 0);
      dockEl.classList.toggle("is-compact", Number(prefs.dockWidth || 720) <= 580);
    }
    applyTranslationVisibility(prefs.showTranslation !== false);
    syncCustomSettingsInputs(prefs);
  }

  function syncCustomSettingsInputs(prefs) {
    const lhInput = document.getElementById("lyricLineHeightInput");
    const lhVal = document.getElementById("lyricLineHeightVal");
    if (lhInput) lhInput.value = prefs.lyricLineHeight;
    if (lhVal) lhVal.textContent = prefs.lyricLineHeight;

    const lsInput = document.getElementById("lyricLetterSpacingInput");
    const lsVal = document.getElementById("lyricLetterSpacingVal");
    if (lsInput) lsInput.value = prefs.lyricLetterSpacing;
    if (lsVal) lsVal.textContent = `${prefs.lyricLetterSpacing}px`;

    const dwInput = document.getElementById("dockWidthInput");
    const dwVal = document.getElementById("dockWidthVal");
    if (dwInput) dwInput.value = prefs.dockWidth;
    if (dwVal) dwVal.textContent = `${prefs.dockWidth}px`;

    const doInput = document.getElementById("dockOpacityInput");
    const doVal = document.getElementById("dockOpacityVal");
    const op = prefs.dockOpacity !== undefined ? Number(prefs.dockOpacity) : 5;
    if (doInput) doInput.value = op;
    if (doVal) doVal.textContent = `${op}%`;

    const dbInput = document.getElementById("dockBlurInput");
    const dbVal = document.getElementById("dockBlurVal");
    const blur = prefs.dockBlur !== undefined ? Number(prefs.dockBlur) : 2;
    if (dbInput) dbInput.value = blur;
    if (dbVal) dbVal.textContent = `${blur}px`;

    updateTopbarPreview(op, blur);

    const apSwitch = document.getElementById("autoPlaySwitch");
    if (apSwitch) {
      const isAuto = (prefs && typeof prefs.autoPlay === "boolean")
        ? prefs.autoPlay
        : ((typeof appState !== "undefined" && typeof appState.autoPlayDisc === "boolean")
            ? appState.autoPlayDisc
            : true);
      apSwitch.checked = isAuto;
    }

    const traySwitch = document.getElementById("closeToTraySwitch");
    if (traySwitch) {
      traySwitch.checked = (prefs && typeof prefs.closeToTray === "boolean") ? prefs.closeToTray : true;
    }

    if (typeof recenterActiveLyric === "function") {
      recenterActiveLyric();
    }
  }

  function updateTopbarPreview(op, blur) {
    const o = op !== undefined ? Number(op) : (currentCustomPrefs.dockOpacity !== undefined ? Number(currentCustomPrefs.dockOpacity) : 5);
    const b = blur !== undefined ? Number(blur) : (currentCustomPrefs.dockBlur !== undefined ? Number(currentCustomPrefs.dockBlur) : 2);
    const isLight = document.documentElement.getAttribute("data-theme") === "light";
    const rgb = isLight ? "255, 255, 255" : "28, 28, 32";
    const alpha = isLight ? Math.max(0.32, o / 100) : (o / 100);
    document.querySelectorAll(".topbar-preview-btn").forEach(el => {
      el.style.backgroundColor = `rgba(${rgb}, ${alpha.toFixed(2)})`;
      el.style.backdropFilter = `blur(${b}px)`;
      el.style.webkitBackdropFilter = `blur(${b}px)`;
    });
  }

  function switchSettingsTab(tabName) {
    document.querySelectorAll(".settings-tab-btn").forEach(btn => {
      btn.classList.toggle("active", btn.dataset.tab === tabName);
    });
    document.querySelectorAll(".settings-tab-pane").forEach(pane => {
      pane.classList.toggle("active", pane.dataset.tabPane === tabName);
    });
    const titleMap = {
      appearance: "外观与歌词",
      playback: "播放与硬件",
      storage: "存储与数据",
      about: "关于 Discflow"
    };
    const paneTitle = document.getElementById("settingsPaneTitle");
    if (paneTitle && titleMap[tabName]) {
      paneTitle.textContent = titleMap[tabName];
    }
    const body = document.querySelector(".settings-modal-body");
    if (body) {
      body.scrollTop = 0;
    }
  }

  (function bindSettingsPeekEvents() {
    const modal = document.getElementById("settingsModal");
    if (!modal) return;
    const stopPeek = () => modal.classList.remove("is-peeking");
    modal.querySelectorAll('input[type="range"]').forEach(rng => {
      rng.addEventListener("pointerdown", () => modal.classList.add("is-peeking"));
      rng.addEventListener("pointerup", stopPeek);
      rng.addEventListener("pointercancel", stopPeek);
    });
    window.addEventListener("pointerup", stopPeek);
  })();

  function onDockBlurChange(val) {
    const v = parseInt(val, 10);
    currentCustomPrefs.dockBlur = v;
    currentCustomPrefs.topbarBlur = v;
    applyCustomPrefs(currentCustomPrefs);
    saveCustomPrefs();
  }

  // --- 手动匹配专辑信息（应对光盘信息读取失败） ---
  function openMatchMetadataModal() {
    const t = document.getElementById("matchAlbumTitle");
    const a = document.getElementById("matchAlbumArtist");
    const hint = document.getElementById("matchMetaHint");
    const cur = appState.albumTitle || "";
    const curArtist = appState.artist || "";
    if (t) t.value = (cur && cur !== "Audio CD" && !cur.startsWith("等待")) ? cur : "";
    if (a) a.value = (curArtist && !curArtist.startsWith("未检测到") && !curArtist.startsWith("放入")) ? curArtist : "";
    if (hint) hint.textContent = "光盘信息读取失败时，在这里填写专辑名，Discflow 会自动在线检索并套用封面、年份与曲目名。";
    openModal("matchMetadataModal");
    setTimeout(() => { if (t) { t.focus(); t.select(); } }, 120);
  }

  // 等待后端整专辑歌词批量任务完成，期间把进度写进提示文案
  async function waitForLyricsBatch(maxMs = 180000) {
    const hint = document.getElementById("matchMetaHint");
    const startTs = Date.now();
    while (Date.now() - startTs < maxMs) {
      const b = appState.lyricsBatch || {};
      if (b.running) {
        if (hint) hint.textContent = `正在批量匹配歌词 ${b.done}/${b.total}，已命中 ${b.matched} 首…`;
      } else if ((b.total || 0) > 0) {
        return b;
      }
      await new Promise(r => setTimeout(r, 350));
    }
    return appState.lyricsBatch || {};
  }

  let albumCandidateCache = [];

  async function submitMatchMetadata() {
    const tEl = document.getElementById("matchAlbumTitle");
    const aEl = document.getElementById("matchAlbumArtist");
    const btn = document.getElementById("matchMetaSubmitBtn");
    const hint = document.getElementById("matchMetaHint");
    const box = document.getElementById("matchAlbumResults");
    const title = tEl ? tEl.value.trim() : "";
    const artist = aEl ? aEl.value.trim() : "";
    if (!title) {
      if (hint) hint.textContent = "请先填写专辑名称。";
      return;
    }
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.search_album_candidates_for_current) return;

    if (btn) { btn.disabled = true; btn.textContent = "检索中…"; }
    if (hint) hint.textContent = "正在从 QQ 音乐 / iTunes / 网易云 三源并发检索…";
    if (box) box.innerHTML = "";
    albumCandidateCache = [];
    try {
      const list = await window.pywebview.api.search_album_candidates_for_current(title, artist);
      albumCandidateCache = Array.isArray(list) ? list : [];
      renderAlbumCandidates(albumCandidateCache);
      if (hint) {
        hint.textContent = albumCandidateCache.length
          ? "点任意一条即可整张套用（专辑信息 + 曲名 + 封面 + 歌词）。"
          : "没有找到匹配的专辑，试试换个更准确的专辑名或补上艺术家。";
      }
    } catch (e) {
      console.error("search_album_candidates error:", e);
      if (hint) hint.textContent = "检索失败：" + e;
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = "搜索"; }
    }
  }

  function renderAlbumCandidates(list) {
    const box = document.getElementById("matchAlbumResults");
    if (!box) return;
    if (!list || !list.length) {
      box.innerHTML = '<div class="lyric-cand-empty">暂无候选结果</div>';
      return;
    }
    box.innerHTML = list.map((c, i) => {
      const label = LYRIC_SOURCE_LABEL[c.source] || c.source || "未知来源";
      const ids = (c.tracks || []).filter(t => t.song_id).length;
      const trackCount = (c.tracks || []).length || c.track_count || 0;
      const canDirect = ids > 0;
      const metaParts = [];
      metaParts.push(trackCount ? `<b>${trackCount}</b> 首` : "曲目数未知");
      if (canDirect) metaParts.push(`<b>${ids}</b> 首可直取歌词`);
      else if (c.source === "netease") metaParts.push("仅封面与专辑信息");
      else metaParts.push("歌词需按曲名搜索");
      // 曲库里有不少 Shift-JIS 误解码的残次条目，标出来避免用户选中后把乱码写进曲名
      if (c.garbled) metaParts.push(`<span class="album-cand-warn">⚠ ${c.dirtyTracks || 0} 首曲名疑似乱码</span>`);
      const trackPreview = (c.tracks || []).slice(0, 4).map(t => t.title).join(" / ");
      const cover = c.cover_url
        ? `<img class="album-cand-cover" src="${escapeHtml(c.cover_url)}" alt="" loading="lazy" />`
        : '<div class="album-cand-cover"></div>';
      return `
        <div class="album-cand" onclick="applyAlbumCandidate(${i})">
          ${cover}
          <div class="album-cand-body">
            <div class="album-cand-top">
              <span class="album-cand-src">${escapeHtml(label)}</span>
              <span class="album-cand-title">${escapeHtml(c.title || "")}</span>
              <span class="album-cand-year">${escapeHtml(c.year || "")} · ${escapeHtml(c.artist || "")}</span>
            </div>
            <div class="album-cand-meta">${metaParts.join(" · ")}</div>
            ${trackPreview ? `<div class="album-cand-tracks">${escapeHtml(trackPreview)}</div>` : ""}
          </div>
        </div>
      `;
    }).join("");
  }

  async function applyAlbumCandidate(index) {
    const cand = albumCandidateCache[index];
    if (!cand) return;
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.apply_album_candidate) return;
    const hint = document.getElementById("matchMetaHint");
    if (hint) hint.textContent = "正在套用专辑信息与封面…";
    try {
      const res = await window.pywebview.api.apply_album_candidate(cand);
      if (res && res.ok) {
        if (res.lyricsBatchStarted) {
          if (hint) hint.textContent = "专辑已套用，正在为整张专辑匹配歌词…";
          const b = await waitForLyricsBatch();
          const miss = (b.missed || []).length;
          if (hint) {
            hint.textContent = miss
              ? `整专辑歌词匹配完成：${b.matched || 0}/${b.total || 0} 首已套用，${miss} 首没对上——关掉弹窗后点曲目行上的「词」逐轨指定。`
              : `整专辑歌词匹配完成：${b.matched || 0}/${b.total || 0} 首已套用。`;
          }
          await new Promise(r => setTimeout(r, miss ? 1600 : 800));
        }
        closeModal("matchMetadataModal");
        triggerImmediatePoll();
        renderQueueList();
        if (res.skippedGarbled > 0) {
          alertToast(`已跳过 ${res.skippedGarbled} 个乱码曲名，保留本地曲名`);
        }
      } else if (hint) {
        hint.textContent = (res && res.reason) || "套用失败，换一条候选试试。";
      }
    } catch (e) {
      console.error("apply_album_candidate error:", e);
      if (hint) hint.textContent = "套用失败：" + e;
    }
  }

  // --- 逐轨手动匹配歌词 ---
  let lyricMatchTrackNumber = null;
  let lyricMatchDuration = 0;
  let lyricCandidateCache = [];

  const LYRIC_SOURCE_LABEL = { netease: "网易云", qq: "QQ 音乐", lrclib: "LRCLIB" };

  function openLyricMatchModal(event, idx) {
    if (event) event.stopPropagation();
    const t = (appState.tracks || [])[idx];
    if (!t) return;
    lyricMatchTrackNumber = t.track_number;
    lyricMatchDuration = Number(t.duration_sec) || 0;
    lyricCandidateCache = [];

    const info = document.getElementById("lyricMatchTrackInfo");
    if (info) {
      info.textContent = `第 ${t.track_number} 轨 · ${t.title} · ${t.artist} · ${formatTime(t.duration_sec)}`;
    }
    const tEl = document.getElementById("lyricMatchTitle");
    const aEl = document.getElementById("lyricMatchArtist");
    if (tEl) tEl.value = t.title || "";
    if (aEl) aEl.value = t.artist || "";
    const box = document.getElementById("lyricMatchResults");
    if (box) box.innerHTML = "";
    const hint = document.getElementById("lyricMatchHint");
    if (hint) hint.textContent = "点击「搜索」从网易云 / QQ 音乐 / LRCLIB 同时检索。";

    openModal("lyricMatchModal");
    searchLyricCandidates();
  }

  async function searchLyricCandidates() {
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.search_track_lyric_candidates) return;
    const hint = document.getElementById("lyricMatchHint");
    const btn = document.getElementById("lyricMatchSearchBtn");
    const tEl = document.getElementById("lyricMatchTitle");
    const aEl = document.getElementById("lyricMatchArtist");
    const box = document.getElementById("lyricMatchResults");
    const title = tEl ? tEl.value.trim() : "";
    const artist = aEl ? aEl.value.trim() : "";

    if (btn) { btn.disabled = true; btn.textContent = "检索中…"; }
    if (hint) hint.textContent = "正在同时检索网易云 / QQ 音乐 / LRCLIB…";
    if (box) box.innerHTML = "";
    try {
      const list = await window.pywebview.api.search_track_lyric_candidates(lyricMatchTrackNumber, title, artist);
      lyricCandidateCache = Array.isArray(list) ? list : [];
      renderLyricCandidates(lyricCandidateCache);
      if (hint) {
        hint.textContent = lyricCandidateCache.length
          ? "点任意一条即可套用并锁定，锁定后自动匹配不会再覆盖。"
          : "没有找到候选，试试把歌名改得更简短或换一种写法。";
      }
    } catch (e) {
      console.error("search_track_lyric_candidates error:", e);
      if (hint) hint.textContent = "检索失败：" + e;
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = "搜索"; }
    }
  }

  function renderLyricCandidates(list) {
    const box = document.getElementById("lyricMatchResults");
    if (!box) return;
    if (!list || !list.length) {
      box.innerHTML = '<div class="lyric-cand-empty">暂无候选结果</div>';
      return;
    }
    box.innerHTML = list.map((c, i) => {
      const label = LYRIC_SOURCE_LABEL[c.source] || c.source || "未知来源";
      const dur = Number(c.duration_sec) || 0;
      const diff = (dur > 0 && lyricMatchDuration > 0) ? Math.abs(dur - lyricMatchDuration) : 0;
      const parts = [];
      if (c.line_count) parts.push(`${c.line_count} 行`);
      parts.push(c.has_translation ? "有翻译" : "无翻译");
      parts.push(c.has_words ? "有逐字" : "无逐字");
      if (diff > 0.15) parts.push(`时长差 ${diff.toFixed(1)}s`);
      const meta = parts.map(p => (p === "有翻译" || p === "有逐字") ? `<b>${p}</b>` : p).join(" · ");
      return `
        <div class="lyric-cand" onclick="applyLyricCandidate(${i})">
          <div class="lyric-cand-top">
            <span class="lyric-cand-src">${escapeHtml(label)}</span>
            <span class="lyric-cand-title">${escapeHtml(c.title || "")}</span>
            <span class="lyric-cand-artist">${escapeHtml(c.artist || "")}</span>
            <span class="lyric-cand-dur">${dur > 0 ? formatTime(dur) : "--"}</span>
          </div>
          <div class="lyric-cand-meta">${meta}</div>
          ${c.preview ? `<div class="lyric-cand-preview">${escapeHtml(c.preview)}</div>` : ""}
        </div>
      `;
    }).join("");
  }

  async function applyLyricCandidate(index) {
    const cand = lyricCandidateCache[index];
    if (!cand) return;
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.apply_track_lyrics) return;
    const hint = document.getElementById("lyricMatchHint");
    if (hint) hint.textContent = "正在套用并锁定这首歌的歌词…";
    try {
      const res = await window.pywebview.api.apply_track_lyrics(
        lyricMatchTrackNumber, cand.source, String(cand.song_id || ""),
        cand.title || "", cand.artist || "", Number(cand.duration_sec) || 0
      );
      if (res && res.ok) {
        if (hint) hint.textContent = `已套用 ${res.lineCount} 行歌词，并锁定该曲目。`;
        closeModal("lyricMatchModal");
        triggerImmediatePoll();
        renderQueueList();
      } else if (hint) {
        hint.textContent = (res && res.reason) || "套用失败，换一条候选试试。";
      }
    } catch (e) {
      console.error("apply_track_lyrics error:", e);
      if (hint) hint.textContent = "套用失败：" + e;
    }
  }

  async function clearTrackLyrics() {
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.clear_track_lyrics) return;
    const hint = document.getElementById("lyricMatchHint");
    try {
      await window.pywebview.api.clear_track_lyrics(lyricMatchTrackNumber);
      if (hint) hint.textContent = "已清除该曲目的歌词并解除锁定。";
      triggerImmediatePoll();
      renderQueueList();
    } catch (e) {
      if (hint) hint.textContent = "清除失败：" + e;
    }
  }

  async function autoMatchTrackLyrics() {
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.auto_match_track_lyrics) return;
    const hint = document.getElementById("lyricMatchHint");
    if (hint) hint.textContent = "正在按曲名重新自动匹配…";
    try {
      const res = await window.pywebview.api.auto_match_track_lyrics(lyricMatchTrackNumber);
      if (hint) {
        hint.textContent = (res && res.ok)
          ? `自动匹配到 ${res.lineCount} 行歌词，已解除锁定。`
          : "自动匹配没有找到歌词，可手动搜索选择。";
      }
      triggerImmediatePoll();
      renderQueueList();
    } catch (e) {
      if (hint) hint.textContent = "自动匹配失败：" + e;
    }
  }

  function openCustomSettingsModal() {
    syncCustomSettingsInputs(currentCustomPrefs);
    openModal("settingsModal");
    switchSettingsTab("appearance");
  }

  function onLyricLineHeightChange(val) {
    currentCustomPrefs.lyricLineHeight = parseFloat(val);
    applyCustomPrefs(currentCustomPrefs);
    saveCustomPrefs();
  }

  function onLyricLetterSpacingChange(val) {
    currentCustomPrefs.lyricLetterSpacing = parseFloat(val);
    applyCustomPrefs(currentCustomPrefs);
    saveCustomPrefs();
  }

  function onDockWidthChange(val) {
    currentCustomPrefs.dockWidth = parseInt(val, 10);
    applyCustomPrefs(currentCustomPrefs);
    saveCustomPrefs();
  }

  function onDockOpacityChange(val) {
    const v = parseInt(val, 10);
    currentCustomPrefs.dockOpacity = v;
    currentCustomPrefs.topbarOpacity = v;
    applyCustomPrefs(currentCustomPrefs);
    saveCustomPrefs();
  }

  function resetCustomSettings() {
    currentCustomPrefs = { ...DEFAULT_CUSTOM_PREFS };
    applyCustomPrefs(currentCustomPrefs);
    saveCustomPrefs();
  }
  // ================= Discflow 专属弹出菜单交互 =================
  let isDiscflowMenuOpen = false;

    // 底部控制栏音量滑块展开/收起
  let isDockVolumeSliderOpen = false;

  function toggleDockVolumeSlider(event) {
    if (event) {
      event.preventDefault();
      event.stopPropagation();
    }
    isDockVolumeSliderOpen = !isDockVolumeSliderOpen;
    const wrap = document.getElementById("dockVolumeWrap");
    if (wrap) {
      wrap.classList.toggle("is-open", isDockVolumeSliderOpen);
    }
  }

  function closeDockVolumeSlider() {
    isDockVolumeSliderOpen = false;
    const wrap = document.getElementById("dockVolumeWrap");
    if (wrap) wrap.classList.remove("is-open");
  }

  // 封面与底栏缩略图点击：切换歌词与曲目列表
  function toggleLyricsAndTracklist(event) {
    if (event) {
      event.preventDefault();
      event.stopPropagation();
    }
    switchMainView("cd");
    if (appState.rightPanelMode === "queue") {
      switchRightPanel("lyrics");
    } else {
      switchRightPanel("queue");
    }
  }
  const toggleRightPanelFromDock = toggleLyricsAndTracklist;

function toggleDiscflowMenu(event) {
    if (event) {
      event.preventDefault();
      event.stopPropagation();
    }
    isDiscflowMenuOpen = !isDiscflowMenuOpen;
    const menu = document.getElementById("discflowAppMenu");
    if (menu) {
      if (isDiscflowMenuOpen) {
        menu.classList.add("is-open");
      } else {
        menu.classList.remove("is-open");
      }
    }
  }

  function closeDiscflowMenu() {
    isDiscflowMenuOpen = false;
    const menu = document.getElementById("discflowAppMenu");
    if (menu) menu.classList.remove("is-open");
  }

  document.addEventListener("click", (e) => {
    const btn = document.getElementById("discflowAppBtn");
    const menu = document.getElementById("discflowAppMenu");
    if (!(btn && btn.contains(e.target)) && !(menu && menu.contains(e.target))) {
      if (isDiscflowMenuOpen) closeDiscflowMenu();
    }
    // 音量弹层同理：点空白处收起，避免一直挂在底栏上方
    const volWrap = document.getElementById("dockVolumeWrap");
    if (isDockVolumeSliderOpen && volWrap && !volWrap.contains(e.target)) {
      closeDockVolumeSlider();
    }
  });

  function selectDiscflowMenu(action) {
    closeDiscflowMenu();
    if (action === "library") {
      switchMainView("library");
    } else if (action === "search") {
      openSearchModal();
    } else if (action === "settings") {
      openModal("settingsModal");
    }
  }

  function openAboutModalFromSettings() {
    openModal("settingsModal");
    switchSettingsTab("about");
  }

  async function confirmClearAllData() {
    if (!confirm("⚠️ 确定要清空 Discflow 本地资料库数据吗？\n所有已转录归档的唱片音频将被彻底删除！此操作不可恢复。")) {
      return;
    }
    try {
      if (window.pywebview && window.pywebview.api && window.pywebview.api.clear_all_library_data) {
        await window.pywebview.api.clear_all_library_data();
      }
      await renderLibraryCards();
      alert("本地资料库已完全清空。");
    } catch (e) {
      console.error("clear_all_library_data error:", e);
      alert("清空数据失败: " + e);
    }
  }

  async function confirmClearLyricsCache() {
    try {
      if (window.pywebview && window.pywebview.api && window.pywebview.api.clear_lyrics_cache) {
        await window.pywebview.api.clear_lyrics_cache();
      }
      alert("双语歌词缓存已全部清空，将在切歌时重新从源头匹配。");
    } catch (e) {
      console.error("clear_lyrics_cache error:", e);
    }
  }

  // 打开资源库物理存储目录（便携优先策略解析出的实际路径）
  async function openLibraryFolder() {
    try {
      if (window.pywebview && window.pywebview.api && window.pywebview.api.open_library_folder) {
        const res = await window.pywebview.api.open_library_folder();
        if (res && res.ok === false) {
          alert("无法打开资源库目录：\n" + (res.path || "路径不可用"));
        }
        return;
      }
      alert("资源库目录功能尚未就绪，请稍后重试。");
    } catch (e) {
      console.error("open_library_folder error:", e);
    }
  }

  // 将后端解析出的真实资源库路径回填到设置菜单与按钮提示
  async function syncLibraryPathText() {
    try {
      if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.get_library_dir) return;
      const dir = await window.pywebview.api.get_library_dir();
      if (!dir) return;
      const sub = document.getElementById("libraryPathSub");
      if (sub) sub.textContent = "存储于：" + dir + " (离线无损免光驱畅播)";
      const btn = document.getElementById("openLibraryFolderBtn");
      if (btn) btn.title = "在文件管理器中打开 " + dir;
    } catch (e) {
      console.warn("get_library_dir error:", e);
    }
  }

  // 可编程复用的 Discflow 极简黑白 SVG 图标生成函数 (支持 24x24 或任意尺寸挂载)
  function createDiscflowIconMarkup(size = 24) {
    return `
      <svg class="discflow-icon" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-label="Discflow">
        <rect x="2.5" y="2.5" width="19" height="19" rx="5.2" />
        <circle cx="11.2" cy="12" r="5.6" />
        <path d="M8.4 9.5a3.8 3.8 0 0 1 2.8-1.1" stroke-width="1.6" opacity="0.45" />
        <path d="M14.0 14.5a3.8 3.8 0 0 1-2.8 1.1" stroke-width="1.6" opacity="0.45" />
        <circle cx="11.2" cy="12" r="1.6" />
        <circle cx="17.8" cy="6.8" r="1.05" fill="currentColor" stroke="none" />
        <path d="M17.8 6.8v4.8l-2.5 2.2" />
      </svg>
    `;
  }

  let appState = {
    drive: "",
    drives: [],
    discId: "",
    freedbId: "",
    albumTitle: "等待放入音频 CD",
    artist: "未检测到音频光盘 · 放入 CD 后将自动识别",
    year: "",
    source: "",
    coverDataUrl: "",
    tracks: [],
    currentTrackIndex: 0,
    isPlaying: false,
    elapsedSec: 0,
    durationSec: 0,
    volume: 80,
    playMode: "order", // "order" | "shuffle" | "one"
    rightPanelMode: "lyrics",
    themeMode: "dark",
    lyrics: [],
    activeLyricIndex: -1,
    lastCoverSignature: "",
    showTranslation: currentCustomPrefs.showTranslation !== false,
    autoPlayDisc: true
  };
  window._discflowAppReady = true;

  function openModal(id) {
    const el = document.getElementById(id);
    if (el) el.classList.add("open");
  }

  function closeModal(id) {
    const el = document.getElementById(id);
    if (el) el.classList.remove("open");
  }

  function closeModalOnBackdrop(e, id) {
    if (e.target && e.target.id === id) {
      closeModal(id);
    }
  }

  async function openAlipayPayment() {
    const alipayUrl = "https://qr.alipay.com/fkx105619v8yxtwwtzxpea1";
    try {
      if (window.pywebview && window.pywebview.api && window.pywebview.api.open_external_url) {
        await window.pywebview.api.open_external_url(alipayUrl);
      } else {
        window.open(alipayUrl, "_blank");
      }
    } catch (e) {
      console.error("openAlipayPayment error:", e);
      window.open(alipayUrl, "_blank");
    }
  }

  const PLAY_MODES = ["order", "shuffle", "one"];
  const PLAY_MODE_INFO = {
    order: {
      title: "当前：顺序播放 (列表循环)",
      icon: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 2l4 4-4 4"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><path d="M7 22l-4-4 4-4"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>`
    },
    shuffle: {
      title: "当前：随机播放",
      icon: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 3h5v5"/><path d="M4 20l6.2-6.2M21 3l-7.8 7.8"/><path d="M21 21h-5v-5"/><path d="M15.2 15.2l5.8 5.8M4 4l9.6 9.6"/></svg>`
    },
    one: {
      title: "当前：单曲循环",
      icon: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 2l4 4-4 4"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><path d="M7 22l-4-4 4-4"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/><path d="M11.2 10.5l1.6-1.1v5.2m-1.2 0h2.4" stroke-width="1.8"/></svg>`
    }
  };

  function cyclePlayMode() {
    const curIdx = PLAY_MODES.indexOf(appState.playMode);
    const nextMode = PLAY_MODES[(curIdx + 1) % PLAY_MODES.length];
    appState.playMode = nextMode;
    applyPlayModeUI(nextMode);
    if (window.pywebview && window.pywebview.api) {
      window.pywebview.api.set_play_mode(nextMode);
    }
  }

  function applyPlayModeUI(mode) {
    const btn = document.getElementById("playModeBtn");
    if (!btn) return;
    const info = PLAY_MODE_INFO[mode] || PLAY_MODE_INFO.order;
    btn.innerHTML = info.icon;
    btn.title = info.title;
  }

  // 双语翻译显示开关（底部导航栏按钮 + 菜单项共用）
  // 用 <html> 上的 no-translation 类控制，歌词重新渲染后依然生效；
  // 注意：本函数在脚本初始化阶段也会被调用，此时 appState 尚未声明，
  // 因此这里只操作 DOM，不直接读写 appState，避免 TDZ 报错。
  function applyTranslationVisibility(show) {
    document.documentElement.classList.toggle("no-translation", !show);
    document.querySelectorAll("#transToggleLabel").forEach(el => {
      el.textContent = show ? "已开启" : "已关闭";
    });
    const btn = document.getElementById("dockTransBtn");
    if (btn) {
      btn.classList.toggle("is-off", !show);
      btn.setAttribute("aria-pressed", show ? "true" : "false");
      btn.title = show ? "双语翻译：已开启（点击关闭）" : "双语翻译：已关闭（点击开启）";
    }
    document.querySelectorAll(".lyric-trans").forEach(el => {
      el.style.display = show ? "" : "none";
    });
  }

  function toggleBilingualLyrics() {
    const next = !appState.showTranslation;
    appState.showTranslation = next;
    currentCustomPrefs.showTranslation = next;
    applyTranslationVisibility(next);
    saveCustomPrefs();
    if (typeof recenterActiveLyric === "function") {
      recenterActiveLyric();
    }
  }

  function toggleAutoPlay() {
    if (typeof appState !== "undefined") {
      appState.autoPlayDisc = !appState.autoPlayDisc;
      const isAuto = appState.autoPlayDisc;
      const label = document.getElementById("autoPlayLabel");
      if (label) label.textContent = isAuto ? "已开启" : "已关闭";
      const apSwitch = document.getElementById("autoPlaySwitch");
      if (apSwitch) apSwitch.checked = isAuto;
      currentCustomPrefs.autoPlay = isAuto;
      saveCustomPrefs();
      if (window.pywebview && window.pywebview.api && window.pywebview.api.save_preferences) {
        window.pywebview.api.save_preferences({ autoPlay: !!isAuto }).catch(() => {});
      }
    }
  }

  function toggleCloseToTray() {
    const sw = document.getElementById("closeToTraySwitch");
    const next = sw ? !!sw.checked : true;
    currentCustomPrefs.closeToTray = next;
    saveCustomPrefs();
    if (window.pywebview && window.pywebview.api) {
      if (window.pywebview.api.set_close_to_tray) {
        window.pywebview.api.set_close_to_tray(next).catch(() => {});
      } else if (window.pywebview.api.save_preferences) {
        window.pywebview.api.save_preferences({ closeToTray: next }).catch(() => {});
      }
    }
    showToast(next ? "已开启：关闭窗口时最小化到托盘" : "已关闭：点击关闭直接退出程序");
  }

  let isDraggingScrubber = false;
  let isDraggingVolume = false;
  let userScrollLockUntil = 0;

  const lyricsViewport = document.getElementById("lyricsViewport");
  if (lyricsViewport) {
    lyricsViewport.addEventListener("wheel", () => {
      userScrollLockUntil = Date.now() + 2600;
    }, { passive: true });
  }
  const standaloneLyricsVp = document.getElementById("standaloneLyricsViewport");
  if (standaloneLyricsVp) {
    standaloneLyricsVp.addEventListener("wheel", () => {
      userScrollLockUntil = Date.now() + 2600;
    }, { passive: true });
  }

  function formatTime(sec) {
    const s = Math.max(0, Math.floor(sec || 0));
    const m = Math.floor(s / 60);
    const r = s % 60;
    return `${m}:${r < 10 ? "0" : ""}${r}`;
  }

  function extractPaletteFromDataUrl(dataUrl) {
    const meshEl = document.getElementById("meshCanvas");
    const ambientEl = document.getElementById("ambientShadow");
    const imgEl = document.getElementById("artworkImg");
    const placeholderEl = document.getElementById("discPlaceholder");

    const backdropEl = document.getElementById("artBackdrop");

    if (!dataUrl) {
      imgEl.classList.remove("loaded");
      placeholderEl.style.display = "flex";
      meshEl.classList.remove("has-cover");
      ambientEl.style.backgroundImage = "none";
      if (backdropEl) backdropEl.style.backgroundImage = "none";
      document.body.classList.remove("has-artwork");
      return;
    }

    const tempImg = new Image();
    tempImg.onload = () => {
      imgEl.src = dataUrl;
      imgEl.classList.add("loaded");
      placeholderEl.style.display = "none";
      ambientEl.style.backgroundImage = `url("${dataUrl}")`;
      if (backdropEl) backdropEl.style.backgroundImage = `url("${dataUrl}")`;
      document.body.classList.add("has-artwork");
      meshEl.classList.add("has-cover");

      try {
        const canvas = document.createElement("canvas");
        const size = 48;
        canvas.width = size;
        canvas.height = size;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(tempImg, 0, 0, size, size);
        const data = ctx.getImageData(0, 0, size, size).data;

        const buckets = [
          { r: 0, g: 0, b: 0, count: 0 },
          { r: 0, g: 0, b: 0, count: 0 },
          { r: 0, g: 0, b: 0, count: 0 }
        ];

        for (let i = 0; i < data.length; i += 16) {
          const r = data[i], g = data[i + 1], b = data[i + 2];
          const max = Math.max(r, g, b), min = Math.min(r, g, b);
          const luma = 0.299 * r + 0.587 * g + 0.114 * b;
          const sat = max === 0 ? 0 : (max - min) / max;

          if (luma < 22 || luma > 240) continue;
          const weight = 1 + sat * 2.5;
          const bucketIdx = (i / 16) % 3;
          buckets[bucketIdx].r += r * weight;
          buckets[bucketIdx].g += g * weight;
          buckets[bucketIdx].b += b * weight;
          buckets[bucketIdx].count += weight;
        }

        const colors = buckets.map((bk, idx) => {
          if (bk.count <= 0) {
            return idx === 0 ? "rgb(68, 52, 110)" : idx === 1 ? "rgb(36, 78, 115)" : "rgb(95, 45, 82)";
          }
          const r = Math.min(235, Math.max(36, Math.round(bk.r / bk.count)));
          const g = Math.min(235, Math.max(36, Math.round(bk.g / bk.count)));
          const b = Math.min(235, Math.max(36, Math.round(bk.b / bk.count)));
          return `rgb(${r}, ${g}, ${b})`;
        });

        document.documentElement.style.setProperty("--mesh-c1", colors[0]);
        document.documentElement.style.setProperty("--mesh-c2", colors[1]);
        document.documentElement.style.setProperty("--mesh-c3", colors[2]);
        // 动态自适应彩胶盘面与沉浸背景色彩提取
        document.documentElement.style.setProperty("--ambient-primary", colors[0]);
        document.documentElement.style.setProperty("--ambient-secondary", colors[1]);
        document.documentElement.style.setProperty("--vinyl-tint-highlight", colors[1]);
        document.documentElement.style.setProperty("--vinyl-tint-base", colors[0]);
        document.documentElement.style.setProperty("--vinyl-tint-dark", colors[2]);
      } catch (e) {
        console.warn("Palette extraction skipped:", e);
      }
    };
    tempImg.src = dataUrl;
  }

  // 逐字歌词分词：中文按单字、英文数字按整词（含尾随空格），标点单独成词
  const KARAOKE_TOKEN_RE = /[A-Za-z0-9'’\-]+\s*|[\u4e00-\u9fff]|[^\sA-Za-z0-9\u4e00-\u9fff]/g;

  function tokenizeKaraokeLine(text) {
    const tokens = [];
    KARAOKE_TOKEN_RE.lastIndex = 0;
    let m;
    while ((m = KARAOKE_TOKEN_RE.exec(text)) !== null) {
      tokens.push(m[0]);
    }
    return tokens;
  }

  // 为缺少逐字时间轴的行合成按字词长度加权的均匀时间轴，
  // 保证任何有同步歌词的歌都能获得逐字卡拉 OK 效果（解决「有的歌有、有的歌没有」）
  function ensureWordTimings(lyrics) {
    if (!Array.isArray(lyrics) || !lyrics.length) return lyrics;
    for (let i = 0; i < lyrics.length; i++) {
      const line = lyrics[i];
      if (!line) continue;
      if (Array.isArray(line.words) && line.words.length) continue;
      const text = String(line.text || "");
      if (!text.trim()) continue;

      const start = Number(line.time) || 0;
      let end;
      if (i + 1 < lyrics.length) {
        end = Number(lyrics[i + 1].time) || (start + 3);
      } else {
        end = start + Math.min(6, Math.max(2, text.length * 0.3));
      }
      const tokens = tokenizeKaraokeLine(text);
      if (!tokens.length) continue;
      // 防止句间长间奏被均摊进普通 LRC 最后一个字，导致逐字与人声严重拖节
      const rawGap = Math.max(0.55, end - start);
      const effectiveGap = rawGap > 1.2 ? rawGap - 0.15 : rawGap;
      const estimatedSungDur = Math.max(1.4, tokens.length * 0.36);
      const dur = Math.min(effectiveGap, estimatedSungDur);

      const weights = tokens.map(tk => Math.max(1, (tk.trim() || tk).length));
      const totalW = weights.reduce((a, b) => a + b, 0) || 1;

      let cursor = start;
      line.words = tokens.map((tk, idx) => {
        const d = dur * (weights[idx] / totalW);
        const item = {
          t: Number(cursor.toFixed(3)),
          d: Number(Math.max(0.05, d).toFixed(3)),
          w: tk
        };
        cursor += d;
        return item;
      });
    }
    return lyrics;
  }

  function renderLyricsList(lyrics) {
    lyrics = ensureWordTimings(lyrics);
    appState.activeLyricIndex = -1;
    userScrollLockUntil = 0;

    const viewports = [
      document.getElementById("lyricsViewport"),
      document.getElementById("standaloneLyricsViewport")
    ].filter(Boolean);

    viewports.forEach(vp => {
      vp.innerHTML = "";
      if (!lyrics || lyrics.length === 0) {
        const hasDisc = (appState.tracks || []).length > 0;
        vp.innerHTML = hasDisc
          ? `
          <div class="lyrics-empty">
            <div class="lyrics-empty-title">纯享 CD 原始无损音轨</div>
            <div class="lyrics-empty-sub">当前曲目为纯音乐或暂无时间轴歌词</div>
          </div>
        `
          : `
          <div class="lyrics-empty">
            <div class="lyrics-empty-title">等待放入音频 CD</div>
            <div class="lyrics-empty-sub">插入光盘后会自动读取曲目与歌词，也可以从「资料库」直接播放已归档的唱片</div>
          </div>
        `;
        return;
      }

      lyrics.forEach((line, idx) => {
        const div = document.createElement("div");
        div.className = "lyric-item";
        div.dataset.index = idx;

        const orig = document.createElement("div");
        orig.className = "lyric-orig";
        if (Array.isArray(line.words) && line.words.length) {
          line.words.forEach(w => {
            const span = document.createElement("span");
            span.className = "lyric-word";
            const wText = w.w || "";
            span.dataset.t = String(typeof w.t === "number" ? w.t : line.time);
            span.dataset.d = String(typeof w.d === "number" && w.d > 0 ? w.d : 0.25);
            span.dataset.text = wText;
            span.textContent = wText;
            orig.appendChild(span);
          });
        } else {
          orig.textContent = line.text;
        }
        div.appendChild(orig);

        if (line.translation) {
          const trans = document.createElement("div");
          trans.className = "lyric-trans";
          trans.textContent = line.translation;
          div.appendChild(trans);
        }

        div.addEventListener("click", () => {
          userScrollLockUntil = 0;
          if (window.pywebview && window.pywebview.api) {
            const seekTarget = Math.max(0, line.time + (Number(currentTrackLyricOffset) || 0.0));
            window.pywebview.api.seek_to(seekTarget);
          }
        });

        vp.appendChild(div);
      });
    });

    applyTranslationVisibility(appState.showTranslation !== false);
    syncKaraokeAnchor();
    const curSec = appState.isPlaying ? karaokeClock : (Number(appState.elapsedSec) || 0);
    updateActiveLyric(curSec, true);
    renderKaraokeWords(appState.activeLyricIndex, curSec - (Number(currentTrackLyricOffset) || 0.0));
  }

  let currentTrackLyricOffset = 0.0;

  function adjustLyricOffset(delta) {
    currentTrackLyricOffset = Math.round((currentTrackLyricOffset + delta) * 10) / 10;
    applyLyricOffset();
  }

  function resetLyricOffset() {
    currentTrackLyricOffset = 0.0;
    applyLyricOffset();
  }

  function applyLyricOffset() {
    updateActiveLyric(appState.elapsedSec, true);
    const sign = currentTrackLyricOffset > 0 ? "+" : "";
    const tip = currentTrackLyricOffset === 0 ? "歌词时间轴已归零" : `歌词时间轴: ${sign}${currentTrackLyricOffset.toFixed(1)}s`;
    alertToast(tip);
    if (window.pywebview && window.pywebview.api && window.pywebview.api.set_track_lyric_offset) {
      window.pywebview.api.set_track_lyric_offset(currentTrackLyricOffset).catch(() => {});
    }
  }

  function recenterActiveLyric() {
    if (!window._discflowAppReady || !appState.lyrics || appState.lyrics.length === 0) return;
    userScrollLockUntil = 0;
    requestAnimationFrame(() => {
      const curSec = appState.isPlaying ? karaokeClock : (Number(appState.elapsedSec) || 0);
      updateActiveLyric(curSec, true);
      if (!appState.isPlaying) {
        renderKaraokeWords(
          appState.activeLyricIndex,
          curSec - (Number(currentTrackLyricOffset) || 0.0)
        );
      }
    });
  }

  function updateActiveLyric(elapsedSec, forceUpdate = false) {
    const lyrics = appState.lyrics;
    if (!lyrics || lyrics.length === 0) return;

    // 统一时间基准：硬件真声卡位置已消除队列滞后，直接应用用户微调偏移即可
    const effectiveProbe = (Number(elapsedSec) || 0) - (Number(currentTrackLyricOffset) || 0.0);

    // 若当前时间早于首句歌词（如刚打开应用、刚切歌或前奏阶段），默认聚焦首句，避免整页歌词全暗无高亮
    let activeIdx = 0;
    for (let i = lyrics.length - 1; i >= 0; i--) {
      if (effectiveProbe >= lyrics[i].time) {
        activeIdx = i;
        break;
      }
    }

    if (!forceUpdate && activeIdx === appState.activeLyricIndex) return;
    appState.activeLyricIndex = activeIdx;

    const viewports = [
      document.getElementById("lyricsViewport"),
      document.getElementById("standaloneLyricsViewport")
    ].filter(Boolean);

    viewports.forEach(vp => {
      const items = vp.querySelectorAll(".lyric-item");
      items.forEach((el, idx) => {
        el.classList.remove("active", "near");
        if (activeIdx >= 0) {
          if (idx === activeIdx) {
            el.classList.add("active");
          } else if (Math.abs(idx - activeIdx) === 1) {
            el.classList.add("near");
          }
        }
      });

      if (activeIdx >= 0 && Date.now() > userScrollLockUntil && items[activeIdx]) {
        const targetEl = items[activeIdx];
        const vpRect = vp.getBoundingClientRect();
        if (vpRect.height > 20) {
          const elRect = targetEl.getBoundingClientRect();
          const currentCenter = elRect.top + elRect.height * 0.5;
          const desiredCenter = vpRect.top + vpRect.height * 0.38;
          const maxScroll = Math.max(0, vp.scrollHeight - vp.clientHeight);
          const targetTop = Math.min(maxScroll, Math.max(0, vp.scrollTop + (currentCenter - desiredCenter)));
          smoothScrollViewport(vp, targetTop, 260);
        }
      }
    });
  }

  const _vpScrollAnim = new WeakMap();
  function smoothScrollViewport(vp, targetTop, durationMs = 220) {
    const prevId = _vpScrollAnim.get(vp);
    if (prevId) cancelAnimationFrame(prevId);
    const startTop = vp.scrollTop;
    const diff = targetTop - startTop;
    if (Math.abs(diff) < 2) {
      vp.scrollTop = targetTop;
      return;
    }
    const startTime = performance.now();
    const tick = (now) => {
      const t = Math.min(1, (now - startTime) / durationMs);
      const eased = 1 - Math.pow(1 - t, 3);
      vp.scrollTop = startTop + diff * eased;
      if (t < 1) {
        _vpScrollAnim.set(vp, requestAnimationFrame(tick));
      } else {
        _vpScrollAnim.delete(vp);
      }
    };
    _vpScrollAnim.set(vp, requestAnimationFrame(tick));
  }

  // --- 逐字歌词卡拉 OK 渲染循环（单向防回退时钟 + 弹性追帧，彻底消除回退抽搐） ---
  let karaokeRafId = null;
  let karaokeClock = 0;      // 当前播放位置的连续估计值（秒）
  let karaokeLastTs = 0;     // 上一帧时间戳
  let karaokeTrackKey = "";  // 碟片 + 曲目标识，用于切歌时硬重置
  let karaokeRate = 1.0;     // 弹性追帧速率（0.72 ~ 1.22），绝不往回倒退

  function renderKaraokeWords(idx, sec) {
    if (idx < 0) return;
    const lyrics = appState.lyrics;
    if (!lyrics || !lyrics.length) return;
    const line = lyrics[idx];
    if (!line || !Array.isArray(line.words) || !line.words.length) return;
    document.querySelectorAll(`.lyric-item[data-index="${idx}"]`).forEach(item => {
      item.querySelectorAll(".lyric-word").forEach(wEl => {
        const t = parseFloat(wEl.dataset.t || "0");
        const d = Math.max(0.08, parseFloat(wEl.dataset.d || "0.25"));
        const p = Math.max(0, Math.min(1, (sec - t) / d));
        wEl.style.setProperty("--wp", (p * 100).toFixed(1) + "%");
      });
    });
  }

  function syncKaraokeAnchor() {
    const backend = Number(appState.elapsedSec) || 0;
    const key = `${appState.discId || ""}#${appState.currentTrackIndex || 0}`;

    if (key !== karaokeTrackKey) {
      karaokeTrackKey = key;
      karaokeClock = backend;
      karaokeLastTs = 0;
      karaokeRate = 1.0;
      return;
    }

    if (!appState.isPlaying) {
      karaokeClock = backend;
      karaokeLastTs = 0;
      karaokeRate = 1.0;
      return;
    }

    const drift = backend - karaokeClock;
    if (Math.abs(drift) > 0.65) {
      // 拖动进度条 Seek 或明显跳变：允许硬同步
      karaokeClock = backend;
      karaokeRate = 1.0;
    } else if (drift >= 0) {
      // 前端略慢：平滑加速追帧
      karaokeRate = Math.min(1.22, 1.0 + drift * 0.65);
    } else {
      // 前端略快于后端离散回调（-0.65s ~ 0）：严禁往回倒退，仅轻微降速等后端追上
      karaokeRate = Math.max(0.72, 1.0 + drift * 0.90);
    }
  }

  function startKaraokeLoop() {
    if (karaokeRafId) return;
    const step = () => {
      karaokeRafId = requestAnimationFrame(step);
      const now = performance.now();
      if (appState.isPlaying) {
        const dt = karaokeLastTs ? (now - karaokeLastTs) / 1000 : 0;
        karaokeClock += Math.min(Math.max(dt, 0), 0.25) * karaokeRate;
      }
      karaokeLastTs = now;

      const lyrics = appState.lyrics;
      if (!lyrics || !lyrics.length) return;
      const baseSec = appState.isPlaying ? karaokeClock : (Number(appState.elapsedSec) || 0);
      if (appState.isPlaying) {
        updateActiveLyric(baseSec, false);
      }
      renderKaraokeWords(appState.activeLyricIndex, baseSec - (Number(currentTrackLyricOffset) || 0.0));
    };
    karaokeRafId = requestAnimationFrame(step);
  }

  function setPlayMode(mode) {
    if (["order", "shuffle", "one"].includes(mode)) {
      appState.playMode = mode;
      applyPlayModeUI(mode);
      if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.set_play_mode(mode);
      }
    }
  }

  function applyPlayModeUI(mode) {
    const btns = document.querySelectorAll(".mode-pill-btn");
    btns.forEach(btn => {
      btn.classList.toggle("active", btn.dataset.mode === mode);
    });
  }

  function renderQueueList() {
    const qEl = document.getElementById("queueViewport");
    const tracks = appState.tracks || [];
    document.getElementById("queueCountText").textContent =
      tracks.length > 0 ? `曲目 (${tracks.length})` : "曲目列表";

    const curMode = appState.playMode || "order";

    let html = `
      <div class="queue-header">
        <div style="min-width: 0; flex: 1;">
          <div class="queue-header-title">${escapeHtml(appState.albumTitle || "Discflow 曲目队列")}</div>
          <div class="queue-header-meta">DiscID: ${escapeHtml((appState.discId || "--").slice(0, 10))} · 共 ${tracks.length} 首</div>
        </div>
        <div class="play-mode-group" role="radiogroup" aria-label="播放模式">
          <button class="mode-pill-btn ${curMode === 'order' ? 'active' : ''}" data-mode="order" onclick="setPlayMode('order')" title="顺序播放 (列表循环)">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 2l4 4-4 4"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><path d="M7 22l-4-4 4-4"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>
            <span>顺序</span>
          </button>
          <button class="mode-pill-btn ${curMode === 'shuffle' ? 'active' : ''}" data-mode="shuffle" onclick="setPlayMode('shuffle')" title="随机播放">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 3h5v5"/><path d="M4 20l6.2-6.2M21 3l-7.8 7.8"/><path d="M21 21h-5v-5"/><path d="M15.2 15.2l5.8 5.8M4 4l9.6 9.6"/></svg>
            <span>随机</span>
          </button>
          <button class="mode-pill-btn ${curMode === 'one' ? 'active' : ''}" data-mode="one" onclick="setPlayMode('one')" title="单曲循环">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 2l4 4-4 4"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><path d="M7 22l-4-4 4-4"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/><path d="M11.2 10.5l1.6-1.1v5.2m-1.2 0h2.4" stroke-width="1.8"/></svg>
            <span>单曲</span>
          </button>
        </div>
        <button class="mode-pill-btn match-meta-btn" onclick="openMatchMetadataModal()" title="手动匹配专辑信息（封面 / 曲名 / 歌词）">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/></svg>
          <span>匹配</span>
        </button>
      </div>
    `;

    tracks.forEach((t, idx) => {
      const isAct = idx === appState.currentTrackIndex;
      const numCol = isAct
        ? `<div class="eq-bars"><span class="eq-bar"></span><span class="eq-bar"></span><span class="eq-bar"></span></div>`
        : String(t.track_number).padStart(2, "0");

      html += `
        <div class="track-row ${isAct ? "active" : ""} ${t.lyricsLocked ? "is-lyric-locked" : ""} ${t.lyricsMissed ? "needs-lyric" : ""}" onclick="selectTrack(${idx})">
          <div class="track-num-col">${numCol}</div>
          <div class="track-row-info">
            <div class="track-row-name">${escapeHtml(t.title)}${t.lyricsMissed ? '<span class="lyric-miss-badge">歌词待匹配</span>' : ""}</div>
            <div class="track-row-artist">${escapeHtml(t.artist)}</div>
          </div>
          <div class="track-row-dur">${formatTime(t.duration_sec)}</div>
          <button class="track-lyric-btn" onclick="openLyricMatchModal(event, ${idx})" title="匹配这首歌的歌词">词</button>
        </div>
      `;
    });

    qEl.innerHTML = html;
  }

  // ================= 资料库与抓轨服务 =================
  let ripPollTimer = null;

  async function startRippingCurrentCd() {
    if (!appState.discId || !appState.tracks || appState.tracks.length === 0) {
      alertToast("请先放入实体音频 CD 识别完成后再抓轨");
      return;
    }

    document.getElementById("ripAlbumTitle").textContent = appState.albumTitle || "音频 CD";
    document.getElementById("ripArtist").textContent = appState.artist || "未知歌手";
    document.getElementById("ripCoverImg").src = appState.coverDataUrl || "";
    document.getElementById("ripOverallBar").style.width = "0%";
    document.getElementById("ripOverallPctText").textContent = "0%";
    document.getElementById("ripTrackBar").style.width = "0%";
    document.getElementById("ripTrackPctText").textContent = "0%";
    document.getElementById("ripStatusText").textContent = "正在启动抓轨引擎...";
    document.getElementById("ripTrackTitle").textContent = "准备读取第一首音轨...";
    document.getElementById("ripDoneBtn").style.display = "none";
    document.getElementById("ripCancelBtn").style.display = "inline-flex";

    openModal("ripModal");

    if (window.pywebview && window.pywebview.api) {
      const res = await window.pywebview.api.start_ripping();
      if (!res || !res.success) {
        document.getElementById("ripStatusText").textContent = res ? res.message : "抓轨启动失败";
        return;
      }
    }

    if (ripPollTimer) clearInterval(ripPollTimer);
    ripPollTimer = setInterval(pollRippingProgress, 280);
  }

  async function pollRippingProgress() {
    if (!window.pywebview || !window.pywebview.api) return;
    try {
      const p = await window.pywebview.api.get_ripping_progress();
      if (!p) return;

      document.getElementById("ripOverallBar").style.width = `${p.overall_progress_pct || 0}%`;
      document.getElementById("ripOverallPctText").textContent = `${p.overall_progress_pct || 0}%`;
      document.getElementById("ripTrackBar").style.width = `${p.track_progress_pct || 0}%`;
      document.getElementById("ripTrackPctText").textContent = `${p.track_progress_pct || 0}%`;
      document.getElementById("ripStatusText").textContent = p.status_text || "";
      document.getElementById("ripTrackTitle").textContent = p.track_title ? `正在转录: ${p.track_title}` : "";

      if (p.is_completed) {
        clearInterval(ripPollTimer);
        ripPollTimer = null;
        document.getElementById("ripDoneBtn").style.display = "inline-flex";
        document.getElementById("ripCancelBtn").style.display = "none";
      } else if (!p.is_ripping && p.error_message) {
        clearInterval(ripPollTimer);
        ripPollTimer = null;
        document.getElementById("ripStatusText").textContent = `错误: ${p.error_message}`;
      }
    } catch (e) {
      console.warn("pollRippingProgress error:", e);
    }
  }

  function cancelRippingAndClose() {
    if (ripPollTimer) {
      clearInterval(ripPollTimer);
      ripPollTimer = null;
    }
    if (window.pywebview && window.pywebview.api) {
      window.pywebview.api.cancel_ripping();
    }
    closeModal("ripModal");
  }

  function finishRippingAndShowLibrary() {
    closeModal("ripModal");
    switchMainView("library");
    renderLibraryCards();
  }

  // ================= 谜底黑胶 (MD Vinyl) 资料库交互 =================
  let cachedLocalAlbums = [];

  function openSearchModal() {
    closeModal("settingsModal");
    closeModal("customSettingsModal");
    closeModal("aboutModal");
    openModal("searchModal");
    const input = document.getElementById("modalSearchInput");
    if (input) {
      input.value = "";
      setTimeout(() => input.focus(), 120);
    }
    const clearBtn = document.getElementById("modalSearchClearBtn");
    if (clearBtn) clearBtn.style.display = "none";
    const resEl = document.getElementById("modalSearchResults");
    if (resEl) {
      resEl.innerHTML = `
        <div style="text-align: center; color: var(--text-tertiary); padding: 36px 12px; font-size: 13px;">
          输入专辑、歌名或歌手关键词进行搜索
        </div>
      `;
    }
    const countEl = document.getElementById("modalSearchCountText");
    if (countEl) countEl.textContent = "准备就绪";

    if (window.pywebview && window.pywebview.api && window.pywebview.api.list_local_albums) {
      window.pywebview.api.list_local_albums().then(albs => {
        cachedLocalAlbums = albs || [];
      }).catch(() => {});
    }
  }

  function onModalSearchInput(val) {
    const clearBtn = document.getElementById("modalSearchClearBtn");
    if (clearBtn) {
      clearBtn.style.display = val ? "inline-block" : "none";
    }
    executeModalSearch();
  }

  function clearModalSearch() {
    const input = document.getElementById("modalSearchInput");
    if (input) {
      input.value = "";
      input.focus();
    }
    const clearBtn = document.getElementById("modalSearchClearBtn");
    if (clearBtn) clearBtn.style.display = "none";
    executeModalSearch();
  }

  async function executeModalSearch() {
    const input = document.getElementById("modalSearchInput");
    const term = (input ? input.value : "").trim().toLowerCase();
    const resEl = document.getElementById("modalSearchResults");
    const countEl = document.getElementById("modalSearchCountText");
    if (!resEl) return;

    if (!term) {
      resEl.innerHTML = `
        <div style="text-align: center; color: var(--text-tertiary); padding: 36px 12px; font-size: 13px;">
          输入专辑、歌名或歌手关键词进行搜索
        </div>
      `;
      if (countEl) countEl.textContent = "准备就绪";
      return;
    }

    if (!cachedLocalAlbums || cachedLocalAlbums.length === 0) {
      if (window.pywebview && window.pywebview.api && window.pywebview.api.list_local_albums) {
        try {
          cachedLocalAlbums = (await window.pywebview.api.list_local_albums()) || [];
        } catch(e) {}
      }
    }

    const matched = [];
    (cachedLocalAlbums || []).forEach(alb => {
      const matchAlbum = (alb.title || "").toLowerCase().includes(term) || (alb.artist || "").toLowerCase().includes(term);
      const matchedTracks = (alb.tracks || []).filter(t => (t.title || "").toLowerCase().includes(term) || (t.artist || "").toLowerCase().includes(term));
      if (matchAlbum || matchedTracks.length > 0) {
        matched.push({
          album: alb,
          matchedTracks: matchedTracks,
          matchedAlbum: matchAlbum
        });
      }
    });

    if (countEl) {
      countEl.textContent = `找到 ${matched.length} 张匹配唱片`;
    }

    if (matched.length === 0) {
      resEl.innerHTML = `
        <div style="text-align: center; color: var(--text-tertiary); padding: 36px 12px; font-size: 13px;">
          未找到与 “<span style="color:var(--text-primary); font-weight:600;">${escapeHtml(term)}</span>” 相关的专辑或歌曲
        </div>
      `;
      return;
    }

    let html = "";
    matched.forEach(item => {
      const alb = item.album;
      const coverSrc = alb.cover_data_url || "";
      const discIdSafe = alb.disc_id.replace(/'/g, "\'");
      const trackHighlight = item.matchedTracks.length > 0
        ? `· 命中歌曲: ${item.matchedTracks.map(t => escapeHtml(t.title)).join(", ")}`
        : "";

      html += `
        <div style="display: flex; align-items: center; justify-content: space-between; padding: 8px 12px; border-radius: 10px; background: rgba(255, 255, 255, 0.04); border: 1px solid var(--border-hairline);">
          <div style="display: flex; align-items: center; gap: 12px; min-width: 0; flex: 1;">
            ${coverSrc
              ? `<img src="${coverSrc}" style="width: 44px; height: 44px; border-radius: 6px; object-fit: cover; flex-shrink: 0;" />`
              : `<div style="width: 44px; height: 44px; border-radius: 6px; background: #222; display: flex; align-items: center; justify-content: center; flex-shrink: 0;">💿</div>`
            }
            <div style="min-width: 0; flex: 1;">
              <div style="font-weight: 600; font-size: 13.5px; color: var(--text-primary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                ${escapeHtml(alb.title)}
              </div>
              <div style="font-size: 12px; color: var(--text-secondary); margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                ${escapeHtml(alb.artist)} <span style="opacity: 0.75; font-size: 11px;">${trackHighlight}</span>
              </div>
            </div>
          </div>
          <div style="display: flex; gap: 6px; margin-left: 10px; flex-shrink: 0;">
            <button class="pill-btn active" onclick="playLocalAlbumFromSearch('${discIdSafe}')" style="padding: 3px 12px; font-size: 12px;">▶ 播放</button>
          </div>
        </div>
      `;
    });
    resEl.innerHTML = html;
  }

  async function playLocalAlbumFromSearch(discId) {
    closeModal("searchModal");
    await playLocalAlbum(discId);
  }

  function jumpToLibraryFromSearch() {
    const input = document.getElementById("modalSearchInput");
    const query = input ? input.value : "";
    closeModal("searchModal");
    switchMainView("library");
    const libInput = document.getElementById("librarySearchInput");
    if (libInput) {
      libInput.value = query;
      filterLibraryCards(query);
      libInput.focus();
    }
  }

  let isLibrarySearchOpen = false;

  function submitLibrarySearch() {
    const input = document.getElementById("librarySearchInput");
    if (input) {
      filterLibraryCards(input.value);
      input.focus();
    }
  }

  function clearLibrarySearch() {
    const input = document.getElementById("librarySearchInput");
    if (input) {
      input.value = "";
      input.focus();
      filterLibraryCards("");
    }
  }

  function filterLibraryCards(keyword) {
    const term = (keyword || "").trim().toLowerCase();
    const cards = document.querySelectorAll("#libraryGrid .vinyl-card");
    let visibleCount = 0;
    cards.forEach(card => {
      const title = card.querySelector(".vinyl-info-title")?.textContent?.toLowerCase() || "";
      const artist = card.querySelector(".vinyl-info-artist")?.textContent?.toLowerCase() || "";
      const tracks = (card.getAttribute("data-tracks") || "").toLowerCase();
      // 支持按专辑名、歌手名、歌曲名（歌名）全维度搜索
      if (!term || title.includes(term) || artist.includes(term) || tracks.includes(term)) {
        card.style.display = "";
        visibleCount++;
      } else {
        card.style.display = "none";
      }
    });

    let emptyPrompt = document.getElementById("librarySearchEmptyPrompt");
    if (cards.length > 0 && visibleCount === 0 && term) {
      if (!emptyPrompt) {
        emptyPrompt = document.createElement("div");
        emptyPrompt.id = "librarySearchEmptyPrompt";
        emptyPrompt.style.cssText = "grid-column: 1/-1; text-align: center; color: var(--text-tertiary); padding: 50px 20px; font-size: 14px;";
        const grid = document.getElementById("libraryGrid");
        if (grid) grid.appendChild(emptyPrompt);
      }
      emptyPrompt.innerHTML = `未找到与 “<span style="color:var(--text-primary); font-weight:600;">${escapeHtml(term)}</span>” 相关的唱片或歌曲<br><button class="pill-btn" style="margin-top:14px;" onclick="clearLibrarySearch()">清空搜索</button>`;
      emptyPrompt.style.display = "block";
    } else if (emptyPrompt) {
      emptyPrompt.style.display = "none";
    }
  }

  async function renderLibraryCards() {
    const grid = document.getElementById("libraryGrid");
    grid.innerHTML = `<div style="grid-column: 1/-1; text-align: center; color: var(--text-tertiary); padding: 40px;">正在加载黑胶唱片...</div>`;

    if (!window.pywebview || !window.pywebview.api) return;
    try {
      const albums = await window.pywebview.api.list_local_albums();
      window._libraryAlbumsCache = albums || [];
      const countEl = document.getElementById("libraryAlbumCount");
      if (countEl) countEl.textContent = albums && albums.length ? `${albums.length} 张专辑` : "";
      if (!albums || albums.length === 0) {
        grid.innerHTML = `
          <div class="library-empty">
            <div style="width: 72px; height: 72px; opacity: 0.35; margin-bottom: 8px;">
              ${createDiscflowIconMarkup(72)}
            </div>
            <div class="library-empty-title">资源库为空</div>
            <div class="library-empty-sub">
              放入音频 CD 后，点击顶部菜单中的「💿 抓轨当前 CD 到本地资料库」，
              即可将整张唱片逐扇区无损转录归档至此处，以后无需插入光盘即可即点即播。
            </div>
          </div>
        `;
        return;
      }

      let html = "";
      albums.forEach(alb => {
        const coverSrc = alb.cover_data_url || "";
        const durFormatted = formatTime(alb.total_duration_sec);
        const trackCount = alb.tracks ? alb.tracks.length : 0;
        const discIdSafe = alb.disc_id.replace(/'/g, "\'");
        const trackNamesSafe = (alb.tracks || []).map(t => (t.title || '').toLowerCase()).join(' ');

        html += `
          <div class="vinyl-card" data-tracks="${escapeHtml(trackNamesSafe)}" onclick="playLocalAlbumWithAnimation(this, '${discIdSafe}')" title="点击免光驱无损播放《${escapeHtml(alb.title)}》">
            <div class="vinyl-stage">
              <!-- 纸质唱片封套 -->
              <div class="vinyl-sleeve">
                ${coverSrc ? `<img src="${coverSrc}" alt="Cover" />` : createDiscflowIconMarkup(140)}
              </div>
              <!-- 抽出的实体黑胶唱片 (带沟槽纹理与中心圆标) -->
              <div class="vinyl-disc">
                <div class="vinyl-label">
                  ${coverSrc ? `<img src="${coverSrc}" alt="Label" />` : ""}
                </div>
              </div>
            </div>
            <div class="vinyl-info-title">${escapeHtml(alb.title)}</div>
            <div class="vinyl-info-artist">${escapeHtml(alb.artist)}</div>
            <div class="vinyl-info-footer">
              <span>${trackCount} 首 · ${durFormatted}</span>
              <span class="vinyl-card-ops">
                <button class="vinyl-delete-btn" onclick="openEditLocalAlbumModal(event, '${discIdSafe}')" title="编辑专辑与曲目名">编辑</button>
                <button class="vinyl-delete-btn" onclick="pickAlbumCover(event, '${discIdSafe}')" title="更换封面">封面</button>
                <button class="vinyl-delete-btn" onclick="deleteLocalAlbum(event, '${discIdSafe}')" title="移除唱片">删除</button>
              </span>
            </div>
          </div>
        `;
      });
      grid.innerHTML = html;
    } catch (e) {
      console.error("renderLibraryCards failed:", e);
      grid.innerHTML = `<div style="grid-column: 1/-1; text-align: center; color: #FF453A; padding: 40px;">加载资料库失败: ${e}</div>`;
    }
  }

  async function playLocalAlbumWithAnimation(cardEl, discId) {
    if (cardEl) {
      cardEl.classList.add("is-pulling-out");
    }
    let playPromise = null;
    if (window.pywebview && window.pywebview.api) {
      playPromise = window.pywebview.api.play_local_album(discId);
    }

    // 等待 460ms 享受拟物抽盘微交互
    await new Promise(r => setTimeout(r, 460));

    if (playPromise) {
      try {
        await playPromise;
      } catch (e) {
        console.warn("play_local_album error:", e);
      }
    }

    setTimeout(() => {
      if (cardEl) cardEl.classList.remove("is-pulling-out");
      triggerImmediatePoll();
      // 路由修复：默认平滑导航至常规双栏播放界面，并确保关闭沉浸全屏
      if (typeof isImmersiveMode !== "undefined" && isImmersiveMode) {
        toggleImmersiveVinylMode(false);
      }
      switchMainView("cd");
      // 从资料库进入时默认展开曲目栏，方便直接查看曲目并手动匹配专辑信息
      applyRightPanelMode("queue");
      renderQueueList();
    }, 280);
  }

  async function playLocalAlbum(discId) {
    if (!window.pywebview || !window.pywebview.api) return;
    try {
      const ok = await window.pywebview.api.play_local_album(discId);
      if (ok) {
        triggerImmediatePoll();
        if (typeof isImmersiveMode !== "undefined" && isImmersiveMode) {
          toggleImmersiveVinylMode(false);
        }
        switchMainView("cd");
      }
    } catch (e) {
      console.warn("playLocalAlbum error:", e);
    }
  }

  async function deleteLocalAlbum(e, discId) {
    e.stopPropagation();
    if (!confirm("确定要从资源库删除这张唱片吗？\n\n专辑音频文件（WAV）、封面与元数据将被一并物理删除，且不可恢复。")) {
      return;
    }
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.delete_local_album) return;
    try {
      const res = await window.pywebview.api.delete_local_album(discId);
      if (res && res.ok) {
        appState.currentTrackIndex = 0;
        await renderLibraryCards();
        return;
      }
      const failedList = (res && res.failed) ? res.failed : [];
      let detail = "";
      if (failedList.length) {
        detail = "\n\n以下文件仍被占用，未能删除：\n" + failedList.slice(0, 5).join("\n");
      } else if (res && res.reason) {
        detail = "\n\n" + res.reason;
      }
      alert("删除失败：唱片文件未能完全移除。" + detail + "\n\n请先停止播放后重试。");
      await renderLibraryCards();
    } catch (err) {
      console.error("delete_local_album error:", err);
      alert("删除失败：" + err);
    }
  }

  // --- 手动更换专辑封面（自动匹配失败时的兜底） ---
  let pendingCoverDiscId = null;

  function pickAlbumCover(e, discId) {
    if (e) e.stopPropagation();
    pendingCoverDiscId = discId;
    const input = document.getElementById("coverFileInput");
    if (!input) return;
    input.value = "";
    input.click();
  }

  function handleCoverFileSelected(ev) {
    const file = ev.target.files && ev.target.files[0];
    if (!file || !pendingCoverDiscId) return;
    const discId = pendingCoverDiscId;
    pendingCoverDiscId = null;

    const reader = new FileReader();
    reader.onload = async () => {
      try {
        if (!window.pywebview || !window.pywebview.api) return;
        const ok = await window.pywebview.api.update_album_cover(discId, reader.result);
        if (ok) {
          await renderLibraryCards();
        } else {
          alert("封面更新失败：唱片元数据缺失或图片不可写。");
        }
      } catch (err) {
        console.error("update_album_cover error:", err);
        alert("封面更新失败：" + err);
      }
    };
    reader.readAsDataURL(file);
  }

  function openEditLocalAlbumModal(e, discId) {
    if (e) e.stopPropagation();
    const list = window._libraryAlbumsCache || [];
    const alb = list.find(a => a.disc_id === discId);
    if (!alb) return;
    const idEl = document.getElementById("editLocalDiscId");
    const tEl = document.getElementById("editLocalTitle");
    const aEl = document.getElementById("editLocalArtist");
    const yEl = document.getElementById("editLocalYear");
    const trkBox = document.getElementById("editLocalTracksList");
    if (idEl) idEl.value = discId;
    if (tEl) tEl.value = alb.title || "";
    if (aEl) aEl.value = alb.artist || "";
    if (yEl) yEl.value = alb.year || "";
    if (trkBox) {
      trkBox.innerHTML = (alb.tracks || []).map(t => `
        <div style="display: flex; align-items: center; gap: 8px;">
          <span style="width: 24px; font-size: 12px; color: var(--text-tertiary); text-align: right; flex-shrink: 0;">${t.track_number}</span>
          <input class="match-meta-input edit-local-track-input" data-track-num="${t.track_number}" value="${escapeHtml(t.title || "")}" style="flex: 1;" />
        </div>
      `).join("");
    }
    openModal("editLocalAlbumModal");
  }

  async function saveLocalAlbumEdit() {
    const discId = (document.getElementById("editLocalDiscId") || {}).value || "";
    const title = ((document.getElementById("editLocalTitle") || {}).value || "").trim();
    const artist = ((document.getElementById("editLocalArtist") || {}).value || "").trim();
    const year = ((document.getElementById("editLocalYear") || {}).value || "").trim();
    if (!discId || !title) {
      showToast("专辑名称不能为空");
      return;
    }
    const trackTitles = {};
    document.querySelectorAll(".edit-local-track-input").forEach(inp => {
      const num = parseInt(inp.dataset.trackNum || "0", 10);
      const val = (inp.value || "").trim();
      if (num > 0 && val) trackTitles[num] = val;
    });
    try {
      if (window.pywebview && window.pywebview.api && window.pywebview.api.update_album_metadata) {
        const ok = await window.pywebview.api.update_album_metadata(discId, title, artist, year, trackTitles);
        if (ok) {
          closeModal("editLocalAlbumModal");
          showToast("专辑与曲目信息已更新");
          renderLibraryCards();
          triggerImmediatePoll();
        } else {
          showToast("保存失败");
        }
      }
    } catch (err) {
      showToast("保存异常: " + err);
    }
  }

  // --- 液态玻璃 (Fluid Morphing) 播放列表与控制面板状态管理 ---
  let isFluidPlaylistOpen = false;

  function toggleFluidPlaylistMorph(event) {
    if (event) event.stopPropagation();
    isFluidPlaylistOpen = !isFluidPlaylistOpen;
    const overlay = document.getElementById("mdFluidMorphOverlay");
    if (!overlay) return;

    if (isFluidPlaylistOpen) {
      overlay.classList.add("is-open");
      renderFluidTrackList();
      syncFluidControls();
    } else {
      overlay.classList.remove("is-open");
    }
  }

  function closeFluidPlaylistMorph(event) {
    if (event) event.stopPropagation();
    isFluidPlaylistOpen = false;
    const overlay = document.getElementById("mdFluidMorphOverlay");
    if (overlay) overlay.classList.remove("is-open");
  }

  function renderFluidTrackList() {
    const container = document.getElementById("fluidTrackList");
    if (!container) return;

    if (!appState.tracks || appState.tracks.length === 0) {
      container.innerHTML = `<div style="text-align: center; color: var(--text-tertiary); padding: 32px 0; font-size: 13px;">暂无当前专辑曲目</div>`;
      return;
    }

    let html = "";
    appState.tracks.forEach((tr, idx) => {
      const isCurrent = idx === appState.currentTrackIndex;
      const title = tr.title || `音轨 ${tr.track_number}`;
      const dur = formatTime(tr.duration_sec);
      html += `
        <div class="fluid-track-row ${isCurrent ? 'is-current' : ''}" onclick="playTrackIndex(${idx})" title="播放 ${escapeHtml(title)}">
          <div class="fluid-track-left">
            <span class="fluid-track-num">${isCurrent ? '▶' : (idx + 1)}</span>
            <span class="fluid-track-title">${escapeHtml(title)}</span>
          </div>
          <span class="fluid-track-dur">${dur}</span>
        </div>
      `;
    });
    container.innerHTML = html;
  }

  function syncFluidControls() {
    // 1. 同步音量滑块与指示条
    const fill = document.getElementById("fluidVolFill");
    if (fill) fill.style.width = `${currentVolumePercent}%`;

    // 2. 同步三态播放模式高亮
    const btnAll = document.getElementById("fluidModeRepeatAll");
    const btnShuff = document.getElementById("fluidModeShuffle");
    const btnOne = document.getElementById("fluidModeRepeatOne");
    if (btnAll && btnShuff && btnOne) {
      btnAll.classList.toggle("active", currentPlayMode === "repeat_all" || currentPlayMode === "order");
      btnShuff.classList.toggle("active", currentPlayMode === "shuffle");
      btnOne.classList.toggle("active", currentPlayMode === "repeat_one" || currentPlayMode === "one");
    }
  }

  function setFluidPlayMode(mode) {
    currentPlayMode = mode;
    if (window.pywebview && window.pywebview.api && window.pywebview.api.set_play_mode) {
      window.pywebview.api.set_play_mode(mode).then(() => triggerImmediatePoll()).catch(() => {});
    }
    syncFluidControls();
    updateModeIconUi();
  }

  // 绑定液态面板内的音量调节
  const fluidVolHit = document.getElementById("fluidVolHit");
  if (fluidVolHit) {
    let isDraggingFluidVol = false;
    function handleFluidVolPointer(e) {
      const rect = fluidVolHit.getBoundingClientRect();
      const x = Math.max(0, Math.min(e.clientX - rect.left, rect.width));
      const pct = Math.round((x / rect.width) * 100);
      setVolumePercent(pct);
      syncFluidControls();
    }
    fluidVolHit.addEventListener("pointerdown", (e) => {
      isDraggingFluidVol = true;
      fluidVolHit.setPointerCapture(e.pointerId);
      handleFluidVolPointer(e);
    });
    fluidVolHit.addEventListener("pointermove", (e) => {
      if (isDraggingFluidVol) handleFluidVolPointer(e);
    });
    fluidVolHit.addEventListener("pointerup", () => {
      isDraggingFluidVol = false;
    });
  }

  // ================= 谜底黑胶 (MD Vinyl) 沉浸播放模式交互 =================
  let isImmersiveMode = false;
  let isMdLyricsVisible = false;

  function toggleImmersiveVinylMode(forceOpen = null) {
    const stage = document.getElementById("immersiveVinylStage");
    if (forceOpen === true) isImmersiveMode = true;
    else if (forceOpen === false) isImmersiveMode = false;
    else isImmersiveMode = !isImmersiveMode;

    stage.classList.toggle("open", isImmersiveMode);
    if (isImmersiveMode) {
      syncImmersiveState();
    }
  }

  function toggleMdLyricsSheet(forceState = null) {
    const sheet = document.getElementById("mdLyricsSheet");
    if (!sheet) return;
    if (forceState === true) isMdLyricsVisible = true;
    else if (forceState === false) isMdLyricsVisible = false;
    else isMdLyricsVisible = !isMdLyricsVisible;
    sheet.classList.toggle("visible", isMdLyricsVisible);
  }

  function cyclePlayMode() {
    let nextMode = "order";
    if (appState.playMode === "order") nextMode = "shuffle";
    else if (appState.playMode === "shuffle") nextMode = "one";
    else nextMode = "order";
    setPlayMode(nextMode);
  }

    // 全局键盘快捷键与多媒体键全面支持 (Stage 4)
  window.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && (e.key === "f" || e.key === "F")) {
      e.preventDefault();
      openSearchModal();
      return;
    }
    // 忽略在输入框、下拉框或可编辑区域内的按键
    if (e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT" || e.target.isContentEditable)) {
      return;
    }

    const openModals = document.querySelectorAll(".popup-modal-overlay.open");
    if (e.key === "Escape" && openModals.length > 0) {
      openModals[openModals.length - 1].classList.remove("open");
      return;
    }
    if (openModals.length > 0) {
      return;
    }

    if (e.key === "Escape") {
      if (isImmersiveMode) {
        toggleImmersiveVinylMode(false);
      } else if (currentMainView === "library") {
        // 资源库页按 Esc 直接回到播放页
        switchMainView("cd");
      }
    } else if (e.key === " " || e.code === "Space") {
      e.preventDefault();
      togglePlayPause();
    } else if (e.key === "ArrowRight" || e.code === "ArrowRight") {
      // 快进 5 秒
      e.preventDefault();
      const targetSec = Math.min((appState.durationSec || 0), (appState.elapsedSec || 0) + 5);
      if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.seek_to(targetSec).then(() => triggerImmediatePoll()).catch(() => {});
      }
    } else if (e.key === "ArrowLeft" || e.code === "ArrowLeft") {
      // 快退 5 秒
      e.preventDefault();
      const targetSec = Math.max(0, (appState.elapsedSec || 0) - 5);
      if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.seek_to(targetSec).then(() => triggerImmediatePoll()).catch(() => {});
      }
    } else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
      e.preventDefault();
      const cur = typeof appState.volume === "number" ? appState.volume : 80;
      setVolumePercent(cur + (e.key === "ArrowUp" ? 5 : -5), true);
    } else if (e.key === "MediaTrackNext") {
      nextTrack();
    } else if (e.key === "MediaTrackPrevious") {
      prevTrack();
    } else if (e.key === "MediaPlayPause") {
      togglePlayPause();
    } else if (e.key === "[" || e.code === "BracketLeft") {
      e.preventDefault();
      adjustLyricOffset(0.5);
    } else if (e.key === "]" || e.code === "BracketRight") {
      e.preventDefault();
      adjustLyricOffset(-0.5);
    } else if (e.key === "\\" || e.code === "Backslash") {
      e.preventDefault();
      resetLyricOffset();
    }
  });

  function syncImmersiveState() {
    if (!isImmersiveMode) return;
    const curTrack = (appState.tracks || [])[appState.currentTrackIndex];
    const trackTitle = curTrack ? curTrack.title : (appState.albumTitle || "等待放入音频 CD");
    const artistName = (curTrack && curTrack.artist) || appState.artist || "--";
    const albumName = appState.albumTitle ? (appState.year ? `${appState.albumTitle} (${appState.year})` : appState.albumTitle) : "--";

    // 封套大图
    const covImg = document.getElementById("immersiveCoverImg");
    if (covImg) {
      if (appState.coverDataUrl) {
        covImg.src = appState.coverDataUrl;
        covImg.style.display = "block";
      } else {
        covImg.style.display = "none";
      }
    }

    // 黑胶中心圆标
    const labelImg = document.getElementById("immersiveLabelImg");
    if (labelImg) {
      if (appState.coverDataUrl) {
        labelImg.src = appState.coverDataUrl;
        labelImg.style.display = "block";
      } else {
        labelImg.style.display = "none";
      }
    }

    // 贴纸文字
    const lblTitle = document.getElementById("mdLabelTrackTitle");
    if (lblTitle) lblTitle.textContent = trackTitle;
    const lblArtist = document.getElementById("mdLabelArtist");
    if (lblArtist) lblArtist.textContent = artistName;

    // 底部胶囊微缩图
    const miniLbl = document.getElementById("mdDockMiniLabel");
    if (miniLbl) {
      if (appState.coverDataUrl) {
        miniLbl.src = appState.coverDataUrl;
        miniLbl.style.display = "block";
      } else {
        miniLbl.style.display = "none";
      }
    }

    // 底部左角资料库缩略图
    const thumbImg = document.getElementById("mdDockThumbImg");
    if (thumbImg) {
      if (appState.coverDataUrl) {
        thumbImg.src = appState.coverDataUrl;
        thumbImg.style.display = "block";
      } else {
        thumbImg.style.display = "none";
      }
    }

    // 底部胶囊曲目信息
    const titleEl = document.getElementById("immersiveTrackTitle");
    if (titleEl) titleEl.textContent = trackTitle;
    const artistEl = document.getElementById("immersiveArtistName");
    if (artistEl) artistEl.textContent = artistName;

    // 底部胶囊播放/暂停图标
    const playIcon = document.getElementById("mdDockPlayIcon");
    const pauseIcon = document.getElementById("mdDockPauseIcon");
    if (playIcon && pauseIcon) {
      playIcon.style.display = appState.isPlaying ? "none" : "block";
      pauseIcon.style.display = appState.isPlaying ? "block" : "none";
    }

    // 底部胶囊播放模式图标
    const modeBtn = document.getElementById("mdDockModeBtn");
    if (modeBtn) {
      let iconSvg = '<path d="M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4z"/>';
      if (appState.playMode === "shuffle") {
        iconSvg = '<path d="M10.59 9.17L5.41 4 4 5.41l5.17 5.17 1.42-1.41zM14.5 4l2.04 2.04L4 18.59 5.41 20 17.96 7.46 20 9.5V4h-5.5zm.33 9.41l-1.41 1.41 3.13 3.13L14.5 20H20v-5.5l-2.04 2.04-3.13-3.13z"/>';
      } else if (appState.playMode === "one") {
        iconSvg = '<path d="M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4zm-4-2V9h-1l-2 1v1h1.5v4H13z"/>';
      }
      modeBtn.innerHTML = `<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">${iconSvg}</svg>`;
    }

    // 同步当前单句高光歌词
    const lyrics = appState.lyrics || [];
    const actLine = document.getElementById("mdLyricsActiveLine");
    const actSub = document.getElementById("mdLyricsActiveSub");
    if (actLine && actSub) {
      if (lyrics.length > 0 && appState.activeLyricIndex >= 0 && appState.activeLyricIndex < lyrics.length) {
        const curL = lyrics[appState.activeLyricIndex];
        actLine.textContent = curL.text || "";
        actSub.textContent = (appState.showTranslation && curL.translation) ? curL.translation : "";
      } else {
        actLine.textContent = "纯享 CD 无损音轨";
        actSub.textContent = "";
      }
    }
  }

  function alertToast(msg) {
    const dot = document.getElementById("statusText");
    if (dot) {
      const old = dot.textContent;
      dot.textContent = msg;
      setTimeout(() => { dot.textContent = old; }, 3200);
    }
  }

  function escapeHtml(str) {
    return String(str || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  let localMetaVer = -1;
  let localLyricsVer = -1;
  let pollInFlight = false;

  window.applyBackendState = function(payload) {
    if (!payload) return;
    const prevTrackIdx = appState.currentTrackIndex;
    const prevDiscId = appState.discId;
    const hasMetaUpdate = ("tracks" in payload) || ("albumTitle" in payload);
    const hasLyricsUpdate = ("lyrics" in payload);

    Object.assign(appState, payload);
    if (typeof payload.lyricOffset === "number") {
      currentTrackLyricOffset = payload.lyricOffset;
    }
    if ("updateAvailable" in payload) {
      renderUpdateNotice(payload.updateAvailable);
    }

    document.body.classList.toggle("is-playing", !!appState.isPlaying);
    document.getElementById("playIcon").style.display = appState.isPlaying ? "none" : "block";
    document.getElementById("pauseIcon").style.display = appState.isPlaying ? "block" : "none";

    const dot = document.getElementById("statusDot");
    if (dot) {
      dot.classList.toggle("loading", !!appState.isLoading);
      dot.classList.toggle("idle", !appState.isLoading && !(appState.discId && (appState.tracks || []).length));
      dot.title = appState.statusText || "光驱状态指示灯";
    }
    const statusTextEl = document.getElementById("statusText");
    if (statusTextEl) statusTextEl.textContent = appState.statusText || "";

    if ("drives" in payload) {
      const sel = document.getElementById("driveSelect");
      const curOptions = Array.from(sel.options).map(o => o.value);
      if (JSON.stringify(curOptions) !== JSON.stringify(appState.drives || [])) {
        sel.innerHTML = "";
        (appState.drives || []).forEach(d => {
          const opt = document.createElement("option");
          opt.value = d;
          opt.textContent = d;
          sel.appendChild(opt);
        });
      }
      if (appState.drive && sel.value !== appState.drive) {
        sel.value = appState.drive;
      }
    }

    if (hasMetaUpdate || prevTrackIdx !== appState.currentTrackIndex) {
      const curTrack = (appState.tracks || [])[appState.currentTrackIndex];
      document.getElementById("trackTitle").textContent =
        curTrack ? curTrack.title : (appState.albumTitle || "等待放入音频 CD");

      const subParts = [];
      if (curTrack && curTrack.artist) subParts.push(curTrack.artist);
      else if (appState.artist) subParts.push(appState.artist);
      if (appState.albumTitle && appState.albumTitle !== "等待放入音频 CD") {
        subParts.push(appState.year ? `${appState.albumTitle} (${appState.year})` : appState.albumTitle);
      }
      document.getElementById("trackSubtitle").textContent =
        subParts.join(" — ") || "Discflow · 插入外置光驱并放入音频 CD 开始播放";
    }

    if ("coverDataUrl" in payload && appState.coverDataUrl !== appState.lastCoverSignature) {
      appState.lastCoverSignature = appState.coverDataUrl;
      extractPaletteFromDataUrl(appState.coverDataUrl);
    }

    if (!isDraggingScrubber) {
      const dur = Math.max(0.01, appState.durationSec || 0);
      const pct = appState.durationSec > 0 ? Math.min(100, Math.max(0, (appState.elapsedSec / dur) * 100)) : 0;
      document.getElementById("scrubberFill").style.width = `${pct}%`;
      document.getElementById("elapsedTime").textContent = formatTime(appState.elapsedSec);
      const rem = Math.max(0, (appState.durationSec || 0) - (appState.elapsedSec || 0));
      document.getElementById("remainingTime").textContent = `-${formatTime(rem)}`;
    }

    let lyricsRendered = false;
    if (hasLyricsUpdate) {
      // 判重签名只取稳定字段并带上曲目索引：
      // ensureWordTimings 会给行对象挂 words 字段，用 JSON.stringify 判重会因自身副作用而误判「未变化」，
      // 导致歌词已下发却不重绘（表现为首曲没歌词、切走再切回才出现）。
      // 另外必须带上 discId：换碟时歌词可能都是空的，只靠曲目索引无法触发重绘，
      // 空状态文案就会一直停留在「等待放入音频 CD」。
      const sig = `${appState.discId || ""}#${appState.currentTrackIndex || 0}#${(appState.tracks || []).length}#` +
        (appState.lyrics || [])
          .map(l => `${l.time}|${l.text}|${l.translation || ""}`)
          .join("\n");
      if (window._lastLyricsSig !== sig) {
        window._lastLyricsSig = sig;
        renderLyricsList(appState.lyrics || []);
        lyricsRendered = true;
      }
    }
    syncKaraokeAnchor();
    updateActiveLyric(appState.isPlaying ? karaokeClock : (appState.elapsedSec || 0), lyricsRendered);
    if (!appState.isPlaying) {
      renderKaraokeWords(
        appState.activeLyricIndex,
        (Number(appState.elapsedSec) || 0) - (Number(currentTrackLyricOffset) || 0.0)
      );
    }

    if (hasMetaUpdate || prevTrackIdx !== appState.currentTrackIndex || prevDiscId !== appState.discId) {
      renderQueueList();
    }

    // 同步底部常驻 Dock 栏状态
    const dockArt = document.getElementById("dockArtworkImg");
    if (dockArt) {
      const nextSrc = appState.coverDataUrl || "";
      if (dockArt.dataset.coverSrc !== nextSrc) {
        dockArt.dataset.coverSrc = nextSrc;
        dockArt.src = nextSrc || "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";
      }
      dockArt.classList.toggle("is-empty", !nextSrc);
      dockArt.style.display = "block";
    }
    const dockTitle = document.getElementById("dockTrackTitle");
    if (dockTitle) {
      const curTrk = (appState.tracks || [])[appState.currentTrackIndex];
      dockTitle.textContent = curTrk ? curTrk.title : (appState.albumTitle || "等待放入音频 CD");
    }
    const dockArtst = document.getElementById("dockArtist");
    if (dockArtst) {
      const curTrk = (appState.tracks || [])[appState.currentTrackIndex];
      dockArtst.textContent = (curTrk && curTrk.artist) || appState.artist || "--";
    }
    const dockPlay = document.getElementById("dockPlayIcon");
    const dockPause = document.getElementById("dockPauseIcon");
    if (dockPlay && dockPause) {
      dockPlay.style.display = appState.isPlaying ? "none" : "block";
      dockPause.style.display = appState.isPlaying ? "block" : "none";
    }
    if (!isDraggingScrubber) {
      const dur = Math.max(0.01, appState.durationSec || 0);
      const pct = appState.durationSec > 0 ? Math.min(100, Math.max(0, (appState.elapsedSec / dur) * 100)) : 0;
      const dFill = document.getElementById("dockScrubberFill");
      if (dFill) dFill.style.width = `${pct}%`;
      const dElap = document.getElementById("dockElapsedTime");
      if (dElap) dElap.textContent = formatTime(appState.elapsedSec);
      const dRem = document.getElementById("dockRemainingTime");
      if (dRem) dRem.textContent = `-${formatTime(Math.max(0, (appState.durationSec || 0) - (appState.elapsedSec || 0)))}`;
    }
    // 音量：仅在启动首次从后端同步（恢复上次音量），之后以用户操作为准；拖动中不覆盖
    if (!volumeSyncedFromBackend && typeof payload.volume === "number") {
      volumeSyncedFromBackend = true;
      appState.volume = payload.volume;
      if (payload.volume > 0) lastNonZeroVolume = payload.volume;
    }
    if (!isDraggingDockVol) renderVolumeUI(appState.volume);

    if ("playMode" in payload && payload.playMode !== appState.playMode) {
      appState.playMode = payload.playMode;
      applyPlayModeUI(appState.playMode);
    }

    syncImmersiveState();
  };

  window.__onBackendStatePush = function(res) {
    if (!res) return;
    if (typeof res.metaVersion === "number") localMetaVer = res.metaVersion;
    if (typeof res.lyricsVersion === "number") localLyricsVer = res.lyricsVersion;
    window.applyBackendState(res);
    syncKaraokeAnchor();
  };

  async function pollBackendState() {
    if (pollInFlight || document.hidden || !window.pywebview || !window.pywebview.api) return;
    pollInFlight = true;
    try {
      const res = await window.pywebview.api.poll_state(
        localMetaVer,
        localLyricsVer,
        (typeof appState.currentTrackIndex === "number" ? appState.currentTrackIndex : -1)
      );
      if (res) {
        window.__onBackendStatePush(res);
      }
    } catch (err) {
      // 忽略窗口关闭瞬间的桥接异常
    } finally {
      pollInFlight = false;
    }
  }

  function triggerImmediatePoll() {
    setTimeout(pollBackendState, 16);
  }

  const scrubberHit = document.getElementById("scrubberHit");
  function handleScrubberPointer(e, commit) {
    const rect = scrubberHit.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    const targetSec = ratio * (appState.durationSec || 0);
    document.getElementById("scrubberFill").style.width = `${ratio * 100}%`;
    document.getElementById("elapsedTime").textContent = formatTime(targetSec);
    document.getElementById("remainingTime").textContent = `-${formatTime((appState.durationSec || 0) - targetSec)}`;
    if (commit && window.pywebview && window.pywebview.api) {
      window.pywebview.api.seek_to(targetSec).then(() => triggerImmediatePoll()).catch(() => {});
    }
  }

  scrubberHit.addEventListener("pointerdown", (e) => {
    isDraggingScrubber = true;
    scrubberHit.classList.add("dragging");
    scrubberHit.setPointerCapture(e.pointerId);
    handleScrubberPointer(e, false);
  });

  scrubberHit.addEventListener("pointermove", (e) => {
    if (isDraggingScrubber) handleScrubberPointer(e, false);
  });

  scrubberHit.addEventListener("pointerup", (e) => {
    if (!isDraggingScrubber) return;
    isDraggingScrubber = false;
    scrubberHit.classList.remove("dragging");
    handleScrubberPointer(e, true);
  });

  const immScrubberHit = document.getElementById("immScrubberHit");
  if (immScrubberHit) {
    immScrubberHit.addEventListener("pointerdown", (e) => {
      isDraggingScrubber = true;
      immScrubberHit.setPointerCapture(e.pointerId);
      handleImmScrubberPointer(e, false);
    });
    immScrubberHit.addEventListener("pointermove", (e) => {
      if (isDraggingScrubber) handleImmScrubberPointer(e, false);
    });
    immScrubberHit.addEventListener("pointerup", (e) => {
      if (!isDraggingScrubber) return;
      isDraggingScrubber = false;
      handleImmScrubberPointer(e, true);
    });
  }

  function handleImmScrubberPointer(e, commit) {
    if (!immScrubberHit) return;
    const rect = immScrubberHit.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    const targetSec = ratio * (appState.durationSec || 0);
    const fillEl = document.getElementById("immScrubberFill");
    if (fillEl) fillEl.style.width = `${ratio * 100}%`;
    const elTime = document.getElementById("immElapsedTime");
    if (elTime) elTime.textContent = formatTime(targetSec);
    const remTime = document.getElementById("immRemainingTime");
    if (remTime) remTime.textContent = `-${formatTime((appState.durationSec || 0) - targetSec)}`;
    if (commit && window.pywebview && window.pywebview.api) {
      window.pywebview.api.seek_to(targetSec).then(() => triggerImmediatePoll()).catch(() => {});
    }
  }

  const volHit = document.getElementById("volHit");
  function handleVolPointer(e) {
    const rect = volHit.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    setVolumePercent(Math.round(ratio * 100));
  }

  volHit.addEventListener("pointerdown", (e) => {
    isDraggingVolume = true;
    volHit.setPointerCapture(e.pointerId);
    handleVolPointer(e);
  });
  volHit.addEventListener("pointermove", (e) => {
    if (isDraggingVolume) handleVolPointer(e);
  });
  volHit.addEventListener("pointerup", () => {
    isDraggingVolume = false;
  });

  // ---------- 音量状态（var 声明避免渲染循环早于此处执行时的 TDZ 报错） ----------
  var isDraggingDockVol = false;
  var volumeSyncedFromBackend = false;
  var lastNonZeroVolume = 80;
  var volumePersistTimer = null;

  function volumeIconMarkup(v) {
    const speaker = '<path d="M11 4.5L6.5 8H3.5a1.5 1.5 0 0 0-1.5 1.5v5A1.5 1.5 0 0 0 3.5 16h3L11 19.5a1 1 0 0 0 1.6-.8V5.3a1 1 0 0 0-1.6-.8z" fill="currentColor"></path>';
    if (v <= 0) {
      return speaker + '<line x1="16.5" y1="9.5" x2="21.5" y2="14.5"></line><line x1="21.5" y1="9.5" x2="16.5" y2="14.5"></line>';
    }
    if (v < 50) {
      return speaker + '<path d="M15.5 8.5a5 5 0 0 1 0 7"></path>';
    }
    return speaker + '<path d="M15.5 8.5a5 5 0 0 1 0 7"></path><path d="M18.8 5.2a9 9 0 0 1 0 13.6"></path>';
  }

  function renderVolumeUI(vol) {
    const v = Math.round(Math.min(100, Math.max(0, typeof vol === "number" ? vol : 80)));
    const vf = document.getElementById("volFill");
    if (vf) vf.style.width = `${v}%`;
    const dvf = document.getElementById("dockVolFill");
    if (dvf) {
      dvf.style.height = `${v}%`;
      dvf.style.width = "100%";
    }
    const knob = document.getElementById("dockVolKnob");
    if (knob) knob.style.bottom = `${v}%`;
    const dvt = document.getElementById("dockVolValText");
    if (dvt) dvt.textContent = `${v}%`;
    const level = v <= 0 ? "mute" : (v < 50 ? "low" : "high");
    const btnIcon = document.getElementById("dockVolBtnIcon");
    if (btnIcon && btnIcon.dataset.level !== level) {
      btnIcon.dataset.level = level;
      btnIcon.innerHTML = volumeIconMarkup(v);
    }
    const popIcon = document.getElementById("dockVolPopoverSvg");
    if (popIcon && popIcon.dataset.level !== level) {
      popIcon.dataset.level = level;
      popIcon.innerHTML = volumeIconMarkup(v);
    }
    const wrap = document.getElementById("dockVolumeWrap");
    if (wrap) wrap.classList.toggle("is-muted", v <= 0);
  }

  function schedulePersistVolume() {
    if (volumePersistTimer) clearTimeout(volumePersistTimer);
    volumePersistTimer = setTimeout(() => {
      volumePersistTimer = null;
      if (window.pywebview && window.pywebview.api && window.pywebview.api.save_preferences) {
        window.pywebview.api.save_preferences({ volume: appState.volume }).catch(() => {});
      }
    }, 400);
  }

  function setVolumePercent(pct, persist = false) {
    const v = Math.round(Math.min(100, Math.max(0, Number(pct) || 0)));
    appState.volume = v;
    volumeSyncedFromBackend = true;
    if (v > 0) lastNonZeroVolume = v;
    renderVolumeUI(v);
    if (window.pywebview && window.pywebview.api) {
      window.pywebview.api.set_volume(v);
    }
    if (persist) schedulePersistVolume();
  }

  function toggleMute() {
    const cur = typeof appState.volume === "number" ? appState.volume : 80;
    setVolumePercent(cur > 0 ? 0 : (lastNonZeroVolume || 80), true);
  }

  document.getElementById("driveSelect").addEventListener("change", (e) => {
    if (window.pywebview && window.pywebview.api) {
      window.pywebview.api.select_drive(e.target.value).then(() => triggerImmediatePoll());
    }
  });

  function togglePlayPause() {
    if (window.pywebview && window.pywebview.api) {
      window.pywebview.api.toggle_play_pause().then(() => triggerImmediatePoll());
    }
  }
  function prevTrack() {
    if (window.pywebview && window.pywebview.api) {
      window.pywebview.api.prev_track().then(() => triggerImmediatePoll());
    }
  }
  function nextTrack() {
    if (window.pywebview && window.pywebview.api) {
      window.pywebview.api.next_track().then(() => triggerImmediatePoll());
    }
  }
  function playTrackIndex(idx) {
    appState.currentTrackIndex = idx;
    if (appState.tracks && appState.tracks[idx]) {
      const title = appState.tracks[idx].title || `音轨 ${appState.tracks[idx].track_number}`;
      const titleEl = document.getElementById("immersiveTrackTitle");
      if (titleEl) titleEl.textContent = title;
      const lblEl = document.getElementById("mdLabelTrackTitle");
      if (lblEl) lblEl.textContent = title;
    }

    // 立即高亮选中歌曲行
    const rows = document.querySelectorAll("#fluidTrackList .fluid-track-row");
    rows.forEach((r, i) => {
      const numSpan = r.querySelector(".fluid-track-num");
      if (i === idx) {
        r.classList.add("is-current");
        if (numSpan) numSpan.textContent = "▶";
      } else {
        r.classList.remove("is-current");
        if (numSpan) numSpan.textContent = String(i + 1);
      }
    });

    selectTrack(idx);
  }

  function selectTrack(idx) {
    if (window.pywebview && window.pywebview.api) {
      window.pywebview.api.play_track_index(idx).then(() => triggerImmediatePoll());
    }
  }
  function rescanDrive() {
    if (window.pywebview && window.pywebview.api) {
      window.pywebview.api.rescan_drive().then(() => triggerImmediatePoll());
    }
  }
  function ejectDrive() {
    if (window.pywebview && window.pywebview.api) {
      window.pywebview.api.eject_drive().then(() => triggerImmediatePoll());
    }
  }

  // 强制设定右侧面板模式（不做「同模式再点即收起」的切换语义）
  function applyRightPanelMode(mode) {
    const stage = document.getElementById("mainStage");
    const lyrBtn = document.getElementById("lyricsTabBtn");
    const queBtn = document.getElementById("queueTabBtn");
    const lyrVp = document.getElementById("lyricsViewport");
    const queVp = document.getElementById("queueViewport");
    if (!stage) return;

    if (mode === "hidden") {
      appState.rightPanelMode = "hidden";
      stage.classList.add("single-column");
      if (lyrBtn) lyrBtn.classList.remove("active");
      if (queBtn) queBtn.classList.remove("active");
      return;
    }

    appState.rightPanelMode = mode;
    stage.classList.remove("single-column");
    if (lyrBtn) lyrBtn.classList.toggle("active", mode === "lyrics");
    if (queBtn) queBtn.classList.toggle("active", mode === "queue");
    if (lyrVp) lyrVp.style.display = mode === "lyrics" ? "block" : "none";
    if (queVp) queVp.style.display = mode === "queue" ? "flex" : "none";
    if (mode === "queue") renderQueueList();
  }

  function switchRightPanel(mode) {
    applyRightPanelMode(appState.rightPanelMode === mode ? "hidden" : mode);
  }

  function applyTheme(mode, persist = true) {
    if (mode !== "light" && mode !== "dark") {
      mode = (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches) ? "light" : "dark";
    }
    appState.themeMode = mode;
    currentCustomPrefs.themeMode = mode;
    document.documentElement.setAttribute("data-theme", mode);
    try {
      localStorage.setItem("discflow_theme", mode);
    } catch (e) {}
    const sub = document.getElementById("settingsThemeSub");
    if (sub) {
      sub.textContent = mode === "light" ? "当前：浅色模式 (点击切换深色)" : "当前：深色模式 (点击切换浅色)";
    }
    if (typeof updateTopbarPreview === "function") {
      updateTopbarPreview();
    }
    if (persist) {
      saveCustomPrefs();
    }
  }

  function cycleTheme() {
    appState.themeMode = appState.themeMode === "light" ? "dark" : "light";
    applyTheme(appState.themeMode);
  }

  function minimizeWindow() {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.minimize_window) {
      window.pywebview.api.minimize_window().catch(() => {});
    }
  }

  async function toggleMaximizeWindow() {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.toggle_maximize_window) {
      try {
        const isMax = await window.pywebview.api.toggle_maximize_window();
        const btn = document.getElementById("winMaxBtn");
        if (btn) {
          btn.innerHTML = isMax
            ? '<svg width="11" height="11" viewBox="0 0 12 12" fill="none"><rect x="3.2" y="1.8" width="7" height="7" rx="1.3" stroke="currentColor" stroke-width="1.2"/><path d="M1.8 4.2v4.5A1.5 1.5 0 0 0 3.3 10.2h4.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>'
            : '<svg width="11" height="11" viewBox="0 0 12 12" fill="none"><rect x="2" y="2" width="8" height="8" rx="1.6" stroke="currentColor" stroke-width="1.25"/></svg>';
        }
      } catch (e) {}
    }
  }

  function closeWindow() {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.close_window) {
      window.pywebview.api.close_window().catch(() => {});
    }
  }

  // 无边框窗口 8 向边缘拖拽缩放
  document.querySelectorAll(".win-resize-edge").forEach(el => {
    el.addEventListener("mousedown", (e) => {
      if (e.button !== 0) return;
      const edge = parseInt(el.dataset.edge || "0", 10);
      if (edge >= 1 && edge <= 8 && window.pywebview && window.pywebview.api && window.pywebview.api.begin_window_resize) {
        window.pywebview.api.begin_window_resize(edge).catch(() => {});
      }
    });
  });

  // ================= 版本检查与新版本提醒 =================
  let latestUpdateMeta = null;

  function renderUpdateNotice(info) {
    const pill = document.getElementById("updateNoticePill");
    const txt = document.getElementById("updateNoticeText");
    if (info && info.hasUpdate && info.latestVersion) {
      latestUpdateMeta = info;
      if (txt) txt.textContent = `发现 v${info.latestVersion}`;
      if (pill && !info.ignored) {
        pill.classList.add("visible");
      } else if (pill) {
        pill.classList.remove("visible");
      }
      renderAboutUpdateBanner(info);
    } else if (pill) {
      pill.classList.remove("visible");
    }
  }

  function renderAboutUpdateBanner(info) {
    const banner = document.getElementById("aboutUpdateBanner");
    const title = document.getElementById("aboutUpdateTitle");
    const sub = document.getElementById("aboutUpdateSub");
    const dlBtn = document.getElementById("aboutUpdateDownloadBtn");
    const rowSub = document.getElementById("aboutCheckUpdateSub");
    if (!banner || !info) return;

    banner.classList.add("visible");
    if (!info.ok) {
      banner.classList.add("is-latest");
      if (title) {
        title.textContent = "暂时无法连接更新服务器";
        title.style.color = "var(--text-secondary)";
      }
      if (sub) sub.textContent = info.error || "请检查网络连接后重试";
      if (dlBtn) dlBtn.style.display = "none";
      return;
    }

    if (info.hasUpdate) {
      banner.classList.remove("is-latest");
      if (title) {
        title.textContent = `发现新版本 v${info.latestVersion}`;
        title.style.color = "#4ade80";
      }
      const noteLine = (info.notes || "").split(/\r?\n/).find(l => l.trim()) || "点击右侧前往 GitHub Releases 下载最新版本";
      if (sub) sub.textContent = noteLine.replace(/^#+\s*/, "");
      if (dlBtn) dlBtn.style.display = "inline-flex";
      if (rowSub) rowSub.textContent = `当前 v${info.currentVersion} · 最新 v${info.latestVersion}`;
    } else {
      banner.classList.add("is-latest");
      if (title) {
        title.textContent = `已是最新版本 (v${info.currentVersion})`;
        title.style.color = "var(--text-primary)";
      }
      if (sub) sub.textContent = "当前已经是 GitHub Releases 最新发布版本";
      if (dlBtn) dlBtn.style.display = "none";
      if (rowSub) rowSub.textContent = `当前 v${info.currentVersion} · 已是最新`;
    }
  }

  async function checkForUpdates(manual = false) {
    const btn = document.getElementById("checkUpdateBtn");
    if (btn && manual) {
      btn.disabled = true;
      btn.textContent = "检查中...";
    }
    try {
      if (window.pywebview && window.pywebview.api && window.pywebview.api.check_for_updates) {
        const res = await window.pywebview.api.check_for_updates(!!manual);
        if (res) {
          latestUpdateMeta = res;
          renderAboutUpdateBanner(res);
          renderUpdateNotice(res);
          if (manual) {
            if (!res.ok) {
              showToast("检查更新失败，请检查网络");
            } else if (res.hasUpdate) {
              showToast(`发现新版本 v${res.latestVersion}`);
            } else {
              showToast(`已是最新版本 v${res.currentVersion}`);
            }
          }
        }
      }
    } catch (e) {
      if (manual) showToast("检查更新失败");
    } finally {
      if (btn && manual) {
        btn.disabled = false;
        btn.textContent = "检查更新";
      }
    }
  }

  function openSettingsUpdate() {
    openModal("settingsModal");
    switchSettingsTab("about");
    if (latestUpdateMeta) {
      renderAboutUpdateBanner(latestUpdateMeta);
    } else {
      checkForUpdates(true);
    }
  }

  function dismissUpdateNotice(e) {
    if (e) e.stopPropagation();
    const pill = document.getElementById("updateNoticePill");
    if (pill) pill.classList.remove("visible");
    if (latestUpdateMeta && latestUpdateMeta.latestVersion && window.pywebview && window.pywebview.api && window.pywebview.api.ignore_update_version) {
      latestUpdateMeta.ignored = true;
      window.pywebview.api.ignore_update_version(latestUpdateMeta.latestVersion).catch(() => {});
    }
  }

  function openUpdateDownload() {
    const url = (latestUpdateMeta && latestUpdateMeta.releaseUrl) || "https://github.com/JiAng10es/Discflow/releases";
    if (window.pywebview && window.pywebview.api && window.pywebview.api.open_external_url) {
      window.pywebview.api.open_external_url(url).catch(() => {});
    } else {
      window.open(url, "_blank");
    }
  }

  let bridgeInitialized = false;
  function initPywebviewBridge() {
    if (bridgeInitialized) return true;
    if (window.pywebview && window.pywebview.api) {
      bridgeInitialized = true;
      try {
        window.pywebview.api.ui_ready();
      } catch (e) {
        console.warn("ui_ready error:", e);
      }
      syncPreferencesFromBackend();
      syncLibraryPathText();
      startKaraokeLoop();
      pollBackendState();
      setInterval(pollBackendState, 500);
      return true;
    }
    return false;
  }

  if (!initPywebviewBridge()) {
    window.addEventListener("pywebviewready", initPywebviewBridge);
    let bridgeCheckCount = 0;
    const bridgePollTimer = setInterval(() => {
      bridgeCheckCount++;
      if (initPywebviewBridge() || bridgeCheckCount > 40) {
        clearInterval(bridgePollTimer);
      }
    }, 60);
  }

  // ================= Apple Music 双栏主视图切换逻辑 =================
  let currentMainView = "cd"; // "cd" | "library" | "lyrics"

  function switchMainView(viewName) {
    currentMainView = viewName;
    const map = { cd: "viewCd", library: "viewLibrary", lyrics: "viewLyrics" };
    document.querySelectorAll(".view-page").forEach(page => page.classList.remove("active"));
    const target = document.getElementById(map[viewName] || "viewCd");
    if (target) target.classList.add("active");

    if (viewName === "library") {
      renderLibraryCards();
    } else if (viewName === "lyrics") {
      const svp = document.getElementById("standaloneLyricsViewport");
      if (svp && svp.children.length === 0 && appState.lyrics && appState.lyrics.length) {
        renderLyricsList(appState.lyrics);
      }
      updateActiveLyric(appState.elapsedSec || 0, true);
      if (svp && appState.activeLyricIndex >= 0) {
        const item = svp.querySelector(`.lyric-item[data-index="${appState.activeLyricIndex}"]`);
        if (item) {
          svp.scrollTo({
            top: Math.max(0, item.offsetTop - svp.clientHeight * 0.36 + item.clientHeight * 0.5),
            behavior: "instant"
          });
        }
      }
    }
  }

  function renderStandaloneLyrics() {
    const vp = document.getElementById("standaloneLyricsViewport");
    const orig = document.getElementById("lyricsViewport");
    if (vp && orig) {
      vp.innerHTML = orig.innerHTML;
    }
  }

  // 底部 Dock 栏进度条拖拽绑定
  const dockScrubberHit = document.getElementById("dockScrubberHit");
  if (dockScrubberHit) {
    dockScrubberHit.addEventListener("pointerdown", (e) => {
      isDraggingScrubber = true;
      dockScrubberHit.setPointerCapture(e.pointerId);
      handleDockScrubberPointer(e, false);
    });
    dockScrubberHit.addEventListener("pointermove", (e) => {
      if (isDraggingScrubber) handleDockScrubberPointer(e, false);
    });
    dockScrubberHit.addEventListener("pointerup", (e) => {
      if (!isDraggingScrubber) return;
      isDraggingScrubber = false;
      handleDockScrubberPointer(e, true);
    });
  }

  function handleDockScrubberPointer(e, commit) {
    if (!dockScrubberHit) return;
    const rect = dockScrubberHit.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    const targetSec = ratio * (appState.durationSec || 0);
    const fill = document.getElementById("dockScrubberFill");
    if (fill) fill.style.width = `${ratio * 100}%`;
    const elTime = document.getElementById("dockElapsedTime");
    if (elTime) elTime.textContent = formatTime(targetSec);
    const remTime = document.getElementById("dockRemainingTime");
    if (remTime) remTime.textContent = `-${formatTime((appState.durationSec || 0) - targetSec)}`;
    if (commit && window.pywebview && window.pywebview.api) {
      window.pywebview.api.seek_to(targetSec).then(() => triggerImmediatePoll()).catch(() => {});
    }
  }

  // 底部 Dock 音量拖拽
  const dockVolHit = document.getElementById("dockVolHit");
  if (dockVolHit) {
    dockVolHit.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      dockVolHit.setPointerCapture(e.pointerId);
      isDraggingDockVol = true;
      dockVolHit.classList.add("is-dragging");
      handleDockVolPointer(e);
    });
    dockVolHit.addEventListener("pointermove", (e) => {
      if (isDraggingDockVol) handleDockVolPointer(e);
    });
    const endDockVolDrag = () => {
      if (!isDraggingDockVol) return;
      isDraggingDockVol = false;
      dockVolHit.classList.remove("is-dragging");
      schedulePersistVolume();
    };
    dockVolHit.addEventListener("pointerup", endDockVolDrag);
    dockVolHit.addEventListener("pointercancel", endDockVolDrag);
    dockVolHit.addEventListener("lostpointercapture", endDockVolDrag);
  }

  // 在音量按钮 / 音量卡片上滚动鼠标滚轮，每格 ±5%
  const dockVolumeWrapEl = document.getElementById("dockVolumeWrap");
  if (dockVolumeWrapEl) {
    dockVolumeWrapEl.addEventListener("wheel", (e) => {
      e.preventDefault();
      const cur = typeof appState.volume === "number" ? appState.volume : 80;
      setVolumePercent(cur + (e.deltaY < 0 ? 5 : -5), true);
    }, { passive: false });
  }

  function handleDockVolPointer(e) {
    if (!dockVolHit) return;
    const rect = dockVolHit.getBoundingClientRect();
    // 竖向计算：从下到上为 0% 到 100%
    const ratio = Math.min(1, Math.max(0, (rect.bottom - e.clientY) / rect.height));
    const pct = ratio * 100;
    setVolumePercent(pct);
  }

