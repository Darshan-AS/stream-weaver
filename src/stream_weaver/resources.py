"""
Resolves bundled shared `library` and `starter` template directories whether `stream-weaver`
is executed from a git checkout or installed as a standalone wheel (`uv tool install` / `uvx`).
"""

from __future__ import annotations

from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent


def bundled_shared_library_root() -> Path | None:
    """Returns the bundled or repo-checkout shared recipe library directory if present."""
    wheel_copy = _PKG_DIR / "_bundled" / "library"
    if wheel_copy.is_dir():
        return wheel_copy
    checkout_copy = _PKG_DIR.parents[1] / "library"
    if checkout_copy.is_dir():
        return checkout_copy
    return None


def bundled_starter_template_root() -> Path | None:
    """Returns the bundled or repo-checkout starter tenant template (`examples/home`) if present."""
    wheel_copy = _PKG_DIR / "_bundled" / "starter"
    if wheel_copy.is_dir():
        return wheel_copy
    checkout_copy = _PKG_DIR.parents[1] / "examples" / "home"
    if checkout_copy.is_dir():
        return checkout_copy
    return None
