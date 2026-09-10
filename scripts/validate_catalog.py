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
    pack_by_id: dict[str, dict] = {}
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
        pack_by_id[pack_id] = pack

        version = str(pack["version"])
        if not SEMVER.fullmatch(version):
            fail(f"{prefix}.version is not valid semantic versioning: {version}")

        if not str(pack["name"]).strip():
            fail(f"{prefix}.name cannot be empty")

        # Legacy catalog entries may contain descriptions longer than the current
        # 160-character submission limit. New submissions/updates are still
        # enforced by validate_submission.py and prepare_publication.py.
        # Do not reject the whole catalog solely because an older entry predates
        # the current description limit.

        for key in ("previewUrl", "downloadUrl"):
            if not valid_url(str(pack[key])):
                fail(f"{prefix}.{key} must be an http(s) URL")


        component_ids = pack.get("componentIds")
        if component_ids is not None:
            if pack_type != "complete":
                fail(f"{prefix}.componentIds is only valid for Complete Packs")
            if not isinstance(component_ids, dict):
                fail(f"{prefix}.componentIds must be an object")
            allowed_component_types = {"visual", "color", "login", "sound"}
            extra_component_types = set(component_ids) - allowed_component_types
            if extra_component_types:
                fail(f"{prefix}.componentIds contains unsupported keys: {', '.join(sorted(extra_component_types))}")
            for child_type, child_id in component_ids.items():
                if not PACK_ID.fullmatch(str(child_id)):
                    fail(f"{prefix}.componentIds.{child_type} contains an invalid Pack ID")

        parent_complete_id = str(pack.get("parentCompletePackId", "")).strip()
        generated_from_complete = pack.get("generatedFromCompletePack")
        if parent_complete_id:
            if pack_type == "complete":
                fail(f"{prefix}.parentCompletePackId is not valid on a Complete Pack")
            if not PACK_ID.fullmatch(parent_complete_id):
                fail(f"{prefix}.parentCompletePackId contains an invalid Pack ID")
        if generated_from_complete is not None and not isinstance(generated_from_complete, bool):
            fail(f"{prefix}.generatedFromCompletePack must be a boolean")
        if generated_from_complete is True and not parent_complete_id:
            fail(f"{prefix}.generatedFromCompletePack requires parentCompletePackId")

        pack_previews = pack.get("packPreviews")
        if pack_previews is not None:
            if pack_type != "complete":
                fail(f"{prefix}.packPreviews is only valid for Complete Packs")
            if not isinstance(pack_previews, dict):
                fail(f"{prefix}.packPreviews must be an object")
            allowed_preview_types = {"visual", "color", "login", "sound"}
            extra_preview_types = set(pack_previews) - allowed_preview_types
            if extra_preview_types:
                fail(f"{prefix}.packPreviews contains unsupported keys: {', '.join(sorted(extra_preview_types))}")
            for child_type, child_url in pack_previews.items():
                if not valid_url(str(child_url)):
                    fail(f"{prefix}.packPreviews.{child_type} must be an http(s) URL")

        for key in ("publishedAt", "updatedAt"):
            if not valid_date(str(pack[key])):
                fail(f"{prefix}.{key} must use YYYY-MM-DD")

        if date.fromisoformat(str(pack["updatedAt"])) < date.fromisoformat(str(pack["publishedAt"])):
            fail(f"{prefix}.updatedAt cannot be earlier than publishedAt")

    # Validate child -> Complete Pack relationships after every root Pack ID is known.
    for index, pack in enumerate(packs):
        parent_complete_id = str(pack.get("parentCompletePackId", "")).strip()
        if not parent_complete_id:
            continue

        prefix = f"packs[{index}]"
        parent = pack_by_id.get(parent_complete_id)
        if parent is None:
            fail(f"{prefix}.parentCompletePackId references missing pack '{parent_complete_id}'")
        if str(parent.get("type", "")).strip().lower() != "complete":
            fail(f"{prefix}.parentCompletePackId must reference a Complete Pack")

        pack_type = str(pack.get("type", "")).strip().lower()
        component_ids = parent.get("componentIds")
        if not isinstance(component_ids, dict) or str(component_ids.get(pack_type, "")).strip() != str(pack.get("id", "")).strip():
            fail(
                f"{prefix} is linked to Complete Pack '{parent_complete_id}', but that Complete Pack "
                f"does not reference it as its {pack_type} component"
            )

    print(f"Catalog OK: {len(packs)} pack(s), {len(seen_ids)} unique ID(s).")


if __name__ == "__main__":
    main()
