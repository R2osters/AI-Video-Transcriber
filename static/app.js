/* ────────────────────────────────────────────────────────────
   AI Video Transcriber · app.js
   ──────────────────────────────────────────────────────────── */

class VideoTranscriber {
  constructor() {
    this.currentTaskId  = null;
    this.eventSource    = null;
    this.apiBase        = '/api';
    this.currentLang    = 'en';

    /* Smart progress simulation */
    this.sp = {
      enabled: false, current: 0, target: 15,
      lastServer: 0, interval: null, startTime: null, stage: 'preparing'
    };

    this.i18n = {
      en: {
        title:                   'AI Video Transcriber',
        subtitle:                'Supports automatic transcription and AI summary for 30+ platforms',
        video_url_placeholder:   'Paste YouTube, Tiktok, Bilibili or other platform video URLs...',
        start_transcription:     'Transcribe',
        ai_settings:             'AI Settings',
        model_base_url:          'Model API Base URL',
        model_base_url_placeholder: 'https://openrouter.ai/api/v1',
        api_key:                 'API Key',
        api_key_placeholder:     'sk-...',
        fetch_models:            'Fetch',
        model_select:            'Model',
        model_default:           '— use server default —',
        summary_language:        'Summary Language',
        processing_progress:     'Processing',
        preparing:               'Preparing…',
        transcript_text:         'Transcript',
        intelligent_summary:     'AI Summary',
        translation:             'Translation',
        download_transcript:     'Transcript',
        download_translation:    'Translation',
        download_summary:        'Summary',
        empty_hint:              'Paste a video URL or drop a file above and let AI do the heavy lifting.',
        footer_text:             'This tool is part of <a href="https://sipsip.ai" target="_blank" style="color:var(--accent-text);text-decoration:none;">sipsip.ai</a> — distill anything and get daily AI briefs from your favorite creators',
        processing:              'Processing…',
        downloading_video:       'Downloading audio…',
        parsing_video:           'Parsing video info…',
        transcribing_audio:      'Transcribing audio…',
        optimizing_transcript:   'Optimizing transcript…',
        generating_summary:      'Generating summary…',
        detecting_subtitles:     'Detecting subtitles…',
        subtitle_found:          'Subtitles found! Processing text…',
        no_subtitle:             'No subtitles found, downloading audio…',
        mode_subtitle:           '⚡ Subtitle',
        mode_whisper:            '🎙 Whisper',
        completed:               'Done!',
        error_invalid_url:       'Please enter a valid video URL',
        error_processing_failed: 'Processing failed: ',
        error_no_download:       'No file available for download',
        error_download_failed:   'Download failed: ',
        fetching_models:         'Fetching models…',
        models_loaded:           (n) => `${n} models loaded`,
        models_error:            'Failed to fetch models',
        upload_or:               'or drop your files',
        upload_formats:          '.mp3 · .wav · .m4a · .aac · .opus · .flac · .ogg · .mp4 · .mov · .webm · .mkv · .avi',
        upload_files_btn:        'Upload files',
        record_btn:              'Record',
        record_stop:             'Stop',
        history:                 'History',
        history_empty:           'No completed transcriptions yet',
        chat_tab:                'Chat',
        chat_empty:              'Ask anything about this transcript.',
        chat_placeholder:        'Ask a question…',
        chat_error:              'Chat failed: ',
        error_mic_unsupported:   'Recording not supported in this browser',
        error_mic_denied:        'Microphone access denied',
        error_upload_type:       'Unsupported file type',
        error_upload_empty:      'File is empty',
        error_upload_size:       (mb) => `File exceeds ${mb} MB limit`,
      },
      zh: {
        title:                   'AI 视频转录器',
        subtitle:                '粘贴 YouTube、TikTok 或任意公开视频链接，获取转录文本和 AI 摘要。',
        video_url_placeholder:   '请输入视频链接…',
        start_transcription:     '开始转录',
        ai_settings:             'AI 设置',
        model_base_url:          'Model API 地址',
        model_base_url_placeholder: 'https://openrouter.ai/api/v1',
        api_key:                 'API Key',
        api_key_placeholder:     'sk-...',
        fetch_models:            '获取',
        model_select:            '模型',
        model_default:           '— 使用服务器默认 —',
        summary_language:        '摘要语言',
        processing_progress:     '处理进度',
        preparing:               '准备中…',
        transcript_text:         '转录文本',
        intelligent_summary:     '智能摘要',
        translation:             '翻译',
        download_transcript:     '转录',
        download_translation:    '翻译',
        download_summary:        '摘要',
        empty_hint:              '在上方粘贴视频链接或拖放文件，让 AI 来处理一切。',
        footer_text:             '本工具是 <a href="https://sipsip.ai" target="_blank" style="color:var(--accent-text);text-decoration:none;">sipsip.ai</a> 的一部分 — 提取任何内容要点并构建你自己的知识库。',
        processing:              '处理中…',
        downloading_video:       '正在下载音频…',
        parsing_video:           '正在解析视频信息…',
        transcribing_audio:      '正在转录音频…',
        optimizing_transcript:   '正在优化转录文本…',
        generating_summary:      '正在生成摘要…',
        detecting_subtitles:     '正在检测字幕…',
        subtitle_found:          '字幕获取成功！正在处理文本…',
        no_subtitle:             '未找到字幕，正在下载音频…',
        mode_subtitle:           '⚡ 字幕模式',
        mode_whisper:            '🎙 Whisper 模式',
        completed:               '处理完成！',
        error_invalid_url:       '请输入有效的视频链接',
        error_processing_failed: '处理失败：',
        error_no_download:       '没有可下载的文件',
        error_download_failed:   '下载失败：',
        fetching_models:         '正在获取模型列表…',
        models_loaded:           (n) => `已加载 ${n} 个模型`,
        models_error:            '获取模型失败',
        upload_or:               '或拖放文件到此处',
        upload_formats:          '.mp3 · .wav · .m4a · .aac · .opus · .flac · .ogg · .mp4 · .mov · .webm · .mkv · .avi',
        upload_files_btn:        '上传文件',
        record_btn:              '录音',
        record_stop:             '停止',
        history:                 '历史记录',
        history_empty:           '暂无已完成的转录',
        chat_tab:                '对话',
        chat_empty:              '就这份转录内容随意提问。',
        chat_placeholder:        '输入问题…',
        chat_error:              '对话失败：',
        error_mic_unsupported:   '当前浏览器不支持录音',
        error_mic_denied:        '麦克风权限被拒绝',
        error_upload_type:       '不支持的文件类型',
        error_upload_empty:      '文件为空',
        error_upload_size:       (mb) => `文件超过 ${mb} MB 限制`,
      }
    };

    this._initElements();
    this._bindEvents();
    this._loadSettings();
    this._switchLang('en');
  }

  /* ── Elements ─────────────────────────────────────────── */
  _initElements() {
    this.form               = document.getElementById('videoForm');
    this.videoUrlInput      = document.getElementById('videoUrl');
    this.submitBtn          = document.getElementById('submitBtn');
    this.summaryLangSel     = document.getElementById('summaryLanguage');
    this.langToggle         = document.getElementById('langToggle');
    this.langText           = document.getElementById('langText');
    this.errorBanner        = document.getElementById('errorBanner');
    this.errorMsg           = document.getElementById('errorMsg');
    this.emptyState         = document.getElementById('emptyState');
    this.progressPanel      = document.getElementById('progressPanel');
    this.modeBadge          = document.getElementById('modeBadge');
    this.progressStatus     = document.getElementById('progressStatus');
    this.progressFill       = document.getElementById('progressFill');
    this.progressMessage    = document.getElementById('progressMessage');
    this.resultsPanel       = document.getElementById('resultsPanel');
    this.scriptContent      = document.getElementById('scriptContent');
    this.summaryContent     = document.getElementById('summaryContent');
    this.translationContent = document.getElementById('translationContent');
    this.dlScript           = document.getElementById('downloadScript');
    this.dlTranslation      = document.getElementById('downloadTranslation');
    this.dlSummary          = document.getElementById('downloadSummary');
    this.dlSrt              = document.getElementById('downloadSrt');
    this.dlVtt              = document.getElementById('downloadVtt');
    this.translationTabBtn  = document.getElementById('translationTabBtn');
    this.tabBtns            = document.querySelectorAll('.tab-btn');
    this.tabPanes           = document.querySelectorAll('.tab-pane');
    // settings
    this.settingsToggle     = document.getElementById('settingsToggle');
    this.settingsBody       = document.getElementById('settingsBody');
    this.modelBaseUrl       = document.getElementById('modelBaseUrl');
    this.apiKeyInput        = document.getElementById('apiKeyInput');
    this.fetchModelsBtn     = document.getElementById('fetchModelsBtn');
    this.fetchStatus        = document.getElementById('fetchStatus');
    this.modelSelect        = document.getElementById('modelSelect');
    this.fetchIcon          = document.getElementById('fetchIcon');
    this.uploadZone         = document.getElementById('uploadZone');
    this.uploadPickBtn      = document.getElementById('uploadPickBtn');
    this.fileInput          = document.getElementById('fileInput');
    this.uploadQueue        = document.getElementById('uploadQueue');
    this.chatMessages       = document.getElementById('chatMessages');
    this.chatEmpty          = document.getElementById('chatEmpty');
    this.chatInput          = document.getElementById('chatInput');
    this.chatSend           = document.getElementById('chatSend');
    this._chatHistory       = [];
    this._chatTaskId        = null;
    this.historyToggle      = document.getElementById('historyToggle');
    this.historyPanel       = document.getElementById('historyPanel');
    this.historyClose       = document.getElementById('historyClose');
    this.historyList        = document.getElementById('historyList');
    this.recordBtn          = document.getElementById('recordBtn');
    this.recordIcon         = document.getElementById('recordIcon');
    this.recordLabel        = document.getElementById('recordLabel');
    this.recordTimer        = document.getElementById('recordTimer');
    this._recorder          = null;
    this._recChunks         = [];
    this._recTimerInterval  = null;
    this._recStream         = null;
    this._queue             = [];
    this._queueBusy         = false;
    this._taskDoneResolve   = null;
    this.uploadMaxMb        = 200;
    this._allowedUploadExts = new Set([
      '.txt',
      '.mp3', '.m4a', '.wav', '.ogg', '.flac', '.aac', '.opus', '.wma', '.aiff',
      '.mp4', '.webm', '.mkv', '.mov', '.avi',
    ]);
  }

  /* ── Events ───────────────────────────────────────────── */
  _bindEvents() {
    this.form.addEventListener('submit', (e) => { e.preventDefault(); this._startTranscription(); });

    this.langToggle.addEventListener('click', () => {
      this._switchLang(this.currentLang === 'en' ? 'zh' : 'en');
    });

    // Settings toggle
    this.settingsToggle.addEventListener('click', () => {
      const open = this.settingsBody.classList.toggle('open');
      this.settingsToggle.classList.toggle('open', open);
    });

    // Chat
    if (this.chatSend) {
      this.chatSend.addEventListener('click', () => this._sendChat());
      this.chatInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); this._sendChat(); }
      });
    }

    // History
    if (this.historyToggle) {
      this.historyToggle.addEventListener('click', () => this._toggleHistory());
      this.historyClose.addEventListener('click', () => this.historyPanel.classList.remove('show'));
    }

    // Fetch models
    this.fetchModelsBtn.addEventListener('click', () => this._fetchModels());

    // Auto-fetch when both fields filled (debounced)
    const debouncedFetch = this._debounce(() => {
      if (this.modelBaseUrl.value.trim() && this.apiKeyInput.value.trim()) this._fetchModels();
    }, 900);
    this.modelBaseUrl.addEventListener('input', debouncedFetch);
    this.apiKeyInput.addEventListener('input', debouncedFetch);

    // Persist settings
    [this.modelBaseUrl, this.apiKeyInput, this.modelSelect, this.summaryLangSel].forEach(el => {
      el.addEventListener('change', () => this._saveSettings());
    });

    // Tabs
    this.tabBtns.forEach(btn => {
      btn.addEventListener('click', () => this._switchTab(btn.dataset.tab));
    });

    // Downloads
    this.dlScript.addEventListener('click',      () => this._downloadFile('script'));
    this.dlTranslation.addEventListener('click', () => this._downloadFile('translation'));
    this.dlSummary.addEventListener('click',     () => this._downloadFile('summary'));
    if (this.dlSrt) this.dlSrt.addEventListener('click', () => this._downloadFile('srt'));
    if (this.dlVtt) this.dlVtt.addEventListener('click', () => this._downloadFile('vtt'));

    if (this.uploadPickBtn && this.fileInput && this.uploadZone) {
      this.uploadPickBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        this.fileInput.click();
      });
      this.uploadZone.addEventListener('click', (e) => {
        if (e.target === this.uploadPickBtn || this.uploadPickBtn.contains(e.target)) return;
        this.fileInput.click();
      });
      this.fileInput.addEventListener('change', () => {
        const files = Array.from(this.fileInput.files || []);
        this.fileInput.value = '';
        if (files.length) this._enqueueFiles(files);
      });
      ['dragenter', 'dragover'].forEach((ev) => {
        this.uploadZone.addEventListener(ev, (e) => {
          e.preventDefault();
          e.stopPropagation();
          this.uploadZone.classList.add('dragover');
        });
      });
      this.uploadZone.addEventListener('dragleave', (e) => {
        e.preventDefault();
        if (!this.uploadZone.contains(e.relatedTarget)) {
          this.uploadZone.classList.remove('dragover');
        }
      });
      if (this.recordBtn) {
        this.recordBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          this._toggleRecording();
        });
      }
      this.uploadZone.addEventListener('drop', (e) => {
        e.preventDefault();
        e.stopPropagation();
        this.uploadZone.classList.remove('dragover');
        const files = Array.from((e.dataTransfer && e.dataTransfer.files) || []);
        if (files.length) this._enqueueFiles(files);
      });
    }
  }

  /* ── i18n ─────────────────────────────────────────────── */
  t(key) { return this.i18n[this.currentLang][key] || this.i18n['en'][key] || key; }

  _switchLang(lang) {
    this.currentLang = lang;
    this.langText.textContent = lang === 'en' ? 'English' : '中文';
    document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en';
    document.title = this.t('title');

    document.querySelectorAll('[data-i18n]').forEach(el => {
      const v = this.t(el.dataset.i18n);
      if (typeof v === 'string') {
        // footer 等允许含 HTML 的 key 用 innerHTML，其余保持 textContent
        if (el.dataset.i18n === 'footer_text') el.innerHTML = v;
        else el.textContent = v;
      }
    });
    document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
      const v = this.t(el.dataset.i18nPlaceholder);
      if (typeof v === 'string') el.placeholder = v;
    });
  }

  /* ── Settings persistence ─────────────────────────────── */
  _saveSettings() {
    const s = {
      baseUrl:  this.modelBaseUrl.value,
      apiKey:   this.apiKeyInput.value,
      model:    this.modelSelect.value,
      summaryLang: this.summaryLangSel.value,
    };
    try { localStorage.setItem('vt_settings', JSON.stringify(s)); } catch (_) {}
  }

  _loadSettings() {
    try {
      const raw = localStorage.getItem('vt_settings');
      if (!raw) return;
      const s = JSON.parse(raw);
      if (s.baseUrl)     this.modelBaseUrl.value = s.baseUrl;
      if (s.apiKey)      this.apiKeyInput.value  = s.apiKey;
      if (s.summaryLang) this.summaryLangSel.value = s.summaryLang;
      // Model options will be restored after fetching
      this._savedModel = s.model || '';

      // Auto-open settings if credentials were saved
      if (s.baseUrl || s.apiKey) {
        this.settingsBody.classList.add('open');
        this.settingsToggle.classList.add('open');
        // Attempt to re-fetch model list silently
        if (s.baseUrl && s.apiKey) {
          setTimeout(() => this._fetchModels(true), 400);
        }
      }
    } catch (_) {}
  }

  /* ── Fetch models ─────────────────────────────────────── */
  async _fetchModels(silent = false) {
    const baseUrl = this.modelBaseUrl.value.trim().replace(/\/$/, '');
    const apiKey  = this.apiKeyInput.value.trim();

    if (!baseUrl || !apiKey) {
      if (!silent) this._setFetchStatus('err', this.t('api_key') + ' & URL required');
      return;
    }

    this.fetchModelsBtn.disabled = true;
    this.fetchIcon.className = 'fas fa-spinner fa-spin';
    if (!silent) this._setFetchStatus('', this.t('fetching_models'));

    try {
      const fd = new FormData();
      fd.append('base_url', baseUrl);
      fd.append('api_key',  apiKey);

      const resp = await fetch(`${this.apiBase}/models`, { method: 'POST', body: fd });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      const models = data.data || data.models || [];

      // Rebuild select options
      this.modelSelect.innerHTML = `<option value="">${this.t('model_default')}</option>`;
      models.forEach(m => {
        const opt = document.createElement('option');
        opt.value = m.id;
        opt.textContent = m.name || m.id;
        this.modelSelect.appendChild(opt);
      });

      // Restore previously selected model
      if (this._savedModel) {
        this.modelSelect.value = this._savedModel;
        this._savedModel = '';
      }

      this._setFetchStatus('ok', typeof this.t('models_loaded') === 'function'
        ? this.t('models_loaded')(models.length)
        : `${models.length} models`);

    } catch (e) {
      console.warn('Model fetch error:', e);
      this._setFetchStatus('err', this.t('models_error') + ': ' + e.message);
    } finally {
      this.fetchModelsBtn.disabled = false;
      this.fetchIcon.className = 'fas fa-sync-alt';
    }
  }

  _setFetchStatus(cls, msg) {
    this.fetchStatus.className = 'fetch-status' + (cls ? ` ${cls}` : '');
    this.fetchStatus.textContent = msg;
  }

  /* ── Transcription ────────────────────────────────────── */
  async _startTranscription() {
    if (this.submitBtn.disabled) return;

    const url     = this.videoUrlInput.value.trim();
    const sumLang = this.summaryLangSel.value;

    if (!url) { this._showError(this.t('error_invalid_url')); return; }

    this._setLoading(true);
    this._hideError();
    this._showProgress();

    try {
      const fd = new FormData();
      fd.append('url',              url);
      fd.append('summary_language', sumLang);

      const apiKey  = this.apiKeyInput.value.trim();
      const baseUrl = this.modelBaseUrl.value.trim().replace(/\/$/, '');
      const modelId = this.modelSelect.value;
      if (apiKey)  fd.append('api_key',       apiKey);
      if (baseUrl) fd.append('model_base_url', baseUrl);
      if (modelId) fd.append('model_id',       modelId);

      const resp = await fetch(`${this.apiBase}/process-video`, { method: 'POST', body: fd });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || 'Request failed');
      }

      const data = await resp.json();
      this.currentTaskId = data.task_id;

      this._initSP();
      this._updateProgress(5, this.t('preparing'), true);
      this._startSSE();
      this._saveSettings();

    } catch (err) {
      this._showError(this.t('error_processing_failed') + err.message);
      this._setLoading(false);
      this._hideProgress();
    }
  }

  /* ── Chat with transcript ─────────────────────────────── */
  _resetChat(taskId) {
    if (this._chatTaskId === taskId) return;
    this._chatTaskId = taskId;
    this._chatHistory = [];
    if (this.chatMessages) {
      this.chatMessages.innerHTML = '';
      if (this.chatEmpty) {
        this.chatMessages.appendChild(this.chatEmpty);
        this.chatEmpty.style.display = '';
      }
    }
  }

  _appendChatMsg(role, content, pending = false) {
    if (this.chatEmpty) this.chatEmpty.style.display = 'none';
    const div = document.createElement('div');
    div.className = `chat-msg ${role}${pending ? ' pending' : ''}`;
    if (role === 'assistant' && !pending) div.innerHTML = marked.parse(content);
    else div.textContent = content;
    this.chatMessages.appendChild(div);
    this.chatMessages.scrollTop = this.chatMessages.scrollHeight;
    return div;
  }

  async _sendChat() {
    const q = (this.chatInput.value || '').trim();
    if (!q || !this.currentTaskId) return;
    if (this.chatSend.disabled) return;

    this.chatInput.value = '';
    this.chatSend.disabled = true;
    this._appendChatMsg('user', q);
    const pendingEl = this._appendChatMsg('assistant', '…', true);

    try {
      const fd = new FormData();
      fd.append('task_id',  this.currentTaskId);
      fd.append('question', q);
      fd.append('history',  JSON.stringify(this._chatHistory.slice(-10)));

      const apiKey  = this.apiKeyInput.value.trim();
      const baseUrl = this.modelBaseUrl.value.trim().replace(/\/$/, '');
      const modelId = this.modelSelect.value;
      if (apiKey)  fd.append('api_key',        apiKey);
      if (baseUrl) fd.append('model_base_url', baseUrl);
      if (modelId) fd.append('model_id',       modelId);

      const resp = await fetch(`${this.apiBase}/chat`, { method: 'POST', body: fd });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(typeof err.detail === 'string' ? err.detail : `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      const answer = data.answer || '';

      pendingEl.classList.remove('pending');
      pendingEl.innerHTML = marked.parse(answer);
      this.chatMessages.scrollTop = this.chatMessages.scrollHeight;

      this._chatHistory.push({ role: 'user', content: q });
      this._chatHistory.push({ role: 'assistant', content: answer });
    } catch (e) {
      pendingEl.classList.remove('pending');
      pendingEl.textContent = this.t('chat_error') + e.message;
    } finally {
      this.chatSend.disabled = false;
      this.chatInput.focus();
    }
  }

  /* ── History ──────────────────────────────────────────── */
  async _toggleHistory() {
    const open = this.historyPanel.classList.toggle('show');
    if (open) await this._loadHistory();
  }

  async _loadHistory() {
    this.historyList.innerHTML = `<div class="hp-empty">…</div>`;
    try {
      const r = await fetch(`${this.apiBase}/history`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      const items = data.items || [];
      this.historyList.innerHTML = '';
      if (!items.length) {
        const d = document.createElement('div');
        d.className = 'hp-empty';
        d.textContent = this.t('history_empty');
        this.historyList.appendChild(d);
        return;
      }
      for (const it of items) {
        const row = document.createElement('div');
        row.className = 'hp-row';

        const info = document.createElement('div');
        info.className = 'hp-info';
        const name = document.createElement('div');
        name.className = 'hp-name';
        name.textContent = it.video_title;
        const meta = document.createElement('div');
        meta.className = 'hp-meta';
        const when = it.created_at ? new Date(it.created_at * 1000).toLocaleString() : '';
        const src = (it.url || '').startsWith('upload:') ? '📁' : '🔗';
        meta.textContent = `${src} ${when}${it.detected_language ? ' · ' + it.detected_language : ''}`;
        info.appendChild(name);
        info.appendChild(meta);

        const del = document.createElement('button');
        del.className = 'hp-del';
        del.innerHTML = '<i class="fas fa-trash"></i>';
        del.addEventListener('click', async (e) => {
          e.stopPropagation();
          try {
            await fetch(`${this.apiBase}/task/${it.task_id}`, { method: 'DELETE' });
          } catch (_) {}
          this._loadHistory();
        });

        row.appendChild(info);
        row.appendChild(del);
        row.addEventListener('click', () => this._openHistoryItem(it.task_id));
        this.historyList.appendChild(row);
      }
    } catch (e) {
      this.historyList.innerHTML = '';
      const d = document.createElement('div');
      d.className = 'hp-empty';
      d.textContent = this.t('error_processing_failed') + e.message;
      this.historyList.appendChild(d);
    }
  }

  async _openHistoryItem(taskId) {
    try {
      const r = await fetch(`${this.apiBase}/task-status/${taskId}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const task = await r.json();
      if (task.status !== 'completed') return;
      this.currentTaskId = taskId;
      this.historyPanel.classList.remove('show');
      this.emptyState.style.display = 'none';
      this._hideProgress();
      this._showResults(task);
    } catch (e) {
      this._showError(this.t('error_processing_failed') + e.message);
    }
  }

  /* ── Microphone recording ─────────────────────────────── */
  async _toggleRecording() {
    if (this._recorder && this._recorder.state === 'recording') {
      this._recorder.stop();
      return;
    }
    if (!navigator.mediaDevices || !window.MediaRecorder) {
      this._showError(this.t('error_mic_unsupported'));
      return;
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (_) {
      this._showError(this.t('error_mic_denied'));
      return;
    }

    // Chrome/Firefox → webm/opus ; Safari → mp4 (.m4a)
    const candidates = [
      { mime: 'audio/webm;codecs=opus', ext: '.webm' },
      { mime: 'audio/webm',             ext: '.webm' },
      { mime: 'audio/mp4',              ext: '.m4a'  },
    ];
    const pick = candidates.find(c => MediaRecorder.isTypeSupported(c.mime)) || { mime: '', ext: '.webm' };

    this._recChunks = [];
    this._recStream = stream;
    this._recorder = new MediaRecorder(stream, pick.mime ? { mimeType: pick.mime } : undefined);
    this._recorder.addEventListener('dataavailable', (e) => {
      if (e.data && e.data.size) this._recChunks.push(e.data);
    });
    this._recorder.addEventListener('stop', () => {
      this._setRecordingUI(false);
      stream.getTracks().forEach(t => t.stop());
      this._recStream = null;
      const blob = new Blob(this._recChunks, { type: pick.mime || 'audio/webm' });
      this._recChunks = [];
      this._recorder = null;
      if (!blob.size) { this._showError(this.t('error_upload_empty')); return; }
      const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
      const file = new File([blob], `recording_${stamp}${pick.ext}`, { type: blob.type });
      this._enqueueFiles([file]);
    });

    this._recorder.start();
    this._setRecordingUI(true);
  }

  _setRecordingUI(on) {
    if (!this.recordBtn) return;
    this.recordBtn.classList.toggle('recording', on);
    this.recordIcon.className = on ? 'fas fa-stop' : 'fas fa-microphone';
    this.recordLabel.textContent = on ? this.t('record_stop') : this.t('record_btn');
    this.recordTimer.style.display = on ? 'inline' : 'none';
    if (on) {
      const t0 = Date.now();
      this.recordTimer.textContent = '0:00';
      this._recTimerInterval = setInterval(() => {
        const s = Math.floor((Date.now() - t0) / 1000);
        this.recordTimer.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
      }, 500);
    } else if (this._recTimerInterval) {
      clearInterval(this._recTimerInterval);
      this._recTimerInterval = null;
    }
  }

  /* ── Multi-file upload queue ──────────────────────────── */
  _validateFile(file) {
    const parts = (file.name || '').split('.');
    const ext = parts.length > 1 ? ('.' + parts.pop().toLowerCase()) : '';
    if (!this._allowedUploadExts.has(ext)) return this.t('error_upload_type');
    if (!file.size)                        return this.t('error_upload_empty');
    if (file.size > this.uploadMaxMb * 1024 * 1024) return this.t('error_upload_size')(this.uploadMaxMb);
    return null;
  }

  _enqueueFiles(files) {
    for (const f of files) {
      const err = this._validateFile(f);
      this._queue.push({
        file: f,
        name: f.name || 'file',
        status: err ? 'error' : 'pending',
        err: err || null,
      });
    }
    this._renderQueue();
    this._pumpQueue();
  }

  _renderQueue() {
    if (!this.uploadQueue) return;
    if (!this._queue.length) { this.uploadQueue.classList.remove('show'); this.uploadQueue.innerHTML = ''; return; }
    this.uploadQueue.classList.add('show');
    this.uploadQueue.innerHTML = '';
    const icons = {
      pending:    '<i class="fas fa-clock"></i>',
      processing: '<span class="spinner" style="width:11px;height:11px;border-width:1.5px;"></span>',
      done:       '<i class="fas fa-check"></i>',
      error:      '<i class="fas fa-times"></i>',
    };
    for (const item of this._queue) {
      const row = document.createElement('div');
      row.className = `uq-row ${item.status}`;
      const name = document.createElement('span');
      name.className = 'uq-name';
      name.textContent = item.name + (item.err ? ` — ${item.err}` : '');
      const st = document.createElement('span');
      st.className = 'uq-status';
      st.innerHTML = icons[item.status] || '';
      row.appendChild(name);
      row.appendChild(st);
      this.uploadQueue.appendChild(row);
    }
  }

  async _pumpQueue() {
    if (this._queueBusy) return;
    this._queueBusy = true;
    try {
      for (;;) {
        const item = this._queue.find(i => i.status === 'pending');
        if (!item) break;
        item.status = 'processing';
        this._renderQueue();
        try {
          await this._uploadAndWait(item.file);
          item.status = 'done';
        } catch (err) {
          item.status = 'error';
          item.err = err.message || String(err);
          this._showError(this.t('error_processing_failed') + item.err);
        }
        this._renderQueue();
      }
    } finally {
      this._queueBusy = false;
      this._setLoading(false);
      this._hideProgress();
    }
  }

  /** Upload one file, resolve when its task completes (or reject on error). */
  _uploadAndWait(file) {
    return new Promise(async (resolve, reject) => {
      this._setLoading(true);
      this._hideError();
      this._showProgress();

      try {
        const fd = new FormData();
        fd.append('file', file, file.name);
        fd.append('summary_language', this.summaryLangSel.value);

        const apiKey  = this.apiKeyInput.value.trim();
        const baseUrl = this.modelBaseUrl.value.trim().replace(/\/$/, '');
        const modelId = this.modelSelect.value;
        if (apiKey)  fd.append('api_key',        apiKey);
        if (baseUrl) fd.append('model_base_url', baseUrl);
        if (modelId) fd.append('model_id',       modelId);

        const resp = await fetch(`${this.apiBase}/process-video`, { method: 'POST', body: fd });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          const d = err.detail;
          const msg = typeof d === 'string'
            ? d
            : (Array.isArray(d) && d[0] && (d[0].msg || d[0].message))
              || `HTTP ${resp.status}`;
          throw new Error(msg);
        }

        const data = await resp.json();
        this.currentTaskId = data.task_id;

        this._taskDoneResolve = (status, errMsg) => {
          this._taskDoneResolve = null;
          if (status === 'completed') resolve();
          else reject(new Error(errMsg || 'Processing error'));
        };

        this._initSP();
        this._updateProgress(5, this.t('preparing'), true);
        this._startSSE();
        this._saveSettings();

      } catch (err) {
        reject(err);
      }
    });
  }

  /* ── SSE ──────────────────────────────────────────────── */
  _startSSE() {
    if (!this.currentTaskId) return;
    this.eventSource = new EventSource(`${this.apiBase}/task-stream/${this.currentTaskId}`);

    this.eventSource.onmessage = (ev) => {
      try {
        const task = JSON.parse(ev.data);
        if (task.type === 'heartbeat') return;

        this._updateProgress(task.progress, task.message, true);

        if (task.status === 'completed') {
          this._stopSP(); this._stopSSE(); this._setLoading(false); this._hideProgress();
          this._showResults(task);
          if (this._taskDoneResolve) this._taskDoneResolve('completed');
        } else if (task.status === 'error') {
          this._stopSP(); this._stopSSE(); this._setLoading(false); this._hideProgress();
          if (this._taskDoneResolve) this._taskDoneResolve('error', task.error);
          else this._showError(task.error || 'Processing error');
        }
      } catch (_) {}
    };

    this.eventSource.onerror = async () => {
      this._stopSSE();
      try {
        if (this.currentTaskId) {
          const r = await fetch(`${this.apiBase}/task-status/${this.currentTaskId}`);
          if (r.ok) {
            const task = await r.json();
            if (task?.status === 'completed') {
              this._stopSP(); this._setLoading(false); this._hideProgress();
              this._showResults(task);
              if (this._taskDoneResolve) this._taskDoneResolve('completed');
              return;
            }
          }
        }
      } catch (_) {}
      if (this._taskDoneResolve) { this._taskDoneResolve('error', 'SSE disconnected'); return; }
      this._showError(this.t('error_processing_failed') + 'SSE disconnected');
      this._setLoading(false);
    };
  }

  _stopSSE() {
    if (this.eventSource) { this.eventSource.close(); this.eventSource = null; }
  }

  /* ── Progress ─────────────────────────────────────────── */
  _updateProgress(pct, msg, fromServer = false) {
    if (fromServer) {
      this._stopSP();
      this.sp.lastServer = pct;
      this.sp.current    = pct;
      this._renderProgress(pct, msg);
      this._updateStage(pct, msg);
      this._startSP();
    } else {
      this._renderProgress(pct, msg);
    }
  }

  _updateStage(pct, msg) {
    const m = (msg || '').toLowerCase();

    // ── 字幕路径（快速）──────────────────────────────────────
    if (m.includes('获取成功') || m.includes('subtitle found') || m.includes('字幕获取')) {
      this.sp.stage = 'subtitle_found';
      this.sp.target = 55;
      this._setModeBadge('subtitle');
    }
    // ── 无字幕 → 音频下载路径（慢）────────────────────────────
    else if (m.includes('未找到字幕') || m.includes('no subtitle') || m.includes('下载视频音频') || m.includes('下载音频')) {
      this.sp.stage = 'downloading';
      this.sp.target = 55;
      this._setModeBadge('whisper');
    }
    else if (m.includes('读取文本') || (m.includes('read') && m.includes('text'))) {
      this.sp.stage = 'parsing';
      this.sp.target = 55;
      this._setModeBadge('whisper');
    }
    else if (m.includes('转换音频') || m.includes('准备转录')) {
      this.sp.stage = 'downloading';
      this.sp.target = 55;
      this._setModeBadge('whisper');
    }
    else if (m.includes('上传') || m.includes('upload')) {
      this.sp.stage = 'preparing';
      this.sp.target = 40;
    }
    // ── 通用字幕检测中 ─────────────────────────────────────────
    else if (m.includes('检测') && (m.includes('字幕') || m.includes('subtitle'))) {
      this.sp.stage = 'subtitle';
      this.sp.target = 40;
    }
    // ── 其他阶段 ───────────────────────────────────────────────
    else if (m.includes('解析') || m.includes('pars'))                     { this.sp.stage = 'parsing';       this.sp.target = 60; }
    else if (m.includes('下载') || m.includes('download'))                 { this.sp.stage = 'downloading';   this.sp.target = 60; }
    else if (m.includes('转录') || m.includes('transcrib') || m.includes('whisper')) { this.sp.stage = 'transcribing';  this.sp.target = 80; }
    else if (m.includes('优化') || m.includes('optimiz'))                  { this.sp.stage = 'optimizing';    this.sp.target = 90; }
    else if (m.includes('摘要') || m.includes('summary'))                  { this.sp.stage = 'summarizing';   this.sp.target = 95; }
    else if (m.includes('完成') || m.includes('complet'))                  { this.sp.stage = 'completed';     this.sp.target = 100; }

    if (pct >= this.sp.target) this.sp.target = Math.min(pct + 8, 99);
  }

  _setModeBadge(mode) {
    if (!this.modeBadge) return;
    if (mode === 'subtitle') {
      this.modeBadge.textContent  = this.t('mode_subtitle');
      this.modeBadge.className    = 'mode-badge subtitle';
      this.modeBadge.style.display = 'inline-block';
      if (this.progressFill) this.progressFill.classList.add('subtitle-mode');
    } else if (mode === 'whisper') {
      this.modeBadge.textContent  = this.t('mode_whisper');
      this.modeBadge.className    = 'mode-badge whisper';
      this.modeBadge.style.display = 'inline-block';
      if (this.progressFill) this.progressFill.classList.remove('subtitle-mode');
    }
  }

  _initSP() {
    this.sp.enabled = false; this.sp.current = 0; this.sp.target = 15;
    this.sp.lastServer = 0;  this.sp.startTime = Date.now(); this.sp.stage = 'preparing';
  }
  _startSP() {
    if (this.sp.interval) clearInterval(this.sp.interval);
    this.sp.enabled   = true;
    this.sp.startTime = this.sp.startTime || Date.now();
    this.sp.interval  = setInterval(() => this._tickSP(), 500);
  }
  _stopSP() {
    if (this.sp.interval) { clearInterval(this.sp.interval); this.sp.interval = null; }
    this.sp.enabled = false;
  }
  _tickSP() {
    if (!this.sp.enabled || this.sp.current >= this.sp.target) return;
    const speeds = { subtitle: .5, parsing: .3, downloading: .18, transcribing: .14, optimizing: .22, summarizing: .28 };
    let inc = speeds[this.sp.stage] || .2;
    const remaining = this.sp.target - this.sp.current;
    if (remaining < 5) inc *= .3;
    const next = Math.min(this.sp.current + inc, this.sp.target);
    if (next > this.sp.current) {
      this.sp.current = next;
      this._renderProgress(next, this._stageMsg());
    }
  }
  _stageMsg() {
    const map = {
      subtitle:       this.t('detecting_subtitles'),
      subtitle_found: this.t('subtitle_found'),
      downloading:    this.t('downloading_video'),
      parsing:        this.t('parsing_video'),
      transcribing:   this.t('transcribing_audio'),
      optimizing:     this.t('optimizing_transcript'),
      summarizing:    this.t('generating_summary'),
      completed:      this.t('completed'),
    };
    return map[this.sp.stage] || this.t('processing');
  }

  _renderProgress(pct, msg) {
    const p = Math.round(pct * 10) / 10;
    this.progressStatus.textContent = `${p}%`;
    this.progressFill.style.width   = `${p}%`;

    // Translate common server messages — more specific checks first
    const m = (msg || '').toLowerCase();
    let label = msg;
    // ── Subtitle path ──────────────────────────────────────────
    if      (m.includes('获取成功') || m.includes('subtitle found'))        label = this.t('subtitle_found');
    else if (m.includes('未找到字幕') || m.includes('no subtitle'))         label = this.t('no_subtitle');
    else if (m.includes('检测') && (m.includes('字幕') || m.includes('subtitle'))) label = this.t('detecting_subtitles');
    // ── Audio / Whisper path ────────────────────────────────────
    else if (m.includes('下载') || m.includes('download'))  label = this.t('downloading_video');
    else if (m.includes('解析') || m.includes('pars'))      label = this.t('parsing_video');
    else if (m.includes('转录') || m.includes('transcrib')) label = this.t('transcribing_audio');
    else if (m.includes('优化') || m.includes('optimiz'))   label = this.t('optimizing_transcript');
    else if (m.includes('摘要') || m.includes('summary'))   label = this.t('generating_summary');
    else if (m.includes('完成') || m.includes('complet'))   label = this.t('completed');
    else if (m.includes('准备') || m.includes('prepar'))    label = this.t('preparing');

    this.progressMessage.textContent = label;
  }

  _showProgress() {
    this.emptyState.style.display    = 'none';
    this.resultsPanel.classList.remove('show');
    this.progressPanel.classList.add('show');
    // Reset mode badge & progress bar color for new task
    if (this.modeBadge) { this.modeBadge.style.display = 'none'; this.modeBadge.className = 'mode-badge'; }
    if (this.progressFill) this.progressFill.classList.remove('subtitle-mode');
  }
  _hideProgress() { this.progressPanel.classList.remove('show'); }

  /* ── Results ──────────────────────────────────────────── */
  /** 与后端 Translator.normalize_lang_code 对齐，用于 Tab 展示判断 */
  _normLangTab(code) {
    if (!code) return '';
    const c = String(code).toLowerCase().trim();
    if (c.startsWith('zh')) return 'zh';
    if (c.length >= 2) return c.slice(0, 2);
    return c;
  }

  _showResults(task) {
    const { script, summary, translation, detected_language: detectedLang, summary_language: summaryLang } = task;
    this._lastTask = task;
    this.scriptContent.innerHTML  = script    ? marked.parse(script)      : '';
    this.summaryContent.innerHTML = summary   ? marked.parse(summary)     : '';

    const d = this._normLangTab(detectedLang);
    const s = this._normLangTab(summaryLang);
    const showTranslation = Boolean(translation) && d && s && d !== s;
    if (showTranslation) {
      this.translationContent.innerHTML = marked.parse(translation);
      this.translationTabBtn.style.display  = 'inline-block';
      this.dlTranslation.style.display      = 'inline-flex';
    } else {
      this.translationTabBtn.style.display  = 'none';
      this.dlTranslation.style.display      = 'none';
    }

    this._resetChat(this.currentTaskId);

    // SRT/VTT disponibles seulement en mode Whisper (segments horodatés)
    const hasSubs = Boolean(task.srt_filename);
    if (this.dlSrt) this.dlSrt.style.display = hasSubs ? 'inline-flex' : 'none';
    if (this.dlVtt) this.dlVtt.style.display = hasSubs ? 'inline-flex' : 'none';

    this.resultsPanel.classList.add('show');
    this._switchTab('script');
    this.resultsPanel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  _hideResults() { this.resultsPanel.classList.remove('show'); }

  /* ── Tabs ─────────────────────────────────────────────── */
  _switchTab(name) {
    this.tabBtns.forEach(b  => b.classList.toggle('active',  b.dataset.tab === name));
    this.tabPanes.forEach(p => p.classList.toggle('active', p.id === `${name}Tab`));
  }

  /* ── Download ─────────────────────────────────────────── */
  async _downloadFile(type) {
    if (!this.currentTaskId) { this._showError(this.t('error_no_download')); return; }
    try {
      const r = await fetch(`${this.apiBase}/task-status/${this.currentTaskId}`);
      if (!r.ok) throw new Error('Failed to get task status');
      const task = await r.json();

      let filename;
      if      (type === 'script')      filename = task.script_path      ? task.script_path.split('/').pop()      : `transcript_${task.safe_title||'x'}_${task.short_id||'x'}.md`;
      else if (type === 'summary')     filename = task.summary_path     ? task.summary_path.split('/').pop()     : `summary_${task.safe_title||'x'}_${task.short_id||'x'}.md`;
      else if (type === 'translation') filename = task.translation_path ? task.translation_path.split('/').pop() : `translation_${task.safe_title||'x'}_${task.short_id||'x'}.md`;
      else if (type === 'srt')         filename = task.srt_filename;
      else if (type === 'vtt')         filename = task.vtt_filename;
      else throw new Error('Unknown type');
      if (!filename) throw new Error(this.t('error_no_download'));

      const a = document.createElement('a');
      a.href = `${this.apiBase}/download/${encodeURIComponent(filename)}`;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    } catch (e) {
      this._showError(this.t('error_download_failed') + e.message);
    }
  }

  /* ── UI helpers ───────────────────────────────────────── */
  _setLoading(on) {
    this.submitBtn.disabled = on;
    this.submitBtn.innerHTML = on
      ? `<span class="spinner"></span> ${this.t('processing')}`
      : `<i class="fas fa-search"></i> <span>${this.t('start_transcription')}</span>`;
    if (this.uploadPickBtn) this.uploadPickBtn.disabled = on;
    if (this.uploadZone) {
      this.uploadZone.style.pointerEvents = on ? 'none' : '';
      this.uploadZone.style.opacity = on ? '0.65' : '';
      this.uploadZone.tabIndex = on ? -1 : 0;
    }
    if (this.fileInput) this.fileInput.disabled = on;
  }

  _showError(msg) {
    this.errorMsg.textContent = msg;
    this.errorBanner.classList.add('show');
    this.errorBanner.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    setTimeout(() => this._hideError(), 6000);
  }
  _hideError() { this.errorBanner.classList.remove('show'); }

  _debounce(fn, ms) {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  }
}

/* ── Boot ──────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  window.vt = new VideoTranscriber();
});

window.addEventListener('beforeunload', () => {
  if (window.vt?.eventSource) window.vt._stopSSE();
});
