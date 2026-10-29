"""Pembaca sensor cahaya untuk HP lokasi, dijalankan di Termux.

Alur: panggil `termux-sensor` (dari paket Termux:API) tiap beberapa detik, ambil angka lux,
lalu publish ke topic light lewat MQTT. Hub menggabungkannya dengan RMS mikrofon yang
dikirim halaman sensor dari HP yang sama (device id harus sama).

Contoh, di Termux:
  python light_termux.py --device edge1
Nama sensor cahaya dicari otomatis dari daftar `termux-sensor -l`. Jika pilihannya salah,
tentukan sendiri dengan --sensor "<nama lengkap atau sebagian nama>". Pencocokan nama di
termux-sensor membedakan huruf besar dan kecil.
"""
import argparse
import json
import math
import subprocess
import time

from shared import config, topics


def parse_lux(text):
    """Ambil angka lux dari keluaran termux-sensor, atau None jika tidak ada.

    Bentuk keluarannya kira-kira: {"nama sensor": {"values": [123.0]}}
    """
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    for sensor in data.values():
        values = sensor.get("values") if isinstance(sensor, dict) else None
        if values and isinstance(values[0], (int, float)) and not isinstance(values[0], bool):
            value = float(values[0])
            if math.isfinite(value) and value >= 0:
                return value
    return None


def pick_light_sensor(names):
    """Pilih satu sensor cahaya sekitar dari daftar nama sensor, atau None jika tidak ada.

    HP biasanya punya banyak sensor yang namanya memuat "light": sensor utama, versi wakeup,
    data mentah, stream, dan sensor turunan vendor. Di Xiaomi misalnya ada "Optimization light fov Sensor",
    yang bukan cahaya sekitar. Maka tiap nama diberi skor dan yang tertinggi dipakai.
    """
    best, best_score = None, 0
    for name in names:
        low = name.lower()
        if "light" not in low and "ambient" not in low:
            continue
        score = 1
        if "ambient light" in low:
            score += 3                      # cahaya sekitar yang sebenarnya
        if "non-wakeup" in low:
            score += 2                      # versi biasa, tidak membangunkan CPU
        if any(w in low for w in ("raw", "strm", "fov", "optimization")):
            score -= 5                      # data mentah, stream, atau sensor turunan
        if "wakeup" in low and "non-wakeup" not in low:
            score -= 5                      # versi wakeup, duplikat dari yang biasa
        if score > best_score:
            best, best_score = name, score
    return best


def list_sensors(timeout=10):
    """Daftar nama sensor dari `termux-sensor -l`."""
    out = subprocess.run(["termux-sensor", "-l"], capture_output=True, text=True, timeout=timeout)
    try:
        data = json.loads(out.stdout)
    except ValueError:
        return []
    names = data.get("sensors") if isinstance(data, dict) else None
    return [n for n in names if isinstance(n, str)] if isinstance(names, list) else []


def read_lux(sensor_name, timeout=5):
    """Satu pembacaan: jalankan termux-sensor sekali (-n 1) lalu urai hasilnya."""
    out = subprocess.run(["termux-sensor", "-s", sensor_name, "-n", "1"],
                         capture_output=True, text=True, timeout=timeout)
    return parse_lux(out.stdout)


def main():
    import paho.mqtt.client as mqtt

    ap = argparse.ArgumentParser()
    ap.add_argument("--device", required=True, help="id perangkat, sama dengan yang diisi di halaman sensor")
    ap.add_argument("--sensor", default=None,
                    help="nama sensor cahaya (lihat termux-sensor -l); jika dikosongkan dicari otomatis")
    ap.add_argument("--interval", type=float, default=1.0, help="selang pembacaan, detik")
    ap.add_argument("--count", type=int, default=0, help="berhenti setelah sekian pesan (0 = terus)")
    args = ap.parse_args()

    pid = config.require_project_id()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="comfyspot-light-%s-%s" % (pid, args.device))
    if config.BROKER_USERNAME:
        client.username_pw_set(config.BROKER_USERNAME, config.BROKER_PASSWORD)
    if config.BROKER_TLS:
        client.tls_set()
    client.connect_async(config.BROKER_HOST, config.BROKER_PORT, keepalive=30)
    client.loop_start()   # thread terpisah yang mengurus jaringan dan menyambung ulang

    sensor = args.sensor
    if sensor is None:
        try:
            sensor = pick_light_sensor(list_sensors())
        except FileNotFoundError:
            raise SystemExit("termux-sensor tidak ditemukan. Jalankan: pkg install termux-api, "
                             "dan pasang aplikasi Termux:API dari F-Droid.")
        except subprocess.TimeoutExpired:
            sensor = None
        if sensor is None:
            raise SystemExit("Sensor cahaya tidak ditemukan otomatis. Lihat `termux-sensor -l` "
                             "lalu jalankan dengan --sensor \"<nama sensor>\".")

    topic = topics.light(pid, args.device)
    sent = failures = 0
    print("Membaca sensor '%s', mengirim ke %s" % (sensor, topic))
    try:
        while args.count == 0 or sent < args.count:
            started = time.time()
            try:
                lux = read_lux(sensor)
            except FileNotFoundError:
                raise SystemExit("termux-sensor tidak ditemukan. Jalankan: pkg install termux-api, "
                                 "dan pasang aplikasi Termux:API dari F-Droid.")
            except subprocess.TimeoutExpired:
                lux = None

            if lux is None:
                failures += 1
                if failures in (1, 5) or failures % 30 == 0:
                    print("Belum ada angka cahaya dari '%s' (gagal %d kali). Cek `termux-sensor -l` "
                          "dan pakai --sensor dengan nama yang sesuai." % (sensor, failures))
            else:
                failures = 0
                sent += 1
                payload = {"device_id": args.device, "lux": round(lux, 1),
                           "lux_source": "termux", "sent_at_ms": int(time.time() * 1000)}
                client.publish(topic, json.dumps(payload), qos=0)
                print("lux %.1f" % lux)
            # tidur sisa selang, dikurangi waktu yang dipakai membaca sensor
            time.sleep(max(0.0, args.interval - (time.time() - started)))
        time.sleep(1.0)   # beri waktu pesan terakhir terkirim
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
