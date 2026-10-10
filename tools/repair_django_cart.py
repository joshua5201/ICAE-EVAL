#!/usr/bin/env python3
"""Apply the fork's reviewed realcode@005 corrections to downloaded PRD data.

Only oracle_data is changed. PRDs, public cases and graders remain untouched.
The original bundle and already-repaired files are pinned by SHA-256: a new
upstream release must be reviewed rather than silently patched.
"""

import argparse
import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASK = "realcode@005"
DIRECTORIES = {
    "normal": "prd_json",
    "medium": "prd_json_medium",
    "easy": "prd_json_easy",
}
ORIGINAL_SHA256 = {
    "normal": "4f78c5d675eabeaf0d84e1978470c8810d9e61e084090cd24cf6a279ada707c8",
    "medium": "e032a56026695e8b0b4b18132603f203b49c89ffebdc52a404401eddb6374a65",
    "easy": "18afe5f4479d53a3686329b9a8dbf8336fe4411b69267f1ba0859527242ae433",
}
REPAIRED_SHA256 = {'normal': '94f621925949a4667061c24a497203483da6ca6059f3469babab3eb403debca6', 'medium': '89dd612be2f3d81606424f1345993e25d97f60710fa4486abd000398bcbe07a1', 'easy': '7491690eac3d68a1ce20eb61a93d0c206f99ae2a0fad93048a2075970a5bc83c'}


def repair_record(record: dict, difficulty: str) -> dict:
    result = copy.deepcopy(record)
    oracle = result["oracle_data"]
    constraints = oracle["hidden_constraints"]

    def set_constraint(identifier: str, keywords: list[str], response: str) -> None:
        existing = next((c for c in constraints if c["constraint_id"] == identifier), None)
        if existing is None:
            constraints.append({"constraint_id": identifier,
                                "trigger_keywords": keywords, "oracle_response": response})
        else:
            existing["trigger_keywords"] = list(dict.fromkeys(existing["trigger_keywords"] + keywords))
            existing["oracle_response"] = response

    oracle["golden_api_signature"] = "bash rcb_tests/test.sh [--cases-dir <subdir>]"
    set_constraint("HARNESS001", ["how is the program invoked", "stdin stdout interface",
                                 "execution adapter", "input schema", "operations format"],
                   "Implement an execution adapter separate from the cart core. Read one JSON "
                   "object from stdin containing 'products' (integer product IDs) and 'operations' "
                   "(an ordered array). Process each operation in order, dispatching on its 'op' "
                   "field, and print the output required by that operation's contract. Cart "
                   "business logic belongs in the core, not in the JSON/stdin/stdout adapter.")
    set_constraint("HARNESS002", ["how to write test.sh", "cases-dir parameter",
                                 "test harness entry point", "output file naming format"],
                   "Deliver rcb_tests/test.sh, invoked with 'bash rcb_tests/test.sh'. Accept "
                   "--cases-dir <subdir> (default: public_test_cases) selecting *.json files under "
                   "rcb_tests/<subdir>/. Run each entry in each file's 'cases' array independently, "
                   "passing its input as JSON to the execution adapter. Write only raw stdout "
                   "to rcb_tests/stdout/<subdir>/{filename_stem}@{case_index_zero_padded_3}.txt "
                   "(zero-based index). Keep outputs for different case directories separate. "
                   "Case files must be available before running; this script does not fetch them.")
    set_constraint("HARNESS003", ["what features have test cases", "show me the test cases",
                                 "are there public test cases"],
                   "Public test case JSON is available by feature name: "
                   + ", ".join(Path(name).stem for name in record["public_test_cases"])
                   + ". Ask for a specific feature and I will return its full JSON. Save that "
                   "JSON under rcb_tests/public_test_cases/ yourself unless the runner has "
                   "already supplied the files via --scaffold.")
    set_constraint("C_DUPLICATE_ADD", ["duplicate add", "adding the same product twice",
                                     "add accumulate or replace", "price on repeated add",
                                     "test cases for duplicate add handling"],
                   "Adding an already-present product updates the same line item: add the "
                   "supplied quantity to its existing quantity and replace its unit price with "
                   "the newly supplied unit price. Recalculate the subtotal using the combined "
                   "quantity and new unit price. This differs from 'update', which replaces both "
                   "quantity and unit price. There is no separate duplicate-add Public test file "
                   "in this record; this is the behavior contract, not an additional test case.")
    oracle["fallback_response"] = (
        "I don't have a documented answer to that question. You may ask for an available "
        "public test case by feature name. Public case files are not guaranteed to be preloaded; "
        "save any JSON returned by the Oracle yourself."
    )

    if difficulty == "easy":
        # These answers are already explicit in the easy PRD, but its generated
        # Oracle has empty responses and an incompatible sorting pointer.
        set_constraint("C006", ["subtotal calculation", "decimal money arithmetic"],
                       "A line item's subtotal is quantity multiplied by unit price, using "
                       "exact fixed-point decimal arithmetic. Render money with two decimal "
                       "places. The cart total is the sum of line-item subtotals.")
        set_constraint("C011", ["dump format", "cart state output format"],
                       "Print line items as 'item product=<product-id> quantity=<n> "
                       "unit_price=<price> subtotal=<price>', ordered by ascending numeric "
                       "product ID. Then print 'total=<price>' followed by 'count=<n>'. "
                       "Render money with exactly two decimal places; count is the sum of quantities.")
        for pointer in oracle["context_pointers"]:
            if pointer["vague_reference"] == "follow the sorting logic used in the headers module":
                pointer["actual_meaning"] = "Sort line items by ascending numeric product ID."
        for filename, cases in record["public_test_cases"].items():
            feature = Path(filename).stem
            set_constraint("TC_" + feature, ["test cases for " + feature,
                                             "test cases for " + feature.replace("_", " ")],
                           f"Here are the public test cases for {feature}:\n"
                           + json.dumps(cases, ensure_ascii=False, indent=2))
    return result


def encode(record: dict) -> bytes:
    return (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def repair_bundle(root: Path, check: bool = False) -> list[str]:
    pending = []
    for difficulty, directory in DIRECTORIES.items():
        path = root / "user_agent" / directory / f"{TASK}.json"
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest == REPAIRED_SHA256.get(difficulty):
            continue
        if digest != ORIGINAL_SHA256[difficulty]:
            raise ValueError(f"Unreviewed data at {path}; refusing to overwrite (sha256={digest})")
        patched = encode(repair_record(json.loads(raw), difficulty))
        if hashlib.sha256(patched).hexdigest() != REPAIRED_SHA256[difficulty]:
            raise ValueError(f"Repair fingerprint mismatch for {difficulty}")
        pending.append((path, patched, difficulty))
    # Validate all levels before changing any files.
    if check and pending:
        raise ValueError("Corrections not applied: " + ", ".join(item[2] for item in pending))
    for path, patched, _ in pending:
        temporary = path.with_suffix(".json.part")
        temporary.write_bytes(patched)
        temporary.replace(path)
    return [difficulty for _, _, difficulty in pending]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT,
                        help="Root containing the downloaded user_agent/prd_json* trees")
    parser.add_argument("--check", action="store_true", help="Verify without writing")
    args = parser.parse_args()
    try:
        changed = repair_bundle(args.root, check=args.check)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"[repair] {exc}\n")
    print(f"[repair] {TASK}: " + ("updated " + ", ".join(changed) if changed else "verified"))


if __name__ == "__main__":
    main()
