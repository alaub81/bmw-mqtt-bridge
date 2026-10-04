"""Check the widget file-import contract and card integration."""
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1] / 'openHAB'
WIDGET_FILE = ROOT / 'bmw-vehicle-widgets.yml'


class OpenHabWidgetTests(unittest.TestCase):
    def test_opening_categories_have_separate_configurable_states(self):
        names = ('windowOpenStates', 'windowClosedStates',
                 'doorOpenStates', 'doorClosedStates')
        widgets = yaml.safe_load(WIDGET_FILE.read_text())['widgets']
        widget = widgets['bmw_vehicle_comfort_list']
        card = widgets['bmw_vehicle_comfort_card']
        for document in (widget, card):
            parameters = {p['name']: p for p in document['props']['parameters']}
            for name in names:
                with self.subTest(parameter=name):
                    self.assertTrue(parameters[name]['multiple'])
                    self.assertNotIn('default', parameters[name])
        config = card['slots']['default'][0]['config']
        for name in names:
            self.assertEqual(config[name], '=props.' + name)

    def test_parameter_defaults_are_scalar(self):
        # The file importer deserializes defaults as strings, including
        # parameters that allow multiple selections in the widget editor.
        for path in ROOT.glob('*.yml'):
            document = yaml.safe_load(path.read_text())
            widgets = document.get('widgets', {document.get('uid'): document})
            for uid, widget in widgets.items():
                for parameter in widget['props']['parameters']:
                    with self.subTest(file=path.name, widget=uid,
                                      parameter=parameter['name']):
                        self.assertNotIsInstance(parameter.get('default'),
                                                 (list, dict))

    def test_card_embeds_list_and_forwards_all_parameters(self):
        bundle = yaml.safe_load(WIDGET_FILE.read_text())
        self.assertEqual(bundle['version'], 1)
        widgets = bundle['widgets']
        widget = widgets['bmw_vehicle_comfort_list']
        card = widgets['bmw_vehicle_comfort_card']
        embedded = card['slots']['default'][0]
        self.assertEqual(embedded['component'], 'widget:bmw_vehicle_comfort_list')
        self.assertEqual(card['props'], widget['props'])
        for parameter in widget['props']['parameters']:
            name = parameter['name']
            with self.subTest(parameter=name):
                self.assertEqual(embedded['config'][name], '=props.' + name)


if __name__ == '__main__':
    unittest.main()
