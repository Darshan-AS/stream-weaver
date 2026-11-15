#!/usr/bin/env python3
"""
Regenerates `examples/home/` — the identity-free demo tenant used by docs and as the test
fixture — from a real tenant (default `tenants/don`, profile `don`).

What it strips / rewrites (deterministically, so re-runs are idempotent):
  * hostnames of the source instances → `*.example.com`
  * every UUID → a stable fake UUID (`00000000-0000-4000-8000-0000000000NN`)
  * personal names / labels → "Demo"
  * AIOManager sync salts → fixed demo salts; personal vault, API keys & deletedAddons cleared
  * AIOMetadata `managerAccounts` webhook links cleared; disabled Gemini key slot cleared
  * the Contraband (`swc`) custom preset and every catalog / folder that references it
  * `raw.githubusercontent.com/<owner>/aio_contraband/...` cover art → placeholder images
  * scopes to a single starter profile (`--profile don`) with Nuvio defaulting to profile index 1

Usage:
    uv run python scripts/make_example_tenant.py            # from tenants/don (profile don)
    uv run python scripts/make_example_tenant.py --from tenants/bro --profile bro
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stream_weaver.cli.app import _compile_all_result, _sync_diagrams_for_ctx, _try_context  # noqa: E402
from stream_weaver.core.aiomanager_accounts import apply_managed_accounts  # noqa: E402
from stream_weaver.core.compile import derive_instance_catalogs  # noqa: E402
from stream_weaver.core.logins import login_env_keys  # noqa: E402
from stream_weaver.core.result import Ok  # noqa: E402
from stream_weaver.core.secrets import append_env_placeholders  # noqa: E402
from stream_weaver.core.tenant import TenantPaths, default_tenant_manifest  # noqa: E402

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
CONTRABAND_PRESET_ID = "swc"
CONTRABAND_CATALOG_PREFIX = "swce3b0."
CONTRABAND_RAW_RE = re.compile(r"https://raw\.githubusercontent\.com/[^/]+/aio_contraband/[^\"\s]+")

HOST_MAP = {
    "aiostreamsfortheweebsstable.midnightignite.me": "aiostreams.example.com",
    "aiometadatafortheweebs.midnightignite.me": "aiometadata.example.com",
    "aiomanagerfortheweebs.midnightignite.me": "aiomanager.example.com",
}
TEXT_MAP = {
    "Don's AIOManager": "Demo AIOManager",
    "Bingecat AI Assistant - darshan": "Bingecat AI Assistant - demo",
    "GNPljgiHat52qzk2JP4pDA==": "ZGVtby1zYWx0LWRlbW8tc2FsdA==",
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
        """First pass: register every UUID so short prefixes (`6d639290-199`, `6d639290`) map consistently."""
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


def _clean_aiometadata_for_example(doc: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(doc)
    out["managerAccounts"] = []
    search_cfg = out.get("search")
    engine_enabled = search_cfg.get("engineEnabled") if isinstance(search_cfg, dict) else None
    if (
        isinstance(engine_enabled, dict)
        and not engine_enabled.get("gemini.search", False)
        and isinstance(out.get("apiKeys"), dict)
    ):
        out["apiKeys"]["gemini"] = ""
    return out


def _clean_aiomanager_for_example(
    doc: dict[str, Any], *, account_id: str | None, display_name: str
) -> dict[str, Any]:
    out = copy.deepcopy(doc)
    accounts = [a for a in out.get("accounts", []) if isinstance(a, dict)]
    kept: list[dict[str, Any]] = []
    for acc in accounts:
        if (account_id and acc.get("id") == account_id) or str(acc.get("name") or "") == display_name:
            acc_clean = copy.deepcopy(acc)
            acc_clean.pop("authKey", None)
            acc_clean.pop("apiKey", None)
            acc_clean["deletedAddons"] = {}
            for conn in acc_clean.get("connections", []):
                if isinstance(conn, dict) and isinstance(conn.get("credentials"), dict):
                    conn["credentials"].pop("profileId", None)
            kept.append(acc_clean)
            break
    out["accounts"] = kept
    kept_ids = {str(a.get("id")) for a in kept if a.get("id")}
    if isinstance(out.get("accountStates"), dict):
        out["accountStates"] = {k: v for k, v in out["accountStates"].items() if k in kept_ids}
    out["apiKeys"] = {}
    out["vault"] = []
    if isinstance(out.get("profiles"), list) and out["profiles"]:
        out["profiles"] = out["profiles"][:1]
    return out


def _reachable_local_recipe_ids(
    profile_doc: dict[str, Any], local_recipes: dict[Path, dict[str, Any]], dropped_ids: set[str]
) -> set[str]:
    by_id = {str(d.get("id")): d for d in local_recipes.values() if str(d.get("id")) not in dropped_ids}
    visited: set[str] = set()
    queue: list[str] = []

    def _enqueue_refs(node: Any) -> None:
        if isinstance(node, dict):
            ref = node.get("ref")
            if isinstance(ref, str) and ref in by_id and ref not in visited:
                visited.add(ref)
                queue.append(ref)
            for v in node.values():
                _enqueue_refs(v)
        elif isinstance(node, list):
            for item in node:
                _enqueue_refs(item)

    _enqueue_refs(profile_doc)
    while queue:
        rid = queue.pop()
        _enqueue_refs(by_id[rid])
    return visited


def _strip_extra_instance_env_lines(text: str) -> str:
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("# aiometadata/dad", "AIOMETADATA_DAD_")):
            continue
        out.append(line)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="source", default="tenants/don")
    ap.add_argument("--to", dest="target", default="examples/home")
    ap.add_argument("--profile", dest="profile", default="don")
    args = ap.parse_args()

    src = (ROOT / args.source).resolve()
    dst = (ROOT / args.target).resolve()
    dst.mkdir(parents=True, exist_ok=True)

    src_profile_path = src / "profiles" / args.profile / "profile.json"
    if not src_profile_path.is_file():
        raise SystemExit(f"Source profile not found: {src_profile_path}")
    raw_src_profile = json.loads(src_profile_path.read_text(encoding="utf-8"))
    src_account_id = raw_src_profile.get("accountId")
    src_display_name = str(raw_src_profile.get("displayName") or args.profile)

    scrub = Scrubber()
    env_file = src / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, _, val = line.partition("=")
            if key.strip().endswith("_UUID") and UUID_RE.fullmatch(val.strip()):
                scrub.fake_uuid(val.strip())

    prepared_docs: dict[str, dict[str, Any]] = {}
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
        if name == "aiometadata":
            doc = _clean_aiometadata_for_example(doc)
        elif name == "aiomanager":
            doc = _clean_aiomanager_for_example(
                doc, account_id=src_account_id, display_name=src_display_name
            )
        scrub.collect(doc)
        prepared_docs[name] = doc

    for name, doc in prepared_docs.items():
        (dst / f"{name}.json").write_text(
            json.dumps(scrub.walk(doc), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"✅ {name}.json")

    lib_src = src / "library"
    recipes: dict[Path, dict[str, Any]] = {}
    if lib_src.is_dir():
        for f in sorted(lib_src.rglob("*.json")):
            recipes[f.relative_to(src)] = json.loads(f.read_text(encoding="utf-8"))
    dropped_ids: set[str] = {str(d.get("id")) for d in recipes.values() if _mentions_contraband(d)}
    changed = True
    while changed:
        changed = False
        for d in recipes.values():
            if str(d.get("id")) in dropped_ids or d.get("kind") != "collection":
                continue
            body = d.get("collection") or {}
            kept = [x for x in body.get("folders", []) if not (isinstance(x, dict) and x.get("ref") in dropped_ids)]
            if not kept:
                dropped_ids.add(str(d.get("id")))
                changed = True

    prof_doc = copy.deepcopy(raw_src_profile)
    prof_doc.pop("accountId", None)
    prof_doc["collections"] = [
        c for c in prof_doc.get("collections", []) if not (isinstance(c, dict) and c.get("ref") in dropped_ids)
    ]
    clients_cfg = prof_doc.get("clients")
    if isinstance(clients_cfg, dict) and isinstance(clients_cfg.get("nuvio"), dict):
        clients_cfg["nuvio"]["profile"] = {}

    reachable_ids = _reachable_local_recipe_ids(prof_doc, recipes, dropped_ids)
    if (dst / "library").is_dir():
        shutil.rmtree(dst / "library")
    for rel, d in recipes.items():
        rid = str(d.get("id"))
        if rid in dropped_ids or rid not in reachable_ids:
            print(f"⏭️  {rel}")
            continue
        if d.get("kind") == "collection":
            body = d["collection"]
            body["folders"] = [
                x for x in body.get("folders", []) if not (isinstance(x, dict) and x.get("ref") in dropped_ids)
            ]
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(scrub.walk(d), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"✅ {rel}")

    if (dst / "profiles").is_dir():
        shutil.rmtree(dst / "profiles")
    prof_out = dst / "profiles" / args.profile / "profile.json"
    prof_out.parent.mkdir(parents=True, exist_ok=True)
    prof_out.write_text(json.dumps(scrub.walk(prof_doc), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"✅ {prof_out.relative_to(dst)}")

    src_manifest = json.loads((src / "tenant.json").read_text(encoding="utf-8")) if (src / "tenant.json").is_file() else {}
    manifest = default_tenant_manifest("home")
    shared_lib = ROOT / "library"
    if shared_lib.is_dir():
        manifest["libraries"] = ["library", os.path.relpath(shared_lib, dst)]
    used_logins: dict[str, dict[str, str]] = {}
    for plat, spec in (prof_doc.get("clients") or {}).items():
        if isinstance(spec, dict) and isinstance(spec.get("login"), str):
            lname = spec["login"]
            prefix = (src_manifest.get("logins") or {}).get(plat, {}).get(lname)
            if prefix:
                used_logins.setdefault(plat, {})[lname] = prefix
    manifest["logins"] = used_logins
    manifest["profiles"] = [args.profile]
    (dst / "tenant.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    env_example = src / ".env.example"
    if env_example.is_file():
        text = _strip_extra_instance_env_lines(scrub.text(env_example.read_text(encoding="utf-8")))
        login_keys = [
            k
            for platform, logins in manifest["logins"].items()
            for prefix in logins.values()
            for k in login_env_keys(platform, prefix)
        ]
        text = append_env_placeholders(text, login_keys, header="Client logins (sw login nuvio|stremio --login <name>)")
        (dst / ".env.example").write_text(text, encoding="utf-8")

    # Derive AIOMetadata catalogs, canonicalise managed accounts, and sync architecture diagrams.
    paths = TenantPaths(dst)
    ctx_res = _try_context(paths)
    assert isinstance(ctx_res, Ok), ctx_res
    comp_res = _compile_all_result(ctx_res.value)
    assert isinstance(comp_res, Ok), comp_res
    meta_path = dst / "aiometadata.json"
    if meta_path.is_file():
        meta_doc = json.loads(meta_path.read_text(encoding="utf-8"))
        plan_res = derive_instance_catalogs("default", meta_doc, comp_res.value)
        assert isinstance(plan_res, Ok), plan_res
        if plan_res.value.changed:
            meta_doc["catalogs"] = plan_res.value.catalogs
            meta_path.write_text(json.dumps(meta_doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    mgr_path = dst / "aiomanager.json"
    if mgr_path.is_file():
        mgr_doc = json.loads(mgr_path.read_text(encoding="utf-8"))
        outcome = apply_managed_accounts(mgr_doc, ctx_res.value.manifest.name, ctx_res.value.profiles)
        if outcome.changed:
            mgr_path.write_text(json.dumps(outcome.state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    ctx_res2 = _try_context(paths)
    assert isinstance(ctx_res2, Ok), ctx_res2
    diag_res = _sync_diagrams_for_ctx(ctx_res2.value)
    assert isinstance(diag_res, Ok), diag_res
    print(f"✅ tenant.json, .env.example, ARCHITECTURE.md, topology.c4  →  {dst.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
