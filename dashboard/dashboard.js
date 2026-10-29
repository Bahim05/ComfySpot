// Dashboard ComfySpot.
// Alur: sambung ke broker lewat MQTT over WebSocket -> subscribe state dari hub (pesan retained, JSON)
// -> tampilkan lokasi terbaik, kartu lokasi, grafik kebisingan per jam, dan tabel peringkat.
// Tombol mode mengirim teks "hening" atau "ramai" ke hub. Grafik mini di kartu dikumpulkan di browser
// dari pesan state yang masuk, jadi kosong lagi saat halaman dimuat ulang.
// Semua teks dari broker dipasang lewat textContent: broker publik bisa diisi siapa saja.

const $ = id => document.getElementById(id);

// Nama topic harus sama dengan shared/topics.py
const topicState = pid => `comfyspot/${pid}/state`;
const topicMode = pid => `comfyspot/${pid}/control/mode`;

const BROKER_URL = 'wss://broker.hivemq.com:8884/mqtt';
const ID_PATTERN = /^[A-Za-z0-9_-]{1,40}$/;   // sama dengan halaman sensor
const HISTORY_MS = 5 * 60 * 1000;            // panjang grafik mini di kartu
const SAMPLE_EVERY_MS = 1000;                // hub bisa publish 4 kali per detik; grafik mini cukup sekali per detik
const GAP_MS = 5000;                         // jeda lebih lama dari ini digambar sebagai putus, bukan garis lurus
const STALE_AFTER_MS = 15000;                // hub publish tiap detik; lebih lama dari ini berarti hub berhenti
const MODE_TIMEOUT_MS = 6000;
const RENDER_GAP_MS = 250;

const STATUS_LABEL = { hijau: 'Nyaman', kuning: 'Cukup', merah: 'Kurang' };
const NOISE_LABEL = { sepi: 'Sepi', sedang: 'Sedang', ramai: 'Ramai' };
// Label cahaya untuk tampilan saja. 300 lux adalah LUX_TARGET di shared/rules.py (skor cahaya penuh).
const lightLabel = lux => lux == null ? '-' : lux >= 300 ? 'terang' : lux >= 100 ? 'redup' : 'gelap';
const lightText = r => lightLabel(r.lux) + (r.lux_stable ? '' : ', tidak stabil');
const MODE_LABEL = { hening: 'Hening', ramai: 'Ramai' };
const SOURCE_LABEL = { termux: 'Termux', sensor: 'Sensor browser', manual: 'Manual (uji alur)' };
const SERIES_SLOTS = 4;   // warna lokasi --s1..--s4 di CSS; lokasi kelima dan seterusnya abu-abu

const DEMO = new URLSearchParams(location.search).has('demo');

let client = null;
let pid = null;
let state = null;
let pendingMode = null;
let pendingTimer = null;
let lastRender = 0;
let renderQueued = false;
let hourlySig = '';
let hourlyHover = null;                      // jam yang sedang disorot di grafik per jam
const sparkHover = new Map();                // device_id -> posisi kursor (0..1) di grafik mini
const history = new Map();                   // device_id -> [{ t, score }]
const knownLocation = new Map();             // device_id -> nama lokasi terakhir, untuk perangkat yang offline
const seriesSlot = new Map();                // lokasi -> nomor warna; tetap selama halaman terbuka

// ---------- pembantu DOM ----------

function h(tag, props, ...kids) {
  const node = document.createElement(tag);
  setProps(node, props);
  for (const k of kids.flat()) if (k != null && k !== false) node.append(k);
  return node;
}

function s(tag, props, ...kids) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  setProps(node, props);
  for (const k of kids.flat()) if (k != null && k !== false) node.append(k);
  return node;
}

function setProps(node, props) {
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') node.setAttribute('class', v);
    else if (k === 'text') node.textContent = v;
    else if (k === 'style') node.style.cssText = v;
    else node.setAttribute(k, v);
  }
}

const fmt1 = v => v == null ? '-' : v.toLocaleString('id-ID', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const fmt0 = v => v == null ? '-' : Math.round(v).toLocaleString('id-ID');
const clock = t => new Date(t).toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
const hh = hour => String(hour).padStart(2, '0') + '.00';

function chip(status, big) {
  const label = STATUS_LABEL[status];
  return h('span', { class: 'chip' + (label ? ' ' + status : ' flat') + (big ? ' big' : ''), text: label || 'Tanpa status' });
}

function colorFor(loc) {
  if (!seriesSlot.has(loc)) seriesSlot.set(loc, seriesSlot.size);
  const i = seriesSlot.get(loc);
  return i < SERIES_SLOTS ? `var(--s${i + 1})` : 'var(--s-other)';
}

function key(loc, line) {
  return h('span', { class: 'key' + (line ? ' line' : ''), style: `background:${colorFor(loc)}`, 'aria-hidden': 'true' });
}

// ---------- penyimpanan kecil (hanya kemudahan, boleh gagal) ----------

function load(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
function save(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* diblokir, abaikan */ } }

// ---------- pesan state ----------

// Saring pesan state. Bentuknya mengikuti Hub.snapshot() di hub.py; isian yang salah dibuang.
function cleanState(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const num = v => typeof v === 'number' && Number.isFinite(v) ? v : null;
  const str = (v, n = 40) => v == null ? '' : String(v).slice(0, n);
  const ids = v => (Array.isArray(v) ? v : []).map(x => str(x)).filter(Boolean);

  const ranking = (Array.isArray(raw.ranking) ? raw.ranking : [])
    .filter(r => r && typeof r === 'object' && num(r.score) !== null && r.device_id != null)
    .map(r => ({
      rank: num(r.rank), device_id: str(r.device_id), location: str(r.location) || str(r.device_id),
      score: r.score, status: STATUS_LABEL[r.status] ? r.status : null,
      noise_level: NOISE_LABEL[r.noise_level] ? r.noise_level : null,
      lux: num(r.lux), lux_stable: r.lux_stable === true, db: num(r.db), lux_source: str(r.lux_source, 12)
    }))
    .sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99));

  // Map, bukan objek biasa, supaya nama lokasi seperti "__proto__" tidak berbuat aneh
  const hourly = new Map();
  if (raw.hourly_db && typeof raw.hourly_db === 'object') {
    for (const [loc, vals] of Object.entries(raw.hourly_db)) {
      if (Array.isArray(vals) && vals.length === 24) hourly.set(str(loc), vals.map(num));
    }
  }
  const busiest = new Map();
  if (raw.busiest_hours && typeof raw.busiest_hours === 'object') {
    for (const [loc, hours] of Object.entries(raw.busiest_hours)) {
      if (Array.isArray(hours)) busiest.set(str(loc), hours.filter(x => Number.isInteger(x) && x >= 0 && x < 24));
    }
  }
  return {
    updated_ms: num(raw.updated_ms),
    mode: MODE_LABEL[raw.mode] ? raw.mode : null,
    ranking,
    offline: ids(raw.offline),
    waiting_light: ids(raw.waiting_light),
    hourly_db: hourly,
    busiest_hours: busiest
  };
}

function handleState(st) {
  const now = Date.now();
  state = st;
  for (const r of st.ranking) {
    knownLocation.set(r.device_id, r.location);
    colorFor(r.location);
    // grafik mini: satu titik per detik per perangkat, buang yang lebih tua dari HISTORY_MS
    const list = history.get(r.device_id) || [];
    const last = list[list.length - 1];
    if (!last || now - last.t >= SAMPLE_EVERY_MS) list.push({ t: now, score: r.score });
    else last.score = r.score;
    while (list.length && now - list[0].t > HISTORY_MS) list.shift();
    history.set(r.device_id, list);
  }
  for (const loc of st.hourly_db.keys()) colorFor(loc);
  if (pendingMode && st.mode === pendingMode) { clearPending(); clearNotice('mode'); }
  scheduleRender();
}

function handlePayload(payload) {
  let raw;
  try { raw = JSON.parse(payload.toString()); } catch (e) { return; }
  const st = cleanState(raw);
  if (st) handleState(st);
}

// ---------- tampilan ----------

function scheduleRender() {
  if (renderQueued) return;
  renderQueued = true;
  const wait = Math.max(0, RENDER_GAP_MS - (Date.now() - lastRender));
  setTimeout(() => requestAnimationFrame(() => {
    renderQueued = false;
    lastRender = Date.now();
    render();
  }), wait);
}

function render() {
  renderMode();
  renderBest();
  renderCards();
  renderHourly(false);
  renderTable();
  renderFreshness();
}

function renderBest() {
  const top = state && state.ranking[0];
  const scoreBox = $('best-score');
  if (!top) {
    $('best-name').textContent = state ? 'Belum ada lokasi aktif' : 'Belum ada data';
    $('best-meta').textContent = !state && !client && !DEMO ? 'Isi PROJECT_ID lalu tekan Sambungkan.'
      : !state ? 'Menunggu data dari hub. Pastikan hub.py berjalan dengan PROJECT_ID yang sama.'
      : 'Belum ada HP lokasi yang mengirim data lengkap (suara dan cahaya).';
    scoreBox.hidden = true;
    return;
  }
  $('best-name').textContent = top.location;
  const parts = [
    'Cahaya ' + fmt0(top.lux) + ' lux',
    'Kebisingan ' + fmt1(top.db) + ' dB relatif' + (top.noise_level ? ' (' + NOISE_LABEL[top.noise_level].toLowerCase() + ')' : ''),
    'Mode ' + (MODE_LABEL[state.mode] || '-').toLowerCase()
  ];
  $('best-meta').textContent = parts.join('  ·  ');
  $('best-value').textContent = fmt1(top.score);
  $('best-chip').replaceChildren(chip(top.status, true));
  scoreBox.hidden = false;
}

function renderCards() {
  const box = $('cards');
  const cards = [];
  if (state) {
    for (const r of state.ranking) cards.push(locationCard(r));
    for (const id of state.waiting_light) cards.push(inactiveCard(id, 'Menunggu data cahaya',
      'Mikrofon sudah mengirim, tetapi lux dari Termux belum ada. Jalankan light_termux.py --device ' + id + ' di HP itu.'));
    for (const id of state.offline) cards.push(inactiveCard(id, 'Offline', 'Tidak ada data lebih dari 15 detik.'));
  }
  if (!cards.length) {
    cards.push(h('article', { class: 'card loc muted' },
      h('p', { class: 'loc-msg', text: state ? 'Belum ada HP lokasi yang terhubung ke hub.' : 'Kartu lokasi muncul setelah data hub masuk.' })));
  }
  box.replaceChildren(...cards);
}

function locationCard(r) {
  const card = h('article', { class: 'card loc' },
    h('div', { style: 'min-width:0' },
      h('div', { class: 'loc-name' }, key(r.location), h('span', { text: r.location })),
      h('div', { class: 'loc-sub', text: 'Peringkat ' + (r.rank ?? '-') + '  ·  ' + r.device_id })),
    h('div', { class: 'loc-score' },
      h('div', {}, h('strong', { text: fmt1(r.score) }), h('span', { text: ' skor' })),
      chip(r.status)),
    h('dl', { class: 'facts' },
      h('div', { class: 'fact' }, h('dt', { text: 'Cahaya' }),
        h('dd', {}, fmt0(r.lux) + ' ', h('small', { text: 'lux' }))),
      h('div', { class: 'fact' }, h('dt', { text: 'Kebisingan' }),
        h('dd', {}, fmt1(r.db) + ' ', h('small', { text: 'dB relatif' })))),
    h('div', { class: 'loc-sub', text: 'Suasana ' + (r.noise_level ? NOISE_LABEL[r.noise_level].toLowerCase() : '-') +
      '  ·  cahaya ' + lightText(r) }),
    sparkline(r.device_id, r.location));
  return card;
}

function inactiveCard(id, title, text) {
  const loc = knownLocation.get(id);
  return h('article', { class: 'card loc muted' },
    h('div', { class: 'loc-top' },
      h('div', { style: 'min-width:0' },
        h('div', { class: 'loc-name' }, h('span', { text: loc || id })),
        h('div', { class: 'loc-sub', text: id })),
      h('span', { class: 'chip flat', text: title })),
    h('p', { class: 'loc-msg', text }));
}

// Grafik mini skor beberapa menit terakhir. viewBox ditarik mengikuti lebar kartu; garis tetap 2px
// karena vector-effect, dan titik digambar sebagai elemen HTML supaya tidak ikut gepeng.
function sparkline(id, loc) {
  const pts = history.get(id) || [];
  const hint = h('span', { text: '' });
  const box = h('div', { class: 'spark' }, h('div', { class: 'spark-label' }, h('span', { text: 'Skor kenyamanan, 5 menit terakhir' }), hint));
  if (pts.length < 2) {
    box.append(h('div', { class: 'spark-empty', text: 'Mengumpulkan data...' }));
    return box;
  }
  // sumbu waktu mulai dari titik pertama selama riwayat belum sampai 5 menit, supaya garis tidak menumpuk di kanan
  const W = 300, H = 56, now = pts[pts.length - 1].t, t0 = Math.max(now - HISTORY_MS, pts[0].t), span = Math.max(1, now - t0);
  const x = t => (t - t0) / span * W;
  const y = v => H - 3 - Math.max(0, Math.min(100, v)) / 100 * (H - 6);
  const color = colorFor(loc);
  const gid = 'sg-' + id.replace(/[^A-Za-z0-9_-]/g, '');

  let line = '', area = '', seg = [];
  const flush = () => {
    if (seg.length > 1) {
      const d = seg.map((p, i) => (i ? 'L' : 'M') + x(p.t).toFixed(1) + ',' + y(p.score).toFixed(1)).join('');
      line += d;
      area += d + `L${x(seg[seg.length - 1].t).toFixed(1)},${H}L${x(seg[0].t).toFixed(1)},${H}Z`;
    }
    seg = [];
  };
  pts.forEach((p, i) => {
    if (i && p.t - pts[i - 1].t > GAP_MS) flush();
    seg.push(p);
  });
  flush();

  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'none', 'aria-hidden': 'true' },
    s('defs', {}, s('linearGradient', { id: gid, x1: 0, y1: 0, x2: 0, y2: 1 },
      s('stop', { offset: 0, style: `stop-color:${color};stop-opacity:.28` }),
      s('stop', { offset: 1, style: `stop-color:${color};stop-opacity:0` }))),
    s('line', { x1: 0, x2: W, y1: y(75), y2: y(75), class: 'grid', style: 'stroke:var(--grid)', 'vector-effect': 'non-scaling-stroke' }),
    s('path', { d: area, style: `fill:url(#${gid})` }),
    s('path', { d: line, style: `fill:none;stroke:${color};stroke-width:2;stroke-linejoin:round;stroke-linecap:round`, 'vector-effect': 'non-scaling-stroke' }));
  const dot = (p, cls) => h('span', { class: cls, style: `position:absolute;width:9px;height:9px;border-radius:50%;background:${color};` +
    `box-shadow:0 0 0 2px var(--card);transform:translate(-50%,-50%);left:${x(p.t) / W * 100}%;top:${y(p.score) / H * 100}%;pointer-events:none` });
  const plot = h('div', { style: 'position:relative' }, svg, dot(pts[pts.length - 1], 'end'));
  box.append(plot);

  const show = frac => {
    const t = t0 + frac * span;
    let best = pts[0];
    for (const p of pts) if (Math.abs(p.t - t) < Math.abs(best.t - t)) best = p;
    plot.querySelector('.hover')?.remove();
    plot.append(dot(best, 'hover'));
    hint.textContent = clock(best.t) + ' · ' + fmt1(best.score);
  };
  plot.addEventListener('pointermove', e => {
    const rect = plot.getBoundingClientRect();
    const frac = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    sparkHover.set(id, frac);
    show(frac);
  });
  plot.addEventListener('pointerleave', () => {
    sparkHover.delete(id);
    plot.querySelector('.hover')?.remove();
    hint.textContent = '';
  });
  if (sparkHover.has(id)) show(sparkHover.get(id));
  return box;
}

function niceTicks(min, max, count) {
  const raw = (max - min || 1) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(v => v >= raw);
  const ticks = [];
  for (let v = Math.floor(min / step) * step; v <= Math.ceil(max / step) * step + 1e-9; v += step) ticks.push(+v.toFixed(6));
  return ticks;
}

function currentHour() { return DEMO ? DEMO_NOW_HOUR : new Date().getHours(); }

// Grafik kebisingan per jam: satu garis per lokasi, area bergradasi, titik bercincin di jam paling ramai.
function renderHourly(force) {
  const wrap = $('hourly-wrap');
  const tip = $('hourly-tip');
  const hourly = state ? state.hourly_db : new Map();
  const busiest = state ? state.busiest_hours : new Map();
  const sig = JSON.stringify([[...hourly], [...busiest], currentHour(), wrap.clientWidth]);
  if (!force && sig === hourlySig) return;
  hourlySig = sig;

  for (const c of [...wrap.children]) if (c !== tip) c.remove();
  tip.hidden = true;
  const series = [...hourly].map(([loc, vals]) => ({ loc, vals, color: colorFor(loc), n: vals.filter(v => v !== null).length }))
    .sort((a, b) => seriesSlot.get(a.loc) - seriesSlot.get(b.loc));

  $('hourly-legend').replaceChildren(...(series.length > 1 ? series.map(sr => h('span', {}, key(sr.loc, true), sr.loc)) : []));
  renderBusiest(series.some(sr => sr.n >= 2) ? series : [], busiest);
  renderHourlyTable(series);

  if (!series.some(sr => sr.n >= 2)) {
    wrap.append(h('div', { class: 'empty' }, h('div', {},
      s('svg', { viewBox: '0 0 120 40', 'aria-hidden': 'true' },
        s('path', { d: 'M4 30 C24 30 30 14 48 16 S78 32 92 22 S110 10 116 10', style: 'fill:none;stroke:var(--axis);stroke-width:2;stroke-dasharray:4 5;stroke-linecap:round' })),
      h('strong', { text: 'Data per jam masih sedikit' }),
      h('p', { text: state
        ? 'Grafik muncul setelah hub mencatat kebisingan di sedikitnya dua jam berbeda. Biarkan hub berjalan; rata-rata per jam ikut tersimpan di data/hourly.json.'
        : 'Sambungkan ke hub untuk melihat rata-rata kebisingan tiap jam.' }))));
    return;
  }

  const W = Math.max(280, wrap.clientWidth);
  const narrow = W < 560;
  const H = narrow ? 230 : 290;
  const m = { l: 36, r: 14, t: 16, b: 28 };
  const vals = series.flatMap(sr => sr.vals.filter(v => v !== null));
  const ticks = niceTicks(Math.min(...vals) - 3, Math.max(...vals) + 3, narrow ? 3 : 4);
  const lo = ticks[0], hi = ticks[ticks.length - 1];
  const step = (W - m.l - m.r) / 23;
  const x = hr => m.l + hr * step;
  const y = v => m.t + (hi - v) / (hi - lo) * (H - m.t - m.b);
  const svg = s('svg', { width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: 'img',
    'aria-label': 'Grafik rata-rata kebisingan per jam untuk ' + series.map(sr => sr.loc).join(', ') + '. Rinciannya ada di tabel di bawah grafik.' });
  const defs = s('defs');
  svg.append(defs);

  const nowH = currentHour();
  svg.append(s('rect', { class: 'now', x: x(nowH) - step / 2, y: m.t, width: step, height: H - m.t - m.b, rx: 4 }));
  svg.append(s('text', { x: x(nowH), y: m.t - 4, 'text-anchor': 'middle', style: 'font-size:11px;font-weight:600', text: 'sekarang' }));
  for (const v of ticks) {
    svg.append(s('line', { class: v === lo ? 'base' : 'grid', x1: m.l, x2: W - m.r, y1: y(v), y2: y(v) }));
    svg.append(s('text', { x: m.l - 8, y: y(v) + 4, 'text-anchor': 'end', text: String(v).replace('.', ',') }));
  }
  for (let hr = 0; hr < 24; hr += narrow ? 6 : 3) {
    svg.append(s('text', { x: x(hr), y: H - 8, 'text-anchor': 'middle', text: String(hr).padStart(2, '0') }));
  }
  svg.append(s('text', { x: W - m.r, y: H - 8, 'text-anchor': 'end', text: narrow ? '' : 'jam' }));

  series.forEach((sr, i) => {
    const gid = 'hg-' + i;
    defs.append(s('linearGradient', { id: gid, x1: 0, y1: 0, x2: 0, y2: 1 },
      s('stop', { offset: 0, style: `stop-color:${sr.color};stop-opacity:${series.length > 2 ? .12 : .2}` }),
      s('stop', { offset: 1, style: `stop-color:${sr.color};stop-opacity:0` })));
    // pisahkan menjadi potongan jam berurutan; jam tanpa data (null) memutus garis
    const segs = [];
    let cur = [];
    sr.vals.forEach((v, hr) => {
      if (v === null) { if (cur.length) segs.push(cur); cur = []; } else cur.push([hr, v]);
    });
    if (cur.length) segs.push(cur);
    for (const seg of segs) {
      if (seg.length === 1) {
        svg.append(s('circle', { cx: x(seg[0][0]), cy: y(seg[0][1]), r: 4, style: `fill:${sr.color};stroke:var(--card);stroke-width:2` }));
        continue;
      }
      const d = smoothPath(seg.map(([hr, v]) => [x(hr), y(v)]));
      svg.append(s('path', { d: d + `L${x(seg[seg.length - 1][0])},${y(lo)}L${x(seg[0][0])},${y(lo)}Z`, style: `fill:url(#${gid})` }));
      svg.append(s('path', { d, style: `fill:none;stroke:${sr.color};stroke-width:2;stroke-linejoin:round;stroke-linecap:round` }));
    }
  });
  // jam paling ramai: titik bercincin, digambar setelah semua garis supaya tidak tertutup
  for (const sr of series) {
    for (const hr of busiest.get(sr.loc) || []) {
      if (sr.vals[hr] === null) continue;
      svg.append(s('circle', { cx: x(hr), cy: y(sr.vals[hr]), r: 5, style: `fill:${sr.color};stroke:var(--card);stroke-width:2` }));
    }
  }

  const cross = s('g', { style: 'display:none;pointer-events:none' });
  svg.append(cross);
  const hit = s('rect', { x: m.l - step / 2, y: 0, width: W - m.l - m.r + step, height: H, style: 'fill:transparent' });
  svg.append(hit);
  wrap.append(svg);

  const show = hr => {
    hourlyHover = hr;
    cross.replaceChildren(s('line', { class: 'cross', x1: x(hr), x2: x(hr), y1: m.t, y2: H - m.b }));
    const rows = [];
    for (const sr of series) {
      const v = sr.vals[hr];
      if (v === null) continue;
      cross.append(s('circle', { cx: x(hr), cy: y(v), r: 5, style: `fill:${sr.color};stroke:var(--card);stroke-width:2` }));
      const isBusy = (busiest.get(sr.loc) || []).includes(hr);
      rows.push(h('div', {}, key(sr.loc), sr.loc, isBusy ? h('em', { text: 'ramai' }) : null, h('strong', { text: fmt1(v) })));
    }
    cross.style.display = '';
    tip.replaceChildren(h('b', { text: 'Pukul ' + hh(hr) + ' sampai ' + String(hr).padStart(2, '0') + '.59' }),
      ...(rows.length ? rows : [h('div', { text: 'Belum ada data di jam ini' })]),
      rows.length ? h('div', { style: 'margin-top:4px;font-size:11px', text: 'dB relatif' }) : null);
    tip.hidden = false;
    const tw = tip.offsetWidth;
    const left = x(hr) + 14 + tw > W ? x(hr) - 14 - tw : x(hr) + 14;
    tip.style.left = Math.max(0, left) + 'px';
    tip.style.top = m.t + 'px';
  };
  const hide = () => { hourlyHover = null; cross.style.display = 'none'; tip.hidden = true; };
  const pick = e => {
    const rect = svg.getBoundingClientRect();
    show(Math.max(0, Math.min(23, Math.round((e.clientX - rect.left - m.l) / step))));
  };
  hit.addEventListener('pointermove', pick);
  hit.addEventListener('pointerdown', pick);
  hit.addEventListener('pointerleave', hide);
  if (hourlyHover !== null) show(hourlyHover);
}

// Kurva halus yang tidak melewati titik data ke atas atau ke bawah (monotone cubic, gaya Fritsch-Carlson).
function smoothPath(p) {
  const n = p.length;
  const dx = [], dy = [], sl = [];
  for (let i = 0; i < n - 1; i++) { dx.push(p[i + 1][0] - p[i][0]); dy.push(p[i + 1][1] - p[i][1]); sl.push(dy[i] / dx[i]); }
  const t = [sl[0]];
  for (let i = 1; i < n - 1; i++) t.push(sl[i - 1] * sl[i] <= 0 ? 0 : 3 * (dx[i - 1] + dx[i]) /
    ((2 * dx[i] + dx[i - 1]) / sl[i - 1] + (dx[i] + 2 * dx[i - 1]) / sl[i]));
  t.push(sl[n - 2]);
  let d = `M${p[0][0].toFixed(1)},${p[0][1].toFixed(1)}`;
  for (let i = 0; i < n - 1; i++) {
    const c = dx[i] / 3;
    d += `C${(p[i][0] + c).toFixed(1)},${(p[i][1] + c * t[i]).toFixed(1)} ${(p[i + 1][0] - c).toFixed(1)},` +
      `${(p[i + 1][1] - c * t[i + 1]).toFixed(1)} ${p[i + 1][0].toFixed(1)},${p[i + 1][1].toFixed(1)}`;
  }
  return d;
}

function renderBusiest(series, busiest) {
  const items = series.filter(sr => (busiest.get(sr.loc) || []).length).map(sr =>
    h('span', {}, key(sr.loc), sr.loc + ': ', h('b', { text: busiest.get(sr.loc).map(hh).join(', ') })));
  $('busiest').replaceChildren(...(items.length
    ? [h('span', { style: 'background:none;padding-left:0;font-weight:600', text: 'Jam paling ramai' }), ...items]
    : series.length ? [h('span', { style: 'background:none;padding-left:0', text: 'Jam paling ramai muncul setelah tiap jam punya sedikitnya 5 pembacaan.' })] : []));
}

function renderHourlyTable(series) {
  const hours = [...Array(24).keys()].filter(hr => series.some(sr => sr.vals[hr] !== null));
  if (!hours.length) {
    $('hourly-table').replaceChildren(h('p', { class: 'section-sub', text: 'Belum ada data.' }));
    return;
  }
  $('hourly-table').replaceChildren(h('table', {},
    h('thead', {}, h('tr', {}, h('th', { scope: 'col', text: 'Jam' }), series.map(sr => h('th', { scope: 'col', class: 'num', text: sr.loc })))),
    h('tbody', {}, hours.map(hr => h('tr', {}, h('td', { text: hh(hr) }),
      series.map(sr => h('td', { class: 'num', text: fmt1(sr.vals[hr]) })))))));
}

function renderTable() {
  const rows = [];
  if (state) {
    for (const r of state.ranking) {
      rows.push(h('tr', {},
        h('td', { text: String(r.rank ?? '-') }),
        h('td', {}, h('div', { class: 'cell-loc' }, key(r.location),
          h('span', {}, r.location, h('span', { class: 'cell-sub', text: r.device_id })))),
        h('td', {}, h('div', { class: 'bar' },
          h('div', { class: 'bar-track', 'aria-hidden': 'true' }, h('div', { class: 'bar-fill', style: `width:${Math.max(0, Math.min(100, r.score))}%` })),
          h('strong', { text: fmt1(r.score) }))),
        h('td', {}, chip(r.status)),
        h('td', {}, fmt0(r.lux) + ' lux', h('span', { class: 'cell-sub', text: lightText(r) })),
        h('td', {}, fmt1(r.db) + ' dB relatif', h('span', { class: 'cell-sub', text: r.noise_level ? NOISE_LABEL[r.noise_level] : '-' })),
        h('td', { text: SOURCE_LABEL[r.lux_source] || r.lux_source || '-' })));
    }
    const inactive = (id, label, note) => h('tr', { class: 'inactive' },
      h('td', { text: '-' }),
      h('td', {}, h('div', { class: 'cell-loc' }, h('span', {}, knownLocation.get(id) || id, h('span', { class: 'cell-sub', text: id })))),
      h('td', { colspan: 5 }, h('span', { class: 'chip flat', text: label }), ' ', note));
    for (const id of state.waiting_light) rows.push(inactive(id, 'Menunggu cahaya', 'belum ada lux dari Termux'));
    for (const id of state.offline) rows.push(inactive(id, 'Offline', 'keluar dari peringkat'));
  }
  if (!rows.length) rows.push(h('tr', {}, h('td', { colspan: 7, style: 'color:var(--muted)', text: state ? 'Belum ada lokasi.' : 'Belum ada data.' })));
  $('rank-body').replaceChildren(...rows);
  $('rank-sub').textContent = state && state.mode
    ? 'Urut dari skor kenyamanan tertinggi, mode ' + MODE_LABEL[state.mode].toLowerCase() + '.'
    : 'Urut dari skor kenyamanan tertinggi.';
}

function renderFreshness() {
  if (!state || state.updated_ms === null) {
    $('updated').textContent = 'Belum ada pembaruan';
    return;
  }
  $('updated').textContent = 'Diperbarui ' + clock(state.updated_ms) + (DEMO ? ' (data contoh)' : '');
  if (DEMO) return;
  // pesan retained tetap dikirim broker walau hub sudah mati, jadi umur data dicek dari updated_ms
  if (Date.now() - state.updated_ms <= STALE_AFTER_MS) clearNotice('stale');
  else if (noticeSource() !== 'conn') {
    setNotice('Hub tidak mengirim data sejak ' + clock(state.updated_ms) + '. Angka di bawah mungkin sudah lama; periksa apakah hub.py masih berjalan.', 'warn', 'stale');
  }
}

// Satu baris pemberitahuan. src mencatat asalnya (conn, stale, mode) supaya tiap bagian hanya menghapus pesannya sendiri.
function setNotice(text, kind, src) {
  const el = $('notice');
  el.textContent = text;
  el.className = 'notice' + (kind ? ' ' + kind : '');
  el.dataset.src = src;
  el.hidden = false;
}

function clearNotice(src) {
  const el = $('notice');
  if (src && el.dataset.src !== src) return;
  el.hidden = true;
  el.dataset.src = '';
}

function noticeSource() { return $('notice').hidden ? '' : $('notice').dataset.src; }

// ---------- mode ----------

function renderMode() {
  const current = state ? state.mode : null;
  const usable = DEMO || (client && client.connected);
  document.querySelectorAll('.mode-btn').forEach(btn => {
    const m = btn.dataset.mode;
    btn.setAttribute('aria-pressed', String((pendingMode || current) === m));
    btn.classList.toggle('pending', pendingMode === m);
    btn.textContent = MODE_LABEL[m] + (pendingMode === m ? '...' : '');
    btn.disabled = !usable;
  });
}

function setMode(m) {
  if (!MODE_LABEL[m] || (state && state.mode === m && !pendingMode)) return;
  if (DEMO) { demoMode = m; handleState(demoState(Date.now())); return; }
  if (!client || !client.connected) return;
  client.publish(topicMode(pid), m, { qos: 1 });
  pendingMode = m;
  clearTimeout(pendingTimer);
  pendingTimer = setTimeout(() => {
    pendingMode = null;
    setNotice('Hub belum menanggapi pergantian mode. Pastikan hub.py berjalan dengan PROJECT_ID yang sama.', 'warn', 'mode');
    renderMode();
  }, MODE_TIMEOUT_MS);
  renderMode();
}

function clearPending() {
  pendingMode = null;
  clearTimeout(pendingTimer);
}

// ---------- koneksi ----------

function setConn(kind, text) {
  $('conn').className = 'conn' + (kind ? ' ' + kind : '');
  $('conn-text').textContent = text;
}

function resetData() {
  state = null;
  history.clear();
  knownLocation.clear();
  seriesSlot.clear();
  sparkHover.clear();
  hourlyHover = null;
  clearPending();
}

function connect(newPid) {
  disconnect();
  if (typeof mqtt === 'undefined') {
    setNotice('Pustaka MQTT gagal dimuat. Periksa koneksi internet lalu muat ulang halaman.', 'warn', 'conn');
    return;
  }
  pid = newPid;
  resetData();
  clearNotice();
  setConn('busy', 'Menghubungkan...');
  $('pid-btn').textContent = 'Putuskan';
  const c = mqtt.connect(BROKER_URL, {
    clientId: 'comfyspot-dash-' + Math.random().toString(16).slice(2, 10),   // harus unik, kalau kembar saling menendang
    reconnectPeriod: 3000,
    connectTimeout: 8000,
    clean: true
  });
  client = c;
  c.on('connect', () => {
    if (client !== c) return;
    setConn('on', 'Tersambung');
    clearNotice('conn');
    // subscribe di sini supaya diulang otomatis setelah sambung ulang; state retained langsung dikirim broker
    c.subscribe(topicState(pid), { qos: 0 });
    scheduleRender();
  });
  c.on('message', (topic, payload) => { if (client === c && topic === topicState(pid)) handlePayload(payload); });
  c.on('reconnect', () => { if (client === c) setConn('busy', 'Menghubungkan ulang...'); });
  c.on('offline', () => {
    if (client !== c) return;
    setConn('', 'Terputus');
    setNotice('Koneksi ke broker terputus. Mencoba lagi otomatis.', 'warn', 'conn');
    scheduleRender();
  });
  scheduleRender();
}

function disconnect() {
  const c = client;
  client = null;
  if (c) c.end(true);
  setConn('', 'Belum tersambung');
  $('pid-btn').textContent = 'Sambungkan';
}

$('pid-form').addEventListener('submit', e => {
  e.preventDefault();
  if (DEMO) return;
  if (client) {
    disconnect();
    resetData();
    clearNotice();
    scheduleRender();
    return;
  }
  const value = $('pid').value.trim();
  if (!ID_PATTERN.test(value)) {
    setNotice('PROJECT_ID hanya boleh huruf, angka, garis bawah, dan strip (maksimal 40 karakter).', 'warn', 'conn');
    $('pid').focus();
    return;
  }
  save('comfyspot.pid', value);   // kunci yang sama dengan halaman sensor
  connect(value);
});

document.querySelectorAll('.mode-btn').forEach(btn => btn.addEventListener('click', () => setMode(btn.dataset.mode)));

// ---------- tema ----------

const THEMES = ['auto', 'light', 'dark'];
const THEME_LABEL = { auto: 'otomatis', light: 'terang', dark: 'gelap' };
let theme = THEMES.includes(load('comfyspot.dashboard.theme')) ? load('comfyspot.dashboard.theme') : 'auto';

function applyTheme() {
  if (theme === 'auto') delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  $('theme-btn').textContent = 'Tema: ' + THEME_LABEL[theme];
}
$('theme-btn').addEventListener('click', () => {
  theme = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
  save('comfyspot.dashboard.theme', theme);
  applyTheme();
});

// ---------- data contoh (?demo) ----------
// Hanya untuk screenshot desain. Rumus skornya salinan shared/rules.py supaya angka contoh masuk akal.

const DEMO_NOW_HOUR = 15;
let demoMode = 'hening';
const DEMO_SITES = [
  { device_id: 'edge1', location: 'Selasar Gedung A', lux: 330, db: 42, ph: 0.3 },
  { device_id: 'edge2', location: 'Kantin', lux: 420, db: 62, ph: 1.7 },
  { device_id: 'edge3', location: 'Ruang Diskusi', lux: 240, db: 49, ph: 2.9 }
];
const DEMO_HOURLY = {
  'Selasar Gedung A': [7, 38.5, 40.2, 42.8, 44.1, 43.0, 45.6, 46.2, 43.4, 41.9],
  'Kantin': [7, 47.2, 50.8, 55.1, 58.3, 64.7, 67.9, 61.2, 57.5, 59.0],
  'Ruang Diskusi': [7, 41.0, 46.5, null, 52.3, 51.0, 47.8, 49.2, 53.6, 50.4]
};

function demoComfort(lux, db, mode) {
  const curve = [[40, 1], [55, 0.6], [75, 0]];
  let ns = curve[curve.length - 1][1];
  if (db <= curve[0][0]) ns = curve[0][1];
  else for (let i = 0; i < curve.length - 1; i++) {
    const [d0, s0] = curve[i], [d1, s1] = curve[i + 1];
    if (db <= d1) { ns = s0 + (s1 - s0) * (db - d0) / (d1 - d0); break; }
  }
  const w = mode === 'ramai' ? { light: 0.6, noise: 0.4 } : { light: 0.3, noise: 0.7 };
  return 100 * Math.min(1, Math.max(0, lux / 300)) ** w.light * ns ** w.noise;
}

function demoState(t) {
  const ranking = DEMO_SITES.map(site => {
    const lux = site.lux + 25 * Math.sin(t / 41000 + site.ph);
    const db = site.db + 2.2 * Math.sin(t / 23000 + site.ph) + 1.1 * Math.sin(t / 6100 + 2 * site.ph);
    const score = Math.round(demoComfort(lux, db, demoMode) * 10) / 10;
    return {
      device_id: site.device_id, location: site.location, score,
      status: score >= 75 ? 'hijau' : score >= 50 ? 'kuning' : 'merah',
      noise_level: db < 45 ? 'sepi' : db < 55 ? 'sedang' : 'ramai',
      lux: Math.round(lux * 10) / 10, lux_stable: site.device_id !== 'edge3', db: Math.round(db * 10) / 10, lux_source: 'termux'
    };
  }).sort((a, b) => b.score - a.score).map((r, i) => Object.assign(r, { rank: i + 1 }));
  const hourly = {}, busiest = {};
  for (const [loc, [start, ...vals]] of Object.entries(DEMO_HOURLY)) {
    const arr = Array(24).fill(null);
    vals.forEach((v, i) => { arr[start + i] = v; });
    hourly[loc] = arr;
    busiest[loc] = arr.map((v, hr) => [v, hr]).filter(([v]) => v !== null).sort((a, b) => b[0] - a[0]).slice(0, 3).map(([, hr]) => hr);
  }
  return cleanState({
    updated_ms: t, mode: demoMode, best: ranking[0].device_id, ranking,
    offline: ['edge4'], waiting_light: ['edge5'], hourly_db: hourly, busiest_hours: busiest
  });
}

function startDemo() {
  document.title = 'DATA CONTOH | ComfySpot Dashboard';
  $('demo-banner').hidden = false;
  $('pid').value = 'contoh';
  $('pid').disabled = true;
  $('pid-btn').disabled = true;
  setConn('on', 'Data contoh, tanpa broker');
  knownLocation.set('edge4', 'Perpustakaan Lt. 2');
  knownLocation.set('edge5', 'Gazebo Timur');
  // isi grafik mini dengan lima menit ke belakang supaya screenshot langsung penuh
  const now = Date.now();
  for (let t = now - HISTORY_MS + 1000; t < now; t += 2000) {
    for (const r of demoState(t).ranking) {
      const list = history.get(r.device_id) || [];
      list.push({ t, score: r.score });
      history.set(r.device_id, list);
    }
  }
  handleState(demoState(now));
  setInterval(() => handleState(demoState(Date.now())), 1000);
}

// ---------- mulai ----------

applyTheme();
if (window.ResizeObserver) new ResizeObserver(() => renderHourly(false)).observe($('hourly-wrap'));
setInterval(renderFreshness, 1000);

if (DEMO) {
  startDemo();
} else {
  const saved = load('comfyspot.pid');
  if (saved && ID_PATTERN.test(saved)) {
    $('pid').value = saved;
    connect(saved);
  }
}
render();
