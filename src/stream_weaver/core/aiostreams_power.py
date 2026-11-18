"""
Pure power-user configuration controls for `aiostreams.json`.

Provides lossless domain lenses and transformations for:
1. Multi-Debrid & Usenet service stacking (`torbox`, `realdebrid`, `alldebrid`, `premiumize`, `easynews`)
2. Custom Stremio addon URL installation without clobbering Stream Weaver's managed custom presets
3. Stream card formatter presets (`prism`, `compact_tv`, `minimal`, plus `custom` detection) with simulator samples
4. Advanced stream filter rules (file-size caps, quality/3D exclusions, keywords, codec priority, HDR policy, seeders, RPDB posters)
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from stream_weaver.core.aiostreams_packs import DEBRID_PROVIDERS
from stream_weaver.core.errors import ConfigValidationError
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.secrets import SECRET_PLACEHOLDER_RE
from stream_weaver.core.types import EnvMap, JsonDict


@dataclass(frozen=True, slots=True)
class MultiDebridProviderSpec:
    id: str
    label: str
    short_name: str
    secret_keys: tuple[str, ...]
    credential_fields: Mapping[str, str]
    signup_url: str


STACKABLE_DEBRID_PROVIDERS: Mapping[str, MultiDebridProviderSpec] = {
    "torbox": MultiDebridProviderSpec(
        id="torbox",
        label="TorBox",
        short_name="TB",
        secret_keys=("SECRET_TORBOX_API_KEY",),
        credential_fields={"apiKey": "{{SECRET_TORBOX_API_KEY}}"},
        signup_url="https://torbox.app/settings",
    ),
    "realdebrid": MultiDebridProviderSpec(
        id="realdebrid",
        label="Real-Debrid",
        short_name="RD",
        secret_keys=("SECRET_RD_API_KEY",),
        credential_fields={"apiKey": "{{SECRET_RD_API_KEY}}"},
        signup_url="https://real-debrid.com/apitoken",
    ),
    "alldebrid": MultiDebridProviderSpec(
        id="alldebrid",
        label="AllDebrid",
        short_name="AD",
        secret_keys=("SECRET_ALLDEBRID_API_KEY",),
        credential_fields={"apiKey": "{{SECRET_ALLDEBRID_API_KEY}}"},
        signup_url="https://alldebrid.com/apikeys/",
    ),
    "premiumize": MultiDebridProviderSpec(
        id="premiumize",
        label="Premiumize",
        short_name="PM",
        secret_keys=("SECRET_PREMIUMIZE_API_KEY",),
        credential_fields={"apiKey": "{{SECRET_PREMIUMIZE_API_KEY}}"},
        signup_url="https://www.premiumize.me/account",
    ),
    "easynews": MultiDebridProviderSpec(
        id="easynews",
        label="Easynews (Usenet)",
        short_name="EN",
        secret_keys=("SECRET_EASYNEWS_USERNAME", "SECRET_EASYNEWS_PASSWORD"),
        credential_fields={
            "username": "{{SECRET_EASYNEWS_USERNAME}}",
            "password": "{{SECRET_EASYNEWS_PASSWORD}}",
        },
        signup_url="https://account.easynews.com",
    ),
}

_LIBRARY_CAT_RE = re.compile(r"aiostreams::library\.([a-z0-9_]+)")
_STRELINGO_PRESET_ID_RE = re.compile(r"^sl\d+$")
_INSTANCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,23}$")

MANAGED_CUSTOM_PRESET_IDS: frozenset[str] = frozenset(
    {"ssp", "ito", "bso", "plv", "etv", "eth", "swc"}
)
CUSTOM_ADDON_GROUPS: tuple[str, ...] = ("primary", "fallback", "subtitles")
VALID_ADDON_RESOURCES: frozenset[str] = frozenset(
    {"stream", "subtitles", "catalog", "meta", "addon_catalog"}
)

STREAM_FORMATTER_PRESETS: Mapping[str, JsonDict] = {
    "prism": {
        "id": "prism",
        "label": "Prism — Detailed Tech Badges",
        "description": "Full technical readout with resolution, HDR/DV, codec, audio channels, bitrate, size, seeds, and release group.",
        "name": (
            '{stream.library::istrue["☁️ "||""]}'
            '{stream.library::isfalse::and::service.cached::istrue["⚡ "||""]}'
            '{service.cached::isfalse["⏳ "||""]}'
            '{service.shortName::exists["[{service.shortName}] "||""]}'
            "{addon.name}"
            '{stream.resolution::exists[" · {stream.resolution}"||""]}'
            '{stream.visualTags::exists[" {stream.visualTags::join(\' \')}"||""]}'
            '{stream.encode::exists[" · {stream.encode}"||""]}'
            '{stream.audioTags::exists[" {stream.audioTags::join(\' \')}"||""]}'
            '{stream.audioChannels::exists[" {stream.audioChannels::join(\' \')}"||""]}'
        ),
        "descriptionTemplate": (
            '{stream.size::>0["◧ {stream.size::sbytes}"||""]}'
            '{stream.bitrate::exists[" · ⟡ {stream.bitrate::sbitrate}"||""]}'
            '{stream.duration::>0[" · ◷ {stream.duration::time(\'[%-Hh ]%-Mm\')}"||""]}'
            '{stream.seeders::>0::and::service.cached::istrue::isfalse[" · ⇅ {stream.seeders} seeds"||""]}\n'
            '{stream.quality::exists["◈ {stream.quality}"||""]}'
            '{stream.releaseGroup::exists[" · ⌗ {stream.releaseGroup}"||""]}\n'
            '{stream.languages::exists["♫ {stream.languages::join(\' · \')}"||""]}'
            '{stream.uSubtitles::exists[" · ☰ {stream.uSubtitles::join(\' · \')}"||""]}\n'
            '{stream.filename::exists["▤ {stream.filename::truncate(84)}"||""]}'
        ),
        "sampleName": "⚡ [TB] Torrentio · 2160p DV HDR10+ · HEVC Atmos 7.1",
        "sampleDescription": (
            "◧ 42.8 GB · ⟡ 58.4 Mbps · ◷ 2h 46m\n"
            "◈ BluRay REMUX · ⌗ FraMeSToR\n"
            "♫ English · Japanese · ☰ EN · JA\n"
            "▤ Dune.Part.Two.2024.2160p.UHD.BluRay.REMUX.DV.HDR10+.TrueHD.7.1.Atmos-FraMeSToR.mkv"
        ),
    },
    "compact_tv": {
        "id": "compact_tv",
        "label": "Compact TV — Clean 2-Line",
        "description": "Living-room 10-foot TV layout: crisp 2-line specs without wrapping filenames.",
        "name": (
            '{stream.library::istrue["☁️ "||""]}'
            '{stream.library::isfalse::and::service.cached::istrue["⚡ "||""]}'
            '{service.cached::isfalse["⏳ "||""]}'
            '{service.shortName::exists["[{service.shortName}] "||""]}'
            '{stream.resolution::exists["{stream.resolution}"||"Stream"]}'
            '{stream.visualTags::exists[" · {stream.visualTags::join(\' \')}"||""]}'
        ),
        "descriptionTemplate": (
            '{stream.size::>0["{stream.size::sbytes} · "||""]}'
            '{stream.quality::exists["{stream.quality} · "||""]}'
            '{stream.encode::exists["{stream.encode} "||""]}'
            '{stream.audioTags::exists["{stream.audioTags::join(\' \')} "||""]}'
            '{stream.audioChannels::exists["{stream.audioChannels::join(\' \')}"||""]}\n'
            "{addon.name}"
            '{stream.languages::exists[" · {stream.languages::join(\'/\')}"||""]}'
            '{stream.releaseGroup::exists[" · {stream.releaseGroup}"||""]}'
        ),
        "sampleName": "⚡ [TB] 2160p · DV HDR10+",
        "sampleDescription": (
            "42.8 GB · BluRay REMUX · HEVC Atmos 7.1\n"
            "Torrentio · English/Japanese · FraMeSToR"
        ),
    },
    "minimal": {
        "id": "minimal",
        "label": "Minimal — Fast Minimalist",
        "description": "Zero-clutter one-line summary showing instant cache status, resolution, file size, and source.",
        "name": (
            '{service.cached::istrue["⚡ "||"⏳ "]}'
            '{stream.resolution::exists["{stream.resolution}"||"HD"]}'
            '{stream.visualTags::exists[" {stream.visualTags::first}"||""]}'
        ),
        "descriptionTemplate": (
            '{stream.size::>0["{stream.size::sbytes} · "||""]}'
            '{stream.quality::exists["{stream.quality} · "||""]}'
            "{addon.name}"
        ),
        "sampleName": "⚡ 2160p DV",
        "sampleDescription": "42.8 GB · BluRay REMUX · Torrentio",
    },
}

_CUSTOM_FORMATTER_SAMPLE_NAME = "⚡ Torrentio · ◆◆◆◆◇"
_CUSTOM_FORMATTER_SAMPLE_DESC = (
    "◧ 42.8 GB · ⟡ 58.4 Mbps · ◷ 2h 46m\n"
    "⌗ FraMeSToR · ◈ BluRay REMUX\n"
    "♫ English ✦ · Japanese\n"
    "▤ Dune.Part.Two.2024.2160p.UHD.BluRay.REMUX.DV.HDR10+.TrueHD.7.1.Atmos-FraMeSToR.mkv"
)

VALID_EXCLUDABLE_QUALITIES: tuple[str, ...] = (
    "CAM",
    "TS",
    "TC",
    "SCR",
    "3D",
    "DVDRip",
    "HDTV",
    "HDRip",
    "HC HD-Rip",
    "WEBRip",
    "WEB-DL",
    "BluRay",
    "BluRay REMUX",
    "Unknown",
)
_QUALITY_BY_LOWER: Mapping[str, str] = {q.lower(): q for q in VALID_EXCLUDABLE_QUALITIES}

VALID_STREAM_CODECS: tuple[str, ...] = ("AV1", "HEVC", "AVC", "XviD", "DivX")
_CODEC_ALIASES: Mapping[str, str] = {
    "av1": "AV1",
    "hevc": "HEVC",
    "h265": "HEVC",
    "h.265": "HEVC",
    "x265": "HEVC",
    "avc": "AVC",
    "h264": "AVC",
    "h.264": "AVC",
    "x264": "AVC",
    "xvid": "XviD",
    "divx": "DivX",
}

HDR_POLICIES: tuple[str, ...] = ("any", "prefer_dv_hdr", "sdr_only")
_HDR_VISUAL_TAGS: tuple[str, ...] = (
    "HDR+DV",
    "HDR10+",
    "HDR10",
    "DV",
    "HDR",
    "DV Only",
    "HDR Only",
    "HLG",
)
_HDR_VISUAL_SET: frozenset[str] = frozenset(_HDR_VISUAL_TAGS)
_PREFERRED_HDR_ORDER: tuple[str, ...] = ("HDR+DV", "HDR10+", "HDR10", "DV", "HDR")
_BYTES_PER_GIB: int = 1024**3


def _err(msg: str) -> Err[ConfigValidationError]:
    return Err(ConfigValidationError(message=msg, service="aiostreams"))


# ---------------------------------------------------------------------------
# 1. Multi-Debrid & Usenet Stacking
# ---------------------------------------------------------------------------


def _serialize_service_item(
    spec: MultiDebridProviderSpec,
    *,
    enabled: bool,
    priority: int,
    env_map: Mapping[str, str],
) -> JsonDict:
    configured = all(bool(env_map.get(k, "").strip()) for k in spec.secret_keys)
    return {
        "id": spec.id,
        "label": spec.label,
        "shortName": spec.short_name,
        "signupUrl": spec.signup_url,
        "enabled": enabled,
        "priority": priority,
        "secretKeys": list(spec.secret_keys),
        "configured": configured,
    }


def inspect_debrid_services(
    doc: Mapping[str, Any],
    env: EnvMap | None = None,
) -> JsonDict:
    """
    Inspects stacked Debrid and Usenet providers in `doc["services"]`, preserving their
    priority order and reporting secret configuration status against `env`.
    """
    env_map: Mapping[str, str] = env if env is not None else {}
    raw_services = doc.get("services")
    active_ids: list[str] = []
    if isinstance(raw_services, list):
        for entry in raw_services:
            if not isinstance(entry, Mapping):
                continue
            sid = str(entry.get("id") or "").strip().lower()
            if sid in STACKABLE_DEBRID_PROVIDERS and entry.get("enabled", True) and sid not in active_ids:
                active_ids.append(sid)

    active_items: list[JsonDict] = []
    for idx, sid in enumerate(active_ids, start=1):
        active_items.append(
            _serialize_service_item(
                STACKABLE_DEBRID_PROVIDERS[sid],
                enabled=True,
                priority=idx,
                env_map=env_map,
            )
        )

    available_items: list[JsonDict] = [copy.deepcopy(item) for item in active_items]
    next_prio = len(available_items) + 1
    for sid, spec in STACKABLE_DEBRID_PROVIDERS.items():
        if sid in active_ids:
            continue
        available_items.append(
            _serialize_service_item(
                spec,
                enabled=False,
                priority=next_prio,
                env_map=env_map,
            )
        )
        next_prio += 1

    return {
        "active": active_items,
        "available": available_items,
    }


def apply_debrid_services(
    doc: Mapping[str, Any],
    specs: Sequence[Mapping[str, Any] | str],
) -> Result[JsonDict, ConfigValidationError]:
    """
    Applies an ordered stack of Debrid / Usenet services to `aiostreams.json`, preserving
    existing credential placeholders (`{{SECRET_...}}`) and rewiring the Cloud Library
    catalog modification to the highest-priority Debrid service.
    """
    existing_by_id: dict[str, JsonDict] = {}
    raw_existing = doc.get("services")
    if isinstance(raw_existing, list):
        for svc in raw_existing:
            if isinstance(svc, Mapping):
                sid = str(svc.get("id") or "").strip().lower()
                if sid and sid not in existing_by_id:
                    existing_by_id[sid] = copy.deepcopy(dict(svc))

    ordered_enabled: list[MultiDebridProviderSpec] = []
    seen_ids: set[str] = set()
    for raw_item in specs:
        if isinstance(raw_item, str):
            sid = raw_item.strip().lower()
            enabled = True
        elif isinstance(raw_item, Mapping):
            sid = str(raw_item.get("id") or "").strip().lower()
            enabled = bool(raw_item.get("enabled", True))
        else:
            return _err("Each debrid service entry must be a provider id or object.")

        if not sid:
            return _err("Debrid service entry is missing `id`.")
        spec = STACKABLE_DEBRID_PROVIDERS.get(sid)
        if spec is None:
            return _err(
                f"Unsupported debrid/usenet provider `{sid}` "
                f"(choose from: {', '.join(STACKABLE_DEBRID_PROVIDERS)})"
            )
        if sid in seen_ids:
            continue
        seen_ids.add(sid)
        if enabled:
            ordered_enabled.append(spec)

    if not ordered_enabled:
        return _err("Select at least one enabled Debrid or Usenet provider.")

    new_services: list[JsonDict] = []
    for spec in ordered_enabled:
        prev = existing_by_id.get(spec.id)
        svc_entry: JsonDict = copy.deepcopy(prev) if prev is not None else {"id": spec.id}
        svc_entry["id"] = spec.id
        svc_entry["enabled"] = True
        prev_creds = svc_entry.get("credentials")
        creds: JsonDict = dict(prev_creds) if isinstance(prev_creds, Mapping) else {}
        for field_name, placeholder in spec.credential_fields.items():
            cur_val = creds.get(field_name)
            if not isinstance(cur_val, str) or not cur_val.strip():
                creds[field_name] = placeholder
        svc_entry["credentials"] = creds
        new_services.append(svc_entry)

    out: JsonDict = copy.deepcopy(dict(doc))
    out["services"] = new_services

    primary_debrid = next((sp.id for sp in ordered_enabled if sp.id in DEBRID_PROVIDERS), None)
    if primary_debrid is not None:
        debrid_spec = DEBRID_PROVIDERS[primary_debrid]
        cat_mods = out.get("catalogModifications")
        if isinstance(cat_mods, list):
            for mod in cat_mods:
                if not isinstance(mod, dict):
                    continue
                cid = str(mod.get("id") or "")
                if _LIBRARY_CAT_RE.search(cid):
                    mod["id"] = _LIBRARY_CAT_RE.sub(f"aiostreams::library.{debrid_spec.id}", cid)
                    mod["name"] = f"{debrid_spec.label} Cloud Library"

    return Ok(out)


def required_multi_debrid_secrets(doc: Mapping[str, Any]) -> tuple[str, ...]:
    """
    Returns all `SECRET_*` keys strictly required by enabled services in `doc["services"]`.
    """
    keys: set[str] = set()
    raw_services = doc.get("services")
    if isinstance(raw_services, list):
        for svc in raw_services:
            if not isinstance(svc, Mapping) or not svc.get("enabled", True):
                continue
            sid = str(svc.get("id") or "").strip().lower()
            spec = STACKABLE_DEBRID_PROVIDERS.get(sid)
            if spec is not None:
                keys.update(spec.secret_keys)
            creds = svc.get("credentials")
            if isinstance(creds, Mapping):
                for val in creds.values():
                    if isinstance(val, str):
                        keys.update(m.group(1) for m in SECRET_PLACEHOLDER_RE.finditer(val))
    if not keys:
        keys.add("SECRET_TORBOX_API_KEY")
    return tuple(sorted(keys))


# ---------------------------------------------------------------------------
# 2. Custom Addon URL Installer
# ---------------------------------------------------------------------------


def is_managed_custom_preset_id(instance_id: str) -> bool:
    """True when `instance_id` belongs to a Stream Weaver managed custom preset."""
    return instance_id in MANAGED_CUSTOM_PRESET_IDS or bool(_STRELINGO_PRESET_ID_RE.match(instance_id))


def _is_user_custom_preset(preset: Mapping[str, Any]) -> bool:
    if str(preset.get("type") or "") != "custom":
        return False
    pid = str(preset.get("instanceId") or "").strip()
    return bool(pid) and not is_managed_custom_preset_id(pid)


def _group_sets(doc: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    groups = doc.get("groups")
    groupings = groups.get("groupings") if isinstance(groups, Mapping) else None
    g1: set[str] = set()
    g2: set[str] = set()
    if isinstance(groupings, list):
        if len(groupings) >= 1 and isinstance(groupings[0], Mapping):
            g1 = {str(x) for x in groupings[0].get("addons", []) if isinstance(x, str)}
        if len(groupings) >= 2 and isinstance(groupings[1], Mapping):
            g2 = {str(x) for x in groupings[1].get("addons", []) if isinstance(x, str)}
    return g1, g2


def _normalize_manifest_url(raw_url: str) -> Result[str, ConfigValidationError]:
    cleaned = raw_url.strip()
    if cleaned.lower().startswith("stremio://"):
        cleaned = "https://" + cleaned[len("stremio://") :]
    if not cleaned.startswith(("http://", "https://", "{{")):
        return _err(f"Custom addon `manifestUrl` must be an HTTP(S) or stremio:// URL, got {raw_url!r}")
    if not cleaned.startswith("{{"):
        parsed = urlparse(cleaned)
        if not parsed.netloc:
            return _err(f"Custom addon `manifestUrl` is missing a hostname: {raw_url!r}")
        if not parsed.path.endswith(".json") and not parsed.query:
            cleaned = cleaned.rstrip("/") + "/manifest.json"
    return Ok(cleaned)


def _derive_custom_instance_id(name: str, url: str, reserved: set[str]) -> str:
    seed = re.sub(r"[^a-z0-9]+", "", name.lower())[:6]
    if not seed:
        host = urlparse(url).hostname or "addon"
        seed = re.sub(r"[^a-z0-9]+", "", host.lower())[:6] or "addon"
    base = f"u{seed}"[:10]
    if not is_managed_custom_preset_id(base) and base not in reserved:
        return base
    idx = 1
    while True:
        cand = f"{base[:8]}{idx}"
        if not is_managed_custom_preset_id(cand) and cand not in reserved:
            return cand
        idx += 1


def inspect_custom_stream_addons(doc: Mapping[str, Any]) -> list[JsonDict]:
    """
    Lists user-installed custom Stremio presets (`type == "custom"`) in `aiostreams.json`,
    ignoring Stream Weaver's managed custom presets (`sl1..sl99`, `ssp`, `ito`, `bso`, `plv`, `etv`, `eth`, `swc`).
    """
    g1_ids, g2_ids = _group_sets(doc)
    raw_presets = doc.get("presets")
    if not isinstance(raw_presets, list):
        return []

    out: list[JsonDict] = []
    for preset in raw_presets:
        if not isinstance(preset, Mapping) or not _is_user_custom_preset(preset):
            continue
        pid = str(preset.get("instanceId") or "").strip()
        raw_opts = preset.get("options")
        opts: Mapping[str, Any] = raw_opts if isinstance(raw_opts, Mapping) else {}
        raw_res = opts.get("resources")
        resources = (
            [str(r) for r in raw_res if isinstance(r, str) and str(r).strip()]
            if isinstance(raw_res, list)
            else []
        )
        if pid in g1_ids:
            group = "primary"
        elif pid in g2_ids:
            group = "fallback"
        else:
            group = "subtitles"
        if not resources:
            resources = ["subtitles"] if group == "subtitles" else ["stream"]
        raw_timeout = opts.get("timeout")
        timeout = int(raw_timeout) if isinstance(raw_timeout, (int, float)) and int(raw_timeout) > 0 else 10000
        out.append(
            {
                "instanceId": pid,
                "name": str(opts.get("name") or pid),
                "manifestUrl": str(opts.get("manifestUrl") or ""),
                "enabled": bool(preset.get("enabled", True)),
                "group": group,
                "resources": resources,
                "timeout": timeout,
            }
        )
    return out


def apply_custom_stream_addons(
    doc: Mapping[str, Any],
    addons: Sequence[Mapping[str, Any]],
) -> Result[JsonDict, ConfigValidationError]:
    """
    Reconciles user-installed custom presets in `aiostreams.json` without touching built-in or
    Stream Weaver managed presets, and wires each custom `instanceId` into Group 1 (`primary`),
    Group 2 (`fallback`), or untiered (`subtitles`).
    """
    out: JsonDict = copy.deepcopy(dict(doc))
    raw_presets = out.get("presets")
    existing_presets: list[JsonDict] = (
        [p for p in raw_presets if isinstance(p, dict)] if isinstance(raw_presets, list) else []
    )

    existing_user_by_id: dict[str, JsonDict] = {}
    protected_ids: set[str] = set(MANAGED_CUSTOM_PRESET_IDS)
    kept_presets: list[JsonDict] = []
    for preset in existing_presets:
        pid = str(preset.get("instanceId") or "").strip()
        if _is_user_custom_preset(preset):
            existing_user_by_id[pid] = preset
        else:
            if pid:
                protected_ids.add(pid)
            kept_presets.append(preset)

    used_ids: set[str] = set(protected_ids)
    new_custom_presets: list[JsonDict] = []
    group_by_id: dict[str, str] = {}

    for idx, item in enumerate(addons):
        if not isinstance(item, Mapping):
            return _err(f"customStreamAddons[{idx}] must be an object.")
        raw_url = str(item.get("manifestUrl") or item.get("url") or "").strip()
        if not raw_url:
            return _err(f"customStreamAddons[{idx}] requires a non-empty `manifestUrl`.")
        match _normalize_manifest_url(raw_url):
            case Err() as err:
                return err
            case Ok(manifest_url):
                pass

        raw_group = str(item.get("group") or "").strip().lower()
        raw_resources = item.get("resources")
        parsed_resources = (
            [str(r).strip() for r in raw_resources if str(r).strip()]
            if isinstance(raw_resources, Sequence) and not isinstance(raw_resources, (str, bytes))
            else []
        )
        if not raw_group:
            raw_group = "subtitles" if parsed_resources == ["subtitles"] else "primary"
        if raw_group not in CUSTOM_ADDON_GROUPS:
            return _err(
                f"Unsupported custom addon group `{raw_group}` "
                f"(choose from: {', '.join(CUSTOM_ADDON_GROUPS)})"
            )

        if not parsed_resources:
            parsed_resources = ["subtitles"] if raw_group == "subtitles" else ["stream"]
        bad_res = [r for r in parsed_resources if r not in VALID_ADDON_RESOURCES]
        if bad_res:
            return _err(f"Unsupported Stremio resource(s) {bad_res} on custom addon #{idx + 1}.")

        raw_timeout = item.get("timeout", 10000)
        if isinstance(raw_timeout, bool) or not isinstance(raw_timeout, (int, float)):
            return _err(f"customStreamAddons[{idx}].timeout must be an integer in milliseconds.")
        timeout = int(raw_timeout)
        if timeout < 500 or timeout > 120000:
            return _err(f"customStreamAddons[{idx}].timeout must be between 500 and 120000 ms.")

        name = str(item.get("name") or "").strip()
        raw_id = str(item.get("instanceId") or "").strip()
        if raw_id:
            if is_managed_custom_preset_id(raw_id) or raw_id in protected_ids:
                return _err(f"Custom addon `instanceId` `{raw_id}` is reserved by a managed preset.")
            if not _INSTANCE_ID_RE.match(raw_id):
                return _err(
                    f"Custom addon `instanceId` `{raw_id}` must match `{_INSTANCE_ID_RE.pattern}`."
                )
            if raw_id in used_ids:
                return _err(f"Duplicate custom addon `instanceId` `{raw_id}`.")
            inst_id = raw_id
        else:
            inst_id = _derive_custom_instance_id(name, manifest_url, used_ids)

        used_ids.add(inst_id)
        if not name:
            name = inst_id

        prev_preset = existing_user_by_id.get(inst_id)
        preset_doc: JsonDict = (
            copy.deepcopy(prev_preset) if prev_preset is not None else {"type": "custom"}
        )
        preset_doc["type"] = "custom"
        preset_doc["instanceId"] = inst_id
        preset_doc["enabled"] = bool(item.get("enabled", True))
        prev_opts = preset_doc.get("options")
        opts_doc: JsonDict = dict(prev_opts) if isinstance(prev_opts, Mapping) else {}
        opts_doc["name"] = name
        opts_doc["manifestUrl"] = manifest_url
        opts_doc["timeout"] = timeout
        opts_doc["resources"] = parsed_resources
        preset_doc["options"] = opts_doc

        new_custom_presets.append(preset_doc)
        group_by_id[inst_id] = raw_group

    old_user_ids = set(existing_user_by_id)
    if new_custom_presets or old_user_ids:
        out["presets"] = [*kept_presets, *new_custom_presets]

        groups = out.get("groups")
        if not isinstance(groups, dict):
            groups = {
                "enabled": True,
                "groupings": [
                    {"addons": [], "condition": "true"},
                    {"addons": [], "condition": "count(cached(totalStreams)) < 3"},
                ],
                "behaviour": "sequential",
                "onConditionFailure": "stop",
            }
            out["groups"] = groups
        groupings = groups.get("groupings")
        if not isinstance(groupings, list):
            groupings = []
            groups["groupings"] = groupings
        while len(groupings) < 2:
            default_cond = "true" if len(groupings) == 0 else "count(cached(totalStreams)) < 3"
            groupings.append({"addons": [], "condition": default_cond})

        managed_or_new_user_ids = old_user_ids | set(group_by_id)
        g1_list = [
            str(a)
            for a in (groupings[0].get("addons", []) if isinstance(groupings[0], Mapping) else [])
            if isinstance(a, str) and a not in managed_or_new_user_ids
        ]
        g2_list = [
            str(a)
            for a in (groupings[1].get("addons", []) if isinstance(groupings[1], Mapping) else [])
            if isinstance(a, str) and a not in managed_or_new_user_ids
        ]
        for pid, grp in group_by_id.items():
            if grp == "primary" and pid not in g1_list:
                g1_list.append(pid)
            elif grp == "fallback" and pid not in g2_list:
                g2_list.append(pid)

        if isinstance(groupings[0], dict):
            groupings[0]["addons"] = g1_list
        if isinstance(groupings[1], dict):
            groupings[1]["addons"] = g2_list

        removed_ids = old_user_ids - set(group_by_id)
        title_matching = out.get("titleMatching")
        if removed_ids and isinstance(title_matching, dict) and isinstance(title_matching.get("addons"), list):
            title_matching["addons"] = [a for a in title_matching["addons"] if a not in removed_ids]

    return Ok(out)


# ---------------------------------------------------------------------------
# 3. Stream Formatter Presets & Sample Renderer
# ---------------------------------------------------------------------------


def _extract_formatter_templates(doc: Mapping[str, Any]) -> tuple[str, str, str]:
    formatter = doc.get("formatter")
    if not isinstance(formatter, Mapping):
        return "", "", ""
    fmt_id = str(formatter.get("id") or "")
    defs = formatter.get("definitions")
    custom_def = defs.get("custom") if isinstance(defs, Mapping) else None
    if isinstance(custom_def, Mapping):
        return (
            fmt_id,
            str(custom_def.get("name") or ""),
            str(custom_def.get("description") or ""),
        )
    return fmt_id, "", ""


def inspect_stream_formatter(doc: Mapping[str, Any]) -> JsonDict:
    """
    Detects which `STREAM_FORMATTER_PRESETS` preset (`prism`, `compact_tv`, `minimal`) is active
    in `doc["formatter"]`, falling back to `"custom"` when the user has another formatter, and
    returns live simulator sample card strings for every preset.
    """
    _fmt_id, cur_name, cur_desc = _extract_formatter_templates(doc)
    active_id = "custom"
    sample_name = _CUSTOM_FORMATTER_SAMPLE_NAME
    sample_desc = _CUSTOM_FORMATTER_SAMPLE_DESC

    for pid, preset in STREAM_FORMATTER_PRESETS.items():
        if cur_name == preset["name"] and cur_desc == preset["descriptionTemplate"]:
            active_id = pid
            sample_name = str(preset["sampleName"])
            sample_desc = str(preset["sampleDescription"])
            break

    presets_list: list[JsonDict] = [copy.deepcopy(p) for p in STREAM_FORMATTER_PRESETS.values()]
    return {
        "active": active_id,
        "sampleName": sample_name,
        "sampleDescription": sample_desc,
        "currentDefinition": {
            "name": cur_name,
            "description": cur_desc,
        },
        "presets": presets_list,
    }


def apply_stream_formatter(
    doc: Mapping[str, Any],
    preset_id: str,
) -> Result[JsonDict, ConfigValidationError]:
    """
    Applies one of `STREAM_FORMATTER_PRESETS` (`prism`, `compact_tv`, `minimal`) onto
    `doc["formatter"]`, or preserves an existing custom formatter when `preset_id == "custom"`.
    """
    pid = preset_id.strip().lower()
    out: JsonDict = copy.deepcopy(dict(doc))
    if pid == "custom":
        if isinstance(out.get("formatter"), Mapping):
            return Ok(out)
        pid = "prism"

    preset = STREAM_FORMATTER_PRESETS.get(pid)
    if preset is None:
        valid = [*STREAM_FORMATTER_PRESETS.keys(), "custom"]
        return _err(
            f"Unsupported stream formatter preset `{preset_id}` (choose from: {', '.join(valid)})"
        )

    out["formatter"] = {
        "id": "custom",
        "definitions": {
            "custom": {
                "name": str(preset["name"]),
                "description": str(preset["descriptionTemplate"]),
            }
        },
    }
    return Ok(out)


# ---------------------------------------------------------------------------
# 4. Advanced Stream Filter Rules
# ---------------------------------------------------------------------------


def _extract_max_size_gb(doc: Mapping[str, Any]) -> int | None:
    size_cfg = doc.get("size")
    if not isinstance(size_cfg, Mapping):
        return None
    global_sec = size_cfg.get("global")
    if isinstance(global_sec, Mapping):
        for media_key in ("movies", "series"):
            bounds = global_sec.get(media_key)
            if (
                isinstance(bounds, list)
                and len(bounds) >= 2
                and isinstance(bounds[1], (int, float))
                and bounds[1] > 0
            ):
                raw_bytes = int(bounds[1])
                if raw_bytes % 1_000_000_000 == 0 and raw_bytes % _BYTES_PER_GIB != 0:
                    return max(1, raw_bytes // 1_000_000_000)
                return max(1, round(raw_bytes / _BYTES_PER_GIB))
    return None


def inspect_advanced_stream_filters(doc: Mapping[str, Any]) -> JsonDict:
    """
    Inspects advanced stream filter rules from `aiostreams.json`:
    `maxSizeGb`, `excludeQualities` (including `"3D"` visual tag), `excludeKeywords`,
    `preferredCodecs`, `hdrPolicy`, `minSeeders`, and `rpdbPostersEnabled`.
    """
    raw_excl_q = doc.get("excludedQualities")
    excl_qualities: list[str] = (
        [str(q) for q in raw_excl_q if isinstance(q, str) and str(q).strip()]
        if isinstance(raw_excl_q, list)
        else []
    )
    raw_excl_v = doc.get("excludedVisualTags")
    excl_visual = (
        [str(v) for v in raw_excl_v if isinstance(v, str)]
        if isinstance(raw_excl_v, list)
        else []
    )
    if "3D" in excl_visual and "3D" not in excl_qualities:
        excl_qualities.append("3D")

    raw_kw = doc.get("excludedKeywords")
    excl_keywords: list[str] = (
        [str(k) for k in raw_kw if isinstance(k, str) and str(k).strip()]
        if isinstance(raw_kw, list)
        else []
    )

    raw_encodes = doc.get("preferredEncodes")
    pref_codecs: list[str] = []
    if isinstance(raw_encodes, list):
        for enc in raw_encodes:
            if isinstance(enc, str) and enc != "Unknown" and enc not in pref_codecs:
                pref_codecs.append(enc)

    raw_pref_v = doc.get("preferredVisualTags")
    pref_visual = (
        {str(v) for v in raw_pref_v if isinstance(v, str)}
        if isinstance(raw_pref_v, list)
        else set()
    )
    excl_visual_set = set(excl_visual)
    if excl_visual_set & _HDR_VISUAL_SET:
        hdr_policy = "sdr_only"
    elif pref_visual & _HDR_VISUAL_SET:
        hdr_policy = "prefer_dv_hdr"
    else:
        hdr_policy = "any"

    min_seeders = 0
    for range_key in ("requiredSeederRange", "seederRange"):
        sr = doc.get(range_key)
        if isinstance(sr, list) and len(sr) >= 1 and isinstance(sr[0], (int, float)) and int(sr[0]) > 0:
            min_seeders = int(sr[0])
            break

    rpdb_key = str(doc.get("rpdbApiKey") or "").strip()
    rpdb_enabled = bool(doc.get("posterService") == "rpdb" and rpdb_key)

    return {
        "maxSizeGb": _extract_max_size_gb(doc),
        "excludeQualities": excl_qualities,
        "excludeKeywords": excl_keywords,
        "preferredCodecs": pref_codecs,
        "hdrPolicy": hdr_policy,
        "minSeeders": min_seeders,
        "rpdbPostersEnabled": rpdb_enabled,
        "qualityOptions": list(VALID_EXCLUDABLE_QUALITIES),
        "codecOptions": list(VALID_STREAM_CODECS),
        "hdrPolicyOptions": list(HDR_POLICIES),
    }


def apply_advanced_stream_filters(
    doc: Mapping[str, Any],
    filters: Mapping[str, Any],
) -> Result[JsonDict, ConfigValidationError]:
    """
    Patch-safely applies advanced stream filter rules onto `aiostreams.json`.
    Only filter keys present in `filters` are mutated.
    """
    out: JsonDict = copy.deepcopy(dict(doc))

    if "maxSizeGb" in filters:
        raw_sz = filters["maxSizeGb"]
        if raw_sz is None or raw_sz == "" or raw_sz == 0:
            out.pop("size", None)
        else:
            if isinstance(raw_sz, bool) or not isinstance(raw_sz, (int, float)):
                return _err("`maxSizeGb` must be an integer (GB) or null.")
            sz_gb = int(raw_sz)
            if sz_gb < 0 or sz_gb > 500:
                return _err("`maxSizeGb` must be between 0 and 500 GB.")
            if sz_gb == 0:
                out.pop("size", None)
            else:
                max_bytes = sz_gb * _BYTES_PER_GIB
                prev_size = out.get("size")
                size_doc: JsonDict = copy.deepcopy(dict(prev_size)) if isinstance(prev_size, Mapping) else {}
                prev_global = size_doc.get("global")
                global_doc: JsonDict = dict(prev_global) if isinstance(prev_global, Mapping) else {}
                global_doc["movies"] = [0, max_bytes]
                global_doc["series"] = [0, max_bytes]
                size_doc["global"] = global_doc
                out["size"] = size_doc

    qual_input = (
        filters["excludeQualities"]
        if "excludeQualities" in filters
        else filters.get("excludedQualities")
    )
    if qual_input is not None:
        if not isinstance(qual_input, Sequence) or isinstance(qual_input, (str, bytes)):
            return _err("`excludeQualities` must be a list of quality strings.")
        norm_qualities: list[str] = []
        exclude_3d = False
        for raw_q in qual_input:
            tok = str(raw_q).strip()
            if not tok:
                continue
            canon = _QUALITY_BY_LOWER.get(tok.lower())
            if canon is None:
                return _err(
                    f"Unsupported quality tag `{tok}` "
                    f"(choose from: {', '.join(VALID_EXCLUDABLE_QUALITIES)})"
                )
            if canon == "3D":
                exclude_3d = True
            elif canon not in norm_qualities:
                norm_qualities.append(canon)
        out["excludedQualities"] = norm_qualities

        cur_ev = [str(v) for v in out.get("excludedVisualTags", []) if isinstance(v, str)]
        if exclude_3d and "3D" not in cur_ev:
            cur_ev = ["3D", *cur_ev]
        elif not exclude_3d and "3D" in cur_ev:
            cur_ev = [v for v in cur_ev if v != "3D"]
        out["excludedVisualTags"] = cur_ev

    kw_input = (
        filters["excludeKeywords"]
        if "excludeKeywords" in filters
        else filters.get("excludedKeywords")
    )
    if kw_input is not None:
        if not isinstance(kw_input, Sequence) or isinstance(kw_input, (str, bytes)):
            return _err("`excludeKeywords` must be a list of strings.")
        seen_kw: list[str] = []
        for raw_k in kw_input:
            kw = str(raw_k).strip()
            if kw and kw not in seen_kw:
                seen_kw.append(kw)
        out["excludedKeywords"] = seen_kw

    if "preferredCodecs" in filters:
        raw_codecs = filters["preferredCodecs"]
        if not isinstance(raw_codecs, Sequence) or isinstance(raw_codecs, (str, bytes)):
            return _err("`preferredCodecs` must be a list of video codec names.")
        chosen_codecs: list[str] = []
        for raw_c in raw_codecs:
            tok = str(raw_c).strip()
            if not tok or tok == "Unknown":
                continue
            canon_c = _CODEC_ALIASES.get(tok.lower())
            if canon_c is None:
                return _err(
                    f"Unsupported video codec `{tok}` "
                    f"(choose from: {', '.join(VALID_STREAM_CODECS)})"
                )
            if canon_c not in chosen_codecs:
                chosen_codecs.append(canon_c)
        prev_encodes = out.get("preferredEncodes")
        encodes_list: list[Any] = prev_encodes if isinstance(prev_encodes, list) else ["Unknown"]
        had_unknown = "Unknown" in encodes_list
        out["preferredEncodes"] = (
            [*chosen_codecs, "Unknown"] if (chosen_codecs and had_unknown) else chosen_codecs
        )

    if "hdrPolicy" in filters:
        policy = str(filters["hdrPolicy"] or "").strip().lower()
        if policy not in HDR_POLICIES:
            return _err(
                f"Unsupported `hdrPolicy` `{filters['hdrPolicy']}` "
                f"(choose from: {', '.join(HDR_POLICIES)})"
            )
        cur_ev = [str(v) for v in out.get("excludedVisualTags", []) if isinstance(v, str)]
        cur_pv = [str(v) for v in out.get("preferredVisualTags", []) if isinstance(v, str)]
        non_hdr_ev = [v for v in cur_ev if v not in _HDR_VISUAL_SET]
        non_hdr_pv = [v for v in cur_pv if v not in _HDR_VISUAL_SET]

        if policy == "sdr_only":
            out["excludedVisualTags"] = [*non_hdr_ev, *_HDR_VISUAL_TAGS]
            out["preferredVisualTags"] = non_hdr_pv
        elif policy == "prefer_dv_hdr":
            out["excludedVisualTags"] = non_hdr_ev
            out["preferredVisualTags"] = [*_PREFERRED_HDR_ORDER, *non_hdr_pv]
        else:  # "any"
            out["excludedVisualTags"] = non_hdr_ev
            out["preferredVisualTags"] = non_hdr_pv

    if "minSeeders" in filters:
        raw_ms = filters["minSeeders"]
        if raw_ms is None:
            raw_ms = 0
        if isinstance(raw_ms, bool) or not isinstance(raw_ms, (int, float)):
            return _err("`minSeeders` must be a non-negative integer.")
        ms = int(raw_ms)
        if ms < 0 or ms > 10000:
            return _err("`minSeeders` must be between 0 and 10000.")
        if ms == 0:
            out["seederRangeTypes"] = []
            out.pop("requiredSeederRange", None)
            out.pop("seederRange", None)
        else:
            prev_sr = out.get("requiredSeederRange")
            upper = (
                int(prev_sr[1])
                if isinstance(prev_sr, list)
                and len(prev_sr) >= 2
                and isinstance(prev_sr[1], (int, float))
                and int(prev_sr[1]) > ms
                else 100000
            )
            out["requiredSeederRange"] = [ms, upper]
            out["seederRangeTypes"] = ["p2p", "uncached"]

    if "rpdbPostersEnabled" in filters:
        rpdb_on = bool(filters["rpdbPostersEnabled"])
        if rpdb_on:
            cur_key = str(out.get("rpdbApiKey") or "").strip()
            out["rpdbApiKey"] = cur_key or "{{SECRET_RPDB_API_KEY}}"
            out["posterService"] = "rpdb"
            out.setdefault("usePosterRedirectApi", True)
        else:
            out["rpdbApiKey"] = ""
            if out.get("posterService") == "rpdb":
                out["posterService"] = "none"

    return Ok(out)
