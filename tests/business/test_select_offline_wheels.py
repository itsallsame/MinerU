from __future__ import annotations

import pytest

from scripts.select_offline_wheels import select


def test_selects_only_versions_not_already_in_base() -> None:
    lock = (
        "# generated\n"
        "torch==2.10.0+cu129 \\\n"
        "    --hash=sha256:aaa\n"
        "    # via mineru\n"
        "docvortex==0.5.16 \\\n"
        "    --hash=sha256:bbb\n"
        "    # via mineru\n"
    )
    filtered, count = select(lock, [
        {"name": "Torch", "version": "2.10.0+cu129"},
        {"name": "docvortex", "version": "0.5.15"},
    ])
    assert count == 1
    assert filtered.startswith("# generated\n")
    assert "torch==" not in filtered
    assert "docvortex==0.5.16" in filtered
    assert "--hash=sha256:bbb" in filtered


def test_rejects_duplicate_normalized_names() -> None:
    with pytest.raises(ValueError, match="Duplicate locked package"):
        select("Foo_Bar==1\nfoo-bar==2\n", [])
