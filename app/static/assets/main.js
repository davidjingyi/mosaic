const API = '/api/v1';

// Navigation
const navBtns = document.querySelectorAll('.nav-btn');
const views = document.querySelectorAll('.view');
navBtns.forEach(btn => {
  btn.addEventListener('click', () => {
    navBtns.forEach(b => b.classList.remove('active'));
    views.forEach(v => v.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('view-' + btn.dataset.view).classList.add('active');
    if (btn.dataset.view === 'docs') loadDocs();
    if (btn.dataset.view === 'config') loadConfig();
  });
});

// Chat
const chatMessages = document.getElementById('chat-messages');
const chatInput = document.getElementById('chat-input');
const btnSend = document.getElementById('btn-send');
let history = [];

function appendMessage(role, text, sources) {
  const div = document.createElement('div');
  div.className = 'message ' + role;
  div.innerHTML = '<div class="text"></div>';
  if (sources && sources.length) {
    div.innerHTML += '<div class="sources">来源: ' + sources.map(s => s.metadata?.source || '?').join(', ') + '</div>';
  }
  div.querySelector('.text').textContent = text;
  chatMessages.appendChild(div);
  chatMessages.scrollTop = chatMessages.scrollHeight;
  return div.querySelector('.text');
}

async function sendMessage() {
  const text = chatInput.value.trim();
  if (!text) return;
  chatInput.value = '';
  appendMessage('user', text);
  btnSend.disabled = true;

  try {
    const res = await fetch(`${API}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: text, history, stream: false })
    });
    const data = await res.json();
    if (data.error) {
      appendMessage('assistant', '错误: ' + data.error);
    } else {
      appendMessage('assistant', data.answer || data.chunk || '', data.sources);
      history.push({ role: 'user', content: text });
      history.push({ role: 'assistant', content: data.answer || data.chunk || '' });
      if (history.length > 20) history = history.slice(-20);
    }
  } catch (e) {
    appendMessage('assistant', '请求失败: ' + e.message);
  }
  btnSend.disabled = false;
}

btnSend.addEventListener('click', sendMessage);
chatInput.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); } });

// Documents
const uploadArea = document.getElementById('upload-area');
const fileInput = document.getElementById('file-input');

uploadArea.addEventListener('dragover', e => { e.preventDefault(); uploadArea.style.borderColor = '#00d4aa'; });
uploadArea.addEventListener('dragleave', () => { uploadArea.style.borderColor = '#e0e0e0'; });
uploadArea.addEventListener('drop', e => {
  e.preventDefault();
  uploadArea.style.borderColor = '#e0e0e0';
  if (e.dataTransfer.files.length) uploadFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener('change', () => { if (fileInput.files.length) uploadFile(fileInput.files[0]); });

async function uploadFile(file) {
  const form = new FormData();
  form.append('file', file);
  try {
    const res = await fetch(`${API}/documents/upload`, { method: 'POST', body: form });
    const data = await res.json();
    alert(data.message || '上传成功');
    loadDocs();
  } catch (e) {
    alert('上传失败: ' + e.message);
  }
}

async function loadDocs() {
  const list = document.getElementById('doc-list');
  list.innerHTML = '<p>加载中...</p>';
  try {
    const res = await fetch(`${API}/documents`);
    const data = await res.json();
    list.innerHTML = '';
    if (!data.length) { list.innerHTML = '<p>暂无文档</p>'; return; }
    data.forEach(doc => {
      const div = document.createElement('div');
      div.className = 'doc-item';
      div.innerHTML = `<span>${doc.filename} (${doc.chunk_count} chunks)</span><button data-id="${doc.doc_id}">删除</button>`;
      list.appendChild(div);
    });
    list.querySelectorAll('button').forEach(btn => {
      btn.addEventListener('click', async () => {
        if (!confirm('确定删除?')) return;
        await fetch(`${API}/documents/${btn.dataset.id}`, { method: 'DELETE' });
        loadDocs();
      });
    });
  } catch (e) {
    list.innerHTML = '<p>加载失败</p>';
  }
}

// Retrieve debug
const btnRetrieve = document.getElementById('btn-retrieve');
btnRetrieve.addEventListener('click', async () => {
  const q = document.getElementById('retrieve-query').value.trim();
  if (!q) return;
  const pre = document.getElementById('retrieve-result');
  pre.textContent = '检索中...';
  try {
    const res = await fetch(`${API}/retrieve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: q })
    });
    const data = await res.json();
    pre.textContent = JSON.stringify(data, null, 2);
  } catch (e) {
    pre.textContent = '请求失败: ' + e.message;
  }
});

// Config
async function loadConfig() {
  const pre = document.getElementById('config-display');
  try {
    const res = await fetch(`${API}/config`);
    const data = await res.json();
    pre.textContent = JSON.stringify(data, null, 2);
  } catch (e) {
    pre.textContent = '加载失败: ' + e.message;
  }
}
