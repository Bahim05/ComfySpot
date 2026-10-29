"""Nama topic MQTT. Satu tempat saja supaya hub, simulator, dan halaman web tidak beda ketik.

Struktur (PID = PROJECT_ID kelompok):
  comfyspot/PID/edge/<device>/telemetry   HP -> broker : hasil pembacaan sensor
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
