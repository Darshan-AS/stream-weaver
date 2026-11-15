"""
Modular Debrid provider selector and toggleable addon packs for `aiostreams.json`.

Why: `profile.json` already composes Nuvio collections, badges, meta, and settings from modular
recipes, whereas `aiostreams.json` is stored as a single raw service document. This pure module
overlays structured **Debrid provider switching** (`torbox`, `realdebrid`, `alldebrid`,
`premiumize`) and **toggleable addon packs** (`core_debrid`, `anime`, `release_alerts`,
`http_fallbacks`, `indian_http`, `core_subtitles`, `dual_subtitles`) onto `aiostreams.json`
losslessly—keeping disabled presets in the local file (`enabled: false`) so re-enabling a pack
never loses its URLs or options, while stripping disabled presets with unset secrets at push time.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from stream_weaver.core.errors import ConfigValidationError
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.secrets import inject_secrets
from stream_weaver.core.types import EnvMap, JsonDict


@dataclass(frozen=True, slots=True)
class DebridProviderSpec:
    id: str
    label: str
    short_name: str
    secret_key: str
    signup_url: str


DEBRID_PROVIDERS: Mapping[str, DebridProviderSpec] = {
    "torbox": DebridProviderSpec(
        id="torbox",
        label="TorBox",
        short_name="TB",
        secret_key="SECRET_TORBOX_API_KEY",
        signup_url="https://torbox.app/settings",
    ),
    "realdebrid": DebridProviderSpec(
        id="realdebrid",
        label="Real-Debrid",
        short_name="RD",
        secret_key="SECRET_RD_API_KEY",
        signup_url="https://real-debrid.com/apitoken",
    ),
    "alldebrid": DebridProviderSpec(
        id="alldebrid",
        label="AllDebrid",
        short_name="AD",
        secret_key="SECRET_ALLDEBRID_API_KEY",
        signup_url="https://alldebrid.com/apikeys/",
    ),
    "premiumize": DebridProviderSpec(
        id="premiumize",
        label="Premiumize",
        short_name="PM",
        secret_key="SECRET_PREMIUMIZE_API_KEY",
        signup_url="https://www.premiumize.me/account",
    ),
}


@dataclass(frozen=True, slots=True)
class AddonPackSpec:
    id: str
    title: str
    description: str
    preset_ids: tuple[str, ...]
    group_index: int | None  # 0 = Group 1 primary, 1 = Group 2 HTTP fallback, None = subtitles/utility
    required_secrets: tuple[str, ...] = ()


AIOSTREAMS_PACKS: tuple[AddonPackSpec, ...] = (
    AddonPackSpec(
        id="core_debrid",
        title="Core Debrid Scrapers & Cloud Library",
        description="Cached torrent indexers (Torrentio, StremThru Torz, Comet, Meteor, MediaFusion, Knaben) + Debrid Cloud Library.",
        preset_ids=("lib", "tio", "stz", "com", "met", "mdf", "kna"),
        group_index=0,
    ),
    AddonPackSpec(
        id="anime",
        title="Anime Scrapers",
        description="Curated anime release indexers (SeaDex best-release tracker + AnimeTosho).",
        preset_ids=("sdx", "atn"),
        group_index=0,
    ),
    AddonPackSpec(
        id="release_alerts",
        title="Release Alerts & Info Cards",
        description="Post-credits scenes (Stinger Pro), digital release status & catalog (ItsOut!), and source adaptation info (BasedOn).",
        preset_ids=("ssp", "ito", "bso"),
        group_index=0,
        required_secrets=("SECRET_ITSOUT_TOKEN",),
    ),
    AddonPackSpec(
        id="http_fallbacks",
        title="Direct HTTP Fallback Scrapers",
        description="Triggered automatically when < 3 cached debrid streams exist (HD Hub, Sootio, PlavioHD).",
        preset_ids=("hdh", "soo", "plv"),
        group_index=1,
    ),
    AddonPackSpec(
        id="indian_http",
        title="Indian Regional HTTP (Einthusan)",
        description="Direct HTTP fallback streams for Kannada & Hindi cinema via EinthusanTV.",
        preset_ids=("etv", "eth"),
        group_index=1,
    ),
    AddonPackSpec(
        id="core_subtitles",
        title="OpenSubtitles",
        description="OpenSubtitles V3 Pro + v3 fallback subtitle providers.",
        preset_ids=("osb", "osv"),
        group_index=None,
    ),
    AddonPackSpec(
        id="dual_subtitles",
        title="SubSource & Strelingo Dual-Subtitles",
        description="SubSource community subtitles plus bilingual dual-subtitle tracks (JA→EN, EN→KN, HI→KN).",
        preset_ids=("ssc", "sl1", "sl2", "sl3"),
        group_index=None,
        required_secrets=("SECRET_SUBSOURCE_API_KEY",),
    ),
)

PACKS_BY_ID: Mapping[str, AddonPackSpec] = {p.id: p for p in AIOSTREAMS_PACKS}

_LIBRARY_CAT_RE = re.compile(r"aiostreams::library\.([a-z0-9_]+)")

_ITSOUT_WATCHLIST_SOURCES: tuple[JsonDict, ...] = (
    {
        "addon": "aiostreams",
        "type": "movie",
        "preset": "ito",
        "catalog": "itsout.released",
        "title": "ItsOut! — Released Movies",
    },
    {
        "addon": "aiostreams",
        "type": "series",
        "preset": "ito",
        "catalog": "itsout.released",
        "title": "ItsOut! — Released Series",
    },
)


@dataclass(frozen=True, slots=True)
class PackStatus:
    spec: AddonPackSpec
    enabled: bool
    present_presets: tuple[str, ...]
    enabled_presets: tuple[str, ...]


def active_debrid_provider(aiostreams_doc: Mapping[str, Any]) -> str:
    """Returns the primary enabled debrid service id in `aiostreams_doc` (defaults to `torbox`)."""
    services = aiostreams_doc.get("services")
    if isinstance(services, list):
        for svc in services:
            if isinstance(svc, Mapping) and svc.get("enabled", True):
                sid = str(svc.get("id") or "").strip().lower()
                if sid in DEBRID_PROVIDERS:
                    return sid
    return "torbox"


def inspect_aiostreams_packs(aiostreams_doc: Mapping[str, Any]) -> tuple[PackStatus, ...]:
    """Inspects which addon packs are currently enabled in `aiostreams_doc`."""
    presets = aiostreams_doc.get("presets")
    by_id: dict[str, bool] = {}
    if isinstance(presets, list):
        for p in presets:
            if isinstance(p, Mapping) and isinstance(p.get("instanceId"), str):
                by_id[p["instanceId"]] = bool(p.get("enabled", True))
    out: list[PackStatus] = []
    for spec in AIOSTREAMS_PACKS:
        present = tuple(pid for pid in spec.preset_ids if pid in by_id)
        enabled = tuple(pid for pid in present if by_id[pid])
        is_on = bool(enabled) if present else False
        out.append(PackStatus(spec=spec, enabled=is_on, present_presets=present, enabled_presets=enabled))
    return tuple(out)


def required_aiostreams_secrets(aiostreams_doc: Mapping[str, Any]) -> tuple[str, ...]:
    """Returns only the `SECRET_*` keys required by *enabled* services and presets in `aiostreams_doc`."""
    keys: set[str] = {"SECRET_TMDB_API_KEY", "SECRET_TMDB_ACCESS_TOKEN", "SECRET_TVDB_API_KEY"}
    if aiostreams_doc.get("posterService") == "rpdb" and aiostreams_doc.get("rpdbApiKey"):
        keys.add("SECRET_RPDB_API_KEY")
    debrid = active_debrid_provider(aiostreams_doc)
    if debrid in DEBRID_PROVIDERS:
        keys.add(DEBRID_PROVIDERS[debrid].secret_key)
    for st in inspect_aiostreams_packs(aiostreams_doc):
        if st.enabled:
            keys.update(st.spec.required_secrets)
    return tuple(sorted(keys))


def apply_debrid_provider(
    aiostreams_doc: Mapping[str, Any], provider_id: str
) -> Result[JsonDict, ConfigValidationError]:
    """Switches the primary Debrid service in `aiostreams_doc` and rewires Cloud Library catalog ids."""
    pid = provider_id.strip().lower()
    spec = DEBRID_PROVIDERS.get(pid)
    if spec is None:
        return Err(
            ConfigValidationError(
                f"Unsupported debrid provider `{provider_id}` (choose from: {', '.join(DEBRID_PROVIDERS)})"
            )
        )
    out: JsonDict = copy.deepcopy(dict(aiostreams_doc))
    out["services"] = [
        {
            "id": spec.id,
            "enabled": True,
            "credentials": {"apiKey": f"{{{{{spec.secret_key}}}}}"},
        }
    ]
    cat_mods = out.get("catalogModifications")
    if isinstance(cat_mods, list):
        for mod in cat_mods:
            if not isinstance(mod, dict):
                continue
            cid = str(mod.get("id") or "")
            if _LIBRARY_CAT_RE.search(cid):
                mod["id"] = _LIBRARY_CAT_RE.sub(f"aiostreams::library.{spec.id}", cid)
                mod["name"] = f"{spec.label} Cloud Library"
    return Ok(out)


def apply_aiostreams_packs(
    aiostreams_doc: Mapping[str, Any], enabled_pack_ids: Iterable[str]
) -> Result[JsonDict, ConfigValidationError]:
    """
    Sets `enabled: true/false` on presets belonging to known packs and updates `groups.groupings`
    and `titleMatching.addons` so only enabled presets remain in active execution groups.
    Unknown custom presets (e.g. `swc`, `tdb`) are left untouched.
    """
    wanted = {p.strip().lower() for p in enabled_pack_ids if p.strip()}
    unknown = sorted(wanted - set(PACKS_BY_ID))
    if unknown:
        return Err(
            ConfigValidationError(
                f"Unknown AIOStreams pack(s) {unknown} (choose from: {', '.join(PACKS_BY_ID)})"
            )
        )

    out: JsonDict = copy.deepcopy(dict(aiostreams_doc))
    managed_preset_state: dict[str, bool] = {}
    for spec in AIOSTREAMS_PACKS:
        state = spec.id in wanted
        for pid in spec.preset_ids:
            managed_preset_state[pid] = state

    raw_presets = out.get("presets")
    present_ids: set[str] = set()
    if isinstance(raw_presets, list):
        for preset in raw_presets:
            if not isinstance(preset, dict):
                continue
            pid = str(preset.get("instanceId") or "")
            present_ids.add(pid)
            if pid in managed_preset_state:
                preset["enabled"] = managed_preset_state[pid]

    groups = out.get("groups")
    if isinstance(groups, dict) and isinstance(groups.get("groupings"), list):
        groupings = groups["groupings"]
        for g_idx, spec_group in (
            (0, [s for s in AIOSTREAMS_PACKS if s.group_index == 0]),
            (1, [s for s in AIOSTREAMS_PACKS if s.group_index == 1]),
        ):
            if g_idx >= len(groupings) or not isinstance(groupings[g_idx], dict):
                continue
            existing_addons = [str(a) for a in groupings[g_idx].get("addons", []) if isinstance(a, str)]
            for sp in spec_group:
                if sp.id not in wanted:
                    continue
                for pid in sp.preset_ids:
                    if pid in present_ids and pid not in existing_addons:
                        existing_addons.append(pid)
            groupings[g_idx]["addons"] = existing_addons

    return Ok(out)


def sync_watchlist_folder_recipe(
    recipe_doc: Mapping[str, Any],
    *,
    debrid_provider: str,
    release_alerts_enabled: bool,
    core_debrid_enabled: bool = True,
) -> JsonDict:
    """
    Keeps a tenant's `fld-watchlist-library.json` folder recipe aligned with the active Debrid
    provider (`aiostreams::library.<provider>`) and whether `release_alerts` (`ito`) is enabled.
    """
    out: JsonDict = copy.deepcopy(dict(recipe_doc))
    folder = out.get("folder")
    if not isinstance(folder, dict):
        return out
    spec = DEBRID_PROVIDERS.get(debrid_provider, DEBRID_PROVIDERS["torbox"])
    sources: list[JsonDict] = []
    if core_debrid_enabled:
        sources.append(
            {
                "addon": "aiostreams",
                "type": "movie",
                "preset": "lib",
                "catalog": f"aiostreams::library.{spec.id}",
                "title": f"{spec.label} Cloud Library",
            }
        )
    if release_alerts_enabled:
        sources.extend(copy.deepcopy(list(_ITSOUT_WATCHLIST_SOURCES)))
    folder["sources"] = sources
    return out


def sync_aiomanager_cached_debrid_catalog(
    aiomanager_doc: Mapping[str, Any], provider_id: str
) -> JsonDict:
    """
    Rewrites `aiostreams::library.<old>` to `aiostreams::library.<provider_id>` inside cached
    AIOStreams manifests in `aiomanager.json` so offline compilation (`sw validate` / Studio preview)
    succeeds immediately before the first live manifest refresh.
    """
    spec = DEBRID_PROVIDERS.get(provider_id.strip().lower())
    if spec is None:
        return copy.deepcopy(dict(aiomanager_doc))

    def _rewrite_manifest(mf: Any) -> None:
        if not isinstance(mf, dict) or not isinstance(mf.get("catalogs"), list):
            return
        for cat in mf["catalogs"]:
            if not isinstance(cat, dict):
                continue
            cid = str(cat.get("id") or "")
            if _LIBRARY_CAT_RE.search(cid):
                cat["id"] = _LIBRARY_CAT_RE.sub(f"aiostreams::library.{spec.id}", cid)
                cat["name"] = f"{spec.label} Cloud Library"

    out: JsonDict = copy.deepcopy(dict(aiomanager_doc))
    addons_sec = out.get("addons")
    if isinstance(addons_sec, dict) and isinstance(addons_sec.get("savedAddons"), list):
        for sa in addons_sec["savedAddons"]:
            if isinstance(sa, dict):
                _rewrite_manifest(sa.get("manifest"))
    for acc in out.get("accounts", []) if isinstance(out.get("accounts"), list) else []:
        if isinstance(acc, dict) and isinstance(acc.get("addons"), list):
            for addon in acc["addons"]:
                if isinstance(addon, dict):
                    _rewrite_manifest(addon.get("manifest"))
    return out


def prepare_aiostreams_for_push(aiostreams_doc: Mapping[str, Any], env: EnvMap) -> JsonDict:
    """
    Strips `enabled: false` presets whose `{{SECRET_*}}` placeholders are unset in `env` before
    secret injection, so disabling an optional pack (`release_alerts`, `dual_subtitles`) never
    forces the user to populate unused API keys—while preserving disabled presets that have no
    missing secrets (zero diff drift on fully-keyed tenants).
    """
    out: JsonDict = copy.deepcopy(dict(aiostreams_doc))
    raw_presets = out.get("presets")
    if not isinstance(raw_presets, list):
        return out

    dropped_ids: set[str] = set()
    kept_presets: list[Any] = []
    for preset in raw_presets:
        if isinstance(preset, Mapping) and preset.get("enabled") is False:
            match inject_secrets(preset, env):
                case Err():
                    pid = str(preset.get("instanceId") or "")
                    if pid:
                        dropped_ids.add(pid)
                    continue
                case Ok():
                    pass
        kept_presets.append(preset)

    if not dropped_ids:
        return out

    out["presets"] = kept_presets
    groups = out.get("groups")
    if isinstance(groups, dict) and isinstance(groups.get("groupings"), list):
        for g in groups["groupings"]:
            if isinstance(g, dict) and isinstance(g.get("addons"), list):
                g["addons"] = [a for a in g["addons"] if a not in dropped_ids]

    cat_mods = out.get("catalogModifications")
    if isinstance(cat_mods, list):
        out["catalogModifications"] = [
            m
            for m in cat_mods
            if not (
                isinstance(m, Mapping)
                and any(str(m.get("id") or "").startswith(pid) for pid in dropped_ids)
            )
        ]
    return out
