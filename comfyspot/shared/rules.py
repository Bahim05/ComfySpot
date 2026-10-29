"""Algoritma ComfySpot: skor kenyamanan, peringkat lokasi, dan rata-rata keramaian per jam.

Semua fungsi di sini murni (tidak memakai MQTT atau jam sistem), jadi mudah dites.
"""
from collections import defaultdict

# Acuan cahaya untuk ruang baca: minimal sekitar 300 lux (SNI 03-6197-2000, perlu diverifikasi ke dokumen aslinya).
LUX_TARGET = 300.0

# Kurva skor kebisingan sebagai pasangan (dB, skor 0..1), diinterpolasi linear di antaranya.
#   sampai 40 dB : skor penuh
#   55 dB        : batas ruang baca (SNI 03-6386-2000, perlu diverifikasi), skor 0,6
#   75 dB atau lebih : skor 0
NOISE_CURVE = [(40.0, 1.0), (55.0, 0.6), (75.0, 0.0)]

# Bobot per mode. Mode "hening" mementingkan suara; mode "ramai" memberi toleransi lebih untuk suara.
MODES = {
    "hening": {"light": 0.3, "noise": 0.7},
    "ramai": {"light": 0.6, "noise": 0.4},
}
DEFAULT_MODE = "hening"

# Batas skor untuk lampu status.
GREEN_MIN = 75.0
YELLOW_MIN = 50.0


def light_score(lux):
    """Skor cahaya 0..1: naik linear sampai LUX_TARGET, lalu penuh."""
    return max(0.0, min(1.0, lux / LUX_TARGET))


def noise_score(db):
    """Skor kebisingan 0..1 dari kurva NOISE_CURVE."""
    first_db, first_score = NOISE_CURVE[0]
    if db <= first_db:
        return first_score
    for (d0, s0), (d1, s1) in zip(NOISE_CURVE, NOISE_CURVE[1:]):
        if db <= d1:
            # Interpolasi linear di antara dua titik kurva.
            return s0 + (s1 - s0) * (db - d0) / (d1 - d0)
    return NOISE_CURVE[-1][1]


def comfort_score(lux, db, mode=DEFAULT_MODE):
    """Skor kenyamanan 0..100 sebagai jumlah berbobot skor cahaya dan skor kebisingan."""
    if mode not in MODES:
        raise ValueError("mode tidak dikenal: %r" % (mode,))
    w = MODES[mode]
    return 100.0 * (w["light"] * light_score(lux) + w["noise"] * noise_score(db))


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


def rank_readings(readings, mode=DEFAULT_MODE):
    """Beri skor dan peringkat ke daftar pembacaan.

    Tiap pembacaan adalah dict yang memuat minimal device_id, lux_avg, dan db.
    Hasilnya salinan yang sudah ditambah score, status, noise_level, rank, urut dari skor tertinggi.
    Skor yang sama diurutkan menurut device_id supaya hasilnya selalu sama.
    """
    scored = []
    for r in readings:
        item = dict(r)
        item["score"] = round(comfort_score(r["lux_avg"], r["db"], mode), 1)
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
