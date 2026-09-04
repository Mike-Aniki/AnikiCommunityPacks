#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "catalog.json"
PREVIEWS_DIR = ROOT / "previews"

PACK_DISPLAY = {
    "visual": "Visual Pack",
    "color": "Color Pack",
    "login": "Login Pack",
    "sound": "Sound Pack",
    "complete": "Complete Pack",
}

PACK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")


class UnpublishError(Exception):
    pass


def issue_section(body: str, heading: str) -> str:
    pattern = re.compile(
        rf"(?ims)^###\s+{re.escape(heading)}\s*\n+(.*?)(?=^###\s+|\Z)"
    )
    match = pattern.search(body or "")
    if not match:
        return ""
    value = match.group(1).strip()
    if value in {"_No response_", "No response"}:
        return ""
    return value


def clean_single_line(value: str) -> str:
    return " ".join((value or "").strip().split())


def load_catalog() -> dict:
    if not CATALOG_PATH.is_file():
        raise UnpublishError("catalog.json was not found.")
    try:
        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UnpublishError(f"Could not read catalog.json: {exc}") from exc
    if not isinstance(catalog, dict) or not isinstance(catalog.get("packs"), list):
        raise UnpublishError("catalog.json does not contain a valid packs array.")
    return catalog


def requested_pack(issue_body: str) -> tuple[str, str]:
    pack_id = clean_single_line(issue_section(issue_body, "Pack ID"))
    reason = issue_section(issue_body, "Reason").strip()

    if not pack_id:
        raise UnpublishError("Pack ID is required.")
    if not PACK_ID_RE.fullmatch(pack_id):
        raise UnpublishError(
            "Pack ID contains invalid characters. Copy the permanent Pack ID exactly as published."
        )
    if not reason:
        raise UnpublishError("A removal reason is required.")
    return pack_id, reason


def find_pack(catalog: dict, pack_id: str) -> tuple[int, dict]:
    for index, pack in enumerate(catalog["packs"]):
        if str(pack.get("id", "")).strip() == pack_id:
            return index, pack
    raise UnpublishError(
        f"Pack ID '{pack_id}' is not currently published in the Community Packs catalog."
    )


def preview_paths_for(pack_id: str, pack: dict) -> list[Path]:
    paths: set[Path] = set()

    for ext in (".jpg", ".jpeg", ".png"):
        paths.add(PREVIEWS_DIR / f"{pack_id}{ext}")

    preview_url = str(pack.get("previewUrl", "")).strip()
    if preview_url:
        try:
            url_path = urllib.parse.urlparse(preview_url).path
            filename = urllib.parse.unquote(Path(url_path).name)
            if filename and Path(filename).name == filename:
                paths.add(PREVIEWS_DIR / filename)
        except Exception:
            pass

    return sorted(paths)


def release_tag_from_download_url(download_url: str) -> str:
    try:
        path_parts = [
            urllib.parse.unquote(part)
            for part in urllib.parse.urlparse(download_url).path.split("/")
            if part
        ]
        # /owner/repo/releases/download/<tag>/<asset>
        if "download" in path_parts:
            index = path_parts.index("download")
            if index + 1 < len(path_parts):
                return path_parts[index + 1]
    except Exception:
        pass
    return ""


def write_report(path: Path | None, lines: list[str]) -> None:
    text = "\n".join(lines).rstrip() + "\n"
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    print(text, end="")


def write_github_output(values: dict[str, str]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not output_path:
        return

    with open(output_path, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            value = str(value)
            if "\n" in value or "\r" in value:
                marker = f"ANIKI_{key.upper()}_EOF"
                handle.write(f"{key}<<{marker}\n{value}\n{marker}\n")
            else:
                handle.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate or apply an Aniki Community Pack unpublish request."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Validate the request only.")
    mode.add_argument("--apply", action="store_true", help="Remove the pack from the catalog.")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    try:
        issue_body = os.environ.get("ISSUE_BODY", "")
        pack_id, reason = requested_pack(issue_body)

        catalog = load_catalog()
        index, pack = find_pack(catalog, pack_id)

        pack_type = str(pack.get("type", "visual")).strip().lower()
        display = PACK_DISPLAY.get(pack_type, pack_type or "Community Pack")
        name = clean_single_line(str(pack.get("name", ""))) or pack_id
        author = clean_single_line(str(pack.get("author", "")))
        version = clean_single_line(str(pack.get("version", "")))
        download_url = str(pack.get("downloadUrl", "")).strip()
        release_tag = release_tag_from_download_url(download_url)

        removed_previews: list[str] = []
        if args.apply:
            del catalog["packs"][index]
            CATALOG_PATH.write_text(
                json.dumps(catalog, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            for preview_path in preview_paths_for(pack_id, pack):
                if preview_path.is_file():
                    preview_path.unlink()
                    removed_previews.append(
                        str(preview_path.relative_to(ROOT)).replace("\\", "/")
                    )

        status = "UNPUBLISH READY" if args.check else "PACK UNPUBLISHED FROM CATALOG"
        lines = [
            status,
            f"Type: {display}",
            f"ID: {pack_id}",
            f"Name: {name}",
            f"Author: {author or '(not specified)'}",
            f"Version: {version or '(not specified)'}",
            f"Reason: {clean_single_line(reason)}",
        ]
        if release_tag:
            lines.append(f"Archived Release tag: {release_tag}")
        if args.apply:
            if removed_previews:
                lines.append(f"Removed preview: {', '.join(removed_previews)}")
            else:
                lines.append("Removed preview: none found")
            lines.append("Existing GitHub Release: kept as archive")

        write_report(args.report, lines)
        write_github_output(
            {
                "pack_type": pack_type,
                "pack_display": display,
                "pack_id": pack_id,
                "name": name,
                "author": author,
                "version": version,
                "download_url": download_url,
                "release_tag": release_tag,
            }
        )
        return 0

    except UnpublishError as exc:
        lines = ["UNPUBLISH BLOCKED", str(exc)]
        write_report(args.report, lines)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
