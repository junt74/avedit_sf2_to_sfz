import struct
import unittest

from sf2_extract_samples import (
    SHDR_STRUCT,
    Sf2Error,
    Sf2SampleHeader,
    parse_riff,
    parse_shdr,
    unique_stem,
)


def shdr_record(
    name,
    start=0,
    end=100,
    start_loop=20,
    end_loop=80,
    sample_rate=44100,
    original_pitch=60,
    pitch_correction=-3,
    sample_link=0,
    sample_type=1,
):
    raw_name = name.encode("ascii").ljust(20, b"\x00")
    return SHDR_STRUCT.pack(
        raw_name,
        start,
        end,
        start_loop,
        end_loop,
        sample_rate,
        original_pitch,
        pitch_correction,
        sample_link,
        sample_type,
    )


def riff_chunk(chunk_id, payload):
    padding = b"\x00" if len(payload) % 2 else b""
    return chunk_id + struct.pack("<I", len(payload)) + payload + padding


def list_chunk(chunk_type, payload):
    return riff_chunk(b"LIST", chunk_type + payload)


class Sf2ExtractTests(unittest.TestCase):
    def test_shdr_parse_stops_before_eos(self):
        shdr = shdr_record("Piano C4") + shdr_record("EOS")

        headers = parse_shdr(shdr)

        self.assertEqual(len(headers), 1)
        self.assertEqual(headers[0].name, "Piano C4")
        self.assertEqual(headers[0].sample_rate, 44100)
        self.assertEqual(headers[0].pitch_correction, -3)

    def test_shdr_size_must_match_record_size(self):
        with self.assertRaises(Sf2Error):
            parse_shdr(b"\x00")

    def test_loop_relative_conversion_and_validation(self):
        header = Sf2SampleHeader(
            name="Looped",
            start=1000,
            end=2000,
            start_loop=1200,
            end_loop=1800,
            sample_rate=32000,
            original_pitch=64,
            pitch_correction=0,
            sample_link=0,
            sample_type=1,
        )

        self.assertTrue(header.has_valid_loop())
        self.assertEqual(header.relative_loop_start(), 200)
        self.assertEqual(header.relative_loop_end(), 800)

    def test_invalid_loop_is_reported_as_no_loop(self):
        header = Sf2SampleHeader(
            name="OneShot",
            start=1000,
            end=2000,
            start_loop=2000,
            end_loop=2000,
            sample_rate=32000,
            original_pitch=64,
            pitch_correction=0,
            sample_link=0,
            sample_type=1,
        )

        self.assertFalse(header.has_valid_loop())
        self.assertIsNone(header.relative_loop_start())
        self.assertIsNone(header.relative_loop_end())

    def test_duplicate_filename_stems_are_numbered(self):
        used = set()

        self.assertEqual(unique_stem("Piano", used), "Piano")
        self.assertEqual(unique_stem("Piano", used), "Piano_002")
        self.assertEqual(unique_stem("Piano", used), "Piano_003")

    def test_riff_padding_does_not_hide_next_chunk(self):
        odd_payload = riff_chunk(b"JUNK", b"x")
        sdta = list_chunk(b"sdta", riff_chunk(b"smpl", b"\x00\x00"))
        pdta = list_chunk(b"pdta", riff_chunk(b"shdr", shdr_record("EOS")))
        payload = b"sfbk" + odd_payload + sdta + pdta
        data = b"RIFF" + struct.pack("<I", len(payload)) + payload

        root = parse_riff(data)

        self.assertEqual(
            [child.chunk_id for child in root.children], [b"JUNK", b"LIST", b"LIST"]
        )
        self.assertEqual(root.children[1].chunk_type, b"sdta")
        self.assertEqual(root.children[2].chunk_type, b"pdta")


if __name__ == "__main__":
    unittest.main()
