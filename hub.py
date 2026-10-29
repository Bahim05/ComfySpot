"""Hub ComfySpot (jalan di laptop).

Tugasnya:
  1. subscribe telemetry dari semua HP lokasi,
  2. mengolah data (shared/processing.py) lalu memberi skor dan peringkat (shared/rules.py),
  3. publish state untuk dashboard dan perintah control untuk tiap HP (lampu status),
  4. menerima pilihan mode dari dashboard (topic control/mode).

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
PUBLISH_EVERY_S = 2       # selang publish state dan control
SAVE_EVERY_S = 30         # selang menyimpan statistik per jam ke file
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
HOURLY_FILE = os.path.join(DATA_DIR, "hourly.json")
LOG_FILE = os.path.join(DATA_DIR, "log.csv")
CALIBRATION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibration.json")


def _number(value):
    """True jika value angka biasa yang terbatas (bukan teks, bukan NaN, bukan tak hingga)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class Hub:
    def __init__(self, offsets=None, mode=rules.DEFAULT_MODE):
        self.offsets = offsets or {}      # offset kalibrasi dB per device_id
        self.mode = mode
        self.processors = {}
        self.latest = {}                  # pembacaan terakhir per device_id
        self.hourly = rules.HourlyStats()
        self.invalid = 0                  # jumlah pesan yang ditolak
        self.lock = threading.Lock()

    # ---------- masukan ----------

    def handle_telemetry(self, device_id, payload, now):
        """Proses satu pesan telemetry. Kembalikan hasil olahan, atau None jika pesan tidak valid."""
        if not device_id or not isinstance(payload, dict):
            self.invalid += 1
            return None
        lux, mic = payload.get("lux"), payload.get("mic_rms")
        # Tolak pesan yang bentuknya salah: pesan dari broker publik bisa dikirim siapa saja.
        if not (_number(lux) and _number(mic)) or lux < 0 or mic < 0:
            self.invalid += 1
            return None

        with self.lock:
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
                lux_source=str(payload.get("lux_source", "sensor"))[:10],
                last_seen=now,
            )
            hour = time.localtime(now).tm_hour
            self.hourly.add(location, hour, result["db"], result["lux_avg"])
            return self.latest[device_id]

    def set_mode(self, text):
        """Terima pilihan mode dari dashboard. Teks yang tidak dikenal diabaikan."""
        text = (text or "").strip().lower()
        if text in rules.MODES:
            with self.lock:
                self.mode = text
            return True
        return False

    # ---------- keluaran ----------

    def snapshot(self, now):
        """Hitung peringkat dari perangkat yang masih online, dan susun state serta control."""
        with self.lock:
            online = [r for r in self.latest.values() if now - r["last_seen"] <= OFFLINE_AFTER_S]
            offline = sorted(r["device_id"] for r in self.latest.values() if now - r["last_seen"] > OFFLINE_AFTER_S)
            ranked = rules.rank_readings(online, self.mode)
            hourly = {loc: [None if v is None else round(v, 1) for v in self.hourly.profile(loc)]
                      for loc in self.hourly.locations()}
            busiest = {loc: self.hourly.busiest_hours(loc) for loc in self.hourly.locations()}
            mode = self.mode

        state = {
            "updated_ms": int(now * 1000),
            "mode": mode,
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
            "hourly_db": hourly,
            "busiest_hours": busiest,
        }
        controls = {
            r["device_id"]: {
                "rank": r["rank"], "score": r["score"], "status": r["status"],
                "mode": mode, "best": r["rank"] == 1, "updated_ms": int(now * 1000),
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
        c.subscribe([(topics.telemetry_all(pid), 0), (topics.mode(pid), 1)])

    def on_message(c, userdata, msg):
        now = time.time()
        if msg.topic == topics.mode(pid):
            if hub.set_mode(msg.payload.decode("utf-8", "ignore")):
                print("Mode diubah menjadi", hub.mode)
            return
        device_id = msg.topic.split("/")[-2]   # comfyspot/PID/edge/<device>/telemetry
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            hub.invalid += 1
            return
        r = hub.handle_telemetry(device_id, payload, now)
        if r:
            append_log([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)), device_id, r["location"],
                        round(r["lux_raw"], 1), round(r["lux_avg"], 1), r["lux_source"],
                        round(r["mic_rms_avg"], 5), round(r["db"], 1)])

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect_async(config.BROKER_HOST, config.BROKER_PORT, keepalive=30)
    client.loop_start()   # thread terpisah yang mengurus jaringan dan menyambung ulang

    last_save = time.time()
    try:
        while True:
            time.sleep(PUBLISH_EVERY_S)
            now = time.time()
            state, controls = hub.snapshot(now)
            # retained: broker menyimpan pesan terakhir, jadi dashboard atau HP yang baru buka langsung dapat isinya
            client.publish(topics.state(pid), json.dumps(state), qos=1, retain=True)
            for device_id, ctl in controls.items():
                client.publish(topics.edge_control(pid, device_id), json.dumps(ctl), qos=1, retain=True)
            if state["ranking"]:
                top = state["ranking"][0]
                print("%s | terbaik: %s (%.1f, %s) | online: %d | ditolak: %d" % (
                    time.strftime("%H:%M:%S"), top["location"], top["score"], top["status"],
                    len(state["ranking"]), hub.invalid))
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
