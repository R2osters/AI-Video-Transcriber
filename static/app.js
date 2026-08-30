/* ────────────────────────────────────────────────────────────
   AI Video Transcriber · app.js
   UI « Transcriber » — sidebar + 5 vues (nouvelle, progression,
   résultats, historique, paramètres). Logique pipeline conservée.
   ──────────────────────────────────────────────────────────── */

const API = '/api';
const $ = (id) => document.getElementById(id);

/* ── Jeton d'accès local (application installée) ───────────────
   Le serveur écoute sur 127.0.0.1, adresse qu'une page web tierce ouverte
   dans le navigateur peut aussi appeler. Le backend injecte ce jeton dans la
   page qu'il sert lui-même ; une page tierce ne l'a pas, et se voit refuser
   l'accès à la bibliothèque. Absent en mode web : tout fonctionne comme avant. */
const AVT_TOKEN = (typeof window !== 'undefined' && window.__AVT_TOKEN__) || '';

/* Pour <audio>, <video> et les liens de téléchargement, qui ne passent pas par
   fetch() et ne peuvent donc pas porter d'en-tête. Reste local à la machine. */
const withTok = (url) => AVT_TOKEN
  ? `${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(AVT_TOKEN)}`
  : url;

if (AVT_TOKEN) {
  const _fetch = window.fetch.bind(window);
  window.fetch = (input, init = {}) => {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    if (url.startsWith('/api') || url.startsWith(`${location.origin}/api`)) {
      init = { ...init, headers: { ...(init.headers || {}), 'X-AVT-Token': AVT_TOKEN } };
    }
    return _fetch(input, init);
  };
}

/* Étapes du pipeline (vue progression) */
const STEPS = ['download', 'transcribe', 'optimize', 'translate', 'summarize'];

/* Détection de plateforme depuis l'URL */
const PLATFORMS = [
  ['youtube.com', 'YouTube'], ['youtu.be', 'YouTube'],
  ['tiktok.com', 'TikTok'], ['bilibili.com', 'Bilibili'],
  ['podcasts.apple.com', 'Apple Podcasts'], ['soundcloud.com', 'SoundCloud'],
  ['vimeo.com', 'Vimeo'], ['twitch.tv', 'Twitch'], ['x.com', 'X'],
  ['twitter.com', 'X'], ['instagram.com', 'Instagram'], ['facebook.com', 'Facebook'],
];

const LANG_NAMES = {
  fr: 'Français', en: 'English', zh: '中文', es: 'Español', de: 'Deutsch',
  it: 'Italiano', pt: 'Português', ru: 'Русский', ja: '日本語', ko: '한국어', ar: 'العربية',
};

class App {
  constructor() {
    this.taskId = null;
    this.sse = null;
    this.job = null;            // travail en cours {title, platform, startTime, stepStart, mode}
    this.result = null;         // résultat affiché {script, summary, translation, media, meta…}
    this.activePane = 'script';
    this.histGroup = 'day';
    this.models = [];           // [{id, name}]
    this.selectedModel = '';
    this._live = null;          // session micro live en cours
    this._lastLiveTranscript = '';
    this._rec = null;           // MediaRecorder en cours
    this.queue = [];            // fichiers en attente (import multiple)
    this._processingQueue = false;
    this.chatHistory = [];      // [{role, content}] pour le chat courant

    /* Progression simulée entre deux événements serveur */
    this.sp = { current: 0, target: 15, stage: 'preparing', interval: null };

    this._bind();
    this._loadSettings();
    this._renderHistory();
  }

  /* ── Navigation ───────────────────────────────────────── */
  showView(name, { navKey } = {}) {
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    $(`view-${name}`).classList.add('active');
    const key = navKey || name;
    document.querySelectorAll('.nav-item').forEach(b =>
      b.classList.toggle('active', b.dataset.view === key));
    $('sidebar').classList.remove('open');
    if (name === 'history') this._renderHistory();
    if (name === 'settings') this._refreshDisk();
  }

  /* ── Bindings ─────────────────────────────────────────── */
  _bind() {
    document.querySelectorAll('.nav-item').forEach(btn => {
      btn.addEventListener('click', () => this.showView(btn.dataset.view));
    });
    $('burgerBtn').addEventListener('click', () => $('sidebar').classList.toggle('open'));
    document.addEventListener('click', (e) => {
      const sb = $('sidebar');
      if (sb.classList.contains('open') && !sb.contains(e.target) && !$('burgerBtn').contains(e.target)) {
        sb.classList.remove('open');
      }
    });

    /* Nouvelle transcription */
    $('videoForm').addEventListener('submit', (e) => { e.preventDefault(); this._startUrl(); });

    const zone = $('uploadZone'), fileInput = $('fileInput');
    zone.addEventListener('click', () => fileInput.click());
    zone.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileInput.click(); } });
    fileInput.addEventListener('change', () => {
      const files = Array.from(fileInput.files || []);
      fileInput.value = '';
      if (files.length) this._enqueueFiles(files);
    });
    ['dragenter', 'dragover'].forEach(ev => zone.addEventListener(ev, (e) => {
      e.preventDefault(); zone.classList.add('dragover');
    }));
    zone.addEventListener('dragleave', (e) => {
      e.preventDefault();
      if (!zone.contains(e.relatedTarget)) zone.classList.remove('dragover');
    });
    zone.addEventListener('drop', (e) => {
      e.preventDefault(); zone.classList.remove('dragover');
      const files = Array.from((e.dataTransfer && e.dataTransfer.files) || []);
      if (files.length) this._enqueueFiles(files);
    });

    /* Progression */
    $('cancelBtn').addEventListener('click', () => this._cancel());
    $('seeResultsBtn').addEventListener('click', () => {
      if ($('seeResultsBtn').classList.contains('ready')) this.showView('results', { navKey: 'history' });
    });

    /* Résultats */
    $('resTabs').querySelectorAll('button').forEach(btn => {
      btn.addEventListener('click', () => this._switchPane(btn.dataset.pane));
    });
    $('copyBtn').addEventListener('click', () => this._copyActive());
    $('downloadBtn').addEventListener('click', () => this._downloadActive());
    $('downloadVideo').addEventListener('click', () => this._downloadMedia());

    /* Historique */
    $('histSearch').addEventListener('input', () => this._renderHistory());
    $('histGrouping').querySelectorAll('button').forEach(btn => {
      btn.addEventListener('click', () => {
        this.histGroup = btn.dataset.group;
        $('histGrouping').querySelectorAll('button').forEach(b => b.classList.toggle('active', b === btn));
        this._renderHistory();
      });
    });
    document.addEventListener('keydown', (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        this.showView('history');
        $('histSearch').focus();
      }
    });

    /* Espace disque */
    $('freeSpaceBtn').addEventListener('click', () => this._freeSpace());
    $('openLibraryBtn').addEventListener('click', () => {
      if (window.avt && window.avt.openLibrary) window.avt.openLibrary();
    });

    /* Paramètres */
    $('fetchModelsBtn').addEventListener('click', () => this._fetchModels());
    $('eyeBtn').addEventListener('click', () => {
      const inp = $('apiKeyInput');
      inp.type = inp.type === 'password' ? 'text' : 'password';
    });
    $('saveBtn').addEventListener('click', () => {
      this._saveSettings();
      this._toast('Paramètres enregistrés');
    });
    $('resetBtn').addEventListener('click', () => {
      $('modelBaseUrl').value = '';
      $('apiKeyInput').value = '';
      this.models = []; this.selectedModel = '';
      this._renderModels();
      this._setConn(false);
      $('fetchStatus').textContent = '';
      this._saveSettings();
    });
    $('defaultLang').addEventListener('change', () => {
      $('summaryLanguage').value = $('defaultLang').value;
      this._saveSettings();
    });
    $('summaryLanguage').addEventListener('change', () => {
      $('defaultLang').value = $('summaryLanguage').value;
      this._saveSettings();
    });
    $('keepVideo').addEventListener('change', () => this._saveSettings());
    $('whisperModel').addEventListener('change', () => this._saveSettings());

    /* Micro live + enregistrement */
    $('liveBtn').addEventListener('click', () => {
      if (this._live) this._stopLive(); else this._startLive();
    });
    $('liveStopBtn').addEventListener('click', () => this._stopLive());
    $('liveUseBtn').addEventListener('click', () => this._useLiveTranscript());
    $('recordBtn').addEventListener('click', () => this._toggleRecord());
    $('liveMode').addEventListener('change', () => this._saveSettings());

    /* Outils (sidebar) */
    $('toolMulti').addEventListener('click', () => { this.showView('new'); $('fileInput').click(); });
    $('toolRecord').addEventListener('click', () => { this.showView('new'); this._toggleRecord(); });
    $('toolSpeakers').addEventListener('click', () => {
      this.showView('settings');
      this._toast('Locuteurs : à activer côté serveur — ENABLE_DIARIZATION=1 + HF_TOKEN (voir README). Les segments seront alors étiquetés Speaker 1/2/…');
    });
    $('toolChat').addEventListener('click', () => {
      if (this.result) { this.showView('results', { navKey: 'history' }); $('chatInput').focus(); }
      else this._toast('Terminez (ou rouvrez) d’abord une transcription pour discuter avec elle');
    });

    /* Chat */
    $('chatForm').addEventListener('submit', (e) => { e.preventDefault(); this._sendChat(); });

    /* SRT / VTT */
    $('srtBtn').addEventListener('click', () => this._downloadSub('srt'));
    $('vttBtn').addEventListener('click', () => this._downloadSub('vtt'));

    window.addEventListener('beforeunload', () => { this._stopSSE(); if (this._live) this._stopLive(); });
  }

  /* ── Paramètres (localStorage) ────────────────────────── */
  _saveSettings() {
    const s = {
      baseUrl: $('modelBaseUrl').value,
      apiKey: $('apiKeyInput').value,
      model: this.selectedModel,
      models: this.models.slice(0, 100),
      summaryLang: $('summaryLanguage').value,
      keepVideo: $('keepVideo').checked,
      whisperModel: $('whisperModel').value,
      liveMode: $('liveMode').value,
    };
    try { localStorage.setItem('vt_settings', JSON.stringify(s)); } catch (_) {}
  }

  _loadSettings() {
    let s = null;
    try { s = JSON.parse(localStorage.getItem('vt_settings') || 'null'); } catch (_) {}
    if (!s) return;
    if (s.baseUrl) $('modelBaseUrl').value = s.baseUrl;
    if (s.apiKey) $('apiKeyInput').value = s.apiKey;
    if (s.summaryLang) { $('summaryLanguage').value = s.summaryLang; $('defaultLang').value = s.summaryLang; }
    if (typeof s.keepVideo === 'boolean') $('keepVideo').checked = s.keepVideo;
    if (s.whisperModel) $('whisperModel').value = s.whisperModel;
    if (s.liveMode) $('liveMode').value = s.liveMode;
    if (Array.isArray(s.models) && s.models.length) {
      this.models = s.models;
      this.selectedModel = s.model || '';
      this._renderModels();
      this._setConn(true);
    } else if (s.baseUrl && s.apiKey) {
      this.selectedModel = s.model || '';
      setTimeout(() => this._fetchModels(true), 400);
    }
  }

  _setConn(ok) {
    const el = $('connStatus');
    el.classList.toggle('ok', ok);
    el.querySelector('.txt').textContent = ok ? 'connecté' : 'non configuré';
  }

  /* ── Modèles ──────────────────────────────────────────── */
  async _fetchModels(silent = false) {
    const baseUrl = $('modelBaseUrl').value.trim().replace(/\/$/, '');
    const apiKey = $('apiKeyInput').value.trim();
    if (!baseUrl || !apiKey) {
      if (!silent) this._fetchNote('err', 'Base URL et API Key requis');
      return;
    }
    const btn = $('fetchModelsBtn');
    btn.disabled = true;
    if (!silent) this._fetchNote('', 'Récupération…');
    try {
      const fd = new FormData();
      fd.append('base_url', baseUrl);
      fd.append('api_key', apiKey);
      const resp = await fetch(`${API}/models`, { method: 'POST', body: fd });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      const list = data.data || data.models || [];
      this.models = list.map(m => ({ id: m.id, name: m.name || m.id }));
      if (this.selectedModel && !this.models.some(m => m.id === this.selectedModel)) {
        this.selectedModel = '';
      }
      this._renderModels();
      this._setConn(true);
      this._fetchNote('ok', `${this.models.length} modèles récupérés`);
      this._saveSettings();
    } catch (e) {
      this._fetchNote('err', `Échec : ${e.message}`);
      this._setConn(false);
    } finally {
      btn.disabled = false;
    }
  }

  _fetchNote(cls, msg) {
    const el = $('fetchStatus');
    el.className = 'fetch-note' + (cls ? ` ${cls}` : '');
    el.textContent = msg;
  }

  _renderModels() {
    const wrap = $('modelList');
    wrap.innerHTML = '';
    $('modelCount').textContent = this.models.length ? `${this.models.length} découverts` : '';

    const mkRow = (id, name, desc, isDefault) => {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'model-row' + ((id === this.selectedModel) ? ' selected' : '');
      row.innerHTML = `
        <span class="radio"></span>
        <span style="flex:1;min-width:0">
          <span class="m-name" style="display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"></span>
          <span class="m-desc" style="display:block"></span>
        </span>
        ${isDefault ? '<span class="m-badge">DÉFAUT</span>' : ''}`;
      row.querySelector('.m-name').textContent = name;
      row.querySelector('.m-desc').textContent = desc;
      row.addEventListener('click', () => {
        this.selectedModel = id;
        wrap.querySelectorAll('.model-row').forEach(r => r.classList.remove('selected'));
        row.classList.add('selected');
        this._saveSettings();
      });
      return row;
    };

    wrap.appendChild(mkRow('', 'défaut serveur', 'Modèle configuré côté serveur', true));
    this.models.forEach(m => wrap.appendChild(mkRow(m.id, m.id, m.name !== m.id ? m.name : '', false)));

    if (!this.models.length) {
      const empty = document.createElement('div');
      empty.className = 'model-empty';
      empty.textContent = 'Renseignez l’endpoint puis récupérez les modèles.';
      wrap.appendChild(empty);
    }
  }

  /* ── Lancement ────────────────────────────────────────── */
  _platformOf(url) {
    try {
      const host = new URL(url).hostname.replace(/^www\./, '');
      const hit = PLATFORMS.find(([h]) => host === h || host.endsWith('.' + h));
      return hit ? hit[1] : host;
    } catch (_) { return 'lien'; }
  }

  async _startUrl() {
    const url = $('videoUrl').value.trim();
    if (!url) { this._toast('Entrez une URL de vidéo valide'); return; }

    const fd = new FormData();
    fd.append('url', url);
    fd.append('summary_language', $('summaryLanguage').value);
    fd.append('download_video', $('keepVideo').checked ? '1' : '0');
    this._appendCreds(fd);

    this._launch(fd, { title: url, platform: this._platformOf(url), source: 'url' });
  }

  async _startFile(file) {
    const allowed = new Set(['.txt', '.mp3', '.mp4', '.m4a', '.wav', '.webm', '.mkv', '.ogg', '.flac',
      '.aac', '.opus', '.wma', '.aiff', '.mov', '.avi']);
    const parts = (file.name || '').split('.');
    const ext = parts.length > 1 ? ('.' + parts.pop().toLowerCase()) : '';
    if (!allowed.has(ext)) { this._toast(`Type non pris en charge : ${file.name}`); this._queueContinue(); return; }
    if (!file.size) { this._toast(`Fichier vide : ${file.name}`); this._queueContinue(); return; }
    if (file.size > 200 * 1024 * 1024) { this._toast(`Au-delà de 200 Mo : ${file.name}`); this._queueContinue(); return; }

    const fd = new FormData();
    fd.append('file', file, file.name);
    fd.append('summary_language', $('summaryLanguage').value);
    this._appendCreds(fd);

    this._launch(fd, { title: file.name, platform: 'upload', source: 'upload', size: file.size });
  }

  _appendCreds(fd) {
    const apiKey = $('apiKeyInput').value.trim();
    const baseUrl = $('modelBaseUrl').value.trim().replace(/\/$/, '');
    if (apiKey) fd.append('api_key', apiKey);
    if (baseUrl) fd.append('model_base_url', baseUrl);
    if (this.selectedModel) fd.append('model_id', this.selectedModel);
    const whisperM = $('whisperModel').value;
    if (whisperM) fd.append('whisper_model', whisperM);
  }

  /* ── Import multiple (file d'attente) ─────────────────── */
  _enqueueFiles(files) {
    const arr = Array.from(files || []);
    if (!arr.length) return;
    this.queue.push(...arr);
    this._updateQueueNote();
    if (!this._processingQueue) this._nextInQueue();
  }

  _nextInQueue() {
    const f = this.queue.shift();
    this._updateQueueNote();
    if (!f) { this._processingQueue = false; return; }
    this._processingQueue = true;
    this._startFile(f);
  }

  _queueContinue() {
    if (this.queue.length) {
      this._toast(`Fichier suivant de la file (${this.queue.length} restant${this.queue.length > 1 ? 's' : ''})…`);
      setTimeout(() => this._nextInQueue(), 600);
    } else {
      this._processingQueue = false;
      this._updateQueueNote();
    }
  }

  _updateQueueNote() {
    const el = $('queueNote');
    if (!el) return;
    if (this.queue.length) {
      el.hidden = false;
      el.textContent = `File d'attente : ${this.queue.length} fichier${this.queue.length > 1 ? 's' : ''} en attente`;
    } else {
      el.hidden = true;
      el.textContent = '';
    }
  }

  /* ── Enregistrement micro (local, sans clé) ───────────── */
  async _toggleRecord() {
    if (this._rec) { try { this._rec.recorder.stop(); } catch (_) {} return; }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch (e) {
      this._toast('Accès au micro refusé : ' + e.message);
      return;
    }
    const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus'
      : (MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : '');
    const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    const chunks = [];
    const startAt = Date.now();
    const rec = { recorder, stream, timer: null };
    this._rec = rec;

    recorder.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
    recorder.onstop = () => {
      clearInterval(rec.timer);
      stream.getTracks().forEach(t => t.stop());
      this._rec = null;
      $('recordBtn').classList.remove('recording');
      $('recordBtnLabel').textContent = 'Enregistrer';
      if (!chunks.length) { this._toast('Enregistrement vide'); return; }
      const stamp = new Date().toISOString().slice(0, 16).replace(/[T:]/g, '-');
      const file = new File(chunks, `enregistrement-${stamp}.webm`, { type: mime || 'audio/webm' });
      this._enqueueFiles([file]);
    };

    recorder.start(500);
    $('recordBtn').classList.add('recording');
    const fmt = (ms) => {
      const s = Math.floor(ms / 1000);
      return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
    };
    rec.timer = setInterval(() => {
      $('recordBtnLabel').textContent = `Arrêter (${fmt(Date.now() - startAt)})`;
    }, 500);
    $('recordBtnLabel').textContent = 'Arrêter (0:00)';
  }

  /* ── Chat sur transcription ───────────────────────────── */
  async _sendChat() {
    const input = $('chatInput');
    const q = input.value.trim();
    if (!q) return;
    if (!this.result || !this.result.id) { this._toast('Aucune transcription active'); return; }
    input.value = '';

    const log = $('chatLog');
    const addMsg = (cls, text) => {
      const div = document.createElement('div');
      div.className = `chat-msg ${cls}`;
      div.textContent = text;
      log.appendChild(div);
      log.scrollTop = log.scrollHeight;
      return div;
    };
    addMsg('q', q);
    const pending = addMsg('a thinking', 'Recherche…');

    try {
      const fd = new FormData();
      fd.append('task_id', this.result.id);
      fd.append('question', q);
      fd.append('history', JSON.stringify(this.chatHistory.slice(-10)));
      const apiKey = $('apiKeyInput').value.trim();
      const baseUrl = $('modelBaseUrl').value.trim().replace(/\/$/, '');
      if (apiKey) fd.append('api_key', apiKey);
      if (baseUrl) fd.append('model_base_url', baseUrl);
      if (this.selectedModel) fd.append('model_id', this.selectedModel);

      const resp = await fetch(`${API}/chat`, { method: 'POST', body: fd });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(typeof err.detail === 'string' ? err.detail : `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      pending.className = 'chat-msg a';
      pending.textContent = data.answer || '(réponse vide)';
      this.chatHistory.push({ role: 'user', content: q }, { role: 'assistant', content: data.answer || '' });
    } catch (e) {
      pending.className = 'chat-msg a';
      pending.textContent = 'Erreur : ' + e.message +
        (e.message.includes('404') || e.message.includes('存在') ? ' (transcription absente du serveur — relancez-la)' : '');
    }
  }

  _downloadSub(kind) {
    const file = this.result && this.result[kind];
    if (!file) return;
    const a = document.createElement('a');
    // Entrée issue de la bibliothèque : on adresse le produit par son type,
    // sans dépendre du nom de fichier d'origine.
    a.href = withTok(this.result.assetBase
      ? `${this.result.assetBase}/${kind}?download=true`
      : `${API}/download/${encodeURIComponent(file)}`);
    a.download = this.result.assetBase ? `${this.result.title || 'sous-titres'}.${kind}` : file;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  /* ── Micro live (transcription temps réel) ────────────── */
  async _startLive() {
    const mode = $('liveMode').value === 'openai' ? 'openai' : 'local';
    const apiKey = $('apiKeyInput').value.trim();
    if (mode === 'openai' && !apiKey) {
      this._toast('Le mode OpenAI Realtime nécessite une clé API OpenAI (Paramètres) — ou passez en mode Local, gratuit et sans clé');
      return;
    }

    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
      });
    } catch (e) {
      this._toast('Accès au micro refusé : ' + e.message);
      return;
    }

    const live = { mode, stream, ctx: null, node: null, ws: null, ready: false, utterances: [], delta: '', partial: '', stopping: false };
    this._live = live;
    $('livePanel').hidden = false;
    $('livePanel').classList.remove('stopped');
    $('liveText').textContent = '';
    $('liveUseBtn').disabled = true;
    $('liveStatus').textContent = mode === 'local' ? 'Connexion… (local, sans clé)' : 'Connexion… (OpenAI Realtime)';
    $('liveBtn').classList.add('recording');
    $('liveBtnLabel').textContent = 'Arrêter';

    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      live.ctx = ctx;
      await ctx.audioWorklet.addModule('/static/pcm-worklet.js');
      const src = ctx.createMediaStreamSource(stream);
      const node = new AudioWorkletNode(ctx, 'pcm-worklet');
      live.node = node;
      src.connect(node); // pas de connexion à destination → pas d'écho

      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      const wsPath = mode === 'openai' ? '/ws/live-transcribe' : '/ws/live-local';
      const ws = new WebSocket(withTok(`${proto}://${location.host}${wsPath}`));
      live.ws = ws;

      ws.onopen = () => {
        const start = mode === 'openai'
          ? { type: 'start', api_key: apiKey }
          : { type: 'start', whisper_model: $('whisperModel').value };
        ws.send(JSON.stringify(start));
      };
      node.port.onmessage = (e) => {
        if (live.ready && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'audio', audio: this._i16ToB64(e.data) }));
        }
      };
      ws.onmessage = (e) => {
        let msg;
        try { msg = JSON.parse(e.data); } catch (_) { return; }
        if (msg.type === 'ready') {
          live.ready = true;
          $('liveStatus').textContent = mode === 'local'
            ? 'À l’écoute… (transcription locale, rafraîchie toutes les ~3 s)'
            : 'À l’écoute…';
        } else if (msg.type === 'delta') {
          live.delta += msg.text || '';
          this._renderLive(live);
        } else if (msg.type === 'partial') {
          // mode local : le serveur renvoie le texte complet à chaque passe
          live.partial = (msg.text || '').trim();
          this._renderLive(live);
        } else if (msg.type === 'utterance') {
          const text = (msg.text || '').trim();
          if (mode === 'local') {
            live.partial = text;
            this._renderLive(live);
            if (live.stopping) this._finalizeLive(live);  // résultat final après stop
          } else {
            live.delta = '';
            if (text) live.utterances.push(text);
            this._renderLive(live);
            if (live.utterances.length) $('liveUseBtn').disabled = false;
          }
        } else if (msg.type === 'error') {
          this._toast('Erreur transcription live : ' + (msg.message || ''));
          this._finalizeLive(live);
        }
      };
      ws.onclose = () => { if (this._live === live || live.stopping) this._finalizeLive(live); };
    } catch (e) {
      this._toast('Erreur transcription live : ' + e.message);
      this._finalizeLive(live);
    }
  }

  _stopLive() {
    const live = this._live;
    if (!live) return;
    if (live.mode === 'local' && live.ws && live.ws.readyState === WebSocket.OPEN && live.ready) {
      // mode local : demande la transcription finale, la fermeture se fait à sa réception
      if (live.stopping) { this._finalizeLive(live); return; }  // 2e clic = forcer
      live.stopping = true;
      try { live.stream.getTracks().forEach(t => t.stop()); } catch (_) {}
      try { live.ws.send(JSON.stringify({ type: 'stop' })); } catch (_) { this._finalizeLive(live); return; }
      $('liveStatus').textContent = 'Finalisation de la transcription…';
      // filet de sécurité si le serveur ne répond pas
      setTimeout(() => { if (this._live === live) this._finalizeLive(live); }, 90000);
      return;
    }
    try { if (live.ws && live.ws.readyState === WebSocket.OPEN) live.ws.send(JSON.stringify({ type: 'stop' })); } catch (_) {}
    this._finalizeLive(live);
  }

  _finalizeLive(live) {
    if (!live) return;
    if (this._live === live) this._live = null;
    try { if (live.ws) { live.ws.onclose = null; live.ws.close(); } } catch (_) {}
    try { live.stream.getTracks().forEach(t => t.stop()); } catch (_) {}
    try { if (live.ctx && live.ctx.state !== 'closed') live.ctx.close(); } catch (_) {}
    $('liveBtn').classList.remove('recording');
    $('liveBtnLabel').textContent = 'Micro live';
    $('livePanel').classList.add('stopped');
    $('liveStatus').textContent = 'Arrêté';
    // conserve la transcription pour relecture / envoi au pipeline
    this._lastLiveTranscript = live.mode === 'local'
      ? (live.partial || '')
      : live.utterances.join('\n\n');
    $('liveUseBtn').disabled = !this._lastLiveTranscript.trim();
  }

  _renderLive(live) {
    // Rendu via DOM (pas d'innerHTML) — le transcript vient d'une source externe
    const box = $('liveText');
    box.textContent = '';
    if (live.mode === 'local') {
      if (live.partial) box.appendChild(document.createTextNode(live.partial));
      else {
        const span = document.createElement('span');
        span.className = 'live-delta';
        span.textContent = 'Parlez — le texte apparaît après quelques secondes…';
        box.appendChild(span);
      }
      box.scrollTop = box.scrollHeight;
      return;
    }
    live.utterances.forEach((u, i) => {
      if (i) box.appendChild(document.createTextNode('\n\n'));
      box.appendChild(document.createTextNode(u));
    });
    if (live.delta) {
      if (live.utterances.length) box.appendChild(document.createTextNode('\n\n'));
      const span = document.createElement('span');
      span.className = 'live-delta';
      span.textContent = live.delta;
      box.appendChild(span);
    }
    box.scrollTop = box.scrollHeight;
  }

  _useLiveTranscript() {
    if (this._live) this._stopLive();
    const text = (this._lastLiveTranscript || '').trim();
    if (!text) { this._toast('Aucune parole capturée'); return; }
    // Réinjecte dans le pipeline upload .txt existant (optimisation → traduction → résumé)
    const stamp = new Date().toISOString().slice(0, 16).replace(/[T:]/g, '-');
    const file = new File([text], `micro-live-${stamp}.txt`, { type: 'text/plain' });
    $('livePanel').hidden = true;
    this._startFile(file);
  }

  _i16ToB64(i16) {
    const bytes = new Uint8Array(i16.buffer, i16.byteOffset, i16.byteLength);
    let bin = '';
    const CHUNK = 0x8000;
    for (let i = 0; i < bytes.length; i += CHUNK) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
    }
    return btoa(bin);
  }

  async _launch(fd, jobInfo) {
    $('submitBtn').disabled = true;
    $('submitBtn').innerHTML = '<span class="spinner"></span> Envoi…';
    try {
      const resp = await fetch(`${API}/process-video`, { method: 'POST', body: fd });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        const d = err.detail;
        const msg = typeof d === 'string' ? d
          : (Array.isArray(d) && d[0] && (d[0].msg || d[0].message)) || `HTTP ${resp.status}`;
        throw new Error(msg);
      }
      const data = await resp.json();
      this.taskId = data.task_id;
      this.job = {
        ...jobInfo,
        startTime: Date.now(),
        stepIndex: -1,
        stepStart: Date.now(),
        mode: null,
        lastMsg: '',
      };
      this._saveSettings();
      this._resetProgressView();
      this.showView('progress', { navKey: 'new' });
      this._initSP();
      this._startSSE();
    } catch (e) {
      this._toast(`Échec du lancement : ${e.message}`);
      this._queueContinue();
    } finally {
      $('submitBtn').disabled = false;
      $('submitBtn').textContent = 'Transcrire';
    }
  }

  /* ── Vue progression ──────────────────────────────────── */
  _resetProgressView() {
    $('progMeta').textContent = this.job.platform === 'upload'
      ? `${this.job.title} · ${this._fmtSize(this.job.size)}`
      : `${this._shortUrl(this.job.title)} · ${this.job.platform}`;
    $('modeBadge').style.display = 'none';
    $('modeNote').textContent = '';
    $('streamBody').innerHTML = '';
    $('streamLabel').textContent = 'stream · SSE';
    $('streamFootLeft').textContent = '0%';
    $('streamFootRight').textContent = '';
    $('stepSummarySub').textContent = `Multilingue · ${LANG_NAMES[$('summaryLanguage').value] || $('summaryLanguage').value}`;
    const btn = $('seeResultsBtn');
    btn.classList.remove('ready');
    btn.disabled = true;
    document.querySelectorAll('#pipeline .step').forEach(s => {
      s.className = 'step pending';
      s.querySelector('.step-time').textContent = '';
      s.querySelector('.fill').style.width = '0';
    });
  }

  _shortUrl(u) {
    try { const p = new URL(u); return p.hostname.replace(/^www\./, '') + p.pathname.slice(0, 24); }
    catch (_) { return u.slice(0, 40); }
  }

  _setStep(idx) {
    if (!this.job || idx <= this.job.stepIndex) return;
    const steps = document.querySelectorAll('#pipeline .step');
    /* Clore les étapes précédentes */
    for (let i = 0; i <= idx - 1; i++) {
      if (!steps[i].classList.contains('done')) {
        steps[i].className = 'step done';
        const t = steps[i].querySelector('.step-time');
        if (!t.textContent) t.textContent = this._fmtElapsed(Date.now() - this.job.stepStart);
      }
    }
    if (idx < steps.length) {
      steps[idx].className = 'step current';
      this.job.stepStart = Date.now();
    }
    this.job.stepIndex = idx;
  }

  _finishAllSteps() {
    document.querySelectorAll('#pipeline .step').forEach(s => {
      if (!s.classList.contains('done')) {
        s.className = 'step done';
        const t = s.querySelector('.step-time');
        if (!t.textContent) t.textContent = '—';
      }
    });
  }

  _fmtElapsed(ms) {
    const s = Math.max(1, Math.round(ms / 1000));
    const m = Math.floor(s / 60);
    return `${String(m).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
  }

  _setMode(mode) {
    if (!this.job || this.job.mode === mode) return;
    this.job.mode = mode;
    const badge = $('modeBadge');
    badge.style.display = 'inline-block';
    if (mode === 'subtitle') {
      badge.textContent = 'SOUS-TITRES NATIFS';
      $('modeNote').textContent = 'whisper évité';
    } else {
      badge.textContent = 'WHISPER';
      $('modeNote').textContent = 'faster-whisper';
    }
  }

  _logStream(msg) {
    if (!msg || msg === this.job?.lastMsg) return;
    if (this.job) this.job.lastMsg = msg;
    const body = $('streamBody');
    const old = body.querySelector('.cursor');
    if (old) old.remove();
    const line = document.createElement('div');
    const ts = this._fmtElapsed(Date.now() - (this.job?.startTime || Date.now()));
    line.innerHTML = `<span class="ts">[${ts}]</span> ${this._esc(msg)}<span class="cursor"></span>`;
    body.appendChild(line);
    body.scrollTop = body.scrollHeight;
  }

  _esc(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  /* ── Traduction FR des messages serveur ───────────────── */
  _frMessage(m) {
    const s = (m || '').toLowerCase();
    if (s.includes('获取成功') || s.includes('subtitle found')) return 'Sous-titres trouvés — traitement du texte…';
    if (s.includes('未找到字幕') || s.includes('no subtitle')) return 'Pas de sous-titres — téléchargement audio…';
    if (s.includes('检测') && (s.includes('字幕') || s.includes('subtitle'))) return 'Détection des sous-titres…';
    if (s.includes('原视频') || s.includes('original video')) return 'Préparation de la vidéo originale…';
    if (s.includes('翻译') || s.includes('translat')) return 'Traduction en cours…';
    if (s.includes('转录') || s.includes('transcrib') || s.includes('whisper')) return 'Transcription audio…';
    if (s.includes('优化') || s.includes('optimiz')) return 'Optimisation du texte…';
    if (s.includes('摘要') || s.includes('summary') || s.includes('summar')) return 'Génération du résumé…';
    if (s.includes('下载') || s.includes('download')) return 'Téléchargement…';
    if (s.includes('解析') || s.includes('pars')) return 'Analyse de la vidéo…';
    if (s.includes('上传') || s.includes('upload')) return 'Réception du fichier…';
    if (s.includes('读取文本') || (s.includes('read') && s.includes('text'))) return 'Lecture du texte…';
    if (s.includes('转换音频') || s.includes('准备转录')) return 'Préparation audio…';
    if (s.includes('完成') || s.includes('complet')) return 'Terminé';
    if (s.includes('准备') || s.includes('prepar')) return 'Préparation…';
    return m;
  }

  _stepFromMessage(m) {
    const s = (m || '').toLowerCase();
    if (s.includes('翻译') || s.includes('translat')) return 3;
    if (s.includes('摘要') || s.includes('summar')) return 4;
    if (s.includes('优化') || s.includes('optimiz')) return 2;
    if (s.includes('获取成功') || s.includes('subtitle found')) return 1;
    if (s.includes('转录') || s.includes('transcrib') || s.includes('whisper')) return 1;
    if (s.includes('原视频') || s.includes('original video')) return 4;
    if (s.includes('下载') || s.includes('download') || s.includes('解析') || s.includes('pars')
      || s.includes('检测') || s.includes('subtitle') || s.includes('上传') || s.includes('upload')
      || s.includes('准备') || s.includes('prepar')) return 0;
    return null;
  }

  /* ── SSE ──────────────────────────────────────────────── */
  _startSSE() {
    if (!this.taskId) return;
    this.sse = new EventSource(`${API}/task-stream/${this.taskId}`);

    this.sse.onmessage = (ev) => {
      let task;
      try { task = JSON.parse(ev.data); } catch (_) { return; }
      if (task.type === 'heartbeat') return;

      this._onServerProgress(task.progress, task.message);

      if (task.status === 'completed') {
        this._stopSP(); this._stopSSE();
        this._finishAllSteps();
        this._onCompleted(task);
      } else if (task.status === 'error') {
        this._stopSP(); this._stopSSE();
        this._toast(`Échec : ${task.error || 'erreur de traitement'}`);
        this.showView('new');
        this._queueContinue();
      }
    };

    this.sse.onerror = async () => {
      this._stopSSE();
      try {
        if (this.taskId) {
          const r = await fetch(`${API}/task-status/${this.taskId}`);
          if (r.ok) {
            const task = await r.json();
            if (task?.status === 'completed') {
              this._stopSP();
              this._finishAllSteps();
              this._onCompleted(task);
              return;
            }
          }
        }
      } catch (_) {}
      this._stopSP();
      this._toast('Connexion au serveur interrompue (SSE)');
      this.showView('new');
    };
  }

  _stopSSE() { if (this.sse) { this.sse.close(); this.sse = null; } }

  _onServerProgress(pct, msg) {
    this._stopSP();
    this.sp.current = pct;
    this.sp.target = Math.min(pct + 8, 99);
    this._detectStage(msg);
    this._renderProgress(pct);
    const fr = this._frMessage(msg);
    this._logStream(fr);

    const step = this._stepFromMessage(msg);
    if (step !== null) this._setStep(step);

    const s = (msg || '').toLowerCase();
    if (s.includes('获取成功') || s.includes('subtitle found')) this._setMode('subtitle');
    if (s.includes('未找到字幕') || s.includes('no subtitle') || s.includes('whisper')) this._setMode('whisper');

    this._startSP();
  }

  /* Progression simulée entre deux événements serveur */
  _detectStage(msg) {
    const m = (msg || '').toLowerCase();
    if (m.includes('转录') || m.includes('transcrib')) this.sp.stage = 'transcribing';
    else if (m.includes('优化') || m.includes('optimiz')) this.sp.stage = 'optimizing';
    else if (m.includes('摘要') || m.includes('summar')) this.sp.stage = 'summarizing';
    else if (m.includes('翻译') || m.includes('translat')) this.sp.stage = 'translating';
    else if (m.includes('下载') || m.includes('download')) this.sp.stage = 'downloading';
    else this.sp.stage = 'preparing';
  }

  _initSP() { this.sp = { current: 0, target: 15, stage: 'preparing', interval: null }; }
  _startSP() {
    if (this.sp.interval) clearInterval(this.sp.interval);
    this.sp.interval = setInterval(() => {
      if (this.sp.current >= this.sp.target) return;
      const speeds = { downloading: .18, transcribing: .14, optimizing: .22, translating: .2, summarizing: .28, preparing: .2 };
      let inc = speeds[this.sp.stage] || .2;
      if (this.sp.target - this.sp.current < 5) inc *= .3;
      this.sp.current = Math.min(this.sp.current + inc, this.sp.target);
      this._renderProgress(this.sp.current);
    }, 500);
  }
  _stopSP() { if (this.sp.interval) { clearInterval(this.sp.interval); this.sp.interval = null; } }

  _renderProgress(pct) {
    const p = Math.round(pct);
    $('streamFootLeft').textContent = `${p}%`;
    $('streamFootRight').textContent = this.job ? this._fmtElapsed(Date.now() - this.job.startTime) + ' écoulées' : '';
    const cur = document.querySelector('#pipeline .step.current');
    if (cur) {
      cur.querySelector('.step-time').textContent = `${p}%`;
      cur.querySelector('.fill').style.width = `${p}%`;
    }
  }

  async _cancel() {
    this._stopSP(); this._stopSSE();
    if (this.taskId) {
      try { await fetch(`${API}/task/${this.taskId}`, { method: 'DELETE' }); } catch (_) {}
    }
    this.taskId = null;
    this.job = null;
    this.showView('new');
  }

  /* ── Fin de tâche ─────────────────────────────────────── */
  _onCompleted(task) {
    const elapsed = this.job ? Date.now() - this.job.startTime : 0;
    const entry = this._buildEntry(task, elapsed);
    // Le backend a déjà archivé la tâche : rien à écrire côté navigateur,
    // on rafraîchit juste la liste pour que la nouvelle entrée y figure.
    this._renderHistory();

    const btn = $('seeResultsBtn');
    btn.classList.add('ready');
    btn.disabled = false;

    this._renderResult(entry);
    this.showView('results', { navKey: 'history' });
    this._queueContinue();
  }

  _buildEntry(task, elapsedMs) {
    const cap = (t) => (t && t.length > 200000) ? t.slice(0, 200000) + '\n\n…(tronqué)' : (t || '');
    const detected = this._normLang(task.detected_language);
    const target = this._normLang(task.summary_language);
    const langPair = detected && target && detected !== target
      ? `${detected.toUpperCase()}→${target.toUpperCase()}`
      : (detected ? detected.toUpperCase() : '—');
    return {
      id: task.task_id || this.taskId || String(Date.now()),
      date: new Date().toISOString(),
      title: task.video_title || this.job?.title || 'Sans titre',
      platform: this.job?.platform || 'upload',
      langPair,
      model: this.selectedModel || 'défaut serveur',
      source: this.job?.mode === 'subtitle' ? 'sous-titres natifs'
        : this.job?.mode === 'whisper' ? 'whisper' : '—',
      elapsedMs,
      noSpeech: Boolean(task.no_speech),
      script: cap(task.script),
      summary: cap(task.summary),
      translation: cap(task.translation),
      showTranslation: Boolean(task.translation) && detected && target && detected !== target && !task.no_speech,
      media: task.media_filename ? {
        file: task.media_filename,
        name: task.media_download_name || task.media_filename,
        kind: task.media_kind || 'video',
        size: task.media_size_bytes || 0,
      } : null,
      srt: task.srt_filename || null,
      vtt: task.vtt_filename || null,
    };
  }

  _normLang(code) {
    if (!code) return '';
    const c = String(code).toLowerCase().trim();
    if (c.startsWith('zh')) return 'zh';
    return c.slice(0, 2);
  }

  /* ── Vue résultats ────────────────────────────────────── */
  _renderResult(entry) {
    this.result = entry;
    $('resTitle').textContent = entry.title;
    $('resTitle').title = entry.title;

    const date = new Date(entry.date);
    const dateStr = date.toLocaleDateString('fr-FR', { day: 'numeric', month: 'long', year: 'numeric' });
    $('resMeta').textContent = [entry.platform, entry.langPair, entry.model, dateStr]
      .filter(Boolean).join(' · ');

    $('pane-script').innerHTML = entry.script ? marked.parse(entry.script) : '<p style="color:var(--faint)">—</p>';
    $('pane-summary').innerHTML = entry.summary ? marked.parse(entry.summary) : '<p style="color:var(--faint)">—</p>';
    $('pane-translation').innerHTML = entry.translation ? marked.parse(entry.translation) : '';

    $('noSpeechBanner').classList.toggle('show', entry.noSpeech);
    $('tabSummary').style.display = entry.noSpeech ? 'none' : '';
    $('tabTranslation').style.display = entry.showTranslation ? '' : 'none';

    /* Sous-titres SRT/VTT (générés sur le chemin Whisper) */
    $('srtBtn').style.display = entry.srt ? '' : 'none';
    $('vttBtn').style.display = entry.vtt ? '' : 'none';

    /* Chat : repart de zéro pour chaque résultat affiché */
    this.chatHistory = [];
    $('chatLog').textContent = '';

    /* Média */
    const block = $('mediaBlock'), frame = $('mediaFrame');
    frame.innerHTML = '';
    if (entry.media) {
      const isAudio = entry.media.kind === 'audio';
      const el = document.createElement(isAudio ? 'audio' : 'video');
      el.src = withTok(entry.media.url || `${API}/media/${encodeURIComponent(entry.media.file)}`);
      el.controls = true;
      el.preload = 'metadata';
      el.addEventListener('error', () => { block.style.display = 'none'; });
      frame.appendChild(el);
      $('mediaLabel').textContent = isAudio ? 'Audio original' : 'Vidéo originale';
      const bits = [];
      const ext = entry.media.file.split('.').pop();
      if (ext) bits.push(ext.toUpperCase());
      const sz = this._fmtSize(entry.media.size);
      if (sz) bits.push(sz);
      $('mediaMeta').textContent = bits.join(' · ');
      block.style.display = 'block';
    } else {
      block.style.display = 'none';
    }

    /* Carte pipeline */
    $('kvSource').textContent = entry.source;
    $('kvLang').textContent = entry.langPair.replace('→', ' → ');
    $('kvModel').textContent = entry.model;
    const words = entry.script ? entry.script.trim().split(/\s+/).length : 0;
    $('kvWords').textContent = words ? words.toLocaleString('fr-FR') : '—';
    $('kvDuration').textContent = entry.elapsedMs ? this._fmtDuration(entry.elapsedMs) : '—';

    this._switchPane('script');
  }

  _fmtDuration(ms) {
    const s = Math.round(ms / 1000);
    if (s < 60) return `${s} s`;
    return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')} s`;
  }

  _switchPane(name) {
    this.activePane = name;
    $('resTabs').querySelectorAll('button').forEach(b =>
      b.classList.toggle('active', b.dataset.pane === name));
    document.querySelectorAll('.pane').forEach(p =>
      p.classList.toggle('active', p.id === `pane-${name}`));
  }

  _activeText() {
    if (!this.result) return '';
    return this.result[this.activePane === 'script' ? 'script'
      : this.activePane === 'summary' ? 'summary' : 'translation'] || '';
  }

  async _copyActive() {
    const text = this._activeText();
    if (!text) { this._toast('Rien à copier'); return; }
    try {
      await navigator.clipboard.writeText(text);
      this._toast('Copié dans le presse-papiers');
    } catch (_) { this._toast('Copie impossible'); }
  }

  _downloadActive() {
    const text = this._activeText();
    if (!text) { this._toast('Rien à télécharger'); return; }
    const label = { script: 'transcription', summary: 'resume', translation: 'traduction' }[this.activePane];
    const safe = (this.result.title || 'transcriber').replace(/[^\w\dàâäéèêëïîôöùûüç-]+/gi, '_').slice(0, 60);
    const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${label}_${safe}.md`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  }

  _downloadMedia() {
    const m = this.result?.media;
    if (!m) { this._toast('Aucun média disponible'); return; }
    const a = document.createElement('a');
    a.href = withTok(m.url
      ? `${m.url}?download=true`
      : `${API}/download/${encodeURIComponent(m.file)}?name=${encodeURIComponent(m.name)}`);
    a.download = m.name;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  _fmtSize(bytes) {
    if (!bytes || bytes <= 0) return '';
    const units = ['o', 'Ko', 'Mo', 'Go'];
    let v = bytes, i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return `${v < 10 && i > 0 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
  }

  /* ── Bibliothèque : accès API ─────────────────────────── */
  async _lib(path, opts) {
    const r = await fetch(`${API}/library${path}`, opts);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  }

  _libJson(method, body) {
    return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  }

  /* Enregistrement de la bibliothèque → forme interne attendue par _renderResult */
  _entryFromApi(rec) {
    const src = (rec.lang_src || '').toUpperCase();
    const dst = (rec.lang_dst || '').toUpperCase();
    const assets = rec.assets || {};
    const sources = rec.asset_sources || {};
    const assetBase = `${API}/library/${encodeURIComponent(rec.id)}/asset`;
    const mediaKind = assets.video ? 'video' : (assets.audio ? 'audio' : null);
    return {
      id: rec.id,
      date: new Date((rec.created_at || 0) * 1000).toISOString(),
      title: rec.title || 'Sans titre',
      platform: rec.platform || '',
      langPair: (src && dst && src !== dst) ? `${src}→${dst}` : (src || '—'),
      model: rec.model || '',
      elapsedMs: rec.elapsed_ms || 0,
      favorite: Boolean(rec.favorite),
      noSpeech: Boolean(rec.no_speech),
      script: rec.script || '',
      summary: rec.summary || '',
      translation: rec.translation || '',
      showTranslation: Boolean(rec.translation) && src && dst && src !== dst && !rec.no_speech,
      assetBase,
      media: mediaKind ? {
        file: sources[mediaKind] || '',
        name: rec.media_download_name || rec.title || 'media',
        kind: mediaKind,
        size: rec.media_size_bytes || 0,
        url: `${assetBase}/${mediaKind}`,
      } : null,
      srt: assets.srt ? 'srt' : null,
      vtt: assets.vtt ? 'vtt' : null,
    };
  }

  /* ── Ancien historique navigateur : repli + migration ──── */
  _legacyHistory() {
    try { return JSON.parse(localStorage.getItem('vt_history') || '[]'); }
    catch (_) { return []; }
  }

  /* Reprise unique de l'ancien historique localStorage vers la bibliothèque.
     Le drapeau évite les doublons ; vt_history n'est effacé qu'après succès. */
  async _migrateLegacyHistory() {
    if (localStorage.getItem('vt_history_imported')) return;
    const legacy = this._legacyHistory();
    if (!legacy.length) { localStorage.setItem('vt_history_imported', '1'); return; }

    const entries = legacy.map(e => {
      const [src, dst] = String(e.langPair || '').toLowerCase().split('→');
      const ts = Date.parse(e.date || '');
      return {
        id: /^[A-Za-z0-9_-]{1,64}$/.test(e.id || '') ? e.id : undefined,
        title: e.title || 'Sans titre',
        created_at: Number.isFinite(ts) ? ts / 1000 : undefined,
        platform: e.platform || '',
        lang_src: src || '', lang_dst: dst || '',
        model: e.model || '',
        script: e.script || '', summary: e.summary || '', translation: e.translation || '',
        no_speech: Boolean(e.noSpeech),
      };
    });

    const res = await this._lib('/import', this._libJson('POST', { entries }));
    localStorage.setItem('vt_history_imported', '1');
    localStorage.removeItem('vt_history');
    if (res.imported) {
      this._toast(`${res.imported} transcription${res.imported > 1 ? 's' : ''} reprise${res.imported > 1 ? 's' : ''} dans votre historique`);
    }
  }

  /* ── Rendu de la bibliothèque ─────────────────────────── */
  async _renderHistory({ append = false } = {}) {
    const listEl = $('histList');
    const q = $('histSearch').value.trim();

    if (!append) {
      this._histOffset = 0;
      this._histSeq = (this._histSeq || 0) + 1;
      this._histLastLabel = null;   // sinon le 1er groupe n'est pas recréé après vidage
      listEl.innerHTML = '';
    }
    const seq = this._histSeq;

    let data;
    try {
      await this._migrateLegacyHistory();
      data = await this._lib(`?q=${encodeURIComponent(q)}&limit=50&offset=${this._histOffset || 0}`);
    } catch (_) {
      // Backend injoignable (page ouverte sans serveur) : repli lecture seule
      this._renderLegacyFallback();
      return;
    }
    if (seq !== this._histSeq) return;   // une frappe plus récente a relancé le rendu

    this._histOffset = (this._histOffset || 0) + data.items.length;
    this._histTotal = data.total;
    if (!append) $('histEmpty').classList.toggle('show', data.total === 0);

    this._appendHistoryRows(listEl, data.items);
    this._renderHistoryFoot();
  }

  _appendHistoryRows(listEl, items) {
    let group = listEl.querySelector('.hist-group:last-of-type');
    let lastLabel = this._histLastLabel;

    for (const item of items) {
      const label = this._groupKey(new Date((item.created_at || 0) * 1000));
      if (label !== lastLabel || !group) {
        const head = document.createElement('div');
        head.className = 'hist-group-label';
        head.innerHTML = `<span class="dot"></span><span class="txt"></span>`;
        head.querySelector('.txt').textContent = label;
        listEl.appendChild(head);
        group = document.createElement('div');
        group.className = 'hist-group';
        listEl.appendChild(group);
        lastLabel = label;
      }
      group.appendChild(this._historyRow(item));
    }
    this._histLastLabel = lastLabel;

    const more = listEl.querySelector('.hist-more-row');
    if (more) more.remove();
    if (this._histOffset < this._histTotal) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'hist-more-row';
      btn.textContent = `Afficher plus (${this._histTotal - this._histOffset} restantes)`;
      btn.addEventListener('click', () => this._renderHistory({ append: true }));
      listEl.appendChild(btn);
    }
  }

  _historyRow(item) {
    const src = (item.lang_src || '').toUpperCase();
    const dst = (item.lang_dst || '').toUpperCase();
    const langPair = (src && dst && src !== dst) ? `${src}→${dst}` : (src || '');

    const row = document.createElement('div');
    row.className = 'hist-entry';
    row.innerHTML = `
      <button type="button" class="hist-open">
        <span class="title"></span><span class="meta"></span>
      </button>
      <button type="button" class="hist-star" aria-pressed="false" title="Favori">★</button>
      <button type="button" class="hist-more" title="Plus d'actions">···</button>`;

    const titleEl = row.querySelector('.title');
    titleEl.textContent = item.title;
    row.querySelector('.meta').textContent = [item.platform, langPair].filter(Boolean).join(' · ');

    row.querySelector('.hist-open').addEventListener('click', () => this._openEntry(item.id));

    const star = row.querySelector('.hist-star');
    star.classList.toggle('on', item.favorite);
    star.setAttribute('aria-pressed', String(Boolean(item.favorite)));
    star.addEventListener('click', async () => {
      const next = !star.classList.contains('on');
      star.classList.toggle('on', next);
      star.setAttribute('aria-pressed', String(next));
      try {
        await this._lib(`/${encodeURIComponent(item.id)}`, this._libJson('PATCH', { favorite: next }));
        item.favorite = next;
      } catch (_) {
        star.classList.toggle('on', !next);   // le serveur a refusé : on remet l'état visuel
        this._toast('Impossible de modifier le favori');
      }
    });

    row.querySelector('.hist-more').addEventListener('click', (e) => {
      e.stopPropagation();
      this._openRowMenu(e.currentTarget, item, row, titleEl);
    });
    return row;
  }

  _openRowMenu(anchor, item, row, titleEl) {
    document.querySelectorAll('.hist-menu').forEach(m => m.remove());
    const menu = document.createElement('div');
    menu.className = 'hist-menu';
    menu.innerHTML = `
      <button type="button" data-act="rename">Renommer</button>
      <button type="button" data-act="delete">Supprimer</button>`;
    const r = anchor.getBoundingClientRect();
    menu.style.top = `${r.bottom + 4}px`;
    menu.style.left = `${Math.max(8, r.right - 150)}px`;
    document.body.appendChild(menu);

    const close = () => { menu.remove(); document.removeEventListener('click', close); };
    setTimeout(() => document.addEventListener('click', close), 0);

    menu.querySelector('[data-act="rename"]').addEventListener('click', () => {
      close();
      this._renameInline(item, titleEl);
    });
    menu.querySelector('[data-act="delete"]').addEventListener('click', async () => {
      close();
      // confirm() plutôt que prompt() : prompt n'existe pas dans Electron
      if (!confirm(`Supprimer « ${item.title} » ?\n\nLa transcription, ses sous-titres et son audio seront effacés définitivement.`)) return;
      try {
        await this._lib(`/${encodeURIComponent(item.id)}`, { method: 'DELETE' });
        row.remove();
        if (this.result && this.result.id === item.id) this.result = null;
        this._renderHistory();
      } catch (_) { this._toast('Suppression impossible'); }
    });
  }

  /* Renommage sur place : le titre devient un champ, Entrée valide, Échap annule */
  _renameInline(item, titleEl) {
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'hist-rename';
    input.value = item.title;
    titleEl.replaceWith(input);
    input.focus();
    input.select();

    let done = false;
    const restore = (title) => {
      if (done) return;
      done = true;
      titleEl.textContent = title;
      input.replaceWith(titleEl);
    };
    const commit = async () => {
      const next = input.value.trim();
      if (!next || next === item.title) return restore(item.title);
      restore(next);
      try {
        await this._lib(`/${encodeURIComponent(item.id)}`, this._libJson('PATCH', { title: next }));
        item.title = next;
        if (this.result && this.result.id === item.id) {
          this.result.title = next;
          $('resTitle').textContent = next;
        }
      } catch (_) {
        titleEl.textContent = item.title;
        this._toast('Renommage impossible');
      }
    };
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); commit(); }
      if (e.key === 'Escape') { e.preventDefault(); restore(item.title); }
    });
    input.addEventListener('blur', commit);
  }

  async _openEntry(id) {
    try {
      const rec = await this._lib(`/${encodeURIComponent(id)}`);
      this._renderResult(this._entryFromApi(rec));
      this.showView('results', { navKey: 'history' });
    } catch (_) { this._toast('Impossible d’ouvrir cette transcription'); }
  }

  async _renderHistoryFoot() {
    try {
      const s = await this._lib('/stats');
      this._libStats = s;
      $('histFoot').textContent = s.count
        ? `${s.count} transcription${s.count > 1 ? 's' : ''} · ${this._fmtSize(s.bytes)} sur disque`
        : '';
      this._renderDiskPanel();
    } catch (_) { $('histFoot').textContent = ''; }
  }

  /* ── Espace disque (Paramètres) ───────────────────────── */
  async _refreshDisk() {
    try {
      this._libStats = await this._lib('/stats');
      this._renderDiskPanel();
    } catch (_) { /* backend absent : le panneau garde sa valeur précédente */ }
  }

  _renderDiskPanel() {
    const s = this._libStats;
    if (!s || !$('diskSummary')) return;
    $('diskSummary').textContent = s.count
      ? `${s.count} transcription${s.count > 1 ? 's' : ''} · ${this._fmtSize(s.bytes)} au total`
        + (s.media_bytes ? ` · dont ${this._fmtSize(s.media_bytes)} de médias` : '')
      : 'Historique vide';
    $('diskPath').textContent = s.root || '';
    // Bouton natif : présent uniquement dans l'application installée
    $('openLibraryBtn').style.display = (window.avt && window.avt.openLibrary) ? '' : 'none';
  }

  /* Libération d'espace : strictement manuelle, et on annonce le volume avant d'agir */
  async _freeSpace() {
    try {
      const { candidates } = await this._lib('/free-space', this._libJson('POST', {}));
      if (!candidates.length) { this._toast('Aucun média à libérer'); return; }
      const bytes = candidates.reduce((n, c) => n + (c.size_bytes || 0), 0);
      const ok = confirm(
        `Libérer environ ${this._fmtSize(bytes)} ?\n\n`
        + `${candidates.length} transcription${candidates.length > 1 ? 's perdront' : ' perdra'} son fichier audio.\n`
        + `Les textes, résumés et sous-titres sont conservés, et les favoris ne sont pas touchés.`
      );
      if (!ok) return;
      const res = await this._lib('/free-space', this._libJson('POST', { ids: candidates.map(c => c.id) }));
      this._libStats = res.stats;
      this._renderDiskPanel();
      this._toast(`${this._fmtSize(res.freed)} libérés`);
    } catch (_) { this._toast('Libération impossible'); }
  }

  /* Backend injoignable : on affiche l'ancien historique en lecture seule */
  _renderLegacyFallback() {
    const listEl = $('histList');
    const q = $('histSearch').value.trim().toLowerCase();
    let entries = this._legacyHistory();
    if (q) entries = entries.filter(e => (e.title || '').toLowerCase().includes(q));

    listEl.innerHTML = '';
    $('histEmpty').classList.toggle('show', entries.length === 0);
    for (const e of entries) {
      const row = document.createElement('div');
      row.className = 'hist-entry';
      row.innerHTML = `<button type="button" class="hist-open">
        <span class="title"></span><span class="meta"></span></button>`;
      row.querySelector('.title').textContent = e.title;
      row.querySelector('.meta').textContent = [e.platform, e.langPair].filter(Boolean).join(' · ');
      row.querySelector('.hist-open').addEventListener('click', () => {
        this._renderResult(e);
        this.showView('results', { navKey: 'history' });
      });
      listEl.appendChild(row);
    }
    $('histFoot').textContent = entries.length ? 'Application hors ligne — historique local en lecture seule' : '';
  }

  _groupKey(d) {
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const day = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    const diffDays = Math.round((today - day) / 86400000);

    if (this.histGroup === 'day') {
      if (diffDays === 0) return 'Aujourd’hui';
      if (diffDays === 1) return 'Hier';
      return d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'long', year: 'numeric' });
    }
    if (this.histGroup === 'week') {
      const mondayOf = (x) => {
        const m = new Date(x);
        m.setDate(x.getDate() - ((x.getDay() + 6) % 7));
        return m;
      };
      const m = mondayOf(day), mNow = mondayOf(today);
      if (m.getTime() === mNow.getTime()) return 'Cette semaine';
      return `Semaine du ${m.toLocaleDateString('fr-FR', { day: 'numeric', month: 'long' })}`;
    }
    /* month */
    if (d.getMonth() === now.getMonth() && d.getFullYear() === now.getFullYear()) return 'Ce mois-ci';
    return d.toLocaleDateString('fr-FR', { month: 'long', year: 'numeric' });
  }

  /* ── Toast ────────────────────────────────────────────── */
  _toast(msg) {
    const el = $('errorToast');
    el.textContent = msg;
    el.classList.add('show');
    clearTimeout(this._toastTimer);
    this._toastTimer = setTimeout(() => el.classList.remove('show'), 4200);
  }
}

document.addEventListener('DOMContentLoaded', () => { window.app = new App(); });
