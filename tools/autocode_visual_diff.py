"""Deterministic PNG channel comparison, not browser provenance or UI acceptance.

PNG samples are converted to straight, 8-bit RGBA under an sRGB-only policy.
Untagged PNGs are assumed sRGB; sRGB and standard gamma 0.45455 are accepted.
Other color profiles, chromaticity, HDR and EXIF metadata are rejected rather
than ignored. This does not implement color management or perceptual comparison.
Sixteen-bit images are rejected rather than silently losing channel precision.
"""
from __future__ import annotations

import hashlib
import io
import math
import struct
import zlib
from pathlib import Path

MAX_PIXELS = 16_000_000
MAX_PNG_BYTES = 128 * 1024 * 1024
MAX_REGIONS = 1024
_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_IEND = b"\x00\x00\x00\x00IEND\xaeB`\x82"
_COLOR_POLICY = (
    "Raw 8-bit straight RGBA channels; alpha and hidden RGB are compared. "
    "Untagged inputs are assumed sRGB; sRGB chunks and standard gamma 0.45455 "
    "are accepted without a color transform. ICC profiles, EXIF, chromaticity, "
    "CICP/HDR and nonstandard gamma are rejected; export normalized sRGB PNG. "
    "No browser color-management or perceptual-equivalence claim."
)


def _number(value, name, *, maximum, minimum=0, integer=False, positive=False, exclusive_max=False):
    if (type(value) not in ((int,) if integer else (int, float))
            or (isinstance(value, float) and not math.isfinite(value))
            or value < minimum or (positive and value == 0) or value > maximum
            or (exclusive_max and value == maximum)):
        kind = "integer" if integer else "finite number"
        lower = "greater than zero" if positive else f"at least {minimum}"
        upper = "less than" if exclusive_max else "at most"
        raise ValueError(f"{name} must be a {kind} {lower} and {upper} {maximum}")
    return value


def _size(width, height, scale, name):
    size = (round(width * scale), round(height * scale))
    if min(size) < 1 or size[0] * size[1] > MAX_PIXELS:
        raise ValueError(f"{name} dimensions must be nonempty and at most {MAX_PIXELS} pixels")
    return size


def _regions(regions, size):
    if regions is None:
        return []
    if not isinstance(regions, list) or len(regions) > MAX_REGIONS:
        raise ValueError(f"regions must be a list of at most {MAX_REGIONS} rectangles")
    result, ids = [], set()
    for region in regions:
        if not isinstance(region, dict) or set(region) != {
            "id", "x", "y", "width", "height", "max_changed_ratio"
        }:
            raise ValueError("each region must contain id, x, y, width, height, max_changed_ratio only")
        identity = region["id"]
        if not isinstance(identity, str) or not identity.strip() or identity in ids:
            raise ValueError("region id must be a nonempty unique string")
        ids.add(identity)
        for key in ("x", "y", "width", "height"):
            _number(region[key], f"region {identity} {key}", maximum=MAX_PIXELS,
                    integer=True, positive=key in ("width", "height"))
        _number(region["max_changed_ratio"], f"region {identity} max_changed_ratio", maximum=1,
                exclusive_max=True)
        if region["x"] + region["width"] > size[0] or region["y"] + region["height"] > size[1]:
            raise ValueError(f"region {identity} lies outside the reference pixel rectangle")
        result.append(dict(region))
    return result


def _load_png(path, expected, label, Image):
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"{label} must be a regular PNG file, not a symlink: {path}")
        with path.open("rb") as encoded:
            data = encoded.read(MAX_PNG_BYTES + 1)
        if len(data) > MAX_PNG_BYTES:
            raise ValueError(f"{label} PNG exceeds the {MAX_PNG_BYTES}-byte limit")
        # Validation, decoding and identity must use the same immutable bytes.
        with io.BytesIO(data) as source:
            header = source.read(33)
            if (len(header) != 33 or header[:8] != _SIGNATURE
                    or header[8:16] != b"\x00\x00\x00\x0dIHDR"):
                raise ValueError(f"{label} is not a nonempty PNG: {path}")
            size = struct.unpack(">II", header[16:24])
            if min(size) < 1 or size[0] * size[1] > MAX_PIXELS:
                raise ValueError(f"{label} must have 1 to {MAX_PIXELS} pixels")
            if size != expected:
                raise ValueError(f"{label} dimensions {size} do not match declared dimensions {expected}")
            if header[24] not in (1, 2, 4, 8):
                raise ValueError(f"{label} PNG bit depth must be at most 8; precision reduction is unsupported")
            length = source.seek(0, 2)
            source.seek(-12, 2)
            # Pillow.verify() stops before the IEND CRC; require the complete end chunk too.
            if source.read(12) != _IEND:
                raise ValueError(f"{label} PNG is truncated or has an invalid end chunk")
            # Pillow accepts a later IHDR that overrides the already-checked size/depth.
            position = 33
            while position < length:
                chunk_length, kind = struct.unpack(">I4s", data[position:position + 8])
                if kind == b"IHDR":
                    raise ValueError(f"{label} PNG contains a duplicate IHDR chunk")
                position += chunk_length + 12
                if position > length:
                    raise ValueError(f"{label} PNG contains a truncated chunk")
            try:
                source.seek(0)
                with Image.open(source, formats=["PNG"]) as image:
                    if image.n_frames != 1:
                        raise ValueError(f"{label} PNG must be single-frame")
                    image.verify()
                source.seek(0)
                with Image.open(source, formats=["PNG"]) as image:
                    image.load()
                    if image.size != expected:
                        raise ValueError(f"{label} decoded dimensions {image.size} "
                                         f"do not match declared dimensions {expected}")
                    rgba = image.convert("RGBA")
            except (IndexError, zlib.error) as exc:
                # Malformed trailing metadata can pass verify() but fail during load().
                raise ValueError(f"{label} PNG is corrupt: {path}: {exc}") from exc

            # Inspect all chunks, not only image.info: Pillow may ignore cICP or
            # overwrite an earlier nonstandard gamma with a later standard one.
            source.seek(8)
            while source.tell() < length:
                chunk_length, kind = struct.unpack(">I4s", source.read(8))
                next_chunk = source.tell() + chunk_length + 4
                if next_chunk > length:
                    raise ValueError(f"{label} PNG contains a truncated chunk")
                if kind == b"IEND" and (chunk_length or next_chunk != length):
                    raise ValueError(f"{label} PNG contains an invalid end chunk")
                unsupported = kind in (b"iCCP", b"eXIf", b"cHRM", b"cICP", b"mDCv", b"cLLi")
                if kind == b"gAMA":
                    unsupported = chunk_length != 4 or source.read(4) != struct.pack(">I", 45455)
                elif kind == b"sRGB":
                    unsupported = chunk_length != 1 or source.read(1) not in (b"\x00", b"\x01", b"\x02", b"\x03")
                if unsupported:
                    raise ValueError(f"{label} PNG has unsupported {kind.decode('ascii')} color/orientation "
                                     "metadata; export normalized sRGB PNG without ICC, EXIF, chromaticity "
                                     "or CICP/HDR metadata (only sRGB and gamma 0.45455 are supported)")
                source.seek(next_chunk)
            return rgba, hashlib.sha256(data).hexdigest()
    except (OSError, SyntaxError, EOFError, struct.error, Image.DecompressionBombError) as exc:
        raise ValueError(f"{label} PNG is unreadable, corrupt or truncated: {path}: {exc}") from exc


def _metrics(delta, mask, channel_tolerance, max_changed_ratio, offset=(0, 0)):
    histogram = delta.histogram()
    changed = sum(histogram[channel_tolerance + 1:])
    total = delta.width * delta.height
    bbox = mask.getbbox()
    return {
        "status": "PASS" if changed / total <= max_changed_ratio else "FAIL",
        "changed_pixels": changed,
        "total_pixels": total,
        "changed_ratio": changed / total,
        "max_channel_delta": max(index for index, count in enumerate(histogram) if count),
        "bbox": ([bbox[0] + offset[0], bbox[1] + offset[1],
                  bbox[2] + offset[0], bbox[3] + offset[1]] if bbox else None),
        "channel_tolerance": channel_tolerance,
        "max_changed_ratio": max_changed_ratio,
    }


def compare(reference: Path, candidate: Path, output: Path, *, viewport: dict,
            export_scale: float, channel_tolerance: int = 0,
            max_changed_ratio: float = 0.0, regions: list | None = None) -> dict:
    """Compare PNGs and create diff.png/overlay.png in a NEW per-case directory.

    The output parent must already exist. Input leaves and the output parent
    cannot be symlinks. Invalid evidence/policy raises ValueError; a valid pixel
    mismatch returns FAIL. An I/O failure may leave a partial output directory,
    which cannot be reused. Source images are never written. reference_sha256
    and candidate_sha256 identify the exact encoded buffers used for decoding,
    not a later read of either input path.

    CSS dimensions must be positive integers. Dimensions use Python round():
    reference = CSS * export_scale, candidate = CSS * device_scale_factor.
    Only the candidate is resized, explicitly with per-channel LANCZOS. Regions
    use reference pixels and exclusive right/bottom edges; overlapping regions
    are allowed. Deltas <= channel_tolerance and ratios <= their limit pass.
    Scales are limited to 0.01..8. Tolerance 255 and ratio 1 are refused because
    they would accept every possible image, regardless of the reference.

    Diff pixels above tolerance are opaque magenta on dark gray. The overlay is
    a 50/50 blend of reference and normalized candidate, each over opaque white.
    Neither rendering changes the all-four-channel comparison itself.
    """
    try:
        from PIL import Image, ImageChops, ImageFile, __version__
    except ImportError as exc:
        raise ValueError("Pillow is required for visual comparison; install it with "
                         "`python -m pip install Pillow` in the active environment") from exc
    if ImageFile.LOAD_TRUNCATED_IMAGES:
        raise ValueError("Pillow ImageFile.LOAD_TRUNCATED_IMAGES must be False for strict PNG comparison")

    if not isinstance(viewport, dict) or set(viewport) != {"width", "height", "device_scale_factor"}:
        raise ValueError("viewport must contain width, height and device_scale_factor only")
    width = _number(viewport["width"], "viewport.width", maximum=MAX_PIXELS, integer=True, positive=True)
    height = _number(viewport["height"], "viewport.height", maximum=MAX_PIXELS, integer=True, positive=True)
    dpr = _number(viewport["device_scale_factor"], "device_scale_factor", minimum=0.01, maximum=8)
    scale = _number(export_scale, "export_scale", minimum=0.01, maximum=8)
    _number(channel_tolerance, "channel_tolerance", maximum=254, integer=True)
    _number(max_changed_ratio, "max_changed_ratio", maximum=1, exclusive_max=True)
    reference_size = _size(width, height, scale, "reference")
    candidate_size = _size(width, height, dpr, "candidate")
    rectangles = _regions(regions, reference_size)
    try:
        reference, candidate, output = Path(reference), Path(candidate), Path(output)
        if output.is_symlink() or output.exists():
            raise ValueError(f"output must be a new directory, not an existing path: {output}")
        if output.parent.is_symlink() or not output.parent.is_dir():
            raise ValueError(f"output parent must be an existing directory, not a symlink: {output.parent}")
        output = output.absolute()
    except (TypeError, OSError) as exc:
        raise ValueError(f"invalid comparison path: {exc}") from exc

    reference_image, reference_sha256 = _load_png(reference, reference_size, "reference", Image)
    candidate_image, candidate_sha256 = _load_png(candidate, candidate_size, "candidate", Image)
    resized = candidate_size != reference_size
    if resized:
        # Resample straight channels separately, preserving RGB beneath transparent pixels.
        candidate_image = Image.merge("RGBA", tuple(
            channel.resize(reference_size, Image.Resampling.LANCZOS)
            for channel in candidate_image.split()
        ))
    channels = ImageChops.difference(reference_image, candidate_image).split()
    delta = channels[0]
    for channel in channels[1:]:
        delta = ImageChops.lighter(delta, channel)
    mask = delta.point([255 if value > channel_tolerance else 0 for value in range(256)])
    result = _metrics(delta, mask, channel_tolerance, max_changed_ratio)
    region_results = []
    for region in rectangles:
        x, y = region["x"], region["y"]
        box = (x, y, x + region["width"], y + region["height"])
        metrics = _metrics(delta.crop(box), mask.crop(box), channel_tolerance,
                           region["max_changed_ratio"], (x, y))
        region_results.append({**region, **metrics})
    if any(region["status"] == "FAIL" for region in region_results):
        result["status"] = "FAIL"

    diff = Image.new("RGB", reference_size, (32, 32, 32))
    diff.paste((255, 0, 255), mask=mask)
    background = Image.new("RGBA", reference_size, (255, 255, 255, 255))
    overlay = Image.blend(Image.alpha_composite(background, reference_image).convert("RGB"),
                          Image.alpha_composite(background, candidate_image).convert("RGB"), 0.5)
    artifacts = {}
    try:
        output.mkdir(exist_ok=False)
        for name, image in (("diff", diff), ("overlay", overlay)):
            path = output / f"{name}.png"
            with path.open("xb") as artifact:
                image.save(artifact, format="PNG")
            with path.open("rb") as artifact:
                digest = hashlib.file_digest(artifact, "sha256").hexdigest()
            artifacts[name] = {"path": str(path), "sha256": digest}
    except OSError as exc:
        raise ValueError(f"cannot create new comparison output {output}: {exc}") from exc
    return {
        **result,
        "reference_sha256": reference_sha256,
        "candidate_sha256": candidate_sha256,
        "reference_size": list(reference_size),
        "candidate_size": list(candidate_size),
        "comparison_size": list(reference_size),
        "regions": region_results,
        "artifacts": artifacts,
        "normalization": {
            "reference": "RGBA conversion only; never resized or cropped",
            "candidate_resized": resized,
            "candidate_from": list(candidate_size),
            "candidate_to": list(reference_size),
            "resampling": "LANCZOS" if resized else None,
            "resampling_channels": "straight RGBA independently; no alpha premultiplication",
        },
        "color_policy": _COLOR_POLICY,
        "engine": {"name": "Pillow", "version": __version__},
    }
