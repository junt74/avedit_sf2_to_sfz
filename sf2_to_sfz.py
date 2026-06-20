#!/usr/bin/env python3
"""Convert a SoundFont 2 (.sf2) file to extracted WAVs and preset SFZ files."""

from __future__ import annotations

import argparse
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

from sf2_extract_samples import (
    SHDR_STRUCT,
    Sf2Error,
    Sf2SampleHeader,
    find_child,
    find_list,
    parse_riff,
    parse_shdr,
    sanitize_filename,
    unique_stem,
    validate_sample_header,
    write_wav,
)


PHDR_STRUCT = struct.Struct("<20sHHHIII")
INST_STRUCT = struct.Struct("<20sH")
BAG_STRUCT = struct.Struct("<HH")
GEN_STRUCT = struct.Struct("<HH")

GEN_START_ADDRS_OFFSET = 0
GEN_END_ADDRS_OFFSET = 1
GEN_STARTLOOP_ADDRS_OFFSET = 2
GEN_ENDLOOP_ADDRS_OFFSET = 3
GEN_START_ADDRS_COARSE_OFFSET = 4
GEN_MOD_LFO_TO_PITCH = 5
GEN_VIB_LFO_TO_PITCH = 6
GEN_MOD_ENV_TO_PITCH = 7
GEN_INITIAL_FILTER_FC = 8
GEN_INITIAL_FILTER_Q = 9
GEN_MOD_LFO_TO_FILTER_FC = 10
GEN_MOD_ENV_TO_FILTER_FC = 11
GEN_END_ADDRS_COARSE_OFFSET = 12
GEN_MOD_LFO_TO_VOLUME = 13
GEN_CHORUS_EFFECTS_SEND = 15
GEN_REVERB_EFFECTS_SEND = 16
GEN_PAN = 17
GEN_DELAY_MOD_LFO = 21
GEN_FREQ_MOD_LFO = 22
GEN_DELAY_VIB_LFO = 23
GEN_FREQ_VIB_LFO = 24
GEN_DELAY_MOD_ENV = 25
GEN_ATTACK_MOD_ENV = 26
GEN_HOLD_MOD_ENV = 27
GEN_DECAY_MOD_ENV = 28
GEN_SUSTAIN_MOD_ENV = 29
GEN_RELEASE_MOD_ENV = 30
GEN_KEYNUM_TO_MOD_ENV_HOLD = 31
GEN_KEYNUM_TO_MOD_ENV_DECAY = 32
GEN_DELAY_VOL_ENV = 33
GEN_ATTACK_VOL_ENV = 34
GEN_HOLD_VOL_ENV = 35
GEN_DECAY_VOL_ENV = 36
GEN_SUSTAIN_VOL_ENV = 37
GEN_RELEASE_VOL_ENV = 38
GEN_KEYNUM_TO_VOL_ENV_HOLD = 39
GEN_KEYNUM_TO_VOL_ENV_DECAY = 40
GEN_INSTRUMENT = 41
GEN_KEY_RANGE = 43
GEN_VEL_RANGE = 44
GEN_STARTLOOP_ADDRS_COARSE_OFFSET = 45
GEN_KEYNUM = 46
GEN_VELOCITY = 47
GEN_INITIAL_ATTENUATION = 48
GEN_ENDLOOP_ADDRS_COARSE_OFFSET = 50
GEN_COARSE_TUNE = 51
GEN_FINE_TUNE = 52
GEN_SAMPLE_ID = 53
GEN_SAMPLE_MODES = 54
GEN_SCALE_TUNING = 56
GEN_EXCLUSIVE_CLASS = 57
GEN_OVERRIDING_ROOT_KEY = 58

GENERATORS_THAT_ADD = {
    GEN_START_ADDRS_OFFSET,
    GEN_END_ADDRS_OFFSET,
    GEN_STARTLOOP_ADDRS_OFFSET,
    GEN_ENDLOOP_ADDRS_OFFSET,
    GEN_START_ADDRS_COARSE_OFFSET,
    GEN_END_ADDRS_COARSE_OFFSET,
    GEN_STARTLOOP_ADDRS_COARSE_OFFSET,
    GEN_ENDLOOP_ADDRS_COARSE_OFFSET,
    GEN_MOD_LFO_TO_PITCH,
    GEN_VIB_LFO_TO_PITCH,
    GEN_MOD_ENV_TO_PITCH,
    GEN_MOD_LFO_TO_FILTER_FC,
    GEN_MOD_ENV_TO_FILTER_FC,
    GEN_MOD_LFO_TO_VOLUME,
    GEN_CHORUS_EFFECTS_SEND,
    GEN_REVERB_EFFECTS_SEND,
    GEN_PAN,
    GEN_DELAY_MOD_LFO,
    GEN_FREQ_MOD_LFO,
    GEN_DELAY_VIB_LFO,
    GEN_FREQ_VIB_LFO,
    GEN_DELAY_MOD_ENV,
    GEN_ATTACK_MOD_ENV,
    GEN_HOLD_MOD_ENV,
    GEN_DECAY_MOD_ENV,
    GEN_SUSTAIN_MOD_ENV,
    GEN_RELEASE_MOD_ENV,
    GEN_KEYNUM_TO_MOD_ENV_HOLD,
    GEN_KEYNUM_TO_MOD_ENV_DECAY,
    GEN_DELAY_VOL_ENV,
    GEN_ATTACK_VOL_ENV,
    GEN_HOLD_VOL_ENV,
    GEN_DECAY_VOL_ENV,
    GEN_SUSTAIN_VOL_ENV,
    GEN_RELEASE_VOL_ENV,
    GEN_KEYNUM_TO_VOL_ENV_HOLD,
    GEN_KEYNUM_TO_VOL_ENV_DECAY,
    GEN_INITIAL_ATTENUATION,
    GEN_COARSE_TUNE,
    GEN_FINE_TUNE,
}


@dataclass(frozen=True)
class PresetHeader:
    name: str
    preset: int
    bank: int
    bag_index: int


@dataclass(frozen=True)
class InstrumentHeader:
    name: str
    bag_index: int


@dataclass(frozen=True)
class Bag:
    gen_index: int
    mod_index: int


@dataclass(frozen=True)
class Generator:
    op: int
    amount: int

    @property
    def signed_amount(self) -> int:
        return self.amount - 0x10000 if self.amount >= 0x8000 else self.amount


@dataclass(frozen=True)
class SampleAsset:
    header: Sf2SampleHeader
    relative_path: str
    loop_start: int | None
    loop_end: int | None


@dataclass(frozen=True)
class Region:
    preset: PresetHeader
    instrument: InstrumentHeader
    sample_index: int
    sample: SampleAsset
    generators: dict[int, int]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert an SF2 file to WAV samples plus preset SFZ files. "
            "This maps common SF2 generators, not every modulation feature."
        )
    )
    parser.add_argument("input_sf2", type=Path, help="Path to the input .sf2 file")
    parser.add_argument("-o", "--output", type=Path, required=True, help="Output directory")
    parser.add_argument(
        "--preset",
        action="append",
        default=[],
        metavar="BANK:PRESET",
        help="Only export a preset, for example 0:0. Can be used more than once.",
    )
    parser.add_argument(
        "--loop-policy",
        choices=("sf2", "sample", "none"),
        default="sf2",
        help=(
            "Loop regions when sampleModes requests it, whenever the sample has a "
            "valid loop, or never (default: sf2)."
        ),
    )
    parser.add_argument(
        "--write-smpl",
        action="store_true",
        help="Embed smpl loop chunks in extracted WAV files.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print conversion details and skipped regions.",
    )
    return parser.parse_args(argv)


def decode_fixed_name(raw_name: bytes) -> str:
    return raw_name.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()


def require_chunk(root, list_type: bytes, chunk_id: bytes, data: bytes) -> bytes:
    parent = find_list(root, list_type)
    if parent is None:
        raise Sf2Error(f"LIST {list_type.decode('ascii')} was not found")
    chunk = find_child(parent, chunk_id)
    if chunk is None:
        raise Sf2Error(
            f"{list_type.decode('ascii')}/{chunk_id.decode('ascii')} chunk was not found"
        )
    return data[chunk.data_start : chunk.data_end]


def parse_phdr(data: bytes) -> list[PresetHeader]:
    if len(data) % PHDR_STRUCT.size != 0:
        raise Sf2Error("phdr chunk size is invalid")
    records: list[PresetHeader] = []
    for offset in range(0, len(data), PHDR_STRUCT.size):
        raw_name, preset, bank, bag_index, _, _, _ = PHDR_STRUCT.unpack_from(data, offset)
        records.append(PresetHeader(decode_fixed_name(raw_name), preset, bank, bag_index))
    return records


def parse_inst(data: bytes) -> list[InstrumentHeader]:
    if len(data) % INST_STRUCT.size != 0:
        raise Sf2Error("inst chunk size is invalid")
    records: list[InstrumentHeader] = []
    for offset in range(0, len(data), INST_STRUCT.size):
        raw_name, bag_index = INST_STRUCT.unpack_from(data, offset)
        records.append(InstrumentHeader(decode_fixed_name(raw_name), bag_index))
    return records


def parse_bags(data: bytes, name: str) -> list[Bag]:
    if len(data) % BAG_STRUCT.size != 0:
        raise Sf2Error(f"{name} chunk size is invalid")
    return [
        Bag(*BAG_STRUCT.unpack_from(data, offset))
        for offset in range(0, len(data), BAG_STRUCT.size)
    ]


def parse_gens(data: bytes, name: str) -> list[Generator]:
    if len(data) % GEN_STRUCT.size != 0:
        raise Sf2Error(f"{name} chunk size is invalid")
    return [
        Generator(*GEN_STRUCT.unpack_from(data, offset))
        for offset in range(0, len(data), GEN_STRUCT.size)
    ]


def zone_generators(bags: list[Bag], gens: list[Generator], zone_index: int) -> list[Generator]:
    start = bags[zone_index].gen_index
    end = bags[zone_index + 1].gen_index
    return gens[start:end]


def has_generator(gens: list[Generator], op: int) -> bool:
    return any(gen.op == op for gen in gens)


def generator_amount(gens: list[Generator], op: int) -> int | None:
    for gen in gens:
        if gen.op == op:
            return gen.amount
    return None


def signed_generator_amount(gens: list[Generator], op: int) -> int | None:
    for gen in gens:
        if gen.op == op:
            return gen.signed_amount
    return None


def merge_generators(*zones: list[Generator]) -> dict[int, int]:
    merged: dict[int, int] = {}
    for gens in zones:
        for gen in gens:
            value = gen.signed_amount
            if gen.op in {GEN_KEY_RANGE, GEN_VEL_RANGE}:
                low, high = range_amount(gen.amount)
                if gen.op in merged:
                    old_low, old_high = range_amount(merged[gen.op])
                    low = max(low, old_low)
                    high = min(high, old_high)
                merged[gen.op] = low | (high << 8)
            elif gen.op in GENERATORS_THAT_ADD:
                merged[gen.op] = merged.get(gen.op, 0) + value
            else:
                merged[gen.op] = gen.amount
    return merged


def range_amount(amount: int) -> tuple[int, int]:
    return amount & 0xFF, (amount >> 8) & 0xFF


def signed_amount(amount: int) -> int:
    return amount - 0x10000 if amount >= 0x8000 else amount


def timecents_to_seconds(value: int) -> float:
    if value <= -32768:
        return 0.0
    return 2 ** (value / 1200)


def cents_to_hz(value: int) -> float:
    return 8.176 * (2 ** (value / 1200))


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def format_float(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text or "0"


def sfz_safe_stem(name: str, used_names: set[str]) -> str:
    stem = sanitize_filename(name, True)
    stem = re.sub(r"\s+", "_", stem)
    return unique_stem(stem, used_names)


def sfz_path(path: str) -> str:
    return path.replace("\\", "/")


def load_sf2(input_sf2: Path):
    data = input_sf2.read_bytes()
    root = parse_riff(data)
    chunks = {
        "smpl": require_chunk(root, b"sdta", b"smpl", data),
        "phdr": require_chunk(root, b"pdta", b"phdr", data),
        "pbag": require_chunk(root, b"pdta", b"pbag", data),
        "pgen": require_chunk(root, b"pdta", b"pgen", data),
        "inst": require_chunk(root, b"pdta", b"inst", data),
        "ibag": require_chunk(root, b"pdta", b"ibag", data),
        "igen": require_chunk(root, b"pdta", b"igen", data),
        "shdr": require_chunk(root, b"pdta", b"shdr", data),
    }
    return chunks


def write_sample_assets(
    output_dir: Path,
    sample_headers: list[Sf2SampleHeader],
    smpl_data: bytes,
    *,
    write_smpl: bool,
    verbose: bool,
) -> dict[int, SampleAsset]:
    sample_dir = output_dir / "samples"
    sample_dir.mkdir(parents=True, exist_ok=True)
    smpl_frame_count = len(smpl_data) // 2
    used_names: set[str] = set()
    assets: dict[int, SampleAsset] = {}

    for index, header in enumerate(sample_headers):
        skip_reason = validate_sample_header(header, smpl_frame_count)
        if skip_reason:
            if verbose:
                print(f"Skipping sample {index} {header.name!r}: {skip_reason}", file=sys.stderr)
            continue

        stem = sfz_safe_stem(header.name, used_names)
        wav_path = sample_dir / f"{stem}.wav"
        relative_path = f"samples/{wav_path.name}"
        sample_bytes = smpl_data[header.start * 2 : header.end * 2]
        loop_start = header.relative_loop_start()
        loop_end = header.relative_loop_end()

        write_wav(
            wav_path,
            sample_bytes,
            header.sample_rate,
            loop_start=loop_start,
            loop_end=loop_end,
            root_key=header.original_pitch,
            write_smpl=write_smpl,
        )
        assets[index] = SampleAsset(
            header=header,
            relative_path=relative_path,
            loop_start=loop_start,
            loop_end=loop_end,
        )
    return assets


def build_regions(
    presets: list[PresetHeader],
    pbag: list[Bag],
    pgen: list[Generator],
    instruments: list[InstrumentHeader],
    ibag: list[Bag],
    igen: list[Generator],
    samples: dict[int, SampleAsset],
    *,
    verbose: bool,
) -> dict[tuple[int, int, str], list[Region]]:
    preset_regions: dict[tuple[int, int, str], list[Region]] = {}
    real_presets = presets[:-1] if presets and presets[-1].name == "EOP" else presets
    real_instruments = instruments[:-1] if instruments and instruments[-1].name == "EOI" else instruments

    for preset_index, preset in enumerate(real_presets):
        next_preset = presets[preset_index + 1]
        preset_zone_indexes = range(preset.bag_index, next_preset.bag_index)
        preset_global: list[Generator] = []
        key = (preset.bank, preset.preset, preset.name)
        preset_regions[key] = []

        for zone_index in preset_zone_indexes:
            zone_gens = zone_generators(pbag, pgen, zone_index)
            instrument_index = generator_amount(zone_gens, GEN_INSTRUMENT)
            if instrument_index is None:
                preset_global = zone_gens
                continue
            if instrument_index >= len(real_instruments):
                if verbose:
                    print(
                        f"Skipping preset zone {preset.name!r}: invalid instrument "
                        f"{instrument_index}",
                        file=sys.stderr,
                    )
                continue

            instrument = real_instruments[instrument_index]
            next_instrument = instruments[instrument_index + 1]
            instrument_zone_indexes = range(instrument.bag_index, next_instrument.bag_index)
            instrument_global: list[Generator] = []

            for inst_zone_index in instrument_zone_indexes:
                inst_gens = zone_generators(ibag, igen, inst_zone_index)
                sample_index = generator_amount(inst_gens, GEN_SAMPLE_ID)
                if sample_index is None:
                    instrument_global = inst_gens
                    continue
                sample = samples.get(sample_index)
                if sample is None:
                    if verbose:
                        print(
                            f"Skipping region {preset.name!r}/{instrument.name!r}: "
                            f"missing sample {sample_index}",
                            file=sys.stderr,
                        )
                    continue

                merged = merge_generators(
                    preset_global,
                    zone_gens,
                    instrument_global,
                    inst_gens,
                )
                if not region_ranges_are_valid(merged):
                    if verbose:
                        print(
                            f"Skipping region {preset.name!r}/{instrument.name!r}: "
                            "empty key or velocity range",
                            file=sys.stderr,
                        )
                    continue
                preset_regions[key].append(
                    Region(
                        preset=preset,
                        instrument=instrument,
                        sample_index=sample_index,
                        sample=sample,
                        generators=merged,
                    )
                )
    return preset_regions


def region_ranges_are_valid(gens: dict[int, int]) -> bool:
    for op in (GEN_KEY_RANGE, GEN_VEL_RANGE):
        if op in gens:
            low, high = range_amount(gens[op])
            if low > high:
                return False
    return True


def region_opcodes(region: Region, loop_policy: str) -> list[tuple[str, str]]:
    gens = region.generators
    sample = region.sample
    header = sample.header
    opcodes: list[tuple[str, str]] = [("sample", sfz_path(sample.relative_path))]
    add_sample_offset_opcodes(opcodes, gens, header.length)

    key_range = gens.get(GEN_KEY_RANGE)
    if key_range is not None:
        lokey, hikey = range_amount(key_range)
        opcodes.extend([("lokey", str(lokey)), ("hikey", str(hikey))])

    vel_range = gens.get(GEN_VEL_RANGE)
    if vel_range is not None:
        lovel, hivel = range_amount(vel_range)
        opcodes.extend([("lovel", str(lovel)), ("hivel", str(hivel))])

    if GEN_KEYNUM in gens:
        opcodes.append(("key", str(signed_amount(gens[GEN_KEYNUM]))))

    root_key = gens.get(GEN_OVERRIDING_ROOT_KEY)
    if root_key is None or root_key == 255:
        root_key = None if header.original_pitch == 255 else header.original_pitch
    if root_key is not None:
        opcodes.append(("pitch_keycenter", str(root_key)))

    tune = (
        signed_amount(gens.get(GEN_COARSE_TUNE, 0)) * 100
        + signed_amount(gens.get(GEN_FINE_TUNE, 0))
        + header.pitch_correction
    )
    if tune:
        opcodes.append(("tune", str(tune)))

    if GEN_SCALE_TUNING in gens and signed_amount(gens[GEN_SCALE_TUNING]) != 100:
        opcodes.append(("pitch_keytrack", str(signed_amount(gens[GEN_SCALE_TUNING]))))

    if GEN_INITIAL_ATTENUATION in gens:
        volume = -signed_amount(gens[GEN_INITIAL_ATTENUATION]) / 10
        opcodes.append(("volume", format_float(volume)))

    if GEN_PAN in gens:
        pan = clamp(signed_amount(gens[GEN_PAN]) / 5, -100, 100)
        opcodes.append(("pan", format_float(pan)))

    if GEN_INITIAL_FILTER_FC in gens and 1500 <= signed_amount(gens[GEN_INITIAL_FILTER_FC]) <= 13500:
        cutoff = cents_to_hz(signed_amount(gens[GEN_INITIAL_FILTER_FC]))
        opcodes.append(("cutoff", format_float(cutoff)))

    if GEN_INITIAL_FILTER_Q in gens:
        opcodes.append(("resonance", format_float(signed_amount(gens[GEN_INITIAL_FILTER_Q]) / 10)))

    if GEN_EXCLUSIVE_CLASS in gens and gens[GEN_EXCLUSIVE_CLASS]:
        group = str(gens[GEN_EXCLUSIVE_CLASS])
        opcodes.extend([("group", group), ("off_by", group)])

    add_envelope_opcodes(opcodes, gens)
    add_loop_opcodes(opcodes, gens, sample, loop_policy)
    return opcodes


def add_sample_offset_opcodes(
    opcodes: list[tuple[str, str]],
    gens: dict[int, int],
    sample_length: int,
) -> None:
    start_offset = signed_amount(gens.get(GEN_START_ADDRS_OFFSET, 0))
    start_offset += signed_amount(gens.get(GEN_START_ADDRS_COARSE_OFFSET, 0)) * 32768
    end_offset = signed_amount(gens.get(GEN_END_ADDRS_OFFSET, 0))
    end_offset += signed_amount(gens.get(GEN_END_ADDRS_COARSE_OFFSET, 0)) * 32768

    if start_offset:
        opcodes.append(("offset", str(max(0, start_offset))))
    if end_offset:
        opcodes.append(("end", str(max(0, sample_length - 1 + end_offset))))


def add_envelope_opcodes(opcodes: list[tuple[str, str]], gens: dict[int, int]) -> None:
    env_map = [
        (GEN_DELAY_VOL_ENV, "ampeg_delay"),
        (GEN_ATTACK_VOL_ENV, "ampeg_attack"),
        (GEN_HOLD_VOL_ENV, "ampeg_hold"),
        (GEN_DECAY_VOL_ENV, "ampeg_decay"),
        (GEN_RELEASE_VOL_ENV, "ampeg_release"),
    ]
    for gen_op, sfz_op in env_map:
        if gen_op in gens:
            opcodes.append((sfz_op, format_float(timecents_to_seconds(signed_amount(gens[gen_op])))))

    if GEN_SUSTAIN_VOL_ENV in gens:
        sustain = clamp(100 - signed_amount(gens[GEN_SUSTAIN_VOL_ENV]) / 10, 0, 100)
        opcodes.append(("ampeg_sustain", format_float(sustain)))


def add_loop_opcodes(
    opcodes: list[tuple[str, str]],
    gens: dict[int, int],
    sample: SampleAsset,
    loop_policy: str,
) -> None:
    has_sample_loop = sample.loop_start is not None and sample.loop_end is not None
    sample_modes = gens.get(GEN_SAMPLE_MODES, 0)
    if loop_policy == "none":
        should_loop = False
    elif loop_policy == "sample":
        should_loop = has_sample_loop
    else:
        should_loop = has_sample_loop and bool(sample_modes & 0x1)

    if should_loop:
        loop_start = sample.loop_start or 0
        loop_end = sample.loop_end or 0
        loop_start += signed_amount(gens.get(GEN_STARTLOOP_ADDRS_OFFSET, 0))
        loop_start += signed_amount(gens.get(GEN_STARTLOOP_ADDRS_COARSE_OFFSET, 0)) * 32768
        loop_end += signed_amount(gens.get(GEN_ENDLOOP_ADDRS_OFFSET, 0))
        loop_end += signed_amount(gens.get(GEN_ENDLOOP_ADDRS_COARSE_OFFSET, 0)) * 32768
        opcodes.extend(
            [
                ("loop_mode", "loop_continuous"),
                ("loop_start", str(max(0, loop_start))),
                ("loop_end", str(max(0, loop_end))),
            ]
        )
    else:
        opcodes.append(("loop_mode", "no_loop"))


def write_preset_sfz(path: Path, preset: PresetHeader, regions: list[Region], loop_policy: str) -> None:
    lines = [
        f"// Generated by sf2_to_sfz.py",
        f"// Preset bank={preset.bank} program={preset.preset} name={preset.name}",
        "<control>",
        "default_path=../",
        "",
        "<group>",
        "",
    ]
    for region in regions:
        lines.append("<region>")
        for key, value in region_opcodes(region, loop_policy):
            lines.append(f"{key}={value}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_preset_filters(filters: list[str]) -> set[tuple[int, int]]:
    parsed: set[tuple[int, int]] = set()
    for value in filters:
        try:
            bank_text, preset_text = value.split(":", 1)
            parsed.add((int(bank_text), int(preset_text)))
        except ValueError as exc:
            raise Sf2Error(f"Invalid --preset value {value!r}; expected BANK:PRESET") from exc
    return parsed


def convert_sf2_to_sfz(
    input_sf2: Path,
    output_dir: Path,
    *,
    preset_filters: set[tuple[int, int]],
    loop_policy: str,
    write_smpl: bool,
    verbose: bool,
) -> tuple[int, int, int]:
    chunks = load_sf2(input_sf2)
    sample_headers = parse_shdr(chunks["shdr"])
    presets = parse_phdr(chunks["phdr"])
    instruments = parse_inst(chunks["inst"])
    pbag = parse_bags(chunks["pbag"], "pbag")
    ibag = parse_bags(chunks["ibag"], "ibag")
    pgen = parse_gens(chunks["pgen"], "pgen")
    igen = parse_gens(chunks["igen"], "igen")

    output_dir.mkdir(parents=True, exist_ok=True)
    samples = write_sample_assets(
        output_dir,
        sample_headers,
        chunks["smpl"],
        write_smpl=write_smpl,
        verbose=verbose,
    )
    regions_by_preset = build_regions(
        presets,
        pbag,
        pgen,
        instruments,
        ibag,
        igen,
        samples,
        verbose=verbose,
    )

    preset_dir = output_dir / "presets"
    preset_dir.mkdir(parents=True, exist_ok=True)
    used_names: set[str] = set()
    written_presets = 0
    written_regions = 0

    for (bank, program, name), regions in regions_by_preset.items():
        if preset_filters and (bank, program) not in preset_filters:
            continue
        if not regions:
            continue
        stem = sfz_safe_stem(f"{bank:03d}_{program:03d}_{name}", used_names)
        path = preset_dir / f"{stem}.sfz"
        write_preset_sfz(path, regions[0].preset, regions, loop_policy)
        written_presets += 1
        written_regions += len(regions)

    return len(samples), written_presets, written_regions


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        sample_count, preset_count, region_count = convert_sf2_to_sfz(
            args.input_sf2,
            args.output,
            preset_filters=parse_preset_filters(args.preset),
            loop_policy=args.loop_policy,
            write_smpl=args.write_smpl,
            verbose=args.verbose,
        )
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Sf2Error as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(
        f"Wrote {sample_count} samples, {preset_count} preset SFZ files, "
        f"and {region_count} regions to {args.output}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
