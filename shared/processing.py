"""Pengolahan data sensor ComfySpot.

Ada dua jenis pengolahan data:
  A. Cahaya (lux): rata-rata dalam jendela waktu, ditambah simpangan baku untuk menilai kestabilan.
  B. Suara: RMS sinyal mikrofon -> rata-rata energi dalam jendela -> desibel relatif.

Modul ini tidak tahu apa pun tentang MQTT, jadi bisa dites tanpa broker dan tanpa HP.
"""
import math
from collections import deque

# Nilai acuan untuk desibel relatif: db = 20 * log10(rms / DB_REF_RMS).
# 1e-4 dipilih supaya ruangan tenang jatuh di kisaran 30 dB dan percakapan di kisaran 50 dB.
# Angka ini bukan standar. Selisihnya terhadap sound meter dikoreksi lewat offset kalibrasi.
DB_REF_RMS = 1e-4

# Panjang jendela rata-rata. Makin panjang makin halus tetapi makin lambat bereaksi:
# jendela 10 detik membuat lampu butuh sekitar 10 detik untuk kembali normal setelah suara keras.
# Suara dibuat lebih pendek dari cahaya karena suara berubah cepat dan terasa "tidak real time" kalau lambat,
# sedangkan cahaya berubah pelan dan butuh sampel lebih banyak supaya simpangan bakunya berarti.
LIGHT_WINDOW_S = 5
MIC_WINDOW_S = 2


def rms(samples):
    """Root Mean Square: akar dari rata-rata kuadrat sampel.

    Sampel suara bernilai positif dan negatif bergantian, jadi rata-rata biasa hampir 0.
    Dengan dikuadratkan dulu, gelombang positif dan negatif tidak saling menghapus.
    """
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


def rms_to_db(rms_value, offset_db=0.0):
    """Ubah RMS menjadi desibel relatif, lalu tambahkan offset kalibrasi.

    Hasil dibatasi minimal 0 supaya sinyal nol (mikrofon mati) tidak menghasilkan minus tak hingga.
    """
    if rms_value is None or rms_value <= 0:
        return 0.0
    db = 20.0 * math.log10(rms_value / DB_REF_RMS) + offset_db
    return max(db, 0.0)


def calibration_offset(phone_db, meter_db):
    """Rata-rata selisih (sound meter - HP) dari beberapa pengukuran berpasangan.

    Pakai hasilnya sebagai offset_db agar angka HP mendekati angka sound meter.
    """
    if not phone_db or len(phone_db) != len(meter_db):
        raise ValueError("daftar pengukuran harus tidak kosong dan sama panjang")
    return sum(m - p for p, m in zip(phone_db, meter_db)) / len(phone_db)


class Window:
    """Jendela waktu geser: hanya menyimpan nilai dari `seconds` detik terakhir."""

    def __init__(self, seconds):
        self.seconds = seconds
        self._items = deque()  # pasangan (waktu, nilai), urut dari yang terlama

    def add(self, t, value):
        self._items.append((t, value))
        # Buang nilai yang sudah lebih tua dari panjang jendela.
        while self._items and t - self._items[0][0] > self.seconds:
            self._items.popleft()

    def count(self):
        return len(self._items)

    def mean(self):
        if not self._items:
            return None
        return sum(v for _, v in self._items) / len(self._items)

    def std(self):
        """Simpangan baku populasi: seberapa besar nilai bergoyang di sekitar rata-rata."""
        if not self._items:
            return None
        m = self.mean()
        return math.sqrt(sum((v - m) ** 2 for _, v in self._items) / len(self._items))

    def rms(self):
        """RMS dari nilai-nilai di jendela. Dipakai untuk merata-ratakan suara berdasarkan energi."""
        if not self._items:
            return None
        return rms([v for _, v in self._items])


class SensorProcessor:
    """Pengolah data satu perangkat: cahaya (jenis A) dan suara (jenis B)."""

    def __init__(self, light_window_s=LIGHT_WINDOW_S, mic_window_s=MIC_WINDOW_S, db_offset=0.0, cv_max=0.25):
        self.light = Window(light_window_s)
        self.mic = Window(mic_window_s)
        self.db_offset = db_offset
        # cv_max: batas koefisien variasi (std / rata-rata) agar cahaya dianggap stabil.
        self.cv_max = cv_max

    def update(self, t, lux, mic_rms):
        """Masukkan satu pembacaan mentah, kembalikan hasil olahan."""
        self.light.add(t, lux)
        self.mic.add(t, mic_rms)

        lux_avg = self.light.mean()
        lux_std = self.light.std()
        # Cahaya stabil jika goyangannya kecil dibanding rata-ratanya.
        stable = True if lux_avg <= 0 else (lux_std / lux_avg) <= self.cv_max

        # Suara dirata-rata dengan RMS (energi), baru diubah ke dB.
        # Merata-ratakan dB langsung akan meremehkan letupan suara keras yang singkat.
        mic_avg = self.mic.rms()
        return {
            "lux_avg": lux_avg,
            "lux_std": lux_std,
            "lux_stable": stable,
            "mic_rms_avg": mic_avg,
            "db": rms_to_db(mic_avg, self.db_offset),
        }
