"""
Boundary lint — enforces the repository's ownership rules (plan §1) as a test so CI blocks violations.

1. `src/` never names `tenants/`, `library/`, or `examples/` by path, and carries no personal identity
   (hostnames, instance UUIDs, personal names) — the tool receives directories as arguments.
2. `library/` recipes carry no literal manifest ids, hostnames, UUIDs, or `{{SECRET_*}}` placeholders.
3. A tenant never references another tenant's directory.
4. `examples/` (the public-eligible fixture) carries no identity markers.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
LIBRARY = REPO / "library"
EXAMPLES = REPO / "examples"
TENANTS = REPO / "tenants"

UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b", re.I)
FAKE_UUID_RE = re.compile(r"^00000000-0000-4000-8000-\d{12}$", re.I)
HOST_RE = re.compile(r"https?://([a-z0-9.-]+\.[a-z]{2,})", re.I)

# Hostnames a recipe or the tool may legitimately reference (public, identity-free infrastructure).
PUBLIC_HOST_ALLOWLIST = {
    "api.nuvio.tv",
    "api.themoviedb.org",
    "image.tmdb.org",
    "raw.githubusercontent.com",
    "github.com",
    "imkaptain.github.io",
    "placehold.co",
    "v3-cinemeta.strem.io",
    "stream-weaver.dev",
    "aiostreams.elfhosted.com",
    "aiometadata.elfhosted.com",
    "aiomanager.elfhosted.com",
    "itsout.elfhosted.com",
    "einthusan.asaddon.com",
    "bingecat.com",
    "torbox.app",
    "www.themoviedb.org",
    "thetvdb.com",
    "mdblist.com",
    "developer.trakt.tv",
    "api.trakt.tv",
    "api.simkl.com",
}

# Markers that identify the maintainer's own deployment and must never appear outside tenants/.
IDENTITY_MARKERS = ("midnightignite", "darshan", "Darshan", "swce3b0", "6d639290")

_PATH_LITERAL_RE = re.compile(r"""["'](\.?/)?(tenants|library|examples)/""")


def _py_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _text_files(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix in suffixes and ".cache" not in p.parts)


# ---------------------------------------------------------------------------
# Rule 1 — src/ is tenant-agnostic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", _py_files(SRC), ids=lambda p: str(p.relative_to(REPO)))
def test_src_has_no_hardcoded_tenant_library_or_example_paths(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    hits = [m.group(0) for m in _PATH_LITERAL_RE.finditer(text)]
    assert not hits, f"{path.relative_to(REPO)} hard-codes repo-layout paths: {hits}"


@pytest.mark.parametrize("path", _py_files(SRC), ids=lambda p: str(p.relative_to(REPO)))
def test_src_has_no_identity_markers(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    hits = [m for m in IDENTITY_MARKERS if m in text]
    assert not hits, f"{path.relative_to(REPO)} contains identity markers: {hits}"
    uuids = [u for u in UUID_RE.findall(text) if not FAKE_UUID_RE.match(u)]
    assert not uuids, f"{path.relative_to(REPO)} contains literal UUIDs: {uuids}"


# ---------------------------------------------------------------------------
# Rule 2 — library/ recipes are portable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", _text_files(LIBRARY, (".json",)), ids=lambda p: str(p.relative_to(REPO)))
def test_library_recipes_are_identity_free(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert "{{SECRET_" not in text, f"{path.relative_to(REPO)} embeds a secret placeholder"
    assert not UUID_RE.search(text), f"{path.relative_to(REPO)} embeds a UUID"
    bad_hosts = sorted({h.lower() for h in HOST_RE.findall(text)} - PUBLIC_HOST_ALLOWLIST)
    assert not bad_hosts, f"{path.relative_to(REPO)} references non-public hosts: {bad_hosts}"
    for marker in IDENTITY_MARKERS:
        assert marker not in text, f"{path.relative_to(REPO)} contains identity marker {marker!r}"


def test_library_dir_is_optional_until_phase_2() -> None:
    # Documents the expectation; keeps the parametrized tests above meaningful once recipes exist.
    assert not LIBRARY.exists() or LIBRARY.is_dir()


# ---------------------------------------------------------------------------
# Rule 3 — tenants are isolated from each other
# ---------------------------------------------------------------------------


def _tenant_dirs() -> list[Path]:
    if not TENANTS.is_dir():
        return []
    return sorted(p for p in TENANTS.iterdir() if (p / "tenant.json").is_file())


@pytest.mark.parametrize("tenant_dir", _tenant_dirs(), ids=lambda p: p.name)
def test_tenants_do_not_reference_other_tenants(tenant_dir: Path) -> None:
    others = [t.name for t in _tenant_dirs() if t != tenant_dir]
    for path in _text_files(tenant_dir, (".json", ".md", ".c4")):
        text = path.read_text(encoding="utf-8")
        for other in others:
            assert f"tenants/{other}/" not in text, f"{path.relative_to(REPO)} references tenants/{other}/"


# ---------------------------------------------------------------------------
# Rule 4 — examples/ is identity-free
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", _text_files(EXAMPLES, (".json", ".md", ".c4", ".example")), ids=lambda p: str(p.relative_to(REPO))
)
def test_examples_are_identity_free(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for marker in IDENTITY_MARKERS:
        assert marker not in text, f"{path.relative_to(REPO)} contains identity marker {marker!r}"
    real_uuids = [u for u in UUID_RE.findall(text) if not FAKE_UUID_RE.match(u)]
    assert not real_uuids, f"{path.relative_to(REPO)} contains real-looking UUIDs: {real_uuids[:3]}"
