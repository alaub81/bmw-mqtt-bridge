"""Compile actual widget expressions and exercise their production helpers."""
import json
from pathlib import Path
import subprocess
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


class WidgetTests(unittest.TestCase):
    def test_expressions_state_mappings_units_and_invalid_items(self):
        document = yaml.safe_load((ROOT / 'openHAB/bmw-vehicle-widgets.yml').read_text())
        expressions, helpers = [], []

        def visit(value):
            if isinstance(value, dict):
                if 'functions' in value:
                    helpers.append(value['functions'])
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
            elif isinstance(value, str) and value.startswith('='):
                expressions.append(value[1:])

        visit(document)
        self.assertGreater(len(expressions), 100)
        self.assertGreaterEqual(len(helpers), 2)
        script = r'''
const assert = require('node:assert/strict');
const data = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
for (const expression of data.expressions) new Function('return (' + expression + ')');
let tested = 0;
for (const definitions of data.helpers) {
  const props = {windowItems: ['window'], doorItems: ['door']};
  const items = {};
  const fn = {};
  for (const [name, expression] of Object.entries(definitions))
    fn[name] = new Function('items', 'props', 'fn', 'return (' + expression.slice(1) + ')')(items, props, fn);
  if (!fn.openingState) continue;
  tested++;
  for (const state of ['OPEN', 'OPENED', 'INTERMEDIATE', 'TILTED', 'OPEN_TILT', 'PARTIALLY_OPEN', ' open_tilt ']) {
    items.window = {state}; assert.equal(fn.openingState('window'), 'open');
  }
  for (const state of ['CLOSE', 'CLOSED']) {
    items.window = {state}; assert.equal(fn.openingState('window'), 'closed');
  }
  for (const [state, expected] of [['TRUE','open'], ['ON','open'], ['FALSE','closed'], ['OFF','closed']]) {
    items.door = {state}; assert.equal(fn.openingState('door'), expected);
  }
  for (const state of ['NULL', 'UNDEF', '', 'nonsense']) {
    items.window = {state}; assert.equal(fn.openingState('window'), 'unknown');
    if (fn.power) assert.equal(fn.power('window'), -1);
    if (fn.pressure) assert.equal(fn.pressure('window'), '—');
  }
  assert.equal(fn.openingState('missing'), 'unknown');
  if (fn.power) assert.equal(fn.power('missing'), -1);
  if (fn.pressure) assert.equal(fn.pressure('missing'), '—');
  props.windowOpenStates = ['CUSTOM']; props.windowClosedStates = ['CUSTOM'];
  items.window = {state:'custom'}; assert.equal(fn.openingState('window'), 'open');
  assert.deepEqual(fn.openingNames(['window', '', 'window', 'door']), ['window','door']);
  assert.equal(fn.openingSummary(['closed','unknown']), '1 unbekannt');
  assert.equal(fn.openingSummary(['open','unknown']), '1 offen · 1 unbekannt');
  assert.equal(fn.openingSummary([]), 'nicht konfiguriert');
  if (fn.power) {
  items.power = {state:'2.5 kW'}; assert.equal(fn.power('power'), 2500);
  items.power = {state:'2500', unit:'W'}; assert.equal(fn.power('power'), 2500);
  items.power = {state:'5 V'}; assert.equal(fn.power('power'), -1);
  }
  if (fn.pressure) for (const [state, expected] of [['250 kPa','2,50 bar'], ['2.5 bar','2,50 bar'], ['250000 Pa','2,50 bar'], ['-1 bar','—'], ['2 V','—']]) {
    items.tire = {state}; assert.equal(fn.pressure('tire'), expected);
  }
  if (fn.energy) {
    items.energy = {state:'500 Wh'}; assert.equal(fn.energy('energy'), 0.5);
    items.energy = {state:'5 V'}; assert.equal(fn.energy('energy'), -1);
    assert.equal(fn.deficit(50, 80, 0, false), 40);
    assert.equal(fn.deficit(101, 80, 0, false), -1);
  }
}
assert.ok(tested >= 2);
'''
        result = subprocess.run(['node', '-e', script], input=json.dumps({
            'expressions': expressions, 'helpers': helpers,
        }), capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
