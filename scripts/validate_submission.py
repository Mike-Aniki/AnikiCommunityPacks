#!/usr/bin/env python3
"""Validate Aniki Community Pack ZIPs submitted through GitHub issues.

The validator supports Visual, Color, Login, Sound and Complete Packs exported by
Aniki Pack Creator. Every pack type has its own strict archive rules, while the
GitHub workflow and moderation flow stay shared.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import struct
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

PACK_TYPES = ("visual", "color", "login", "sound", "complete")
PACK_LABELS = {pack_type: f"{pack_type}-pack-submission" for pack_type in PACK_TYPES}
PACK_DISPLAY = {
    "visual": "Visual Pack",
    "color": "Color Pack",
    "login": "Login Pack",
    "sound": "Sound Pack",
    "complete": "Complete Pack",
}
MANIFESTS = {
    "visual": "visualpack.json",
    "color": "colorpack.json",
    "login": "loginpack.json",
    "sound": "soundpack.json",
    "complete": "completepack.json",
}
EXPECTED_MANIFEST_TYPES = {
    "visual": None,  # Current Visual exports intentionally omit a type field.
    "color": "colorPack",
    "login": "loginPack",
    "sound": "soundPack",
    "complete": "completePack",
}
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
PACK_ID = re.compile(r"^[A-Za-z0-9._-]+$")
MARKDOWN_ZIP_LINK = re.compile(r"\[([^\]]+\.zip)\]\((https://[^)\s]+)\)", re.IGNORECASE)
RAW_ZIP_URL = re.compile(r"https://[^\s<>()\]]+\.zip(?:\?[^\s<>()\]]*)?", re.IGNORECASE)
ALLOWED_DOWNLOAD_HOSTS = {
    "github.com",
    "raw.githubusercontent.com",
    "objects.githubusercontent.com",
    "user-attachments.githubusercontent.com",
}
MAX_DOWNLOAD_BYTES = 250 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 500 * 1024 * 1024
MAX_PREVIEW_BYTES = 20 * 1024 * 1024
PREVIEW_ENTRY_NAMES = {"preview.jpg", "preview.png"}
MAX_MANIFEST_BYTES = 128 * 1024
MAX_COLOR_XAML_BYTES = 2 * 1024 * 1024
MAX_LOGIN_VIDEO_BYTES = 50 * 1024 * 1024  # Creator requires strictly less than 50 MB.

EXPECTED_VISUAL_IMAGES = {
    "MainBackground.jpg": (1920, 1080),
    "Welcome.jpg": (1920, 1080),
    "StatView.jpg": (1920, 1080),
    "FriendsView.jpg": (1920, 1080),
    "AchievementsView.jpg": (1920, 1080),
    "MediaView.jpg": (1920, 1080),
    "StoreView.jpg": (1920, 1080),
    "MainMenu.jpg": (531, 986),
    "SettingsBackground.jpg": (487, 1080),
    "FrameSettingsBackground.jpg": (1247, 900),
    "MessageBox.jpg": (830, 429),
    "GameMenu.jpg": (470, 655),
    "ItemMenu.jpg": (503, 818),
    "Login.jpg": (857, 238),
}

SOUND_SLOTS = {
    "navigation": ("audio/navigation.wav", "sound", "wav"),
    "activation": ("audio/activation.wav", "sound", "wav"),
    "change-display": ("audio/ChangeDisplay.wav", "sound", "wav"),
    "enter-game-details": ("audio/EnterGameDetails.wav", "sound", "wav"),
    "exit-game-details": ("audio/ExitGameDetails.wav", "sound", "wav"),
    "home-hub-close": ("audio/HomeHubClose.wav", "sound", "wav"),
    "open-additional-view": ("audio/OpenAdditionalView.wav", "sound", "wav"),
    "notification": ("audio/Noti.wav", "sound", "wav"),
    "session-summary": ("audio/SessionSummary.wav", "sound", "wav"),
    "warning": ("audio/Warning.wav", "sound", "wav"),
    "application-stopped": ("audio/Events/ApplicationStopped.wav", "sound", "wav"),
    "game-installed": ("audio/Events/GameInstalled.wav", "sound", "wav"),
    "game-starting": ("audio/Events/GameStarting.wav", "sound", "wav"),
    "game-started": ("audio/Events/GameStarted.wav", "sound", "wav"),
    "game-stopped": ("audio/Events/GameStopped.wav", "sound", "wav"),
    "game-uninstalled": ("audio/Events/GameUninstalled.wav", "sound", "wav"),
    "library-updated": ("audio/Events/LibraryUpdated.wav", "sound", "wav"),
    "login-music": ("audio/LoginOST.mp3", "music", "mp3"),
    "hub-music": ("audio/HubOST.mp3", "music", "mp3"),
    "secondary-views-music": ("audio/SecondaryViewsOST.mp3", "music", "mp3"),
}
SOUND_TARGET_TO_KEY = {target.casefold(): key for key, (target, _, _) in SOUND_SLOTS.items()}

PRESENTATION_NS = "http://schemas.microsoft.com/winfx/2006/xaml/presentation"
XAML_NS = "http://schemas.microsoft.com/winfx/2006/xaml"
SYSTEM_NS = "clr-namespace:System;assembly=mscorlib"
ALLOWED_COLOR_XAML_ELEMENTS = {
    "ResourceDictionary",
    "Color",
    "SolidColorBrush",
    "LinearGradientBrush",
    "RadialGradientBrush",
    "GradientStop",
    "RotateTransform",
    "LinearGradientBrush.RelativeTransform",
    "Style",
    "Setter",
}
MASTER_COLOR_KEYS = {
    "primaryAccent",
    "secondaryAccent",
    "focus",
    "actionButtons",
    "progress",
    "background",
    "bars",
    "menus",
    "menuHeader",
    "cards",
    "border",
    "notifications",
    "primaryText",
    "secondaryText",
    "highlightText",
}
COLOR_VALUE = re.compile(r"^#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?$")


class ValidationError(Exception):
    pass


def fail(message: str) -> None:
    raise ValidationError(message)


def resolve_pack_type(explicit: str | None = None) -> str:
    if explicit and explicit != "auto":
        if explicit not in PACK_TYPES:
            fail(f"Unsupported pack type: {explicit}")
        return explicit

    raw_labels = os.environ.get("ISSUE_LABELS", "")
    labels: list[str] = []
    if raw_labels:
        try:
            parsed = json.loads(raw_labels)
            if isinstance(parsed, list):
                labels = [str(item) for item in parsed]
        except Exception:
            labels = [item.strip() for item in raw_labels.split(",") if item.strip()]

    matches = [pack_type for pack_type, label in PACK_LABELS.items() if label in labels]
    if len(matches) != 1:
        fail("The submission issue must contain exactly one Community Pack type label.")
    return matches[0]


def extract_zip_url(issue_body: str, pack_type: str) -> tuple[str, str]:
    display = PACK_DISPLAY[pack_type]
    headings = [f"{display} ZIP", "Pack ZIP"]
    haystack = issue_body
    for heading in headings:
        section_match = re.search(
            rf"###\s+{re.escape(heading)}\s*\n(.*?)(?=\n###\s+|\Z)",
            issue_body,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if section_match:
            haystack = section_match.group(1)
            break

    candidates: list[tuple[str, str]] = []
    for label, url in MARKDOWN_ZIP_LINK.findall(haystack):
        candidates.append((label, url))
    for url in RAW_ZIP_URL.findall(haystack):
        if not any(existing_url == url for _, existing_url in candidates):
            name = Path(urllib.parse.urlparse(url).path).name or "submission.zip"
            candidates.append((name, url))

    if not candidates:
        fail(f"No .zip attachment or direct .zip link was found in the '{display} ZIP' field.")
    if len(candidates) != 1:
        names = ", ".join(name for name, _ in candidates)
        fail(f"Exactly one {display} ZIP must be attached. Found {len(candidates)}: {names}")

    name, url = candidates[0]
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        fail("The ZIP download link must use HTTPS.")
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_DOWNLOAD_HOSTS and not host.endswith(".githubusercontent.com"):
        fail("For automatic validation, attach the ZIP directly to the GitHub issue instead of using an external host.")
    return name, url


def download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "AnikiCommunityPacks-Validator/2.0"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
            total = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    fail("The submitted ZIP is too large for automatic validation (maximum 250 MB).")
                output.write(chunk)
    except ValidationError:
        raise
    except Exception as exc:
        fail(f"Could not download the submitted ZIP: {exc}")


def safe_archive_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or normalized.startswith("/") or path.is_absolute() or ".." in path.parts or "." in path.parts:
        fail(f"Unsafe ZIP path: {name}")
    return normalized


def archive_files(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    bad_crc = archive.testzip()
    if bad_crc:
        fail(f"ZIP integrity check failed for: {bad_crc}")

    infos = archive.infolist()
    if not infos:
        fail("The ZIP is empty.")

    files: dict[str, zipfile.ZipInfo] = {}
    seen_casefold: set[str] = set()
    total = 0
    for info in infos:
        name = safe_archive_name(info.filename)
        unix_mode = (info.external_attr >> 16) & 0o170000
        if unix_mode == 0o120000:
            fail(f"Symbolic links are not allowed: {name}")
        if info.flag_bits & 0x1:
            fail(f"Encrypted ZIP entries are not allowed: {name}")
        if info.is_dir():
            continue
        folded = name.casefold()
        if folded in seen_casefold:
            fail(f"The ZIP contains duplicate file names: {name}")
        seen_casefold.add(folded)
        files[name] = info
        total += info.file_size
        if total > MAX_UNCOMPRESSED_BYTES:
            fail("The uncompressed package is unexpectedly large (maximum 500 MB).")
    if not files:
        fail("The ZIP does not contain any files.")
    return files


def find_entry(files: dict[str, zipfile.ZipInfo], expected: str) -> zipfile.ZipInfo | None:
    target = expected.casefold()
    return next((info for name, info in files.items() if name.casefold() == target), None)


def require_exact_files(files: dict[str, zipfile.ZipInfo], expected: set[str], pack_name: str) -> None:
    actual_folded = {name.casefold(): name for name in files}
    expected_folded = {name.casefold(): name for name in expected}
    missing = [expected_folded[key] for key in expected_folded.keys() - actual_folded.keys()]
    extra = [
        actual_folded[key]
        for key in actual_folded.keys() - expected_folded.keys()
        if key not in PREVIEW_ENTRY_NAMES
    ]
    if missing:
        fail(f"{pack_name} is missing required file(s): " + ", ".join(sorted(missing)))
    if extra:
        fail(f"{pack_name} contains unexpected file(s): " + ", ".join(sorted(extra)))


def read_json_manifest(archive: zipfile.ZipFile, files: dict[str, zipfile.ZipInfo], manifest_name: str) -> dict:
    entry = find_entry(files, manifest_name)
    if entry is None:
        fail(f"Missing required manifest: {manifest_name}")
    if entry.file_size <= 0 or entry.file_size > MAX_MANIFEST_BYTES:
        fail(f"{manifest_name} has an invalid file size.")
    try:
        value = json.loads(archive.read(entry).decode("utf-8-sig"))
    except Exception as exc:
        fail(f"{manifest_name} is invalid: {exc}")
    if not isinstance(value, dict):
        fail(f"{manifest_name} must contain a JSON object.")
    return value


def common_manifest_metadata(manifest: dict, pack_type: str) -> dict[str, str]:
    manifest_name = MANIFESTS[pack_type]
    if manifest.get("formatVersion") != 1:
        fail(f"{manifest_name} formatVersion must be 1.")

    expected_type = EXPECTED_MANIFEST_TYPES[pack_type]
    actual_type = str(manifest.get("type", "")).strip()
    if expected_type and actual_type.casefold() != expected_type.casefold():
        fail(f"{manifest_name} type must be '{expected_type}'.")
    if pack_type == "visual" and actual_type and actual_type.casefold() not in {"visual", "visualpack"}:
        fail(f"{manifest_name} contains an unexpected type value: {actual_type}")

    pack_id = str(manifest.get("id", "")).strip()
    name = str(manifest.get("name", "")).strip()
    author = str(manifest.get("author", "")).strip()
    version = str(manifest.get("version", "")).strip()
    description = str(manifest.get("description", "")).strip()

    if not pack_id or not PACK_ID.fullmatch(pack_id):
        fail(f"{manifest_name} contains an invalid or missing pack ID.")
    if not name or len(name) > 120:
        fail(f"{manifest_name} contains an invalid pack name.")
    if len(author) > 120:
        fail(f"{manifest_name} author cannot exceed 120 characters.")
    if not SEMVER.fullmatch(version):
        fail(f"{manifest_name} contains an invalid semantic version.")
    if len(description) > 160:
        fail(f"{manifest_name} description cannot exceed 160 characters.")

    return {
        "type": pack_type,
        "id": pack_id,
        "name": name,
        "author": author or "(not specified)",
        "version": version,
        "description": description,
    }


def jpeg_dimensions(stream) -> tuple[int, int]:
    if stream.read(2) != b"\xff\xd8":
        fail("Image is not a valid JPEG file.")
    while True:
        prefix = stream.read(1)
        if not prefix:
            fail("JPEG ended before image dimensions were found.")
        if prefix != b"\xff":
            continue
        marker = stream.read(1)
        while marker == b"\xff":
            marker = stream.read(1)
        if not marker:
            fail("Invalid JPEG marker.")
        marker_value = marker[0]
        if marker_value in {0xD8, 0xD9} or 0xD0 <= marker_value <= 0xD7:
            continue
        length_bytes = stream.read(2)
        if len(length_bytes) != 2:
            fail("Invalid JPEG segment length.")
        segment_length = int.from_bytes(length_bytes, "big")
        if segment_length < 2:
            fail("Invalid JPEG segment.")
        if marker_value in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            data = stream.read(segment_length - 2)
            if len(data) < 5:
                fail("Invalid JPEG size segment.")
            return int.from_bytes(data[3:5], "big"), int.from_bytes(data[1:3], "big")
        stream.seek(segment_length - 2, 1)


def validate_embedded_preview(
    archive: zipfile.ZipFile,
    files: dict[str, zipfile.ZipInfo],
    required: bool,
) -> str | None:
    matches = [
        info
        for name, info in files.items()
        if name.casefold() in PREVIEW_ENTRY_NAMES
    ]
    if len(matches) > 1:
        fail("The pack must contain only one preview image: preview.jpg or preview.png.")
    if not matches:
        if required:
            fail("The pack is missing its embedded preview image (preview.jpg or preview.png). Export it again with Aniki Pack Creator.")
        return None

    entry = matches[0]
    if entry.file_size <= 0 or entry.file_size > MAX_PREVIEW_BYTES:
        fail("The embedded preview image has an invalid size (maximum 20 MB).")

    data = archive.read(entry)
    lower_name = entry.filename.casefold()
    if lower_name == "preview.jpg":
        if not data.startswith(b"\xff\xd8\xff"):
            fail("preview.jpg is not a real JPEG image.")
        jpeg_dimensions(io.BytesIO(data))
    elif lower_name == "preview.png":
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            fail("preview.png is not a real PNG image.")
        if len(data) < 24 or data[12:16] != b"IHDR":
            fail("preview.png is missing a valid PNG IHDR header.")
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        if width <= 0 or height <= 0:
            fail("preview.png has invalid dimensions.")
    return entry.filename


def validate_visual(archive: zipfile.ZipFile, files: dict[str, zipfile.ZipInfo]) -> dict[str, str]:
    expected = set(EXPECTED_VISUAL_IMAGES) | {"visualpack.json"}
    require_exact_files(files, expected, "Visual Pack")
    for file_name, expected_size in EXPECTED_VISUAL_IMAGES.items():
        entry = find_entry(files, file_name)
        assert entry is not None
        with archive.open(entry, "r") as stream:
            actual_size = jpeg_dimensions(stream)
        if actual_size != expected_size:
            fail(
                f"{file_name} has invalid dimensions: {actual_size[0]}x{actual_size[1]} "
                f"(expected {expected_size[0]}x{expected_size[1]})."
            )
    manifest = read_json_manifest(archive, files, "visualpack.json")
    metadata = common_manifest_metadata(manifest, "visual")
    metadata["details"] = "14/14 required images"
    return metadata


def xml_local_name(tag: str) -> tuple[str, str]:
    if tag.startswith("{") and "}" in tag:
        namespace, local = tag[1:].split("}", 1)
        return namespace, local
    return "", tag


def validate_color_xaml(data: bytes) -> None:
    if not data or len(data) > MAX_COLOR_XAML_BYTES:
        fail("colors.xaml has an invalid file size (maximum 2 MB).")
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        fail("colors.xaml cannot contain DTD or entity declarations.")
    try:
        root = ET.fromstring(data)
    except Exception as exc:
        fail(f"colors.xaml is not valid XML: {exc}")
    root_ns, root_local = xml_local_name(root.tag)
    if root_ns != PRESENTATION_NS or root_local != "ResourceDictionary":
        fail("colors.xaml must contain a WPF ResourceDictionary.")

    elements = list(root.iter())
    if len(elements) > 2000:
        fail("colors.xaml contains too many XAML elements.")

    seen_keys: set[str] = set()
    has_color = False
    for element in elements:
        ns, local = xml_local_name(element.tag)
        if ns == SYSTEM_NS and local == "Int32":
            key = element.attrib.get(f"{{{XAML_NS}}}Key", "")
            if key.casefold() == "backgroundimageindex":
                continue
        if ns != PRESENTATION_NS or local not in ALLOWED_COLOR_XAML_ELEMENTS:
            fail(f"colors.xaml contains a forbidden XAML element: {element.tag}")
        if local == "Color":
            has_color = True
        for attr_name, value in element.attrib.items():
            attr_ns, attr_local = xml_local_name(attr_name)
            if attr_ns:
                if attr_ns != XAML_NS or attr_local != "Key":
                    fail(f"colors.xaml contains a forbidden XAML attribute: {attr_name}")
            elif attr_local.casefold() == "source":
                fail("colors.xaml cannot reference an external ResourceDictionary.")
            text = (value or "").strip()
            if "{" in text:
                allowed = (
                    (text.startswith("{DynamicResource ") or text.startswith("{StaticResource "))
                    and text.endswith("}")
                )
                if not allowed:
                    fail("colors.xaml contains a forbidden markup extension.")
        key = element.attrib.get(f"{{{XAML_NS}}}Key", "").strip()
        if key:
            folded = key.casefold()
            if folded in seen_keys:
                fail(f"colors.xaml contains a duplicate resource key: {key}")
            seen_keys.add(folded)
    if not has_color:
        fail("colors.xaml does not contain any color resources.")


def validate_color(archive: zipfile.ZipFile, files: dict[str, zipfile.ZipInfo]) -> dict[str, str]:
    require_exact_files(files, {"colorpack.json", "colors.xaml"}, "Color Pack")
    manifest = read_json_manifest(archive, files, "colorpack.json")
    metadata = common_manifest_metadata(manifest, "color")
    if str(manifest.get("template", "")).strip().casefold() != "3.goldengraphite":
        fail("colorpack.json uses an unsupported Color Pack template.")
    if str(manifest.get("resource", "")).strip().casefold() != "colors.xaml":
        fail("colorpack.json resource must be colors.xaml.")
    master_colors = manifest.get("masterColors")
    if not isinstance(master_colors, dict) or set(master_colors) != MASTER_COLOR_KEYS:
        fail("colorpack.json must contain exactly the 15 expected master colors.")
    for key in MASTER_COLOR_KEYS:
        if not COLOR_VALUE.fullmatch(str(master_colors.get(key, "")).strip()):
            fail(f"colorpack.json contains an invalid master color: {key}")
    colors_entry = find_entry(files, "colors.xaml")
    assert colors_entry is not None
    validate_color_xaml(archive.read(colors_entry))
    metadata["details"] = "15 master colors + validated colors.xaml"
    return metadata


def _read_u32(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def _read_u64(data: bytes, offset: int) -> int:
    return struct.unpack_from(">Q", data, offset)[0]


def _boxes(data: bytes, start: int = 0, end: int | None = None):
    if end is None:
        end = len(data)
    pos = start
    while pos + 8 <= end:
        size32 = _read_u32(data, pos)
        box_type = data[pos + 4:pos + 8].decode("latin1")
        header = 8
        if size32 == 1:
            if pos + 16 > end:
                fail(f"Invalid MP4 box header for '{box_type}'.")
            size = _read_u64(data, pos + 8)
            header = 16
        elif size32 == 0:
            size = end - pos
        else:
            size = size32
        if size < header or pos + size > end:
            fail(f"Invalid MP4 box bounds for '{box_type}'.")
        yield box_type, pos + header, pos + size
        pos += size


def inspect_mp4(data: bytes) -> str:
    has_ftyp = False
    video_codecs: list[str] = []

    def parse_range(start: int, end: int, track: dict | None = None) -> None:
        nonlocal has_ftyp
        for box_type, payload_start, box_end in _boxes(data, start, end):
            if box_type == "ftyp":
                has_ftyp = True
            elif box_type in {"moov", "mdia", "minf", "stbl"}:
                parse_range(payload_start, box_end, track)
            elif box_type == "trak":
                current: dict[str, str] = {}
                parse_range(payload_start, box_end, current)
                if current.get("handler") == "vide" and current.get("codec"):
                    video_codecs.append(current["codec"])
            elif box_type == "hdlr" and track is not None:
                payload = data[payload_start:box_end]
                if len(payload) >= 12:
                    track["handler"] = payload[8:12].decode("latin1")
            elif box_type == "stsd" and track is not None:
                payload = data[payload_start:box_end]
                if len(payload) >= 16 and _read_u32(payload, 4) > 0:
                    track["codec"] = payload[12:16].decode("latin1")

    parse_range(0, len(data))
    if not has_ftyp:
        fail("Login.mp4 is not a valid MP4 container (ftyp box missing).")
    if not video_codecs:
        fail("Login.mp4 does not contain a readable video track.")
    codec = video_codecs[0].lower()
    if codec not in {"avc1", "avc3", "hvc1", "hev1"}:
        fail(f"Login.mp4 uses an unsupported video codec: {codec}")
    return codec


def validate_login(archive: zipfile.ZipFile, files: dict[str, zipfile.ZipInfo]) -> dict[str, str]:
    require_exact_files(files, {"loginpack.json", "Login.mp4"}, "Login Pack")
    manifest = read_json_manifest(archive, files, "loginpack.json")
    metadata = common_manifest_metadata(manifest, "login")
    video = manifest.get("video")
    if not isinstance(video, dict):
        fail("loginpack.json must contain a video object.")
    if str(video.get("target", "")).strip().casefold() != "login.mp4":
        fail("loginpack.json video target must be Login.mp4.")
    if str(video.get("container", "")).strip().casefold() != "mp4":
        fail("loginpack.json video container must be mp4.")
    video_entry = find_entry(files, "Login.mp4")
    assert video_entry is not None
    if video_entry.file_size <= 0 or video_entry.file_size >= MAX_LOGIN_VIDEO_BYTES:
        fail("Login.mp4 must be smaller than 50 MB.")
    codec = inspect_mp4(archive.read(video_entry))
    manifest_codec = str(video.get("codec", "")).strip().lower()
    if manifest_codec and manifest_codec != codec:
        fail(f"loginpack.json codec '{manifest_codec}' does not match Login.mp4 codec '{codec}'.")
    metadata["details"] = f"Login.mp4 validated ({codec})"
    return metadata


def validate_wav(data: bytes, target: str) -> None:
    # Match Aniki Pack Creator: a Sound Pack WAV must be a RIFF/WAVE file.
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        fail(f"{target} is not a valid WAV file.")


def validate_mp3(data: bytes, target: str) -> None:
    if len(data) < 2:
        fail(f"{target} is not a valid MP3 file.")
    if data[:3] == b"ID3":
        return
    sample = data[:4096]
    for index in range(len(sample) - 1):
        if sample[index] == 0xFF and (sample[index + 1] & 0xE0) == 0xE0:
            return
    fail(f"{target} is not a valid MP3 file.")


def validate_sound(archive: zipfile.ZipFile, files: dict[str, zipfile.ZipInfo]) -> dict[str, str]:
    manifest = read_json_manifest(archive, files, "soundpack.json")
    metadata = common_manifest_metadata(manifest, "sound")
    sounds = manifest.get("sounds")
    if not isinstance(sounds, list) or not sounds:
        fail("soundpack.json must contain at least one audio item.")
    if len(sounds) > len(SOUND_SLOTS):
        fail(f"soundpack.json cannot contain more than {len(SOUND_SLOTS)} audio items.")

    expected_files = {"soundpack.json"}
    seen_keys: set[str] = set()
    seen_targets: set[str] = set()
    for item in sounds:
        if not isinstance(item, dict):
            fail("Every soundpack.json audio item must be an object.")
        key = str(item.get("key", "")).strip()
        target = str(item.get("target", "")).strip().replace("\\", "/")
        kind = str(item.get("kind", "")).strip().lower()
        fmt = str(item.get("format", "")).strip().lower()
        if key not in SOUND_SLOTS:
            fail(f"soundpack.json contains an unsupported audio key: {key or '(empty)'}")
        expected_target, expected_kind, expected_format = SOUND_SLOTS[key]
        if target.casefold() != expected_target.casefold():
            fail(f"Audio key '{key}' must target '{expected_target}'.")
        if kind != expected_kind or fmt != expected_format:
            fail(f"Audio key '{key}' must use kind '{expected_kind}' and format '{expected_format}'.")
        if key in seen_keys or target.casefold() in seen_targets:
            fail(f"soundpack.json contains a duplicate audio item: {key}")
        seen_keys.add(key)
        seen_targets.add(target.casefold())
        expected_files.add(expected_target)

    require_exact_files(files, expected_files, "Sound Pack")
    for key in seen_keys:
        target, _, fmt = SOUND_SLOTS[key]
        entry = find_entry(files, target)
        assert entry is not None
        data = archive.read(entry)
        if fmt == "wav":
            validate_wav(data, target)
        else:
            validate_mp3(data, target)
    metadata["details"] = f"{len(seen_keys)}/{len(SOUND_SLOTS)} optional audio slots included"
    return metadata


def validate_complete(archive: zipfile.ZipFile, files: dict[str, zipfile.ZipInfo]) -> dict[str, str]:
    allowed = {
        "completepack.json",
        "packs/visual.zip",
        "packs/login.zip",
        "packs/sound.zip",
        "packs/color.zip",
        "preview.jpg",
        "preview.png",
    }
    actual = set(files)
    extra = [name for name in actual if name.casefold() not in {item.casefold() for item in allowed}]
    if extra:
        fail("Complete Pack contains unexpected file(s): " + ", ".join(sorted(extra)))
    for required in ("completepack.json", "packs/visual.zip", "packs/login.zip"):
        if find_entry(files, required) is None:
            fail(f"Complete Pack is missing required file: {required}")

    manifest = read_json_manifest(archive, files, "completepack.json")
    metadata = common_manifest_metadata(manifest, "complete")
    if str(manifest.get("behavior", "")).strip().casefold() != "installseparatepacks":
        fail("completepack.json behavior must be installSeparatePacks.")
    declared = manifest.get("packs")
    if not isinstance(declared, dict):
        fail("completepack.json must contain a packs object.")

    children: list[str] = []
    child_map = {
        "visual": "packs/visual.zip",
        "login": "packs/login.zip",
        "sound": "packs/sound.zip",
        "color": "packs/color.zip",
    }
    for child_type, entry_name in child_map.items():
        entry = find_entry(files, entry_name)
        declaration = declared.get(child_type)
        if entry is None:
            if declaration not in (None, {}):
                fail(f"completepack.json declares '{child_type}' but {entry_name} is missing.")
            continue
        if not isinstance(declaration, dict):
            fail(f"completepack.json must describe the included {child_type} pack.")
        declared_file = str(declaration.get("file", "")).strip().replace("\\", "/")
        if declared_file.casefold() != entry_name.casefold():
            fail(f"completepack.json {child_type}.file must be '{entry_name}'.")
        child_bytes = archive.read(entry)
        if len(child_bytes) > MAX_DOWNLOAD_BYTES:
            fail(f"Nested {PACK_DISPLAY[child_type]} ZIP is too large.")
        child_meta = validate_zip_stream(io.BytesIO(child_bytes), child_type, require_preview=False)
        for field in ("id", "name", "version"):
            declared_value = str(declaration.get(field, "")).strip()
            if declared_value != child_meta[field]:
                fail(f"completepack.json {child_type}.{field} does not match the nested {PACK_DISPLAY[child_type]}.")
        declared_kind = str(declaration.get("kind", "")).strip().lower()
        if declared_kind and declared_kind != child_type:
            fail(f"completepack.json {child_type}.kind must be '{child_type}'.")
        children.append(child_type)

    metadata["details"] = "Nested packs validated: " + ", ".join(PACK_DISPLAY[item] for item in children)
    return metadata


VALIDATORS = {
    "visual": validate_visual,
    "color": validate_color,
    "login": validate_login,
    "sound": validate_sound,
    "complete": validate_complete,
}


def validate_zip_stream(stream, pack_type: str, require_preview: bool = True) -> dict[str, str]:
    try:
        with zipfile.ZipFile(stream, "r") as archive:
            files = archive_files(archive)
            preview_name = validate_embedded_preview(archive, files, required=require_preview)
            metadata = VALIDATORS[pack_type](archive, files)
            if preview_name:
                metadata["preview"] = preview_name
            return metadata
    except ValidationError:
        raise
    except zipfile.BadZipFile as exc:
        fail(f"The submitted file is not a valid ZIP archive: {exc}")
    except Exception as exc:
        fail(f"Could not validate the {PACK_DISPLAY[pack_type]} ZIP: {exc}")
    raise AssertionError("unreachable")


def validate_zip(zip_path: Path, pack_type: str) -> dict[str, str]:
    if not zip_path.is_file():
        fail(f"ZIP not found: {zip_path}")
    if zip_path.stat().st_size > MAX_DOWNLOAD_BYTES:
        fail("The submitted ZIP is too large for automatic validation (maximum 250 MB).")
    with zip_path.open("rb") as stream:
        return validate_zip_stream(stream, pack_type)


def write_report(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", choices=("auto",) + PACK_TYPES, default="auto")
    parser.add_argument("--zip", type=Path, help="Validate a local ZIP instead of a GitHub issue attachment.")
    parser.add_argument("--issue-body-env", default="ISSUE_BODY", help="Environment variable containing the GitHub issue body.")
    parser.add_argument("--report", type=Path, default=Path("submission-validation.txt"))
    args = parser.parse_args()

    try:
        pack_type = resolve_pack_type(args.type)
        if args.zip:
            package_name = args.zip.name
            metadata = validate_zip(args.zip, pack_type)
        else:
            issue_body = os.environ.get(args.issue_body_env, "")
            if not issue_body.strip():
                fail("GitHub issue body is empty.")
            package_name, url = extract_zip_url(issue_body, pack_type)
            with tempfile.TemporaryDirectory(prefix=f"aniki-{pack_type}-") as temp_dir:
                zip_path = Path(temp_dir) / "submission.zip"
                download(url, zip_path)
                metadata = validate_zip(zip_path, pack_type)

        report = [
            "VALIDATION PASSED",
            f"Type: {PACK_DISPLAY[pack_type]}",
            f"Package: {package_name}",
            f"ID: {metadata['id']}",
            f"Name: {metadata['name']}",
            f"Author: {metadata['author']}",
            f"Version: {metadata['version']}",
            f"Content: {metadata.get('details', 'validated')}",
        ]
        write_report(args.report, report)
        print("\n".join(report))
        return 0
    except ValidationError as exc:
        report = ["VALIDATION FAILED", str(exc)]
        write_report(args.report, report)
        print("\n".join(report), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
