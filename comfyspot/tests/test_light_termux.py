import unittest

from light_termux import parse_lux


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


if __name__ == "__main__":
    unittest.main()
