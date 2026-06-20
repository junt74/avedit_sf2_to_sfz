#!/usr/bin/env python3
"""Extract loop-aware sample WAV files from a SoundFont 2 (.sf2) file."""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path


SHDR_STRUCT = struct.Struct("<20sIIIIIBbHH")
INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]+')
SAMPLE_TYPE_NAMES = {
    1: "mono",
    2: "right",
    4: "left",
    8: "linked",
    0x8001: "rom_mono",
    0x8002: "rom_right",
    0x8004: "rom_left",
    0x8008: "rom_linked",
}


class Sf2Error(Exception):
    """Raised when an SF2 file cannot be parsed or extracted."""


@dataclass(frozen=True)
class RiffChunk:
    chunk_id: bytes
    chunk_type: bytes | None
    data_start: int
    data_size: int
    children: tuple["RiffChunk", ...] = ()

    @property
    def data_end(self) -> int:
        return self.data_start + self.data_size


@dataclass(frozen=True)
class Sf2SampleHeader:
    name: str
    start: int
    end: int
    start_loop: int
    end_loop: int
    sample_rate: int
    original_pitch: int
    pitch_correction: int
    sample_link: int
    sample_type: int

    @property
    def length(self) -> int:
        return self.end - self.start

    def has_valid_loop(self) -> bool:
        return (
            self.start <= self.start_loop < self.end
            and self.start < self.end_loop <= self.end
            and self.end_loop > self.start_loop
        )

    def relative_loop_start(self) -> int | None:
        if not self.has_valid_loop():
            return None
        return self.start_loop - self.start

    def relative_loop_end(self) -> int | None:
        if not self.has_valid_loop():
            return None
        return self.end_loop - self.start


@dataclass(frozen=True)
class ExtractedSample:
    header: Sf2SampleHeader
    wav_path: Path
    json_path: Path | None
    relative_sample_path: str
    has_loop: bool
    loop_start: int | None
    loop_end: int | None


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract internal sample WAVs, loop metadata, and an SFZ from an SF2 file."
    )
    parser.add_argument("input_sf2", type=Path, help="Path to the input .sf2 file")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        required=True,
        help="Output directory",
    )
    parser.add_argument(
        "--write-json",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Write one JSON metadata file per WAV (default: on)",
    )
    parser.add_argument(
        "--write-sfz",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Write extracted.sfz (default: on)",
    )
    parser.add_argument(
        "--write-smpl",
        action="store_true",
        help="Embed a WAV smpl chunk for loop metadata (default: off)",
    )
    parser.add_argument(
        "--sanitize-names",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Replace characters that are unsafe in filenames (default: on)",
    )
    parser.add_argument(
        "--skip-empty",
        action="store_true",
        help="Skip zero-length samples and samples containing only zero bytes",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print skipped samples and chunk summary information",
    )
    return parser.parse_args(argv)


def parse_riff(data: bytes) -> RiffChunk:
    if len(data) < 12:
        raise Sf2Error("File is too small to be a RIFF/SF2 file")
    chunk_id = data[0:4]
    if chunk_id != b"RIFF":
        raise Sf2Error("File does not start with a RIFF chunk")
    size = struct.unpack_from("<I", data, 4)[0]
    if 8 + size > len(data):
        raise Sf2Error("RIFF chunk size extends beyond the file")
    chunk_type = data[8:12]
    if chunk_type != b"sfbk":
        raise Sf2Error(f"RIFF type is {chunk_type!r}, expected b'sfbk'")
    data_start = 12
    data_size = size - 4
    return RiffChunk(
        chunk_id=chunk_id,
        chunk_type=chunk_type,
        data_start=data_start,
        data_size=data_size,
        children=tuple(parse_chunk_list(data, data_start, data_start + data_size)),
    )


def parse_chunk_list(data: bytes, start: int, end: int) -> list[RiffChunk]:
    chunks: list[RiffChunk] = []
    offset = start
    while offset + 8 <= end:
        chunk_id = data[offset : offset + 4]
        size = struct.unpack_from("<I", data, offset + 4)[0]
        data_start = offset + 8
        data_end = data_start + size
        if data_end > end:
            raise Sf2Error(
                f"Chunk {chunk_id!r} at offset {offset} extends beyond its parent"
            )

        chunk_type = None
        children: tuple[RiffChunk, ...] = ()
        if chunk_id in {b"RIFF", b"LIST"}:
            if size < 4:
                raise Sf2Error(f"Chunk {chunk_id!r} at offset {offset} has no type")
            chunk_type = data[data_start : data_start + 4]
            child_start = data_start + 4
            children = tuple(parse_chunk_list(data, child_start, data_end))

        chunks.append(
            RiffChunk(
                chunk_id=chunk_id,
                chunk_type=chunk_type,
                data_start=data_start,
                data_size=size,
                children=children,
            )
        )
        offset = data_end + (size % 2)
    return chunks


def find_list(root: RiffChunk, chunk_type: bytes) -> RiffChunk | None:
    for child in root.children:
        if child.chunk_id == b"LIST" and child.chunk_type == chunk_type:
            return child
    return None


def find_child(parent: RiffChunk, chunk_id: bytes) -> RiffChunk | None:
    for child in parent.children:
        if child.chunk_id == chunk_id:
            return child
    return None


def decode_sample_name(raw_name: bytes) -> str:
    name = raw_name.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()
    return name or "unnamed"


def parse_shdr(shdr_data: bytes) -> list[Sf2SampleHeader]:
    if len(shdr_data) % SHDR_STRUCT.size != 0:
        raise Sf2Error(
            f"shdr chunk size {len(shdr_data)} is not a multiple of {SHDR_STRUCT.size}"
        )

    headers: list[Sf2SampleHeader] = []
    for offset in range(0, len(shdr_data), SHDR_STRUCT.size):
        (
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
        ) = SHDR_STRUCT.unpack_from(shdr_data, offset)
        name = decode_sample_name(raw_name)
        if name == "EOS":
            break
        headers.append(
            Sf2SampleHeader(
                name=name,
                start=start,
                end=end,
                start_loop=start_loop,
                end_loop=end_loop,
                sample_rate=sample_rate,
                original_pitch=original_pitch,
                pitch_correction=pitch_correction,
                sample_link=sample_link,
                sample_type=sample_type,
            )
        )
    return headers


def sanitize_filename(name: str, enabled: bool) -> str:
    cleaned = name.strip()
    if enabled:
        cleaned = INVALID_FILENAME_CHARS.sub("_", cleaned)
        cleaned = "".join("_" if ord(ch) < 32 else ch for ch in cleaned)
        cleaned = cleaned.rstrip(" .")
    return cleaned or "unnamed"


def unique_stem(base_name: str, used_names: set[str]) -> str:
    if base_name not in used_names:
        used_names.add(base_name)
        return base_name

    counter = 2
    while True:
        candidate = f"{base_name}_{counter:03d}"
        if candidate not in used_names:
            used_names.add(candidate)
            return candidate
        counter += 1


def is_all_silence(sample_bytes: bytes) -> bool:
    return not sample_bytes or all(byte == 0 for byte in sample_bytes)


def validate_sample_header(header: Sf2SampleHeader, smpl_frame_count: int) -> str | None:
    if header.sample_rate == 0:
        return "sample rate is 0"
    if header.end <= header.start:
        return f"invalid range start={header.start}, end={header.end}"
    if header.start < 0 or header.end > smpl_frame_count:
        return (
            f"sample range start={header.start}, end={header.end} exceeds "
            f"smpl frame count {smpl_frame_count}"
        )
    return None


def write_wav(
    path: Path,
    sample_bytes: bytes,
    sample_rate: int,
    loop_start: int | None = None,
    loop_end: int | None = None,
    root_key: int = 60,
    write_smpl: bool = False,
) -> None:
    fmt_payload = struct.pack("<HHIIHH", 1, 1, sample_rate, sample_rate * 2, 2, 16)
    chunks = [(b"fmt ", fmt_payload)]

    if write_smpl and loop_start is not None and loop_end is not None:
        chunks.append((b"smpl", make_smpl_chunk(sample_rate, loop_start, loop_end, root_key)))

    chunks.append((b"data", sample_bytes))
    riff_size = 4
    for _, payload in chunks:
        riff_size += 8 + len(payload) + (len(payload) % 2)

    with path.open("wb") as handle:
        handle.write(b"RIFF")
        handle.write(struct.pack("<I", riff_size))
        handle.write(b"WAVE")
        for chunk_id, payload in chunks:
            handle.write(chunk_id)
            handle.write(struct.pack("<I", len(payload)))
            handle.write(payload)
            if len(payload) % 2:
                handle.write(b"\x00")


def make_smpl_chunk(
    sample_rate: int,
    loop_start: int,
    loop_end: int,
    root_key: int,
) -> bytes:
    sample_period_ns = round(1_000_000_000 / sample_rate)
    header = struct.pack(
        "<9I",
        0,  # manufacturer
        0,  # product
        sample_period_ns,
        root_key,
        0,  # MIDI pitch fraction
        0,  # SMPTE format
        0,  # SMPTE offset
        1,  # sample loops
        0,  # sampler data
    )
    loop = struct.pack(
        "<6I",
        0,  # cue point ID
        0,  # forward loop
        loop_start,
        max(loop_start, loop_end - 1),
        0,  # fraction
        0,  # infinite playback
    )
    return header + loop


def make_metadata(
    header: Sf2SampleHeader,
    relative_sample_path: str,
    has_loop: bool,
    loop_start: int | None,
    loop_end: int | None,
) -> dict[str, object]:
    return {
        "sample_name": header.name,
        "file": relative_sample_path,
        "sample_rate": header.sample_rate,
        "channels": 1,
        "bits_per_sample": 16,
        "length_samples": header.length,
        "root_key": header.original_pitch,
        "pitch_correction_cents": header.pitch_correction,
        "has_loop": has_loop,
        "loop_mode": "forward" if has_loop else "none",
        "loop_start": loop_start,
        "loop_end": loop_end,
        "loop_start_absolute": header.start_loop if has_loop else None,
        "loop_end_absolute": header.end_loop if has_loop else None,
        "sf2_sample_type": header.sample_type,
        "sf2_sample_type_name": SAMPLE_TYPE_NAMES.get(header.sample_type, "unknown"),
        "sample_link": header.sample_link,
        "sf2_start": header.start,
        "sf2_end": header.end,
    }


def write_json(path: Path, metadata: dict[str, object]) -> None:
    path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def sfz_quote_path(path: str) -> str:
    return path.replace("\\", "/")


def write_sfz(path: Path, samples: list[ExtractedSample]) -> None:
    lines = [
        "// Generated by sf2_extract_samples.py",
        "<group>",
        "ampeg_release=0.01",
        "",
    ]
    for sample in samples:
        header = sample.header
        lines.extend(
            [
                "<region>",
                f"sample={sfz_quote_path(sample.relative_sample_path)}",
                f"pitch_keycenter={header.original_pitch}",
                f"tune={header.pitch_correction}",
            ]
        )
        if sample.has_loop:
            lines.extend(
                [
                    "loop_mode=loop_continuous",
                    f"loop_start={sample.loop_start}",
                    f"loop_end={sample.loop_end}",
                ]
            )
        else:
            lines.append("loop_mode=no_loop")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def extract_samples(
    input_sf2: Path,
    output_dir: Path,
    *,
    write_json_files: bool = True,
    write_sfz_file: bool = True,
    write_smpl: bool = False,
    sanitize_names: bool = True,
    skip_empty: bool = False,
    verbose: bool = False,
) -> list[ExtractedSample]:
    data = input_sf2.read_bytes()
    root = parse_riff(data)
    sdta = find_list(root, b"sdta")
    pdta = find_list(root, b"pdta")
    if sdta is None:
        raise Sf2Error("LIST sdta was not found")
    if pdta is None:
        raise Sf2Error("LIST pdta was not found")

    smpl_chunk = find_child(sdta, b"smpl")
    shdr_chunk = find_child(pdta, b"shdr")
    if smpl_chunk is None:
        raise Sf2Error("sdta/smpl chunk was not found")
    if shdr_chunk is None:
        raise Sf2Error("pdta/shdr chunk was not found")

    smpl_data = data[smpl_chunk.data_start : smpl_chunk.data_end]
    shdr_data = data[shdr_chunk.data_start : shdr_chunk.data_end]
    headers = parse_shdr(shdr_data)
    smpl_frame_count = len(smpl_data) // 2

    if verbose:
        print(
            f"Found smpl={len(smpl_data)} bytes ({smpl_frame_count} frames), "
            f"shdr={len(headers)} sample headers",
            file=sys.stderr,
        )

    samples_dir = output_dir / "samples"
    samples_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[ExtractedSample] = []
    used_names: set[str] = set()

    for index, header in enumerate(headers):
        skip_reason = validate_sample_header(header, smpl_frame_count)
        if skip_reason:
            if verbose:
                print(f"Skipping {header.name!r} ({index}): {skip_reason}", file=sys.stderr)
            continue

        byte_start = header.start * 2
        byte_end = header.end * 2
        sample_bytes = smpl_data[byte_start:byte_end]
        if skip_empty and is_all_silence(sample_bytes):
            if verbose:
                print(f"Skipping {header.name!r} ({index}): empty/silent", file=sys.stderr)
            continue

        stem = unique_stem(sanitize_filename(header.name, sanitize_names), used_names)
        wav_path = samples_dir / f"{stem}.wav"
        json_path = samples_dir / f"{stem}.json" if write_json_files else None
        relative_sample_path = f"samples/{wav_path.name}"

        has_loop = header.has_valid_loop()
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
        if json_path is not None:
            write_json(
                json_path,
                make_metadata(header, relative_sample_path, has_loop, loop_start, loop_end),
            )

        extracted.append(
            ExtractedSample(
                header=header,
                wav_path=wav_path,
                json_path=json_path,
                relative_sample_path=relative_sample_path,
                has_loop=has_loop,
                loop_start=loop_start,
                loop_end=loop_end,
            )
        )

    if write_sfz_file:
        output_dir.mkdir(parents=True, exist_ok=True)
        write_sfz(output_dir / "extracted.sfz", extracted)

    return extracted


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        extracted = extract_samples(
            args.input_sf2,
            args.output,
            write_json_files=args.write_json,
            write_sfz_file=args.write_sfz,
            write_smpl=args.write_smpl,
            sanitize_names=args.sanitize_names,
            skip_empty=args.skip_empty,
            verbose=args.verbose,
        )
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Sf2Error as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    looped = sum(1 for sample in extracted if sample.has_loop)
    print(
        f"Extracted {len(extracted)} samples ({looped} with loops) to {args.output}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
