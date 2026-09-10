#!/usr/bin/env python3
"""One-time migration for Complete Packs already published in the catalog.

For every existing Complete Pack, download its published ZIP, validate/extract the
nested Visual/Login/Sound/Color ZIPs, expose missing children as individual
catalog entries, and prepare the nested ZIPs to be uploaded as assets on the
existing Complete Pack GitHub Release.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from complete_components import (
    CompleteComponentError,
    extract_complete_components,
    release_tag_from_download_url,
    sort_catalog_packs,
    upsert_complete_components,
)
from validate_submission import ValidationError, download, validate_zip

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "catalog.json"
DEFAULT_OUTPUT_DIR = ROOT / "backfill-assets"
DEFAULT_MANIFEST = ROOT / "backfill-upload-manifest.json"
DEFAULT_REPORT = ROOT / "backfill-report.txt"


class BackfillError(Exception):
    pass


def fail(message: str) -> None:
    raise BackfillError(message)


def load_catalog() -> dict:
    try:
        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"Could not read catalog.json: {exc}")
    if not isinstance(catalog, dict) or not isinstance(catalog.get("packs"), list):
        fail("catalog.json has an invalid structure.")
    return catalog


def relative_to_root(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default="Mike-Aniki/AnikiCommunityPacks")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    try:
        catalog = load_catalog()
        packs = catalog["packs"]
        complete_packs = [
            pack for pack in list(packs)
            if isinstance(pack, dict) and str(pack.get("type", "")).strip().lower() == "complete"
        ]

        if args.output_dir.exists():
            shutil.rmtree(args.output_dir)
        args.output_dir.mkdir(parents=True, exist_ok=True)

        uploads: list[dict] = []
        report_lines = [
            "COMPLETE PACK COMPONENT BACKFILL",
            f"Complete Packs found: {len(complete_packs)}",
        ]

        for parent in complete_packs:
            parent_id = str(parent.get("id", "")).strip()
            parent_name = str(parent.get("name", parent_id)).strip() or parent_id
            download_url = str(parent.get("downloadUrl", "")).strip()
            release_tag = release_tag_from_download_url(download_url)
            if not parent_id or not download_url or not release_tag:
                fail(f"Complete Pack '{parent_name}' has an invalid ID or download URL.")

            work_dir = args.output_dir / parent_id
            work_dir.mkdir(parents=True, exist_ok=True)
            complete_zip = work_dir / f"{parent_id}.zip"

            print(f"Downloading Complete Pack: {parent_name}")
            download(download_url, complete_zip)
            metadata = validate_zip(complete_zip, "complete")
            if str(metadata.get("id", "")).strip() != parent_id:
                fail(
                    f"Downloaded Complete Pack '{parent_name}' has ID '{metadata.get('id')}', "
                    f"but catalog.json expects '{parent_id}'."
                )

            components_dir = work_dir / "components"
            components = extract_complete_components(complete_zip, components_dir)
            if not components:
                fail(f"Complete Pack '{parent_name}' contains no usable nested components.")

            # For legacy entries, trust the ZIP that is actually published and
            # repair componentIds if an older catalog entry was incomplete/stale.
            parent["componentIds"] = {
                kind: str(component["id"]).strip()
                for kind, component in components.items()
            }

            preview_urls = parent.get("packPreviews")
            if not isinstance(preview_urls, dict):
                preview_urls = {}

            parent_uploads = upsert_complete_components(
                packs=packs,
                parent_entry=parent,
                components=components,
                repo=args.repo,
                release_tag=release_tag,
                preview_urls=preview_urls,
            )

            for upload in parent_uploads:
                upload["path"] = relative_to_root(Path(upload["path"]))
                uploads.append(upload)

            exposed = ", ".join(
                f"{kind}:{component['id']}" for kind, component in components.items()
            )
            report_lines.append(
                f"- {parent_name} ({parent_id}): {len(components)} component(s) -> {exposed}"
            )

            # The outer Complete ZIP is not needed after extraction.
            complete_zip.unlink(missing_ok=True)

        sort_catalog_packs(packs)
        CATALOG_PATH.write_text(
            json.dumps(catalog, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        manifest = {"uploads": uploads}
        args.manifest.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        generated_count = sum(
            1 for pack in packs if isinstance(pack, dict) and pack.get("generatedFromCompletePack") is True
        )
        linked_count = sum(
            1 for pack in packs if isinstance(pack, dict) and str(pack.get("parentCompletePackId", "")).strip()
        )
        report_lines.extend(
            [
                f"Release assets to upload: {len(uploads)}",
                f"Catalog entries linked to a Complete Pack: {linked_count}",
                f"Auto-generated component entries: {generated_count}",
                "BACKFILL READY",
            ]
        )
        args.report.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
        print("\n".join(report_lines))
        return 0

    except (BackfillError, CompleteComponentError, ValidationError) as exc:
        text = f"BACKFILL BLOCKED\n{exc}\n"
        try:
            args.report.write_text(text, encoding="utf-8")
        except Exception:
            pass
        print(text, file=sys.stderr, end="")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
