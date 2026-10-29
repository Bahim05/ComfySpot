import unittest

from light_termux import parse_lux, pick_light_sensor


class ParseLuxTests(unittest.TestCase):
    def test_normal_output(self):
        text = '{\n  "TCS3701 Light": {\n    "values": [\n      123.5\n    ]\n  }\n}'
        self.assertAlmostEqual(parse_lux(text), 123.5)

    def test_integer_value(self):
        self.assertEqual(parse_lux('{"light": {"values": [87]}}'), 87.0)

    def test_first_usable_sensor_is_taken(self):
        text = '{"Light (wakeup)": {"values": []}, "Light": {"values": [55.0]}}'
        self.assertEqual(parse_lux(text), 55.0)

    def test_dark_room_zero_is_valid(self):
        self.assertEqual(parse_lux('{"light": {"values": [0.0]}}'), 0.0)

    def test_no_sensor_or_junk(self):
        for text in ["", "bukan json", "{}", "[]", "null", '{"light": {}}', '{"light": {"values": ["a"]}}',
                     '{"light": {"values": [-3]}}', '{"light": "x"}', '{"light": {"values": [true]}}', None]:
            self.assertIsNone(parse_lux(text), text)


# Daftar sensor asli dari satu HP Xiaomi (dipotong ke bagian yang relevan)
XIAOMI = [
    "Proximity Sensor",
    "lsm6dsv Accelerometer Non-wakeup",
    "tsl2522_fb ambient Ambient Light Sensor Non-wakeup",
    "tsl2522_fb ambient Ambient Light Sensor Wakeup",
    "tsl2522_fb ambient Ambient Light Sensor Strm Non-wakeup",
    "tsl2522_fb ambient Ambient Light Sensor Raw Data Non-wakeup",
    "Optimization light fov Sensor",
]


class PickLightSensorTests(unittest.TestCase):
    def test_xiaomi_picks_the_real_ambient_sensor(self):
        self.assertEqual(pick_light_sensor(XIAOMI), "tsl2522_fb ambient Ambient Light Sensor Non-wakeup")

    def test_order_does_not_matter(self):
        self.assertEqual(pick_light_sensor(list(reversed(XIAOMI))), "tsl2522_fb ambient Ambient Light Sensor Non-wakeup")

    def test_fov_sensor_alone_is_still_a_last_resort_none(self):
        self.assertIsNone(pick_light_sensor(["Optimization light fov Sensor"]))

    def test_simple_names(self):
        self.assertEqual(pick_light_sensor(["Accelerometer", "Light Sensor"]), "Light Sensor")
        self.assertEqual(pick_light_sensor(["lightsensor"]), "lightsensor")
        self.assertEqual(pick_light_sensor(["BH1745 Ambient Light Sensor", "BH1745 Ambient Light Sensor Wakeup"]),
                         "BH1745 Ambient Light Sensor")

    def test_nothing_found(self):
        self.assertIsNone(pick_light_sensor([]))
        self.assertIsNone(pick_light_sensor(["Accelerometer", "Gyroscope"]))


if __name__ == "__main__":
    unittest.main()
