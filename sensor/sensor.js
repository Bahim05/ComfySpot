// Halaman sensor untuk HP lokasi (edge device).
// Alur: baca mikrofon dan sensor cahaya -> tiap detik hitung RMS suara -> kirim ke broker lewat MQTT over WebSocket
// -> (cahaya: dari sensor browser jika ada, atau dari skrip Termux lewat hub) -> terima perintah dari hub (skor, peringkat, warna lampu status) dan tampilkan.
// Suara tidak pernah direkam atau dikirim. Yang keluar dari HP hanya angka: RMS suara dan lux.

const $ = id => document.getElementById(id);

// Nama topic harus sama dengan shared/topics.py
const topicTelemetry = (pid, dev) => `comfyspot/${pid}/edge/${dev}/telemetry`;
const topicControl = (pid, dev) => `comfyspot/${pid}/edge/${dev}/control`;

const ID_PATTERN = /^[A-Za-z0-9_-]{1,40}$/;   // melarang / + # supaya tidak merusak struktur topic
const SEND_EVERY_MS = 500;                    // dua kali per detik, supaya hub dan lampu bereaksi cepat
const POLL_EVERY_MS = 40;                     // buffer 2048 sampel pada 48 kHz berdurasi sekitar 43 ms

let client = null;
let audioCtx = null;
let micStream = null;
let analyser = null;
let buf = null;
let pollTimer = null;
let sendTimer = null;
let wakeLock = null;
let lightSensor = null;

let sumSq = 0;           // jumlah kuadrat sampel sejak pengiriman terakhir
let nSamples = 0;
let sensorLux = null;    // pembacaan dari sensor cahaya HP
let manualLux = null;    // isian manual, hanya dipakai kalau sensor tidak tersedia
let seq = 0;
let cfg = null;          // { pid, device, location }
let hubLux = false;      // true jika angka cahaya yang tampil berasal dari hub (dikirim balik lewat control)

// ---------- tampilan ----------

function setMsg(text, kind) {
  const el = $('msg');
  el.textContent = text;
  el.className = 'hint' + (kind ? ' ' + kind : '');
}

function setStatus(status, text, sub) {
  document.body.classList.remove('s-hijau', 's-kuning', 's-merah');
  if (status === 'hijau' || status === 'kuning' || status === 'merah') document.body.classList.add('s-' + status);
  $('status-text').textContent = text;
  $('status-sub').textContent = sub;
}

function currentLux() {
  if (sensorLux !== null) return { value: sensorLux, source: 'sensor' };
  if (manualLux !== null) return { value: manualLux, source: 'manual' };
  return null;
}

// ---------- mikrofon ----------

async function startMic() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    throw Object.assign(new Error('no api'), { name: 'NoMicApi' });
  }
  // Pemrosesan otomatis browser dimatikan agar angka RMS mencerminkan suara asli, bukan suara yang sudah diratakan
  micStream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
    video: false
  });
  const AC = window.AudioContext || window.webkitAudioContext;
  audioCtx = new AC();
  if (audioCtx.state === 'suspended') await audioCtx.resume();
  analyser = audioCtx.createAnalyser();
  analyser.fftSize = 2048;
  audioCtx.createMediaStreamSource(micStream).connect(analyser);
  buf = new Float32Array(analyser.fftSize);
  pollTimer = setInterval(pollMic, POLL_EVERY_MS);
}

function pollMic() {
  analyser.getFloatTimeDomainData(buf);
  // Kuadratkan dulu karena sampel suara bergantian positif dan negatif; kalau dirata-rata langsung hasilnya mendekati 0
  for (let i = 0; i < buf.length; i++) sumSq += buf[i] * buf[i];
  nSamples += buf.length;
}

function stopMic() {
  clearInterval(pollTimer);
  pollTimer = null;
  if (micStream) micStream.getTracks().forEach(t => { try { t.stop(); } catch (e) {} });
  if (audioCtx) { try { audioCtx.close(); } catch (e) {} }
  micStream = null; audioCtx = null; analyser = null;
}

// ---------- sensor cahaya ----------

function startLight() {
  sensorLux = null;
  if (!('AmbientLightSensor' in window)) {
    $('manual-wrap').hidden = false;
    $('light-note').textContent = 'Browser ini tidak membaca sensor cahaya. Cahaya diambil dari Termux di HP yang sama (python light_termux.py --device ' + ($('device').value.trim() || 'edge1') + '). Isian manual di bawah hanya untuk uji alur, kosongkan kalau memakai Termux.';
    return;
  }
  try {
    lightSensor = new AmbientLightSensor({ frequency: 2 });
    lightSensor.addEventListener('reading', () => {
      sensorLux = lightSensor.illuminance;
      $('manual-wrap').hidden = true;
      $('light-note').textContent = 'Sensor cahaya aktif.';
    });
    lightSensor.addEventListener('error', e => {
      $('manual-wrap').hidden = false;
      $('light-note').textContent = 'Sensor cahaya gagal (' + (e.error && e.error.name || 'error') + '). Pakai Termux untuk cahaya, atau isi lux manual hanya untuk uji alur.';
    });
    lightSensor.start();
    $('light-note').textContent = 'Menunggu pembacaan sensor cahaya...';
    // Jika dalam 3 detik belum ada pembacaan, tampilkan isian manual sebagai cadangan
    setTimeout(() => { if (sensorLux === null && client) $('manual-wrap').hidden = false; }, 3000);
  } catch (e) {
    $('manual-wrap').hidden = false;
    $('light-note').textContent = 'Sensor cahaya ditolak (' + e.name + '). Pakai Termux untuk cahaya, atau isi lux manual hanya untuk uji alur.';
  }
}

function stopLight() {
  if (lightSensor) { try { lightSensor.stop(); } catch (e) {} }
  lightSensor = null;
  sensorLux = null;
}

// ---------- pengiriman ----------

function sendTick() {
  const rms = nSamples > 0 ? Math.sqrt(sumSq / nSamples) : 0;
  sumSq = 0; nSamples = 0;
  $('v-rms').textContent = rms.toFixed(5);

  // Cahaya dari halaman hanya dikirim jika ada. Kalau tidak ada, hub memakai lux dari skrip Termux
  // di HP yang sama (topic light) dan menggabungkannya dengan RMS ini.
  const lux = currentLux();
  if (lux) {
    $('v-lux').textContent = lux.value.toFixed(1);
    $('v-src').textContent = lux.source;
  } else if (!hubLux) {
    // belum ada angka dari hub; setelah hub menggabungkan lux Termux, angkanya tampil lewat handleControl
    $('v-lux').textContent = '-';
    $('v-src').textContent = 'menunggu Termux';
  }

  if (!client || !client.connected) return;
  seq += 1;
  const payload = {
    device_id: cfg.device, location: cfg.location, sequence: seq,
    mic_rms: Math.round(rms * 1e5) / 1e5, sent_at_ms: Date.now()
  };
  if (lux) {
    payload.lux = Math.round(lux.value * 10) / 10;
    payload.lux_source = lux.source;
  }
  client.publish(topicTelemetry(cfg.pid, cfg.device), JSON.stringify(payload), { qos: 0 });
  $('v-seq').textContent = String(seq);
}

function handleControl(payload) {
  let ctl;
  try { ctl = JSON.parse(payload.toString()); } catch (e) { return; }
  if (!ctl || typeof ctl !== 'object') return;
  const labels = { hijau: 'Nyaman', kuning: 'Cukup', merah: 'Kurang nyaman' };
  const status = labels[ctl.status] ? ctl.status : null;
  if (!status) return;
  // Hub mengirim balik angka yang dipakainya, jadi cahaya dari Termux dan dB ikut terlihat di halaman ini
  if (typeof ctl.db === 'number') $('v-db').textContent = ctl.db.toFixed(1);
  if (typeof ctl.lux === 'number' && currentLux() === null) {
    hubLux = true;
    $('v-lux').textContent = ctl.lux.toFixed(1);
    $('v-src').textContent = ctl.lux_source === 'termux' ? 'Termux (lewat hub)' : String(ctl.lux_source);
  }
  setStatus(status, labels[status],
    'Peringkat ' + ctl.rank + ' | skor ' + ctl.score + (ctl.best ? ' | terbaik' : ''));
}

// ---------- koneksi ----------

async function start() {
  const url = $('url').value.trim();
  const pid = $('pid').value.trim();
  const device = $('device').value.trim();
  const location = $('location').value.trim() || device;

  if (!/^wss?:\/\//.test(url)) { setMsg('Alamat harus diawali wss:// atau ws://', 'err'); return; }
  if (!ID_PATTERN.test(pid)) { setMsg('PROJECT_ID hanya boleh huruf, angka, garis bawah, dan strip.', 'err'); return; }
  if (!ID_PATTERN.test(device)) { setMsg('ID perangkat hanya boleh huruf, angka, garis bawah, dan strip.', 'err'); return; }
  cfg = { pid, device, location: location.slice(0, 40) };
  saveForm();

  const btn = $('connect-btn');
  btn.disabled = true;
  setMsg('Meminta izin mikrofon...', '');
  try {
    await startMic();
  } catch (e) {
    const why = e.name === 'NotAllowedError' ? 'izin mikrofon ditolak'
      : e.name === 'NoMicApi' ? 'browser menolak mikrofon, buka halaman lewat HTTPS'
      : 'mikrofon tidak bisa dibuka';
    setMsg('Gagal: ' + why + '.', 'err');
    btn.disabled = false;
    return;
  }
  btn.disabled = false;
  startLight();
  keepAwake();

  btn.textContent = 'Berhenti';
  $('v-mqtt').textContent = 'Menghubungkan...';
  setMsg('Menghubungkan ke broker...', '');
  const mine = mqtt.connect(url, {
    username: $('username').value.trim() || undefined,
    password: $('password').value || undefined,
    clientId: 'comfyspot-' + device + '-' + Math.random().toString(16).slice(2, 8),   // harus unik, kalau kembar saling menendang
    reconnectPeriod: 2000,
    connectTimeout: 8000,
    clean: true
  });
  client = mine;
  mine.on('connect', () => {
    $('v-mqtt').textContent = 'Tersambung';
    setMsg('Mengirim data ke ' + topicTelemetry(cfg.pid, cfg.device), 'ok');
    // subscribe di sini supaya diulang otomatis setelah sambung ulang
    mine.subscribe(topicControl(cfg.pid, cfg.device), { qos: 1 });
  });
  mine.on('message', (topic, payload) => { if (topic === topicControl(cfg.pid, cfg.device)) handleControl(payload); });
  mine.on('reconnect', () => { $('v-mqtt').textContent = 'Menghubungkan ulang...'; });
  mine.on('offline', () => { $('v-mqtt').textContent = 'Terputus'; });
  mine.on('error', err => {
    if (err && (err.code === 4 || err.code === 5)) {
      setMsg('Login ditolak broker. Periksa username dan password.', 'err');
      $('v-mqtt').textContent = 'Login ditolak';
      stop();
    }
  });
  sendTimer = setInterval(sendTick, SEND_EVERY_MS);
}

function stop() {
  clearInterval(sendTimer);
  sendTimer = null;
  const c = client;
  client = null;
  if (c) c.end(true);
  stopMic();
  stopLight();
  sumSq = 0; nSamples = 0;
  hubLux = false;
  $('v-db').textContent = '-';
  $('manual-wrap').hidden = true;
  $('connect-btn').textContent = 'Mulai';
  $('v-mqtt').textContent = 'Belum terhubung';
  setStatus(null, 'Belum ada perintah', 'Menunggu hub');
}

// ---------- layar tetap menyala ----------

async function keepAwake() {
  try {
    if ('wakeLock' in navigator && !wakeLock) {
      wakeLock = await navigator.wakeLock.request('screen');
      wakeLock.addEventListener('release', () => { wakeLock = null; });
    }
  } catch (e) { /* tidak didukung atau ditolak, abaikan */ }
}
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && client) keepAwake();
});

// ---------- ingatan form ----------
// Isian disimpan di localStorage; password hanya selama tab terbuka (sessionStorage)

const FIELDS = ['url', 'pid', 'device', 'location', 'username'];
function saveForm() {
  try {
    FIELDS.forEach(f => localStorage.setItem('comfyspot.' + f, $(f).value));
    sessionStorage.setItem('comfyspot.password', $('password').value);
  } catch (e) { /* penyimpanan diblokir, abaikan */ }
}
function loadForm() {
  try {
    FIELDS.forEach(f => { const v = localStorage.getItem('comfyspot.' + f); if (v) $(f).value = v; });
    const p = sessionStorage.getItem('comfyspot.password'); if (p) $('password').value = p;
  } catch (e) { /* abaikan */ }
}

// ---------- pemasangan event ----------

$('connect-form').addEventListener('submit', e => {
  e.preventDefault();
  if (client) stop(); else start();
});
$('lux-manual').addEventListener('input', () => {
  const v = parseFloat($('lux-manual').value);
  manualLux = Number.isFinite(v) && v >= 0 ? v : null;
});

loadForm();
