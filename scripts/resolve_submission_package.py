#!/usr/bin/env python3
"""Resolve a Community Pack submission into one normal ZIP.

GitHub issue attachments are limited per file, so Aniki Pack Creator can split a
large Community submission into several small ZIP files. Each multipart ZIP
contains exactly:

    multipart.json
    chunk.bin

This script accepts either one normal pack ZIP or all multipart ZIPs attached in
the pack field, verifies multipart metadata and SHA-256 hashes, reconstructs the
original ZIP, and writes it to the requested output path.

The existing pack validator and publisher can then continue to work with one
normal ZIP exactly as before.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.parse
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from validate_submission import (
    ALLOWED_DOWNLOAD_HOSTS,
    MARKDOWN_ZIP_LINK,
    MAX_DOWNLOAD_BYTES,
    PACK_DISPLAY,
    PACK_TYPES,
    RAW_ZIP_URL,
    ValidationError,
    download,
    resolve_pack_type,
)

MAX_MULTIPART_FILES = 32
MAX_MULTIPART_ARCHIVE_BYTES = 25 * 1024 * 1024
MAX_MULTIPART_MANIFEST_BYTES = 64 * 1024
MAX_MULTIPART_CHUNK_BYTES = 20 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9A-Fa-f]{64}$")
SUBMISSION_ID_RE = re.compile(r"^[0-9A-Fa-f]{32}$")


@dataclass(frozen=True)
class SubmissionFile:
    name: str
    url: str


@dataclass(frozen=True)
class MultipartPart:
    archive_path: Path
    source_name: str
    submission_id: str
    original_file_name: str
    original_size: int
    original_sha256: str
    part: int
    total_parts: int
    chunk_size: int
    chunk_sha256: str


def fail(message: str) -> None:
    raise ValidationError(message)


def write_report(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def issue_pack_section(issue_body: str, pack_type: str) -> str:
    display = PACK_DISPLAY[pack_type]
    headings = (
        f"{display} ZIP",
        "Pack ZIP",
        f"{display} file(s)",
        "Pack file(s)",
    )
    for heading in headings:
        match = re.search(
            rf"###\s+{re.escape(heading)}\s*\n(.*?)(?=\n###\s+|\Z)",
            issue_body,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if match:
            return match.group(1)
    return issue_body


def validate_download_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        fail("Community Pack ZIP links must use HTTPS.")
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_DOWNLOAD_HOSTS and not host.endswith(".githubusercontent.com"):
        fail("Attach the Community Pack ZIP file(s) directly to the GitHub issue instead of using an external host.")


def extract_zip_files(issue_body: str, pack_type: str) -> list[SubmissionFile]:
    display = PACK_DISPLAY[pack_type]
    haystack = issue_pack_section(issue_body, pack_type)
    candidates: list[SubmissionFile] = []

    for label, url in MARKDOWN_ZIP_LINK.findall(haystack):
        if not any(existing.url == url for existing in candidates):
            candidates.append(SubmissionFile(label.strip(), url))

    for url in RAW_ZIP_URL.findall(haystack):
        if not any(existing.url == url for existing in candidates):
            name = Path(urllib.parse.urlparse(url).path).name or "submission.zip"
            candidates.append(SubmissionFile(name, url))

    if not candidates:
        fail(
            f"No ZIP attachment was found in the '{display} ZIP' field. "
            "Attach the ZIP prepared by Aniki Pack Creator, or attach every multipart ZIP if the Creator split the pack."
        )
    if len(candidates) > MAX_MULTIPART_FILES:
        fail(f"Too many ZIP attachments were found (maximum {MAX_MULTIPART_FILES}).")

    for candidate in candidates:
        validate_download_url(candidate.url)

    return candidates


def safe_original_file_name(value: object) -> str:
    name = str(value or "").strip()
    if not name or not name.lower().endswith(".zip"):
        fail("multipart.json contains an invalid originalFileName.")
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if normalized.startswith("/") or path.is_absolute() or len(path.parts) != 1 or ".." in path.parts:
        fail("multipart.json originalFileName must be a plain ZIP file name.")
    if len(name) > 180:
        fail("multipart.json originalFileName is too long.")
    return name


def require_int(manifest: dict, key: str) -> int:
    value = manifest.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        fail(f"multipart.json '{key}' must be an integer.")
    return value


def safe_zip_entries(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    bad_crc = archive.testzip()
    if bad_crc:
        fail(f"Multipart ZIP integrity check failed for: {bad_crc}")

    files: dict[str, zipfile.ZipInfo] = {}
    seen: set[str] = set()
    for info in archive.infolist():
        normalized = info.filename.replace("\\", "/")
        path = PurePosixPath(normalized)
        if not normalized or normalized.startswith("/") or path.is_absolute() or ".." in path.parts or "." in path.parts:
            fail(f"Unsafe path in multipart ZIP: {info.filename}")
        unix_mode = (info.external_attr >> 16) & 0o170000
        if unix_mode == 0o120000:
            fail(f"Symbolic links are not allowed in multipart ZIPs: {info.filename}")
        if info.flag_bits & 0x1:
            fail(f"Encrypted entries are not allowed in multipart ZIPs: {info.filename}")
        if info.is_dir():
            continue
        folded = normalized.casefold()
        if folded in seen:
            fail(f"Multipart ZIP contains duplicate file names: {normalized}")
        seen.add(folded)
        files[normalized] = info
    return files


def find_casefold(files: dict[str, zipfile.ZipInfo], name: str) -> zipfile.ZipInfo | None:
    target = name.casefold()
    return next((info for entry_name, info in files.items() if entry_name.casefold() == target), None)


def inspect_multipart_part(path: Path, source_name: str) -> MultipartPart | None:
    if path.stat().st_size > MAX_MULTIPART_ARCHIVE_BYTES:
        fail(f"Multipart file '{source_name}' is larger than 25 MB.")

    try:
        with zipfile.ZipFile(path, "r") as archive:
            files = safe_zip_entries(archive)
            manifest_entry = find_casefold(files, "multipart.json")
            chunk_entry = find_casefold(files, "chunk.bin")

            # A normal Community Pack is not a multipart archive. It is handled
            # by the existing validator after this resolver returns it.
            if manifest_entry is None and chunk_entry is None:
                return None

            if manifest_entry is None or chunk_entry is None or len(files) != 2:
                fail(
                    f"'{source_name}' looks like a multipart file but must contain exactly multipart.json and chunk.bin."
                )
            if manifest_entry.file_size <= 0 or manifest_entry.file_size > MAX_MULTIPART_MANIFEST_BYTES:
                fail(f"'{source_name}' contains an invalid multipart.json size.")
            if chunk_entry.file_size <= 0 or chunk_entry.file_size > MAX_MULTIPART_CHUNK_BYTES:
                fail(f"'{source_name}' contains an invalid chunk.bin size.")

            try:
                manifest = json.loads(archive.read(manifest_entry).decode("utf-8-sig"))
            except Exception as exc:
                fail(f"'{source_name}' contains an invalid multipart.json: {exc}")
            if not isinstance(manifest, dict):
                fail(f"'{source_name}' multipart.json must contain a JSON object.")

            if manifest.get("formatVersion") != 1:
                fail(f"'{source_name}' multipart.json formatVersion must be 1.")
            if str(manifest.get("type", "")).strip().casefold() != "aniki-community-multipart":
                fail(f"'{source_name}' is not an Aniki Community multipart file.")

            submission_id = str(manifest.get("submissionId", "")).strip()
            if not SUBMISSION_ID_RE.fullmatch(submission_id):
                fail(f"'{source_name}' contains an invalid submissionId.")

            original_file_name = safe_original_file_name(manifest.get("originalFileName"))
            original_size = require_int(manifest, "originalSize")
            if original_size <= 0 or original_size > MAX_DOWNLOAD_BYTES:
                fail(f"'{source_name}' contains an invalid originalSize (maximum 250 MB).")

            original_sha256 = str(manifest.get("originalSha256", "")).strip().lower()
            if not SHA256_RE.fullmatch(original_sha256):
                fail(f"'{source_name}' contains an invalid originalSha256.")

            part = require_int(manifest, "part")
            total_parts = require_int(manifest, "totalParts")
            if total_parts < 2 or total_parts > MAX_MULTIPART_FILES:
                fail(f"'{source_name}' contains an invalid totalParts value.")
            if part < 1 or part > total_parts:
                fail(f"'{source_name}' contains an invalid part number.")

            chunk_size = require_int(manifest, "chunkSize")
            if chunk_size != chunk_entry.file_size:
                fail(f"'{source_name}' chunkSize does not match chunk.bin.")
            if chunk_size <= 0 or chunk_size > MAX_MULTIPART_CHUNK_BYTES:
                fail(f"'{source_name}' contains an invalid chunkSize.")

            chunk_sha256 = str(manifest.get("chunkSha256", "")).strip().lower()
            if not SHA256_RE.fullmatch(chunk_sha256):
                fail(f"'{source_name}' contains an invalid chunkSha256.")

            actual_chunk_hash = hashlib.sha256()
            with archive.open(chunk_entry, "r") as chunk_stream:
                while True:
                    block = chunk_stream.read(1024 * 1024)
                    if not block:
                        break
                    actual_chunk_hash.update(block)
            if actual_chunk_hash.hexdigest() != chunk_sha256:
                fail(f"'{source_name}' chunk.bin SHA-256 does not match multipart.json.")

            return MultipartPart(
                archive_path=path,
                source_name=source_name,
                submission_id=submission_id.lower(),
                original_file_name=original_file_name,
                original_size=original_size,
                original_sha256=original_sha256,
                part=part,
                total_parts=total_parts,
                chunk_size=chunk_size,
                chunk_sha256=chunk_sha256,
            )
    except ValidationError:
        raise
    except zipfile.BadZipFile:
        # A normal pack still has to be a ZIP, but leave that final error to the
        # existing pack validator when there is only one attachment.
        return None
    except Exception as exc:
        fail(f"Could not inspect multipart file '{source_name}': {exc}")
    raise AssertionError("unreachable")


def reconstruct_multipart(parts: list[MultipartPart], output: Path) -> tuple[str, int]:
    if not parts:
        fail("No multipart files were provided.")

    reference = parts[0]
    for part in parts[1:]:
        if part.submission_id != reference.submission_id:
            fail("The attached multipart ZIPs belong to different submissions. Attach only parts from the same Creator export.")
        if part.original_file_name != reference.original_file_name:
            fail("The attached multipart ZIPs disagree on the original file name.")
        if part.original_size != reference.original_size:
            fail("The attached multipart ZIPs disagree on the original file size.")
        if part.original_sha256 != reference.original_sha256:
            fail("The attached multipart ZIPs disagree on the original SHA-256.")
        if part.total_parts != reference.total_parts:
            fail("The attached multipart ZIPs disagree on the total number of parts.")

    by_number: dict[int, MultipartPart] = {}
    for part in parts:
        if part.part in by_number:
            fail(f"Multipart part {part.part}/{reference.total_parts} was attached more than once.")
        by_number[part.part] = part

    expected = set(range(1, reference.total_parts + 1))
    missing = sorted(expected - set(by_number))
    extra = sorted(set(by_number) - expected)
    if missing:
        missing_text = ", ".join(str(number) for number in missing)
        fail(f"Multipart submission is incomplete. Missing part(s): {missing_text} of {reference.total_parts}.")
    if extra:
        fail("Multipart submission contains unexpected part numbers: " + ", ".join(map(str, extra)))
    if len(parts) != reference.total_parts:
        fail(f"Expected {reference.total_parts} multipart ZIPs but found {len(parts)}.")

    declared_total = sum(part.chunk_size for part in parts)
    if declared_total != reference.original_size:
        fail(
            f"Multipart chunk sizes total {declared_total} bytes but originalSize is {reference.original_size} bytes."
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output.with_suffix(output.suffix + ".partial")
    final_hash = hashlib.sha256()
    total_written = 0

    try:
        with temp_output.open("wb") as destination:
            for number in range(1, reference.total_parts + 1):
                part = by_number[number]
                with zipfile.ZipFile(part.archive_path, "r") as archive:
                    files = safe_zip_entries(archive)
                    chunk_entry = find_casefold(files, "chunk.bin")
                    assert chunk_entry is not None
                    with archive.open(chunk_entry, "r") as chunk_stream:
                        while True:
                            block = chunk_stream.read(1024 * 1024)
                            if not block:
                                break
                            total_written += len(block)
                            if total_written > MAX_DOWNLOAD_BYTES:
                                fail("The reconstructed pack is too large for automatic validation (maximum 250 MB).")
                            final_hash.update(block)
                            destination.write(block)

        if total_written != reference.original_size:
            fail(
                f"Reconstructed pack size is {total_written} bytes but multipart metadata expects {reference.original_size} bytes."
            )
        if final_hash.hexdigest() != reference.original_sha256:
            fail("The reconstructed pack SHA-256 does not match multipart metadata.")

        if output.exists():
            output.unlink()
        temp_output.replace(output)
    finally:
        if temp_output.exists():
            temp_output.unlink()

    return reference.original_file_name, reference.total_parts


def resolve_submission(issue_body: str, pack_type: str, output: Path) -> tuple[str, bool, int]:
    candidates = extract_zip_files(issue_body, pack_type)

    with tempfile.TemporaryDirectory(prefix=f"aniki-{pack_type}-submission-") as temp_dir:
        temp_root = Path(temp_dir)
        downloaded: list[tuple[SubmissionFile, Path]] = []
        for index, candidate in enumerate(candidates, start=1):
            destination = temp_root / f"attachment-{index:02}.zip"
            download(candidate.url, destination)
            downloaded.append((candidate, destination))

        inspected: list[MultipartPart | None] = [
            inspect_multipart_part(path, candidate.name)
            for candidate, path in downloaded
        ]

        multipart_count = sum(part is not None for part in inspected)

        if len(downloaded) == 1 and multipart_count == 0:
            # Normal submission: copy the untouched ZIP and let the existing
            # strict pack validator decide whether the package itself is valid.
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(downloaded[0][1], output)
            return downloaded[0][0].name, False, 1

        if multipart_count != len(downloaded):
            fail(
                "Do not mix a normal pack ZIP with multipart ZIPs. "
                "For a large pack, attach every .partXX-of-XX.zip file generated by Aniki Pack Creator and nothing else in the Pack ZIP field."
            )

        multipart_parts = [part for part in inspected if part is not None]
        original_name, part_count = reconstruct_multipart(multipart_parts, output)
        return original_name, True, part_count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", choices=("auto",) + PACK_TYPES, default="auto")
    parser.add_argument("--issue-body-env", default="ISSUE_BODY")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=Path("submission-resolution.txt"))
    args = parser.parse_args()

    try:
        pack_type = resolve_pack_type(args.type)
        issue_body = os.environ.get(args.issue_body_env, "")
        if not issue_body.strip():
            fail("GitHub issue body is empty.")

        package_name, multipart, part_count = resolve_submission(issue_body, pack_type, args.output)
        lines = [
            "SUBMISSION PACKAGE RESOLVED",
            f"Type: {PACK_DISPLAY[pack_type]}",
            f"Package: {package_name}",
            f"Upload: {'multipart (' + str(part_count) + ' parts)' if multipart else 'single ZIP'}",
        ]
        write_report(args.report, lines)
        print("\n".join(lines))
        return 0
    except ValidationError as exc:
        lines = ["SUBMISSION RESOLUTION FAILED", str(exc)]
        write_report(args.report, lines)
        print("\n".join(lines), file=sys.stderr)
        return 1
    except Exception as exc:
        lines = ["SUBMISSION RESOLUTION FAILED", f"Unexpected resolver error: {exc}"]
        write_report(args.report, lines)
        print("\n".join(lines), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
