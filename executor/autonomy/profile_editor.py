"""Private, explicit future-task profile versions; never a task/profile writer.

Only the authenticated local editor may use the value-bearing projection below.
Resume payloads are opaque bytes. Publication shares the settings directory CAS
and the existing profile writer's lock inode, but never rewrites selected files.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import math
import os
import re
import secrets
import stat
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

from .. import settings
from .profile_setup import PROFILE_LIMIT, validate_profile_text
from .task_preparation import CHECKLIST_FIELDS, RESUME_EXTENSIONS, _LocalFile, _entry, _signature

BODY_LIMIT = 20 * 1024 * 1024
METADATA_LIMIT = 32 * 1024
HEADER_LIMIT = 4096
REQUEST_HEADER_LIMIT = 8192
CHUNK_SIZE = 65536
FIELD_KEYS = frozenset(key for key, _ in CHECKLIST_FIELDS)
_HEX = re.compile(r"[0-9a-f]{64}\Z")


class EditorInvalid(ValueError):
    def __init__(self):
        super().__init__("invalid_profile_editor_request")


class EditorConflict(RuntimeError):
    def __init__(self):
        super().__init__("profile_editor_conflict")


class PublicationUncertain(RuntimeError):
    def __init__(self, current=None):
        super().__init__("publication_uncertain")
        self.current = current


def _headers(headers, name):
    return headers.get_all(name, [])


def _body_parameters(headers, content_type):
    if sum(len(key) + len(value) + 4 for key, value in headers.items()) > REQUEST_HEADER_LIMIT:
        raise EditorInvalid()
    lengths, types = _headers(headers, "Content-Length"), _headers(headers, "Content-Type")
    if (len(lengths) != 1 or len(types) != 1
            or _headers(headers, "Transfer-Encoding")
            or _headers(headers, "Content-Encoding")
            or _headers(headers, "Content-Transfer-Encoding")):
        raise EditorInvalid()
    length = lengths[0]
    if not isinstance(length, str) or not re.fullmatch(r"0|[1-9][0-9]{0,9}", length):
        raise EditorInvalid()
    size = int(length)
    if size > BODY_LIMIT or size == 0 or len(types[0]) > 200:
        raise EditorInvalid()
    if content_type == "json":
        if types[0].lower() != "application/json" or size > METADATA_LIMIT:
            raise EditorInvalid()
        return size, None
    match = re.fullmatch(r'multipart/form-data;[ \t]*boundary=(?:"([A-Za-z0-9_-]{1,70})"|([A-Za-z0-9_-]{1,70}))', types[0], re.I)
    if not match:
        raise EditorInvalid()
    return size, (match[1] or match[2]).encode("ascii")


def _read_body(stream, size):
    # Each requested read fits inside the TOTAL ceiling. Never probe limit+1.
    chunks, actual = [], 0
    while actual < size:
        requested = min(CHUNK_SIZE, size - actual, BODY_LIMIT - actual)
        try:
            chunk = stream.read(requested)
        except OSError:
            raise EditorInvalid() from None
        if not isinstance(chunk, bytes) or not chunk or len(chunk) > requested:
            raise EditorInvalid()
        actual += len(chunk)
        if actual > size or actual > BODY_LIMIT:
            raise EditorInvalid()
        chunks.append(chunk)
    return b"".join(chunks)


def _json(raw):
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=settings._object,
                          parse_constant=settings._constant)
        if not isinstance(data, dict):
            raise EditorInvalid()
        return data
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise EditorInvalid() from None


def read_reconcile_request(headers, stream):
    size, _ = _body_parameters(headers, "json")
    if _json(_read_body(stream, size)) != {}:
        raise EditorInvalid()


def read_save_request(headers, stream):
    """Parse one strict bounded multipart envelope, without trusting filenames."""
    size, boundary = _body_parameters(headers, "multipart")
    body = _read_body(stream, size)
    marker = b"--" + boundary
    if not body.startswith(marker + b"\r\n") or not body.endswith(b"\r\n" + marker + b"--\r\n"):
        raise EditorInvalid()
    # Delimiters inside opaque bytes are meaningful only at a MIME line boundary.
    pieces = body[len(marker) + 2:-(len(marker) + 6)].split(b"\r\n" + marker + b"\r\n")
    if not 1 <= len(pieces) <= 2:
        raise EditorInvalid()
    parts = {}
    for piece in pieces:
        raw_headers, separator, payload = piece.partition(b"\r\n\r\n")
        if not separator or len(raw_headers) > HEADER_LIMIT:
            raise EditorInvalid()
        part_headers = {}
        try:
            for line in raw_headers.decode("utf-8").split("\r\n"):
                key, sep, value = line.partition(":")
                key = key.lower()
                if (not sep or key not in {"content-disposition", "content-type"}
                        or key in part_headers or not value.startswith(" ")
                        or any(ord(char) < 32 or ord(char) == 127 for char in value)):
                    raise EditorInvalid()
                part_headers[key] = value.strip()
        except UnicodeError:
            raise EditorInvalid() from None
        if set(part_headers) != {"content-disposition", "content-type"}:
            raise EditorInvalid()
        disposition = part_headers["content-disposition"]
        match = re.fullmatch(r'form-data; name="(metadata|resume)"(?:; filename="([^"\r\n]{0,255})")?', disposition)
        if not match or match[1] in parts:
            raise EditorInvalid()
        name, filename = match[1], match[2]
        mime = part_headers["content-type"].lower()
        if name == "metadata":
            if mime != "application/json" or len(payload) > METADATA_LIMIT:
                raise EditorInvalid()
        elif (filename is None or not re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", mime)
              or mime.startswith("multipart/")):
            raise EditorInvalid()
        # No premature terminating delimiter, nested envelope, or epilogue.
        if b"\r\n" + marker in payload:
            raise EditorInvalid()
        parts[name] = payload
    if "metadata" not in parts:
        raise EditorInvalid()
    metadata = _json(parts["metadata"])
    resume = parts.get("resume")
    validate_metadata(metadata, resume)
    return metadata, resume


def _type(key):
    if key == "preferences.preferred_cities":
        return "text_list"
    if key == "language.cet6.score":
        return "text_or_number"
    return "text"


def _supported(key, value):
    if value is None:
        return True
    if isinstance(value, str):
        return _type(key) != "text_list" and len(value) <= 2048 and not any(
            ord(c) < 32 and c not in "\n\t" for c in value)
    if _type(key) == "text_or_number":
        return type(value) in {int, float} and abs(value) <= 1e12 and math.isfinite(value)
    if _type(key) == "text_list":
        return (isinstance(value, list) and len(value) <= 32
                and all(isinstance(item, str) and len(item) <= 200
                        and not any(ord(c) < 32 for c in item) for item in value))
    return False


def validate_metadata(metadata, resume):
    required = {"schema_version", "expected_settings_version", "expected_profile_version",
                "edits", "resume_action", "resume_kind"}
    if (not isinstance(metadata, dict) or set(metadata) != required
            or type(metadata["schema_version"]) is not int or metadata["schema_version"] != 1
            or not isinstance(metadata["expected_settings_version"], str)
            or not _HEX.fullmatch(metadata["expected_settings_version"])
            or (metadata["expected_profile_version"] is not None
                and (not isinstance(metadata["expected_profile_version"], str)
                     or not _HEX.fullmatch(metadata["expected_profile_version"])))
            or not isinstance(metadata["edits"], dict)
            or set(metadata["edits"]) - FIELD_KEYS):
        raise EditorInvalid()
    if any(not _supported(key, value) for key, value in metadata["edits"].items()):
        raise EditorInvalid()
    if metadata["resume_action"] == "keep":
        if metadata["resume_kind"] is not None or resume is not None:
            raise EditorInvalid()
    elif metadata["resume_action"] == "replace":
        if (not isinstance(metadata["resume_kind"], str)
                or metadata["resume_kind"] not in RESUME_EXTENSIONS
                or not isinstance(resume, bytes) or not 0 < len(resume) <= BODY_LIMIT):
            raise EditorInvalid()
    else:
        raise EditorInvalid()
    try:
        if len(json.dumps(metadata, allow_nan=False, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > METADATA_LIMIT:
            raise EditorInvalid()
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise EditorInvalid() from None


class _DirectoryPin:
    """Keep every ancestor descriptor; reject aliases and replaced path edges."""
    def __init__(self, path):
        self.path, self.descriptors, self.edges = Path(path), [], []
        self.descriptor = None

    def __enter__(self):
        path = self.path
        if not path.is_absolute() or ".." in path.parts or path.anchor != "/":
            raise EditorInvalid()
        try:
            parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            self.descriptors.append(parent)
            for name in path.parts[1:]:
                info = _entry(parent, name)
                if info is None or not stat.S_ISDIR(info.st_mode):
                    raise EditorInvalid()
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                self.descriptors.append(child)
                self.edges.append((parent, name, _signature(info)))
                if _signature(os.fstat(child)) != _signature(info):
                    raise EditorConflict()
                parent = child
            _private_directory(parent)
            self.descriptor = parent
            return self
        except Exception:
            self.__exit__()
            raise

    def fence(self):
        for parent, name, signature in self.edges:
            if _signature(_entry(parent, name)) != signature:
                raise EditorConflict()
        _private_directory(self.descriptor)

    def __exit__(self, *_):
        for descriptor in reversed(self.descriptors):
            os.close(descriptor)
        self.descriptors = []


def _private_directory(descriptor):
    info = os.fstat(descriptor)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077):
        raise EditorInvalid()


class _ProfileRead(_LocalFile):
    def read(self):
        self._admit()
        chunks, size = [], 0
        while size < self.info.st_size:
            chunk = os.read(self.descriptor, min(CHUNK_SIZE, self.info.st_size - size, self.limit - size))
            if not chunk:
                raise EditorConflict()
            size += len(chunk)
            if size > self.limit or size > self.info.st_size:
                raise EditorConflict()
            chunks.append(chunk)
        self.fence()
        return b"".join(chunks)


class _ProfileLock:
    def __init__(self, pin, name):
        self.pin, self.name, self.descriptor, self.identity = pin, name + ".lock", None, None

    def __enter__(self):
        parent = self.pin.descriptor
        before = _entry(parent, self.name)
        try:
            if before is None:
                self.descriptor = os.open(self.name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                          0o600, dir_fd=parent)
            else:
                self._regular(before)
                self.descriptor = os.open(self.name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                                          dir_fd=parent)
            current = os.fstat(self.descriptor)
            self._regular(current)
            self.identity = self._identity(current)
            if before is not None and self._identity(before) != self.identity:
                raise EditorConflict()
            fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.fence()
            return self
        except Exception:
            self.__exit__()
            raise

    @staticmethod
    def _identity(info):
        return info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_nlink

    @staticmethod
    def _regular(info):
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o077
                or info.st_size > HEADER_LIMIT):
            raise EditorInvalid()

    def fence(self):
        self.pin.fence()
        info = _entry(self.pin.descriptor, self.name)
        if info is None:
            raise EditorConflict()
        self._regular(info)
        if self._identity(info) != self.identity or self._identity(os.fstat(self.descriptor)) != self.identity:
            raise EditorConflict()

    def __exit__(self, *_):
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None


def _decode_profile(raw):
    try:
        return validate_profile_text(raw.decode("utf-8"))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise EditorInvalid() from None


def _encode_profile(profile):
    encoded = json.dumps(profile, ensure_ascii=False, allow_nan=False,
                         separators=(",", ":")).encode("utf-8")
    _decode_profile(encoded)
    return encoded


def _read_profile(stack, current):
    path = current.get("profile_path")
    if path is None or path == "":
        return {"schema_version": "1.0", "fields": {}}, None, "new", None
    if not isinstance(path, str) or str(Path(path)) != path:
        raise EditorInvalid()
    reader = stack.enter_context(_ProfileRead(path, PROFILE_LIMIT, private=True))
    raw = reader.read()
    # Validation only; keep the original decoded JSON and exact-byte version.
    profile = _decode_profile(raw)
    return profile, hashlib.sha256(raw).hexdigest(), "canonical" if "fields" in profile else "legacy", reader


def _editable(profile, key):
    # Structured education is independently consumed by application plans. A
    # flat edit cannot silently diverge from those existing structured records.
    if key.startswith("education.highest.") and profile.get("collections", {}).get("education_records"):
        return False
    return _supported(key, profile["fields"].get(key, {}).get("value"))


def _owned_resume(stack, profile):
    asset = profile.get("assets", {}).get("resume")
    if not isinstance(asset, dict):
        return None
    source = asset.get("source")
    if not isinstance(source, dict) or source.get("note") != "local_profile_editor":
        return None
    kind, path, digest = asset.get("kind"), asset.get("path"), asset.get("sha256")
    if (kind not in RESUME_EXTENSIONS or not isinstance(path, str)
            or Path(path).parent != settings.PATH.parent or str(Path(path)) != path
            or not re.fullmatch(r"resume-[0-9a-f]{32}" + re.escape(RESUME_EXTENSIONS[kind]), Path(path).name)
            or not isinstance(digest, str) or not _HEX.fullmatch(digest)):
        raise EditorConflict()
    reader = stack.enter_context(_ProfileRead(path, BODY_LIMIT, private=True))
    raw = reader.read()
    if not raw or hashlib.sha256(raw).hexdigest() != digest:
        raise EditorConflict()
    return reader


def _state(current, profile, version, mode):
    fields = []
    if mode != "legacy":
        for key, label in CHECKLIST_FIELDS:
            value = profile["fields"].get(key, {}).get("value")
            supported = _editable(profile, key)
            missing = value is None or value == "" or value == []
            fields.append({"key": key, "label": label, "type": _type(key),
                           "status": "unsupported" if not supported else "missing" if missing else "supported",
                           "editable": supported, "value": value if supported else None})
    asset = profile.get("assets", {}).get("resume")
    kind = asset.get("kind") if isinstance(asset, dict) else None
    resume_status = "missing" if asset is None else "recorded_locally" if kind in RESUME_EXTENSIONS else "unsupported"
    return {"schema_version": 1, "settings_version": settings.settings_version(current),
            "profile_version": version, "mode": mode, "fields": fields,
            "resume": {"status": resume_status, "kind": kind if kind in RESUME_EXTENSIONS else None},
            "future_tasks_only": True, "submit_capability": False}


def _observe_at(parent, pin):
    with ExitStack() as stack:
        current = settings._read_at(parent)
        profile, version, mode, reader = _read_profile(stack, current)
        resume_reader = _owned_resume(stack, profile)
        if resume_reader:
            resume_reader.fence()
        if reader:
            reader.fence()
        if settings.settings_version(settings._read_at(parent)) != settings.settings_version(current):
            raise EditorConflict()
        pin.fence()
        return current, _state(current, profile, version, mode)


def editor_state():
    try:
        with settings._parent(settings.PATH, create=False) as parent:
            with _DirectoryPin(settings.PATH.parent) as pin:
                if _signature(os.fstat(parent)) != _signature(os.fstat(pin.descriptor)):
                    raise EditorConflict()
                return _observe_at(parent, pin)
    except FileNotFoundError:
        # A missing settings authority is a new profile only if settings agrees.
        current = settings.load_settings()
        if current.get("profile_path"):
            raise EditorConflict() from None
        return current, _state(current, {"fields": {}}, None, "new")
    except (EditorInvalid, EditorConflict):
        raise
    except Exception:
        raise EditorConflict() from None


def _publish_file(parent, prefix, suffix, payload):
    name = prefix + secrets.token_hex(16) + suffix
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=parent)
    complete = False
    created = os.fstat(descriptor)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            # 0600 is also enforced after a restrictive process umask.
            os.fchmod(handle.fileno(), 0o600)
            for start in range(0, len(payload), CHUNK_SIZE):
                block = payload[start:start + CHUNK_SIZE]
                if handle.write(block) != len(block):
                    raise OSError("private_write_failed")
            handle.flush()
            os.fsync(handle.fileno())
            info = os.fstat(handle.fileno())
        if info.st_size != len(payload) or _signature(_entry(parent, name)) != _signature(info):
            raise EditorConflict()
        complete = True
        os.fsync(parent)
        return name, _signature(info)
    finally:
        if not complete:
            entry = _entry(parent, name)
            if entry is not None and (entry.st_dev, entry.st_ino) == (created.st_dev, created.st_ino):
                os.unlink(name, dir_fd=parent)


def _patch(profile, edits):
    updated = copy.deepcopy(profile)
    now = datetime.now(timezone.utc).isoformat()
    for key, value in edits.items():
        previous = updated["fields"].get(key, {})
        if not _editable(profile, key):
            raise EditorInvalid()
        # Preserve constraints, extensions and all prior provenance. This is not
        # evidence extraction and never claims to refresh other derived facts.
        field = copy.deepcopy(previous)
        field["value"] = copy.deepcopy(value)
        field.setdefault("sources", []).append({"kind": "user_explicit", "locator": key,
                                                 "verified_at": now, "note": "local_profile_editor"})
        field["last_verified"] = now
        field["user_confirmed"] = value is not None
        updated["fields"][key] = field
    return updated


def save_profile(metadata, resume=None):
    """One CAS publication; return current settings + the private editor view."""
    validate_metadata(metadata, resume)
    try:
        with ExitStack() as stack:
            parent = stack.enter_context(settings._parent(settings.PATH, create=True))
            pin = stack.enter_context(_DirectoryPin(settings.PATH.parent))
            if _signature(os.fstat(parent)) != _signature(os.fstat(pin.descriptor)):
                raise EditorConflict()
            # Same authority as settings.change_settings, with bounded admission.
            fcntl.flock(parent, fcntl.LOCK_EX | fcntl.LOCK_NB)
            current = settings._read_at(parent)
            if settings.settings_version(current) != metadata["expected_settings_version"]:
                raise EditorConflict()
            lock = None
            if current.get("profile_path"):
                path = current["profile_path"]
                if not isinstance(path, str) or str(Path(path)) != path:
                    raise EditorInvalid()
                profile_pin = stack.enter_context(_DirectoryPin(Path(path).parent))
                admission = stack.enter_context(_ProfileRead(path, PROFILE_LIMIT, private=True))
                admission._admit()
                admission.fence()
                lock = stack.enter_context(_ProfileLock(profile_pin, Path(path).name))
            profile, version, mode, reader = _read_profile(stack, current)
            if version != metadata["expected_profile_version"]:
                raise EditorConflict()
            if mode == "legacy":
                raise EditorInvalid()
            updated = _patch(profile, metadata["edits"])
            published = []
            if resume is not None:
                # Retain the complete previous slot as evidence, not just its path.
                assets = updated.setdefault("assets", {})
                if "resume" in assets:
                    history_key = "resume_previous_" + secrets.token_hex(16)
                    if history_key in updated:
                        raise EditorConflict()
                    # Active assets are execution attachment slots. History is
                    # an opaque top-level extension, never another upload slot.
                    updated[history_key] = copy.deepcopy(assets["resume"])
                name, signature = _publish_file(parent, "resume-", RESUME_EXTENSIONS[metadata["resume_kind"]], resume)
                published.append((name, signature))
                assets["resume"] = {"path": str(settings.PATH.parent / name),
                                    "kind": metadata["resume_kind"],
                                    "sha256": hashlib.sha256(resume).hexdigest(),
                                    "source": {"kind": "user_explicit", "note": "local_profile_editor"}}
            encoded = _encode_profile(updated)
            name, signature = _publish_file(parent, "profile-", ".json", encoded)
            published.append((name, signature))
            selected = {**current, "profile_path": str(settings.PATH.parent / name)}
            if reader:
                reader.fence()
            if lock:
                lock.fence()
            pin.fence()
            for filename, signature in published:
                if _signature(_entry(parent, filename)) != signature:
                    raise EditorConflict()
            try:
                settings._write_at(parent, settings._encoded(selected))
                pin.fence()
                if reader:
                    reader.fence()
                if lock:
                    lock.fence()
                for filename, signature in published:
                    if _signature(_entry(parent, filename)) != signature:
                        raise EditorConflict()
                observed, view = _observe_at(parent, pin)
                if (settings.settings_version(observed) != settings.settings_version(selected)
                        or view["profile_version"] != hashlib.sha256(encoded).hexdigest()):
                    raise EditorConflict()
            except Exception:
                # _write_at may already have replaced settings before fsync
                # failed. A proven old selection is the only safe failure case.
                try:
                    observed, _ = _observe_at(parent, pin)
                except Exception:
                    raise PublicationUncertain() from None
                if settings.settings_version(observed) == settings.settings_version(current):
                    raise EditorConflict() from None
                raise PublicationUncertain(observed) from None
            return selected, view
    except (EditorInvalid, EditorConflict, PublicationUncertain):
        raise
    except Exception:
        raise EditorConflict() from None
