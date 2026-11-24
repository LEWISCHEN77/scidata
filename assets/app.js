'use strict';

const enc = new TextEncoder();
const dec = new TextDecoder();
const $ = (sel) => document.querySelector(sel);

const VAULT_URL = 'data/vault.json';
const REMEMBER_KEY = 'sdata.pw';

const state = {
  root: null,
  catalog: null,
  cache: new Map(),
  query: '',
};

/* ---------- crypto ---------- */

function b64ToBytes(b64) {
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
}

async function deriveRoot(password, salt, iterations) {
  const base = await crypto.subtle.importKey(
    'raw', enc.encode(password), 'PBKDF2', false, ['deriveBits']
  );
  const bits = await crypto.subtle.deriveBits(
    { name: 'PBKDF2', salt, iterations, hash: 'SHA-256' }, base, 256
  );
  return crypto.subtle.importKey('raw', bits, 'HKDF', false, ['deriveKey']);
}

async function subKey(root, label) {
  return crypto.subtle.deriveKey(
    { name: 'HKDF', hash: 'SHA-256', salt: enc.encode(label), info: enc.encode('sdata-v1') },
    root,
    { name: 'AES-GCM', length: 256 },
    false,
    ['decrypt']
  );
}

async function unseal(root, label, buffer) {
  const bytes = new Uint8Array(buffer);
  if (bytes.length < 16 || dec.decode(bytes.subarray(0, 3)) !== 'SD1') {
    throw new Error('format');
  }
  const iv = bytes.subarray(3, 15);
  const data = bytes.subarray(15);
  return crypto.subtle.decrypt({ name: 'AES-GCM', iv }, await subKey(root, label), data);
}

async function fetchBuffer(url) {
  const res = await fetch(url, { cache: 'no-store' });
  if (!res.ok) throw new Error('HTTP ' + res.status);
  return res.arrayBuffer();
}

/* ---------- unlock ---------- */

async function unlock(password) {
  const vaultRes = await fetch(VAULT_URL, { cache: 'no-store' });
  if (!vaultRes.ok) throw new Error('HTTP ' + vaultRes.status);
  const vault = await vaultRes.json();

  const root = await deriveRoot(
    password, b64ToBytes(vault.salt), vault.iterations || 310000
  );
  const plain = await unseal(root, 'catalog', await fetchBuffer(vault.catalog));
  const catalog = JSON.parse(dec.decode(plain));

  state.root = root;
  state.catalog = catalog;
  state.cache.clear();
  return catalog;
}

function lock() {
  state.root = null;
  state.catalog = null;
  state.cache.clear();
  state.query = '';
  $('#q').value = '';
  $('#app').hidden = true;
  $('#gate').hidden = false;
  $('#pw').value = '';
  $('#gate-msg').textContent = '';
  sessionStorage.removeItem(REMEMBER_KEY);
  $('#pw').focus();
}

/* ---------- rendering ---------- */

function fmtSize(bytes) {
  if (!bytes) return '';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(0) + ' KB';
  return (bytes / 1024 / 1024).toFixed(1) + ' MB';
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function button(label, className, onClick) {
  const b = el('button', className, label);
  b.type = 'button';
  b.addEventListener('click', onClick);
  return b;
}

function matches(item) {
  if (!state.query) return true;
  const hay = [item.title, item.note, item.url].filter(Boolean).join(' ').toLowerCase();
  return hay.includes(state.query);
}

function pdfCard(item) {
  const card = el('div', 'card');
  card.appendChild(el('h3', null, item.title));

  const meta = [item.note, fmtSize(item.size)].filter(Boolean).join(' · ');
  if (meta) card.appendChild(el('p', 'meta', meta));

  const row = el('div', 'row');
  row.appendChild(button('開啟', 'primary', () => openPdf(item)));
  row.appendChild(button('下載', 'ghost', () => downloadPdf(item)));
  card.appendChild(row);
  return card;
}

function linkCard(item) {
  const card = el('div', 'card');
  card.appendChild(el('h3', null, item.title));

  const meta = el('p', 'meta');
  let host = item.url;
  try { host = new URL(item.url).host.replace(/^www\./, ''); } catch { /* keep raw */ }
  meta.appendChild(el('span', 'link-host', host));
  if (item.note) {
    meta.appendChild(document.createTextNode(' · ' + item.note));
  }
  card.appendChild(meta);

  const row = el('div', 'row');
  const open = button('前往', 'ghost', () => {
    window.open(item.url, '_blank', 'noopener,noreferrer');
  });
  row.appendChild(open);
  card.appendChild(row);
  return card;
}

function render() {
  const items = state.catalog?.items || [];
  const visible = items.filter(matches);

  const pdfList = $('#pdf-list');
  const linkList = $('#link-list');
  pdfList.textContent = '';
  linkList.textContent = '';

  const pdfs = visible.filter((i) => i.type === 'pdf');
  const links = visible.filter((i) => i.type === 'link');

  pdfs.forEach((item) => pdfList.appendChild(pdfCard(item)));
  links.forEach((item) => linkList.appendChild(linkCard(item)));

  const totalPdfs = items.filter((i) => i.type === 'pdf').length;
  const totalLinks = items.filter((i) => i.type === 'link').length;

  $('#pdf-count').textContent = totalPdfs ? `${pdfs.length}/${totalPdfs}` : '';
  $('#link-count').textContent = totalLinks ? `${links.length}/${totalLinks}` : '';

  $('#pdf-empty').hidden = pdfs.length > 0;
  $('#pdf-empty').textContent = totalPdfs ? '沒有符合搜尋的文件' : '尚無文件';
  $('#link-empty').hidden = links.length > 0;
  $('#link-empty').textContent = totalLinks ? '沒有符合搜尋的連結' : '尚無連結';

  if (state.catalog?.updated) {
    const d = new Date(state.catalog.updated);
    $('#updated').textContent = isNaN(d) ? '' : '更新於 ' + d.toLocaleString('zh-TW', { hour12: false });
  }
}

/* ---------- pdf ---------- */

async function loadPdf(item) {
  if (state.cache.has(item.id)) return state.cache.get(item.id);
  const buffer = await fetchBuffer(item.file);
  const data = await unseal(state.root, 'file:' + item.id, buffer);
  const url = URL.createObjectURL(new Blob([data], { type: 'application/pdf' }));
  state.cache.set(item.id, url);
  return url;
}

function blobAnchor(url, filename, download) {
  const a = document.createElement('a');
  a.href = url;
  if (download) {
    a.download = filename;
  } else {
    a.target = '_blank';
    a.rel = 'noopener';
  }
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function safeName(title) {
  return (title || 'document').replace(/[\\/:*?"<>|]+/g, '_').slice(0, 80) + '.pdf';
}

async function openPdf(item) {
  try {
    blobAnchor(await loadPdf(item), safeName(item.title), false);
  } catch (err) {
    console.error(err);
    alert('無法開啟此文件：' + err.message);
  }
}

async function downloadPdf(item) {
  try {
    blobAnchor(await loadPdf(item), safeName(item.title), true);
  } catch (err) {
    console.error(err);
    alert('無法下載此文件：' + err.message);
  }
}

/* ---------- events ---------- */

$('#unlock-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const btn = $('#unlock-btn');
  const msg = $('#gate-msg');
  const password = $('#pw').value;

  if (!password) {
    msg.textContent = '請輸入密碼';
    return;
  }

  btn.disabled = true;
  msg.textContent = '解碼中…';

  try {
    await unlock(password);
    if ($('#remember').checked) sessionStorage.setItem(REMEMBER_KEY, password);
    msg.textContent = '';
    $('#gate').hidden = true;
    $('#app').hidden = false;
    render();
    $('#q').focus();
  } catch (err) {
    console.error(err);
    msg.textContent = err.message === 'format'
      ? '資料格式錯誤，請確認檔案完整'
      : '密碼不正確';
    $('#pw').select();
  } finally {
    btn.disabled = false;
  }
});

$('#toggle-pw').addEventListener('click', () => {
  const input = $('#pw');
  const show = input.type === 'password';
  input.type = show ? 'text' : 'password';
  $('#toggle-pw').textContent = show ? '隱藏' : '顯示';
  input.focus();
});

$('#q').addEventListener('input', (event) => {
  state.query = event.target.value.trim().toLowerCase();
  render();
});

$('#lock').addEventListener('click', lock);

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !$('#app').hidden) lock();
});

/* ---------- boot ---------- */

(function boot() {
  const saved = sessionStorage.getItem(REMEMBER_KEY);
  if (!saved) return;
  unlock(saved)
    .then(() => {
      $('#gate').hidden = true;
      $('#app').hidden = false;
      $('#remember').checked = true;
      render();
    })
    .catch(() => sessionStorage.removeItem(REMEMBER_KEY));
})();
