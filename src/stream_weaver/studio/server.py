"""
HTTP server and pure request dispatcher for Stream Weaver Studio v2 (`sw studio`).

Supports two operating modes through one unified engine:
1. **Local GitOps Mode (`tenant_paths` set):** reads and writes the tenant directory on disk.
2. **Stateless Hosted Mode (`tenant_paths is None`):** materializes each request's browser-held
   `bundle` (`files` + `env`) inside an ephemeral temporary directory, executes the requested
   engine action, returns the updated `bundle` in JSON/NDJSON, and immediately wipes the temp
   directory—so no user configuration or API key is ever persisted on the server.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import shutil
import tempfile
import webbrowser
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from stream_weaver.core.instances import DEFAULT_INSTANCE
from stream_weaver.core.result import Err, Ok
from stream_weaver.core.secrets import parse_env_text
from stream_weaver.core.tenant import TenantPaths
from stream_weaver.core.types import JsonDict
from stream_weaver.effects.http import HttpClientProtocol
from stream_weaver.effects.storage import save_env_updates
from stream_weaver.engine import (
    apply_studio_mutation,
    build_custom_folder_sources,
    diff_workspace,
    export_tenant_zip_bytes,
    inspect_raw_document,
    inspect_tenant_state,
    inspect_workspace_addon_manifest,
    login_nuvio_and_list_profiles,
    login_stremio,
    manage_studio_profile,
    preview_catalog_items,
    provision_instances,
    pull_workspace,
    push_workspace,
    save_raw_document,
    verify_workspace_health,
    warm_workspace_catalogs,
)

_STATIC_DIR = Path(__file__).with_name("static").resolve()
_INDEX_HTML_PATH = _STATIC_DIR / "index.html"

_STATIC_CONTENT_TYPES: Mapping[str, str] = {
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
}


@dataclass(frozen=True, slots=True)
class StudioResponse:
    status: int
    content_type: str
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)


def _json_resp(status: int, payload: Mapping[str, Any]) -> StudioResponse:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return StudioResponse(status=status, content_type="application/json; charset=utf-8", body=raw)


def _err_resp(status: int, message: str) -> StudioResponse:
    return _json_resp(status, {"ok": False, "error": message})


def _serialize_bundle(paths: TenantPaths) -> JsonDict:
    """Packs a tenant directory's text files and `.env` map into a browser-storable JSON bundle."""
    files: dict[str, str] = {}
    for p in sorted(paths.root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(paths.root)
        if ".cache" in rel.parts or "__pycache__" in rel.parts or rel.name == ".env":
            continue
        if p.suffix in (".json", ".md", ".c4", ".example") or rel.name == ".gitignore":
            files[rel.as_posix()] = p.read_text(encoding="utf-8")
    env_text = paths.env_file.read_text(encoding="utf-8") if paths.env_file.is_file() else ""
    return {"files": files, "env": parse_env_text(env_text)}


def _materialize_bundle(
    target_dir: Path,
    template_root: Path,
    shared_library_root: Path,
    bundle: Mapping[str, Any] | None,
) -> TenantPaths:
    """Hydrates `target_dir` from `template_root`, overlaying browser `bundle` files + `env`."""
    shutil.copytree(template_root, target_dir, dirs_exist_ok=True)
    env_ex = target_dir / ".env.example"
    if env_ex.is_file() and not (target_dir / ".env").exists():
        shutil.copyfile(env_ex, target_dir / ".env")

    if isinstance(bundle, Mapping):
        raw_files = bundle.get("files")
        if isinstance(raw_files, Mapping):
            for rel_str, content in raw_files.items():
                if not isinstance(rel_str, str) or not isinstance(content, str):
                    continue
                rel_path = Path(rel_str)
                if rel_path.is_absolute() or ".." in rel_path.parts:
                    continue
                dest = target_dir / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8")
        raw_env = bundle.get("env")
        if isinstance(raw_env, Mapping):
            clean_env = {str(k): str(v) for k, v in raw_env.items() if str(v)}
            if clean_env:
                _ = save_env_updates(target_dir / ".env", clean_env)

    # Re-anchor `libraries` in tenant.json so the shared library resolves from the temp directory
    manifest_path = target_dir / "tenant.json"
    if manifest_path.is_file() and shared_library_root.is_dir():
        try:
            mdoc = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(mdoc, dict):
                mdoc["libraries"] = ["library", os.path.relpath(shared_library_root, target_dir)]
                manifest_path.write_text(json.dumps(mdoc, indent=2) + "\n", encoding="utf-8")
        except (OSError, ValueError):
            pass

    return TenantPaths(target_dir)


def _has_meaningful_mutation(mut: Mapping[str, Any]) -> bool:
    for k, v in mut.items():
        if k == "bundle":
            continue
        if k == "envUpdates":
            if isinstance(v, Mapping) and any(str(val).strip() for val in v.values()):
                return True
            continue
        if v is not None:
            return True
    return False


class StudioDispatcher:
    """Pure HTTP route dispatcher for Stream Weaver Studio (testable without network sockets)."""

    def __init__(
        self,
        *,
        tenant_paths: TenantPaths | None,
        template_root: Path | None = None,
        shared_library_root: Path | None = None,
        http_client_factory: Callable[[], HttpClientProtocol | None] | None = None,
        csrf_token: str | None = None,
    ) -> None:
        self._tenant_paths = tenant_paths
        self._template_root = template_root
        self._shared_library_root = shared_library_root
        self._http_client_factory = http_client_factory
        self._csrf_token = csrf_token if csrf_token is not None else secrets.token_urlsafe(24)

    @property
    def csrf_token(self) -> str:
        return self._csrf_token

    @property
    def is_stateless(self) -> bool:
        return self._tenant_paths is None

    def _mode_name(self) -> str:
        return "stateless" if self.is_stateless else "local"

    def _extra_libs(self) -> tuple[Path, ...]:
        if self._shared_library_root and self._shared_library_root.is_dir():
            return (self._shared_library_root,)
        return ()

    def _http_client(self) -> HttpClientProtocol | None:
        return self._http_client_factory() if self._http_client_factory is not None else None

    @contextmanager
    def _workspace_paths(self, payload: Mapping[str, Any]) -> Iterator[TenantPaths]:
        if self._tenant_paths is not None:
            yield self._tenant_paths
            return
        if self._template_root is None or not self._template_root.is_dir():
            raise RuntimeError("Stateless Studio requires a valid starter template directory.")
        shared_lib = self._shared_library_root or Path()
        with tempfile.TemporaryDirectory(prefix="sw-studio-") as tmp:
            bundle = payload.get("bundle") if isinstance(payload.get("bundle"), Mapping) else None
            yield _materialize_bundle(Path(tmp) / "home", self._template_root, shared_lib, bundle)

    def _parse_json_body(self, body: bytes) -> JsonDict | None:
        if not body:
            return {}
        try:
            parsed = json.loads(body.decode("utf-8"))
            return parsed if isinstance(parsed, dict) else None
        except (UnicodeDecodeError, ValueError):
            return None

    def _verify_csrf_and_origin(self, headers: Mapping[str, str] | None) -> str | None:
        hdrs = {k.lower(): v for k, v in (headers or {}).items()}
        provided = hdrs.get("x-studio-token", "")
        if not provided or not hmac.compare_digest(provided, self._csrf_token):
            return "Missing or invalid X-Studio-Token session header (reload Studio tab)"
        origin = hdrs.get("origin", "").strip()
        host = hdrs.get("host", "").strip()
        if origin and host:
            origin_host = urlsplit(origin).netloc
            if origin_host and origin_host.lower() != host.lower():
                return f"Cross-origin Studio request rejected (origin {origin_host} != host {host})"
        sec_site = hdrs.get("sec-fetch-site", "").strip().lower()
        if sec_site in ("cross-site",):
            return "Cross-site browser request to Studio rejected"
        return None

    def _serve_static_asset(self, raw_rel_path: str) -> StudioResponse:
        decoded = unquote(raw_rel_path)
        if (
            not decoded
            or "\x00" in decoded
            or "\\" in decoded
            or decoded.startswith("/")
            or ".." in decoded.split("/")
        ):
            return _err_resp(403, "Path traversal in static asset route rejected")

        rel = Path(decoded)
        if rel.is_absolute() or ".." in rel.parts:
            return _err_resp(403, "Path traversal in static asset route rejected")

        candidate = (_STATIC_DIR / rel).resolve()
        if not candidate.is_relative_to(_STATIC_DIR) or candidate == _STATIC_DIR:
            return _err_resp(403, "Path traversal in static asset route rejected")
        if not candidate.is_file():
            return _err_resp(404, f"Static asset not found: {decoded}")

        ctype = _STATIC_CONTENT_TYPES.get(candidate.suffix.lower())
        if ctype is None:
            return _err_resp(404, f"Unsupported static asset type: {candidate.suffix}")

        return StudioResponse(
            status=200,
            content_type=ctype,
            body=candidate.read_bytes(),
            headers={"X-Content-Type-Options": "nosniff"},
        )

    def dispatch_push_stream(
        self,
        body: bytes = b"",
        *,
        headers: Mapping[str, str] | None = None,
        enforce_csrf: bool = False,
        on_chunk: Callable[[bytes], None] | None = None,
    ) -> StudioResponse:
        """
        Executes a workspace push (with optional pre-mutation and catalog pre-warming), emitting
        newline-delimited JSON (`application/x-ndjson`) per `on_step` plus a final `done` frame.
        """
        if enforce_csrf:
            csrf_err = self._verify_csrf_and_origin(headers)
            if csrf_err is not None:
                return _err_resp(403, csrf_err)

        payload = self._parse_json_body(body)
        if payload is None:
            return _err_resp(400, "Request body must be a JSON object")

        target = str(payload.get("target") or "all")
        inst = str(payload.get("instance") or DEFAULT_INSTANCE).strip() or DEFAULT_INSTANCE
        prof = (
            str(payload["profile"])
            if isinstance(payload.get("profile"), str) and payload["profile"]
            else None
        )
        warm_catalogs = bool(payload.get("warmCatalogs", False))
        create_flag = bool(payload.get("create", False))

        chunks: list[bytes] = []

        def _emit(event: Mapping[str, Any]) -> None:
            raw_line = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
            chunks.append(raw_line)
            if on_chunk is not None:
                on_chunk(raw_line)

        try:
            with self._workspace_paths(payload) as paths:
                raw_mut = payload.get("mutation")
                if isinstance(raw_mut, Mapping) and _has_meaningful_mutation(raw_mut):
                    match apply_studio_mutation(
                        paths, raw_mut, extra_library_roots=self._extra_libs()
                    ):
                        case Err(m_err):
                            if on_chunk is None:
                                return _err_resp(400, str(m_err))
                            _emit({"type": "error", "ok": False, "error": str(m_err)})
                            return StudioResponse(
                                status=200,
                                content_type="application/x-ndjson",
                                body=b"".join(chunks),
                            )
                        case Ok():
                            _emit({"type": "step", "step": "Saved pending Studio edits"})

                match push_workspace(
                    paths,
                    target=target,
                    instance=inst,
                    profile=prof,
                    create=create_flag,
                    warm_catalogs=warm_catalogs,
                    on_step=lambda step_msg: _emit({"type": "step", "step": step_msg}),
                    extra_library_roots=self._extra_libs(),
                    http_client=self._http_client(),
                ):
                    case Err(err):
                        if on_chunk is None:
                            return _err_resp(400, str(err))
                        _emit({"type": "error", "ok": False, "error": str(err)})
                        return StudioResponse(
                            status=200,
                            content_type="application/x-ndjson",
                            body=b"".join(chunks),
                        )
                    case Ok(report):
                        done_event: JsonDict = {
                            "type": "done",
                            "ok": True,
                            "mode": self._mode_name(),
                            "steps": list(report.steps),
                            "state": report.snapshot,
                        }
                        if self.is_stateless:
                            done_event["bundle"] = _serialize_bundle(paths)
                        _emit(done_event)
                        return StudioResponse(
                            status=200,
                            content_type="application/x-ndjson",
                            body=b"".join(chunks),
                        )
        except RuntimeError as exc:
            if on_chunk is None:
                return _err_resp(500, str(exc))
            _emit({"type": "error", "ok": False, "error": str(exc)})
            return StudioResponse(
                status=200,
                content_type="application/x-ndjson",
                body=b"".join(chunks),
            )

    def dispatch(
        self,
        method: str,
        path: str,
        body: bytes = b"",
        *,
        headers: Mapping[str, str] | None = None,
        enforce_csrf: bool = False,
    ) -> StudioResponse:
        clean_path = path.split("?", 1)[0]

        if method == "GET" and clean_path in ("/", "/index.html"):
            raw_html = _INDEX_HTML_PATH.read_text(encoding="utf-8")
            meta_tag = f'  <meta name="sw-csrf-token" content="{self._csrf_token}" />\n</head>'
            injected = raw_html.replace("</head>", meta_tag, 1).encode("utf-8")
            return StudioResponse(
                status=200,
                content_type="text/html; charset=utf-8",
                body=injected,
                headers={"X-Frame-Options": "DENY"},
            )

        if method == "GET" and (clean_path == "/static" or clean_path.startswith("/static/")):
            rel_asset = clean_path.removeprefix("/static").lstrip("/") if clean_path.startswith("/static/") else ""
            if clean_path.startswith("/static/") and clean_path[len("/static/"):].startswith("/"):
                return _err_resp(403, "Path traversal in static asset route rejected")
            return self._serve_static_asset(rel_asset)

        if method == "POST" and clean_path == "/api/push-stream":
            return self.dispatch_push_stream(
                body, headers=headers, enforce_csrf=enforce_csrf, on_chunk=None
            )

        if enforce_csrf and clean_path.startswith("/api/"):
            csrf_err = self._verify_csrf_and_origin(headers)
            if csrf_err is not None:
                return _err_resp(403, csrf_err)

        if clean_path == "/api/state" and method in ("GET", "POST"):
            payload = self._parse_json_body(body) if method == "POST" else {}
            if payload is None:
                return _err_resp(400, "Request body must be a JSON object")
            try:
                with self._workspace_paths(payload) as paths:
                    match inspect_tenant_state(paths, extra_library_roots=self._extra_libs()):
                        case Err(err):
                            return _err_resp(400, str(err))
                        case Ok(snap):
                            out: JsonDict = {
                                "ok": True,
                                "mode": self._mode_name(),
                                "state": snap,
                            }
                            if self.is_stateless:
                                out["bundle"] = _serialize_bundle(paths)
                            return _json_resp(200, out)
            except RuntimeError as exc:
                return _err_resp(500, str(exc))

        if method == "POST" and clean_path == "/api/mutate":
            payload = self._parse_json_body(body)
            if payload is None:
                return _err_resp(400, "Request body must be a JSON object")
            raw_mut = payload.get("mutation")
            mutation: Mapping[str, Any] = raw_mut if isinstance(raw_mut, Mapping) else payload
            with self._workspace_paths(payload) as paths:
                match apply_studio_mutation(
                    paths, mutation, extra_library_roots=self._extra_libs()
                ):
                    case Err(err):
                        return _err_resp(400, str(err))
                    case Ok(snap):
                        out = {
                            "ok": True,
                            "mode": self._mode_name(),
                            "state": snap,
                        }
                        if self.is_stateless:
                            out["bundle"] = _serialize_bundle(paths)
                        return _json_resp(200, out)

        if method == "POST" and clean_path == "/api/profile":
            payload = self._parse_json_body(body)
            if payload is None:
                return _err_resp(400, "Request body must be a JSON object")
            raw_spec = payload.get("spec")
            prof_spec: Mapping[str, Any] = raw_spec if isinstance(raw_spec, Mapping) else payload
            with self._workspace_paths(payload) as paths:
                match manage_studio_profile(
                    paths, prof_spec, extra_library_roots=self._extra_libs()
                ):
                    case Err(err):
                        return _err_resp(400, str(err))
                    case Ok(snap):
                        out = {
                            "ok": True,
                            "mode": self._mode_name(),
                            "state": snap,
                        }
                        if self.is_stateless:
                            out["bundle"] = _serialize_bundle(paths)
                        return _json_resp(200, out)

        if method == "POST" and clean_path == "/api/doctor":
            payload = self._parse_json_body(body)
            if payload is None:
                return _err_resp(400, "Request body must be a JSON object")
            pending_env = (
                payload.get("pendingEnv")
                if isinstance(payload.get("pendingEnv"), Mapping)
                else None
            )
            probe_net = bool(payload.get("probeNetwork", True))
            with self._workspace_paths(payload) as paths:
                match verify_workspace_health(
                    paths,
                    pending_env=pending_env,
                    probe_network=probe_net,
                    extra_library_roots=self._extra_libs(),
                    http_client=self._http_client(),
                ):
                    case Err(err):
                        return _err_resp(400, str(err))
                    case Ok(health_rep):
                        return _json_resp(200, {"ok": True, "report": health_rep, **health_rep})

        if method == "POST" and clean_path == "/api/inspect-manifest":
            payload = self._parse_json_body(body)
            if payload is None:
                return _err_resp(400, "Request body must be a JSON object")
            raw_url = str(payload.get("url") or payload.get("manifestUrl") or "").strip()
            match inspect_workspace_addon_manifest(raw_url, http_client=self._http_client()):
                case Err(err):
                    return _err_resp(400, str(err))
                case Ok(manifest_info):
                    return _json_resp(
                        200, {"ok": True, "manifest": manifest_info, **manifest_info}
                    )

        if method == "POST" and clean_path == "/api/preview-catalog":
            payload = self._parse_json_body(body)
            if payload is None:
                return _err_resp(400, "Request body must be a JSON object")
            raw_spec = payload.get("spec")
            cat_spec: Mapping[str, Any] = raw_spec if isinstance(raw_spec, Mapping) else payload
            pending_env = (
                payload.get("pendingEnv")
                if isinstance(payload.get("pendingEnv"), Mapping)
                else None
            )
            with self._workspace_paths(payload) as paths:
                match preview_catalog_items(
                    paths,
                    cat_spec,
                    pending_env=pending_env,
                    http_client=self._http_client(),
                ):
                    case Err(err):
                        return _err_resp(400, str(err))
                    case Ok(preview):
                        return _json_resp(200, {"ok": True, **preview})

        if method == "POST" and clean_path == "/api/raw-doc":
            payload = self._parse_json_body(body)
            if payload is None:
                return _err_resp(400, "Request body must be a JSON object")
            doc_key = str(payload.get("doc") or payload.get("docKey") or "aiostreams").strip()
            action = str(
                payload.get("action") or ("save" if "content" in payload else "get")
            ).strip().lower()
            with self._workspace_paths(payload) as paths:
                if action in ("get", "read", "inspect"):
                    match inspect_raw_document(paths, doc_key):
                        case Err(err):
                            return _err_resp(400, str(err))
                        case Ok(doc_info):
                            return _json_resp(200, {"ok": True, **doc_info})
                if action in ("save", "write", "put"):
                    raw_content = payload.get("content")
                    if not isinstance(raw_content, str):
                        return _err_resp(400, "`content` must be a JSON string when saving raw document")
                    match save_raw_document(
                        paths,
                        doc_key,
                        raw_content,
                        extra_library_roots=self._extra_libs(),
                    ):
                        case Err(err):
                            return _err_resp(400, str(err))
                        case Ok(saved_info):
                            out = {
                                "ok": True,
                                "mode": self._mode_name(),
                                **saved_info,
                            }
                            if self.is_stateless:
                                out["bundle"] = _serialize_bundle(paths)
                            return _json_resp(200, out)
                return _err_resp(400, f"Unsupported raw-doc action `{action}`")

        if method == "POST" and clean_path == "/api/warm":
            payload = self._parse_json_body(body)
            if payload is None:
                return _err_resp(400, "Request body must be a JSON object")
            raw_workers = payload.get("workers", 8)
            if isinstance(raw_workers, bool) or not isinstance(raw_workers, (int, float)):
                return _err_resp(400, "`workers` must be an integer")
            workers = max(1, min(32, int(raw_workers)))
            with self._workspace_paths(payload) as paths:
                match warm_workspace_catalogs(
                    paths,
                    workers=workers,
                    extra_library_roots=self._extra_libs(),
                    http_client=self._http_client(),
                ):
                    case Err(err):
                        return _err_resp(400, str(err))
                    case Ok(warm_res):
                        return _json_resp(200, {"ok": True, **warm_res})

        if method == "POST" and clean_path == "/api/nuvio-login":
            payload = self._parse_json_body(body)
            if payload is None:
                return _err_resp(400, "Request body must be a JSON object")
            login_name = str(payload.get("login") or "")
            email = (
                str(payload["email"])
                if isinstance(payload.get("email"), str) and payload["email"].strip()
                else None
            )
            password = (
                str(payload["password"])
                if isinstance(payload.get("password"), str) and payload["password"]
                else None
            )
            with self._workspace_paths(payload) as paths:
                match login_nuvio_and_list_profiles(
                    paths,
                    login=login_name,
                    email=email,
                    password=password,
                    http_client=self._http_client(),
                ):
                    case Err(err):
                        return _err_resp(400, str(err))
                    case Ok(login_info):
                        match inspect_tenant_state(paths, extra_library_roots=self._extra_libs()):
                            case Err(s_err):
                                return _err_resp(400, str(s_err))
                            case Ok(snap):
                                out = {
                                    "ok": True,
                                    "nuvio": login_info,
                                    "state": snap,
                                }
                                if self.is_stateless:
                                    out["bundle"] = _serialize_bundle(paths)
                                return _json_resp(200, out)

        if method == "POST" and clean_path == "/api/stremio-login":
            payload = self._parse_json_body(body)
            if payload is None:
                return _err_resp(400, "Request body must be a JSON object")
            login_name = str(payload.get("login") or "")
            email = (
                str(payload["email"])
                if isinstance(payload.get("email"), str) and payload["email"].strip()
                else None
            )
            password = (
                str(payload["password"])
                if isinstance(payload.get("password"), str) and payload["password"]
                else None
            )
            with self._workspace_paths(payload) as paths:
                match login_stremio(
                    paths,
                    login=login_name,
                    email=email,
                    password=password,
                    http_client=self._http_client(),
                ):
                    case Err(err):
                        return _err_resp(400, str(err))
                    case Ok(login_info):
                        match inspect_tenant_state(paths, extra_library_roots=self._extra_libs()):
                            case Err(s_err):
                                return _err_resp(400, str(s_err))
                            case Ok(snap):
                                out = {
                                    "ok": True,
                                    "stremio": login_info,
                                    "state": snap,
                                }
                                if self.is_stateless:
                                    out["bundle"] = _serialize_bundle(paths)
                                return _json_resp(200, out)

        if method == "POST" and clean_path == "/api/provision":
            payload = self._parse_json_body(body)
            if payload is None:
                return _err_resp(400, "Request body must be a JSON object")
            with self._workspace_paths(payload) as paths:
                if isinstance(payload.get("mutation"), Mapping):
                    match apply_studio_mutation(
                        paths, payload["mutation"], extra_library_roots=self._extra_libs()
                    ):
                        case Err(m_err):
                            return _err_resp(400, str(m_err))
                        case Ok():
                            pass
                match provision_instances(
                    paths, extra_library_roots=self._extra_libs(), http_client=self._http_client()
                ):
                    case Err(err):
                        return _err_resp(400, str(err))
                    case Ok(report):
                        out = {
                            "ok": True,
                            "steps": list(report.steps),
                            "state": report.snapshot,
                        }
                        if self.is_stateless:
                            out["bundle"] = _serialize_bundle(paths)
                        return _json_resp(200, out)

        if method == "POST" and clean_path == "/api/push":
            payload = self._parse_json_body(body)
            if payload is None:
                return _err_resp(400, "Request body must be a JSON object")
            target = str(payload.get("target") or "all")
            inst = str(payload.get("instance") or DEFAULT_INSTANCE).strip() or DEFAULT_INSTANCE
            prof = (
                str(payload["profile"])
                if isinstance(payload.get("profile"), str) and payload["profile"]
                else None
            )
            warm_catalogs = bool(payload.get("warmCatalogs", False))
            create_flag = bool(payload.get("create", False))
            with self._workspace_paths(payload) as paths:
                if isinstance(payload.get("mutation"), Mapping):
                    match apply_studio_mutation(
                        paths, payload["mutation"], extra_library_roots=self._extra_libs()
                    ):
                        case Err(m_err):
                            return _err_resp(400, str(m_err))
                        case Ok():
                            pass
                match push_workspace(
                    paths,
                    target=target,
                    instance=inst,
                    profile=prof,
                    create=create_flag,
                    warm_catalogs=warm_catalogs,
                    extra_library_roots=self._extra_libs(),
                    http_client=self._http_client(),
                ):
                    case Err(err):
                        return _err_resp(400, str(err))
                    case Ok(report):
                        out = {
                            "ok": True,
                            "steps": list(report.steps),
                            "state": report.snapshot,
                        }
                        if self.is_stateless:
                            out["bundle"] = _serialize_bundle(paths)
                        return _json_resp(200, out)

        if method == "POST" and clean_path == "/api/pull":
            payload = self._parse_json_body(body)
            if payload is None:
                return _err_resp(400, "Request body must be a JSON object")
            target = str(payload.get("target") or "all")
            inst = str(payload.get("instance") or DEFAULT_INSTANCE).strip() or DEFAULT_INSTANCE
            prof = (
                str(payload["profile"])
                if isinstance(payload.get("profile"), str) and payload["profile"]
                else None
            )
            with self._workspace_paths(payload) as paths:
                match pull_workspace(
                    paths,
                    target=target,
                    instance=inst,
                    profile=prof,
                    extra_library_roots=self._extra_libs(),
                    http_client=self._http_client(),
                ):
                    case Err(err):
                        return _err_resp(400, str(err))
                    case Ok(report):
                        out = {
                            "ok": True,
                            "steps": list(report.steps),
                            "state": report.snapshot,
                        }
                        if self.is_stateless:
                            out["bundle"] = _serialize_bundle(paths)
                        return _json_resp(200, out)

        if method == "POST" and clean_path == "/api/build-row":
            payload = self._parse_json_body(body)
            if payload is None:
                return _err_resp(400, "Request body must be a JSON object")
            raw_spec = payload.get("spec")
            spec: Mapping[str, Any] = raw_spec if isinstance(raw_spec, Mapping) else payload
            pending_env = (
                payload.get("pendingEnv")
                if isinstance(payload.get("pendingEnv"), Mapping)
                else None
            )
            with self._workspace_paths(payload) as paths:
                match build_custom_folder_sources(
                    paths,
                    spec,
                    pending_env=pending_env,
                    http_client=self._http_client(),
                ):
                    case Err(err):
                        return _err_resp(400, str(err))
                    case Ok(built):
                        return _json_resp(200, {"ok": True, **built})

        if method == "POST" and clean_path == "/api/diff":
            payload = self._parse_json_body(body)
            if payload is None:
                return _err_resp(400, "Request body must be a JSON object")
            target = str(payload.get("target") or "all")
            inst = str(payload.get("instance") or DEFAULT_INSTANCE).strip() or DEFAULT_INSTANCE
            prof = (
                str(payload["profile"])
                if isinstance(payload.get("profile"), str) and payload["profile"]
                else None
            )
            with self._workspace_paths(payload) as paths:
                match diff_workspace(
                    paths,
                    target=target,
                    instance=inst,
                    profile=prof,
                    extra_library_roots=self._extra_libs(),
                    http_client=self._http_client(),
                ):
                    case Err(err):
                        return _err_resp(400, str(err))
                    case Ok(diff_rep):
                        return _json_resp(
                            200,
                            {
                                "ok": True,
                                "outcomes": [
                                    {
                                        "target": o.target,
                                        "inSync": o.in_sync,
                                        "addedLines": o.added_lines,
                                        "removedLines": o.removed_lines,
                                        "changedKeys": list(o.changed_keys),
                                        "unifiedDiff": o.unified_diff,
                                    }
                                    for o in diff_rep.outcomes
                                ],
                                "skipped": list(diff_rep.skipped),
                            },
                        )

        if clean_path == "/api/export-zip" and method in ("GET", "POST"):
            payload = self._parse_json_body(body) if method == "POST" else {}
            if payload is None:
                return _err_resp(400, "Request body must be a JSON object")
            with self._workspace_paths(payload) as paths:
                zip_bytes = export_tenant_zip_bytes(paths, include_env=False)
                fname = f"{paths.root.name or 'stream-weaver-tenant'}.zip"
                return StudioResponse(
                    status=200,
                    content_type="application/zip",
                    body=zip_bytes,
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'},
                )

        return _err_resp(404, f"Unknown route: {method} {clean_path}")


def serve_studio(
    dispatcher: StudioDispatcher,
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
    open_browser: bool = True,
    on_listen: Callable[[str], None] | None = None,
) -> None:
    """Runs the multi-threaded Studio HTTP server until interrupted (`Ctrl+C`)."""

    class _Handler(BaseHTTPRequestHandler):
        def _send_studio_response(self, resp: StudioResponse) -> None:
            self.send_response(resp.status)
            self.send_header("Content-Type", resp.content_type)
            self.send_header("Content-Length", str(len(resp.body)))
            self.send_header("Cache-Control", "no-store")
            for hk, hv in resp.headers.items():
                self.send_header(hk, hv)
            self.end_headers()
            self.wfile.write(resp.body)
            self.wfile.flush()

        def _handle(self, method: str) -> None:
            length = int(self.headers.get("Content-Length") or "0")
            body = self.rfile.read(length) if length > 0 else b""
            req_headers = dict(self.headers.items())
            clean_path = self.path.split("?", 1)[0]

            if method == "POST" and clean_path == "/api/push-stream":
                csrf_err = dispatcher._verify_csrf_and_origin(req_headers)
                if csrf_err is not None:
                    self._send_studio_response(_err_resp(403, csrf_err))
                    return
                if dispatcher._parse_json_body(body) is None:
                    self._send_studio_response(_err_resp(400, "Request body must be a JSON object"))
                    return

                headers_sent = False

                def _stream_line(chunk: bytes) -> None:
                    nonlocal headers_sent
                    if not headers_sent:
                        self.send_response(200)
                        self.send_header("Content-Type", "application/x-ndjson")
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("X-Content-Type-Options", "nosniff")
                        self.end_headers()
                        headers_sent = True
                    self.wfile.write(chunk)
                    self.wfile.flush()

                resp = dispatcher.dispatch_push_stream(
                    body,
                    headers=req_headers,
                    enforce_csrf=False,
                    on_chunk=_stream_line,
                )
                if not headers_sent:
                    self._send_studio_response(resp)
                return

            resp = dispatcher.dispatch(
                method,
                self.path,
                body,
                headers=req_headers,
                enforce_csrf=True,
            )
            self._send_studio_response(resp)

        def do_GET(self) -> None:  # noqa: N802
            self._handle("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._handle("POST")

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            _ = format, args

    server = ThreadingHTTPServer((host, port), _Handler)
    bound_host, bound_port = server.server_address[:2]
    shown_host = "127.0.0.1" if bound_host in ("0.0.0.0", "") else str(bound_host)
    url = f"http://{shown_host}:{bound_port}"
    if on_listen is not None:
        on_listen(url)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
