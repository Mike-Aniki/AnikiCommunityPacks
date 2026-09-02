#!/usr/bin/env python3
"""Prepare an approved Community Pack submission for automatic publication."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

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

ALLOWED_PREVIEW_HOSTS = {
    "github.com",
    "raw.githubusercontent.com",
    "objects.githubusercontent.com",
    "user-attachments.githubusercontent.com",
}
MAX_PREVIEW_BYTES = 20 * 1024 * 1024
URL_RE = re.compile(r"https://[^\s<>()\]]+", re.IGNORECASE)
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


def preview_url_from_issue(issue_body: str) -> str:
    section = issue_section(issue_body, "Preview image")
    if not section:
        fail("No preview image was found in the 'Preview image' field.")

    urls: list[str] = []
    for raw_url in URL_RE.findall(section):
        url = raw_url.rstrip(".,;:'\"")
        if url not in urls:
            urls.append(url)

    if not urls:
        fail("No preview image link was found in the 'Preview image' field.")
    if len(urls) != 1:
        fail(f"Exactly one preview image must be attached. Found {len(urls)} links.")

    url = urls[0]
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        fail("The preview image link must use HTTPS.")
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_PREVIEW_HOSTS and not host.endswith(".githubusercontent.com"):
        fail("Attach the preview directly to the GitHub issue instead of using an external host.")
    return url


def download_limited(url: str, destination: Path, max_bytes: int) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "AnikiCommunityPacks-Publisher/2.0"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
            total = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    fail(f"The preview image is too large (maximum {max_bytes // (1024 * 1024)} MB).")
                output.write(chunk)
    except PublishError:
        raise
    except Exception as exc:
        fail(f"Could not download the preview image: {exc}")


def detect_preview_extension(path: Path) -> str:
    with path.open("rb") as stream:
        header = stream.read(16)
    if header.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    fail("The preview must be a real JPG or PNG image.")
    raise AssertionError("unreachable")


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
    parser.add_argument("--preview", type=Path, help="Use a local preview instead of the issue attachment (testing).")
    parser.add_argument("--issue-body-file", type=Path, help="Read the issue body from a local file (testing).")
    parser.add_argument("--issue-body-env", default="ISSUE_BODY")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "Mike-Aniki/AnikiCommunityPacks"))
    parser.add_argument("--branch", default=os.environ.get("DEFAULT_BRANCH", "main"))
    parser.add_argument("--issue-number", default=os.environ.get("ISSUE_NUMBER", "0"))
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
        name = safe_text(metadata["name"], 120, "Pack name")
        author = safe_text(manifest.get("author", ""), 120, "Author", allow_empty=True)
        if not author:
            author = safe_text(issue_section(issue_body, "Author"), 120, "Author")
        version = metadata["version"]
        description = safe_text(manifest.get("description", ""), 300, "Description", allow_empty=True)
        if not description:
            description = safe_text(issue_section(issue_body, "Description"), 300, "Description", allow_empty=True)

        temp_preview = PUBLICATION_DIR / "submitted-preview"
        if args.preview:
            if not args.preview.is_file():
                fail(f"Preview not found: {args.preview}")
            if args.preview.stat().st_size > MAX_PREVIEW_BYTES:
                fail("The preview image is too large (maximum 20 MB).")
            shutil.copy2(args.preview, temp_preview)
        else:
            preview_url = preview_url_from_issue(issue_body)
            download_limited(preview_url, temp_preview, MAX_PREVIEW_BYTES)
        preview_ext = detect_preview_extension(temp_preview)

        catalog = load_catalog()
        packs = catalog["packs"]
        existing_index = next((i for i, pack in enumerate(packs) if pack.get("id") == pack_id), None)
        today = datetime.now(timezone.utc).date().isoformat()

        if existing_index is None:
            if submission_type != "New pack":
                fail(f"Pack ID '{pack_id}' is not in the catalog, so this submission must be marked as 'New pack'.")
            published_at = today
            featured = False
            mode = "new"
            previous_version = ""
        else:
            if submission_type != "Update to an existing pack":
                fail(f"Pack ID '{pack_id}' already exists in the catalog, so this submission must be marked as an update.")
            existing = packs[existing_index]
            existing_type = str(existing.get("type", "visual")).strip().lower()
            if existing_type != pack_type:
                fail(f"Pack ID '{pack_id}' is already published as a {PACK_DISPLAY.get(existing_type, existing_type)} and cannot change type.")
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
            "version": version,
            "description": description,
            "previewUrl": preview_url,
            "downloadUrl": download_url,
            "publishedAt": published_at,
            "updatedAt": today,
            "featured": featured,
        }

        if existing_index is None:
            packs.append(entry)
        else:
            packs[existing_index] = entry
        packs.sort(
            key=lambda pack: (
                str(pack.get("type", "visual")).casefold(),
                str(pack.get("name", "")).casefold(),
                str(pack.get("id", "")).casefold(),
            )
        )
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
            f"Version: {version}",
        ]
        if previous_version:
            report.append(f"Previous version: {previous_version}")
        report.extend([f"Release tag: {tag}", f"Preview: previews/{preview_name}"])
        write_report(args.report, report)
        print("\n".join(report))

        write_github_output(
            {
                "pack_type": pack_type,
                "pack_display": display,
                "pack_id": pack_id,
                "name": name,
                "author": author,
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

    except (PublishError, ValidationError) as exc:
        report = ["PUBLICATION BLOCKED", str(exc)]
        write_report(args.report, report)
        print("\n".join(report), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
