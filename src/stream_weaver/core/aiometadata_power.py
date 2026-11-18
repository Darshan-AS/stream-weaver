"""
Pure power-user configuration controls for `aiometadata.json`.

Exposes metadata router provider selection (`movieProvider`, `seriesProvider`, `animeProvider`),
search engine toggles + AI Search (`gemini` / `openrouter`), poster rating engine switching
(`rpdb`, `topposters`, `postersplus`, `none`), catalog overrides (toggle, rename, reorder),
and exact dynamic secret requirements.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

from stream_weaver.core.errors import ConfigValidationError
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.types import JsonDict

MOVIE_METADATA_PROVIDERS: tuple[str, ...] = ("tmdb", "tvdb", "imdb")
SERIES_METADATA_PROVIDERS: tuple[str, ...] = ("tmdb", "tvdb", "tvmaze", "imdb")
ANIME_METADATA_PROVIDERS: tuple[str, ...] = (
    "tvdb",
    "tmdb",
    "mal",
    "anilist",
    "kitsu",
    "anidb",
    "imdb",
)
ANIME_ID_PROVIDERS: tuple[str, ...] = ("kitsu", "mal", "anilist", "anidb", "tvdb", "imdb")
AI_SEARCH_PROVIDERS: tuple[str, ...] = ("gemini", "openrouter")
POSTER_PROVIDERS: tuple[str, ...] = ("rpdb", "topposters", "postersplus", "none")

DEFAULT_POSTERSPLUS_URL_PATTERN = (
    "https://postersplus.slokker.cc/poster?tmdb_id={tmdb_id?}&imdb_id={imdb_id?}"
    "&stremio_id={id}&type={type}&shape={shape}&pc=stremio_tv_nuvio"
)


def _err(msg: str) -> Err[ConfigValidationError]:
    return Err(ConfigValidationError(message=msg, service="aiometadata"))


def _detect_poster_provider(doc: Mapping[str, Any]) -> str:
    raw_prov = str(doc.get("posterRatingProvider") or "").strip()
    raw_lower = raw_prov.lower()
    if raw_lower == "rpdb":
        return "rpdb"
    if raw_lower in ("top", "topposter", "topposters"):
        return "topposters"
    if raw_lower == "custom":
        pattern = str(doc.get("customPosterUrlPattern") or "").lower()
        if "postersplus" in pattern or pattern:
            return "postersplus"
        return "none"
    if raw_lower in ("none", "", "disabled", "off"):
        return "none"
    return raw_lower if raw_lower in POSTER_PROVIDERS else "none"


def _detect_ai_search(doc: Mapping[str, Any]) -> tuple[bool, str]:
    search = doc.get("search")
    search_map: Mapping[str, Any] = search if isinstance(search, Mapping) else {}
    eng = search_map.get("engineEnabled")
    eng_map: Mapping[str, Any] = eng if isinstance(eng, Mapping) else {}
    api_keys = doc.get("apiKeys")
    keys_map: Mapping[str, Any] = api_keys if isinstance(api_keys, Mapping) else {}

    ai_enabled = bool(
        search_map.get("ai_enabled", False)
        or eng_map.get("gemini.search", False)
        or eng_map.get("openrouter.search", False)
    )
    raw_ai_prov = str(search_map.get("ai_provider") or "").strip().lower()
    if raw_ai_prov in AI_SEARCH_PROVIDERS:
        ai_provider = raw_ai_prov
    elif bool(str(keys_map.get("openrouter") or "").strip()) and not bool(
        str(keys_map.get("gemini") or "").strip()
    ):
        ai_provider = "openrouter"
    else:
        ai_provider = "gemini"
    return ai_enabled, ai_provider


def inspect_aiometadata_power(doc: Mapping[str, Any]) -> JsonDict:
    """
    Inspects metadata providers, search engines, AI search state, poster rating provider,
    and catalog summaries from `aiometadata.json`.
    """
    raw_providers = doc.get("providers")
    prov_map: Mapping[str, Any] = raw_providers if isinstance(raw_providers, Mapping) else {}

    raw_search = doc.get("search")
    search_map: Mapping[str, Any] = raw_search if isinstance(raw_search, Mapping) else {}
    raw_engines = search_map.get("engineEnabled")
    engines_out: dict[str, bool] = (
        {str(k): bool(v) for k, v in raw_engines.items()}
        if isinstance(raw_engines, Mapping)
        else {}
    )
    ai_enabled, ai_provider = _detect_ai_search(doc)
    poster_provider = _detect_poster_provider(doc)

    catalogs_summary: list[JsonDict] = []
    raw_cats = doc.get("catalogs")
    if isinstance(raw_cats, list):
        for cat in raw_cats:
            if not isinstance(cat, Mapping):
                continue
            cid = str(cat.get("id") or "")
            if not cid:
                continue
            catalogs_summary.append(
                {
                    "id": cid,
                    "name": str(cat.get("name") or cid),
                    "type": str(cat.get("type") or "movie"),
                    "source": str(cat.get("source") or "tmdb"),
                    "enabled": bool(cat.get("enabled", True)),
                    "showInHome": bool(cat.get("showInHome", False)),
                }
            )

    return {
        "movieProvider": str(prov_map.get("movie") or "tmdb"),
        "seriesProvider": str(prov_map.get("series") or "tmdb"),
        "animeProvider": str(prov_map.get("anime") or "tvdb"),
        "animeIdProvider": str(prov_map.get("anime_id_provider") or "kitsu"),
        "searchEngines": engines_out,
        "aiSearchEnabled": ai_enabled,
        "aiSearchProvider": ai_provider,
        "posterProvider": poster_provider,
        "catalogs": catalogs_summary,
        "providerOptions": {
            "movie": list(MOVIE_METADATA_PROVIDERS),
            "series": list(SERIES_METADATA_PROVIDERS),
            "anime": list(ANIME_METADATA_PROVIDERS),
            "animeId": list(ANIME_ID_PROVIDERS),
        },
        "aiSearchProviderOptions": list(AI_SEARCH_PROVIDERS),
        "posterProviderOptions": list(POSTER_PROVIDERS),
    }


def _apply_catalog_overrides(
    raw_cats: list[JsonDict],
    overrides: Any,
    explicit_order: Any,
) -> Result[list[JsonDict], ConfigValidationError]:
    cats = [copy.deepcopy(c) for c in raw_cats]

    def _find_matching_indices(cid: str, ctype: str | None) -> list[int]:
        if "::" in cid and not ctype:
            cid, ctype = cid.split("::", 1)
        matches: list[int] = []
        for idx, c in enumerate(cats):
            if str(c.get("id") or "") != cid:
                continue
            if ctype and str(c.get("type") or "") != ctype:
                continue
            matches.append(idx)
        return matches

    if isinstance(overrides, Mapping):
        for raw_key, ov in overrides.items():
            if not isinstance(ov, Mapping):
                return _err(f"catalogOverrides[{raw_key!r}] must be an object.")
            idxs = _find_matching_indices(str(raw_key), str(ov["type"]) if "type" in ov else None)
            for idx in idxs:
                if "enabled" in ov:
                    cats[idx]["enabled"] = bool(ov["enabled"])
                if "showInHome" in ov:
                    cats[idx]["showInHome"] = bool(ov["showInHome"])
                if "name" in ov and str(ov["name"]).strip():
                    cats[idx]["name"] = str(ov["name"]).strip()
    elif isinstance(overrides, Sequence) and not isinstance(overrides, (str, bytes)):
        matched_sequence: list[int] = []
        explicit_moves: list[tuple[int, int]] = []
        for item_idx, ov in enumerate(overrides):
            if not isinstance(ov, Mapping):
                return _err(f"catalogOverrides[{item_idx}] must be an object.")
            cid = str(ov.get("id") or ov.get("key") or "").strip()
            if not cid:
                return _err(f"catalogOverrides[{item_idx}] requires `id`.")
            ctype = str(ov["type"]).strip() if isinstance(ov.get("type"), str) and ov["type"].strip() else None
            idxs = _find_matching_indices(cid, ctype)
            for idx in idxs:
                if "enabled" in ov:
                    cats[idx]["enabled"] = bool(ov["enabled"])
                if "showInHome" in ov:
                    cats[idx]["showInHome"] = bool(ov["showInHome"])
                if "name" in ov and str(ov["name"]).strip():
                    cats[idx]["name"] = str(ov["name"]).strip()
                if idx not in matched_sequence:
                    matched_sequence.append(idx)
                pos = ov.get("position", ov.get("index"))
                if isinstance(pos, int) and not isinstance(pos, bool):
                    explicit_moves.append((idx, pos))

        if explicit_moves:
            for orig_idx, target_pos in explicit_moves:
                if 0 <= orig_idx < len(cats):
                    entry = cats.pop(orig_idx)
                    clamped = max(0, min(len(cats), target_pos))
                    cats.insert(clamped, entry)
        elif len(matched_sequence) >= 2 and matched_sequence != sorted(matched_sequence):
            sorted_slots = sorted(matched_sequence)
            reordered_entries = [cats[i] for i in matched_sequence]
            for slot, entry in zip(sorted_slots, reordered_entries, strict=True):
                cats[slot] = entry
    elif overrides is not None:
        return _err("`catalogOverrides` must be a list or mapping of catalog override specs.")

    if explicit_order is not None:
        if not isinstance(explicit_order, Sequence) or isinstance(explicit_order, (str, bytes)):
            return _err("`catalogOrder` must be a list of catalog ids or `{id, type}` objects.")
        front_indices: list[int] = []
        for ord_item in explicit_order:
            if isinstance(ord_item, str):
                idxs = _find_matching_indices(ord_item.strip(), None)
            elif isinstance(ord_item, Mapping):
                cid = str(ord_item.get("id") or "").strip()
                ctype = str(ord_item["type"]).strip() if isinstance(ord_item.get("type"), str) else None
                idxs = _find_matching_indices(cid, ctype)
            else:
                continue
            for i in idxs:
                if i not in front_indices:
                    front_indices.append(i)
        front_set = set(front_indices)
        cats = [cats[i] for i in front_indices] + [c for i, c in enumerate(cats) if i not in front_set]

    return Ok(cats)


def apply_aiometadata_power(
    doc: Mapping[str, Any],
    spec: Mapping[str, Any],
) -> Result[JsonDict, ConfigValidationError]:
    """
    Patch-safely applies AIOMetadata power settings (metadata router providers, search engines,
    AI search provider + secret placeholders, poster rating engine, and catalog overrides).
    """
    out: JsonDict = copy.deepcopy(dict(doc))

    raw_prov = out.get("providers")
    providers: JsonDict = dict(raw_prov) if isinstance(raw_prov, Mapping) else {}
    prov_changed = False

    if "movieProvider" in spec:
        mp = str(spec["movieProvider"] or "").strip().lower()
        if mp not in MOVIE_METADATA_PROVIDERS:
            return _err(
                f"Unsupported `movieProvider` `{spec['movieProvider']}` "
                f"(choose from: {', '.join(MOVIE_METADATA_PROVIDERS)})"
            )
        providers["movie"] = mp
        prov_changed = True

    if "seriesProvider" in spec:
        sp = str(spec["seriesProvider"] or "").strip().lower()
        if sp not in SERIES_METADATA_PROVIDERS:
            return _err(
                f"Unsupported `seriesProvider` `{spec['seriesProvider']}` "
                f"(choose from: {', '.join(SERIES_METADATA_PROVIDERS)})"
            )
        providers["series"] = sp
        prov_changed = True

    if "animeProvider" in spec:
        ap = str(spec["animeProvider"] or "").strip().lower()
        if ap not in ANIME_METADATA_PROVIDERS:
            return _err(
                f"Unsupported `animeProvider` `{spec['animeProvider']}` "
                f"(choose from: {', '.join(ANIME_METADATA_PROVIDERS)})"
            )
        providers["anime"] = ap
        prov_changed = True

    if "animeIdProvider" in spec:
        aidp = str(spec["animeIdProvider"] or "").strip().lower()
        if aidp not in ANIME_ID_PROVIDERS:
            return _err(
                f"Unsupported `animeIdProvider` `{spec['animeIdProvider']}` "
                f"(choose from: {', '.join(ANIME_ID_PROVIDERS)})"
            )
        providers["anime_id_provider"] = aidp
        prov_changed = True

    if prov_changed:
        out["providers"] = providers

    orig_api_keys = doc.get("apiKeys")
    had_openrouter_key = isinstance(orig_api_keys, Mapping) and "openrouter" in orig_api_keys
    raw_keys = out.get("apiKeys")
    api_keys: JsonDict = dict(raw_keys) if isinstance(raw_keys, Mapping) else {}

    orig_search = doc.get("search")
    had_ai_provider_key = isinstance(orig_search, Mapping) and "ai_provider" in orig_search
    raw_search = out.get("search")
    search_sec: JsonDict = copy.deepcopy(dict(raw_search)) if isinstance(raw_search, Mapping) else {"enabled": True}
    raw_eng = search_sec.get("engineEnabled")
    eng_enabled: JsonDict = dict(raw_eng) if isinstance(raw_eng, Mapping) else {}

    search_changed = False
    if "searchEngines" in spec:
        se_in = spec["searchEngines"]
        if not isinstance(se_in, Mapping):
            return _err("`searchEngines` must be an object mapping engine ids to booleans.")
        for eng_id, is_on in se_in.items():
            eng_enabled[str(eng_id)] = bool(is_on)
        search_changed = True

    cur_ai_on, cur_ai_prov = _detect_ai_search(out)
    if "aiSearchEnabled" in spec or "aiSearchProvider" in spec:
        eff_ai_on = bool(spec["aiSearchEnabled"]) if "aiSearchEnabled" in spec else cur_ai_on
        eff_ai_prov = (
            str(spec["aiSearchProvider"] or "").strip().lower()
            if "aiSearchProvider" in spec
            else cur_ai_prov
        )
        if eff_ai_prov not in AI_SEARCH_PROVIDERS:
            return _err(
                f"Unsupported `aiSearchProvider` `{spec.get('aiSearchProvider')}` "
                f"(choose from: {', '.join(AI_SEARCH_PROVIDERS)})"
            )
        search_sec["ai_enabled"] = eff_ai_on
        eng_enabled["gemini.search"] = eff_ai_on
        if eff_ai_on:
            search_sec["ai_provider"] = eff_ai_prov
            s_order = search_sec.get("searchOrder")
            if isinstance(s_order, list) and "gemini.search" not in s_order:
                s_order.append("gemini.search")
            if eff_ai_prov == "openrouter":
                api_keys["openrouter"] = str(api_keys.get("openrouter") or "").strip() or "{{SECRET_OPENROUTER_API_KEY}}"
                api_keys["gemini"] = ""
            else:
                api_keys["gemini"] = str(api_keys.get("gemini") or "").strip() or "{{SECRET_GEMINI_API_KEY}}"
                if had_openrouter_key:
                    api_keys["openrouter"] = ""
                else:
                    api_keys.pop("openrouter", None)
        else:
            if eff_ai_prov == "openrouter" or had_ai_provider_key:
                search_sec["ai_provider"] = eff_ai_prov
            else:
                search_sec.pop("ai_provider", None)
            api_keys["gemini"] = ""
            if had_openrouter_key:
                api_keys["openrouter"] = ""
            else:
                api_keys.pop("openrouter", None)
        search_changed = True

    if search_changed:
        search_sec["engineEnabled"] = eng_enabled
        out["search"] = search_sec

    if "posterProvider" in spec:
        pp = str(spec["posterProvider"] or "").strip().lower()
        if pp not in POSTER_PROVIDERS:
            return _err(
                f"Unsupported `posterProvider` `{spec['posterProvider']}` "
                f"(choose from: {', '.join(POSTER_PROVIDERS)})"
            )
        prev_pp = _detect_poster_provider(doc)
        if pp == "rpdb":
            out["posterRatingProvider"] = "rpdb"
            api_keys["rpdb"] = str(api_keys.get("rpdb") or "").strip() or "{{SECRET_RPDB_API_KEY}}"
            api_keys["topPoster"] = ""
        elif pp == "topposters":
            out["posterRatingProvider"] = "top"
            api_keys["topPoster"] = (
                str(api_keys.get("topPoster") or "").strip() or "{{SECRET_TOP_POSTER_API_KEY}}"
            )
        elif pp == "postersplus":
            out["posterRatingProvider"] = "custom"
            cur_pat = str(out.get("customPosterUrlPattern") or "").strip()
            if "postersplus" not in cur_pat.lower():
                out["customPosterUrlPattern"] = DEFAULT_POSTERSPLUS_URL_PATTERN
            if prev_pp == "topposters":
                api_keys["topPoster"] = ""
        else:  # "none"
            out["posterRatingProvider"] = "none"
            api_keys["rpdb"] = ""
            api_keys["topPoster"] = ""

    if search_changed or "posterProvider" in spec:
        out["apiKeys"] = api_keys

    if "catalogOverrides" in spec or "catalogOrder" in spec:
        raw_cats = out.get("catalogs")
        cat_list: list[JsonDict] = (
            [c for c in raw_cats if isinstance(c, dict)] if isinstance(raw_cats, list) else []
        )
        match _apply_catalog_overrides(
            cat_list,
            spec.get("catalogOverrides"),
            spec.get("catalogOrder"),
        ):
            case Err() as err:
                return err
            case Ok(updated_cats):
                out["catalogs"] = updated_cats

    return Ok(out)


def required_aiometadata_power_secrets(doc: Mapping[str, Any]) -> tuple[str, ...]:
    """
    Returns all `SECRET_*` keys strictly required by active AIOMetadata providers,
    AI search (`gemini` vs `openrouter`), poster rating engine (`rpdb` vs `topposters`),
    watch tracking, and enabled Home catalogs.
    """
    keys: set[str] = {"SECRET_TMDB_API_KEY", "SECRET_TVDB_API_KEY"}

    poster_prov = _detect_poster_provider(doc)
    if poster_prov == "rpdb":
        keys.add("SECRET_RPDB_API_KEY")
    elif poster_prov == "topposters":
        keys.add("SECRET_TOP_POSTER_API_KEY")

    ai_on, ai_prov = _detect_ai_search(doc)
    if ai_on:
        if ai_prov == "openrouter":
            keys.add("SECRET_OPENROUTER_API_KEY")
        else:
            keys.add("SECRET_GEMINI_API_KEY")

    if doc.get("traktWatchTracking") or doc.get("hideWatchedTrakt"):
        keys.add("SECRET_TRAKT_TOKEN_ID")
    if doc.get("mdblistWatchTracking") or doc.get("hideWatchedMdblist"):
        keys.add("SECRET_MDBLIST_API_KEY")

    raw_cats = doc.get("catalogs")
    if isinstance(raw_cats, list):
        for cat in raw_cats:
            if not isinstance(cat, Mapping) or not cat.get("enabled") or not cat.get("showInHome", True):
                continue
            cid = str(cat.get("id") or "")
            src = str(cat.get("source") or "")
            if src == "trakt" or cid.startswith("trakt."):
                keys.add("SECRET_TRAKT_TOKEN_ID")
            elif src == "mdblist" or cid.startswith("mdblist."):
                keys.add("SECRET_MDBLIST_API_KEY")

    return tuple(sorted(keys))
