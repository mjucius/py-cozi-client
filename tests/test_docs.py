"""
README.md names only methods and exports that exist.
"""

import re
from pathlib import Path

import cozi_client
from cozi_client import CoziClient

README = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")


def test_every_documented_method_exists():
    names = set(re.findall(r"await client\.(\w+)\(", README))
    assert len(names) > 40, "the API reference was not found"
    missing = sorted(n for n in names if not hasattr(CoziClient, n))
    assert not missing, f"README documents methods CoziClient does not have: {missing}"


def test_every_documented_import_exists():
    lines = re.findall(r"^\s*from cozi_client import (.+)$", README, re.MULTILINE)
    assert lines, "no 'from cozi_client import' line in the README"
    names = {n.strip() for line in lines for n in line.split(",") if n.strip()}
    missing = sorted(n for n in names if not hasattr(cozi_client, n))
    assert not missing, f"README imports names cozi_client does not export: {missing}"
