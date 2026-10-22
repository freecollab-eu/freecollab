#!/usr/bin/env python3
"""Run one exhaustive, deterministic backend test-file shard without new deps."""
import argparse
import json
from pathlib import Path
import sys


BACKEND = Path(__file__).resolve().parents[1] / "backend"


def partition_files(test_root, shard_count):
    if shard_count < 1:
        raise ValueError("shard count must be positive")
    if not test_root.is_dir():
        raise ValueError("backend test directory does not exist")
    # These are pytest's two default Python test-file patterns. Full collection
    # below also rejects any collected file outside this exhaustive manifest.
    files = sorted(path.resolve() for path in test_root.rglob("*.py")
                   if path.name.startswith("test_") or path.name.endswith("_test.py"))
    if len(files) != len(set(files)):
        raise ValueError("test manifest contains duplicate files")
    shards = [files[index::shard_count] for index in range(shard_count)]
    if any(not shard for shard in shards):
        raise ValueError("every shard must contain at least one test file")
    return shards


def select_items(items, selected_files, all_files):
    unknown = {Path(item.path).resolve() for item in items} - set(all_files)
    if unknown:
        raise ValueError("pytest collected files outside the shard manifest")
    selected, deselected = [], []
    selected_paths = set(selected_files)
    for item in items:
        (selected if Path(item.path).resolve() in selected_paths else deselected).append(item)
    if not selected:
        raise ValueError("the selected shard collected no test cases")
    return selected, deselected


class FileShard:
    def __init__(self, selected_files, all_files, shard, shard_count):
        self.selected_files, self.all_files = selected_files, all_files
        self.shard, self.shard_count = shard, shard_count

    def pytest_collection_modifyitems(self, config, items):
        import pytest
        try:
            selected, deselected = select_items(items, self.selected_files, self.all_files)
        except ValueError as error:
            raise pytest.UsageError(str(error)) from None
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected
        terminal = config.pluginmanager.get_plugin("terminalreporter")
        if terminal:
            terminal.write_line(f"Backend shard {self.shard}/{self.shard_count}: "
                                f"{len(selected)} cases in {len(self.selected_files)} files; "
                                f"{len(deselected)} cases belong to other shards")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard", type=int, required=True, help="One-based shard number")
    parser.add_argument("--shards", type=int, required=True, help="Total independent shards")
    parser.add_argument("--list", action="store_true", help="Print the selected file manifest as JSON")
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        shards = partition_files(BACKEND / "tests", args.shards)
        if not 1 <= args.shard <= args.shards:
            raise ValueError("shard must be between 1 and the shard count")
    except ValueError as error:
        parser.error(str(error))
    selected_files = shards[args.shard - 1]
    all_files = [path for shard in shards for path in shard]
    if args.list:
        print(json.dumps({"shard": args.shard, "shards": args.shards,
                          "total_files": len(all_files),
                          "files": [str(path.relative_to(BACKEND)) for path in selected_files]}))
        return 0
    import pytest
    sys.path.insert(0, str(BACKEND))
    pytest_args = args.pytest_args[1:] if args.pytest_args[:1] == ["--"] else args.pytest_args
    # Import the unchanged full suite before selecting cases. Several SQLModel
    # fixtures rely on models registered during collection of sibling files.
    return int(pytest.main([str(BACKEND / "tests"), *pytest_args], plugins=[
        FileShard(selected_files, all_files, args.shard, args.shards)]))


if __name__ == "__main__":
    raise SystemExit(main())
