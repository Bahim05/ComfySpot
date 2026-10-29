import os
import tempfile
import unittest

from hub import Hub, OFFLINE_AFTER_S


def msg(lux, mic, location="Selasar"):
    return {"location": location, "lux": lux, "mic_rms": mic}


class HubTests(unittest.TestCase):
    def feed(self, hub, device, lux, mic, location, start=1000.0, n=5):
        for i in range(n):
            hub.handle_telemetry(device, msg(lux, mic, location), start + i)
        return start + n - 1

    def test_ranking_and_controls(self):
        hub = Hub()
        now = self.feed(hub, "edge1", 450, 0.004, "Selasar")
        self.feed(hub, "edge2", 200, 0.12, "Kantin")
        state, controls = hub.snapshot(now)
        self.assertEqual(state["best"], "edge1")
        self.assertEqual([x["location"] for x in state["ranking"]], ["Selasar", "Kantin"])
        self.assertEqual(controls["edge1"]["status"], "hijau")
        self.assertTrue(controls["edge1"]["best"])
        self.assertEqual(controls["edge2"]["status"], "merah")

    def test_mode_message_changes_ranking(self):
        hub = Hub()
        now = self.feed(hub, "terang", 500, 0.12, "Terang ribut")
        self.feed(hub, "redup", 120, 0.004, "Redup tenang")
        self.assertEqual(hub.snapshot(now)[0]["best"], "redup")
        self.assertTrue(hub.set_mode("RAMAI "))
        self.assertEqual(hub.snapshot(now)[0]["best"], "terang")

    def test_unknown_mode_is_ignored(self):
        hub = Hub()
        self.assertFalse(hub.set_mode("bebas"))
        self.assertFalse(hub.set_mode(None))
        self.assertEqual(hub.mode, "hening")

    def test_invalid_payloads_are_rejected(self):
        hub = Hub()
        bad = [
            {"lux": "300", "mic_rms": 0.01},
            {"lux": 300},
            {"lux": -5, "mic_rms": 0.01},
            {"lux": float("nan"), "mic_rms": 0.01},
            {"lux": float("inf"), "mic_rms": 0.01},
            {"lux": True, "mic_rms": 0.01},
            "bukan dict",
            None,
        ]
        for payload in bad:
            self.assertIsNone(hub.handle_telemetry("edge1", payload, 1000.0), payload)
        self.assertEqual(hub.invalid, len(bad))
        self.assertEqual(hub.snapshot(1000.0)[0]["ranking"], [])

    def test_offline_device_leaves_ranking(self):
        hub = Hub()
        self.feed(hub, "edge1", 450, 0.004, "Selasar", start=1000.0)
        later = 1004.0 + OFFLINE_AFTER_S + 1
        state, controls = hub.snapshot(later)
        self.assertEqual(state["ranking"], [])
        self.assertEqual(state["offline"], ["edge1"])
        self.assertEqual(controls, {})

    def test_calibration_offset_per_device(self):
        plain = Hub()
        cal = Hub(offsets={"edge1": 5.0})
        a = plain.handle_telemetry("edge1", msg(300, 0.01), 1000.0)["db"]
        b = cal.handle_telemetry("edge1", msg(300, 0.01), 1000.0)["db"]
        self.assertAlmostEqual(b - a, 5.0)

    def test_location_defaults_to_device_id_and_is_trimmed(self):
        hub = Hub()
        r = hub.handle_telemetry("edge9", {"lux": 300, "mic_rms": 0.01}, 1000.0)
        self.assertEqual(r["location"], "edge9")
        r = hub.handle_telemetry("edge8", {"lux": 300, "mic_rms": 0.01, "location": "x" * 200}, 1000.0)
        self.assertEqual(len(r["location"]), 40)

    def test_hourly_save_and_load(self):
        hub = Hub()
        self.feed(hub, "edge1", 450, 0.004, "Selasar")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "hourly.json")
            hub.save_hourly(path)
            other = Hub()
            other.load_hourly(path)
        self.assertEqual(other.hourly.locations(), ["Selasar"])

    def test_state_is_json_serializable(self):
        import json
        hub = Hub()
        now = self.feed(hub, "edge1", 450, 0.004, "Selasar")
        state, controls = hub.snapshot(now)
        json.dumps(state)
        json.dumps(controls)


if __name__ == "__main__":
    unittest.main()
