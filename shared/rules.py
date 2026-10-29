"""Algoritma ComfySpot: skor kenyamanan, peringkat lokasi, dan rata-rata keramaian per jam.

Semua fungsi di sini murni (tidak memakai MQTT atau jam sistem), jadi mudah dites.
"""
import math
from collections import defaultdict

# Dua kriteria yang bisa diatur dari dashboard, berlaku untuk semua lokasi sekaligus.
# Nilai standarnya berasal dari acuan ruang baca:
#   LUX_TARGET  : minimal terang, sekitar 300 lux (SNI 03-6197-2000, perlu diverifikasi ke dokumen aslinya)
#   NOISE_LIMIT : batas bising, sekitar 55 dB (SNI 03-6386-2000, perlu diverifikasi)
LUX_TARGET = 300.0
NOISE_LIMIT = 55.0

# Rentang yang diterima hub dari dashboard. Di luar ini pesan dianggap tidak masuk akal dan ditolak.
LUX_TARGET_RANGE = (50.0, 1000.0)
NOISE_LIMIT_RANGE = (40.0, 80.0)

# Batas skor untuk lampu status.
GREEN_MIN = 75.0
YELLOW_MIN = 50.0


def light_score(lux, lux_target=LUX_TARGET):
    """Skor terang 0..1: naik linear sampai lux_target, lalu penuh."""
    return max(0.0, min(1.0, lux / lux_target))


def noise_curve(noise_limit=NOISE_LIMIT):
    """Kurva skor sepi sebagai pasangan (dB, skor 0..1), digeser mengikuti batas bising.

      limit - 15 dB atau kurang : skor penuh
      limit                     : skor 0,6
      limit + 20 dB atau lebih  : skor 0
    Dengan batas standar 55 dB kurvanya (40, 1,0), (55, 0,6), (75, 0,0).
    """
    return [(noise_limit - 15.0, 1.0), (noise_limit, 0.6), (noise_limit + 20.0, 0.0)]


def noise_score(db, noise_limit=NOISE_LIMIT):
    """Skor sepi 0..1, interpolasi linear di antara titik-titik noise_curve."""
    curve = noise_curve(noise_limit)
    first_db, first_score = curve[0]
    if db <= first_db:
        return first_score
    for (d0, s0), (d1, s1) in zip(curve, curve[1:]):
        if db <= d1:
            # Interpolasi linear di antara dua titik kurva.
            return s0 + (s1 - s0) * (db - d0) / (d1 - d0)
    return curve[-1][1]


def comfort_score(lux, db, lux_target=LUX_TARGET, noise_limit=NOISE_LIMIT):
    """Skor kenyamanan 0..100: rata-rata geometrik skor terang dan skor sepi, berbobot sama.

    skor = 100 * sqrt(skor_terang * skor_sepi)

    Kenapa rata-rata geometrik dan bukan rata-rata biasa: pada rata-rata biasa, suara yang sangat
    tenang bisa menutupi ruangan yang gelap gulita (lux 0 dan 30 dB tetap mendapat 50, kuning).
    Pada rata-rata geometrik, kalau salah satu skor 0 maka hasilnya 0. Tempat yang gelap atau
    sangat bising memang tidak layak untuk belajar, seberapa bagus pun faktor yang lain.
    Kedua faktor berbobot sama; kebutuhan yang berbeda diatur lewat lux_target dan noise_limit,
    bukan lewat bobot.
    """
    return 100.0 * math.sqrt(light_score(lux, lux_target) * noise_score(db, noise_limit))


def status_for(score):
    """Warna lampu status: hijau, kuning, atau merah."""
    if score >= GREEN_MIN:
        return "hijau"
    if score >= YELLOW_MIN:
        return "kuning"
    return "merah"


def noise_level(db):
    """Label keramaian dari kebisingan."""
    if db < 45:
        return "sepi"
    if db < 55:
        return "sedang"
    return "ramai"


def rank_readings(readings, lux_target=LUX_TARGET, noise_limit=NOISE_LIMIT):
    """Beri skor dan peringkat ke daftar pembacaan.

    Tiap pembacaan adalah dict yang memuat minimal device_id, lux_avg, dan db.
    Skor dihitung dengan kriteria lux_target dan noise_limit yang sama untuk semua lokasi.
    Hasilnya salinan yang sudah ditambah score, status, noise_level, rank, urut dari skor tertinggi.
    Skor yang sama diurutkan menurut device_id supaya hasilnya selalu sama.
    """
    scored = []
    for r in readings:
        item = dict(r)
        item["score"] = round(comfort_score(r["lux_avg"], r["db"], lux_target, noise_limit), 1)
        item["status"] = status_for(item["score"])
        item["noise_level"] = noise_level(r["db"])
        scored.append(item)
    scored.sort(key=lambda x: (-x["score"], x["device_id"]))
    for i, item in enumerate(scored, start=1):
        item["rank"] = i
    return scored


class HourlyStats:
    """Rata-rata kebisingan dan cahaya per jam (0..23) untuk tiap lokasi.

    Data dikumpulkan sepanjang hari. Dari sini dashboard bisa menjawab
    "lokasi ini biasanya ramai jam berapa?".
    """

    def __init__(self):
        self._sum_db = defaultdict(float)
        self._sum_lux = defaultdict(float)
        self._count = defaultdict(int)

    def add(self, location, hour, db, lux):
        key = (location, int(hour))
        self._sum_db[key] += db
        self._sum_lux[key] += lux
        self._count[key] += 1

    def average_db(self, location, hour):
        n = self._count.get((location, hour), 0)
        return None if n == 0 else self._sum_db[(location, hour)] / n

    def average_lux(self, location, hour):
        n = self._count.get((location, hour), 0)
        return None if n == 0 else self._sum_lux[(location, hour)] / n

    def profile(self, location):
        """Daftar 24 rata-rata dB (indeks = jam). Jam tanpa data bernilai None."""
        return [self.average_db(location, h) for h in range(24)]

    def busiest_hours(self, location, top=3, min_samples=5):
        """Jam paling ramai (rata-rata dB tertinggi). Jam dengan sampel terlalu sedikit diabaikan."""
        candidates = []
        for h in range(24):
            if self._count.get((location, h), 0) >= min_samples:
                candidates.append((self.average_db(location, h), h))
        candidates.sort(reverse=True)
        return [h for _, h in candidates[:top]]

    def locations(self):
        return sorted({loc for loc, _ in self._count})

    def to_dict(self):
        return {
            "%s|%d" % (loc, h): {
                "sum_db": self._sum_db[(loc, h)],
                "sum_lux": self._sum_lux[(loc, h)],
                "count": self._count[(loc, h)],
            }
            for (loc, h) in self._count
        }

    @classmethod
    def from_dict(cls, data):
        stats = cls()
        for key, v in data.items():
            loc, hour = key.rsplit("|", 1)
            k = (loc, int(hour))
            stats._sum_db[k] = v["sum_db"]
            stats._sum_lux[k] = v["sum_lux"]
            stats._count[k] = v["count"]
        return stats
