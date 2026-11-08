from __future__ import annotations

import json
from pathlib import Path

from tendermate_shared.build_info import load_build_info


def test_reads_release_file(tmp_path: Path) -> None:
    path = tmp_path / "build_info.json"
    path.write_text(json.dumps({"version": "1.2.3", "git_sha": "abc", "service": "spoofed"}))

    info = load_build_info("tendermate-backend", path)

    assert (info.version, info.git_sha) == ("1.2.3", "abc")
    assert info.service == "tendermate-backend"  # never taken from the file
    assert info.release == "tendermate-backend@1.2.3"
    assert info.shared_version != ""


def test_without_file_is_dev(tmp_path: Path) -> None:
    info = load_build_info("svc", tmp_path / "missing.json")

    assert info.version == "dev"
    assert info.release == "svc@dev"
