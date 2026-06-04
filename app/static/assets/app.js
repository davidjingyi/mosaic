// Auto-detect base path (works for /oncorag/ or root)
var _pathBase = window.location.pathname.startsWith('/oncorag') ? '/oncorag' : '';
const API = _pathBase + '/api/v1';

const app = {
  sessionId: null,
  sessions: [],
  messages: [],
  isStreaming: false,
  isMobile: false,
  selectedPrompt: '',
  availablePrompts: [],

  // DOM refs (computed dynamically based on viewport)
  get elSessionList() {
    return document.getElementById(this.isMobile ? 'mobile-session-list' : 'desktop-session-list');
  },
  get elChatMessages() {
    return document.getElementById(this.isMobile ? 'mobile-chat-messages' : 'desktop-chat-messages');
  },
  get elChatInput() {
    return document.getElementById(this.isMobile ? 'mobile-chat-input' : 'desktop-chat-input');
  },
  get elBtnSend() {
    return document.getElementById(this.isMobile ? 'mobile-btn-send' : 'desktop-btn-send');
  },
  get elCurrentTitle() {
    return document.getElementById(this.isMobile ? 'mobile-current-title' : 'desktop-current-title');
  },
  get elMsgCount() {
    return document.getElementById('desktop-msg-count');
  },

  async init() {
    this.checkViewport();
    window.addEventListener('resize', () => {
      const wasMobile = this.isMobile;
      this.checkViewport();
      if (wasMobile !== this.isMobile) {
        // Viewport crossed breakpoint — re-render
        this.renderSessionList();
        this.renderMessages();
        this.updateHeader(this.sessions.find(s => s.session_id === this.sessionId)?.title || '新会话');
      }
    });

    // Mobile keyboard handling
    if ('visualViewport' in window) {
      window.visualViewport.addEventListener('resize', () => this.handleKeyboard());
    }

    // Auto-resize textarea
    [document.getElementById('mobile-chat-input'), document.getElementById('desktop-chat-input')].forEach(el => {
      if (!el) return;
      el.addEventListener('input', () => {
        el.style.height = 'auto';
        el.style.height = Math.min(el.scrollHeight, 120) + 'px';
      });
    });

    // Enter to send (both mobile & desktop)
    ['mobile-chat-input', 'desktop-chat-input'].forEach(id => {
      const el = document.getElementById(id);
      if (el) {
        el.addEventListener('keydown', e => {
          if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); app.send(); }
        });
      }
    });

    await this.loadSessions();
    if (!this.sessions.length) {
      await this.newSession();
    } else {
      this.selectSession(this.sessions[0].session_id);
    }
    this.loadPrompts();
  },

  async loadPrompts() {
    try {
      const res = await fetch(`${API}/prompts/public`);
      if (res.ok) {
        const data = await res.json();
        this.availablePrompts = data.prompts || [];
        this.renderPromptSelector();
      }
    } catch (e) { console.error('loadPrompts', e); }
  },

  setPrompt(id) {
    this.selectedPrompt = id;
    // Sync both desktop and mobile selectors
    document.querySelectorAll('.prompt-select').forEach(el => { el.value = id; });
  },

  renderPromptSelector() {
    const defaultOption = '<option value="">默认 Prompt</option>';
    const options = this.availablePrompts.map(p => 
      `<option value="${p.id}" ${p.is_default ? 'selected' : ''}>${p.name}</option>`
    ).join('');
    if (!this.selectedPrompt) {
      const def = this.availablePrompts.find(p => p.is_default);
      if (def) this.selectedPrompt = def.id;
    }
    document.querySelectorAll('.prompt-select').forEach(el => {
      el.innerHTML = defaultOption + options;
      el.value = this.selectedPrompt || '';
    });
  },

  checkViewport() {
    this.isMobile = window.innerWidth < 768;
  },

  handleKeyboard() {
    if (!this.isMobile) return;
    const inputArea = document.getElementById('mobile-input-area');
    const messagesArea = document.getElementById('mobile-chat-messages');
    if (!inputArea || !messagesArea) return;

    const vv = window.visualViewport;
    const offset = window.innerHeight - vv.height - vv.offsetTop;

    if (offset > 60) {
      // Keyboard likely open
      inputArea.style.bottom = offset + 'px';
      messagesArea.style.bottom = (offset + inputArea.offsetHeight) + 'px';
    } else {
      // Keyboard closed
      inputArea.style.bottom = '0';
      messagesArea.style.bottom = (inputArea.offsetHeight || 72) + 'px';
    }
  },

  // Drawer (mobile)
  toggleDrawer() {
    const overlay = document.getElementById('drawer-overlay');
    const panel = document.getElementById('drawer-panel');
    const isOpen = overlay.classList.contains('open');
    if (isOpen) {
      this.closeDrawer();
    } else {
      overlay.classList.add('open');
      panel.classList.add('open');
      document.body.style.overflow = 'hidden';
    }
  },

  closeDrawer() {
    const overlay = document.getElementById('drawer-overlay');
    const panel = document.getElementById('drawer-panel');
    overlay.classList.remove('open');
    panel.classList.remove('open');
    document.body.style.overflow = '';
  },

  async loadSessions() {
    try {
      const res = await fetch(`${API}/sessions?limit=50`);
      this.sessions = await res.json();
      this.renderSessionList();
    } catch (e) {
      console.error('load sessions failed', e);
    }
  },

  renderSessionList() {
    const el = this.elSessionList;
    if (!el) return;
    if (!this.sessions.length) {
      el.innerHTML = '<p class="text-xs text-gray-400 text-center mt-4">暂无会话</p>';
      return;
    }
    el.innerHTML = this.sessions.map(s => `
      <div onclick="app.selectSession('${s.session_id}')${this.isMobile ? '; app.closeDrawer()' : ''}"
           class="cursor-pointer p-3 rounded-lg text-sm transition ${s.session_id === this.sessionId ? 'bg-emerald-50 border border-emerald-200' : 'hover:bg-gray-50 border border-transparent'}">
        <div class="font-medium text-gray-800 truncate">${this.escapeHtml(s.title || '未命名会话')}</div>
        <div class="text-xs text-gray-400 mt-1">${s.messages?.length || 0} 条消息</div>
      </div>
    `).join('');
  },

  async newSession() {
    try {
      const res = await fetch(`${API}/sessions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: '新会话' })
      });
      const sess = await res.json();
      this.sessionId = sess.session_id;
      this.messages = [];
      await this.loadSessions();
      this.renderMessages();
      this.updateHeader('新会话');
    } catch (e) {
      alert('创建会话失败: ' + e.message);
    }
  },

  selectSession(id) {
    const sess = this.sessions.find(s => s.session_id === id);
    if (!sess) return;
    this.sessionId = id;
    this.messages = sess.messages || [];
    this.renderSessionList();
    this.renderMessages();
    this.updateHeader(sess.title || '新会话');
  },

  updateHeader(title) {
    const titleEl = this.elCurrentTitle;
    if (titleEl) titleEl.textContent = title;
    const countEl = this.elMsgCount;
    if (countEl) countEl.textContent = `${this.messages.length} 条消息`;
  },

  renderMessages() {
    const el = this.elChatMessages;
    if (!el) return;

    if (!this.messages.length) {
      if (this.isMobile) {
        el.innerHTML = `
          <div class="welcome-box">
            <div class="text-4xl mb-3">👋</div>
            <h3>欢迎来到肿瘤智能答疑系统</h3>
            <p>请在下方输入您的问题，我将尽力为您解答</p>
            <div style="margin-top:16px;padding:10px 14px;background:#fef3c7;border-radius:8px;border:1px solid #fcd34d;display:inline-block;max-width:360px">
              <p style="font-size:12px;color:#92400e;margin:0;line-height:1.6;text-align:center">
                ⚠️ <strong>免责声明：</strong>AI辅助答疑仅供参考，不能代替专业医疗诊断、治疗和建议。如有健康问题，请及时就医。
              </p>
              <p style="font-size:12px;color:#92400e;margin:4px 0 0 0;line-height:1.6;text-align:center">
                📋 本网页功能仅供测试使用，不作为任何意见建议。
              </p>
            </div>
          </div>`;
      } else {
        el.innerHTML = '<div class="text-center text-gray-400 text-sm mt-10">👋 欢迎来到肿瘤智能答疑系统<br>请在下方输入您的问题<div style="margin-top:16px;padding:10px 14px;background:#fef3c7;border-radius:8px;border:1px solid #fcd34d;display:inline-block;max-width:480px"><p style="font-size:12px;color:#92400e;margin:0;line-height:1.6;text-align:center">⚠️ <strong>免责声明：</strong>AI辅助答疑仅供参考，不能代替专业医疗诊断、治疗和建议。如有健康问题，请及时就医。</p><p style="font-size:12px;color:#92400e;margin:4px 0 0 0;line-height:1.6;text-align:center">📋 本网页功能仅供测试使用，不作为任何意见建议。</p></div></div>';
      }
      return;
    }

    el.innerHTML = this.messages.map(m => {
      let sourcesHtml = '';
      if (m.role === 'assistant' && m.sources?.length) {
        sourcesHtml = `<div class="mt-2 pt-2 border-t border-gray-200/50">
          <div class="text-xs text-gray-500 mb-1">📚 参考来源</div>
          <div class="space-y-1">` +
          m.sources.map((s, i) => `
            <div class="bg-white/60 rounded px-2 py-1 text-xs text-gray-600">
              <span class="font-medium">[${i + 1}]</span> ${this.escapeHtml(s.metadata?.doc_title || s.metadata?.source || '未知文档')}
              <span class="text-gray-400">(score: ${(s.score || 0).toFixed(2)})</span>
            </div>
          `).join('') +
          `</div></div>`;
      }

      const maxW = this.isMobile ? 'max-w-[88%]' : 'max-w-[80%]';
      const bubbleCls = m.role === 'user' ? 'chat-bubble-user' : 'chat-bubble-assistant';
      const align = m.role === 'user' ? 'justify-end' : 'justify-start';

      return `
      <div class="flex ${align}">
        <div class="${maxW} px-4 py-3 text-sm ${bubbleCls} whitespace-pre-wrap shadow-sm">
          ${this.escapeHtml(m.content)}
          ${sourcesHtml}
        </div>
      </div>
      `;
    }).join('');

    el.scrollTop = el.scrollHeight;
  },

  escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  },

  async send() {
    const input = this.elChatInput;
    if (!input) return;
    const text = input.value.trim();
    if (!text || this.isStreaming) return;
    if (!this.sessionId) await this.newSession();

    // Reset textarea height
    input.value = '';
    input.style.height = 'auto';
    if (this.isMobile) {
      input.style.height = '44px';
    }

    this.messages.push({ role: 'user', content: text });
    this.renderMessages();
    this.updateHeader(this.sessions.find(s => s.session_id === this.sessionId)?.title || '新会话');

    const btn = this.elBtnSend;
    if (btn) {
      btn.disabled = true;
      btn.textContent = '生成中...';
    }
    this.isStreaming = true;

    // Add assistant placeholder
    this.messages.push({ role: 'assistant', content: '' });
    const assistantIdx = this.messages.length - 1;

    // Throttle rendering during streaming (requestAnimationFrame)
    let pendingRender = false;
    const scheduleRender = () => {
      if (pendingRender) return;
      pendingRender = true;
      requestAnimationFrame(() => {
        this.renderMessages();
        pendingRender = false;
      });
    };

    try {
      const res = await fetch(`${API}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + (_authToken || '') },
        body: JSON.stringify({ query: text, stream: true, session_id: this.sessionId, prompt_id: this.selectedPrompt || null })
      });

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let assistantText = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });
        for (const line of chunk.split('\n')) {
          const trimmed = line.trim();
          if (!trimmed || !trimmed.startsWith('data: ')) continue;
          try {
            const data = JSON.parse(trimmed.slice(6));
            if (data.chunk) {
              assistantText += data.chunk;
              this.messages[assistantIdx].content = assistantText;
              scheduleRender();
            }
            if (data.done) {
              if (data.sources?.length) {
                this.messages[assistantIdx].sources = data.sources;
              }
              // Final render to show sources
              this.renderMessages();
            }
          } catch (e) {}
        }
      }

      // Backend now auto-saves sessions after stream completes; refresh list only
      await this.loadSessions();

    } catch (e) {
      this.messages[assistantIdx].content = '请求失败: ' + e.message;
      this.renderMessages();
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = '发送';
      }
      this.isStreaming = false;
    }
  }
};

app.init();
