#!/usr/bin/env python3
"""Align django-cart empty totals with the Oracle's two-decimal money contract."""

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASE_PATH = Path("rcb_tests_repos/bmentges__django-cart/rcb_tests/enhanced_test_cases")
# Only reviewed Enhanced files are patched; inputs and non-total output are unchanged.
HASHES = {
    "enhanced_boundary_empty_cart.json": (
        "7c0b14aed42a11458f3b260f379d8c26e98b871a32c95664e05766796a29d6d7",
        "3b63a0a3a43ae3f87626ae5b4d37afe3f643f4d661e88b3b9921be379995c40b"),
    "enhanced_error_absent_item.json": (
        "f11efbe30a9f3f782e2ba19480effbf4487b63592630ed22c53fbe3424be9ca5",
        "178f9e1ed7c96daaab9f48c1a4375bf094d675e26fc55988645a0d92637be36b"),
    "enhanced_lifecycle_sequences.json": (
        "9571ff7b244abf8ecb3447d386a59d1002086bc5df1b1f0169e2984356f99b35",
        "e820b79600d40be986e9af157d2f83fbca8a2cf894f48b78a91975adbbe677b2"),
    "enhanced_reopen_persistence.json": (
        "0bd201a1462fe0848e44047349c3a09169633a69f0be9a09e0c10f074c861acb",
        "acad8ceb1206909e9ffca440538a2344dc0b2e57a4deacf9ea489ba1009f3d85"),
    "enhanced_update_replace.json": (
        "7982f9a25160c497b17d1e58ee792df5d7b37c7c35e87f71146e3979ccafba73",
        "20b8408ee4f373dd0f8c6c7619739f900112b915baa101e628d3a72f77041c16"),
}


def repair(root: Path) -> list[str]:
    pending = []
    for filename, (original, corrected) in HASHES.items():
        path = root / CASE_PATH / filename
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest == corrected:
            continue
        if digest != original:
            raise ValueError(f"Unreviewed grader data at {path} (sha256={digest})")
        data = json.loads(raw)
        for case in data["cases"]:
            case["expected_output"] = "\n".join(
                "total=0.00" if line == "total=0" else line
                for line in case["expected_output"].split("\n")
            )
        patched = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()
        if hashlib.sha256(patched).hexdigest() != corrected:
            raise ValueError(f"Grader repair fingerprint mismatch: {filename}")
        pending.append((path, patched))
    for path, patched in pending:
        temporary = path.with_suffix(".json.part")
        temporary.write_bytes(patched)
        temporary.replace(path)
    return [path.name for path, _ in pending]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        changed = repair(args.root)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"[grader-repair] {exc}\n")
    print(f"[grader-repair] django-cart: {len(changed)} files updated")
