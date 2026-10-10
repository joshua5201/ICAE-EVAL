"""Offline regression checks against downloaded data (no model/API requests)."""

import ast
import copy
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.repair_django_cart import DIRECTORIES, ROOT, TASK, repair_bundle, repair_record


class CartDataRepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data_root = Path(os.environ.get("ICAE_TEST_PRD_ROOT", ROOT))
        cls.records = {}
        for difficulty, directory in DIRECTORIES.items():
            path = cls.data_root / "user_agent" / directory / f"{TASK}.json"
            if not path.exists():
                raise unittest.SkipTest("Download the PRD bundle or set ICAE_TEST_PRD_ROOT")
            cls.records[difficulty] = json.loads(path.read_text())

    def test_preserves_prds_cases_and_other_record_fields(self):
        for difficulty, record in self.records.items():
            with self.subTest(difficulty=difficulty):
                before = copy.deepcopy(record)
                after = repair_record(record, difficulty)
                self.assertEqual(record, before)
                self.assertEqual({k: v for k, v in before.items() if k != "oracle_data"},
                                 {k: v for k, v in after.items() if k != "oracle_data"})
                self.assertEqual(repair_record(after, difficulty), after)

    def test_adapter_and_defaults_match_public_cases(self):
        for difficulty, record in self.records.items():
            with self.subTest(difficulty=difficulty):
                oracle = repair_record(record, difficulty)["oracle_data"]
                by_id = {c["constraint_id"]: c for c in oracle["hidden_constraints"]}
                adapter = by_id["HARNESS001"]["oracle_response"]
                self.assertIn("'operations'", adapter)
                self.assertIn("'op'", adapter)
                self.assertIn("default: public_test_cases", by_id["HARNESS002"]["oracle_response"])
                for casefile in record["public_test_cases"].values():
                    for case in casefile["cases"]:
                        self.assertIsInstance(case["input"]["products"], list)
                        self.assertTrue(all("op" in op for op in case["input"]["operations"]))

    def test_public_examples_are_recoverable_in_all_levels(self):
        for difficulty, record in self.records.items():
            with self.subTest(difficulty=difficulty):
                oracle = repair_record(record, difficulty)["oracle_data"]
                by_id = {c["constraint_id"]: c for c in oracle["hidden_constraints"]}
                self.assertTrue(all(c["oracle_response"] for c in by_id.values()))
                for name, expected in record["public_test_cases"].items():
                    answer = by_id["TC_" + Path(name).stem]["oracle_response"]
                    self.assertEqual(json.loads(answer[answer.index("{"):]), expected)
                self.assertIn("not guaranteed to be preloaded", oracle["fallback_response"])
                self.assertIn("--scaffold", by_id["HARNESS003"]["oracle_response"])

    def test_easy_contract_can_answer_existing_prd_questions(self):
        oracle = repair_record(self.records["easy"], "easy")["oracle_data"]
        self.assertEqual(oracle["golden_api_signature"], "bash rcb_tests/test.sh [--cases-dir <subdir>]")
        self.assertIn("numeric product ID", oracle["context_pointers"][0]["actual_meaning"])

    def test_apply_is_restart_safe_and_check_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for directory in DIRECTORIES.values():
                target = root / "user_agent" / directory / f"{TASK}.json"
                target.parent.mkdir(parents=True)
                target.write_bytes((self.data_root / "user_agent" / directory / target.name).read_bytes())
            before = {p: p.read_bytes() for p in root.rglob("*.json")}
            if repair_bundle(root):
                # The originals are restored to exercise --check on unapplied data.
                for p, raw in before.items():
                    p.write_bytes(raw)
                with self.assertRaisesRegex(ValueError, "Corrections not applied"):
                    repair_bundle(root, check=True)
                self.assertEqual(before, {p: p.read_bytes() for p in before})
                self.assertEqual(set(repair_bundle(root)), set(DIRECTORIES))
            self.assertEqual(repair_bundle(root), [])
            self.assertEqual(repair_bundle(root, check=True), [])

    def test_unreviewed_level_prevents_any_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for difficulty, directory in DIRECTORIES.items():
                target = root / "user_agent" / directory / f"{TASK}.json"
                target.parent.mkdir(parents=True)
                raw = (self.data_root / "user_agent" / directory / target.name).read_bytes()
                target.write_bytes(raw + (b" " if difficulty == "easy" else b""))
            before = {p: p.read_bytes() for p in root.rglob("*.json")}
            with self.assertRaisesRegex(ValueError, "Unreviewed data"):
                repair_bundle(root)
            self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_oracle_loads_repaired_data_into_its_system_prompt(self):
        # Import the actual service while replacing API libraries. No credentials
        # or model calls are needed to verify the Oracle's injection boundary.
        spec = importlib.util.spec_from_file_location("offline_oracle", ROOT / "user_agent/user_agent.py")
        service = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"anthropic": SimpleNamespace(),
                                      "openai": SimpleNamespace(OpenAI=None)}):
            spec.loader.exec_module(service)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for difficulty, directory in DIRECTORIES.items():
                target = root / directory / f"{TASK}.json"
                target.parent.mkdir(parents=True)
                expected = repair_record(self.records[difficulty], difficulty)
                target.write_text(json.dumps(expected))
                service.PRD_DIRS[difficulty] = target.parent
                record = service.load_record(TASK, difficulty)
                prompt = service.build_system_prompt(record)
                injected = prompt.split("# Injected oracle_data", 1)[1].split("```json\n", 1)[1].split("\n```", 1)[0]
                self.assertEqual(json.loads(injected), expected["oracle_data"])
                self.assertIn("C_DUPLICATE_ADD", injected)


class OriginalCartBehaviorTests(unittest.TestCase):
    def test_duplicate_add_against_original_method(self):
        source = os.environ.get("ICAE_TEST_GOLDEN_CART")
        if not source:
            self.skipTest("Set ICAE_TEST_GOLDEN_CART to the original cart/cart.py")
        tree = ast.parse(Path(source).read_text())
        cart_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Cart")
        add = next(node for node in cart_class.body if isinstance(node, ast.FunctionDef) and node.name == "add")
        # Execute the original method, stubbing only its persistence dependency.
        class Store:
            item = None

            def filter(self, **kwargs):
                return SimpleNamespace(first=lambda: self.item)

            def create(self, **kwargs):
                self.item = SimpleNamespace(**kwargs, save=lambda: None)

        store = Store()
        namespace = {"models": SimpleNamespace(Item=SimpleNamespace(objects=store))}
        method_module = ast.Module(body=[copy.deepcopy(add)], type_ignores=[])
        exec(compile(method_module, str(source), "exec"), namespace)
        cart = SimpleNamespace(cart=object())
        namespace["add"](cart, product=1, unit_price=7, quantity=2)
        first = store.item
        namespace["add"](cart, product=1, unit_price=11, quantity=4)
        self.assertIs(store.item, first)
        self.assertEqual((first.quantity, first.unit_price), (6, 11))


if __name__ == "__main__":
    unittest.main()
