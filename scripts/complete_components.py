#!/usr/bin/env python3
"""Shared helpers for exposing Complete Pack children as individual Community Packs."""

from __future__ import annotations

import io
import re
import urllib.parse
import zipfile
from pathlib import Path

from validate_submission import PACK_DISPLAY, ValidationError, validate_zip_stream

COMPONENT_ARCHIVES = {
    "visual": "packs/visual.zip",
    "login": "packs/login.zip",
    "sound": "packs/sound.zip",
    "color": "packs/color.zip",
}

SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?$"
)


class CompleteComponentError(Exception):
    pass


def fail(message: str) -> None:
    raise CompleteComponentError(message)


def semver_key(value: str):
    match = SEMVER.fullmatch(str(value or "").strip())
    if not match:
        fail(f"Invalid semantic version: {value}")
    major, minor, patch = (int(match.group(i)) for i in (1, 2, 3))
    prerelease = match.group(4)
    if prerelease is None:
        pre_key = (1,)
    else:
        identifiers = []
        for part in prerelease.split("."):
            if part.isdigit():
                identifiers.append((0, int(part)))
            else:
                identifiers.append((1, part))
        pre_key = (0, tuple(identifiers))
    return major, minor, patch, pre_key


def compare_semver(left: str, right: str) -> int:
    l = semver_key(left)
    r = semver_key(right)
    return (l > r) - (l < r)


def _archive_entry_map(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    return {entry.filename.replace("\\", "/").casefold(): entry for entry in archive.infolist() if not entry.is_dir()}


def extract_complete_components(zip_path: Path, output_dir: Path) -> dict[str, dict]:
    """Validate and extract nested component ZIPs from a Complete Pack.

    Component previews remain optional because older Complete Packs were valid
    without embedded child previews. The returned metadata always describes the
    real nested pack, never the parent declaration only.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, dict] = {}

    try:
        with zipfile.ZipFile(zip_path, "r") as outer:
            entries = _archive_entry_map(outer)
            for component_type, archive_name in COMPONENT_ARCHIVES.items():
                entry = entries.get(archive_name.casefold())
                if entry is None:
                    continue

                child_bytes = outer.read(entry)
                try:
                    metadata = validate_zip_stream(
                        io.BytesIO(child_bytes), component_type, require_preview=False
                    )
                except ValidationError as exc:
                    fail(
                        f"Nested {PACK_DISPLAY[component_type]} in '{zip_path.name}' is invalid: {exc}"
                    )

                component_id = str(metadata.get("id", "")).strip()
                version = str(metadata.get("version", "")).strip()
                if not component_id or not version:
                    fail(f"Nested {PACK_DISPLAY[component_type]} is missing its ID or version.")

                asset_name = f"{component_id}-v{version}.zip"
                asset_path = output_dir / asset_name
                asset_path.write_bytes(child_bytes)

                result[component_type] = {
                    "type": component_type,
                    "id": component_id,
                    "name": str(metadata.get("name", "")).strip(),
                    "author": str(metadata.get("author", "")).strip(),
                    "version": version,
                    "description": str(metadata.get("description", "")).strip(),
                    "preview": str(metadata.get("preview", "")).strip(),
                    "assetName": asset_name,
                    "assetPath": asset_path,
                }
    except CompleteComponentError:
        raise
    except zipfile.BadZipFile as exc:
        fail(f"Complete Pack ZIP is invalid: {exc}")
    except Exception as exc:
        fail(f"Could not extract Complete Pack components: {exc}")

    return result


def release_tag_from_download_url(download_url: str) -> str:
    try:
        parts = [
            urllib.parse.unquote(part)
            for part in urllib.parse.urlparse(str(download_url or "")).path.split("/")
            if part
        ]
        if "download" in parts:
            index = parts.index("download")
            if index + 1 < len(parts):
                return parts[index + 1]
    except Exception:
        pass
    return ""


def release_asset_url(repo: str, tag: str, asset_name: str) -> str:
    encoded_tag = urllib.parse.quote(tag, safe="-._~")
    encoded_asset = urllib.parse.quote(asset_name, safe="-._~")
    return f"https://github.com/{repo}/releases/download/{encoded_tag}/{encoded_asset}"


def _find_root_pack(packs: list[dict], pack_id: str) -> dict | None:
    return next((pack for pack in packs if str(pack.get("id", "")).strip() == pack_id), None)


def detach_removed_components(packs: list[dict], parent_complete_id: str, active_component_ids: set[str]) -> None:
    """Keep old child packs downloadable if a Complete Pack stops including them.

    Only the relationship is removed. This avoids silently deleting an individual
    Community Pack merely because a later Complete Pack version changed contents.
    """
    for pack in packs:
        if str(pack.get("parentCompletePackId", "")).strip() != parent_complete_id:
            continue
        if str(pack.get("id", "")).strip() in active_component_ids:
            continue
        pack.pop("parentCompletePackId", None)
        pack.pop("generatedFromCompletePack", None)


def upsert_complete_components(
    packs: list[dict],
    parent_entry: dict,
    components: dict[str, dict],
    repo: str,
    release_tag: str,
    preview_urls: dict[str, str] | None = None,
) -> list[dict]:
    """Expose Complete Pack children as individual catalog entries.

    Existing independently-published packs keep their own metadata/download URL;
    they are only linked to the Complete Pack. Auto-generated children follow the
    Complete Pack on later updates.
    """
    preview_urls = preview_urls or {}
    parent_id = str(parent_entry.get("id", "")).strip()
    parent_owner = str(parent_entry.get("owner", "")).strip()
    parent_author = str(parent_entry.get("author", "")).strip()
    parent_preview = str(parent_entry.get("previewUrl", "")).strip()
    parent_published = str(parent_entry.get("publishedAt", "")).strip()
    parent_updated = str(parent_entry.get("updatedAt", "")).strip()

    if not parent_id or str(parent_entry.get("type", "")).strip().lower() != "complete":
        fail("Parent catalog entry must be a Complete Pack with a valid ID.")
    if not release_tag:
        fail(f"Could not determine the GitHub Release tag for Complete Pack '{parent_id}'.")

    active_ids = {str(item.get("id", "")).strip() for item in components.values() if item.get("id")}
    detach_removed_components(packs, parent_id, active_ids)

    uploads: list[dict] = []

    for component_type, component in components.items():
        component_id = str(component.get("id", "")).strip()
        component_version = str(component.get("version", "")).strip()
        if not component_id or not component_version:
            fail(f"Nested {PACK_DISPLAY.get(component_type, component_type)} has invalid metadata.")

        existing = _find_root_pack(packs, component_id)
        if existing is parent_entry:
            fail(f"Component ID '{component_id}' cannot be the same as its Complete Pack ID.")

        if existing is not None:
            existing_type = str(existing.get("type", "")).strip().lower()
            if existing_type != component_type:
                fail(
                    f"Component ID '{component_id}' is already published as "
                    f"{PACK_DISPLAY.get(existing_type, existing_type or 'another pack type')}."
                )
            existing_owner = str(existing.get("owner", "")).strip()
            if existing_owner and parent_owner and existing_owner.casefold() != parent_owner.casefold():
                fail(
                    f"Component ID '{component_id}' belongs to GitHub user @{existing_owner}, "
                    f"not @{parent_owner}."
                )
            previous_parent = str(existing.get("parentCompletePackId", "")).strip()
            if previous_parent and previous_parent != parent_id:
                fail(
                    f"Component ID '{component_id}' is already linked to Complete Pack '{previous_parent}'."
                )

        generated_by_this_parent = bool(
            existing is not None
            and existing.get("generatedFromCompletePack") is True
            and str(existing.get("parentCompletePackId", "")).strip() == parent_id
        )

        # A pack that was already published independently remains independently
        # managed. Linking it to a Complete Pack must not overwrite its release.
        if existing is not None and not generated_by_this_parent:
            existing["parentCompletePackId"] = parent_id
            existing.pop("generatedFromCompletePack", None)
            continue

        if generated_by_this_parent:
            existing_version = str(existing.get("version", "")).strip()
            if existing_version and compare_semver(component_version, existing_version) < 0:
                fail(
                    f"Complete Pack '{parent_entry.get('name', parent_id)}' would downgrade "
                    f"component '{component_id}' from v{existing_version} to v{component_version}."
                )

        asset_name = str(component.get("assetName", "")).strip()
        asset_path = component.get("assetPath")
        if not asset_name or not isinstance(asset_path, Path):
            fail(f"No extracted release asset is available for component '{component_id}'.")

        author = str(component.get("author", "")).strip()
        if not author or author == "(not specified)":
            author = parent_author

        preview_url = str(preview_urls.get(component_type, "")).strip() or parent_preview
        child_entry = {
            "type": component_type,
            "id": component_id,
            "name": str(component.get("name", "")).strip(),
            "author": author,
            "owner": parent_owner,
            "version": component_version,
            "description": str(component.get("description", "")).strip(),
            "previewUrl": preview_url,
            "downloadUrl": release_asset_url(repo, release_tag, asset_name),
            "publishedAt": (
                str(existing.get("publishedAt", "")).strip()
                if existing is not None
                else parent_published
            ),
            "updatedAt": parent_updated,
            "featured": bool(existing.get("featured", False)) if existing is not None else False,
            "parentCompletePackId": parent_id,
            "generatedFromCompletePack": True,
        }

        if existing is None:
            packs.append(child_entry)
        else:
            existing.clear()
            existing.update(child_entry)

        uploads.append(
            {
                "tag": release_tag,
                "path": str(asset_path),
                "assetName": asset_name,
                "componentId": component_id,
                "componentType": component_type,
            }
        )

    return uploads


def sort_catalog_packs(packs: list[dict]) -> None:
    packs.sort(
        key=lambda pack: (
            str(pack.get("type", "visual")).casefold(),
            str(pack.get("name", "")).casefold(),
            str(pack.get("id", "")).casefold(),
        )
    )
