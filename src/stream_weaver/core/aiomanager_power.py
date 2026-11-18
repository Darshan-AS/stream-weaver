"""
Pure power-user configuration controls for `aiomanager.json` and profile `extra_addons`.

Summarizes and updates AIOManager Cloud Sync, Autopilot failover interval, webhook status,
Shared Key Vault count, and managed household account connection status, and reconciles
standalone Stremio/Nuvio `manifest.json` extra addons on `Profile.extra_addons` while
preserving `cinemeta`'s `cinemetaConfig` metadata.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlparse

from stream_weaver.core.errors import ConfigValidationError
from stream_weaver.core.profiles import ExtraAddon
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.types import EnvMap, JsonDict

_ADDON_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
DEFAULT_SYNC_INTERVAL_MINUTES = 15

DEFAULT_CINEMETA_CONFIG: Mapping[str, bool] = {
    "removeSearch": True,
    "removeStandardCatalogs": True,
    "removeMeta": False,
}


def _err(msg: str) -> Err[ConfigValidationError]:
    return Err(ConfigValidationError(message=msg, service="aiomanager"))


def _norm_url_identity(url: str) -> str:
    cleaned = url.strip()
    if cleaned.lower().startswith("stremio://"):
        cleaned = "https://" + cleaned[len("stremio://") :]
    return cleaned.removesuffix("/manifest.json").rstrip("/")


def _slugify_addon_name(raw: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", raw.strip().lower()).strip("-_")
    slug = re.sub(r"-{2,}", "-", slug)
    if not slug:
        return "addon"
    if not slug[0].isalpha():
        slug = f"a-{slug}"
    return slug[:32].rstrip("-_") or "addon"


def inspect_aiomanager_power(
    mgr_doc: Mapping[str, Any],
    env: EnvMap | None = None,
) -> JsonDict:
    """
    Summarizes AIOManager presence, `.env` sync configuration, Autopilot & webhook status,
    Key Vault count, and managed household account connection states.
    """
    env_map: Mapping[str, str] = env if env is not None else {}
    url = env_map.get("AIOMANAGER_URL", "").strip()
    uuid = env_map.get("AIOMANAGER_UUID", "").strip()
    sync_password = env_map.get("AIOMANAGER_SYNC_PASSWORD", "").strip()
    sync_configured = bool(url and uuid and sync_password)
    present = bool(mgr_doc)

    raw_autopilot = mgr_doc.get("autopilot")
    autopilot_sec: Mapping[str, Any] = raw_autopilot if isinstance(raw_autopilot, Mapping) else {}
    raw_failover = mgr_doc.get("failover")
    failover_sec: Mapping[str, Any] = raw_failover if isinstance(raw_failover, Mapping) else {}
    raw_settings = mgr_doc.get("settings")
    settings_sec: Mapping[str, Any] = raw_settings if isinstance(raw_settings, Mapping) else {}

    rules = failover_sec.get("rules")
    any_active_rule = (
        any(isinstance(r, Mapping) and bool(r.get("enabled", True)) for r in rules)
        if isinstance(rules, list)
        else False
    )
    if "enabled" in autopilot_sec:
        autopilot_enabled = bool(autopilot_sec.get("enabled"))
    elif "enabled" in failover_sec:
        autopilot_enabled = bool(failover_sec.get("enabled"))
    else:
        autopilot_enabled = any_active_rule

    raw_interval = (
        autopilot_sec.get("syncIntervalMinutes")
        or failover_sec.get("intervalMinutes")
        or settings_sec.get("syncIntervalMinutes")
    )
    sync_interval = (
        int(raw_interval)
        if isinstance(raw_interval, (int, float))
        and not isinstance(raw_interval, bool)
        and int(raw_interval) > 0
        else DEFAULT_SYNC_INTERVAL_MINUTES
    )

    raw_webhook = failover_sec.get("webhook")
    webhook_sec: Mapping[str, Any] = raw_webhook if isinstance(raw_webhook, Mapping) else {}
    webhook_url = str(webhook_sec.get("url") or "").strip()
    webhook_linked = bool(webhook_sec.get("enabled", False) and webhook_url)

    raw_vault = mgr_doc.get("vault")
    vault_count = len(raw_vault) if isinstance(raw_vault, list) else 0

    raw_addons_sec = mgr_doc.get("addons")
    addons_sec: Mapping[str, Any] = raw_addons_sec if isinstance(raw_addons_sec, Mapping) else {}
    saved_addons = addons_sec.get("savedAddons")
    saved_addons_count = len(saved_addons) if isinstance(saved_addons, list) else 0

    managed_accounts: list[JsonDict] = []
    raw_accounts = mgr_doc.get("accounts")
    if isinstance(raw_accounts, list):
        for acc in raw_accounts:
            if not isinstance(acc, Mapping):
                continue
            raw_acc_addons = acc.get("addons")
            acc_addons: list[Any] = raw_acc_addons if isinstance(raw_acc_addons, list) else []
            addons_count = 0
            for a in acc_addons:
                if isinstance(a, Mapping):
                    a_flags = a.get("flags")
                    if not isinstance(a_flags, Mapping) or bool(a_flags.get("enabled", True)):
                        addons_count += 1
            raw_conns = acc.get("connections")
            conns: list[Any] = raw_conns if isinstance(raw_conns, list) else []
            nuvio_connected = any(
                isinstance(c, Mapping)
                and str(c.get("platform") or "").lower() == "nuvio"
                and bool(c.get("enabled", True))
                for c in conns
            )
            stremio_connected = any(
                isinstance(c, Mapping)
                and str(c.get("platform") or "").lower() == "stremio"
                and bool(c.get("enabled", True))
                for c in conns
            )
            managed_accounts.append(
                {
                    "id": str(acc.get("id") or ""),
                    "name": str(acc.get("name") or ""),
                    "emoji": str(acc.get("emoji") or "🎬"),
                    "accentColor": str(acc.get("accentColor") or "#3b82f6"),
                    "addonsCount": addons_count,
                    "nuvioConnected": nuvio_connected,
                    "stremioConnected": stremio_connected,
                }
            )

    return {
        "present": present,
        "syncConfigured": sync_configured,
        "url": url,
        "uuid": uuid,
        "autopilotEnabled": autopilot_enabled,
        "syncIntervalMinutes": sync_interval,
        "webhookLinked": webhook_linked,
        "webhookUrl": webhook_url,
        "vaultCount": vault_count,
        "savedAddonsCount": saved_addons_count,
        "managedAccounts": managed_accounts,
    }


def apply_aiomanager_power(
    mgr_doc: Mapping[str, Any],
    spec: Mapping[str, Any],
) -> Result[JsonDict, ConfigValidationError]:
    """
    Applies Autopilot, sync interval, auto-sync, and webhook flags onto `aiomanager.json`
    without losing `accounts`, `addons.savedAddons`, `vault`, or existing failover rules.
    """
    out: JsonDict = copy.deepcopy(dict(mgr_doc))
    cur = inspect_aiomanager_power(out)

    had_autopilot_key = "autopilot" in mgr_doc
    autopilot_doc: JsonDict = (
        copy.deepcopy(dict(out["autopilot"])) if isinstance(out.get("autopilot"), Mapping) else {}
    )
    autopilot_touched = False

    eff_autopilot = cur["autopilotEnabled"]
    if "autopilotEnabled" in spec:
        eff_autopilot = bool(spec["autopilotEnabled"])
        autopilot_touched = True

    eff_interval = cur["syncIntervalMinutes"]
    if "syncIntervalMinutes" in spec:
        raw_iv = spec["syncIntervalMinutes"]
        if isinstance(raw_iv, bool) or not isinstance(raw_iv, (int, float)):
            return _err("`syncIntervalMinutes` must be an integer between 1 and 1440.")
        iv = int(raw_iv)
        if iv < 1 or iv > 1440:
            return _err("`syncIntervalMinutes` must be between 1 and 1440 minutes.")
        eff_interval = iv
        autopilot_touched = True

    if "autoSync" in spec:
        autopilot_doc["autoSync"] = bool(spec["autoSync"])
        autopilot_touched = True

    if autopilot_touched:
        is_default_noop = (
            not had_autopilot_key
            and "autoSync" not in spec
            and eff_autopilot == cur["autopilotEnabled"]
            and eff_interval == cur["syncIntervalMinutes"]
        )
        if not is_default_noop:
            if not out.get("version"):
                out["version"] = "2.0.0"
            out.setdefault("accounts", [])
            autopilot_doc["enabled"] = eff_autopilot
            autopilot_doc["syncIntervalMinutes"] = eff_interval
            out["autopilot"] = autopilot_doc

    if "webhookUrl" in spec or "webhookEnabled" in spec or "webhookLinked" in spec:
        raw_cur_fo = out.get("failover")
        failover: JsonDict = (
            copy.deepcopy(dict(raw_cur_fo))
            if isinstance(raw_cur_fo, Mapping)
            else {"rules": [], "webhook": {"url": "", "enabled": False, "updatedAt": 0}}
        )
        raw_cur_wh = failover.get("webhook")
        webhook: JsonDict = (
            dict(raw_cur_wh)
            if isinstance(raw_cur_wh, Mapping)
            else {"url": "", "enabled": False, "updatedAt": 0}
        )
        if "webhookUrl" in spec:
            w_url = str(spec["webhookUrl"] or "").strip()
            if w_url and not w_url.startswith(("http://", "https://", "{{")):
                return _err("`webhookUrl` must be an HTTP(S) URL or empty.")
            webhook["url"] = w_url
        if "webhookEnabled" in spec:
            webhook["enabled"] = bool(spec["webhookEnabled"]) and bool(webhook.get("url"))
        elif "webhookLinked" in spec:
            if not spec["webhookLinked"]:
                webhook["enabled"] = False
            elif webhook.get("url"):
                webhook["enabled"] = True
        elif "webhookUrl" in spec:
            webhook["enabled"] = bool(webhook["url"])
        failover["webhook"] = webhook
        if failover != out.get("failover"):
            out["failover"] = failover

    return Ok(out)


def inspect_extra_addons(existing_extras: Sequence[ExtraAddon]) -> list[JsonDict]:
    """Serializes a `Profile.extra_addons` sequence for Studio UI inspection."""
    return [
        {
            "name": addon.name,
            "transportUrl": addon.transport_url,
            "customName": addon.custom_name or addon.name,
            "enabled": addon.enabled,
            "metadata": copy.deepcopy(dict(addon.metadata)),
            "isCinemeta": addon.name == "cinemeta" or "cinemeta" in addon.transport_url.lower(),
        }
        for addon in existing_extras
    ]


def normalize_extra_addons(
    existing_extras: Sequence[ExtraAddon],
    specs: Sequence[Mapping[str, Any] | ExtraAddon],
) -> Result[tuple[ExtraAddon, ...], ConfigValidationError]:
    """
    Reconciles standalone Stremio/Nuvio `manifest.json` addons onto a `Profile.extra_addons`
    tuple—supporting add, remove, and `enabled` toggles—while preserving `cinemeta`'s
    `cinemetaConfig` metadata (and any existing addon `metadata` when omitted by the caller).
    """
    existing_by_name: dict[str, ExtraAddon] = {a.name: a for a in existing_extras}
    existing_by_url: dict[str, ExtraAddon] = {
        _norm_url_identity(a.transport_url): a for a in existing_extras
    }

    normalized: list[ExtraAddon] = []
    used_names: set[str] = set()

    for idx, raw_item in enumerate(specs):
        if isinstance(raw_item, ExtraAddon):
            item_map: JsonDict = {
                "name": raw_item.name,
                "transportUrl": raw_item.transport_url,
                "customName": raw_item.custom_name,
                "metadata": copy.deepcopy(dict(raw_item.metadata)),
                "enabled": raw_item.enabled,
            }
        elif isinstance(raw_item, Mapping):
            if raw_item.get("remove") is True:
                continue
            item_map = dict(raw_item)
        else:
            return _err(f"extraAddons[{idx}] must be an ExtraAddon or object.")

        raw_url = str(
            item_map.get("transportUrl")
            or item_map.get("manifestUrl")
            or item_map.get("url")
            or ""
        ).strip()
        if raw_url.lower().startswith("stremio://"):
            raw_url = "https://" + raw_url[len("stremio://") :]
        if not raw_url or not raw_url.startswith(("http://", "https://", "{{")):
            return _err(
                f"extraAddons[{idx}] `transportUrl` must be an HTTP(S) or stremio:// URL, got {raw_url!r}"
            )
        if not raw_url.startswith("{{"):
            parsed = urlparse(raw_url)
            if not parsed.netloc:
                return _err(f"extraAddons[{idx}] `transportUrl` is missing a hostname: {raw_url!r}")

        url_ident = _norm_url_identity(raw_url)
        raw_name = str(item_map.get("name") or "").strip()
        raw_custom_name = item_map.get("customName")
        custom_name = (
            str(raw_custom_name).strip()
            if isinstance(raw_custom_name, str) and raw_custom_name.strip()
            else None
        )

        matched_existing = (
            existing_by_name.get(raw_name.lower())
            if raw_name
            else None
        ) or existing_by_url.get(url_ident)

        if raw_name:
            base_slug = raw_name if _ADDON_NAME_RE.match(raw_name) else _slugify_addon_name(raw_name)
        elif matched_existing is not None:
            base_slug = matched_existing.name
        elif custom_name:
            base_slug = _slugify_addon_name(custom_name)
        else:
            host = urlparse(raw_url).hostname or "addon"
            base_slug = _slugify_addon_name(host.split(".")[0])

        slug = base_slug
        suffix = 2
        while slug in used_names:
            prefix = base_slug[: max(1, 32 - len(str(suffix)) - 1)].rstrip("-_")
            slug = f"{prefix}-{suffix}"
            suffix += 1
        used_names.add(slug)

        if custom_name is None and matched_existing is not None:
            custom_name = matched_existing.custom_name

        raw_meta = item_map.get("metadata")
        if isinstance(raw_meta, Mapping):
            meta_out: JsonDict = copy.deepcopy(dict(raw_meta))
            if (
                matched_existing is not None
                and "cinemetaConfig" in matched_existing.metadata
                and "cinemetaConfig" not in meta_out
            ):
                meta_out["cinemetaConfig"] = copy.deepcopy(
                    matched_existing.metadata["cinemetaConfig"]
                )
        elif matched_existing is not None:
            meta_out = copy.deepcopy(dict(matched_existing.metadata))
        else:
            meta_out = {}

        is_cinemeta = slug == "cinemeta" or "v3-cinemeta.strem.io" in url_ident.lower()
        if is_cinemeta and "cinemetaConfig" not in meta_out:
            existing_cm = existing_by_name.get("cinemeta")
            if existing_cm is not None and "cinemetaConfig" in existing_cm.metadata:
                meta_out["cinemetaConfig"] = copy.deepcopy(existing_cm.metadata["cinemetaConfig"])
            else:
                meta_out["cinemetaConfig"] = dict(DEFAULT_CINEMETA_CONFIG)

        enabled = bool(item_map.get("enabled", True))
        normalized.append(
            ExtraAddon(
                name=slug,
                transport_url=raw_url,
                custom_name=custom_name,
                metadata=meta_out,
                enabled=enabled,
            )
        )

    return Ok(tuple(normalized))
