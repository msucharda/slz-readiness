import re

from slz_readiness import __version__


def test_version() -> None:
    # Don't pin to a specific version here — scripts/release.py is the source of
    # truth and the release workflow verifies the tag matches every manifest.
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?", __version__)
