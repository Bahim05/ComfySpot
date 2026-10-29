# Dashboard ComfySpot

Halaman web untuk memantau tempat belajar mana yang paling nyaman saat ini, berdasarkan cahaya dan kebisingan dari HP di tiap lokasi.

**Buka dashboard:** https://bahim05.github.io/ComfySpot/dashboard/

## Cara memakai

1. Jalankan hub di laptop (`python hub.py`), lalu nyalakan HP sensor di tiap lokasi (lihat README utama).
2. Buka tautan dashboard di atas. Saat demo, perangkat yang dipakai (HP, laptop hub, dan dashboard) berada di WiFi atau hotspot yang sama.
3. Isi PROJECT_ID yang sama dengan di `.env`.
4. Atur kebutuhan belajar di panel **Kebutuhan belajar** bila perlu. Peringkat lokasi ikut berubah.

## Panel kebutuhan belajar

Panel ini menentukan cara hub menilai semua lokasi:

- **Minimal terang** (50 sampai 1000 lux, standar 300): cahaya yang dianggap sudah cukup untuk membaca. Makin tinggi nilainya, makin sulit tempat yang redup mendapat skor bagus.
- **Batas bising** (40 sampai 80 dB relatif, standar 55): kebisingan yang masih bisa ditoleransi. Makin rendah nilainya, makin ketat penilaian terhadap suara.

Tombol **Kembalikan standar** memulihkan 300 lux dan 55 dB. Tombol ini mati kalau kriteria yang berlaku sudah standar.

Nilai baru dikirim saat slider dilepas atau tombol ditekan. Hub lalu menghitung ulang skor dan berlaku untuk semua lokasi sekaligus. Slider selalu mengikuti kriteria yang sedang dipakai hub, jadi dua dashboard yang terbuka bersamaan tetap menampilkan nilai yang sama.

## Yang ditampilkan

- Tempat terbaik saat ini beserta skornya
- Kartu tiap lokasi: skor kenyamanan, lampu status, lux, dan dB relatif
- Grafik kebisingan rata-rata per jam dan jam paling ramai
- Tabel peringkat, termasuk perangkat yang offline atau menunggu data cahaya

## Cara kerja

Dashboard tidak mengolah data. Semua olahan dilakukan hub. Dashboard terhubung ke broker MQTT lewat WebSocket aman (`wss://broker.hivemq.com:8884/mqtt`), lalu:

- menerima hasil olahan hub dari topic `comfyspot/<PROJECT_ID>/state`, termasuk kriteria yang sedang berlaku
- mengirim kriteria ke topic `comfyspot/<PROJECT_ID>/control/criteria` dalam bentuk `{"lux_target": 300, "noise_limit": 55}`. Pesan ini disimpan broker (retained), jadi hub yang baru dinyalakan langsung memakai nilai terakhir.

## Catatan

- Desibel dari HP bersifat relatif, bukan angka resmi.
- Grafik per jam baru terisi setelah hub berjalan cukup lama.
