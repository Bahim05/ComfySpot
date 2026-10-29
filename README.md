# ComfySpot

Sistem rekomendasi tempat belajar berbasis sensor cahaya dan suara. Tugas IoT "Praktek Multicommunication 2 Arah dan Multisensor", Informatika Undip.

Saat perpus penuh, mahasiswa harus cari tempat belajar lain tanpa tahu mana yang sedang tenang dan cukup terang. ComfySpot menaruh satu HP di tiap lokasi (selasar, kantin, ruang diskusi). HP mengukur cahaya dan kebisingan, lalu hub memberi skor kenyamanan dan peringkat tiap lokasi.

## Cara kerja

```
HP lokasi A --+                                     +-- dashboard (atur kebutuhan dan lihat peringkat)
              +-- MQTT --> broker (cloud) <-- MQTT -+
HP lokasi B --+                                     +-- hub (laptop): olah data, skor, peringkat
   telemetry mengalir ke atas, control mengalir ke bawah
```

Ada dua HP atau lebih sebagai edge device, dan masing-masing memakai dua sensor: sensor cahaya (lux) dan mikrofon. Di tiap HP, mikrofon dibaca oleh halaman web di Chrome dan sensor cahaya dibaca oleh skrip Python di Termux. Hub menggabungkan keduanya berdasarkan ID perangkat yang sama. Suara tidak direkam: browser hanya menghitung RMS (seberapa keras bunyinya) tiap detik dan mengirim angkanya.

Protokolnya MQTT ke broker publik HiveMQ. HP dan dashboard memakai MQTT over WebSocket (port 8884), sedangkan hub memakai MQTT over TLS (port 8883).

Komunikasinya dua arah, baik antara HP dan hub maupun antara dashboard dan hub:

- HP ke hub: telemetry (RMS mikrofon dari halaman sensor) dan light (lux dari Termux).
- Hub ke HP: control, berisi skor, peringkat, dan warna lampu status lokasi itu.
- Dashboard ke hub: criteria, yaitu kebutuhan belajar (minimal terang dan batas bising) yang diatur pengguna.
- Hub ke dashboard: state, berisi peringkat semua lokasi, kriteria yang berlaku, dan statistik per jam.

Semua perangkat berada di WiFi atau hotspot yang sama.

## Pengolahan data

Dua jenis pengolahan (`shared/processing.py`):

1. Cahaya: rata-rata dalam jendela 5 detik dan simpangan baku untuk menilai kestabilan cahaya.
2. Suara: RMS sinyal mikrofon, dirata-ratakan berdasarkan energi dalam jendela 2 detik, lalu diubah ke desibel relatif: `db = 20 * log10(rms / 1e-4) + offset_kalibrasi`.

Panjang jendela diatur lewat `LIGHT_WINDOW_S` dan `MIC_WINDOW_S` di `shared/processing.py`. Jendela pendek membuat lampu bereaksi cepat, jendela panjang membuat angkanya lebih halus. Suara dibuat lebih pendek karena berubah cepat. Dengan jendela 10 detik, lampu butuh sekitar 10 detik untuk kembali normal setelah suara keras berhenti.

Respons real time: halaman sensor mengirim RMS dua kali per detik, dan hub mem-publish control segera setelah data masuk (paling cepat tiap 0,25 detik), tidak menunggu putaran berkala. Hasil ukur dengan broker lokal: lampu berubah sekitar 0,5 detik setelah suara keras muncul dan kembali normal sekitar 2 detik setelah suara berhenti. Lewat HiveMQ ditambah waktu jaringan.

## Algoritma

Di `shared/rules.py`:

Skor memakai dua kriteria yang diatur dari dashboard dan berlaku untuk semua lokasi sekaligus:

- **Minimal terang** (`lux_target`, standar 300 lux): cahaya yang dianggap sudah cukup untuk membaca. Diterima hub dari 50 sampai 1000 lux.
- **Batas bising** (`noise_limit`, standar 55 dB relatif): kebisingan yang masih bisa ditoleransi. Diterima hub dari 40 sampai 80 dB.

Rumusnya:

```
skor_terang = min(1, lux / lux_target)
skor_sepi   = interpolasi linear kurva (limit - 15, 1,0), (limit, 0,6), (limit + 20, 0,0)
              (1,0 jika db <= limit - 15, 0,0 jika db >= limit + 20)
skor        = 100 * sqrt(skor_terang * skor_sepi)
```

- Dengan kriteria standar, skor sepi bernilai 1,0 sampai 40 dB, turun ke 0,6 di 55 dB, dan ke 0 di 75 dB. Menaikkan batas bising menggeser seluruh kurva: dengan batas 70 dB, suara 60 dB dinilai sama dengan 45 dB pada batas standar.
- Kedua faktor berbobot sama. Kebutuhan yang berbeda diatur lewat kedua kriteria itu, bukan lewat bobot.
- Alasan memakai rata-rata geometrik: pada rata-rata biasa, suara yang tenang bisa menutupi ruangan yang gelap gulita (lux 0 dan 30 dB tetap mendapat 50, kuning). Pada rata-rata geometrik, kalau salah satu skor 0 maka hasilnya 0, karena tempat yang gelap atau terlalu bising tidak layak untuk belajar walau faktor yang lain bagus.
- Lampu status: hijau mulai skor 75, kuning mulai 50, selebihnya merah.
- Lokasi diurutkan dari skor tertinggi. Perangkat yang diam lebih dari 15 detik dianggap offline dan keluar dari peringkat.
- Rata-rata kebisingan per jam (0 sampai 23) dihitung sepanjang hari, dari sini diturunkan jam paling ramai tiap lokasi.

Nilai standar 300 lux dan 55 dB berasal dari standar ruang baca (SNI 03-6197-2000 dan SNI 03-6386-2000) yang masih perlu dicocokkan dengan dokumen aslinya.

## Topic MQTT

| Topic | Arah | Isi |
|---|---|---|
| `comfyspot/<PROJECT_ID>/edge/<device>/telemetry` | HP ke hub | `{"device_id","location","sequence","mic_rms","sent_at_ms"}`, ditambah `lux` dan `lux_source` jika halaman punya data cahaya sendiri |
| `comfyspot/<PROJECT_ID>/edge/<device>/light` | HP ke hub | `{"device_id","lux","lux_source","sent_at_ms"}` dari skrip Termux |
| `comfyspot/<PROJECT_ID>/edge/<device>/control` | hub ke HP | `{"rank","score","status","best","lux","lux_source","db","updated_ms"}` |
| `comfyspot/<PROJECT_ID>/control/criteria` | dashboard ke hub | `{"lux_target","noise_limit"}`, retained supaya hub yang baru nyala langsung memakai nilai terakhir |
| `comfyspot/<PROJECT_ID>/state` | hub ke dashboard | peringkat, `criteria` yang berlaku, offline, dB per jam, jam paling ramai |

## Menjalankan

Butuh Python 3.9 atau lebih baru.

```
pip install -r requirements.txt
cp .env.example .env        # lalu isi PROJECT_ID dengan id unik kelompok
python hub.py
```

Tanpa HP, jalankan dua simulator di terminal lain:

```
python edge_sim.py --device edge1 --location Selasar --profile quiet
python edge_sim.py --device edge2 --location Kantin --profile noisy
```

Dengan HP asli, tiap HP menjalankan dua hal bersamaan, dengan ID perangkat yang sama (`edge1`, `edge2`, dan seterusnya):

1. Mikrofon: buka halaman `sensor/` di Chrome. Mikrofon di browser hanya boleh dipakai lewat HTTPS, jadi buka dari GitHub Pages (Settings, Pages, pilih branch `main`). Isi PROJECT_ID yang sama, ID perangkat, dan nama lokasi, lalu tekan Mulai. Biarkan Chrome di depan dengan layar menyala.
2. Cahaya: di Termux jalankan `python light_termux.py --device edge1`. Skrip ini memanggil `termux-sensor` tiap detik dan mengirim lux ke topic `light`.

Setup Termux (sekali saja per HP). Pasang F-Droid, lalu pasang Termux dan Termux:API dari F-Droid, dan buka Termux:

```
pkg update
pkg install python git termux-api
git clone https://github.com/Bahim05/ComfySpot
cd ComfySpot
pip install paho-mqtt python-dotenv
cp .env.example .env
nano .env                   # ganti PROJECT_ID, simpan dengan Ctrl+O lalu Enter, keluar dengan Ctrl+X
termux-sensor -l            # opsional: lihat daftar sensor HP
termux-wake-lock            # agar Termux tidak ditidurkan Android saat Chrome di depan
python light_termux.py --device edge1
```

Skrip memilih sensor cahaya sekitar otomatis dari daftar `termux-sensor -l` (di Xiaomi misalnya "Ambient Light Sensor Non-wakeup", bukan "Optimization light fov Sensor"). Kalau pilihannya salah, tentukan sendiri dengan `--sensor "<nama sensor>"`. Hub hanya memakai lux Termux yang umurnya kurang dari 5 detik. Kalau skrip Termux berhenti, HP itu dilaporkan menunggu data cahaya dan keluar dari peringkat setelah 15 detik.

Chrome Android versi baru tidak lagi menyediakan sensor cahaya, itu sebabnya cahaya dibaca lewat Termux. Kalau suatu browser mendukung `AmbientLightSensor`, halaman memakainya langsung. Isian lux manual di halaman hanya untuk menguji alur dan ditandai `lux_source: manual`, jadi tidak boleh dipakai untuk pengujian akhir.

## Membuka dashboard

Dashboard ada di folder `dashboard/` dan ikut dihosting GitHub Pages bersama halaman sensor, jadi alamatnya `https://bahim05.github.io/ComfySpot/dashboard/`. Bisa juga dibuka di laptop tanpa GitHub Pages:

```
python -m http.server 8000
```

lalu buka `http://localhost:8000/dashboard/`. Isi PROJECT_ID yang sama dengan di `.env`, lalu tekan Sambungkan. Dashboard langsung menerima state terakhir dari hub (pesan retained) dan memperbaruinya tiap detik. Panel Kebutuhan belajar mengatur minimal terang dan batas bising lewat dua slider, dan tombol Kembalikan standar memulihkan 300 lux dan 55 dB. Nilainya dikirim ke hub saat slider dilepas, lalu hub menghitung ulang skor semua lokasi.

Yang tampil: lokasi terbaik beserta skornya, kartu tiap lokasi (skor, lampu status, lux, dB relatif, dan grafik skor beberapa menit terakhir), grafik kebisingan per jam dengan jam paling ramai, dan tabel peringkat yang menandai HP offline atau yang masih menunggu data cahaya. Grafik mini di kartu dikumpulkan oleh browser selama halaman terbuka, jadi kosong lagi setelah halaman dimuat ulang. PROJECT_ID terakhir diingat browser, jadi lain kali dashboard tersambung sendiri.

Untuk screenshot desain tanpa hub, buka `dashboard/?demo`. Halaman itu memakai data buatan yang diberi label DATA CONTOH dan tidak tersambung ke broker, jadi jangan dipakai sebagai hasil pengujian.

## Kalibrasi

HP tidak punya mikrofon terkalibrasi, jadi desibelnya relatif. Untuk mendekatkan ke sound meter: ukur beberapa kondisi suara dengan HP dan aplikasi sound meter secara bersamaan, hitung `calibration_offset(db_hp, db_meter)` dari `shared/processing.py`, lalu tulis hasilnya di `calibration.json`, misalnya `{"edge1": 3.5, "edge2": -2.0}`. Hub membaca file itu saat dijalankan.

## Pengujian

```
python -m unittest discover -s tests -t .
```

Hub mencatat setiap pembacaan ke `data/log.csv`, bahan grafik pengujian. Statistik per jam disimpan di `data/hourly.json`.

## Keterbatasan

- Desibel dari HP bersifat relatif, bukan angka resmi.
- Yang terukur adalah kebisingan, bukan jumlah orang.
- Sensor cahaya HP bukan lux meter terkalibrasi.
- Broker publik dipakai bersama. Karena itu PROJECT_ID harus unik dan tidak ada data pribadi yang dikirim.

## Struktur

```
hub.py            hub di laptop
edge_sim.py       simulator HP lokasi
light_termux.py   pembaca sensor cahaya untuk HP lokasi (Termux)
shared/           pengolahan data, algoritma, topic, konfigurasi
sensor/           halaman sensor untuk HP (HTML, JS)
dashboard/        dashboard web (HTML, JS)
tests/            tes unit
data/             log dan statistik hasil jalan (tidak di-commit)
```
