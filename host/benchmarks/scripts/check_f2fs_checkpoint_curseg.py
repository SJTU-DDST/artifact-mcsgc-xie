#!/usr/bin/env python3
"""Validate persisted F2FS curseg offsets in both checkpoint packs."""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import zlib
from typing import BinaryIO, Dict, List


F2FS_SUPER_MAGIC = 0xF2F52010
F2FS_SUPER_OFFSET = 1024
F2FS_BLOCK_SIZE = 4096
PERSISTENT_LOGS = 3


def read_exact(stream: BinaryIO, offset: int, length: int) -> bytes:
    """Read an exact byte range or raise a descriptive short-read error."""
    stream.seek(offset)
    data = stream.read(length)
    if len(data) != length:
        raise ValueError(
            f"short read at byte {offset}: expected={length} actual={len(data)}"
        )
    return data


def crc32_le(seed: int, data: bytes) -> int:
    """Match the Linux crc32_le() convention used by F2FS checkpoints."""
    return zlib.crc32(data, seed ^ 0xFFFFFFFF) ^ 0xFFFFFFFF


def find_superblock(stream: BinaryIO) -> bytes:
    """Return the first valid F2FS superblock copy."""
    for block in (0, 1):
        raw = read_exact(
            stream,
            block * F2FS_BLOCK_SIZE + F2FS_SUPER_OFFSET,
            F2FS_BLOCK_SIZE - F2FS_SUPER_OFFSET,
        )
        if struct.unpack_from("<I", raw, 0)[0] == F2FS_SUPER_MAGIC:
            return raw
    raise ValueError("no F2FS superblock found in either copy")


def parse_checkpoint(block: bytes, pack: int, start_block: int) -> Dict[str, object]:
    """Decode and validate one checkpoint header block."""
    checksum_offset = struct.unpack_from("<I", block, 164)[0]
    if checksum_offset > len(block) - 4 or checksum_offset < 192:
        raise ValueError(
            f"pack {pack}: invalid checksum offset {checksum_offset}"
        )
    stored_crc = struct.unpack_from("<I", block, checksum_offset)[0]
    computed_crc = crc32_le(F2FS_SUPER_MAGIC, block[:checksum_offset]) & 0xFFFFFFFF
    version = struct.unpack_from("<Q", block, 0)[0]
    pack_blocks = struct.unpack_from("<I", block, 136)[0]
    node_offsets = list(struct.unpack_from("<3H", block, 68))
    data_offsets = list(struct.unpack_from("<3H", block, 116))
    return {
        "pack": pack,
        "start_block": start_block,
        "version": version,
        "pack_blocks": pack_blocks,
        "checksum_offset": checksum_offset,
        "stored_crc": stored_crc,
        "computed_crc": computed_crc,
        "crc_valid": stored_crc == computed_crc,
        "node_offsets": node_offsets,
        "data_offsets": data_offsets,
    }


def inspect(device: str) -> Dict[str, object]:
    """Inspect both checkpoint packs and return their decoded state."""
    with open(device, "rb", buffering=0) as stream:
        superblock = find_superblock(stream)
        log_blocksize = struct.unpack_from("<I", superblock, 16)[0]
        log_blocks_per_seg = struct.unpack_from("<I", superblock, 20)[0]
        cp_start = struct.unpack_from("<I", superblock, 76)[0]
        block_size = 1 << log_blocksize
        blocks_per_seg = 1 << log_blocks_per_seg
        if block_size != F2FS_BLOCK_SIZE:
            raise ValueError(f"unsupported F2FS block size {block_size}")

        packs: List[Dict[str, object]] = []
        for index, start_block in enumerate(
            (cp_start, cp_start + blocks_per_seg), start=1
        ):
            first = read_exact(stream, start_block * block_size, block_size)
            parsed = parse_checkpoint(first, index, start_block)
            pack_blocks = int(parsed["pack_blocks"])
            if not 2 <= pack_blocks <= blocks_per_seg:
                raise ValueError(
                    f"pack {index}: invalid block count {pack_blocks}"
                )
            last_block = start_block + pack_blocks - 1
            last = read_exact(stream, last_block * block_size, block_size)
            last_parsed = parse_checkpoint(last, index, last_block)
            parsed["last_version"] = last_parsed["version"]
            parsed["last_crc_valid"] = last_parsed["crc_valid"]
            parsed["header_pair_valid"] = (
                parsed["version"] == last_parsed["version"]
                and bool(parsed["crc_valid"])
                and bool(last_parsed["crc_valid"])
            )
            offsets = parsed["node_offsets"] + parsed["data_offsets"]
            parsed["offsets_valid"] = all(
                int(offset) < blocks_per_seg for offset in offsets
            )
            packs.append(parsed)

    return {
        "device": os.path.realpath(device),
        "block_size": block_size,
        "blocks_per_segment": blocks_per_seg,
        "checkpoint_start_block": cp_start,
        "packs": packs,
        "valid": all(
            bool(pack["header_pair_valid"]) and bool(pack["offsets_valid"])
            for pack in packs
        ),
    }


def main() -> int:
    """Parse arguments, print JSON, and fail when either pack is unsafe."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("device", help="Unmounted F2FS block device or image")
    args = parser.parse_args()
    try:
        result = inspect(args.device)
    except (OSError, ValueError, struct.error) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
