"""Simulator HP lokasi, untuk melatih hub dan dashboard tanpa HP.

Contoh dua lokasi, di dua terminal:
  python edge_sim.py --device edge1 --location Selasar --profile quiet
  python edge_sim.py --device edge2 --location Kantin  --profile noisy
"""
import argparse
import json
import math
import random
import time

from shared import config, topics

# Tiap profil: (lux rata-rata, simpangan lux, RMS mikrofon rata-rata, simpangan RMS)
# RMS 0.004 sekitar 32 dB relatif, 0.02 sekitar 46 dB, 0.2 sekitar 66 dB (lihat shared/processing.py)
PROFILES = {
    "quiet": (450, 25, 0.004, 0.001),
    "normal": (320, 40, 0.02, 0.006),
    "noisy": (180, 40, 0.2, 0.04),
}


def sample(profile, t0, now):
    """Satu pembacaan palsu. Profil dynamic berpindah antara tenang dan ramai tiap 60 detik."""
    if profile == "dynamic":
        phase = (now - t0) / 60.0
        profile = "quiet" if math.sin(phase * math.pi) >= 0 else "noisy"
    lux_mu, lux_sd, rms_mu, rms_sd = PROFILES[profile]
    return max(0.0, random.gauss(lux_mu, lux_sd)), max(0.0, random.gauss(rms_mu, rms_sd))


def main():
    import paho.mqtt.client as mqtt

    ap = argparse.ArgumentParser()
    ap.add_argument("--device", required=True, help="id perangkat, misalnya edge1")
    ap.add_argument("--location", default=None, help="nama lokasi yang tampil di dashboard")
    ap.add_argument("--profile", choices=list(PROFILES) + ["dynamic"], default="normal")
    ap.add_argument("--interval", type=float, default=1.0, help="selang kirim, detik")
    ap.add_argument("--count", type=int, default=0, help="berhenti setelah sekian pesan (0 = terus)")
    args = ap.parse_args()

    pid = config.require_project_id()
    location = args.location or args.device

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="comfyspot-sim-%s-%s" % (pid, args.device))
    if config.BROKER_USERNAME:
        client.username_pw_set(config.BROKER_USERNAME, config.BROKER_PASSWORD)
    if config.BROKER_TLS:
        client.tls_set()

    def on_connect(c, userdata, flags, reason_code, properties):
        print("Tersambung, mendengarkan", topics.edge_control(pid, args.device))
        c.subscribe(topics.edge_control(pid, args.device), qos=1)

    def on_message(c, userdata, msg):
        # perintah dari hub: di HP asli ini yang memicu lampu hijau, kuning, atau merah di layar
        ctl = json.loads(msg.payload.decode("utf-8"))
        print("  lampu status: %s | peringkat %s | skor %s | mode %s" % (
            ctl.get("status"), ctl.get("rank"), ctl.get("score"), ctl.get("mode")))

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect_async(config.BROKER_HOST, config.BROKER_PORT, keepalive=30)
    client.loop_start()

    t0 = time.time()
    seq = 0
    try:
        while args.count == 0 or seq < args.count:
            now = time.time()
            lux, mic_rms = sample(args.profile, t0, now)
            seq += 1
            payload = {
                "device_id": args.device, "location": location, "sequence": seq,
                "lux": round(lux, 1), "mic_rms": round(mic_rms, 5), "lux_source": "sim",
                "sent_at_ms": int(now * 1000),
            }
            client.publish(topics.telemetry(pid, args.device), json.dumps(payload), qos=0)
            time.sleep(args.interval)
        time.sleep(1.5)   # beri waktu pesan terakhir terkirim dan perintah balasan masuk
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
