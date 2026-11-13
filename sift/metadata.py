import json
import os
import shutil
import subprocess
import time
import warnings
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache

import PIL
import pillow_heif
from PIL import Image, UnidentifiedImageError

from .local_paths import is_network_path

try:
    import rawpy
except ImportError:
    rawpy = None

pillow_heif.register_heif_opener()  # lets Pillow open HEIC/HEIF files

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".3gp", ".mkv", ".avi", ".webm", ".mts"}
RAW_EXTS = {".dng", ".nef", ".arw"}
AUDIO_EXTS = {
    ".mp3",
    ".m4a",
    ".aac",
    ".wav",
    ".flac",
    ".ogg",
    ".opus",
    ".wma",
    ".aiff",
    ".aif",
    ".mka",
}
IMAGE_FORMATS = {"JPEG", "PNG", "GIF", "WEBP", "BMP", "TIFF", "HEIF"}
FFPROBE_TIMEOUT = 30  # seconds


class MetadataCancelled(Exception):
    """Raised when the user cancels while an extractor is running."""


@dataclass
class MetadataResult:
    status: str  # "ok", "unsupported", or "failed"
    media_kind: str | None = None
    detected_format: str | None = None
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    video_codec: str | None = None
    capture_date: str | None = None
    capture_date_source: str | None = None
    camera_make: str | None = None
    camera_model: str | None = None
    orientation: int | None = None
    error: str | None = None
    retryable: bool = False
    audio_codec: str | None = None
    audio_sample_rate: int | None = None
    audio_channels: int | None = None
    audio_channel_layout: str | None = None
    audio_bit_rate: int | None = None


# ----- which extractor handles a file -----
def kind_for(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in RAW_EXTS:
        return "raw"
    detected = _kind_from_signature(path)
    if detected:
        return detected
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    return None


def _kind_from_signature(path):
    try:
        with open(path, "rb") as media_file:
            header = media_file.read(16)
    except OSError:
        return None
    if header.startswith(
        (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a", b"BM", b"II*\x00", b"MM\x00*")
    ):
        return "image"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image"
    if header[4:8] == b"ftyp":
        brand = header[8:12]
        if brand in (
            b"heic",
            b"heif",
            b"heix",
            b"hevc",
            b"hevx",
            b"mif1",
            b"msf1",
            b"avif",
            b"avis",
        ):
            return "image"
        return "video"
    if header.startswith(b"\x1aE\xdf\xa3"):
        return "video"
    if header.startswith(b"RIFF") and header[8:12] == b"AVI ":
        return "video"
    if len(header) >= 1 and header[0] == 0x47:
        try:
            with open(path, "rb") as media_file:
                media_file.seek(188)
                if media_file.read(1) == b"\x47":
                    return "video"
        except OSError:
            pass
    return None


def ffprobe_path():
    return shutil.which("ffprobe")


@lru_cache(maxsize=1)
def _ffprobe_version():
    exe = ffprobe_path()
    if not exe:
        return "missing"
    try:
        out = subprocess.run(
            [exe, "-version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return out.stdout.split("\n")[0].split()[2]  # "ffprobe version X ..."
    except (OSError, subprocess.SubprocessError, IndexError):
        return "unknown"


def extractor_info(kind):
    """(name, version) used as the cache key for saved results."""
    if kind == "image":
        return "pillow", f"{PIL.__version__}+heif{pillow_heif.__version__}"
    if kind == "video":
        return "ffprobe", _ffprobe_version()
    if kind == "audio":
        return "ffprobe", _ffprobe_version()
    if kind == "raw":
        return "rawpy", f"{getattr(rawpy, '__version__', 'missing')}+sift2"
    return "none", "1"


def extract(path, kind, cancel_check):
    if kind == "image":
        return extract_image_metadata(path)
    if kind == "video":
        return extract_video_metadata(path, cancel_check)
    if kind == "audio":
        return extract_video_metadata(path, cancel_check)
    if kind == "raw":
        return extract_raw_metadata(path)
    ext = os.path.splitext(path)[1].lower() or "no extension"
    return MetadataResult("unsupported", error=f"No extractor for '{ext}' files")


# ----- images -----
def extract_raw_metadata(path):
    if rawpy is None:
        return MetadataResult(
            "failed",
            media_kind="image",
            error="rawpy is not installed; install project requirements",
        )
    try:
        with rawpy.imread(path) as raw:
            sizes = raw.sizes
            try:
                timestamp = getattr(raw.other, "timestamp", None)
            except Exception:  # noqa: BLE001 - One file failing must never abort the scan.
                timestamp = None
            capture = None
            if timestamp:
                capture = datetime.fromtimestamp(timestamp, UTC).isoformat()
            return MetadataResult(
                "ok",
                "image",
                os.path.splitext(path)[1][1:].upper(),
                sizes.width,
                sizes.height,
                capture_date=capture,
                capture_date_source="rawpy:timestamp" if capture else None,
            )
    except Exception as error:  # noqa: BLE001 - One file failing must never abort the scan.
        return MetadataResult(
            "failed",
            media_kind="image",
            error=f"Could not decode RAW image: {error}",
            retryable=isinstance(error, PermissionError),
        )


def _text(value):
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    value = str(value).strip("\x00 ").strip()
    return value or None


def _capture_date(exif):
    try:
        exif_ifd = exif.get_ifd(0x8769)  # the "Exif" sub-block
    except Exception:  # noqa: BLE001 - One file failing must never abort the scan.
        exif_ifd = {}
    for tag, source in ((0x9003, "exif:DateTimeOriginal"), (0x9004, "exif:DateTimeDigitized")):
        raw = _text(exif_ifd.get(tag))
        if raw:
            try:
                parsed = datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")  # noqa: DTZ007 - EXIF has no timezone.
                return parsed.isoformat(), source  # no timezone assumed
            except ValueError:
                continue
    return None, None


def extract_image_metadata(path):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            with Image.open(path) as probe:
                if probe.format == "HEIF":
                    probe.load()
                else:
                    probe.verify()
            with Image.open(path) as img:
                fmt = img.format
                if fmt not in IMAGE_FORMATS:
                    return MetadataResult(
                        "unsupported",
                        detected_format=fmt,
                        error=f"Actual format is {fmt}, not supported",
                    )
                width, height = img.size
                exif = img.getexif()
                orientation = exif.get(0x0112)
                capture, source = _capture_date(exif)
                return MetadataResult(
                    "ok",
                    "image",
                    fmt,
                    width,
                    height,
                    None,
                    None,
                    capture,
                    source,
                    _text(exif.get(0x010F)),
                    _text(exif.get(0x0110)),
                    orientation if isinstance(orientation, int) else None,
                )
    except UnidentifiedImageError:
        return MetadataResult("failed", error="Not a readable image (corrupt or wrong format)")
    except Exception as e:  # noqa: BLE001 - One file failing must never abort the scan.
        return MetadataResult(
            "failed",
            error=f"Could not read metadata: {e}",
            retryable=isinstance(e, PermissionError),
        )


# ----- videos -----
def _run_ffprobe(exe, path, cancel_check):
    cmd = [
        exe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "-protocol_whitelist",
        "file",
        "-format_whitelist",
        "mov,matroska,avi,mpegts,m4v,mp3,aac,wav,flac,ogg,asf,aiff",
        "-i",
        path,
    ]  # a list: no shell, no quoting issues
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    deadline = time.monotonic() + FFPROBE_TIMEOUT
    while True:
        try:
            out, err = proc.communicate(timeout=0.2)
            return proc.returncode, out, err
        except subprocess.TimeoutExpired:
            if cancel_check():
                proc.kill()
                proc.communicate()
                raise MetadataCancelled
            if time.monotonic() > deadline:
                proc.kill()
                proc.communicate()
                raise TimeoutError(f"ffprobe took longer than {FFPROBE_TIMEOUT}s")


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def extract_video_metadata(path, cancel_check):
    if is_network_path(path):
        return MetadataResult("unsupported", error="Network media paths are not probed")
    exe = ffprobe_path()
    if not exe:
        return MetadataResult("failed", error="ffprobe not found; install FFmpeg")
    try:
        code, out, err = _run_ffprobe(exe, path, cancel_check)
    except MetadataCancelled:
        raise
    except Exception as e:  # noqa: BLE001 - One file failing must never abort the scan.
        return MetadataResult(
            "failed",
            error=f"ffprobe failed: {e}",
            retryable=isinstance(e, (TimeoutError, PermissionError, FileNotFoundError)),
        )
    if code != 0:
        first = (err.decode("utf-8", "ignore").strip().splitlines() or ["unknown error"])[0]
        return MetadataResult("failed", error=f"Not readable media: {first}")
    try:
        data = json.loads(out)
    except ValueError:
        return MetadataResult("failed", error="ffprobe returned unreadable output")

    streams = data.get("streams", [])
    video = next(
        (
            s
            for s in streams
            if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
        ),
        None,
    )
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None and audio is None:
        return MetadataResult("unsupported", error="No audio or video stream found")

    fmt = data.get("format", {})
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}

    # Prefer Apple's date (includes a UTC offset) over the generic creation_time.
    if tags.get("com.apple.quicktime.creationdate"):
        capture, source = tags["com.apple.quicktime.creationdate"], "quicktime:creationdate"
    elif tags.get("creation_time"):
        capture, source = tags["creation_time"], "container:creation_time"
    else:
        capture, source = None, None

    return MetadataResult(
        status="ok",
        media_kind="video" if video else "audio",
        detected_format=fmt.get("format_name"),
        width=video.get("width") if video else None,
        height=video.get("height") if video else None,
        duration_seconds=_float(fmt.get("duration")) or _float((video or audio).get("duration")),
        video_codec=video.get("codec_name") if video else None,
        capture_date=capture,
        capture_date_source=source,
        camera_make=_text(tags.get("com.apple.quicktime.make")),
        camera_model=_text(tags.get("com.apple.quicktime.model")),
        audio_codec=audio.get("codec_name") if audio else None,
        audio_sample_rate=int(audio["sample_rate"])
        if audio and str(audio.get("sample_rate", "")).isdigit()
        else None,
        audio_channels=audio.get("channels") if audio else None,
        audio_channel_layout=_text(audio.get("channel_layout")) if audio else None,
        audio_bit_rate=int(audio["bit_rate"])
        if audio and str(audio.get("bit_rate", "")).isdigit()
        else None,
    )
