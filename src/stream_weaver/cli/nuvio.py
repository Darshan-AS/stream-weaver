"""
Nuvio flows over profiles — push / pull / diff of each profile's compiled client documents.

One `NuvioTarget` per profile that has a Nuvio client and a configured login. All network calls
share one `NuvioClientSync` (hence one `NuvioSessionPool`), so profiles on the same login
authenticate once. Pull reconciles server state back into the profile via eject semantics
(`core.composition.eject_*`) and returns the updated profile documents; it never writes files.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from stream_weaver.core.aiomanager_accounts import (
    ResolvedProfile,
    apply_managed_accounts,
    build_managed_account,
)
from stream_weaver.core.compile import CompiledProfile
from stream_weaver.core.composition import eject_collections, eject_doc
from stream_weaver.core.errors import StreamWeaverError
from stream_weaver.core.library import Library
from stream_weaver.core.nuvio_settings import active_blocks, normalize_nuvio_settings_doc
from stream_weaver.core.profiles import Profile, profile_to_doc
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.secrets import harvest_and_scrub, inject_secrets
from stream_weaver.core.tenant import TenantPaths
from stream_weaver.core.types import EnvMap, JsonDict
from stream_weaver.effects.http import HttpClientProtocol
from stream_weaver.effects.storage import read_json_file
from stream_weaver.providers.services.clients import NuvioClientSync

NuvioDoc = Literal["collections", "badges", "meta", "settings"]
ALL_NUVIO_DOCS: tuple[NuvioDoc, ...] = ("collections", "badges", "meta", "settings")

NUVIO_TARGET_ALIASES: Mapping[str, tuple[NuvioDoc, ...]] = {
    "nuvio": ALL_NUVIO_DOCS,
    "nuvio_collections": ("collections",),
    "nuvio_badges": ("badges",),
    "nuvio_meta": ("meta",),
    "nuvio_settings": ("settings",),
}


@dataclass(frozen=True, slots=True)
class NuvioTarget:
    compiled: CompiledProfile
    connection: Mapping[str, Any]  # injected (secrets resolved) AIOManager connection
    master_password: str
    salt_b64: str
    profile_name_hint: str | None

    @property
    def resolved(self) -> ResolvedProfile:
        return self.compiled.resolved

    @property
    def name(self) -> str:
        return self.compiled.name


def nuvio_targets(
    *,
    tenant_name: str,
    scrubbed_aiomanager: Mapping[str, Any],
    env: EnvMap,
    compiled_profiles: Sequence[CompiledProfile],
) -> Result[list[NuvioTarget], StreamWeaverError]:
    """Resolves the Nuvio connection for every profile with a Nuvio client, via the managed account."""
    password = env.get("AIOMANAGER_SYNC_PASSWORD", "")
    if not password:
        return Err(StreamWeaverError("AIOMANAGER_SYNC_PASSWORD is not set in .env"))
    managed = apply_managed_accounts(scrubbed_aiomanager, tenant_name, [cp.resolved for cp in compiled_profiles]).state
    match inject_secrets(managed, env):
        case Err(err):
            return Err(StreamWeaverError(str(err)))
        case Ok(injected):
            pass
    salt_b64 = str(injected.get("salt") or "")
    accounts = {str(a.get("id")): a for a in injected.get("accounts", []) if isinstance(a, dict)}
    out: list[NuvioTarget] = []
    for cp in compiled_profiles:
        profile = cp.resolved.profile
        if profile.nuvio is None:
            continue
        account_id = profile.account_id or next(
            (aid for aid, a in accounts.items() if a.get("name") == profile.display_name), ""
        )
        acc = accounts.get(account_id)
        if acc is None:
            return Err(StreamWeaverError(f"profile `{profile.name}`: no managed account found for its Nuvio client"))
        conn = next(
            (c for c in acc.get("connections", []) if isinstance(c, dict) and c.get("platform") == "nuvio"), None
        )
        if conn is None:
            return Err(StreamWeaverError(f"profile `{profile.name}`: account has no Nuvio connection"))
        out.append(
            NuvioTarget(
                compiled=cp,
                connection=conn,
                master_password=password,
                salt_b64=salt_b64,
                profile_name_hint=profile.nuvio.profile_name if not profile.nuvio.profile_id else None,
            )
        )
    return Ok(out)


# ---------------------------------------------------------------------------
# push
# ---------------------------------------------------------------------------


def push_nuvio(
    targets: Sequence[NuvioTarget],
    docs: Sequence[NuvioDoc],
    env: EnvMap,
    http_client: HttpClientProtocol,
) -> Result[tuple[list[str], dict[str, str]], StreamWeaverError]:
    """Pushes the selected compiled documents for every target. Returns (messages, refreshed secrets)."""
    syncer = NuvioClientSync(http_client)
    messages: list[str] = []
    refreshed: dict[str, str] = {}
    working = dict(env)
    for t in targets:
        cp = t.compiled
        if "collections" in docs and cp.collections is not None:
            payload: JsonDict = {"addons": [], "nuvioCollections": cp.collections_doc}
            if t.profile_name_hint:
                payload["nuvioProfileName"] = t.profile_name_hint
            # sync_account pushes addons too; for a collections-only push we pass the account's real addon list
            payload["addons"] = _account_addons(t)
            match inject_secrets(payload, working):
                case Err(sec_err):
                    return Err(StreamWeaverError(f"[{t.name}] {sec_err}"))
                case Ok(inj):
                    pass
            match syncer.sync_account(inj, t.connection, t.master_password, t.salt_b64, working):
                case Err(sync_err):
                    return Err(StreamWeaverError(f"[{t.name}] {sync_err}"))
                case Ok((msg, secrets)):
                    messages.append(f"[{t.name}] {msg}")
                    refreshed.update(secrets)
                    working.update(secrets)
        for kind, doc, fn in (
            ("badges", cp.badges, syncer.push_badges),
            ("meta", cp.meta, syncer.push_meta),
            ("settings", cp.settings, syncer.push_settings),
        ):
            if kind not in docs or doc is None:
                continue
            match inject_secrets(doc, working):
                case Err(doc_sec_err):
                    return Err(StreamWeaverError(f"[{t.name}] {kind}: {doc_sec_err}"))
                case Ok(inj_doc):
                    pass
            match fn(inj_doc, t.connection, t.master_password, t.salt_b64, working):
                case Err(push_err):
                    return Err(StreamWeaverError(f"[{t.name}] {kind}: {push_err}"))
                case Ok((msg, secrets)):
                    messages.append(f"[{t.name}] {kind}: {msg}")
                    refreshed.update(secrets)
                    working.update(secrets)
    return Ok((messages, refreshed))


def _account_addons(t: NuvioTarget) -> list[JsonDict]:
    """The addon list Nuvio should hold for this profile — taken from the managed account build."""
    acc = build_managed_account("", t.resolved, None)
    return [a for a in acc.get("addons", []) if isinstance(a, dict)]


# ---------------------------------------------------------------------------
# pull (eject) / diff
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PulledProfile:
    name: str
    remote: dict[NuvioDoc, Any]  # scrubbed server documents (collections → list, others → doc)
    updated_profile: Profile
    changed_docs: tuple[NuvioDoc, ...]


def pull_nuvio(
    targets: Sequence[NuvioTarget],
    docs: Sequence[NuvioDoc],
    env: EnvMap,
    http_client: HttpClientProtocol,
    library: Library,
) -> Result[tuple[list[PulledProfile], dict[str, str]], StreamWeaverError]:
    """Pulls server documents per profile, scrubs secrets, and ejects differences into the profile."""
    syncer = NuvioClientSync(http_client)
    working = dict(env)
    refreshed: dict[str, str] = {}
    out: list[PulledProfile] = []
    for t in targets:
        cp = t.compiled
        profile = cp.resolved.profile
        remote: dict[NuvioDoc, Any] = {}
        if "collections" in docs:
            match syncer.pull_collections(t.connection, t.master_password, t.salt_b64, working):
                case Err(err):
                    return Err(StreamWeaverError(f"[{t.name}] collections: {err}"))
                case Ok((cols, secrets)):
                    working.update(secrets)
                    refreshed.update(secrets)
                    scrubbed, working = harvest_and_scrub("aiomanager", {"collections": cols}, working)
                    remote["collections"] = scrubbed.get("collections", [])
        settings_blocks: list[str] | None = None
        if cp.settings is not None and isinstance(norm := normalize_nuvio_settings_doc(cp.settings), Ok):
            settings_blocks = sorted(active_blocks(norm.value))
        doc_kinds: tuple[NuvioDoc, ...] = ("badges", "meta", "settings")
        for kind in doc_kinds:
            if kind not in docs:
                continue
            if kind == "badges":
                res = syncer.pull_badges(t.connection, t.master_password, t.salt_b64, working, version=_version(cp.badges))
            elif kind == "meta":
                res = syncer.pull_meta(t.connection, t.master_password, t.salt_b64, working, version=_version(cp.meta))
            else:
                res = syncer.pull_settings(
                    t.connection, t.master_password, t.salt_b64, working, version=_version(cp.settings), blocks=settings_blocks
                )
            match res:
                case Err(err):
                    return Err(StreamWeaverError(f"[{t.name}] {kind}: {err}"))
                case Ok((doc, secrets)):
                    working.update(secrets)
                    refreshed.update(secrets)
                    scrubbed, working = harvest_and_scrub("aiomanager", doc, working)
                    remote[kind] = scrubbed

        changed: list[NuvioDoc] = []
        new_collections = profile.collections
        if "collections" in remote and cp.collections is not None:
            ejected = eject_collections(remote["collections"], profile.collections, cp.collections, library, cp.resolver)
            if list(ejected) != list(profile.collections):
                changed.append("collections")
            new_collections = tuple(ejected)
        elif "collections" in remote and cp.collections is None and remote["collections"]:
            new_collections = tuple(remote["collections"])
            changed.append("collections")
        new_docs: dict[str, Any] = {"badges": profile.badges, "meta": profile.meta, "settings": profile.settings}
        for kind in doc_kinds:
            if kind in remote:
                before = new_docs[kind]
                after = eject_doc(remote[kind], before, kind, library)
                if after != before:
                    changed.append(kind)
                new_docs[kind] = after
        updated = replace(
            profile,
            collections=new_collections,
            badges=new_docs["badges"],
            meta=new_docs["meta"],
            settings=new_docs["settings"],
        )
        out.append(PulledProfile(name=t.name, remote=remote, updated_profile=updated, changed_docs=tuple(changed)))
    return Ok((out, refreshed))


def _version(doc: Mapping[str, Any] | None) -> str:
    return str(doc.get("version") or "1.0.0") if doc else "1.0.0"


def local_nuvio_docs(cp: CompiledProfile) -> dict[NuvioDoc, Any]:
    """Compiled local documents in the same shape `pull_nuvio` reports remote ones (for diffs)."""
    out: dict[NuvioDoc, Any] = {}
    if cp.collections is not None:
        out["collections"] = cp.collections_doc
    pairs: tuple[tuple[NuvioDoc, JsonDict | None], ...] = (("badges", cp.badges), ("meta", cp.meta), ("settings", cp.settings))
    for kind, doc in pairs:
        if doc is not None:
            out[kind] = doc
    return out


def profile_doc_after_pull(pulled: PulledProfile) -> JsonDict:
    return profile_to_doc(pulled.updated_profile)


def profile_nuvio_connection(
    tenant_root: Path,
    env: EnvMap,
    *,
    profile_name: str | None = None,
) -> Result[tuple[Mapping[str, Any], str, str, str | None], StreamWeaverError]:
    """
    Script helper: `(injected connection, master password, salt, profile-name hint)` for one profile
    (default: the first profile with a Nuvio client). Mirrors what `sw push nuvio` would use.
    """
    from stream_weaver.cli.app import (  # local: avoid a cycle at import
        _compile_all_result,
        _try_context,
    )

    match _try_context(TenantPaths(tenant_root)):
        case Err() as err:
            return err
        case Ok(ctx):
            pass
    match _compile_all_result(ctx):
        case Err() as err:
            return err
        case Ok(compiled):
            pass
    match nuvio_targets(
        tenant_name=ctx.manifest.name,
        scrubbed_aiomanager=_load_json_or_empty(ctx.paths.doc("aiomanager")),
        env=env,
        compiled_profiles=compiled,
    ):
        case Err() as err:
            return err
        case Ok(targets):
            pass
    chosen = next((t for t in targets if profile_name is None or t.name == profile_name), None)
    if chosen is None:
        return Err(StreamWeaverError(f"no profile with a Nuvio client{f' named {profile_name!r}' if profile_name else ''}"))
    return Ok((chosen.connection, chosen.master_password, chosen.salt_b64, chosen.profile_name_hint))


def _load_json_or_empty(path: Path) -> JsonDict:
    match read_json_file(path):
        case Ok(doc):
            return doc
        case Err():
            return {}
