"""Optional real IDA test: set IDA_TEST_DATABASE to a closed .i64 file."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from run_site import ida_export


@pytest.mark.skipif(
    not os.environ.get("IDA_TEST_DATABASE"),
    reason="requires IDA_TEST_DATABASE and licensed idalib",
)
def test_real_database_snapshot_preserves_source(tmp_path):
    source = Path(os.environ["IDA_TEST_DATABASE"]).resolve()
    before = ida_export.digest(source)
    output = tmp_path / "data"
    result = subprocess.run(
        [
            sys.executable,
            ida_export.__file__,
            str(source),
            str(output),
        ],
        check=True,
        timeout=300,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert "DeprecationWarning" not in result.stderr, result.stderr
    assert ida_export.digest(source) == before
    index = json.loads((output / "index.json").read_text())
    assert index["source_sha256"] == before
    assert index["functions"]
    assert index["head_count"] > 0
    assert "entry_points" in index
    for entry in index["entry_points"]:
        assert int(entry["address"], 16) >= 0
        assert entry["name"]
    previous_end = 0
    for chunk in index["chunks"]:
        assert int(chunk["start"], 16) >= previous_end
        rows = json.loads((output / chunk["file"]).read_text())
        assert len(rows) == chunk["count"]
        assert rows[0]["address"] == chunk["start"]
        previous_end = int(chunk["end"], 16)
    for item in index["functions"]:
        details = json.loads((output / item["file"]).read_text())
        assert details["pseudocode"] or details["error"]
    for item in index["types"]:
        assert json.loads((output / item["file"]).read_text())["declaration"]
