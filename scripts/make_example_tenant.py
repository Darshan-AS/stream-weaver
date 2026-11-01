#!/usr/bin/env python3
"""
Regenerates `examples/home/` — the identity-free demo tenant used by docs and as the test
fixture — from a real tenant (default `tenants/don`).

What it strips / rewrites (deterministically, so re-runs are idempotent):
  * hostnames of the source instances → `*.example.com`
  * every UUID → a stable fake UUID (`00000000-0000-4000-8000-0000000000NN`)
  * personal names / labels → "Demo"
  * AIOManager sync salts → fixed demo salts
  * the Contraband (`swc`) custom preset and every catalog / folder that references it
  * `raw.githubusercontent.com/<owner>/aio_contraband/...` cover art → placeholder images

Usage:
    uv run python scripts/make_example_tenant.py            # from tenants/don
    uv run python scripts/make_example_tenant.py --from tenants/bro
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stream_weaver.core.logins import login_env_keys  # noqa: E402
from stream_weaver.core.secrets import append_env_placeholders  # noqa: E402
from stream_weaver.core.tenant import default_tenant_manifest  # noqa: E402

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
CONTRABAND_PRESET_ID = "swc"
CONTRABAND_CATALOG_PREFIX = "swce3b0."
CONTRABAND_RAW_RE = re.compile(r"https://raw\.githubusercontent\.com/[^/]+/aio_contraband/[^\"\s]+")

HOST_MAP = {
    "aiostreams.example.com": "aiostreams.example.com",
    "aiometadata.example.com": "aiometadata.example.com",
    "aiomanager.example.com": "aiomanager.example.com",
}
TEXT_MAP = {
    "Demo AIOManager": "Demo AIOManager",
    "Bingecat AI Assistant - demo": "Bingecat AI Assistant - demo",
    "ZGVtby1zYWx0LWRlbW8tc2FsdA==": "ZGVtby1zYWx0LWRlbW8tc2FsdA==",
    "ameu09paiJ5Mx1oQYiyobg==": "ZGVtby1zeW5jLXNhbHQtZGVtbw==",
}
EXACT_VALUE_MAP = {"Don": "Demo", "darshan": "demo"}
PLACEHOLDER_COVER = "https://placehold.co/600x900/111827/f9fafb/webp?text=Folder"
PLACEHOLDER_BACKDROP = "https://placehold.co/1920x1080/111827/f9fafb/webp?text=Backdrop"

DOC_NAMES = (
    "aiostreams",
    "aiometadata",
    "aiomanager",
    "nuvio_collections",
    "nuvio_badges",
    "nuvio_meta",
    "nuvio_settings",
)


class Scrubber:
    def __init__(self) -> None:
        self._uuids: dict[str, str] = {}

    def fake_uuid(self, real: str) -> str:
        key = real.lower()
        if key not in self._uuids:
            n = len(self._uuids) + 1
            self._uuids[key] = f"00000000-0000-4000-8000-{n:012d}"
        return self._uuids[key]

    def collect(self, node: Any) -> None:
        """First pass: register every UUID so short prefixes (`00000000-000`, `00000000`) map consistently."""
        for m in UUID_RE.finditer(json.dumps(node)):
            self.fake_uuid(m.group(0))

    def text(self, s: str) -> str:
        for real, fake in HOST_MAP.items():
            s = s.replace(real, fake)
        for real, fake in TEXT_MAP.items():
            s = s.replace(real, fake)
        if s in EXACT_VALUE_MAP:
            return EXACT_VALUE_MAP[s]
        s = UUID_RE.sub(lambda m: self.fake_uuid(m.group(0)), s)
        for real, fake in self._uuids.items():
            s = s.replace(real[:12], fake[:12]).replace(real[:8], fake[:8])
        if CONTRABAND_RAW_RE.search(s):
            s = PLACEHOLDER_BACKDROP if "backdrop" in s else PLACEHOLDER_COVER
        return s

    def walk(self, node: Any) -> Any:
        if isinstance(node, dict):
            return {self.text(k) if isinstance(k, str) else k: self.walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [self.walk(v) for v in node]
        if isinstance(node, str):
            return self.text(node)
        return node


def _mentions_contraband(node: Any) -> bool:
    blob = json.dumps(node)
    return CONTRABAND_CATALOG_PREFIX in blob or f'"{CONTRABAND_PRESET_ID}"' in blob


def _drop_contraband(node: Any) -> Any:
    """Removes the swc preset, swc group references, swc catalogs, and folders/collections using them."""
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for k, v in node.items():
            if k == "presets" and isinstance(v, list):
                v = [p for p in v if not (isinstance(p, dict) and p.get("instanceId") == CONTRABAND_PRESET_ID)]
            elif k in ("addons", "catalogs", "catalogModifications", "folders", "collections", "sources") and isinstance(v, list):
                v = [
                    item
                    for item in v
                    if not (
                        item == CONTRABAND_PRESET_ID
                        or (isinstance(item, dict) and _mentions_contraband(item) and k != "collections")
                    )
                ]
                if k == "collections":
                    cleaned: list[Any] = []
                    for col in v:
                        col2 = _drop_contraband(col)
                        if isinstance(col2, dict) and col2.get("folders") == []:
                            continue
                        cleaned.append(col2)
                    out[k] = cleaned
                    continue
            out[k] = _drop_contraband(v)
        return out
    if isinstance(node, list):
        return [_drop_contraband(v) for v in node]
    return node


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="source", default="tenants/don")
    ap.add_argument("--to", dest="target", default="examples/home")
    args = ap.parse_args()

    src = (ROOT / args.source).resolve()
    dst = (ROOT / args.target).resolve()
    dst.mkdir(parents=True, exist_ok=True)
    scrub = Scrubber()
    # Instance UUIDs usually appear only as {{PREFIX_UUID}} placeholders after sanitising, but their
    # short prefixes still live inside AIOStreams manifest ids — seed the mapping from `.env` first.
    env_file = src / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, _, val = line.partition("=")
            if key.strip().endswith("_UUID") and UUID_RE.fullmatch(val.strip()):
                scrub.fake_uuid(val.strip())
    for name in DOC_NAMES:
        src_doc = src / f"{name}.json"
        if src_doc.is_file():
            scrub.collect(json.loads(src_doc.read_text(encoding="utf-8")))

    for name in DOC_NAMES:
        src_doc = src / f"{name}.json"
        if not src_doc.is_file():
            stale = dst / f"{name}.json"
            if stale.exists():
                stale.unlink()
                print(f"🗑️  {name}.json (no longer in source tenant)")
            continue
        doc = json.loads(src_doc.read_text(encoding="utf-8"))
        doc = _drop_contraband(copy.deepcopy(doc))
        doc = scrub.walk(doc)
        (dst / f"{name}.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"✅ {name}.json")

    # profiles/ and the tenant-local library/ are copied verbatim (they are already identity-free by
    # construction: symbolic addons, placeholders) — only names/ids pass through the scrubber.
    # Recipes that depend on the private Contraband preset are dropped entirely, and every ref to
    # them is pruned (folders from collections, collections from the profile).
    lib_src = src / "library"
    recipes: dict[Path, dict[str, Any]] = {}
    if lib_src.is_dir():
        for f in sorted(lib_src.rglob("*.json")):
            recipes[f.relative_to(src)] = json.loads(f.read_text(encoding="utf-8"))
    dropped_ids: set[str] = {str(d.get("id")) for d in recipes.values() if _mentions_contraband(d)}
    changed = True
    while changed:  # collections whose folders all vanished are dropped too
        changed = False
        for d in recipes.values():
            if str(d.get("id")) in dropped_ids or d.get("kind") != "collection":
                continue
            body = d.get("collection") or {}
            kept = [x for x in body.get("folders", []) if not (isinstance(x, dict) and x.get("ref") in dropped_ids)]
            if not kept:
                dropped_ids.add(str(d.get("id")))
                changed = True
    for rel, d in recipes.items():
        if str(d.get("id")) in dropped_ids:
            print(f"⏭️  {rel} (depends on the private Contraband preset)")
            continue
        if d.get("kind") == "collection":
            body = d["collection"]
            body["folders"] = [x for x in body.get("folders", []) if not (isinstance(x, dict) and x.get("ref") in dropped_ids)]
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(scrub.walk(d), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"✅ {rel}")
    prof_src = src / "profiles"
    if prof_src.is_dir():
        for f in sorted(prof_src.rglob("profile.json")):
            rel = f.relative_to(src)
            doc = json.loads(f.read_text(encoding="utf-8"))
            doc.pop("accountId", None)
            doc["collections"] = [
                c for c in doc.get("collections", []) if not (isinstance(c, dict) and c.get("ref") in dropped_ids)
            ]
            out = dst / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(scrub.walk(doc), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"✅ {rel}")

    src_manifest = json.loads((src / "tenant.json").read_text(encoding="utf-8")) if (src / "tenant.json").is_file() else {}
    manifest = default_tenant_manifest("home")
    manifest["logins"] = src_manifest.get("logins", {})
    manifest["profiles"] = src_manifest.get("profiles", [])
    (dst / "tenant.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    env_example = src / ".env.example"
    if env_example.is_file():
        text = scrub.text(env_example.read_text(encoding="utf-8"))
        login_keys = [
            k
            for platform, logins in manifest["logins"].items()
            for prefix in logins.values()
            for k in login_env_keys(platform, prefix)
        ]
        text = append_env_placeholders(text, login_keys, header="Client logins (sw login nuvio|stremio --login <name>)")
        (dst / ".env.example").write_text(text, encoding="utf-8")
    print(f"✅ tenant.json, .env.example  →  {dst.relative_to(ROOT)}  (now run: uv run sw diagram --dir {args.target})")


if __name__ == "__main__":
    main()
