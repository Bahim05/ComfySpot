"""Nama topic MQTT. Satu tempat saja supaya hub, simulator, dan halaman web tidak beda ketik.

Struktur (PID = PROJECT_ID kelompok):
  comfyspot/PID/edge/<device>/telemetry   HP -> broker : RMS mikrofon dari halaman sensor (lux ikut jika ada)
  comfyspot/PID/edge/<device>/light       HP -> broker : lux dari skrip Termux di HP yang sama
  comfyspot/PID/edge/<device>/control     hub -> HP    : skor, peringkat, warna lampu status
  comfyspot/PID/control/mode              dashboard -> hub : mode "hening" atau "ramai"
  comfyspot/PID/state                     hub -> dashboard : peringkat dan statistik per jam
"""


def telemetry(pid, device):
    return "comfyspot/%s/edge/%s/telemetry" % (pid, device)


def telemetry_all(pid):
    # tanda + mengganti tepat satu level, jadi cocok untuk semua device
    return "comfyspot/%s/edge/+/telemetry" % pid


def edge_control(pid, device):
    return "comfyspot/%s/edge/%s/control" % (pid, device)


def mode(pid):
    return "comfyspot/%s/control/mode" % pid


def state(pid):
    return "comfyspot/%s/state" % pid


def light(pid, device):
    # dikirim skrip Termux di HP yang sama; hub menggabungkannya dengan telemetry dari halaman sensor
    return "comfyspot/%s/edge/%s/light" % (pid, device)


def light_all(pid):
    return "comfyspot/%s/edge/+/light" % pid
