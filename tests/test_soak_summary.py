"""Protect summary attribution, missing-value handling and restart boundaries."""
import importlib.util
import json
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location('summarize_soak', Path(__file__).with_name('summarize_soak.py'))
summary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(summary)


def row(elapsed, generation=1, **values):
    return dict(schemaVersion=2, elapsedSeconds=elapsed, generation=generation,
                status='running', health='healthy', composeService='bmw-mqtt-bridge',
                bridgeVersion='fixture', **values)


def report(*rows):
    return summary.summarize(json.dumps(value) for value in rows)


class SoakSummaryTests(unittest.TestCase):
    def test_weighted_cpu_memory_and_write_totals_have_explicit_coverage(self):
        result = report(
            row(0, restartCount=2, processRssSumBytes=100, processesExpected=1, processesRead=1),
            row(60, cpuPercentOneCore=1, processRssSumBytes=120, processesExpected=1, processesRead=1,
                processIoPerSecond={'write_bytes': 2}),
            row(180, cpuPercentOneCore=4, processRssSumBytes=110, processesExpected=1, processesRead=1,
                processIoPerSecond={'write_bytes': 1}),
        )
        segment = result['segments'][0]
        self.assertEqual(result['composeServices'], ['bmw-mqtt-bridge'])
        self.assertEqual(segment['cpuMeanPercentOneCore'], 3)
        self.assertEqual(segment['cpuCoverageSeconds'], 180)
        self.assertEqual(segment['restartCountMax'], 2)
        self.assertEqual(segment['rssMaxBytes'], 120)
        self.assertEqual(segment['rssChangeBytes'], 10)
        self.assertEqual(segment['processWriteBytesApprox'], 240)
        self.assertEqual(segment['processWriteCoverageSeconds'], 180)

    def test_generations_and_missing_metrics_do_not_imply_zero_or_memory_leaks(self):
        result = report(row(0), row(60, 2, cpuPercentOneCore=99, processIoPerSecond={'write_bytes': 999}),
                        row(120, 2, processRssSumBytes=100, processesExpected=2, processesRead=1))
        for segment in result['segments']:
            self.assertIsNone(segment['cpuMeanPercentOneCore'])
            self.assertNotIn('cpuWeightedTotal', segment)
            self.assertIsNone(segment['processWriteBytesApprox'])
            self.assertIsNone(segment['rssMaxBytes'])
            self.assertIsNone(segment['rssChangeBytes'])
        self.assertEqual(len(result['segments']), 2)
        self.assertEqual(report(row(0), row(60, processIoPerSecond={'write_bytes': 0}))
                         ['segments'][0]['processWriteBytesApprox'], 0)

    def test_bad_empty_nonfinite_and_out_of_order_input_fails(self):
        for values in [[], ['broken'], ['null'], [json.dumps(row(float('nan')))],
                       [json.dumps(row(0)), json.dumps(row(0))],
                       [json.dumps(row(1, 2)), json.dumps(row(2, 1))]]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                summary.summarize(values)

    def test_old_files_are_explicitly_unattributed(self):
        legacy = row(0)
        del legacy['composeService']
        del legacy['bridgeVersion']
        result = report(legacy)
        self.assertEqual(result['composeServices'], ['unknown'])
        self.assertEqual(result['bridgeVersions'], ['unknown'])
