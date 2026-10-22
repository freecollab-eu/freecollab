"""Dependency-free checks for exhaustive CI backend sharding."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest


SCRIPT = Path(__file__).with_name("ci-backend-tests.py")
spec = importlib.util.spec_from_file_location("ci_backend_tests", SCRIPT)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class BackendShardTests(unittest.TestCase):
    def test_real_manifest_is_deterministic_exhaustive_and_disjoint(self):
        shards = runner.partition_files(runner.BACKEND / "tests", 4)
        expected = sorted(path.resolve() for path in (runner.BACKEND / "tests").rglob("*.py")
                          if path.name.startswith("test_") or path.name.endswith("_test.py"))
        self.assertEqual(sorted(path for shard in shards for path in shard), expected)
        self.assertEqual(sum(map(len, shards)), len(set(expected)))
        self.assertTrue(all(shards))
        self.assertLessEqual(max(map(len, shards)) - min(map(len, shards)), 1)
        self.assertEqual(shards, runner.partition_files(runner.BACKEND / "tests", 4))

    def test_default_pytest_patterns_and_nested_files_are_included(self):
        with tempfile.TemporaryDirectory() as owned:
            root = Path(owned)
            (root / "nested").mkdir()
            for name in ["test_first.py", "last_test.py", "helper.py", "nested/test_deep.py"]:
                (root / name).touch()
            self.assertEqual([path.name for path in runner.partition_files(root, 1)[0]],
                             ["last_test.py", "test_deep.py", "test_first.py"])

    def test_empty_invalid_and_missing_manifests_fail(self):
        with tempfile.TemporaryDirectory() as owned:
            root = Path(owned)
            for count in [0, -1, 1, 4]:
                with self.assertRaises(ValueError):
                    runner.partition_files(root, count)
            (root / "test_one.py").touch()
            with self.assertRaises(ValueError):
                runner.partition_files(root, 2)
            with self.assertRaises(ValueError):
                runner.partition_files(root / "missing", 1)

    def test_each_collected_case_is_owned_exactly_once(self):
        shards = runner.partition_files(runner.BACKEND / "tests", 4)
        files = [path for shard in shards for path in shard]
        items = [SimpleNamespace(path=path, name=f"case-{index}")
                 for path in files for index in range(3)]
        kept = []
        for shard in shards:
            selected, deselected = runner.select_items(items, shard, files)
            self.assertEqual(len(selected) + len(deselected), len(items))
            kept.extend(id(item) for item in selected)
        self.assertCountEqual(kept, [id(item) for item in items])
        self.assertEqual(len(kept), len(set(kept)))

    def test_empty_or_unrecognized_collected_cases_fail(self):
        files = runner.partition_files(runner.BACKEND / "tests", 1)[0]
        with self.assertRaises(ValueError):
            runner.select_items([], files, files)
        with self.assertRaises(ValueError):
            runner.select_items([SimpleNamespace(path=Path("outside_test.py"))], files, files)

    def test_cli_manifest_and_invalid_shards(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "--shard", "1", "--shards", "4", "--list"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["files"])
        for shard, count in [(0, 4), (5, 4), (1, 0), (-1, 4), (1, 100000)]:
            result = subprocess.run([sys.executable, str(SCRIPT), "--shard", str(shard),
                                     "--shards", str(count), "--list"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
