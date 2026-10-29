"""Hub ComfySpot (jalan di laptop).

Tugasnya:
  1. subscribe telemetry (RMS mikrofon dari halaman sensor) dan light (lux dari Termux) semua HP lokasi,
     lalu menggabungkan keduanya per device_id,
  2. mengolah data (shared/processing.py) lalu memberi skor dan peringkat (shared/rules.py),
  3. publish state untuk dashboard dan perintah control untuk tiap HP (lampu status),
  4. menerima kriteria kebutuhan belajar dari dashboard (topic control/criteria): minimal terang dan
     batas bising, berlaku untuk semua lokasi.

Logika dipisah ke kelas Hub tanpa MQTT, jadi bisa dites tanpa broker (tests/test_hub.py).
"""
import csv
import json
import math
import os
import random
import threading
import time

from shared import config, rules, topics
from shared.processing import SensorProcessor

OFFLINE_AFTER_S = 15      # perangkat yang diam lebih lama dari ini dianggap offline
LIGHT_MAX_AGE_S = 5       # lux dari Termux yang lebih tua dari ini dianggap basi dan tidak dipakai
PUBLISH_EVERY_S = 1       # publish berkala (menjaga pesan retained segar dan mendeteksi perangkat offline)
MIN_PUBLISH_GAP_S = 0.25  # jarak minimal antar publish yang dipicu data baru, supaya broker publik tidak kebanjiran
PRINT_EVERY_S = 2         # selang mencetak ringkasan ke terminal
SAVE_EVERY_S = 30         # selang menyimpan statistik per jam ke file
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
HOURLY_FILE = os.path.join(DATA_DIR, "hourly.json")
LOG_FILE = os.path.join(DATA_DIR, "log.csv")
CALIBRATION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibration.json")


def _number(value):
    """True jika value angka biasa yang terbatas (bukan teks, bukan NaN, bukan tak hingga)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class Hub:
    def __init__(self, offsets=None):
        self.offsets = offsets or {}      # offset kalibrasi dB per device_id
        # kriteria skor yang berlaku untuk semua lokasi; diubah dari dashboard lewat handle_criteria
        self.criteria = {"lux_target": rules.LUX_TARGET, "noise_limit": rules.NOISE_LIMIT}
        self.processors = {}
        self.latest = {}                  # pembacaan terakhir per device_id
        self.hourly = rules.HourlyStats()
        self.invalid = 0                  # jumlah pesan yang ditolak
        self.light = {}                   # lux terbaru dari skrip Termux: device_id -> (lux, waktu terima)
        self.waiting = {}                 # device_id -> waktu terakhir telemetry datang tanpa data cahaya
        self.lock = threading.Lock()

    # ---------- masukan ----------

    def handle_light(self, device_id, payload, now):
        """Simpan lux dari skrip Termux. Dipakai nanti saat telemetry dari halaman sensor datang."""
        lux = payload.get("lux") if isinstance(payload, dict) else None
        if not device_id or not _number(lux) or lux < 0:
            self.invalid += 1
            return False
        with self.lock:
            self.light[device_id] = (float(lux), now)
        return True

    def handle_telemetry(self, device_id, payload, now):
        """Proses satu pesan telemetry. Kembalikan hasil olahan, atau None jika pesan tidak dipakai."""
        if not device_id or not isinstance(payload, dict):
            self.invalid += 1
            return None
        lux, mic = payload.get("lux"), payload.get("mic_rms")
        source = str(payload.get("lux_source", "sensor"))[:10]
        # Tolak pesan yang bentuknya salah: pesan dari broker publik bisa dikirim siapa saja.
        if not _number(mic) or mic < 0:
            self.invalid += 1
            return None

        if lux is None:
            # Halaman sensor tidak membawa data cahaya (sensor cahaya dibaca Termux di HP yang sama).
            # Pakai lux Termux terbaru, tetapi hanya jika masih segar; data basi lebih buruk daripada tidak ada.
            with self.lock:
                entry = self.light.get(device_id)
                if entry is None or now - entry[1] > LIGHT_MAX_AGE_S:
                    self.waiting[device_id] = now
                    return None
                lux, source = entry[0], "termux"
        elif not _number(lux) or lux < 0:
            self.invalid += 1
            return None

        with self.lock:
            self.waiting.pop(device_id, None)
            proc = self.processors.get(device_id)
            if proc is None:
                proc = SensorProcessor(db_offset=self.offsets.get(device_id, 0.0))
                self.processors[device_id] = proc
            result = proc.update(now, float(lux), float(mic))

            location = str(payload.get("location") or device_id)[:40]
            self.latest[device_id] = dict(
                result,
                device_id=device_id,
                location=location,
                lux_raw=float(lux),
                lux_source=source,
                last_seen=now,
            )
            hour = time.localtime(now).tm_hour
            self.hourly.add(location, hour, result["db"], result["lux_avg"])
            return self.latest[device_id]

    def handle_criteria(self, payload):
        """Terima kriteria dari dashboard: JSON {"lux_target": angka, "noise_limit": angka}.

        Topic ini bisa ditulis siapa saja di broker publik, jadi validasinya ketat. Pesan yang rusak,
        bukan dict, kuncinya kurang, atau nilainya bukan angka terbatas di dalam rentang diabaikan:
        kriteria lama tetap dipakai dan hitungan invalid bertambah. Kembalikan True jika diterima.
        """
        try:
            data = json.loads(payload.decode("utf-8") if isinstance(payload, bytes) else payload)
        except (TypeError, ValueError, UnicodeDecodeError):
            data = None
        lux_target = data.get("lux_target") if isinstance(data, dict) else None
        noise_limit = data.get("noise_limit") if isinstance(data, dict) else None
        lux_lo, lux_hi = rules.LUX_TARGET_RANGE
        noise_lo, noise_hi = rules.NOISE_LIMIT_RANGE
        if not (_number(lux_target) and lux_lo <= lux_target <= lux_hi
                and _number(noise_limit) and noise_lo <= noise_limit <= noise_hi):
            self.invalid += 1
            return False
        with self.lock:
            self.criteria = {"lux_target": float(lux_target), "noise_limit": float(noise_limit)}
        return True

    def get_criteria(self):
        with self.lock:
            return dict(self.criteria)

    # ---------- keluaran ----------

    def snapshot(self, now):
        """Hitung peringkat dari perangkat yang masih online, dan susun state serta control."""
        with self.lock:
            online = [r for r in self.latest.values() if now - r["last_seen"] <= OFFLINE_AFTER_S]
            offline = sorted(r["device_id"] for r in self.latest.values() if now - r["last_seen"] > OFFLINE_AFTER_S)
            criteria = dict(self.criteria)
            ranked = rules.rank_readings(online, criteria["lux_target"], criteria["noise_limit"])
            hourly = {loc: [None if v is None else round(v, 1) for v in self.hourly.profile(loc)]
                      for loc in self.hourly.locations()}
            busiest = {loc: self.hourly.busiest_hours(loc) for loc in self.hourly.locations()}
            ranked_ids = {r["device_id"] for r in ranked}
            # perangkat yang mikrofonnya sudah mengirim tetapi data cahayanya belum ada (skrip Termux belum jalan)
            waiting = sorted(d for d, t in self.waiting.items()
                             if now - t <= OFFLINE_AFTER_S and d not in ranked_ids)

        state = {
            "updated_ms": int(now * 1000),
            "criteria": criteria,
            "best": ranked[0]["device_id"] if ranked else None,
            "ranking": [
                {
                    "rank": r["rank"], "device_id": r["device_id"], "location": r["location"],
                    "score": r["score"], "status": r["status"], "noise_level": r["noise_level"],
                    "lux": round(r["lux_avg"], 1), "lux_stable": r["lux_stable"],
                    "db": round(r["db"], 1), "lux_source": r["lux_source"],
                }
                for r in ranked
            ],
            "offline": offline,
            "waiting_light": waiting,
            "hourly_db": hourly,
            "busiest_hours": busiest,
        }
        controls = {
            r["device_id"]: {
                "rank": r["rank"], "score": r["score"], "status": r["status"],
                "best": r["rank"] == 1, "updated_ms": int(now * 1000),
                # angka hasil olahan dikirim balik supaya HP bisa menampilkan cahaya dan dB yang dipakai hub
                "lux": round(r["lux_avg"], 1), "lux_source": r["lux_source"], "db": round(r["db"], 1),
            }
            for r in ranked
        }
        return state, controls

    # ---------- penyimpanan ----------

    def save_hourly(self, path=HOURLY_FILE):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with self.lock:
            data = self.hourly.to_dict()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)   # ganti sekaligus, jadi file tidak rusak kalau program mati di tengah tulis

    def load_hourly(self, path=HOURLY_FILE):
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.hourly = rules.HourlyStats.from_dict(json.load(f))


def append_log(row, path=LOG_FILE):
    """Catat tiap pembacaan ke CSV. File ini bahan grafik untuk bagian pengujian di PPT."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new_file = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["waktu", "device_id", "location", "lux_raw", "lux_avg", "lux_source", "mic_rms", "db"])
        w.writerow(row)


def load_offsets(path=CALIBRATION_FILE):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def main():
    import paho.mqtt.client as mqtt

    pid = config.require_project_id()
    hub = Hub(offsets=load_offsets())
    hub.load_hourly()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id="comfyspot-hub-%s-%04x" % (pid, random.randrange(0x10000)))
    if config.BROKER_USERNAME:
        client.username_pw_set(config.BROKER_USERNAME, config.BROKER_PASSWORD)
    if config.BROKER_TLS:
        client.tls_set()   # memakai sertifikat CA bawaan sistem

    def on_connect(c, userdata, flags, reason_code, properties):
        if reason_code != 0:
            print("Gagal tersambung ke broker:", reason_code)
            return
        print("Tersambung ke %s:%s, PROJECT_ID=%s" % (config.BROKER_HOST, config.BROKER_PORT, pid))
        # subscribe dilakukan di sini supaya otomatis diulang setelah koneksi terputus
        c.subscribe([(topics.telemetry_all(pid), 0), (topics.light_all(pid), 0), (topics.criteria(pid), 1)])

    publish_lock = threading.Lock()
    last_publish = [0.0]

    def publish_snapshot(now):
        """Hitung peringkat lalu publish state dan control. Mengembalikan state."""
        state, controls = hub.snapshot(now)
        # retained: broker menyimpan pesan terakhir, jadi dashboard atau HP yang baru buka langsung dapat isinya.
        # QoS 0 cukup karena pesan berikutnya datang dalam hitungan detik; yang lama tidak perlu dikirim ulang.
        client.publish(topics.state(pid), json.dumps(state), qos=0, retain=True)
        for device_id, ctl in controls.items():
            client.publish(topics.edge_control(pid, device_id), json.dumps(ctl), qos=0, retain=True)
        return state

    def on_message(c, userdata, msg):
        now = time.time()
        if msg.topic == topics.criteria(pid):
            before = hub.get_criteria()
            if hub.handle_criteria(msg.payload):
                after = hub.get_criteria()
                if after != before:
                    print("Kriteria diubah: minimal terang %g lux, batas bising %g dB" % (
                        after["lux_target"], after["noise_limit"]))
                    # langsung publish supaya dashboard dan lampu HP segera memakai skor baru
                    with publish_lock:
                        last_publish[0] = now
                    publish_snapshot(now)
            else:
                print("Pesan kriteria ditolak (bukan JSON lux_target dan noise_limit dalam rentang)")
            return
        parts = msg.topic.split("/")   # comfyspot/PID/edge/<device>/telemetry atau .../light
        device_id, kind = parts[-2], parts[-1]
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            hub.invalid += 1
            return
        if kind == "light":
            hub.handle_light(device_id, payload, now)
            return
        r = hub.handle_telemetry(device_id, payload, now)
        if r:
            append_log([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)), device_id, r["location"],
                        round(r["lux_raw"], 1), round(r["lux_avg"], 1), r["lux_source"],
                        round(r["mic_rms_avg"], 5), round(r["db"], 1)])
            # Publish langsung saat data baru masuk, tidak menunggu putaran berkala. Inilah yang membuat
            # lampu di HP bereaksi dalam kurang dari satu detik setelah suara berubah.
            with publish_lock:
                due = now - last_publish[0] >= MIN_PUBLISH_GAP_S
                if due:
                    last_publish[0] = now
            if due:
                publish_snapshot(now)

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect_async(config.BROKER_HOST, config.BROKER_PORT, keepalive=30)
    client.loop_start()   # thread terpisah yang mengurus jaringan dan menyambung ulang

    last_save = last_print = time.time()
    try:
        while True:
            time.sleep(PUBLISH_EVERY_S)
            now = time.time()
            with publish_lock:
                last_publish[0] = now
            state = publish_snapshot(now)
            if now - last_print < PRINT_EVERY_S:
                continue
            last_print = now
            if state["ranking"]:
                top = state["ranking"][0]
                print("%s | terbaik: %s (%.1f, %s) | online: %d | ditolak: %d" % (
                    time.strftime("%H:%M:%S"), top["location"], top["score"], top["status"],
                    len(state["ranking"]), hub.invalid))
            if state["waiting_light"]:
                print("%s | menunggu data cahaya dari: %s (jalankan light_termux.py di HP itu)" % (
                    time.strftime("%H:%M:%S"), ", ".join(state["waiting_light"])))
            if now - last_save >= SAVE_EVERY_S:
                hub.save_hourly()
                last_save = now
    except KeyboardInterrupt:
        pass
    finally:
        hub.save_hourly()
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
