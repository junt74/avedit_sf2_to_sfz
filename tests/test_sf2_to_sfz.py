import unittest

from sf2_extract_samples import Sf2SampleHeader
from sf2_to_sfz import (
    GEN_COARSE_TUNE,
    GEN_FINE_TUNE,
    GEN_KEY_RANGE,
    GEN_PAN,
    GEN_SAMPLE_MODES,
    Generator,
    InstrumentHeader,
    PresetHeader,
    Region,
    SampleAsset,
    merge_generators,
    range_amount,
    region_opcodes,
    region_ranges_are_valid,
)


class Sf2ToSfzTests(unittest.TestCase):
    def test_key_ranges_are_intersected_when_generators_merge(self):
        merged = merge_generators(
            [Generator(GEN_KEY_RANGE, 20 | (80 << 8))],
            [Generator(GEN_KEY_RANGE, 40 | (100 << 8))],
        )

        self.assertEqual(range_amount(merged[GEN_KEY_RANGE]), (40, 80))
        self.assertTrue(region_ranges_are_valid(merged))

    def test_empty_key_range_is_invalid(self):
        merged = merge_generators(
            [Generator(GEN_KEY_RANGE, 90 | (100 << 8))],
            [Generator(GEN_KEY_RANGE, 20 | (40 << 8))],
        )

        self.assertFalse(region_ranges_are_valid(merged))

    def test_region_opcodes_include_loop_and_tuning(self):
        header = Sf2SampleHeader(
            name="Looped",
            start=1000,
            end=2000,
            start_loop=1200,
            end_loop=1800,
            sample_rate=44100,
            original_pitch=60,
            pitch_correction=-2,
            sample_link=0,
            sample_type=1,
        )
        sample = SampleAsset(
            header=header,
            relative_path="samples/Looped.wav",
            loop_start=200,
            loop_end=800,
        )
        region = Region(
            preset=PresetHeader("Preset", 0, 0, 0),
            instrument=InstrumentHeader("Inst", 0),
            sample_index=0,
            sample=sample,
            generators={
                GEN_KEY_RANGE: 10 | (30 << 8),
                GEN_COARSE_TUNE: 1,
                GEN_FINE_TUNE: 5,
                GEN_PAN: 250,
                GEN_SAMPLE_MODES: 1,
            },
        )

        opcodes = dict(region_opcodes(region, "sf2"))

        self.assertEqual(opcodes["sample"], "samples/Looped.wav")
        self.assertEqual(opcodes["lokey"], "10")
        self.assertEqual(opcodes["hikey"], "30")
        self.assertEqual(opcodes["pitch_keycenter"], "60")
        self.assertEqual(opcodes["tune"], "103")
        self.assertEqual(opcodes["pan"], "50")
        self.assertEqual(opcodes["loop_mode"], "loop_continuous")
        self.assertEqual(opcodes["loop_start"], "200")
        self.assertEqual(opcodes["loop_end"], "800")


if __name__ == "__main__":
    unittest.main()
