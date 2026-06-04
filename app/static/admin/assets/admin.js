const API = '/api/v1';

function adminApp() {
  return {
    page: 'overview',
    lang: localStorage.getItem('onco_lang') || 'zh',
    t(key) {
      const dict = I18N[this.lang] || I18N.zh;
      return dict[key] || key;
    },
    menu: [
      { id: 'dashboard', label: '运行看板', icon: '📈' },
      { id: 'overview', label: '系统概览', icon: '📊' },
      { id: 'users', label: '用户管理', icon: '👥' },
      { id: 'kb', label: '知识库管理', icon: '📚' },
      { id: 'chunks', label: 'Chunk 管理', icon: '🧩' },
      { id: 'chunk', label: '分块策略', icon: '✂️' },
      { id: 'models', label: '嵌入&重排', icon: '🧬' },
      { id: 'model_configs', label: '模型配置管理', icon: '🔧' },
      { id: 'retrieve', label: '检索设置', icon: '🔍' },
      { id: 'debug', label: '检索调试', icon: '🧪' },
      { id: 'pdf_map', label: 'PDF源文件', icon: '🗂️' },
      { id: 'prompt', label: 'Prompt 编辑', icon: '📝' },
      { id: 'sessions', label: '会话管理', icon: '💬' },
    ],
    stats: {},
    config: {
      llm: { base_url: '', api_key: '', model_name: '', temperature: 0.5, max_tokens: 4096, top_p: 0.9, system_prompt: '' },
      embedding: { model_name: '', device: 'cpu', normalize_embeddings: true },
      retrieval: { search_k: 15, score_threshold: 0.15, use_rerank: false, rerank_model: '', rerank_top_k: 5 },
      hybrid_search: { enabled: true, bm25_weight: 0.4, vector_weight: 0.6, rrf_k: 60 },
      chunking: { chunk_size: 800, chunk_overlap: 150, separator: '\n\n', enable_contextual_chunking: true, contextual_prefix_format: '' },
      doc_type_rules: [],
      query_expansion: { enabled: true, synonym_dict: {} },
      terminology: { enabled: true, source: 'medct', medct_path: 'data/medct', medct_download_url: '', auto_download: true, custom_rules: {} },
      cache: { enabled: true, max_size: 1000, similarity_threshold: 0.92, default_ttl: 3600, medical_fact_ttl: 86400, guideline_ttl: 43200, persist_path: 'data/query_cache.json', save_interval: 300 },
      knowledge_base: { pdf_paths: [], markdown_paths: [], auto_scan: true, scan_interval_hours: 24 },
      host: '0.0.0.0', port: 8000, log_level: 'info', data_dir: 'data', knowledge_base_dir: 'knowledge-base'
    },
    documents: [],
    allSessions: [],
    users: [],
    _usersInterval: null,
    dispatchModal: { show: false, username: '', code: '' },
    kbPaths: { pdf_paths: [], markdown_paths: [] },
    scanning: false,
    toast: { show: false, message: '', type: 'success' },

    // --- Auth ---
    token: localStorage.getItem('admin_token') || '',
    showLogin: false,
    loginForm: { username: 'admin', password: '' },
    showChangePassword: false,
    passwordForm: { old_password: '', new_password: '', confirm_password: '' },

    // --- Knowledge Base v2.1 ---
    uploadFileList: [],
    uploading: false,
    folders: [],
    selectedFolder: 'root',
    kbFiles: [],
    kbTab: 'all', // 'all' | 'pending' | 'chunked' | 'indexed' | 'backup'
    scanResults: { total: 0, new: [], modified: [], unchanged: [], missing: [] },
    selectedFiles: [],
    selectedScanFiles: [],
    selectedDocIds: [],
    showNewFolder: false,
    newFolderName: '',
    showChunkPreview: false,
    chunkPreviewResults: [],

    get kbFileCounts() {
      const counts = { all: 0, pending: 0, chunked: 0, indexed: 0, backup: 0 };
      for (const doc of this.kbFiles || []) {
        counts.all++;
        if (['pending', 'scanned', 'failed', 'uploaded'].includes(doc.status)) counts.pending++;
        else if (doc.status === 'chunked') counts.chunked++;
        else if (['indexed', 'embedding', 'imported'].includes(doc.status)) counts.indexed++;
        if (['chunked', 'indexed', 'embedding', 'imported'].includes(doc.status)) counts.backup++;
      }
      return counts;
    },

    get filteredKbFiles() {
      const statusMap = {
        all: null,  // null = no filter, show everything
        pending: ['pending', 'scanned', 'failed', 'uploaded'],
        chunked: ['chunked'],
        indexed: ['indexed', 'embedding', 'imported'],
        backup: ['chunked', 'indexed', 'embedding', 'imported']
      };
      const allowed = statusMap[this.kbTab];
      if (allowed === null) return this.kbFiles || [];
      return (this.kbFiles || []).filter(doc => allowed.includes(doc.status));
    },

    // --- Ingest Jobs ---
    ingestJobs: [],
    activeIngestJob: null,
    ingestProgress: { show: false, job_id: '', job_type: 'embed', stage: '', current: 0, total: 0, file: '', pct: 0, errors: [] },
    ingestPollInterval: null,

    // --- Dashboard ---
    dashboardJobs: [],
    dashboardStats: { total: 0, running: 0, completed: 0, failed: 0, cancelled: 0 },
    _dashboardInterval: null,

    // --- Chunk Management ---
    allDocsForChunk: [],
    chunkDocId: '',
    chunks: [],
    chunkFilter: '',
    selectedChunkIds: [],
    showChunkEditor: false,
    editingChunk: { content: '', metadata: {}, customTagsStr: '' },

    async init() {
      if (!this.token) {
        this.showLogin = true;
        return;
      }
      // Verify token is still valid
      try {
        const res = await this._fetch(`${API}/auth/me`);
        if (!res.ok) {
          this.logout();
          return;
        }
      } catch (e) {
        this.logout();
        return;
      }
      await this.loadStats();
      await this.loadConfig();
      await this.loadProfiles();
      await this.loadDocuments();
      await this.loadSessions();
      await this.loadUsers();
      await this.loadFolders();
      await this.loadKbFiles();
      await this.loadAllDocsForChunk();
      await this.loadIngestJobs();
      // Auto-resume polling if there's a running ingest job (e.g. after page refresh)
      const runningJob = this.ingestJobs.find(j => j.status === 'running');
      if (runningJob) {
        this.startIngestPolling(runningJob.job_id, runningJob.job_type || 'embed');
      }
      await this.loadKbPaths();
      // Clear any existing stats interval before setting a new one
      if (this._statsInterval) clearInterval(this._statsInterval);
      this._statsInterval = setInterval(() => this.loadStats(), 10000);
      // Dashboard polling: every 2s, only fetches when on dashboard page
      if (this._dashboardInterval) clearInterval(this._dashboardInterval);
      this._dashboardInterval = setInterval(() => this.loadDashboard(), 2000);
    },

    // --- Auth methods ---
    async login() {
      try {
        const res = await fetch(`${API}/auth/login`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(this.loginForm)
        });
        const data = await res.json();
        if (res.ok && data.access_token) {
          this.token = data.access_token;
          localStorage.setItem('admin_token', this.token);
          this.showLogin = false;
          this.showToast('登录成功');
          await this.init();
        } else {
          this.showToast(data.detail || '登录失败', 'error');
        }
      } catch (e) {
        this.showToast('登录失败: ' + e.message, 'error');
      }
    },

    logout() {
      this.token = '';
      localStorage.removeItem('admin_token');
      this.showLogin = true;
      this.showToast('已登出');
    },

    async changePassword() {
      if (this.passwordForm.new_password !== this.passwordForm.confirm_password) {
        this.showToast('两次输入的新密码不一致', 'error');
        return;
      }
      if (this.passwordForm.new_password.length < 4) {
        this.showToast('新密码至少4位', 'error');
        return;
      }
      try {
        const res = await fetch(`${API}/auth/password`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + this.token
          },
          body: JSON.stringify({
            old_password: this.passwordForm.old_password,
            new_password: this.passwordForm.new_password
          })
        });
        const data = await res.json();
        if (res.ok && data.success) {
          this.showToast('密码已修改，请重新登录');
          this.showChangePassword = false;
          this.passwordForm = { old_password: '', new_password: '', confirm_password: '' };
          setTimeout(() => this.logout(), 1500);
        } else {
          this.showToast(data.detail || '修改失败', 'error');
        }
      } catch (e) {
        this.showToast('修改失败: ' + e.message, 'error');
      }
    },

    // Wrapper for fetch that adds auth header and handles rate-limit
    async _fetch(url, options = {}) {
      options.headers = options.headers || {};
      options.headers['Authorization'] = `Bearer ${this.token}`;
      console.log('[_fetch]', options.method || 'GET', url);
      const res = await fetch(url, options);
      console.log('[_fetch] response', res.status, url);
      if (res.status === 429) {
        this.showToast('请求过于频繁，请稍后再试', 'error');
      } else if (res.status === 413) {
        this.showToast('上传文件过大', 'error');
      }
      return res;
    },

    showToast(message, type = 'success') {
      this.toast = { show: true, message, type };
      setTimeout(() => this.toast.show = false, 3000);
    },

    // --- Stats & Config ---
    async loadStats() {
      try {
        const res = await this._fetch(`${API}/stats`);
        if (res.ok) this.stats = await res.json();
      } catch (e) { console.error('stats', e); }
    },

    async loadConfig() {
      try {
        const res = await fetch(`${API}/config`); // config GET is public
        const data = await res.json();
        this.config = JSON.parse(JSON.stringify(data));
      } catch (e) { console.error('config', e); }
    },

    async saveConfig() {
      try {
        const payload = JSON.parse(JSON.stringify(this.config));
        if (payload.llm?.api_key?.includes('...') || payload.llm?.api_key === '') {
          delete payload.llm.api_key;
        }
        const res = await this._fetch(`${API}/config`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ data: payload })
        });
        const result = await res.json();
        if (result.success) {
          this.showToast('配置已保存并热重载');
          await this.loadConfig();
          await this.loadStats();
        } else {
          this.showToast('保存失败', 'error');
        }
      } catch (e) {
        this.showToast('保存失败: ' + e.message, 'error');
      }
    },

    // --- Documents (uploaded) ---
    async loadDocuments() {
      try {
        const res = await fetch(`${API}/documents`);
        this.documents = await res.json();
      } catch (e) { console.error('docs', e); }
    },

    async deleteDoc(id) {
      if (!confirm('确定删除该文档?')) return;
      try {
        await this._fetch(`${API}/documents/${id}`, { method: 'DELETE' });
        this.showToast('文档已删除');
        await this.loadDocuments();
        await this.loadStats();
        await this.loadKbFiles();
        await this.loadAllDocsForChunk();
      } catch (e) { this.showToast('删除失败', 'error'); }
    },

    // --- Folders ---
    async loadFolders() {
      try {
        const res = await fetch(`${API}/knowledge-base/folders`);
        const data = await res.json();
        this.folders = data.folders || [];
        if (!this.folders.length) {
          this.folders = [{ id: 'root', name: '根目录', children: [] }];
        }
      } catch (e) { console.error('folders', e); }
    },

    selectFolder(id) {
      this.selectedFolder = id;
      this.loadKbFiles();
    },

    async createFolder() {
      if (!this.newFolderName.trim()) return;
      try {
        const res = await this._fetch(`${API}/knowledge-base/folders`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: this.newFolderName.trim(), parent_id: this.selectedFolder })
        });
        const data = await res.json();
        if (data.success) {
          this.showToast('文件夹已创建');
          this.showNewFolder = false;
          this.newFolderName = '';
          await this.loadFolders();
        }
      } catch (e) { this.showToast('创建失败', 'error'); }
    },

    // --- KB Files ---
    async loadKbFiles() {
      try {
        const res = await fetch(`${API}/knowledge-base/files?folder_id=${this.selectedFolder}`, {
          headers: { 'Authorization': 'Bearer ' + this.token }
        });
        if (!res.ok) {
          console.error('loadKbFiles HTTP error:', res.status, await res.text());
          this.showToast('加载文件列表失败', 'error');
          return;
        }
        const data = await res.json();
        this.kbFiles = data.files || [];
      } catch (e) {
        console.error('kb files error:', e);
        this.showToast('加载文件列表失败', 'error');
      }
    },

    async deleteKbDoc(id) {
      if (!confirm('确定删除该文档?')) return;
      try {
        await this._fetch(`${API}/knowledge-base/files/${id}`, { method: 'DELETE' });
        this.showToast('文档已删除');
        await this.loadKbFiles();
        await this.loadStats();
        await this.loadAllDocsForChunk();
      } catch (e) { this.showToast('删除失败', 'error'); }
    },

    async rechunkDoc(id) {
      if (!confirm('确定重新分块该文档? 会清空现有向量后重建。')) return;
      try {
        const res = await this._fetch(`${API}/knowledge-base/rechunk`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ doc_ids: [id] })
        });
        const data = await res.json();
        if (data.job_id) {
          this.startIngestPolling(data.job_id);
          this.showToast(`后台重新分块任务已启动 (Job: ${data.job_id})`);
        } else {
          this.showToast('重新分块失败: 未返回任务ID', 'error');
        }
      } catch (e) { this.showToast('重新分块失败', 'error'); }
    },

    toggleSelectAll(e) {
      if (e.target.checked) {
        this.selectedDocIds = this.filteredKbFiles.map(d => d.doc_id);
      } else {
        this.selectedDocIds = [];
      }
    },

    async batchDelete() {
      if (!confirm(`确定删除选中的 ${this.selectedDocIds.length} 个文档?`)) return;
      try {
        await this._fetch(`${API}/knowledge-base/batch-action`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'delete', doc_ids: this.selectedDocIds })
        });
        this.showToast('批量删除完成');
        this.selectedDocIds = [];
        await this.loadKbFiles();
        await this.loadStats();
        await this.loadAllDocsForChunk();
      } catch (e) { this.showToast('批量删除失败', 'error'); }
    },

    async batchMove() {
      const targetFolder = prompt('请输入目标文件夹 ID:', this.selectedFolder);
      if (!targetFolder) return;
      try {
        await this._fetch(`${API}/knowledge-base/batch-action`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'move', doc_ids: this.selectedDocIds, folder_id: targetFolder })
        });
        this.showToast('批量移动完成');
        this.selectedDocIds = [];
        await this.loadKbFiles();
      } catch (e) { this.showToast('批量移动失败', 'error'); }
    },

    // --- KB Doc Actions (chunk / embed) ---
    async chunkDoc(docId) {
      if (!docId) { this.showToast('文档ID无效', 'error'); return; }
      try {
        const doc = this.kbFiles.find(d => d.doc_id === docId);
        if (!doc || !doc.file_path) {
          this.showToast('文档路径未找到', 'error'); return;
        }
        console.log('[chunkDoc] Starting chunk for', docId, doc.file_path);
        await this._startChunkJob([doc.file_path]);
      } catch (e) {
        console.error('[chunkDoc] Error:', e);
        this.showToast('分块失败: ' + (e.message || '未知错误'), 'error');
      }
    },

    async embedDoc(docId) {
      if (!docId) { this.showToast('文档ID无效', 'error'); return; }
      try {
        console.log('[embedDoc] Starting embed for', docId);
        await this._startEmbedJob([docId]);
      } catch (e) {
        console.error('[embedDoc] Error:', e);
        this.showToast('嵌入失败: ' + (e.message || '未知错误'), 'error');
      }
    },

    async batchChunkDocs() {
      if (!this.selectedDocIds.length) { this.showToast('请先选择文档', 'error'); return; }
      if (this.kbTab === 'indexed') {
        // For indexed docs, rechunk means rechunk+embed via /batch-action
        await this._startRechunkJob(this.selectedDocIds);
      } else {
        // For pending/chunked docs, rechunk means just chunk
        const filePaths = this.selectedDocIds.map(id => {
          const doc = this.kbFiles.find(d => d.doc_id === id);
          return doc ? doc.file_path : null;
        }).filter(Boolean);
        if (!filePaths.length) { this.showToast('无法获取文档路径', 'error'); return; }
        await this._startChunkJob(filePaths);
      }
      this.selectedDocIds = [];
    },

    async batchEmbedDocs() {
      if (!this.selectedDocIds.length) { this.showToast('请先选择文档', 'error'); return; }
      // Only embed docs that are chunked/indexed
      const docIds = this.selectedDocIds.filter(id => {
        const doc = this.kbFiles.find(d => d.doc_id === id);
        return doc && ['chunked', 'indexed'].includes(doc.status);
      });
      if (!docIds.length) { this.showToast('选中的文档中没有可嵌入的', 'error'); return; }
      await this._startEmbedJob(docIds);
      this.selectedDocIds = [];
    },

    // --- File Upload ---
    handleFileSelect(e) {
      this.uploadFileList = Array.from(e.target.files);
    },

    async uploadFiles() {
      if (!this.uploadFileList.length) return;
      this.uploading = true;
      let success = 0;
      for (const file of this.uploadFileList) {
        const form = new FormData();
        form.append('file', file);
        try {
          const res = await this._fetch(`${API}/documents/upload`, { method: 'POST', body: form });
          if (res.ok) success++;
        } catch (e) { console.error('upload', e); }
      }
      this.showToast(`上传完成: ${success}/${this.uploadFileList.length}`);
      this.uploadFileList = [];
      document.getElementById('file-input').value = '';
      await this.loadDocuments();
      await this.loadStats();
      await this.loadAllDocsForChunk();
      this.uploading = false;
    },

    // --- Step-by-step Ingestion ---
    async previewScan() {
      this.scanning = true;
      this.scanResults = { total: 0, new: [], modified: [], unchanged: [], missing: [] };
      this.selectedScanFiles = [];
      try {
        const res = await this._fetch(`${API}/knowledge-base/preview-scan`, { method: 'POST' });
        const data = await res.json();
        this.scanResults = data;
        this.showToast(`预扫描完成: 新${data.new.length} 改${data.modified.length} 未变${data.unchanged.length} 缺失${data.missing.length}`);
      } catch (e) {
        console.error('previewScan error', e);
        this.showToast('预扫描失败: ' + (e.message || '未知错误'), 'error');
      }
      this.scanning = false;
    },

    toggleSelectScanAll(e) {
      if (e.target.checked) {
        const allPaths = [
          ...this.scanResults.new.map(f => f.file_path),
          ...this.scanResults.modified.map(f => f.file_path),
          ...this.scanResults.unchanged.map(f => f.file_path),
          ...this.scanResults.missing.map(f => f.file_path),
        ];
        this.selectedScanFiles = allPaths;
      } else {
        this.selectedScanFiles = [];
      }
    },

    async chunkSingle(filePath) {
      await this._startChunkJob([filePath]);
    },

    async batchChunk() {
      if (!this.selectedScanFiles.length) { this.showToast('请先选择文件', 'error'); return; }
      // Only chunk new/modified files from scan results
      const chunkable = this.selectedScanFiles.filter(path => {
        return this.scanResults.new.some(f => f.file_path === path) ||
               this.scanResults.modified.some(f => f.file_path === path);
      });
      if (!chunkable.length) {
        this.showToast('选中的文件中无需分块（未变/缺失文件不需要分块）', 'error');
        return;
      }
      await this._startChunkJob(chunkable);
    },

    async _startChunkJob(filePaths) {
      try {
        const res = await this._fetch(`${API}/knowledge-base/chunk-jobs`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ file_paths: filePaths, folder_id: this.selectedFolder })
        });
        const data = await res.json();
        if (data.job_id) {
          this.startIngestPolling(data.job_id, 'chunk');
          this.showToast(`后台分块任务已启动 (Job: ${data.job_id})`);
        } else {
          this.showToast('分块失败: 未返回任务ID', 'error');
        }
      } catch (e) { this.showToast('分块任务启动失败', 'error'); }
    },

    async _startRechunkJob(docIds) {
      try {
        const res = await this._fetch(`${API}/knowledge-base/rechunk`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ doc_ids: docIds })
        });
        const data = await res.json();
        if (data.job_id) {
          this.startIngestPolling(data.job_id, 'embed');
          this.showToast(`后台重新分块任务已启动 (Job: ${data.job_id})`);
        } else {
          this.showToast('重新分块失败: 未返回任务ID', 'error');
        }
      } catch (e) { this.showToast('重新分块任务启动失败', 'error'); }
    },

    async embedSingle(docId) {
      if (!docId) { this.showToast('文档ID无效', 'error'); return; }
      await this._startEmbedJob([docId]);
    },

    async batchEmbedFromScan() {
      // Collect embeddable doc_ids: unchanged files with existing_doc_id, plus any chunked docs
      const docIds = [];
      for (const path of this.selectedScanFiles) {
        const unchanged = this.scanResults.unchanged.find(f => f.file_path === path);
        if (unchanged && unchanged.existing_doc_id) {
          docIds.push(unchanged.existing_doc_id);
          continue;
        }
        // Also allow embedding files that are already chunked in registry
        const modified = this.scanResults.modified.find(f => f.file_path === path);
        if (modified && modified.existing_doc_id) {
          const doc = this.kbFiles.find(d => d.doc_id === modified.existing_doc_id);
          if (doc && doc.status === 'chunked') docIds.push(modified.existing_doc_id);
        }
      }
      if (!docIds.length) { this.showToast('选中的文件中没有可嵌入的文档（请先分块）', 'error'); return; }
      await this._startEmbedJob(docIds);
    },

    async _startEmbedJob(docIds) {
      console.log('[_startEmbedJob] docIds=', docIds, 'token=', this.token ? 'exists' : 'MISSING');
      try {
        const res = await this._fetch(`${API}/knowledge-base/ingest`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ doc_ids: docIds })
        });
        console.log('[_startEmbedJob] res.status=', res.status);
        const data = await res.json();
        console.log('[_startEmbedJob] data=', data);
        if (data.job_id) {
          this.startIngestPolling(data.job_id, 'embed');
          this.showToast(`后台嵌入任务已启动 (Job: ${data.job_id})`);
        } else {
          this.showToast('嵌入失败: 未返回任务ID', 'error');
        }
      } catch (e) {
        console.error('[_startEmbedJob] ERROR:', e);
        this.showToast('嵌入任务启动失败: ' + (e.message || '未知错误'), 'error');
      }
    },

    async previewChunks() {
      const paths = this.selectedFiles;
      if (!paths.length) { this.showToast('请先选择文件', 'error'); return; }
      try {
        const res = await this._fetch(`${API}/knowledge-base/preview-chunks`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ file_paths: paths, folder_id: this.selectedFolder })
        });
        const data = await res.json();
        this.chunkPreviewResults = data.results || [];
        this.showChunkPreview = true;
        this.showToast('Chunk 预览已生成');
        await this.loadKbFiles();
      } catch (e) { this.showToast('预览生成失败', 'error'); }
    },

    async ingestSelected() {
      const docIds = [];
      for (const f of this.scanResults.new) {
        if (this.selectedFiles.includes(f.file_path)) {
          docIds.push(f.existing_doc_id || this._extractDocIdFromScan(f));
        }
      }
      for (const f of this.scanResults.modified) {
        if (this.selectedFiles.includes(f.file_path)) {
          docIds.push(f.existing_doc_id);
        }
      }
      for (const r of this.chunkPreviewResults) {
        if (!docIds.includes(r.doc_id)) docIds.push(r.doc_id);
      }

      if (!docIds.length) { this.showToast('没有可入库的文档', 'error'); return; }

      try {
        const res = await this._fetch(`${API}/knowledge-base/ingest`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ doc_ids: docIds })
        });
        const data = await res.json();
        if (data.job_id) {
          this.startIngestPolling(data.job_id);
          this.showToast(`后台入库任务已启动 (Job: ${data.job_id})`);
        } else {
          this.showToast('入库失败: 未返回任务ID', 'error');
        }
      } catch (e) { this.showToast('入库失败', 'error'); }
    },

    async startIngestPolling(jobId, jobType) {
      this.activeIngestJob = jobId;
      this.ingestProgress = {
        show: true,
        job_id: jobId,
        job_type: jobType,
        stage: 'pending',
        current: 0,
        total: 0,
        file: '',
        pct: 0,
        errors: []
      };
      if (this.ingestPollInterval) clearInterval(this.ingestPollInterval);
      this.ingestPollInterval = setInterval(() => this.pollIngestProgress(), 1000);
      // Immediate first poll
      await this.pollIngestProgress();
    },

    async pollIngestProgress() {
      if (!this.activeIngestJob) return;
      try {
        const res = await this._fetch(`${API}/knowledge-base/ingest/jobs/${this.activeIngestJob}`);
        const data = await res.json();
        const job = data.job;
        if (!job) return;

        // Sync into ingestJobs so the KB task panel updates in real-time
        if (this.ingestJobs && this.ingestJobs.length) {
          const idx = this.ingestJobs.findIndex(j => j.job_id === job.job_id);
          if (idx >= 0) this.ingestJobs[idx] = job;
          else this.ingestJobs.unshift(job);
        }

        this.ingestProgress = {
          show: true,
          job_id: job.job_id,
          job_type: job.job_type || 'embed',
          stage: job.current_stage || job.status,
          current: job.current_doc || 0,
          total: job.total_docs || 0,
          file: job.current_file || '',
          pct: job.progress_pct || 0,
          errors: job.errors || [],
          chunk_current: job.chunk_current || 0,
          chunk_total: job.chunk_total || 0,
        };

        // Refresh file list during running state so users see real-time status updates
        if (job.status === 'running') {
          await this.loadKbFiles();
        }

        if (['completed', 'failed', 'cancelled'].includes(job.status)) {
          clearInterval(this.ingestPollInterval);
          this.ingestPollInterval = null;
          this.activeIngestJob = null;
          const isChunk = job.job_type === 'chunk';
          const verb = isChunk ? '分块' : '嵌入';
          const msg = job.status === 'completed'
            ? `${verb}完成`
            : `${verb}${job.status === 'cancelled' ? '已取消' : '失败'}`;
          this.showToast(msg, job.status === 'completed' ? 'success' : 'error');
          await this.loadKbFiles();
          await this.loadStats();
          await this.loadAllDocsForChunk();
          await this.loadIngestJobs();
          if (job.status === 'completed') {
            setTimeout(() => { this.ingestProgress.show = false; }, 4000);
          }
        }
      } catch (e) { console.error('poll ingest progress', e); }
    },

    async cancelIngest() {
      if (!this.activeIngestJob) return;
      if (!confirm('确定终止当前任务?')) return;
      try {
        await this._fetch(`${API}/knowledge-base/ingest/jobs/${this.activeIngestJob}/cancel`, { method: 'POST' });
        this.showToast('已发送终止请求');
      } catch (e) { this.showToast('终止失败', 'error'); }
    },

    async loadIngestJobs() {
      try {
        const res = await this._fetch(`${API}/knowledge-base/ingest/jobs?limit=10`);
        const data = await res.json();
        this.ingestJobs = data.jobs || [];
        // Auto-resume polling if there's a running job (survives page refresh)
        const running = (this.ingestJobs || []).find(j => j.status === 'running');
        if (running && !this.activeIngestJob) {
          await this.startIngestPolling(running.job_id, running.job_type);
        }
      } catch (e) { console.error('load ingest jobs', e); }
    },

    _extractDocIdFromScan(scanItem) {
      const found = this.chunkPreviewResults.find(r => r.file_path === scanItem.file_path);
      return found ? found.doc_id : null;
    },

    // --- Chunk Management ---
    async loadAllDocsForChunk() {
      try {
        // Fetch documents that actually have chunks in vector store
        const [docRes, kbRes] = await Promise.all([
          this._fetch(`${API}/documents`),
          fetch(`${API}/knowledge-base/files`)
        ]);
        const docData = await docRes.json();
        const kbData = await kbRes.json();
        const kbMap = new Map();
        for (const d of (kbData.files || [])) kbMap.set(d.doc_id, d);

        // Merge: docs from /documents have actual chunks, enrich with kb metadata
        const merged = [];
        for (const d of (docData || [])) {
          const kb = kbMap.get(d.doc_id);
          merged.push({
            doc_id: d.doc_id,
            file_path: kb?.file_path || d.filename || d.doc_id,
            chunk_count: d.chunk_count || kb?.chunk_count || 0,
            file_size: kb?.file_size || d.file_size || 0,
            status: kb?.status || 'indexed',
            source: kb?.source || 'kb',
          });
        }
        this.allDocsForChunk = merged;
      } catch (e) { console.error('all docs', e); }
    },

    async loadChunks() {
      if (!this.chunkDocId) { this.chunks = []; return; }
      try {
        const res = await fetch(`${API}/chunks?doc_id=${this.chunkDocId}&limit=200`);
        const data = await res.json();
        this.chunks = data.chunks || [];
      } catch (e) { console.error('chunks', e); }
    },

    get filteredChunks() {
      if (!this.chunkFilter) return this.chunks;
      const kw = this.chunkFilter.toLowerCase();
      return this.chunks.filter(c =>
        (c.document || '').toLowerCase().includes(kw) ||
        (c.metadata?.section_title || '').toLowerCase().includes(kw)
      );
    },

    toggleChunkSelectAll(e) {
      if (e.target.checked) {
        this.selectedChunkIds = this.filteredChunks.map(c => c.chunk_id);
      } else {
        this.selectedChunkIds = [];
      }
    },

    openChunkEditor(chunk) {
      this.editingChunk = {
        chunk_id: chunk.chunk_id,
        content: chunk.document || '',
        metadata: { ...chunk.metadata },
        customTagsStr: (chunk.metadata?.custom_tags || []).join(', '),
      };
      this.showChunkEditor = true;
    },

    async saveChunk() {
      const payload = {
        content: this.editingChunk.content,
        metadata: {
          ...this.editingChunk.metadata,
          custom_tags: this.editingChunk.customTagsStr.split(',').map(s => s.trim()).filter(Boolean),
        }
      };
      try {
        const res = await this._fetch(`${API}/chunks/${this.editingChunk.chunk_id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data.success) {
          this.showToast('Chunk 已保存');
          this.showChunkEditor = false;
          await this.loadChunks();
          await this.loadStats();
        } else {
          this.showToast('保存失败', 'error');
        }
      } catch (e) { this.showToast('保存失败', 'error'); }
    },

    async toggleChunk(chunkId) {
      try {
        await this._fetch(`${API}/chunks/${chunkId}/toggle`, { method: 'POST' });
        this.showToast('状态已切换');
        await this.loadChunks();
      } catch (e) { this.showToast('切换失败', 'error'); }
    },

    async deleteChunk(chunkId) {
      if (!confirm('确定删除该 chunk?')) return;
      try {
        await this._fetch(`${API}/chunks/${chunkId}`, { method: 'DELETE' });
        this.showToast('Chunk 已删除');
        await this.loadChunks();
        await this.loadStats();
      } catch (e) { this.showToast('删除失败', 'error'); }
    },

    // --- Legacy KB ---
    async loadKbPaths() {
      try {
        const res = await fetch(`${API}/knowledge-base/paths`);
        this.kbPaths = await res.json();
      } catch (e) { console.error('kb paths', e); }
    },

    async saveKbPaths() {
      try {
        const res = await this._fetch(`${API}/knowledge-base/paths`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            pdf_paths: this.kbPaths.pdf_paths.filter(p => p),
            markdown_paths: this.kbPaths.markdown_paths.filter(p => p)
          })
        });
        const data = await res.json();
        if (data.success) this.showToast('路径已保存');
      } catch (e) { this.showToast('保存失败', 'error'); }
    },

    async scanKb() {
      this.scanning = true;
      try {
        const res = await this._fetch(`${API}/knowledge-base/scan`, { method: 'POST' });
        const data = await res.json();
        this.showToast(`扫描完成: 导入${data.imported} 更新${data.updated} 删除${data.removed}`);
        await this.loadKbFiles();
        await this.loadStats();
        await this.loadAllDocsForChunk();
      } catch (e) { this.showToast('扫描失败', 'error'); }
      this.scanning = false;
    },

    addDocTypeRule() {
      if (!this.config.doc_type_rules) this.config.doc_type_rules = [];
      this.config.doc_type_rules.push({ keywords: '', chunk_size: 500, chunk_overlap: 50 });
    },

    // --- Config Profiles ---
    profiles: [],
    selectedProfile: '',
    showProfileUpload: false,
    profileUpload: { name: '', description: '', content: '' },

    async loadProfiles() {
      try {
        const res = await fetch(`${API}/config/profiles`);
        if (res.ok) {
          const data = await res.json();
          this.profiles = data.profiles || [];
        }
      } catch (e) { console.error('profiles', e); }
    },

    async applyProfile(profileId) {
      if (!profileId) return;
      if (!confirm('应用此方案将覆盖当前配置，确定继续？')) {
        this.selectedProfile = '';
        return;
      }
      try {
        const res = await this._fetch(`${API}/config/profiles/${profileId}/apply`, { method: 'POST' });
        const data = await res.json();
        if (data.success) {
          this.showToast(`已应用方案: ${this.profiles.find(p => p.id === profileId)?.name || profileId}`);
          await this.loadConfig();
          await this.loadStats();
        } else {
          this.showToast('应用方案失败', 'error');
        }
      } catch (e) {
        this.showToast('应用方案失败: ' + e.message, 'error');
      }
      this.selectedProfile = '';
    },

    async uploadProfile() {
      try {
        let configObj;
        try {
          configObj = JSON.parse(this.profileUpload.content);
        } catch (e) {
          this.showToast('JSON 格式错误，请检查', 'error');
          return;
        }
        const payload = {
          name: this.profileUpload.name,
          description: this.profileUpload.description,
          config: configObj
        };
        const res = await this._fetch(`${API}/config/upload-profile`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data.success) {
          this.showToast('自定义方案已应用');
          this.showProfileUpload = false;
          this.profileUpload = { name: '', description: '', content: '' };
          await this.loadConfig();
          await this.loadStats();
        } else {
          this.showToast('上传失败: ' + (data.detail || '未知错误'), 'error');
        }
      } catch (e) {
        this.showToast('上传失败: ' + e.message, 'error');
      }
    },

    // --- Prompt versions ---
    prompts: [],
    promptEditor: { id: '', name: '', content: '', is_default: false },
    showPromptEditor: false,

    async loadPrompts() {
      try {
        const res = await this._fetch(`${API}/prompts`);
        if (res.ok) {
          const data = await res.json();
          this.prompts = data.prompts || [];
        }
      } catch (e) { console.error('prompts', e); }
    },

    openPromptEditor(prompt) {
      if (prompt) {
        this.promptEditor = { id: prompt.id, name: prompt.name, content: '', is_default: prompt.is_default };
        this._fetch(`${API}/prompts/${prompt.id}`).then(r => r.json()).then(data => {
          this.promptEditor.content = data.content || '';
          this.showPromptEditor = true;
        });
      } else {
        this.promptEditor = { id: '', name: '', content: '', is_default: false };
        this.showPromptEditor = true;
      }
    },

    async savePrompt() {
      try {
        let res;
        if (this.promptEditor.id) {
          res = await this._fetch(`${API}/prompts/${this.promptEditor.id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(this.promptEditor)
          });
        } else {
          res = await this._fetch(`${API}/prompts`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(this.promptEditor)
          });
        }
        const data = await res.json();
        if (data.success) {
          this.showToast('Prompt 已保存');
          this.showPromptEditor = false;
          await this.loadPrompts();
        } else {
          this.showToast('保存失败', 'error');
        }
      } catch (e) { this.showToast('保存失败', 'error'); }
    },

    async deletePrompt(id) {
      if (!confirm('确定删除该 Prompt?')) return;
      try {
        await this._fetch(`${API}/prompts/${id}`, { method: 'DELETE' });
        this.showToast('已删除');
        await this.loadPrompts();
      } catch (e) { this.showToast('删除失败', 'error'); }
    },

    async setDefaultPrompt(id) {
      try {
        await this._fetch(`${API}/prompts/${id}/default`, { method: 'PUT' });
        this.showToast('已设为默认');
        await this.loadPrompts();
      } catch (e) { this.showToast('设置失败', 'error'); }
    },

    // --- Cache entries ---
    cacheEntries: [],
    async loadCacheEntries() {
      try {
        const res = await this._fetch(`${API}/cache/entries`);
        if (res.ok) {
          const data = await res.json();
          this.cacheEntries = data.entries || [];
        }
      } catch (e) { console.error('cache entries', e); }
    },

    // --- Retrieval debug ---
    debugQuery: '',
    debugResults: null,
    debugShowPrompt: true,
    debugK: 200,
    debugLoading: false,
    async runRetrievalDebug() {
      if (!this.debugQuery.trim()) return;
      this.debugLoading = true;
      this.debugResults = null;
      try {
        const res = await this._fetch(`${API}/retrieve`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: this.debugQuery.trim(), k: this.debugK || 50 })
        });
        if (res.ok) {
          this.debugResults = await res.json();
        } else {
          const err = await res.json().catch(() => ({}));
          this.showToast(err.detail || `检索失败 (${res.status})`, 'error');
        }
      } catch (e) { this.showToast('检索调试失败: ' + e.message, 'error'); }
      finally { this.debugLoading = false; }
    },

    // --- PDF Mapping ---
    pdfMap: null,
    async loadPdfMap() {
      if (this.pdfMap) return;
      try {
        const res = await this._fetch(`${API}/knowledge-base/pdf-mapping`);
        if (res.ok) this.pdfMap = await res.json();
      } catch (e) { console.error('pdfMap', e); }
    },
    async loadSessions() {
      try {
        const res = await this._fetch(`${API}/sessions?limit=200`);
        if (res.ok) this.allSessions = await res.json();
      } catch (e) { console.error('sessions', e); }
    },

    async loadUsers() {
      try {
        const res = await fetch(`${API}/auth/admin/users`, {
          headers: { 'Authorization': 'Bearer ' + this.token }
        });
        if (res.ok) {
          const data = await res.json();
          this.users = data.users || [];
        }
      } catch (e) { console.error('users', e); }
    },

    async dispatchCode(username) {
      try {
        const res = await this._fetch(`${API}/auth/admin/users/${encodeURIComponent(username)}/activation-code`);
        if (res.ok) {
          const data = await res.json();
          this.dispatchModal = { show: true, username, code: data.activation_code };
        } else {
          const err = await res.json().catch(() => ({}));
          this.showToast(err.detail || '获取激活码失败', 'error');
        }
      } catch (e) {
          this.showToast('网络错误', 'error');
        }
      },

      async deleteUser(username) {
        if (!confirm(`确定要删除用户「${username}」吗？此操作不可撤销。`)) return;
        try {
          const res = await this._fetch(`${API}/auth/admin/users/${encodeURIComponent(username)}`, { method: 'DELETE' });
          if (res.ok) {
            this.showToast(`已删除用户 ${username}`);
            await this.loadUsers();
          } else {
            const err = await res.json().catch(() => ({}));
            this.showToast(err.detail || '删除失败', 'error');
          }
        } catch (e) {
            this.showToast('网络错误', 'error');
          }
        },

        async freezeUser(username) {
          try {
            const res = await this._fetch(`${API}/auth/admin/users/${encodeURIComponent(username)}/freeze`, { method: 'PUT' });
            if (res.ok) {
              this.showToast(`已冻结用户 ${username}`);
              await this.loadUsers();
            } else {
              const err = await res.json().catch(() => ({}));
              this.showToast(err.detail || '冻结失败', 'error');
            }
          } catch (e) {
            this.showToast('网络错误', 'error');
          }
        },

        async unfreezeUser(username) {
          try {
            const res = await this._fetch(`${API}/auth/admin/users/${encodeURIComponent(username)}/unfreeze`, { method: 'PUT' });
            if (res.ok) {
              this.showToast(`已解冻用户 ${username}`);
              await this.loadUsers();
            } else {
              const err = await res.json().catch(() => ({}));
              this.showToast(err.detail || '解冻失败', 'error');
            }
          } catch (e) {
            this.showToast('网络错误', 'error');
          }
        },

        copyActivationCode() {
      navigator.clipboard.writeText(this.dispatchModal.code).then(() => {
        this.showToast('激活码已复制到剪贴板');
      }).catch(() => {
        this.showToast('复制失败，请手动复制', 'error');
      });
    },

    async deleteSession(id) {
      if (!confirm('确定删除该会话?')) return;
      try {
        await this._fetch(`${API}/sessions/${id}`, { method: 'DELETE' });
        this.showToast('会话已删除');
        await this.loadSessions();
        await this.loadStats();
      } catch (e) { this.showToast('删除失败', 'error'); }
    },

    async clearAllSessions() {
      if (!confirm('确定清空全部会话? 不可恢复!')) return;
      try {
        await this._fetch(`${API}/sessions`, { method: 'DELETE' });
        this.showToast('全部会话已清空');
        await this.loadSessions();
        await this.loadStats();
      } catch (e) { this.showToast('清空失败', 'error'); }
    },

    async clearCache() {
      if (!confirm('确定清空全部缓存?')) return;
      try {
        await this._fetch(`${API}/cache/invalidate`, {
          method: 'DELETE',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({})
        });
        this.showToast('缓存已清空');
        await this.loadStats();
      } catch (e) { this.showToast('清空失败', 'error'); }
    },

    // --- Dashboard ---
    async loadDashboard() {
      if (this.page !== 'dashboard') return;
      // Refresh stats in parallel for vector-store metrics
      this.loadStats();
      try {
        const res = await this._fetch(`${API}/knowledge-base/ingest/jobs?limit=50`);
        const data = await res.json();
        const jobs = data.jobs || [];
        this.dashboardJobs = jobs;
        this.dashboardStats = {
          total: jobs.length,
          running: jobs.filter(j => j.status === 'running' || j.status === 'pending').length,
          completed: jobs.filter(j => j.status === 'completed').length,
          failed: jobs.filter(j => j.status === 'failed').length,
          cancelled: jobs.filter(j => j.status === 'cancelled').length,
        };
      } catch (e) { console.error('load dashboard', e); }
    },

    get dashboardRunningJobs() {
      return this.dashboardJobs.filter(j => j.status === 'running' || j.status === 'pending');
    },

    formatJobTime(ts) {
      if (!ts) return '-';
      const d = new Date(ts * 1000);
      const now = new Date();
      const diff = Math.floor((now - d) / 1000);
      if (diff < 60) return diff + '秒前';
      if (diff < 3600) return Math.floor(diff / 60) + '分钟前';
      if (diff < 86400) return Math.floor(diff / 3600) + '小时前';
      return d.toLocaleString();
    },

    async cancelDashboardJob(jobId) {
      if (!confirm('确定终止该任务?')) return;
      try {
        await this._fetch(`${API}/knowledge-base/ingest/jobs/${jobId}/cancel`, { method: 'POST' });
        this.showToast('已发送终止请求');
        await this.loadDashboard();
      } catch (e) { this.showToast('终止失败', 'error'); }
    },

    // --- Model Configs ---
    modelConfigs: [],
    activeModelId: '',
    showModelEditor: false,
    editingModel: { model_id: '', name: '', base_url: '', api_key: '', api_id: '', model_name: '', temperature: 0.5, max_tokens: 4096, top_p: 0.9, system_prompt: '' },

    async loadModelConfigs() {
      try {
        const res = await this._fetch(`${API}/model-configs`);
        if (res.ok) {
          const data = await res.json();
          this.modelConfigs = data.models || [];
          this.activeModelId = data.active_id || '';
        }
      } catch (e) { console.error('load model configs', e); }
    },

    openModelEditor(model) {
      if (model) {
        this.editingModel = { ...model, api_key: '', _custom_model: '', _is_custom: false };
        const knownModels = [
          'moonshot-v1-8k','moonshot-v1-32k','moonshot-v1-128k','kimi-k2',
          'deepseek-chat','deepseek-reasoner','deepseek-v3','deepseek-r1',
          'gpt-4o','gpt-4.1','gpt-4.1-mini','o3','o4-mini',
          'qwen-turbo','qwen-plus','qwen-max','qwen3-235b-a22b',
          'glm-4-plus','glm-4-flash','glm-4-air','glm-4-airx',
          'ernie-4.5','ernie-4.0-turbo','ernie-speed-pro',
          'claude-sonnet-4-20250514','claude-3.5-sonnet','claude-3-haiku','claude-3-opus',
          'gemini-2.5-flash','gemini-2.5-pro','gemini-1.5-pro',
          'qwen3:14b','qwen3:8b','llama4','phi4',
          'mistral','deepseek-r1:8b'
        ];
        if (model.model_name && !knownModels.includes(model.model_name)) {
          this.editingModel._custom_model = model.model_name;
          this.editingModel.model_name = '__custom__';
          this.editingModel._is_custom = true;
        }
      } else {
        this.editingModel = { model_id: '', name: '', base_url: '', api_key: '', api_id: '', model_name: '', temperature: 0.5, max_tokens: 4096, top_p: 0.9, system_prompt: '', _custom_model: '' };
      }
      this.showModelEditor = true;
    },

    async saveModel() {
      try {
        const payload = { ...this.editingModel };
        // Resolve custom model name before cleanup
        if (payload.model_name === '__custom__' && payload._custom_model) {
          payload.model_name = payload._custom_model;
        }
        delete payload._custom_model;
        // Don't send empty/masked api_key
        if (!payload.api_key || payload.api_key.includes('...')) {
          delete payload.api_key;
        }
        let res;
        if (this.editingModel.model_id) {
          res = await this._fetch(`${API}/model-configs/${this.editingModel.model_id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ config: payload })
          });
        } else {
          res = await this._fetch(`${API}/model-configs`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: payload.name, config: payload })
          });
        }
        const data = await res.json();
        if (data.success) {
          this.showToast(this.editingModel.model_id ? '模型配置已更新' : '模型配置已创建');
          this.showModelEditor = false;
          await this.loadModelConfigs();
        } else {
          this.showToast(data.detail || '保存失败', 'error');
        }
      } catch (e) { this.showToast('保存失败', 'error'); }
    },

    async activateModel(modelId) {
      try {
        const res = await this._fetch(`${API}/model-configs/${modelId}/activate`, { method: 'POST' });
        const data = await res.json();
        if (data.success) {
          this.activeModelId = modelId;
          this.showToast('模型已激活并热重载');
          await this.loadModelConfigs();
          await this.loadConfig();
        } else {
          this.showToast(data.detail || '激活失败', 'error');
        }
      } catch (e) { this.showToast('激活失败', 'error'); }
    },

    async deleteModel(modelId) {
      if (!confirm('确定删除该模型配置?')) return;
      try {
        const res = await this._fetch(`${API}/model-configs/${modelId}`, { method: 'DELETE' });
        const data = await res.json();
        if (data.success) {
          this.showToast('模型配置已删除');
          await this.loadModelConfigs();
        } else {
          this.showToast(data.detail || '删除失败', 'error');
        }
      } catch (e) { this.showToast('删除失败', 'error'); }
    }
  };
}
