import unittest

from shared import rules as r


class ScoreTests(unittest.TestCase):
    def test_light_score(self):
        self.assertEqual(r.light_score(0), 0.0)
        self.assertAlmostEqual(r.light_score(150), 0.5)
        self.assertEqual(r.light_score(300), 1.0)
        self.assertEqual(r.light_score(2000), 1.0)

    def test_noise_score_curve_points(self):
        self.assertEqual(r.noise_score(20), 1.0)
        self.assertEqual(r.noise_score(40), 1.0)
        self.assertAlmostEqual(r.noise_score(55), 0.6)
        self.assertAlmostEqual(r.noise_score(75), 0.0)
        self.assertEqual(r.noise_score(100), 0.0)

    def test_noise_score_interpolates(self):
        self.assertAlmostEqual(r.noise_score(47.5), 0.8)    # tengah antara 40 dB dan 55 dB
        self.assertAlmostEqual(r.noise_score(65), 0.3)      # tengah antara 55 dB dan 75 dB

    def test_noise_score_never_increases_with_noise(self):
        scores = [r.noise_score(db) for db in range(0, 100)]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_comfort_score_perfect_and_worst(self):
        self.assertAlmostEqual(r.comfort_score(500, 30), 100.0)
        self.assertAlmostEqual(r.comfort_score(0, 90), 0.0)

    def test_dark_room_is_never_comfortable(self):
        # kasus nyata dari uji HP: lux 0 dan 34 dB dulu mendapat 70 (kuning) karena suaranya tenang
        for mode in r.MODES:
            score = r.comfort_score(0, 34, mode)
            self.assertEqual(score, 0.0)
            self.assertEqual(r.status_for(score), "merah")

    def test_deafening_room_is_never_comfortable(self):
        for mode in r.MODES:
            self.assertEqual(r.comfort_score(500, 80, mode), 0.0)

    def test_score_rises_with_light_and_falls_with_noise(self):
        lights = [r.comfort_score(lux, 40) for lux in range(0, 400, 20)]
        self.assertEqual(lights, sorted(lights))
        noises = [r.comfort_score(300, db) for db in range(30, 90, 5)]
        self.assertEqual(noises, sorted(noises, reverse=True))

    def test_dim_but_quiet_is_not_green(self):
        self.assertEqual(r.status_for(r.comfort_score(100, 30, "hening")), "kuning")

    def test_mode_changes_weighting(self):
        # ruang terang tapi ribut: lebih layak di mode ramai daripada mode hening
        quiet_mode = r.comfort_score(500, 65, "hening")
        loud_ok_mode = r.comfort_score(500, 65, "ramai")
        self.assertGreater(loud_ok_mode, quiet_mode)

    def test_unknown_mode(self):
        with self.assertRaises(ValueError):
            r.comfort_score(300, 40, "bebas")

    def test_status_thresholds(self):
        self.assertEqual(r.status_for(75), "hijau")
        self.assertEqual(r.status_for(74.9), "kuning")
        self.assertEqual(r.status_for(50), "kuning")
        self.assertEqual(r.status_for(49.9), "merah")

    def test_noise_level_labels(self):
        self.assertEqual(r.noise_level(30), "sepi")
        self.assertEqual(r.noise_level(50), "sedang")
        self.assertEqual(r.noise_level(60), "ramai")


class RankTests(unittest.TestCase):
    def test_ranking_order_and_fields(self):
        readings = [
            {"device_id": "edge2", "lux_avg": 200.0, "db": 62.0},
            {"device_id": "edge1", "lux_avg": 450.0, "db": 32.0},
        ]
        out = r.rank_readings(readings)
        self.assertEqual([x["device_id"] for x in out], ["edge1", "edge2"])
        self.assertEqual([x["rank"] for x in out], [1, 2])
        self.assertEqual(out[0]["status"], "hijau")
        self.assertEqual(out[1]["status"], "merah")
        self.assertNotIn("rank", readings[0])    # masukan asli tidak diubah

    def test_ties_are_deterministic(self):
        readings = [
            {"device_id": "b", "lux_avg": 300.0, "db": 40.0},
            {"device_id": "a", "lux_avg": 300.0, "db": 40.0},
        ]
        self.assertEqual([x["device_id"] for x in r.rank_readings(readings)], ["a", "b"])

    def test_mode_can_change_winner(self):
        readings = [
            {"device_id": "terang_ribut", "lux_avg": 500.0, "db": 62.0},
            {"device_id": "redup_tenang", "lux_avg": 120.0, "db": 33.0},
        ]
        self.assertEqual(r.rank_readings(readings, "hening")[0]["device_id"], "redup_tenang")
        self.assertEqual(r.rank_readings(readings, "ramai")[0]["device_id"], "terang_ribut")

    def test_empty(self):
        self.assertEqual(r.rank_readings([]), [])


class HourlyTests(unittest.TestCase):
    def test_average_per_hour(self):
        h = r.HourlyStats()
        h.add("Kantin", 12, 60.0, 200)
        h.add("Kantin", 12, 64.0, 220)
        self.assertAlmostEqual(h.average_db("Kantin", 12), 62.0)
        self.assertAlmostEqual(h.average_lux("Kantin", 12), 210.0)
        self.assertIsNone(h.average_db("Kantin", 13))

    def test_profile_has_24_slots(self):
        h = r.HourlyStats()
        h.add("Kantin", 0, 40.0, 100)
        p = h.profile("Kantin")
        self.assertEqual(len(p), 24)
        self.assertEqual(p[0], 40.0)
        self.assertIsNone(p[1])

    def test_busiest_hours_ignores_thin_data(self):
        h = r.HourlyStats()
        for _ in range(6):
            h.add("Kantin", 12, 65.0, 200)
            h.add("Kantin", 9, 50.0, 300)
        h.add("Kantin", 3, 90.0, 10)           # hanya satu sampel, harus diabaikan
        self.assertEqual(h.busiest_hours("Kantin", top=2), [12, 9])

    def test_roundtrip_dict(self):
        h = r.HourlyStats()
        h.add("Ruang | Diskusi", 8, 45.0, 350)   # tanda | di nama lokasi tidak boleh merusak
        h2 = r.HourlyStats.from_dict(h.to_dict())
        self.assertAlmostEqual(h2.average_db("Ruang | Diskusi", 8), 45.0)


if __name__ == "__main__":
    unittest.main()
