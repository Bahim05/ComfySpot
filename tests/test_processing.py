import math
import unittest

from shared import processing as p


class RmsTests(unittest.TestCase):
    def test_constant_signal(self):
        self.assertAlmostEqual(p.rms([0.5, 0.5, 0.5]), 0.5)

    def test_positive_and_negative_do_not_cancel(self):
        # rata-rata biasa gelombang ini 0, tetapi RMS-nya 1
        self.assertAlmostEqual(p.rms([1, -1, 1, -1]), 1.0)

    def test_empty(self):
        self.assertEqual(p.rms([]), 0.0)


class DbTests(unittest.TestCase):
    def test_reference_is_zero_db(self):
        self.assertAlmostEqual(p.rms_to_db(p.DB_REF_RMS), 0.0)

    def test_ten_times_is_twenty_db(self):
        self.assertAlmostEqual(p.rms_to_db(p.DB_REF_RMS * 10), 20.0)

    def test_offset_is_added(self):
        self.assertAlmostEqual(p.rms_to_db(p.DB_REF_RMS * 10, offset_db=3.5), 23.5)

    def test_zero_or_none_gives_zero(self):
        self.assertEqual(p.rms_to_db(0), 0.0)
        self.assertEqual(p.rms_to_db(None), 0.0)

    def test_never_negative(self):
        self.assertEqual(p.rms_to_db(p.DB_REF_RMS / 100), 0.0)

    def test_calibration_offset(self):
        # HP membaca 2 dB terlalu rendah dan 4 dB terlalu rendah, rata-rata selisih 3
        self.assertAlmostEqual(p.calibration_offset([40, 50], [42, 54]), 3.0)

    def test_calibration_offset_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            p.calibration_offset([], [])
        with self.assertRaises(ValueError):
            p.calibration_offset([1, 2], [1])


class WindowTests(unittest.TestCase):
    def test_old_values_are_dropped(self):
        w = p.Window(10)
        w.add(0, 100)
        w.add(5, 200)
        w.add(12, 300)      # nilai pada t=0 sudah lebih tua dari 10 detik
        self.assertEqual(w.count(), 2)
        self.assertAlmostEqual(w.mean(), 250)

    def test_mean_and_std(self):
        w = p.Window(100)
        for i, v in enumerate([2, 4, 4, 4, 5, 5, 7, 9]):
            w.add(i, v)
        self.assertAlmostEqual(w.mean(), 5.0)
        self.assertAlmostEqual(w.std(), 2.0)

    def test_empty_window(self):
        w = p.Window(10)
        self.assertIsNone(w.mean())
        self.assertIsNone(w.std())
        self.assertIsNone(w.rms())

    def test_rms_weights_loud_bursts(self):
        w = p.Window(10)
        w.add(0, 0.0)
        w.add(1, 0.2)
        self.assertAlmostEqual(w.rms(), math.sqrt(0.02))   # lebih besar dari rata-rata biasa (0.1)


class SensorProcessorTests(unittest.TestCase):
    def test_steady_light_is_stable(self):
        sp = p.SensorProcessor()
        out = None
        for t in range(10):
            out = sp.update(t, 400.0, 0.004)
        self.assertAlmostEqual(out["lux_avg"], 400.0)
        self.assertTrue(out["lux_stable"])
        self.assertAlmostEqual(out["db"], 20 * math.log10(0.004 / p.DB_REF_RMS), places=3)

    def test_flickering_light_is_not_stable(self):
        sp = p.SensorProcessor()
        out = None
        for t in range(10):
            out = sp.update(t, 100.0 if t % 2 else 600.0, 0.004)
        self.assertFalse(out["lux_stable"])

    def test_offset_applied(self):
        a = p.SensorProcessor(db_offset=0.0).update(0, 300, 0.01)["db"]
        b = p.SensorProcessor(db_offset=4.0).update(0, 300, 0.01)["db"]
        self.assertAlmostEqual(b - a, 4.0)

    def test_loud_burst_fades_quickly(self):
        # Jendela suara pendek: setelah teriakan berhenti, desibel kembali normal dalam beberapa detik
        # (dengan jendela 10 detik, angkanya masih tinggi lama setelah suara berhenti).
        sp = p.SensorProcessor()
        t = 0.0
        while t < 10:                       # 10 detik tenang, dua pembacaan per detik
            quiet = sp.update(t, 400.0, 0.005)["db"]
            t += 0.5
        loud = sp.update(t, 400.0, 0.3)["db"]
        self.assertGreater(loud, quiet + 15)          # naik langsung pada pembacaan pertama
        t += 0.5
        while t < 10.5 + p.MIC_WINDOW_S + 1:
            after = sp.update(t, 400.0, 0.005)["db"]
            t += 0.5
        self.assertAlmostEqual(after, quiet, places=3)

    def test_dark_room_does_not_crash(self):
        out = p.SensorProcessor().update(0, 0.0, 0.0)
        self.assertTrue(out["lux_stable"])
        self.assertEqual(out["db"], 0.0)


if __name__ == "__main__":
    unittest.main()
