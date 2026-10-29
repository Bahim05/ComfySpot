# ComfySpot

Sistem rekomendasi tempat belajar berbasis sensor cahaya dan suara. Tugas IoT "Praktek Multicommunication 2 Arah dan Multisensor", Informatika Undip.

Saat perpus penuh, mahasiswa harus cari tempat belajar lain tanpa tahu mana yang sedang tenang dan cukup terang. ComfySpot menaruh satu HP di tiap lokasi (selasar, kantin, ruang diskusi). HP mengukur cahaya dan kebisingan, lalu hub memberi skor kenyamanan dan peringkat tiap lokasi.

## Cara kerja

```
HP lokasi A --+                                     +-- dashboard (pilih mode, lihat peringkat)
              +-- MQTT --> broker (cloud) <-- MQTT -+
HP lokasi B --+                                     +-- hub (laptop): olah data, skor, peringkat
   telemetry mengalir ke atas, control mengalir ke bawah
```

Ada dua HP atau lebih sebagai edge device, dan masing-masing memakai dua sensor: sensor cahaya (lux) dan mikrofon. Suara tidak direkam. Browser hanya menghitung RMS (seberapa keras bunyinya) tiap detik dan mengirim angkanya.

Protokolnya MQTT ke broker publik HiveMQ. HP dan dashboard memakai MQTT over WebSocket (port 8884), sedangkan hub memakai MQTT over TLS (port 8883).

Komunikasinya dua arah. HP mengirim telemetry ke hub, lalu hub mengirim balik skor, peringkat, dan warna lampu status ke tiap HP. Dashboard mengirim pilihan mode ("hening" atau "ramai") ke hub. Semua perangkat berada di WiFi atau hotspot yang sama.

## Pengolahan data

Dua jenis pengolahan (`shared/processing.py`):

1. Cahaya: rata-rata dalam jendela 10 detik dan simpangan baku untuk menilai kestabilan cahaya.
2. Suara: RMS sinyal mikrofon, dirata-ratakan berdasarkan energi dalam jendela 10 detik, lalu diubah ke desibel relatif: `db = 20 * log10(rms / 1e-4) + offset_kalibrasi`.

## Algoritma

Di `shared/rules.py`:

- Skor cahaya naik linear sampai 300 lux. Skor kebisingan 1,0 sampai 40 dB, turun ke 0,6 di 55 dB, dan ke 0 di 75 dB.
- Skor kenyamanan 0 sampai 100 adalah jumlah berbobot kedua skor itu. Mode "hening" memberi bobot 0,3 untuk cahaya dan 0,7 untuk suara. Mode "ramai" memberi 0,6 dan 0,4.
- Lampu status: hijau mulai skor 75, kuning mulai 50, selebihnya merah.
- Lokasi diurutkan dari skor tertinggi. Perangkat yang diam lebih dari 15 detik dianggap offline dan keluar dari peringkat.
- Rata-rata kebisingan per jam (0 sampai 23) dihitung sepanjang hari, dari sini diturunkan jam paling ramai tiap lokasi.

Angka acuan 300 lux dan 55 dB berasal dari standar ruang baca (SNI 03-6197-2000 dan SNI 03-6386-2000) yang masih perlu dicocokkan dengan dokumen aslinya.

## Topic MQTT

| Topic | Arah | Isi |
|---|---|---|
| `comfyspot/<PROJECT_ID>/edge/<device>/telemetry` | HP ke hub | `{"device_id","location","sequence","lux","mic_rms","lux_source","sent_at_ms"}` |
| `comfyspot/<PROJECT_ID>/edge/<device>/control` | hub ke HP | `{"rank","score","status","mode","best","updated_ms"}` |
| `comfyspot/<PROJECT_ID>/control/mode` | dashboard ke hub | teks `hening` atau `ramai` |
| `comfyspot/<PROJECT_ID>/state` | hub ke dashboard | peringkat, offline, dB per jam, jam paling ramai |

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

Dengan HP asli, buka halaman `sensor/` di Chrome. Mikrofon di browser hanya boleh dipakai lewat HTTPS, jadi halaman itu sebaiknya dibuka dari GitHub Pages (Settings, Pages, pilih branch `main`). Isi PROJECT_ID yang sama, ID perangkat unik (`edge1`, `edge2`), dan nama lokasi, lalu tekan Mulai.

Sensor cahaya di Chrome Android perlu diaktifkan di `chrome://flags/#enable-generic-sensor-extra-classes`. Kalau tidak tersedia, halaman menyediakan isian lux manual hanya untuk menguji alur. Data manual ditandai `lux_source: manual` dan tidak boleh dipakai untuk pengujian akhir.

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
shared/           pengolahan data, algoritma, topic, konfigurasi
sensor/           halaman sensor untuk HP (HTML, JS)
dashboard/        dashboard web (menyusul)
tests/            tes unit
data/             log dan statistik hasil jalan (tidak di-commit)
```
