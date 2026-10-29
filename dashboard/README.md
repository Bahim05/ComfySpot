# Dashboard ComfySpot

Halaman web untuk memantau tempat belajar mana yang paling nyaman saat ini, berdasarkan cahaya dan kebisingan dari HP di tiap lokasi.

**Buka dashboard:** https://bahim05.github.io/ComfySpot/dashboard/

## Cara memakai

1. Jalankan hub di laptop (`python hub.py`), lalu nyalakan HP sensor di tiap lokasi (lihat README utama).
2. Buka tautan dashboard di atas. Saat demo, perangkat yang dipakai (HP, laptop hub, dan dashboard) berada di WiFi atau hotspot yang sama.
3. Isi PROJECT_ID yang sama dengan di `.env`.
4. Pilih mode **Hening** atau **Ramai** sesuai kebutuhan belajar. Peringkat lokasi ikut berubah.

## Yang ditampilkan

- Tempat terbaik saat ini beserta skornya
- Kartu tiap lokasi: skor kenyamanan, lampu status, lux, dan dB relatif
- Grafik kebisingan rata-rata per jam dan jam paling ramai
- Tabel peringkat, termasuk perangkat yang offline atau menunggu data cahaya

## Cara kerja

Dashboard tidak mengolah data. Semua olahan dilakukan hub. Dashboard terhubung ke broker MQTT lewat WebSocket aman (`wss://broker.hivemq.com:8884/mqtt`), lalu:

- menerima hasil olahan hub dari topic `comfyspot/<PROJECT_ID>/state`
- mengirim pilihan mode ke topic `comfyspot/<PROJECT_ID>/control/mode`

## Catatan

- Desibel dari HP bersifat relatif, bukan angka resmi.
- Grafik per jam baru terisi setelah hub berjalan cukup lama.