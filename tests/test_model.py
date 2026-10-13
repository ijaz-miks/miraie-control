import pathlib
import sys
import unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'backend'))
from core import capabilities, control_payload


class ModelTests(unittest.TestCase):
    def test_model_controls_replace_generic_choices_and_temperature_limits(self):
        detail = {'_model': {'controlCapabilities': [
            {'capability': 'ACMode', 'attributes': [{'key': 'acmd', 'values': ['cool', 'dry']}]},
            {'capability': 'ACFanSpeed', 'attributes': [{'key': 'acfs', 'values': ['auto', 'low']}]},
            {'capability': 'ACTemperature', 'attributes': [{'key': 'actmp', 'range': {
                'min': [{'unit': 'celcius', 'value': 18}], 'max': [{'unit': 'celcius', 'value': 28}],
                'increment': [{'unit': 'celcius', 'value': 0.5}]}}]},
            {'capability': 'Convertible', 'attributes': [{'key': 'cnv', 'intValues': [0, 40, 100, 110]}]},
        ]}}
        caps = capabilities({'acmd': 'cool', 'acfs': 'auto', 'actmp': '24', 'cnv': 0, 'achs': 0}, detail)
        self.assertEqual(caps['modes'], ['cool', 'dry'])
        self.assertEqual(caps['fanModes'], ['auto', 'low'])
        self.assertEqual(caps['temperature'], {'min': 18, 'max': 28, 'step': 0.5})
        self.assertEqual(caps['capacity'], ['0', '40', '100', '110'])
        self.assertEqual(caps['horizontalSwing'], [])

    def test_heat_requires_model_or_observed_support(self):
        self.assertNotIn('heat', capabilities({'acmd': 'cool'}, {})['modes'])
        caps = capabilities({'acmd': 'cool'}, {'_model': {'controlCapabilities': [
            {'capability': 'ACMode', 'attributes': [{'key': 'acmd', 'values': ['cool', 'heat']}]}]}})
        self.assertEqual(caps['modes'], ['cool', 'heat'])

    def test_nanoe_g_is_enabled_only_with_matching_model_type_and_correct_index(self):
        self.assertFalse(capabilities({'acng': 'off'}, {})['nanoe'])
        caps = capabilities({'acng': 'off'}, {'_model': {'controlCapabilities': [
            {'capability': 'NanoeG', 'attributes': [{'key': 'acng', 'values': ['on', 'off']}]}]}})
        self.assertTrue(caps['nanoe'])
        payload = control_payload('nanoe', True, {'state': {'nanoe': False}, 'capabilities': caps})
        self.assertEqual(payload['ki'], 7)
        self.assertEqual(payload['acng'], 'on')


if __name__ == '__main__':
    unittest.main()
