#!/usr/bin/env python3
"""Prepare an approved Community Pack submission for automatic publication."""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import sys
import urllib.parse
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from complete_components import (
    CompleteComponentError,
    extract_complete_components,
    sort_catalog_packs,
    upsert_complete_components,
)

from validate_submission import (
    PACK_DISPLAY,
    PACK_TYPES,
    ValidationError,
    download as download_zip,
    extract_zip_url,
    resolve_pack_type,
    validate_zip,
)

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "catalog.json"
PREVIEWS_DIR = ROOT / "previews"
PUBLICATION_DIR = ROOT / "publication"

MAX_PREVIEW_BYTES = 20 * 1024 * 1024
SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?$"
)
MANIFEST_BY_TYPE = {
    "visual": "visualpack.json",
    "color": "colorpack.json",
    "login": "loginpack.json",
    "sound": "soundpack.json",
    "complete": "completepack.json",
}


class PublishError(Exception):
    pass


def fail(message: str) -> None:
    raise PublishError(message)


def issue_section(issue_body: str, heading: str) -> str:
    match = re.search(
        rf"###\s+{re.escape(heading)}\s*\n(.*?)(?=\n###\s+|\Z)",
        issue_body,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return ""
    value = match.group(1).strip()
    if value.casefold() in {"_no response_", "no response"}:
        return ""
    return value


def detect_preview_extension(path: Path) -> str:
    with path.open("rb") as stream:
        header = stream.read(16)
    if header.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    fail("The preview must be a real JPG or PNG image.")
    raise AssertionError("unreachable")


def _find_preview_entry(archive: zipfile.ZipFile):
    matches = [
        entry
        for entry in archive.infolist()
        if entry.filename.casefold() in {"preview.jpg", "preview.png"}
    ]
    if len(matches) != 1:
        fail("The validated pack must contain exactly one embedded preview image.")
    return matches[0]


def extract_embedded_preview(zip_path: Path, destination: Path) -> str:
    try:
        with zipfile.ZipFile(zip_path, "r") as archive:
            entry = _find_preview_entry(archive)
            if entry.file_size <= 0 or entry.file_size > MAX_PREVIEW_BYTES:
                fail("The embedded preview image has an invalid size (maximum 20 MB).")
            destination.write_bytes(archive.read(entry))
    except PublishError:
        raise
    except Exception as exc:
        fail(f"Could not extract the embedded preview image: {exc}")
    return detect_preview_extension(destination)


def extract_complete_child_previews(zip_path: Path, destination_dir: Path) -> dict[str, tuple[Path, str]]:
    result: dict[str, tuple[Path, str]] = {}
    child_entries = {
        "visual": "packs/visual.zip",
        "login": "packs/login.zip",
        "sound": "packs/sound.zip",
        "color": "packs/color.zip",
    }
    try:
        with zipfile.ZipFile(zip_path, "r") as outer:
            by_name = {entry.filename.casefold(): entry for entry in outer.infolist()}
            for child_type, child_name in child_entries.items():
                child_entry = by_name.get(child_name.casefold())
                if child_entry is None:
                    continue
                child_bytes = outer.read(child_entry)
                with zipfile.ZipFile(io.BytesIO(child_bytes), "r") as child:
                    matches = [
                        entry
                        for entry in child.infolist()
                        if entry.filename.casefold() in {"preview.jpg", "preview.png"}
                    ]
                    if not matches:
                        continue
                    if len(matches) != 1:
                        fail(f"Nested {PACK_DISPLAY[child_type]} contains more than one preview image.")
                    preview_entry = matches[0]
                    if preview_entry.file_size <= 0 or preview_entry.file_size > MAX_PREVIEW_BYTES:
                        fail(f"Nested {PACK_DISPLAY[child_type]} preview has an invalid size.")
                    destination = destination_dir / f"{child_type}.preview"
                    destination.write_bytes(child.read(preview_entry))
                    result[child_type] = (destination, detect_preview_extension(destination))
    except PublishError:
        raise
    except Exception as exc:
        fail(f"Could not extract Complete Pack child previews: {exc}")
    return result


def read_manifest(zip_path: Path, pack_type: str) -> dict:
    manifest_name = MANIFEST_BY_TYPE[pack_type]
    try:
        with zipfile.ZipFile(zip_path, "r") as archive:
            matching = [entry for entry in archive.infolist() if entry.filename.casefold() == manifest_name.casefold()]
            if len(matching) != 1:
                fail(f"Could not find exactly one {manifest_name} after validation.")
            manifest = json.loads(archive.read(matching[0]).decode("utf-8-sig"))
    except PublishError:
        raise
    except Exception as exc:
        fail(f"Could not read {manifest_name} after validation: {exc}")
    if not isinstance(manifest, dict):
        fail(f"{manifest_name} must contain a JSON object.")
    return manifest


def semver_key(value: str):
    match = SEMVER.fullmatch(value)
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


def load_catalog() -> dict:
    try:
        data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"Could not read catalog.json: {exc}")
    if not isinstance(data, dict) or not isinstance(data.get("packs"), list):
        fail("catalog.json has an invalid structure.")
    return data


def catalog_id_claims(packs: list[dict]) -> dict[str, list[tuple[dict, str]]]:
    claims: dict[str, list[tuple[dict, str]]] = {}

    def add_claim(pack_id: str, pack: dict, slot: str) -> None:
        claims.setdefault(pack_id, []).append((pack, slot))

    for pack in packs:
        if not isinstance(pack, dict):
            continue

        root_id = str(pack.get("id", "")).strip()
        if root_id:
            add_claim(root_id, pack, "root")

        component_ids = pack.get("componentIds")
        if isinstance(component_ids, dict):
            for component_type, component_id in component_ids.items():
                value = str(component_id or "").strip()
                if value:
                    add_claim(value, pack, str(component_type or "component").strip().lower())

    return claims


def validate_component_id_claims(
    packs: list[dict],
    pack_id: str,
    pack_type: str,
    component_ids: dict[str, str],
    existing: dict | None,
    submitter: str,
) -> None:
    claims = catalog_id_claims(packs)
    submitter_folded = str(submitter or "").strip().casefold()

    pack_claims = claims.get(pack_id, [])
    root_claims = [claim for claim in pack_claims if claim[1] == "root"]
    component_claims = [claim for claim in pack_claims if claim[1] != "root"]

    if len(root_claims) > 1:
        fail(f"Pack ID '{pack_id}' is used by more than one published root pack.")

    existing_owner = str(existing.get("owner", "")).strip() if existing else ""

    # A standalone pack may share its ID with a component of one Complete Pack,
    # but only when the type and GitHub owner match.
    for parent, slot in component_claims:
        parent_owner = str(parent.get("owner", "")).strip()
        parent_name = str(parent.get("name", parent.get("id", "another Complete Pack")))

        if pack_type == "complete" or slot != pack_type:
            fail(
                f"Pack ID '{pack_id}' is already used by the {slot} component of "
                f"'{parent_name}'."
            )

        if parent_owner:
            if parent_owner.casefold() != submitter_folded:
                fail(
                    f"Pack ID '{pack_id}' is already used by the {slot} component of "
                    f"'{parent_name}', owned by GitHub user @{parent_owner}."
                )
        elif not existing_owner or existing_owner.casefold() != submitter_folded:
            fail(
                f"Pack ID '{pack_id}' is already used by the {slot} component of "
                f"'{parent_name}', but that Complete Pack has no stored GitHub owner."
            )

    for component_type, component_id in component_ids.items():
        child_type = str(component_type).strip().lower()
        child_id = str(component_id or "").strip()
        if not child_id:
            continue

        child_claims = claims.get(child_id, [])
        same_complete_component = any(
            existing is not None
            and claimed_pack is existing
            and claimed_slot == child_type
            for claimed_pack, claimed_slot in child_claims
        )

        for claimed_pack, claimed_slot in child_claims:
            # Updating the same Complete Pack with the same component is valid.
            if (
                existing is not None
                and claimed_pack is existing
                and claimed_slot == child_type
            ):
                continue

            # Reusing an already-published standalone pack inside a Complete Pack
            # is valid only for the same GitHub owner and the same pack type.
            if claimed_slot == "root":
                claimed_type = str(claimed_pack.get("type", "")).strip().lower()
                claimed_owner = str(claimed_pack.get("owner", "")).strip()

                if claimed_type != child_type:
                    fail(
                        f"Nested {PACK_DISPLAY.get(child_type, child_type)} ID '{child_id}' "
                        f"is already published as {PACK_DISPLAY.get(claimed_type, claimed_type or 'another pack type')}."
                    )

                if claimed_owner:
                    if claimed_owner.casefold() != submitter_folded:
                        fail(
                            f"Nested {PACK_DISPLAY.get(child_type, child_type)} ID '{child_id}' "
                            f"belongs to GitHub user @{claimed_owner}. "
                            f"Only that account can reuse it in a Complete Pack."
                        )
                elif not same_complete_component:
                    fail(
                        f"Nested {PACK_DISPLAY.get(child_type, child_type)} ID '{child_id}' "
                        f"is already published as a standalone pack, but it has no stored GitHub owner."
                    )

                continue

            # The same component ID cannot be claimed by a second Complete Pack.
            parent_name = str(
                claimed_pack.get("name", claimed_pack.get("id", "another Complete Pack"))
            )
            fail(
                f"Nested {PACK_DISPLAY.get(child_type, child_type)} ID '{child_id}' "
                f"is already used by the {claimed_slot} component of '{parent_name}'."
            )


def safe_text(value: object, max_length: int, field: str, allow_empty: bool = False) -> str:
    text = str(value or "").strip()
    if not allow_empty and not text:
        fail(f"{field} cannot be empty.")
    if len(text) > max_length:
        fail(f"{field} cannot exceed {max_length} characters.")
    if any(ord(ch) < 32 and ch not in "\t" for ch in text):
        fail(f"{field} contains unsupported control characters.")
    return text


def write_report(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_github_output(values: dict[str, str]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as stream:
        for key, value in values.items():
            marker = f"ANIKI_{key.upper()}_EOF"
            stream.write(f"{key}<<{marker}\n{value}\n{marker}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", choices=("auto",) + PACK_TYPES, default="auto")
    parser.add_argument("--zip", type=Path, help="Use a local ZIP instead of the issue attachment (testing).")
    parser.add_argument("--issue-body-file", type=Path, help="Read the issue body from a local file (testing).")
    parser.add_argument("--issue-body-env", default="ISSUE_BODY")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "Mike-Aniki/AnikiCommunityPacks"))
    parser.add_argument("--branch", default=os.environ.get("DEFAULT_BRANCH", "main"))
    parser.add_argument("--issue-number", default=os.environ.get("ISSUE_NUMBER", "0"))
    parser.add_argument("--submitter", default=os.environ.get("SUBMITTER_LOGIN", ""))
    parser.add_argument("--report", type=Path, default=ROOT / "publication-report.txt")
    args = parser.parse_args()

    try:
        pack_type = resolve_pack_type(args.type)
        display = PACK_DISPLAY[pack_type]

        if args.issue_body_file:
            issue_body = args.issue_body_file.read_text(encoding="utf-8")
        else:
            issue_body = os.environ.get(args.issue_body_env, "")
        if not issue_body.strip():
            fail("GitHub issue body is empty.")

        submission_type = issue_section(issue_body, "Submission type")
        if submission_type not in {"New pack", "Update to an existing pack"}:
            fail("Submission type must be 'New pack' or 'Update to an existing pack'.")

        submitter = str(args.submitter or "").strip()
        if not submitter:
            fail("The GitHub submitter login is missing, so pack ownership cannot be verified.")

        PUBLICATION_DIR.mkdir(parents=True, exist_ok=True)

        if args.zip:
            if not args.zip.is_file():
                fail(f"ZIP not found: {args.zip}")
            source_zip = args.zip.resolve()
            package_name = args.zip.name
            metadata = validate_zip(source_zip, pack_type)
        else:
            package_name, zip_url = extract_zip_url(issue_body, pack_type)
            source_zip = PUBLICATION_DIR / "submitted-pack.zip"
            download_zip(zip_url, source_zip)
            metadata = validate_zip(source_zip, pack_type)

        manifest = read_manifest(source_zip, pack_type)
        pack_id = metadata["id"]
        raw_component_ids = metadata.get("componentIds", {})
        component_ids = {
            str(kind): str(value).strip()
            for kind, value in raw_component_ids.items()
            if str(value).strip()
        } if isinstance(raw_component_ids, dict) else {}
        name = safe_text(metadata["name"], 120, "Pack name")
        author = safe_text(manifest.get("author", ""), 120, "Author", allow_empty=True)
        if not author:
            author = safe_text(issue_section(issue_body, "Author"), 120, "Author")
        version = metadata["version"]
        description = safe_text(manifest.get("description", ""), 160, "Description", allow_empty=True)

        temp_preview = PUBLICATION_DIR / "submitted-preview"
        preview_ext = extract_embedded_preview(source_zip, temp_preview)
        child_previews = (
            extract_complete_child_previews(source_zip, PUBLICATION_DIR)
            if pack_type == "complete"
            else {}
        )
        complete_components = (
            extract_complete_components(source_zip, PUBLICATION_DIR / "components")
            if pack_type == "complete"
            else {}
        )

        catalog = load_catalog()
        packs = catalog["packs"]
        existing_index = next((i for i, pack in enumerate(packs) if pack.get("id") == pack_id), None)
        existing = packs[existing_index] if existing_index is not None else None
        validate_component_id_claims(packs, pack_id, pack_type, component_ids, existing, submitter)
        today = datetime.now(timezone.utc).date().isoformat()

        if existing_index is None:
            if submission_type != "New pack":
                fail(f"Pack ID '{pack_id}' is not in the catalog, so this submission must be marked as 'New pack'.")
            published_at = today
            featured = False
            owner = submitter
            mode = "new"
            previous_version = ""
        else:
            if submission_type != "Update to an existing pack":
                fail(f"Pack ID '{pack_id}' already exists in the catalog, so this submission must be marked as an update.")
            assert existing is not None
            existing_type = str(existing.get("type", "visual")).strip().lower()
            if existing_type != pack_type:
                fail(f"Pack ID '{pack_id}' is already published as a {PACK_DISPLAY.get(existing_type, existing_type)} and cannot change type.")
            existing_owner = str(existing.get("owner", "")).strip()
            if existing_owner and existing_owner.casefold() != submitter.casefold():
                fail(
                    f"Pack ID '{pack_id}' belongs to GitHub user @{existing_owner}. "
                    f"Only that account can publish an update for this pack."
                )
            # Legacy catalog entries published before ownership tracking have no owner yet.
            # The first maintainer-approved update binds the Pack ID to that issue author.
            owner = existing_owner or submitter
            previous_version = str(existing.get("version", ""))
            if compare_semver(version, previous_version) <= 0:
                fail(f"Submitted version {version} must be newer than published version {previous_version}.")
            published_at = str(existing.get("publishedAt", today))
            featured = bool(existing.get("featured", False))
            mode = "update"

        tag = f"pack-{pack_id}-v{version}"
        asset_name = f"{pack_id}-v{version}.zip"
        package_path = PUBLICATION_DIR / asset_name
        if source_zip.resolve() != package_path.resolve():
            shutil.copy2(source_zip, package_path)

        PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)
        preview_name = f"{pack_id}{preview_ext}"
        preview_path = PREVIEWS_DIR / preview_name
        for old_ext in (".jpg", ".png"):
            old_path = PREVIEWS_DIR / f"{pack_id}{old_ext}"
            if old_path != preview_path and old_path.exists():
                old_path.unlink()
        shutil.copy2(temp_preview, preview_path)

        component_preview_urls: dict[str, str] = {}
        component_preview_dir = PREVIEWS_DIR / pack_id
        if component_preview_dir.exists():
            shutil.rmtree(component_preview_dir)
        if child_previews:
            component_preview_dir.mkdir(parents=True, exist_ok=True)
            for child_type, (temp_child_preview, child_ext) in child_previews.items():
                child_name = f"{child_type}{child_ext}"
                child_path = component_preview_dir / child_name
                shutil.copy2(temp_child_preview, child_path)
                component_preview_urls[child_type] = (
                    f"https://raw.githubusercontent.com/{args.repo}/"
                    f"{urllib.parse.quote(args.branch, safe='-._~/')}/previews/"
                    f"{urllib.parse.quote(pack_id, safe='-._~')}/{urllib.parse.quote(child_name, safe='-._~')}"
                )

        encoded_tag = urllib.parse.quote(tag, safe="-._~")
        encoded_asset = urllib.parse.quote(asset_name, safe="-._~")
        download_url = f"https://github.com/{args.repo}/releases/download/{encoded_tag}/{encoded_asset}"
        preview_url = (
            f"https://raw.githubusercontent.com/{args.repo}/"
            f"{urllib.parse.quote(args.branch, safe='-._~/')}/previews/{urllib.parse.quote(preview_name, safe='-._~')}"
        )

        entry = {
            "type": pack_type,
            "id": pack_id,
            "name": name,
            "author": author,
            "owner": owner,
            "version": version,
            "description": description,
            "previewUrl": preview_url,
            "downloadUrl": download_url,
            "publishedAt": published_at,
            "updatedAt": today,
            "featured": featured,
        }
        if component_preview_urls:
            entry["packPreviews"] = component_preview_urls
        if component_ids:
            entry["componentIds"] = component_ids

        # If an auto-exposed Complete Pack component later receives its own
        # standalone submission, keep the relationship but make the standalone
        # publication the source of truth from now on.
        if pack_type != "complete" and existing is not None:
            parent_complete_id = str(existing.get("parentCompletePackId", "")).strip()
            if parent_complete_id:
                entry["parentCompletePackId"] = parent_complete_id

        if existing_index is None:
            packs.append(entry)
        else:
            packs[existing_index] = entry

        if pack_type == "complete":
            upsert_complete_components(
                packs=packs,
                parent_entry=entry,
                components=complete_components,
                repo=args.repo,
                release_tag=tag,
                preview_urls=component_preview_urls,
            )

        sort_catalog_packs(packs)
        CATALOG_PATH.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        release_notes = [
            f"# {name} v{version}",
            "",
            description or f"Community {display} for Aniki ReMake.",
            "",
            f"**Type:** {display}",
            f"**Author:** {author}",
            f"**Pack ID:** `{pack_id}`",
            f"**Submission:** #{args.issue_number}",
        ]
        notes_path = PUBLICATION_DIR / "release-notes.md"
        notes_path.write_text("\n".join(release_notes).rstrip() + "\n", encoding="utf-8")

        metadata_path = PUBLICATION_DIR / "publication-metadata.json"
        publish_metadata = {
            "mode": mode,
            "type": pack_type,
            "pack_id": pack_id,
            "name": name,
            "author": author,
            "owner": owner,
            "version": version,
            "previous_version": previous_version,
            "tag": tag,
            "asset_name": asset_name,
            "package_path": str(package_path.relative_to(ROOT)).replace("\\", "/"),
            "preview_path": str(preview_path.relative_to(ROOT)).replace("\\", "/"),
            "release_notes_path": str(notes_path.relative_to(ROOT)).replace("\\", "/"),
            "release_title": f"{name} v{version}",
            "download_url": download_url,
            "preview_url": preview_url,
        }
        metadata_path.write_text(json.dumps(publish_metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        report = [
            "PUBLICATION READY",
            f"Type: {display}",
            f"Mode: {'Update' if mode == 'update' else 'New pack'}",
            f"Package: {package_name}",
            f"ID: {pack_id}",
            f"Name: {name}",
            f"Author: {author}",
            f"GitHub owner: @{owner}",
            f"Version: {version}",
        ]
        if previous_version:
            report.append(f"Previous version: {previous_version}")
        report.extend([f"Release tag: {tag}", f"Preview: previews/{preview_name}"])
        if pack_type == "complete":
            report.append(f"Individual components exposed: {len(complete_components)}")
            report.append(f"Component release assets: {len(complete_components)}")
        write_report(args.report, report)
        print("\n".join(report))

        write_github_output(
            {
                "pack_type": pack_type,
                "pack_display": display,
                "pack_id": pack_id,
                "name": name,
                "author": author,
                "owner": owner,
                "version": version,
                "mode": mode,
                "tag": tag,
                "asset_name": asset_name,
                "package_path": str(package_path.relative_to(ROOT)).replace("\\", "/"),
                "preview_path": str(preview_path.relative_to(ROOT)).replace("\\", "/"),
                "release_notes_path": str(notes_path.relative_to(ROOT)).replace("\\", "/"),
                "release_title": f"{name} v{version}",
            }
        )
        return 0

    except (PublishError, ValidationError, CompleteComponentError) as exc:
        report = ["PUBLICATION BLOCKED", str(exc)]
        write_report(args.report, report)
        print("\n".join(report), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
