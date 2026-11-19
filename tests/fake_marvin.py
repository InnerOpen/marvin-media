"""A stand-in for Marvin's HTTP API, at the one place the worker talks to it (`marvin_api.request`), so the worker's real calls run against it:
`query`, `create_entry`, `update_entry`, `asset`, `download_asset`, `upload_asset`. It keeps entries and assets in memory and behaves as Marvin
does where that matters here:
- a create whose slug is taken gets the next free one (`slug-2`), a slug in the Trash included (Marvin's unique index covers it);
- a query leaves the Trash out; an asset looked up by a slug that is not an id is a 404, then found by `?q=`;
- every write is logged, so a test can say "nothing was written"."""

import json
import re
import shutil
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from marvin_worker import marvin_api

TYPES = {"job": "type-job", "recording": "type-recording", "transcript": "type-transcript"}


class FakeMarvin:
    def __init__(self, types: dict | None = None):
        self.types = dict(TYPES if types is None else types)
        self.entries: list[dict] = []
        self.assets: dict[str, dict] = {}
        self.files: dict[str, Path] = {}
        self.blobs: dict[str, bytes] = {}  # what each upload sent, by its new id
        self.writes: list[tuple[str, str]] = []  # (method, path) of every call that changes something
        self.downloads: list[str] = []
        self.before_create = None  # a hook: called with the create's slug just before Marvin makes it (a rival job's create, say)
        self.fail_entry_patch = False

    # ── set-up ─────────────────────────────────────────────────────────────
    def add_asset(self, file: "Path | None", *, mime: str, name: str = "", meta: dict | None = None, origin: str = "upload", asset_id: str = "", slug: str = "") -> dict:
        asset_id = asset_id or str(uuid.uuid4())
        name = name or (file.name if file else "file")
        record = {"id": asset_id, "slug": slug or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"), "name": name, "mimeType": mime,
                  "originalFilename": file.name if file else name, "metadataJson": dict(meta or {}), "origin": origin, "trashedAt": None}  # fmt: skip
        self.assets[asset_id] = record
        if file:
            self.files[asset_id] = file
        return record

    def add_entry(self, entry_type: str, slug: str, data: dict, *, title: str = "", status: str = "draft") -> dict:
        row = {"id": str(uuid.uuid4()), "slug": slug, "title": title or slug, "status": status, "entryType": entry_type, "dataJson": dict(data)}
        self.entries.append(row)
        return row

    def wire(self, monkeypatch) -> "FakeMarvin":
        monkeypatch.setattr(marvin_api, "request", self.request)
        return self

    # ── reading back ───────────────────────────────────────────────────────
    def live(self, entry_type: str = "recording") -> list[dict]:
        return [e for e in self.entries if e["entryType"] == entry_type and e["status"] != "trashed"]

    def entry(self, slug: str) -> dict:
        return next(e for e in self.entries if e["slug"] == slug)

    def uploads(self) -> list[dict]:
        return [a for a in self.assets.values() if a.get("uploaded")]

    # ── the API ────────────────────────────────────────────────────────────
    def request(self, creds, path: str, *, method: str = "GET", data: "bytes | None" = None, content_type: "str | None" = None, **_) -> bytes:
        url = urlparse(path)
        parts = url.path.strip("/").split("/")  # api, platform, ...
        assert parts[:2] == ["api", "platform"], path
        rest = parts[2:]
        if method != "GET" and not (method == "POST" and rest == ["entries", "query"]):
            self.writes.append((method, url.path))
        if rest == ["entry-types"] and method == "GET":
            return _json([{"slug": s, "id": i} for s, i in self.types.items()])
        if rest == ["entries", "query"] and method == "POST":
            return _json({"entries": self._query(json.loads(data)["spec"])})
        if rest == ["entries"] and method == "POST":
            return _json(self._create(json.loads(data)))
        if len(rest) == 2 and rest[0] == "entries":
            row = next((e for e in self.entries if e["id"] == rest[1]), None)
            if row is None:
                raise marvin_api.MarvinError(f"Marvin answered 404 for {path}: no entry")
            if method == "PATCH":
                if self.fail_entry_patch:
                    raise marvin_api.MarvinError(f"Marvin answered 500 for {path}: boom")
                body = json.loads(data)
                row["dataJson"] = dict(body["dataJson"])
                return _json(row)
            if method == "DELETE":
                row["status"] = "trashed"
                return b""
        if rest == ["assets"] and method == "GET":
            q = parse_qs(url.query).get("q", [""])[0]
            return _json([a for a in self.assets.values() if q in (a["slug"], a["name"]) or q in a["slug"]])
        if rest == ["assets", "upload"] and method == "POST":
            return _json(self._upload(data, content_type))
        if len(rest) >= 2 and rest[0] == "assets":
            record = self.assets.get(rest[1])
            if record is None:
                raise marvin_api.MarvinError(f"Marvin answered 404 for {path}: no asset")
            if len(rest) == 3 and rest[2] == "file":
                self.downloads.append(rest[1])
                return self.files[rest[1]].read_bytes()
            if method == "GET":
                return _json(record)
            if method == "PATCH":
                record["metadataJson"] = dict(json.loads(data)["metadata"])
                return _json(record)
            if method == "DELETE":
                record["trashedAt"] = "2026-11-18T00:00:00Z"
                return b""
        raise AssertionError(f"the fake Marvin has no {method} {path}")

    def _query(self, spec: dict) -> list[dict]:
        rows = [e for e in self.entries if e["status"] != "trashed"]
        if "entries" in spec:
            wanted = {str(spec["entries"])}
            rows = [e for e in rows if e["slug"] in wanted or e["id"] in wanted]
        if spec.get("entry_type"):
            rows = [e for e in rows if e["entryType"] == spec["entry_type"]]
        for key, value in (spec.get("data") or {}).items():
            rows = [e for e in rows if e["dataJson"].get(key) == value]
        return [json.loads(json.dumps(e)) for e in rows]  # oldest first: the order they were made

    def _free(self, slug: str, taken: set[str]) -> str:
        n, out = 2, slug
        while out in taken:
            out, n = f"{slug}-{n}", n + 1
        return out

    def _create(self, body: dict) -> dict:
        if self.before_create:
            hook, self.before_create = self.before_create, None
            hook(body["slug"])
        entry_type = next(s for s, i in self.types.items() if i == body["entryTypeId"])
        slug = self._free(body["slug"], {e["slug"] for e in self.entries})
        row = {"id": str(uuid.uuid4()), "slug": slug, "title": body["title"], "status": body.get("status", "draft"), "entryType": entry_type, "dataJson": dict(body["dataJson"])}
        self.entries.append(row)
        return row

    def _upload(self, data: bytes, content_type: str) -> dict:
        boundary = content_type.split("boundary=", 1)[1].encode()
        fields, blob, filename, mime = {}, b"", "upload.bin", ""
        for part in data.split(b"--" + boundary):
            head, _, body = part.partition(b"\r\n\r\n")
            name = re.search(rb'name="([^"]+)"', head)
            if not name:
                continue
            body = body[:-2] if body.endswith(b"\r\n") else body
            if name.group(1) == b"file":
                blob, filename = body, re.search(rb'filename="([^"]+)"', head).group(1).decode()
                mime = re.search(rb"Content-Type: ([^\r\n]+)", head).group(1).decode()  # what the worker said the file is
            else:
                fields[name.group(1).decode()] = body.decode()
        asset_id = str(uuid.uuid4())
        slug = self._free(fields.get("slug") or filename, {a["slug"] for a in self.assets.values()})
        meta = json.loads(fields.get("metadata") or "{}")
        record = {"id": asset_id, "slug": slug, "name": fields.get("name", filename), "mimeType": mime, "originalFilename": filename,
                  "metadataJson": meta, "origin": "derived" if meta.get("derived_from") in self.assets else "upload", "trashedAt": None, "uploaded": True, "bytes": len(blob)}  # fmt: skip
        self.assets[asset_id] = record
        self.blobs[asset_id] = blob
        return record


def _json(value) -> bytes:
    return json.dumps(value).encode()


def copy_into(file: Path, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    return Path(shutil.copy(file, folder / file.name))
