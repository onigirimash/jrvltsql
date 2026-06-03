'use strict';

// ============================================================
// State & Constants
// ============================================================
let C = {};   // constants from /api/constants
let state = { page: 'home', kaishiId: null, tab: 'track', raceId: null };

// ============================================================
// API helpers
// ============================================================
const api = {
  async _req(method, url, body) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body !== undefined) opts.body = JSON.stringify(body);
    const res = await fetch(url, opts);
    if (res.status === 204) return null;
    const data = await res.json();
    if (!res.ok) {
      const detail = data.detail;
      const msg = typeof detail === 'string' ? detail : (detail?.message || `HTTP ${res.status}`);
      const err = new Error(msg);
      err.detail = detail;
      throw err;
    }
    return data;
  },
  get: (url)        => api._req('GET',    url),
  post: (url, body) => api._req('POST',   url, body),
  put:  (url, body) => api._req('PUT',    url, body),
  del:  (url)       => api._req('DELETE', url),
};

// ============================================================
// Toast
// ============================================================
function toast(msg, type = 'success') {
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.textContent = msg;
  document.getElementById('toasts').appendChild(el);
  setTimeout(() => el.remove(), 3000);
}

// ============================================================
// Router
// ============================================================
function navigate(page, kaishiId = null, tab = 'track', raceId = null) {
  state = { page, kaishiId, tab, raceId };
  render();
}

async function render() {
  const main = document.getElementById('main');
  main.innerHTML = '<div class="empty">読み込み中...</div>';
  try {
    if (state.page === 'home')   await renderHome();
    if (state.page === 'kaishi') await renderKaishi();
  } catch (e) {
    main.innerHTML = `<div class="empty">エラー: ${e.message}</div>`;
  }
}

// ============================================================
// Home Page — 日付グループ表示
// ============================================================
async function renderHome() {
  const list = await api.get('/api/kaishi');
  const main = document.getElementById('main');
  document.getElementById('btn-back').classList.remove('visible');

  // 日付でグループ化（API は race_date DESC 順で返ってくる）
  const byDate = new Map();
  for (const k of list) {
    const d = String(k.race_date).slice(0, 10);
    if (!byDate.has(d)) byDate.set(d, []);
    byDate.get(d).push(k);
  }

  let content = '';
  if (!byDate.size) {
    content = '<div class="empty">開催記録がありません。「＋ 新規開催」から追加してください。</div>';
  } else {
    for (const [date, venues] of byDate) {
      const venueCards = venues.map(k => `
        <div class="venue-card" onclick="navigate('kaishi',${k.id})">
          <div class="venue-card-name">${k.jyo_name}</div>
          <div class="venue-card-meta">${k.kaiji ? `第${k.kaiji}回` : ''}${k.nichiji ? `&nbsp;${k.nichiji}日目` : ''}</div>
          <div><span class="badge">${k.race_count}R入力済</span></div>
        </div>`).join('');

      content += `
        <div class="date-group">
          <div class="date-group-header">${fmtDate(date)}</div>
          <div class="date-group-venues">${venueCards}</div>
        </div>`;
    }
  }

  main.innerHTML = `
    <div class="section-header">
      <h2>開催一覧</h2>
      <button class="btn btn-accent" onclick="openCreateKaishiModal()">＋ 新規開催</button>
    </div>
    ${content}`;
}

// ============================================================
// Create Kaishi Modal — 日付選択 → nl_ra から競馬場自動取得
// ============================================================
let _venuesForDate = [];

function openCreateKaishiModal() {
  _venuesForDate = [];
  showModal(`
    <div class="modal-title">新規開催を追加</div>
    <div class="field" style="margin-bottom:.75rem">
      <label>開催日 *</label>
      <div style="display:flex;gap:.5rem;align-items:flex-end">
        <input type="date" id="m-date" value="${todayStr()}" style="flex:1">
        <button class="btn btn-accent btn-sm" onclick="fetchVenuesForDate()">競馬場を取得</button>
      </div>
    </div>
    <div id="venue-list-area"></div>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="closeModal()">キャンセル</button>
      <button class="btn btn-primary" id="btn-create-bulk" style="display:none" onclick="submitBulkCreateKaishi()">作成</button>
    </div>`);
}

async function fetchVenuesForDate() {
  const date = document.getElementById('m-date').value;
  if (!date) { toast('開催日を選択してください', 'error'); return; }

  const area = document.getElementById('venue-list-area');
  area.innerHTML = '<div style="padding:.5rem 0;color:var(--text-muted);font-size:.85rem">取得中...</div>';

  try {
    const venues = await api.get(`/api/kaishi/venues-for-date?date=${date}`);
    _venuesForDate = venues;

    if (!venues.length) {
      area.innerHTML = `
        <div class="empty" style="padding:.75rem 0;font-size:.85rem">
          この日の開催情報がDBに見つかりませんでした
        </div>`;
      document.getElementById('btn-create-bulk').style.display = 'none';
      return;
    }

    const rows = venues.map((v, i) => `
      <label class="venue-check-row">
        <input type="checkbox" id="vc-${i}" checked>
        <span class="venue-check-name">${v.jyo_name}</span>
        <span class="venue-check-meta">${v.kaiji ? `第${v.kaiji}回` : ''}${v.nichiji ? `&nbsp;${v.nichiji}日目` : ''}</span>
      </label>`).join('');

    area.innerHTML = `
      <div class="divider"></div>
      <div style="font-size:.8rem;font-weight:700;margin-bottom:.5rem;color:var(--text-muted)">
        開催競馬場（チェックで作成）
      </div>
      <div class="venue-check-list">${rows}</div>`;

    document.getElementById('btn-create-bulk').style.display = '';
  } catch (e) {
    area.innerHTML = '';
    toast(e.message, 'error');
  }
}

async function submitBulkCreateKaishi() {
  const date = document.getElementById('m-date').value;
  if (!date) { toast('開催日を選択してください', 'error'); return; }

  const selected = _venuesForDate.filter((_, i) => {
    const chk = document.getElementById(`vc-${i}`);
    return chk && chk.checked;
  });
  if (!selected.length) { toast('競馬場を1つ以上選択してください', 'error'); return; }

  try {
    const result = await api.post('/api/kaishi/bulk', {
      race_date: date,
      venues: selected.map(v => ({ jyo_cd: v.jyo_cd, kaiji: v.kaiji || null, nichiji: v.nichiji || null })),
    });
    closeModal();
    const n = result.length;
    const skipped = selected.length - n;
    let msg = `${n}件の開催を作成しました`;
    if (skipped > 0) msg += `（${skipped}件は既存のためスキップ）`;
    toast(msg);
    navigate('home');
  } catch (e) { toast(e.message, 'error'); }
}

// ============================================================
// Kaishi Detail Page
// ============================================================
async function renderKaishi() {
  const k = await api.get(`/api/kaishi/${state.kaishiId}`);

  // バイアスタブのときだけペース統計を取得
  let paceStats = [];
  if (state.tab === 'bias') {
    try { paceStats = await api.get(`/api/kaishi/${state.kaishiId}/pace-stats`); } catch {}
  }

  // レースタブでパネルが開いているとき出走馬を取得
  let raceHorses = [];
  if (state.tab === 'races' && state.raceId) {
    try { raceHorses = await api.get(`/api/races/${state.raceId}/horses`); } catch {}
  }

  const main = document.getElementById('main');
  document.getElementById('btn-back').classList.add('visible');

  const tabDefs = [
    { id: 'track',   label: '馬場情報' },
    { id: 'weather', label: '天候' },
    { id: 'bias',    label: 'バイアス' },
    { id: 'races',   label: `レース (${k.races.length})` },
  ];
  const tabs = tabDefs.map(t =>
    `<button class="tab-btn ${state.tab === t.id ? 'active' : ''}"
             onclick="switchTab('${t.id}')">${t.label}</button>`
  ).join('');

  let tabContent = '';
  if (state.tab === 'track')   tabContent = buildTrackTab(k);
  if (state.tab === 'weather') tabContent = buildWeatherTab(k);
  if (state.tab === 'bias')    tabContent = buildBiasTab(k, paceStats);
  if (state.tab === 'races')   tabContent = buildRacesTab(k, raceHorses);

  main.innerHTML = `
    <div class="section-header">
      <h2>${fmtDate(k.race_date)}　${k.jyo_name}</h2>
      <button class="btn btn-ghost btn-sm" onclick="confirmDeleteKaishi(${k.id})">削除</button>
    </div>
    <div class="tabs">${tabs}</div>
    <div id="tab-content">${tabContent}</div>`;

  attachTabListeners(k);
}

function switchTab(tab) {
  state.tab = tab;
  state.raceId = null;
  renderKaishi();
}

// ============================================================
// Track Condition Tab
// ============================================================
function buildTrackTab(k) {
  const existing = {};
  k.track_conditions.forEach(tc => { existing[tc.track_type] = tc; });

  const dateStr = String(k.race_date).slice(0, 10).replace(/-/g, '');
  const venue   = String(k.jyo_cd || '').padStart(2, '0');

  const babaCard = `
    <div class="card">
      <div class="card-title">JRA馬場情報（含水率・クッション値）</div>
      <div id="jra-baba-data" style="color:#666;font-size:.875rem;line-height:1.8;">
        — 未取得 —
      </div>
      <div class="flex-row" style="margin-top:.75rem">
        <button class="btn btn-secondary btn-sm"
                onclick="fetchAndFillBabaInfo('${dateStr}','${venue}')">
          JRAデータ自動入力
        </button>
      </div>
    </div>`;

  const formCards = C.track_types.map(tt => {
    const tc = existing[tt] || {};
    const isTurf = tt === '芝';
    return `
    <div class="card">
      <div class="card-title">${tt}</div>
      <div class="form-grid" id="tc-form-${tt}">
        ${isTurf ? `
        <div class="field">
          <label>クッション値</label>
          <input type="number" step="0.1" min="5" max="15" id="tc-cushion-${tt}"
                 value="${tc.cushion_value ?? ''}" placeholder="例: 9.2">
        </div>` : ''}
        <div class="field">
          <label>含水率 (%)</label>
          <input type="number" step="0.1" min="0" max="100" id="tc-moisture-${tt}"
                 value="${tc.moisture_rate ?? ''}" placeholder="例: 12.5">
        </div>
        <div class="field">
          <label>整備状況</label>
          <input type="text" id="tc-maintenance-${tt}"
                 value="${tc.maintenance_status ?? ''}" placeholder="例: 散水あり">
        </div>
        <div class="field form-full">
          <label>馬場説明</label>
          <textarea id="tc-desc-${tt}" rows="2">${tc.going_description ?? ''}</textarea>
        </div>
      </div>
      <div class="flex-row" style="margin-top:.75rem">
        <button class="btn btn-primary btn-sm" onclick="saveTrackCondition('${tt}')">保存</button>
        ${tc.id ? `<button class="btn btn-ghost btn-sm" onclick="deleteTrackCondition(${tc.id},'${tt}')">削除</button>` : ''}
      </div>
    </div>`;
  }).join('');

  return babaCard + formCards;
}

async function fetchAndFillBabaInfo(date, venue) {
  try {
    const d = await api.get(`/api/baba_info?date=${date}&venue=${venue}`);

    const fmt = v => v !== null && v !== undefined ? v : '−';

    document.getElementById('jra-baba-data').innerHTML =
      `クッション値: <strong>${fmt(d.cushion_value)}</strong> &nbsp;|&nbsp; ` +
      `芝含水率: <strong>${fmt(d.turf_moisture)}%</strong>` +
      `<span style="color:#999;font-size:.8em"> (ゴール${fmt(d.turf_moisture_goal)} / 4C${fmt(d.turf_moisture_4corner)})</span>` +
      ` &nbsp;|&nbsp; ` +
      `ダート含水率: <strong>${fmt(d.dirt_moisture)}%</strong>` +
      `<span style="color:#999;font-size:.8em"> (ゴール${fmt(d.dirt_moisture_goal)} / 4C${fmt(d.dirt_moisture_4corner)})</span>`;

    if (d.cushion_value !== null) {
      const el = document.getElementById('tc-cushion-芝');
      if (el) el.value = d.cushion_value;
    }
    if (d.turf_moisture !== null) {
      const el = document.getElementById('tc-moisture-芝');
      if (el) el.value = d.turf_moisture;
    }
    if (d.dirt_moisture !== null) {
      const el = document.getElementById('tc-moisture-ダート');
      if (el) el.value = d.dirt_moisture;
    }

    toast('JRAデータを自動入力しました');
  } catch (e) {
    const msg = e.message || '';
    if (msg.includes('馬場情報がありません')) {
      toast('この日のJRA馬場データはありません（PDF未公開または取得前）', 'warn');
    } else {
      toast(msg, 'error');
    }
  }
}

async function saveTrackCondition(tt) {
  const body = {
    track_type: tt,
    cushion_value:      numOrNull(`tc-cushion-${tt}`),
    moisture_rate:      numOrNull(`tc-moisture-${tt}`),
    maintenance_status: strOrNull(`tc-maintenance-${tt}`),
    going_description:  strOrNull(`tc-desc-${tt}`),
  };
  try {
    await api.post(`/api/kaishi/${state.kaishiId}/track-condition`, body);
    toast(`${tt}の馬場情報を保存しました`);
    renderKaishi();
  } catch (e) { toast(e.message, 'error'); }
}

async function deleteTrackCondition(id, tt) {
  if (!confirm(`${tt}の馬場情報を削除しますか？`)) return;
  try {
    await api.del(`/api/kaishi/track-condition/${id}`);
    toast('削除しました');
    renderKaishi();
  } catch (e) { toast(e.message, 'error'); }
}

// ============================================================
// Wind assessment helpers
// ============================================================
const _WIND_BADGE_CLASS = {
  '向かい風': 'wind-headwind',
  '追い風':   'wind-tailwind',
  '横風':     'wind-crosswind',
  '無風':     'wind-calm',
};

function windBadge(assessment) {
  if (!assessment) return '';
  const cls = _WIND_BADGE_CLASS[assessment] || '';
  return `<span class="wind-badge ${cls}">${assessment}</span>`;
}

function buildWindSummary(k) {
  if (!k.weathers || !k.weathers.length) return '';

  const w = k.weathers.find(w => (w.measurement_time || '').startsWith('12'))
         || k.weathers[0];
  if (!w) return '';

  const assessment = w.wind_assessment || '無風';
  const dir   = w.wind_direction ?? '—';
  const spd   = w.wind_speed  != null ? `${w.wind_speed}m/s` : '';
  const time  = w.measurement_time ?? '';
  const speed = parseFloat(w.wind_speed || 0);

  let influence = '';
  if (assessment === '無風') {
    influence = '無風または微風のため、風による影響は軽微。';
  } else if (assessment === '向かい風') {
    if (speed >= 5) {
      influence = '強い向かい風。先行馬の消耗が激しく差し・追い込みが有利になりやすい。タイムも重くなる傾向。';
    } else if (speed >= 3) {
      influence = '向かい風。先行馬の消耗に注意。後半の脚の使い方がカギ。';
    } else {
      influence = '弱い向かい風。影響は限定的だが先行馬にわずかに不利。';
    }
  } else if (assessment === '追い風') {
    if (speed >= 5) {
      influence = '強い追い風。好タイムが出やすく先行馬がバテにくい展開になりやすい。';
    } else {
      influence = '追い風。先行馬有利でタイムは速くなりやすい傾向。';
    }
  } else if (assessment === '横風') {
    if (speed >= 5) {
      influence = '強い横風。外側の馬がバランスを崩しやすく内枠・内コース有利になりやすい。';
    } else {
      influence = '横風。強ければ外側の馬への影響に注意。';
    }
  }

  return `
    <div class="card" style="margin-bottom:.75rem">
      <div class="card-title">風の影響（${time} 基準）</div>
      <div style="display:flex;align-items:center;gap:.5rem;margin-bottom:.35rem">
        ${windBadge(assessment)}
        <span style="font-weight:600">${dir}${spd ? '&nbsp;' + spd : ''}</span>
      </div>
      <div style="font-size:.85rem;color:var(--text-muted)">${influence}</div>
    </div>`;
}

// ============================================================
// Weather Tab
// ============================================================
function buildWeatherTab(k) {
  const weatherOpts = C.weather_options.map(w =>
    `<option value="${w}">${w}</option>`).join('');
  const windOpts = ['', ...C.wind_directions].map(d =>
    `<option value="${d}">${d || '選択...'}</option>`).join('');

  const rows = k.weathers.map(w => `
    <tr>
      <td>${w.measurement_time ?? '—'}</td>
      <td>${w.weather_code}</td>
      <td>${w.wind_speed != null ? w.wind_speed + 'm/s' : '—'}</td>
      <td>${w.wind_direction ?? '—'}${w.wind_assessment ? '&nbsp;' + windBadge(w.wind_assessment) : ''}</td>
      <td>${w.temperature != null ? w.temperature + '℃' : '—'}</td>
      <td>${w.precipitation != null ? w.precipitation + 'mm' : '—'}</td>
      <td><button class="btn btn-danger btn-sm btn-icon"
                  onclick="deleteWeather(${w.id})">✕</button></td>
    </tr>`).join('');

  const table = k.weathers.length ? `
    <table class="disadv-table" style="margin-bottom:1rem">
      <thead><tr>
        <th>時刻</th><th>天気</th><th>風速</th><th>風向・判定</th><th>気温</th><th>降水量</th><th></th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>` : '<div class="empty" style="padding:1rem 0">天候記録なし</div>';

  return `
    <div class="card">
      <div class="card-title">天候記録</div>
      ${table}
      <div class="divider"></div>
      <div style="font-size:.85rem;font-weight:700;margin-bottom:.6rem;color:var(--text-muted)">新規追加</div>
      <div class="form-grid cols-3">
        <div class="field">
          <label>計測時刻</label>
          <input type="time" id="w-time">
        </div>
        <div class="field">
          <label>天気 *</label>
          <select id="w-code"><option value="">選択...</option>${weatherOpts}</select>
        </div>
        <div class="field">
          <label>風速 (m/s)</label>
          <input type="number" step="0.1" min="0" id="w-wind-speed" placeholder="例: 3.5">
        </div>
        <div class="field">
          <label>風向き</label>
          <select id="w-wind-dir">${windOpts}</select>
        </div>
        <div class="field">
          <label>気温 (℃)</label>
          <input type="number" step="0.1" id="w-temp" placeholder="例: 22.5">
        </div>
        <div class="field">
          <label>降水量 (mm)</label>
          <input type="number" step="0.5" min="0" id="w-precipitation" placeholder="例: 0.0">
        </div>
      </div>
      <div style="margin-top:.75rem;display:flex;gap:.5rem;align-items:center">
        <button class="btn btn-primary btn-sm" onclick="saveWeather()">追加</button>
        <button class="btn btn-accent btn-sm" onclick="fetchJmaWeather()">気象庁から取得</button>
      </div>
    </div>`;
}

async function saveWeather() {
  const code = document.getElementById('w-code').value;
  if (!code) { toast('天気は必須です', 'error'); return; }
  try {
    await api.post(`/api/kaishi/${state.kaishiId}/weather`, {
      measurement_time: strOrNull('w-time'),
      weather_code:     code,
      wind_speed:       numOrNull('w-wind-speed'),
      wind_direction:   strOrNull('w-wind-dir'),
      temperature:      numOrNull('w-temp'),
      precipitation:    numOrNull('w-precipitation'),
    });
    toast('天候情報を追加しました');
    renderKaishi();
  } catch (e) { toast(e.message, 'error'); }
}

async function deleteWeather(id) {
  if (!confirm('この天候記録を削除しますか？')) return;
  try {
    await api.del(`/api/kaishi/weather/${id}`);
    toast('削除しました');
    renderKaishi();
  } catch (e) { toast(e.message, 'error'); }
}

// ============================================================
// 気象庁 時別値スクレイピング → 天候自動取得
// ============================================================
let _jmaEntries = [];

async function fetchJmaWeather() {
  try {
    const entries = await api.get(`/api/kaishi/${state.kaishiId}/fetch-weather-jma`);
    if (!entries.length) {
      toast('気象データを取得できませんでした', 'error');
      return;
    }
    _jmaEntries = entries;
    showJmaModal(entries);
  } catch (e) {
    const d = e.detail;
    if (d?.code === 'SCRAPE_FAILED' && d.url) {
      showModal(`
        <div class="modal-title">天候データ取得失敗</div>
        <p style="margin:.5rem 0">${d.message}</p>
        <p style="margin-top:.5rem">
          <a href="${d.url}" target="_blank" rel="noopener">気象庁 時別値（${d.station_name}）を開く →</a>
        </p>
        <div style="text-align:right;margin-top:1rem">
          <button onclick="closeModal()">閉じる</button>
        </div>`);
    } else {
      toast(e.message, 'error');
    }
  }
}

function showJmaModal(entries) {
  const rows = entries.map((e, i) => `
    <tr>
      <td style="text-align:center"><input type="checkbox" id="jw-chk-${i}" checked></td>
      <td>${e.measurement_time}</td>
      <td>
        <select id="jw-code-${i}" style="font-size:.85rem">
          ${C.weather_options.map(w =>
            `<option value="${w}" ${w === e.weather_code ? 'selected' : ''}>${w}</option>`
          ).join('')}
        </select>
      </td>
      <td>${e.wind_speed != null ? e.wind_speed + 'm/s' : '—'}</td>
      <td>${e.wind_direction ?? '—'}</td>
      <td>${e.temperature != null ? e.temperature + '℃' : '—'}</td>
      <td>${e.precipitation != null ? e.precipitation + 'mm' : '—'}</td>
    </tr>`).join('');

  showModal(`
    <div class="modal-title">気象庁 時別値（取得結果）</div>
    <p style="font-size:.85rem;color:var(--text-muted);margin-bottom:.75rem">
      保存する行にチェックを入れ、天気コードを確認してから「保存」してください。
    </p>
    <table class="disadv-table" style="margin-bottom:1rem">
      <thead><tr>
        <th>保存</th><th>時刻</th><th>天気</th><th>風速</th><th>風向</th><th>気温</th><th>降水量</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="closeModal()">キャンセル</button>
      <button class="btn btn-primary" onclick="saveJmaEntries()">保存</button>
    </div>`);
}

async function saveJmaEntries() {
  let saved = 0;
  for (let i = 0; i < _jmaEntries.length; i++) {
    const chk = document.getElementById(`jw-chk-${i}`);
    if (!chk || !chk.checked) continue;
    const e    = _jmaEntries[i];
    const code = document.getElementById(`jw-code-${i}`).value;
    try {
      await api.post(`/api/kaishi/${state.kaishiId}/weather`, {
        measurement_time: e.measurement_time,
        weather_code:     code,
        wind_speed:       e.wind_speed,
        wind_direction:   e.wind_direction,
        temperature:      e.temperature,
        precipitation:    e.precipitation,
      });
      saved++;
    } catch {}
  }
  closeModal();
  toast(`${saved}件の天候情報を保存しました`);
  renderKaishi();
}

// ============================================================
// Bias Tab
// ============================================================
const BIAS_LABELS_IO = { '-3':'大内有利','-2':'内有利','-1':'やや内','0':'フラット','1':'やや外','2':'外有利','3':'大外有利' };
const BIAS_LABELS_FB = { '-3':'大逃げ有利','-2':'逃げ有利','-1':'先行有利','0':'フラット','1':'差し有利','2':'追込有利','3':'大追込有利' };

const RUNNING_STYLES = ['逃げ', '先行', '差し', '追込'];

function buildBiasTab(k, paceStats = []) {
  const existing = {};
  k.track_biases.forEach(b => {
    const key = `${b.track_type}__${b.distance_category ?? 'ALL'}`;
    existing[key] = b;
  });

  const distOpts = ['', ...C.distance_categories].map(d =>
    `<option value="${d}">${d || '全距離共通'}</option>`).join('');

  const paceSection = paceStats.length
    ? buildPaceStatsSection(paceStats)
    : '<div class="card" style="margin-bottom:.75rem"><div class="card-title">ペース判定</div><div class="empty">ラップタイムデータがDBに未登録です</div></div>';

  return buildWindSummary(k) + paceSection + C.track_types.map(tt => {
    const b = existing[`${tt}__ALL`] || {};
    const ioScore = b.inside_outside_score ?? 0;
    const fbScore = b.front_back_score ?? 0;
    const savedStyles = (b.benefited_running_style || '').split(',').filter(Boolean);

    const styleChecks = RUNNING_STYLES.map(style => `
      <label class="style-check">
        <input type="checkbox" name="style-${tt}" value="${style}"
               ${savedStyles.includes(style) ? 'checked' : ''}>
        ${style}
      </label>`).join('');

    return `
    <div class="card">
      <div class="card-title">${tt}</div>
      <div class="field" style="margin-bottom:.75rem;max-width:200px">
        <label>距離帯</label>
        <select id="bias-dist-${tt}">${distOpts}</select>
      </div>
      <div style="display:flex;flex-direction:column;gap:.75rem;margin-bottom:.75rem">
        <div class="bias-row">
          <label>内外バイアス</label>
          ${buildBiasScale('io', tt, ioScore)}
          <span class="bias-label-text" id="bias-io-label-${tt}">${BIAS_LABELS_IO[String(ioScore)]}</span>
        </div>
        <div class="bias-row">
          <label>前後バイアス</label>
          ${buildBiasScale('fb', tt, fbScore)}
          <span class="bias-label-text" id="bias-fb-label-${tt}">${BIAS_LABELS_FB[String(fbScore)]}</span>
        </div>
      </div>
      <div class="field" style="margin-bottom:.75rem">
        <label>展開コメント</label>
        <textarea id="bias-pace-comment-${tt}" rows="2"
          placeholder="例: 前崩れ・差し有利、スロー逃げ残り、先行総崩れ...">${b.pace_comment ?? ''}</textarea>
      </div>
      <div class="field" style="margin-bottom:.75rem">
        <label>恩恵を受けた脚質</label>
        <div class="running-style-checks">${styleChecks}</div>
      </div>
      <div class="field" style="margin-bottom:.75rem">
        <label>詳細メモ (コース取りなど)</label>
        <textarea id="bias-detail-${tt}" rows="2">${b.bias_detail ?? ''}</textarea>
      </div>
      <div class="field" style="margin-bottom:.75rem">
        <label>備考</label>
        <textarea id="bias-notes-${tt}" rows="2">${b.notes ?? ''}</textarea>
      </div>
      <div class="flex-row">
        <button class="btn btn-primary btn-sm" onclick="saveBias('${tt}')">保存</button>
        ${b.id ? `<button class="btn btn-ghost btn-sm" onclick="deleteBias(${b.id})">削除</button>` : ''}
      </div>
    </div>`;
  }).join('');
}

// ============================================================
// Pace Stats Section (バイアスタブ内)
// ============================================================
const _TRACK_PREFIX_NAME = { '1': '芝', '2': 'ダ', '5': '障' };

function buildPaceStatsSection(paceStats) {
  const rows = paceStats.map(p => {
    const tname = _TRACK_PREFIX_NAME[String(p.track_cd || '').slice(0, 1)] || '';
    const medianFmt = p.median_time != null ? p.median_time.toFixed(1) : '—';
    const frontFmt  = p.front_half_3f != null ? p.front_half_3f.toFixed(1) : '—';
    const lastFmt   = p.last_3f != null ? p.last_3f.toFixed(1) : '—';
    const pciFmt    = p.pci != null ? p.pci.toFixed(1) : '—';
    const avgPciFmt = p.avg_pci != null
      ? `${p.avg_pci.toFixed(1)}<span class="pace-avg">&thinsp;(n=${p.sample_count})</span>`
      : '<span class="pace-avg">—</span>';
    const judgeBadge = paceBadge(p.pace_judge);
    return `
      <tr>
        <td>${p.race_num}R</td>
        <td>${p.distance ? p.distance + 'm' : '—'}${tname ? `<span class="pace-avg">&thinsp;${tname}</span>` : ''}</td>
        <td>${medianFmt}</td>
        <td>${frontFmt}</td>
        <td>${lastFmt}</td>
        <td>${pciFmt}</td>
        <td>${avgPciFmt}</td>
        <td>${judgeBadge}</td>
      </tr>`;
  }).join('');

  return `
    <div class="card" style="margin-bottom:.75rem">
      <div class="card-title">ペース判定（PCI方式）</div>
      <table class="disadv-table pace-table">
        <thead><tr>
          <th>R</th><th>距離</th><th>走破中央値</th><th>前半Ave-3F</th><th>上がり3F</th><th>PCI</th><th>同コース平均PCI</th><th>判定</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div style="font-size:.75rem;color:var(--text-muted);margin-top:.5rem">
        前半Ave-3F = (走破タイム − 上がり3F) × 600 ÷ (距離 − 600)　／　PCI = 上がり3F ÷ (前半Ave-3F + 上がり3F) × 100<br>
        PCI &lt; 47: ハイペース（赤）　47〜53: ミドルペース（灰）　PCI &gt; 53: スローペース（青）　同コース = 過去5年
      </div>
    </div>`;
}

function paceBadge(judge) {
  const cls = judge === 'ハイペース' ? 'pace-high' : judge === 'スローペース' ? 'pace-slow' : 'pace-mid';
  return `<span class="pace-badge ${cls}">${judge ?? '—'}</span>`;
}

// ============================================================
// Bias helpers
// ============================================================
function buildBiasScale(axis, tt, current) {
  return `<div class="bias-scale">` +
    [-3,-2,-1,0,1,2,3].map(v => {
      let cls = '';
      if (v === current) cls = v < 0 ? 'selected-neg' : v > 0 ? 'selected-pos' : 'selected-zero';
      return `<button class="bias-btn ${cls}" data-axis="${axis}" data-track="${tt}" data-val="${v}"
                onclick="selectBias('${axis}','${tt}',${v})">${v}</button>`;
    }).join('') +
  `</div>`;
}

function selectBias(axis, tt, val) {
  document.querySelectorAll(`.bias-btn[data-axis="${axis}"][data-track="${tt}"]`).forEach(btn => {
    const v = parseInt(btn.dataset.val);
    btn.className = 'bias-btn' + (v === val ? (v < 0 ? ' selected-neg' : v > 0 ? ' selected-pos' : ' selected-zero') : '');
  });
  const labels = axis === 'io' ? BIAS_LABELS_IO : BIAS_LABELS_FB;
  document.getElementById(`bias-${axis}-label-${tt}`).textContent = labels[String(val)];
}

function getBiasScore(axis, tt) {
  const btn = document.querySelector(`.bias-btn[data-axis="${axis}"][data-track="${tt}"].selected-neg,
    .bias-btn[data-axis="${axis}"][data-track="${tt}"].selected-pos,
    .bias-btn[data-axis="${axis}"][data-track="${tt}"].selected-zero`);
  return btn ? parseInt(btn.dataset.val) : 0;
}

async function saveBias(tt) {
  const ioScore = getBiasScore('io', tt);
  const fbScore = getBiasScore('fb', tt);
  const dist    = document.getElementById(`bias-dist-${tt}`).value || null;

  const styleChecks = document.querySelectorAll(`input[name="style-${tt}"]:checked`);
  const benefitedStyles = Array.from(styleChecks).map(c => c.value).join(',') || null;

  try {
    await api.post(`/api/kaishi/${state.kaishiId}/track-bias`, {
      track_type:              tt,
      distance_category:       dist,
      inside_outside_score:    ioScore,
      inside_outside_label:    BIAS_LABELS_IO[String(ioScore)],
      front_back_score:        fbScore,
      front_back_label:        BIAS_LABELS_FB[String(fbScore)],
      pace_comment:            strOrNull(`bias-pace-comment-${tt}`),
      benefited_running_style: benefitedStyles,
      bias_detail:             strOrNull(`bias-detail-${tt}`),
      notes:                   strOrNull(`bias-notes-${tt}`),
    });
    toast(`${tt}のバイアスを保存しました`);
    renderKaishi();
  } catch (e) { toast(e.message, 'error'); }
}

async function deleteBias(id) {
  if (!confirm('このバイアス記録を削除しますか？')) return;
  try {
    await api.del(`/api/kaishi/track-bias/${id}`);
    toast('削除しました');
    renderKaishi();
  } catch (e) { toast(e.message, 'error'); }
}

// ============================================================
// Races Tab
// ============================================================
function buildRacesTab(k, raceHorses = []) {
  const raceNums = Array.from({length: 12}, (_, i) => i + 1);
  const existingMap = {};
  k.races.forEach(r => { existingMap[r.race_num] = r; });

  const fetchBtn = `
    <div style="margin-bottom:.75rem">
      <button class="btn btn-accent btn-sm" onclick="fetchNlRaces()">DBから一括取得</button>
    </div>`;

  const items = raceNums.map(n => {
    const r = existingMap[n];
    const isActive = state.raceId && r && state.raceId === r.id;
    const badge = r && r.disadvantage_count > 0
      ? `<span class="badge badge-accent">${r.disadvantage_count}件</span>` : '';

    return `
      <div class="race-row ${isActive ? 'active' : ''}"
           onclick="toggleRacePanel(${n}, ${r ? r.id : 'null'})">
        <div class="race-num">${n}</div>
        <div class="race-info">
          <div class="race-name">${r ? (r.race_name || `第${n}レース`) : `第${n}レース`}</div>
          <div class="race-meta">${r ? raceMetaStr(r) : '未入力'}</div>
        </div>
        ${badge}
      </div>
      ${isActive && r ? buildRacePanel(r, raceHorses) : ''}`;
  }).join('');

  return fetchBtn + `<div class="race-list">${items}</div>`;
}

async function fetchNlRaces() {
  if (!confirm('DBからレース情報を一括取得します。既存のレース名・距離・種別が上書きされます。続けますか？')) return;
  try {
    const races = await api.get(`/api/kaishi/${state.kaishiId}/nl-races`);
    if (!races.length) { toast('該当日・競馬場のレースデータがDBに見つかりませんでした', 'error'); return; }
    for (const r of races) {
      await api.post(`/api/kaishi/${state.kaishiId}/races`, {
        race_num:   r.race_num,
        race_name:  r.race_name,
        track_type: r.track_type,
        distance:   r.distance,
        grade:      r.grade,
        notes:      null,
      });
    }
    toast(`${races.length}件のレース情報を取得しました`);
    renderKaishi();
  } catch (e) { toast(e.message, 'error'); }
}

function raceMetaStr(r) {
  const parts = [];
  if (r.track_type) parts.push(r.track_type);
  if (r.distance)   parts.push(r.distance + 'm');
  if (r.grade)      parts.push(r.grade);
  return parts.join(' · ') || '詳細未入力';
}

async function toggleRacePanel(raceNum, raceId) {
  if (state.raceId && raceId && state.raceId === raceId) {
    state.raceId = null;
    renderKaishi();
    return;
  }
  if (!raceId) {
    await openRaceEditModal(raceNum, null);
    return;
  }
  state.raceId = raceId;
  renderKaishi();
}

// _raceHorsesCache: { raceId: [{umaban, bamei, kettonum, memo_flag, memo_text}, ...] }
let _raceHorsesCache = {};

// 印アイコン変換
const _FLAG_ICONS = { '注目': '◎', '次走': '△', '危険': '×', '消し': '✓' };
function flagIcon(flag) { return _FLAG_ICONS[flag] || flag; }

const _FLAG_OPTS = [
  { v: '',    label: '—' },
  { v: '注目', label: '◎注目' },
  { v: '次走', label: '△次走' },
  { v: '危険', label: '×危険' },
  { v: '消し', label: '✓消し' },
];

function buildRacePanel(r, raceHorses = []) {
  // 馬データをキャッシュに保存（saveDisadvantage など非同期処理から参照できるように）
  _raceHorsesCache[r.id] = raceHorses;

  const rows = (r.disadvantages || []).map(d => `
    <tr>
      <td>${d.horse_num != null ? d.horse_num + '番' : '—'}</td>
      <td>${d.horse_name}</td>
      <td>${d.disadvantage_type}</td>
      <td>${d.timing}</td>
      <td>${severityDots(d.severity)}</td>
      <td>${d.estimated_loss != null ? d.estimated_loss + '馬身' : '—'}</td>
      <td>${d.memo ?? ''}</td>
      <td><button class="btn btn-danger btn-sm btn-icon"
                  onclick="deleteDisadvantage(${d.id}, event)">✕</button></td>
    </tr>`).join('');

  const table = (r.disadvantages || []).length ? `
    <table class="disadv-table">
      <thead><tr>
        <th>馬番</th><th>馬名</th><th>不利種別</th><th>タイミング</th>
        <th>程度</th><th>推定ロス</th><th>メモ</th><th></th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>` : '<div class="empty" style="padding:.5rem 0 .75rem">不利記録なし</div>';

  const typeOpts = C.disadvantage_types.map(t =>
    `<option value="${t}">${t}</option>`).join('');
  const timingOpts = C.timing_options.map(t =>
    `<option value="${t}">${t}</option>`).join('');

  // 出走馬ドロップダウン（nl_se データがある場合のみ表示）
  const horseSelect = raceHorses.length ? `
    <div class="field form-full" style="margin-bottom:.25rem">
      <label>出走馬を選択（馬番・馬名を自動入力）</label>
      <select id="d-horse-sel-${r.id}" onchange="onHorseSelect(${r.id})">
        <option value="">選択してください...</option>
        ${raceHorses.map((h, i) =>
          `<option value="${i}">${h.umaban}番&nbsp;${h.bamei}</option>`
        ).join('')}
      </select>
    </div>` : '';

  return `
    <div class="race-panel">
      <div class="race-panel-title">
        第${r.race_num}レース 不利情報
        <button class="btn btn-ghost btn-sm" style="margin-left:.5rem"
                onclick="openRaceEditModal(${r.race_num}, ${r.id}, event)">レース情報編集</button>
      </div>
      ${table}
      <div class="divider"></div>
      <div style="font-size:.85rem;font-weight:700;margin-bottom:.6rem;color:var(--text-muted)">不利を追加</div>
      <div class="form-grid">
        ${horseSelect}
        <div class="field">
          <label>馬番</label>
          <input type="number" id="d-num-${r.id}" min="1" max="18" placeholder="例: 5">
        </div>
        <div class="field">
          <label>馬名 *</label>
          <input type="text" id="d-name-${r.id}" placeholder="例: ディープインパクト">
        </div>
        <div class="field">
          <label>不利種別 *</label>
          <select id="d-type-${r.id}">${typeOpts}</select>
        </div>
        <div class="field">
          <label>発生タイミング *</label>
          <select id="d-timing-${r.id}">${timingOpts}</select>
        </div>
        <div class="field">
          <label>程度 (1〜5)</label>
          <input type="number" id="d-severity-${r.id}" min="1" max="5" placeholder="3">
        </div>
        <div class="field">
          <label>推定ロス (馬身)</label>
          <input type="number" id="d-loss-${r.id}" step="0.5" min="0" placeholder="例: 1.5">
        </div>
        <div class="field form-full">
          <label>メモ</label>
          <input type="text" id="d-memo-${r.id}" placeholder="詳細を自由記述">
        </div>
      </div>
      <button class="btn btn-accent btn-sm" style="margin-top:.75rem"
              onclick="saveDisadvantage(${r.id}, event)">追加</button>
      ${raceHorses.some(h => h.kettonum) ? `
      <div class="divider"></div>
      <div style="font-size:.85rem;font-weight:700;margin-bottom:.6rem;color:var(--text-muted)">馬メモ・印</div>
      <div class="memo-horse-list">
        ${raceHorses.filter(h => h.kettonum).map(h => {
          const kt       = h.kettonum;
          const flagOpts = _FLAG_OPTS.map(f =>
            `<option value="${f.v}"${h.memo_flag === f.v ? ' selected' : ''}>${f.label}</option>`
          ).join('');
          const badge = h.memo_flag
            ? `<span class="memo-badge memo-${h.memo_flag}">${flagIcon(h.memo_flag)}</span>` : '';
          const memoEsc = (h.memo_text || '').replace(/"/g, '&quot;');
          return `<div class="memo-row" id="memo-row-${kt}">
            <span class="memo-horse-num">${h.umaban}番</span>
            <span class="memo-horse-name" id="memo-name-${kt}">${h.bamei}${badge}</span>
            <select class="memo-flag-sel" id="memo-flag-${kt}">${flagOpts}</select>
            <input class="memo-text-input" id="memo-text-${kt}" type="text"
                   value="${memoEsc}" placeholder="メモを入力...">
            <button class="btn btn-ghost btn-sm"
                    onclick="saveMemo('${kt}','${h.bamei}',event)">保存</button>
          </div>`;
        }).join('')}
      </div>` : ''}
    </div>`;
}

function onHorseSelect(raceId) {
  const sel = document.getElementById(`d-horse-sel-${raceId}`);
  if (!sel) return;
  const idx = parseInt(sel.value);
  if (isNaN(idx)) return;
  const horses = _raceHorsesCache[raceId] || [];
  const horse  = horses[idx];
  if (!horse) return;
  const numEl  = document.getElementById(`d-num-${raceId}`);
  const nameEl = document.getElementById(`d-name-${raceId}`);
  if (numEl)  numEl.value  = horse.umaban;
  if (nameEl) nameEl.value = horse.bamei;
}

async function saveMemo(kettonum, bamei, evt) {
  evt.preventDefault();
  const flag = document.getElementById(`memo-flag-${kettonum}`)?.value || null;
  const memo = document.getElementById(`memo-text-${kettonum}`)?.value || null;
  try {
    await api.put(`/api/memo/${kettonum}`, { memo: memo || null, flag: flag || null });
    // バッジを即時更新
    const nameEl = document.getElementById(`memo-name-${kettonum}`);
    if (nameEl) {
      const badge = flag
        ? `<span class="memo-badge memo-${flag}">${flagIcon(flag)}</span>` : '';
      nameEl.innerHTML = `${bamei}${badge}`;
    }
    // キャッシュも更新
    for (const horses of Object.values(_raceHorsesCache)) {
      const h = horses.find(h => h.kettonum === kettonum);
      if (h) { h.memo_flag = flag || null; h.memo_text = memo || null; }
    }
  } catch (e) {
    alert('保存に失敗しました: ' + (e.message || e));
  }
}

function severityDots(s) {
  if (s == null) return '—';
  return Array.from({length:5}, (_,i) =>
    `<span class="severity-dot ${i < s ? 'filled' : ''}"></span>`
  ).join('');
}

async function openRaceEditModal(raceNum, raceId, event) {
  if (event) event.stopPropagation();
  let r = {};
  if (raceId) {
    try { r = await api.get(`/api/races/${raceId}`); } catch {}
  }
  const ttOpts = C.track_types.map(t =>
    `<option value="${t}" ${r.track_type === t ? 'selected' : ''}>${t}</option>`).join('');
  const gradeOpts = C.grade_options.map(g =>
    `<option value="${g}" ${r.grade === g ? 'selected' : ''}>${g}</option>`).join('');

  showModal(`
    <div class="modal-title">第${raceNum}レース 情報入力</div>
    <div class="form-grid">
      <div class="field form-full">
        <label>レース名</label>
        <input type="text" id="rm-name" value="${r.race_name ?? ''}" placeholder="例: 春の宴特別">
      </div>
      <div class="field">
        <label>トラック種別</label>
        <select id="rm-tt"><option value="">選択...</option>${ttOpts}</select>
      </div>
      <div class="field">
        <label>距離 (m)</label>
        <input type="number" id="rm-dist" min="800" max="4000" step="100" value="${r.distance ?? ''}">
      </div>
      <div class="field">
        <label>グレード</label>
        <select id="rm-grade"><option value="">選択...</option>${gradeOpts}</select>
      </div>
      <div class="field form-full">
        <label>メモ</label>
        <textarea id="rm-notes" rows="2">${r.notes ?? ''}</textarea>
      </div>
    </div>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="closeModal()">キャンセル</button>
      <button class="btn btn-primary" onclick="submitRaceEdit(${raceNum}, ${raceId ?? 'null'})">保存</button>
    </div>`);
}

async function submitRaceEdit(raceNum, raceId) {
  try {
    const r = await api.post(`/api/kaishi/${state.kaishiId}/races`, {
      race_num:   raceNum,
      race_name:  strOrNull('rm-name'),
      track_type: strOrNull('rm-tt'),
      distance:   numOrNull('rm-dist'),
      grade:      strOrNull('rm-grade'),
      notes:      strOrNull('rm-notes'),
    });
    closeModal();
    state.raceId = r.id;
    toast('レース情報を保存しました');
    renderKaishi();
  } catch (e) { toast(e.message, 'error'); }
}

async function saveDisadvantage(raceId, event) {
  event.stopPropagation();
  const name = document.getElementById(`d-name-${raceId}`).value.trim();
  if (!name) { toast('馬名は必須です', 'error'); return; }
  try {
    await api.post(`/api/races/${raceId}/disadvantages`, {
      horse_name:       name,
      horse_num:        numOrNull(`d-num-${raceId}`),
      disadvantage_type: document.getElementById(`d-type-${raceId}`).value,
      timing:           document.getElementById(`d-timing-${raceId}`).value,
      severity:         numOrNull(`d-severity-${raceId}`),
      estimated_loss:   numOrNull(`d-loss-${raceId}`),
      memo:             strOrNull(`d-memo-${raceId}`),
    });
    toast('不利情報を追加しました');
    const race = await api.get(`/api/races/${raceId}`);
    const panel = document.querySelector('.race-panel');
    const horses = _raceHorsesCache[raceId] || [];
    if (panel) panel.outerHTML = buildRacePanel(race, horses);
    else renderKaishi();
  } catch (e) { toast(e.message, 'error'); }
}

async function deleteDisadvantage(id, event) {
  event.stopPropagation();
  if (!confirm('この不利情報を削除しますか？')) return;
  try {
    await api.del(`/api/disadvantages/${id}`);
    toast('削除しました');
    const race = await api.get(`/api/races/${state.raceId}`);
    const panel = document.querySelector('.race-panel');
    const horses = _raceHorsesCache[state.raceId] || [];
    if (panel) panel.outerHTML = buildRacePanel(race, horses);
    else renderKaishi();
  } catch (e) { toast(e.message, 'error'); }
}

async function confirmDeleteKaishi(id) {
  if (!confirm('この開催の全データを削除しますか？（元に戻せません）')) return;
  try {
    await api.del(`/api/kaishi/${id}`);
    toast('開催を削除しました');
    navigate('home');
  } catch (e) { toast(e.message, 'error'); }
}

// ============================================================
// Tab attachment
// ============================================================
function attachTabListeners(k) {
  // Tab buttons already have onclick inline
}

// ============================================================
// Modal helpers
// ============================================================
function showModal(html) {
  let overlay = document.getElementById('modal-overlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'modal-overlay';
    overlay.className = 'modal-overlay';
    overlay.onclick = e => { if (e.target === overlay) closeModal(); };
    document.body.appendChild(overlay);
  }
  overlay.innerHTML = `<div class="modal">${html}</div>`;
  overlay.style.display = 'flex';
}

function closeModal() {
  const overlay = document.getElementById('modal-overlay');
  if (overlay) overlay.style.display = 'none';
}

// ============================================================
// Utils
// ============================================================
function fmtDate(d) {
  if (!d) return '';
  const [y, m, day] = String(d).split(/[-T]/);
  return `${y}年${parseInt(m)}月${parseInt(day)}日`;
}
function todayStr() {
  return new Date().toISOString().slice(0, 10);
}
function num(v) { const n = parseFloat(v); return isNaN(n) ? null : n; }
function numOrNull(id) {
  const el = document.getElementById(id);
  if (!el || el.value === '') return null;
  const n = parseFloat(el.value);
  return isNaN(n) ? null : n;
}
function strOrNull(id) {
  const el = document.getElementById(id);
  if (!el) return null;
  return el.value.trim() || null;
}

// ============================================================
// Boot
// ============================================================
document.addEventListener('DOMContentLoaded', async () => {
  C = await api.get('/api/constants');
  document.getElementById('btn-back').addEventListener('click', () => navigate('home'));
  document.getElementById('app-title').addEventListener('click', () => navigate('home'));
  navigate('home');
});
