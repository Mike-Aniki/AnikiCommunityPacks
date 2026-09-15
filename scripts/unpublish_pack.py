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


def normalized_name(value: str) -> str:
    return clean_single_line(value).casefold()


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


def find_pack_by_name_type(catalog: dict, pack_name: str, pack_type: str) -> tuple[int, dict]:
    wanted_name = normalized_name(pack_name)
    wanted_type = clean_single_line(pack_type).lower()

    if not wanted_name:
        raise UnpublishError("Pack name is required.")
    if wanted_type not in PACK_DISPLAY:
        raise UnpublishError(
            "Pack type must be one of: visual, login, sound, color, complete."
        )

    matches: list[tuple[int, dict]] = []
    for index, pack in enumerate(catalog["packs"]):
        current_type = clean_single_line(str(pack.get("type", "visual"))).lower()
        current_name = normalized_name(str(pack.get("name", "")))
        if current_type == wanted_type and current_name == wanted_name:
            matches.append((index, pack))

    if not matches:
        raise UnpublishError(
            f"No {PACK_DISPLAY[wanted_type]} named '{clean_single_line(pack_name)}' "
            "was found in the Community Packs catalog."
        )

    if len(matches) > 1:
        ids = ", ".join(str(pack.get("id", "(missing id)")) for _, pack in matches)
        raise UnpublishError(
            f"Multiple {PACK_DISPLAY[wanted_type]} entries named "
            f"'{clean_single_line(pack_name)}' were found ({ids}). Nothing was removed. "
            "Use the existing Pack ID unpublish flow for this ambiguous case."
        )

    return matches[0]


def preview_paths_for(pack_id: str, pack: dict) -> list[Path]:
    paths: set[Path] = set()

    for ext in (".jpg", ".jpeg", ".png"):
        paths.add(PREVIEWS_DIR / f"{pack_id}{ext}")

    preview_url = str(pack.get("previewUrl", "")).strip()
    if preview_url:
        try:
            url_path = urllib.parse.urlparse(preview_url).path
            parts = [urllib.parse.unquote(part) for part in url_path.split("/") if part]
            if "previews" in parts:
                preview_index = parts.index("previews")
                relative_parts = parts[preview_index + 1 :]
                if relative_parts and all(
                    part not in {".", ".."} and Path(part).name == part
                    for part in relative_parts
                ):
                    # Generated Complete-Pack children can share a preview stored
                    # under previews/<parent-id>/. Do not delete that shared file
                    # when removing only the child; the parent Complete Pack still
                    # needs it. Root previews and files owned by this pack are safe.
                    if len(relative_parts) == 1 or relative_parts[0] == pack_id:
                        paths.add(PREVIEWS_DIR.joinpath(*relative_parts))
        except Exception:
            pass

    component_dir = PREVIEWS_DIR / pack_id
    if component_dir.is_dir():
        for child_path in component_dir.iterdir():
            if child_path.is_file():
                paths.add(child_path)

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


def complete_children(catalog: dict, complete_pack: dict) -> list[dict]:
    parent_id = clean_single_line(str(complete_pack.get("id", "")))
    if not parent_id:
        return []

    component_ids = complete_pack.get("componentIds", {})
    explicit_ids = {
        clean_single_line(str(value))
        for value in component_ids.values()
        if clean_single_line(str(value))
    } if isinstance(component_ids, dict) else set()

    children: list[dict] = []
    seen: set[str] = set()
    for pack in catalog["packs"]:
        child_id = clean_single_line(str(pack.get("id", "")))
        if not child_id or child_id == parent_id:
            continue
        linked_parent = clean_single_line(str(pack.get("parentCompletePackId", "")))
        if linked_parent == parent_id or child_id in explicit_ids:
            if child_id not in seen:
                children.append(pack)
                seen.add(child_id)
    return children


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate or apply an Aniki Community Pack unpublish request."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Validate the request only.")
    mode.add_argument("--apply", action="store_true", help="Remove the pack from the catalog.")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--pack-id", help="Directly target a published Pack ID.")
    parser.add_argument("--pack-name", help="Target a pack by its exact catalog name.")
    parser.add_argument(
        "--pack-type",
        choices=sorted(PACK_DISPLAY),
        help="Pack type used together with --pack-name.",
    )
    parser.add_argument("--reason", help="Removal reason for direct/admin execution.")
    args = parser.parse_args()

    try:
        catalog = load_catalog()

        direct_mode = bool(args.pack_id or args.pack_name or args.pack_type or args.reason)
        if direct_mode:
            reason = (args.reason or "").strip()
            if not reason:
                raise UnpublishError("--reason is required for direct/admin removal.")

            if args.pack_id:
                if args.pack_name or args.pack_type:
                    raise UnpublishError(
                        "Use either --pack-id or --pack-name with --pack-type, not both."
                    )
                pack_id = clean_single_line(args.pack_id)
                if not PACK_ID_RE.fullmatch(pack_id):
                    raise UnpublishError("--pack-id contains invalid characters.")
                index, pack = find_pack(catalog, pack_id)
            else:
                if not args.pack_name or not args.pack_type:
                    raise UnpublishError(
                        "--pack-name and --pack-type are both required when --pack-id is not used."
                    )
                index, pack = find_pack_by_name_type(catalog, args.pack_name, args.pack_type)
                pack_id = clean_single_line(str(pack.get("id", "")))
        else:
            issue_body = os.environ.get("ISSUE_BODY", "")
            pack_id, reason = requested_pack(issue_body)
            index, pack = find_pack(catalog, pack_id)

        pack_type = str(pack.get("type", "visual")).strip().lower()
        display = PACK_DISPLAY.get(pack_type, pack_type or "Community Pack")
        name = clean_single_line(str(pack.get("name", ""))) or pack_id
        author = clean_single_line(str(pack.get("author", "")))
        version = clean_single_line(str(pack.get("version", "")))
        download_url = str(pack.get("downloadUrl", "")).strip()
        release_tag = release_tag_from_download_url(download_url)

        child_packs = complete_children(catalog, pack) if pack_type == "complete" else []
        removed_pack_ids = [pack_id] + [
            clean_single_line(str(child.get("id", ""))) for child in child_packs
        ]
        removed_pack_ids = [value for value in removed_pack_ids if value]
        removed_id_set = set(removed_pack_ids)

        removed_previews: list[str] = []
        if args.apply:
            # Complete Packs are bundles. Removing a problematic Complete Pack must
            # also remove every individually exposed component generated from it.
            packs_being_removed = [pack] + child_packs
            catalog["packs"] = [
                current
                for current in catalog["packs"]
                if clean_single_line(str(current.get("id", ""))) not in removed_id_set
            ]
            CATALOG_PATH.write_text(
                json.dumps(catalog, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            preview_paths: set[Path] = set()
            for removed_pack in packs_being_removed:
                removed_id = clean_single_line(str(removed_pack.get("id", "")))
                preview_paths.update(preview_paths_for(removed_id, removed_pack))

            for preview_path in sorted(preview_paths):
                if preview_path.is_file():
                    preview_path.unlink()
                    removed_previews.append(
                        str(preview_path.relative_to(ROOT)).replace("\\", "/")
                    )

            # Clean empty preview folders for the parent and any removed children.
            for removed_id in sorted(removed_id_set, key=len, reverse=True):
                preview_dir = PREVIEWS_DIR / removed_id
                if preview_dir.is_dir() and not any(preview_dir.iterdir()):
                    preview_dir.rmdir()

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
        if child_packs:
            child_summary = ", ".join(
                f"{clean_single_line(str(child.get('name', ''))) or child.get('id')} "
                f"({clean_single_line(str(child.get('id', '')))})"
                for child in child_packs
            )
            action = "Will also remove generated children" if args.check else "Removed generated children"
            lines.append(f"{action}: {child_summary}")
        if release_tag:
            lines.append(f"Archived Release tag: {release_tag}")
        if args.apply:
            if removed_previews:
                lines.append(f"Removed preview: {', '.join(removed_previews)}")
            else:
                lines.append("Removed preview: none found")
            lines.append(f"Catalog entries removed: {len(removed_pack_ids)}")
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
                "removed_count": str(len(removed_pack_ids) if args.apply else 0),
                "removed_ids": ",".join(removed_pack_ids),
            }
        )
        return 0

    except UnpublishError as exc:
        lines = ["UNPUBLISH BLOCKED", str(exc)]
        write_report(args.report, lines)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
