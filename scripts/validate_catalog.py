#!/usr/bin/env python3
import json
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "catalog.json"
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
PACK_ID = re.compile(r"^[A-Za-z0-9._-]+$")
PACK_TYPES = {"visual", "color", "login", "sound", "complete"}
REQUIRED = {
    "type",
    "id",
    "name",
    "author",
    "version",
    "description",
    "previewUrl",
    "downloadUrl",
    "publishedAt",
    "updatedAt",
}


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def valid_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def valid_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def main() -> None:
    try:
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"catalog.json cannot be parsed: {exc}")

    if catalog.get("formatVersion") != 1:
        fail("formatVersion must be 1")

    packs = catalog.get("packs")
    if not isinstance(packs, list):
        fail("packs must be an array")

    seen_ids: set[str] = set()
    for index, pack in enumerate(packs):
        prefix = f"packs[{index}]"
        if not isinstance(pack, dict):
            fail(f"{prefix} must be an object")

        missing = REQUIRED - pack.keys()
        if missing:
            fail(f"{prefix} is missing: {', '.join(sorted(missing))}")

        pack_type = str(pack["type"]).strip().lower()
        if pack_type not in PACK_TYPES:
            fail(f"{prefix}.type must be one of: {', '.join(sorted(PACK_TYPES))}")

        pack_id = str(pack["id"])
        if not PACK_ID.fullmatch(pack_id):
            fail(f"{prefix}.id contains unsupported characters: {pack_id}")
        if pack_id in seen_ids:
            fail(f"duplicate pack id: {pack_id}")
        seen_ids.add(pack_id)

        version = str(pack["version"])
        if not SEMVER.fullmatch(version):
            fail(f"{prefix}.version is not valid semantic versioning: {version}")

        if not str(pack["name"]).strip():
            fail(f"{prefix}.name cannot be empty")
        if len(str(pack["description"])) > 300:
            fail(f"{prefix}.description cannot exceed 300 characters")

        for key in ("previewUrl", "downloadUrl"):
            if not valid_url(str(pack[key])):
                fail(f"{prefix}.{key} must be an http(s) URL")

        for key in ("publishedAt", "updatedAt"):
            if not valid_date(str(pack[key])):
                fail(f"{prefix}.{key} must use YYYY-MM-DD")

        if date.fromisoformat(str(pack["updatedAt"])) < date.fromisoformat(str(pack["publishedAt"])):
            fail(f"{prefix}.updatedAt cannot be earlier than publishedAt")

    print(f"Catalog OK: {len(packs)} pack(s), {len(seen_ids)} unique ID(s).")


if __name__ == "__main__":
    main()
