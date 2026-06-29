"""Guard: the in-app changelog, __version__, and CHANGELOG.md stay in sync."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="earshot_test_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from meeting_notes import __version__, changelog  # noqa: E402


def main() -> int:
    assert changelog.RELEASES, "no releases defined"
    assert changelog.latest().version == __version__, (
        f"changelog latest {changelog.latest().version} != __version__ {__version__}"
    )

    versions = [r.version for r in changelog.RELEASES]
    assert len(versions) == len(set(versions)), "duplicate versions in changelog"
    parsed = [tuple(int(x) for x in v.split(".")) for v in versions]
    assert parsed == sorted(parsed, reverse=True), "releases must be listed newest-first"

    for rel in changelog.RELEASES:
        assert rel.date and len(rel.date) == 10 and rel.date[4] == "-", f"bad date: {rel.date}"
        assert rel.sections, f"release {rel.version} has no sections"

    md_path = Path(__file__).resolve().parent.parent / "CHANGELOG.md"
    assert md_path.exists(), "CHANGELOG.md missing"
    on_disk = md_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert on_disk == changelog.to_markdown(), (
        "CHANGELOG.md is stale — regenerate it from meeting_notes.changelog.to_markdown()"
    )

    print("CHANGELOG OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
