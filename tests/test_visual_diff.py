"""Generated PNG controls for the public, dependency-optional comparator API."""
import builtins
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

import autocode_visual_diff as visual_diff

try:
    from PIL import Image, ImageDraw, ImageFile, PngImagePlugin, __version__
except ImportError:
    Image = None


class VisualDiffDependencyTests(unittest.TestCase):
    def test_missing_pillow_has_actionable_error(self):
        original_import = builtins.__import__

        def without_pillow(name, *args, **kwargs):
            if name == "PIL" or name.startswith("PIL."):
                raise ImportError("Pillow deliberately unavailable")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=without_pillow):
            with self.assertRaisesRegex(ValueError, "Pillow.*pip install Pillow"):
                visual_diff.compare(Path("reference.png"), Path("candidate.png"), Path("new"),
                                    viewport={"width": 1, "height": 1, "device_scale_factor": 1},
                                    export_scale=1)


@unittest.skipIf(Image is None, "optional Pillow dependency is not installed")
class VisualDiffTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.reference = self.root / "reference.png"
        self.candidate = self.root / "candidate.png"
        self.output = self.root / "comparison"
        self.viewport = {"width": 40, "height": 30, "device_scale_factor": 1}
        self.image = Image.new("RGBA", (40, 30), "white")
        self.save_pair(self.image, self.image)

    def save_pair(self, reference, candidate):
        reference.save(self.reference, format="PNG")
        candidate.save(self.candidate, format="PNG")

    def add_chunk(self, path, kind, payload, *, after_idat=False):
        data = path.read_bytes()
        chunk = (struct.pack(">I", len(payload)) + kind + payload
                 + struct.pack(">I", zlib.crc32(kind + payload)))
        offset = len(data) - 12 if after_idat else 33
        path.write_bytes(data[:offset] + chunk + data[offset:])

    def compare(self, **options):
        arguments = {"viewport": self.viewport, "export_scale": 1, **options}
        return visual_diff.compare(self.reference, self.candidate, self.output, **arguments)

    def assert_invalid(self, message=None, **options):
        with self.assertRaisesRegex(ValueError, message or "."):
            self.compare(**options)
        self.assertFalse(self.output.exists(), "invalid evidence must not create comparison artifacts")

    def test_identical_images_report_serializable_pass_and_artifact_hashes(self):
        before = [path.read_bytes() for path in (self.reference, self.candidate)]
        result = self.compare()
        self.assertEqual("PASS", result["status"])
        self.assertEqual([40, 30], result["reference_size"])
        self.assertEqual([40, 30], result["candidate_size"])
        self.assertEqual([40, 30], result["comparison_size"])
        self.assertEqual(0, result["changed_pixels"])
        self.assertEqual(1200, result["total_pixels"])
        self.assertEqual(0, result["changed_ratio"])
        self.assertEqual(0, result["max_channel_delta"])
        self.assertIsNone(result["bbox"])
        self.assertEqual([], result["regions"])
        self.assertFalse(result["normalization"]["candidate_resized"])
        self.assertEqual({"name": "Pillow", "version": __version__}, result["engine"])
        self.assertEqual(hashlib.sha256(before[0]).hexdigest(), result["reference_sha256"])
        self.assertEqual(hashlib.sha256(before[1]).hexdigest(), result["candidate_sha256"])
        self.assertEqual(result, json.loads(json.dumps(result, allow_nan=False)))
        for name, artifact in result["artifacts"].items():
            path = Path(artifact["path"])
            self.assertEqual(self.output / f"{name}.png", path)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), artifact["sha256"])
            with Image.open(path) as image:
                self.assertEqual((40, 30), image.size)
                self.assertEqual("PNG", image.format)
                image.load()
        self.assertEqual(before, [path.read_bytes() for path in (self.reference, self.candidate)])

    def test_reencoding_and_text_metadata_do_not_change_pixels(self):
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("description", "different encoding, identical samples")
        self.image.save(self.reference, compress_level=0)
        self.image.save(self.candidate, compress_level=9, pnginfo=metadata)
        reference_bytes, candidate_bytes = self.reference.read_bytes(), self.candidate.read_bytes()
        self.assertNotEqual(reference_bytes, candidate_bytes)
        result = self.compare()
        self.assertEqual("PASS", result["status"])
        self.assertEqual(hashlib.sha256(reference_bytes).hexdigest(), result["reference_sha256"])
        self.assertEqual(hashlib.sha256(candidate_bytes).hexdigest(), result["candidate_sha256"])
        self.assertNotEqual(result["reference_sha256"], result["candidate_sha256"])

    def test_input_hashes_bind_decoded_buffers_when_paths_are_restored_during_decode(self):
        original = self.reference.read_bytes()
        changed = Image.new("RGBA", self.image.size, "black")
        original_open = Image.open
        for name, decode_call in (("reference", 1), ("candidate", 3)):
            for atomic_replace in (False, True):
                with self.subTest(input=name, atomic_replace=atomic_replace):
                    self.output = self.root / f"restored-{name}-{atomic_replace}"
                    self.save_pair(changed, changed)
                    decoded_bytes = self.reference.read_bytes()
                    target = getattr(self, name)
                    replacement = self.root / "original.png"
                    replacement.write_bytes(original)
                    calls = 0

                    def restore_path_while_decoding(*args, **kwargs):
                        nonlocal calls
                        calls += 1
                        if calls == decode_call:
                            if atomic_replace:
                                replacement.replace(target)
                            else:
                                target.write_bytes(original)
                        return original_open(*args, **kwargs)

                    with patch.object(Image, "open", side_effect=restore_path_while_decoding):
                        result = self.compare()
                    self.assertEqual(original, target.read_bytes())
                    self.assertEqual("PASS", result["status"])
                    self.assertEqual(0, result["changed_pixels"])
                    self.assertEqual(hashlib.sha256(decoded_bytes).hexdigest(), result["reference_sha256"])
                    self.assertEqual(hashlib.sha256(decoded_bytes).hexdigest(), result["candidate_sha256"])
                    self.assertNotEqual(hashlib.sha256(original).hexdigest(), result[f"{name}_sha256"])

    def test_encoded_buffer_size_limit_is_checked_before_pillow_decode(self):
        with patch.object(visual_diff, "MAX_PNG_BYTES", 32):
            with patch.object(Image, "open", side_effect=AssertionError("must not decode")):
                self.assert_invalid("32-byte limit")

    def test_palette_rgb_and_rgba_normalize_to_same_samples(self):
        rgb = Image.new("RGB", (40, 30), (25, 70, 120))
        palette = Image.new("P", rgb.size, 0)
        palette.putpalette([25, 70, 120] + [0] * 765)
        modes = [palette, rgb, rgb.convert("RGBA")]
        for index, candidate in enumerate(modes):
            with self.subTest(mode=candidate.mode):
                self.output = self.root / f"mode-{index}"
                self.save_pair(rgb, candidate)
                self.assertEqual("PASS", self.compare()["status"])

    def test_palette_transparency_preserves_alpha(self):
        palette = Image.new("P", (40, 30), 0)
        palette.putpalette([25, 70, 120] + [0] * 765)
        # Palette index zero is made partially transparent through a tRNS table.
        palette.info["transparency"] = bytes([80])
        self.save_pair(Image.new("RGBA", palette.size, (25, 70, 120, 80)), palette)
        self.assertEqual("PASS", self.compare()["status"])

    def test_shifted_layout_fails_with_thresholded_bounding_box(self):
        reference, candidate = self.image.copy(), self.image.copy()
        ImageDraw.Draw(reference).rectangle((5, 8, 14, 17), fill="black")
        ImageDraw.Draw(candidate).rectangle((7, 8, 16, 17), fill="black")
        self.save_pair(reference, candidate)
        result = self.compare()
        self.assertEqual("FAIL", result["status"])
        self.assertEqual(40, result["changed_pixels"])
        self.assertEqual([5, 8, 17, 18], result["bbox"])
        self.assertEqual(255, result["max_channel_delta"])

    def test_small_missing_control_fails_exact_default(self):
        reference = self.image.copy()
        ImageDraw.Draw(reference).rectangle((31, 23, 33, 24), fill="black")
        self.save_pair(reference, self.image)
        result = self.compare()
        self.assertEqual("FAIL", result["status"])
        self.assertEqual(6, result["changed_pixels"])
        self.assertEqual([31, 23, 34, 25], result["bbox"])

    def test_font_like_stroke_weight_difference_fails(self):
        reference, candidate = self.image.copy(), self.image.copy()
        for x in (4, 10, 16):
            ImageDraw.Draw(reference).rectangle((x, 7, x, 15), fill=(40, 40, 40, 255))
            ImageDraw.Draw(candidate).rectangle((x, 7, x + 1, 15), fill=(40, 40, 40, 255))
        self.save_pair(reference, candidate)
        result = self.compare()
        self.assertEqual("FAIL", result["status"])
        self.assertEqual(27, result["changed_pixels"])
        self.assertEqual([5, 7, 18, 16], result["bbox"])

    def test_color_difference_uses_largest_channel_not_sum(self):
        self.save_pair(Image.new("RGB", (40, 30), (100, 120, 140)),
                       Image.new("RGB", (40, 30), (102, 123, 144)))
        result = self.compare(channel_tolerance=3)
        self.assertEqual("FAIL", result["status"])
        self.assertEqual(4, result["max_channel_delta"])
        self.assertEqual(1200, result["changed_pixels"])

    def test_alpha_only_difference_counts_and_is_visible_in_diff(self):
        reference = Image.new("RGBA", (40, 30), (20, 30, 40, 100))
        candidate = reference.copy()
        candidate.putpixel((3, 4), (20, 30, 40, 99))
        self.save_pair(reference, candidate)
        result = self.compare()
        self.assertEqual("FAIL", result["status"])
        self.assertEqual(1, result["changed_pixels"])
        self.assertEqual(1, result["max_channel_delta"])
        self.assertEqual([3, 4, 4, 5], result["bbox"])
        with Image.open(result["artifacts"]["diff"]["path"]) as image:
            self.assertEqual((255, 0, 255), image.getpixel((3, 4)))
            self.assertEqual((32, 32, 32), image.getpixel((2, 4)))

    def test_hidden_rgb_is_not_discarded_when_alpha_is_zero(self):
        self.save_pair(Image.new("RGBA", (40, 30), (0, 0, 0, 0)),
                       Image.new("RGBA", (40, 30), (1, 0, 0, 0)))
        result = self.compare()
        self.assertEqual("FAIL", result["status"])
        self.assertEqual(1200, result["changed_pixels"])

    def test_channel_tolerance_boundary_is_inclusive(self):
        candidate = self.image.copy()
        candidate.putpixel((2, 3), (248, 248, 248, 255))
        candidate.putpixel((8, 9), (247, 255, 255, 255))
        self.save_pair(self.image, candidate)
        result = self.compare(channel_tolerance=7)
        self.assertEqual(1, result["changed_pixels"])
        self.assertEqual([8, 9, 9, 10], result["bbox"])
        self.assertEqual(8, result["max_channel_delta"])
        self.output = self.root / "inclusive"
        accepted = self.compare(channel_tolerance=8)
        self.assertEqual("PASS", accepted["status"])
        self.assertEqual(8, accepted["max_channel_delta"])
        self.assertIsNone(accepted["bbox"])

    def test_changed_ratio_boundary_is_inclusive(self):
        candidate = self.image.copy()
        ImageDraw.Draw(candidate).rectangle((0, 0, 19, 29), fill="black")
        self.save_pair(self.image, candidate)
        self.assertEqual("PASS", self.compare(max_changed_ratio=0.5)["status"])
        self.output = self.root / "below-boundary"
        self.assertEqual("FAIL", self.compare(max_changed_ratio=0.499999)["status"])

    def test_strict_region_defeats_global_dilution_and_has_absolute_bbox(self):
        candidate = self.image.copy()
        candidate.putpixel((31, 23), (0, 0, 0, 255))
        self.save_pair(self.image, candidate)
        region = {"id": "submit", "x": 30, "y": 22, "width": 4, "height": 4, "max_changed_ratio": 0}
        result = self.compare(max_changed_ratio=0.01, regions=[region])
        self.assertLess(result["changed_ratio"], 0.01)
        self.assertEqual("FAIL", result["status"])
        strict = result["regions"][0]
        self.assertEqual("FAIL", strict["status"])
        self.assertEqual(1, strict["changed_pixels"])
        self.assertEqual(16, strict["total_pixels"])
        self.assertEqual(1 / 16, strict["changed_ratio"])
        self.assertEqual([31, 23, 32, 24], strict["bbox"])
        self.output = self.root / "region-boundary"
        region["max_changed_ratio"] = 1 / 16
        self.assertEqual("PASS", self.compare(max_changed_ratio=0.01, regions=[region])["status"])

    def test_regions_share_channel_tolerance_and_empty_region_bbox(self):
        candidate = self.image.copy()
        candidate.putpixel((31, 23), (250, 255, 255, 255))
        self.save_pair(self.image, candidate)
        region = {"id": "submit", "x": 30, "y": 22, "width": 4, "height": 4, "max_changed_ratio": 0}
        result = self.compare(channel_tolerance=5, regions=[region])
        self.assertEqual("PASS", result["status"])
        self.assertEqual(0, result["regions"][0]["changed_pixels"])
        self.assertEqual(5, result["regions"][0]["max_channel_delta"])
        self.assertIsNone(result["regions"][0]["bbox"])

    def test_native_css_viewport_and_scaled_export_have_explicit_alignment(self):
        candidate = Image.new("RGBA", (40, 30), "white")
        ImageDraw.Draw(candidate).rectangle((10, 8, 25, 20), fill="black")
        reference = candidate.resize((80, 60), Image.Resampling.LANCZOS)
        self.save_pair(reference, candidate)
        result = self.compare(export_scale=2)
        self.assertEqual("PASS", result["status"])
        self.assertEqual([80, 60], result["reference_size"])
        self.assertEqual([40, 30], result["candidate_size"])
        self.assertEqual([80, 60], result["comparison_size"])
        self.assertTrue(result["normalization"]["candidate_resized"])
        self.assertEqual("LANCZOS", result["normalization"]["resampling"])
        self.assertEqual([40, 30], result["normalization"]["candidate_from"])
        self.assertEqual([80, 60], result["normalization"]["candidate_to"])

    def test_dpr_candidate_is_downsampled_to_reference_export_not_css_size(self):
        candidate = Image.new("RGBA", (80, 60), "white")
        ImageDraw.Draw(candidate).rectangle((10, 8, 25, 20), fill="black")
        reference = candidate.resize((60, 45), Image.Resampling.LANCZOS)
        self.save_pair(reference, candidate)
        result = self.compare(viewport={**self.viewport, "device_scale_factor": 2}, export_scale=1.5)
        self.assertEqual("PASS", result["status"])
        self.assertEqual([60, 45], result["comparison_size"])
        self.assertEqual([80, 60], result["candidate_size"])

    def test_fractional_scale_uses_declared_rounding(self):
        self.save_pair(Image.new("RGB", (8, 4), "white"), Image.new("RGB", (6, 4), "white"))
        result = self.compare(viewport={"width": 5, "height": 3, "device_scale_factor": 1.25}, export_scale=1.5)
        self.assertEqual("PASS", result["status"])
        self.assertEqual([8, 4], result["reference_size"])
        self.assertEqual([6, 4], result["candidate_size"])

    def test_transparent_rgb_survives_candidate_resampling(self):
        self.save_pair(Image.new("RGBA", (80, 60), (1, 2, 3, 0)),
                       Image.new("RGBA", (40, 30), (1, 2, 3, 0)))
        self.assertEqual("PASS", self.compare(export_scale=2)["status"])

    def test_wrong_dimensions_are_rejected_before_any_resize(self):
        cases = [((40, 30), (40, 30), 2, 1, "reference"),
                 ((80, 60), (80, 60), 2, 1, "candidate"),
                 ((40, 30), (40, 30), 1, 2, "candidate"),
                 ((41, 30), (40, 30), 1, 1, "reference")]
        for reference, candidate, scale, dpr, label in cases:
            with self.subTest(reference=reference, candidate=candidate, scale=scale, dpr=dpr):
                self.save_pair(Image.new("RGB", reference), Image.new("RGB", candidate))
                with patch.object(Image.Image, "resize", side_effect=AssertionError("resize before validation")):
                    self.assert_invalid(f"{label} dimensions", export_scale=scale,
                                        viewport={**self.viewport, "device_scale_factor": dpr})

    def test_regions_are_in_scaled_reference_pixels(self):
        self.save_pair(Image.new("RGB", (80, 60), "white"), Image.new("RGB", (40, 30), "white"))
        region = {"id": "edge", "x": 79, "y": 59, "width": 1, "height": 1, "max_changed_ratio": 0}
        result = self.compare(export_scale=2, regions=[region])
        self.assertEqual("PASS", result["status"])
        self.assertEqual(1, result["regions"][0]["total_pixels"])

    def test_overlay_is_half_reference_half_candidate_over_white(self):
        self.save_pair(Image.new("RGBA", (40, 30), (0, 0, 0, 255)),
                       Image.new("RGBA", (40, 30), (0, 0, 0, 0)))
        result = self.compare()
        with Image.open(result["artifacts"]["overlay"]["path"]) as image:
            self.assertEqual("RGB", image.mode)
            self.assertEqual((127, 127, 127), image.getpixel((0, 0)))

    def test_standard_srgb_and_gamma_metadata_are_accepted(self):
        for after_idat in (False, True):
            for kinds in ((b"sRGB",), (b"gAMA",), (b"sRGB", b"gAMA")):
                with self.subTest(after_idat=after_idat, kinds=kinds):
                    self.output = self.root / f"standard-{after_idat}-{len(kinds)}-{kinds[0].decode()}"
                    self.save_pair(self.image, self.image)
                    for kind in kinds:
                        payload = b"\x00" if kind == b"sRGB" else struct.pack(">I", 45455)
                        self.add_chunk(self.candidate, kind, payload, after_idat=after_idat)
                    result = self.compare()
                    self.assertEqual("PASS", result["status"])
                    self.assertIn("assumed sRGB", result["color_policy"])
                    self.assertIn("gamma 0.45455", result["color_policy"])
                    self.assertIn("rejected", result["color_policy"])
                    self.assertIn("No browser color-management", result["color_policy"])

    def test_unsupported_metadata_is_rejected_before_or_after_idat_on_either_input(self):
        exif = Image.Exif()
        exif[274] = 6
        chunks = ((b"iCCP", b"Profile\x00\x00" + zlib.compress(b"unsupported profile")),
                  (b"eXIf", exif.tobytes()[6:]),
                  (b"cHRM", struct.pack(">8I", 31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000)),
                  (b"cICP", bytes([9, 16, 0, 1])),
                  (b"mDCv", bytes(24)),
                  (b"cLLi", bytes(8)),
                  (b"gAMA", struct.pack(">I", 100000)))
        for kind, payload in chunks:
            for after_idat in (False, True):
                for path in (self.reference, self.candidate):
                    with self.subTest(kind=kind, after_idat=after_idat, input=path.name):
                        self.output = self.root / f"unsupported-{kind.decode()}-{after_idat}-{path.stem}"
                        self.save_pair(self.image, self.image)
                        self.add_chunk(path, kind, payload, after_idat=after_idat)
                        self.assert_invalid(f"{kind.decode()}.*export normalized sRGB PNG")

    def test_nonstandard_gamma_cannot_be_hidden_by_later_standard_gamma(self):
        self.add_chunk(self.candidate, b"gAMA", struct.pack(">I", 100000))
        self.add_chunk(self.candidate, b"gAMA", struct.pack(">I", 45455), after_idat=True)
        self.assert_invalid("gAMA.*export normalized sRGB PNG")

    def test_nonstandard_gamma_and_invalid_srgb_intent_are_rejected(self):
        chunks = [(b"gAMA", struct.pack(">I", gamma)) for gamma in (0, 45454, 45456)]
        chunks += [(b"sRGB", b"\x04"), (b"sRGB", b"\x00\x00")]
        for index, (kind, payload) in enumerate(chunks):
            with self.subTest(kind=kind, payload=payload):
                self.output = self.root / f"invalid-color-{index}"
                self.save_pair(self.image, self.image)
                self.add_chunk(self.candidate, kind, payload)
                self.assert_invalid(f"{kind.decode()}.*export normalized sRGB PNG")

    def test_trailing_irrelevant_text_metadata_remains_accepted(self):
        self.add_chunk(self.candidate, b"tEXt", b"Description\x00Metadata after pixels", after_idat=True)
        self.assertEqual("PASS", self.compare()["status"])

    def test_empty_iccp_after_idat_is_a_value_error_not_uncaught_decoder_index_error(self):
        image = Image.new("RGBA", (1, 1), "white")
        self.save_pair(image, image)
        self.viewport = {"width": 1, "height": 1, "device_scale_factor": 1}
        self.add_chunk(self.candidate, b"iCCP", b"", after_idat=True)
        with Image.open(self.candidate) as encoded:
            encoded.verify()
        self.assert_invalid("candidate PNG.*corrupt")

    def test_decoder_index_struct_and_zlib_errors_are_normalized(self):
        for exception in (IndexError("bad metadata"), struct.error("bad struct"), zlib.error("bad deflate")):
            with self.subTest(exception=type(exception).__name__):
                with patch.object(PngImagePlugin.PngImageFile, "load", side_effect=exception):
                    self.assert_invalid("PNG.*corrupt")

    def test_comparison_programming_errors_are_not_hidden_as_invalid_png(self):
        with patch.object(visual_diff, "_metrics", side_effect=IndexError("not a decoder error")):
            with self.assertRaisesRegex(IndexError, "not a decoder error"):
                self.compare()

    def test_non_png_data_and_empty_files_are_rejected(self):
        for data in (b"", b"not a png", b"\x89PNG\r\n\x1a\n"):
            with self.subTest(data=data):
                self.candidate.write_bytes(data)
                self.assert_invalid("PNG")
        self.image.convert("RGB").save(self.candidate, format="JPEG")
        self.assert_invalid("PNG")

    def test_truncated_png_including_missing_iend_crc_is_rejected(self):
        data = self.candidate.read_bytes()
        for length in (1, 4, 12, len(data) // 2):
            with self.subTest(bytes_missing=length):
                self.candidate.write_bytes(data[:-length])
                self.assert_invalid("PNG")

    def test_corrupt_idat_crc_is_rejected(self):
        data = bytearray(self.candidate.read_bytes())
        position = data.index(b"IDAT")
        data[position + 4] ^= 1
        self.candidate.write_bytes(data)
        self.assert_invalid("corrupt")

    def test_valid_chunk_crc_but_invalid_compressed_pixels_are_rejected(self):
        data = self.candidate.read_bytes()
        position = data.index(b"IDAT")
        length = struct.unpack(">I", data[position - 4:position])[0]
        payload = b"x" * length
        crc = struct.pack(">I", zlib.crc32(b"IDAT" + payload))
        self.candidate.write_bytes(data[:position + 4] + payload + crc + data[position + 8 + length:])
        self.assert_invalid("corrupt")

    def test_animated_png_is_rejected(self):
        other = Image.new("RGBA", self.image.size, "black")
        self.image.save(self.candidate, format="PNG", save_all=True, append_images=[other], duration=100, loop=0)
        self.assert_invalid("single-frame")

    def test_sixteen_bit_png_is_rejected_not_silently_quantized(self):
        Image.new("I;16", (40, 30), 1024).save(self.candidate, format="PNG")
        self.assert_invalid("bit depth")

    def test_duplicate_ihdr_cannot_hide_wrong_dpr_dimensions(self):
        Image.new("RGBA", (80, 60), "white").save(self.candidate, format="PNG")
        self.candidate.write_bytes(self.candidate.read_bytes()[:33] + self.reference.read_bytes()[8:])
        with patch.object(Image, "open", wraps=Image.open) as decoder:
            self.assert_invalid("duplicate IHDR", viewport={**self.viewport, "device_scale_factor": 2})
        self.assertEqual(2, decoder.call_count, "only the valid reference may reach Pillow")

    def test_duplicate_ihdr_cannot_override_bit_depth_before_or_after_idat(self):
        original = self.reference.read_bytes()
        Image.new("I;16", (40, 30), 65535).save(self.candidate, format="PNG")
        sixteen_bit = self.candidate.read_bytes()
        for after_idat in (False, True):
            with self.subTest(after_idat=after_idat):
                if after_idat:
                    self.reference.write_bytes(original)
                    self.add_chunk(self.reference, b"IHDR", sixteen_bit[16:29], after_idat=True)
                else:
                    self.reference.write_bytes(original[:33] + sixteen_bit[8:])
                with patch.object(Image, "open", side_effect=AssertionError("must reject before decoding")):
                    self.assert_invalid("duplicate IHDR")

    def test_actual_decoded_dimensions_are_checked_against_declared_size(self):
        original_load = PngImagePlugin.PngImageFile.load

        def decode_wrong_size(image, *args, **kwargs):
            result = original_load(image, *args, **kwargs)
            image._size = (39, 30)
            return result

        with patch.object(PngImagePlugin.PngImageFile, "load", decode_wrong_size):
            self.assert_invalid("reference decoded dimensions.*declared dimensions")

    def test_permissive_pillow_truncation_setting_is_refused(self):
        with patch.object(ImageFile, "LOAD_TRUNCATED_IMAGES", True):
            self.assert_invalid("LOAD_TRUNCATED_IMAGES")

    def test_thresholds_reject_bool_nonfinite_wrong_types_and_out_of_range(self):
        cases = {"channel_tolerance": [True, False, 0.0, -1, 255, 256, "0", None, float("nan"), float("inf")],
                 "max_changed_ratio": [True, False, -0.1, 1, 1.1, "0", None, float("nan"), float("inf"), -float("inf")],
                 "export_scale": [True, False, 0, -1, 0.001, 8.1, "1", None, float("nan"), float("inf"), 10 ** 400]}
        for key, values in cases.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    self.assert_invalid(key, **{key: value})

    def test_invalid_viewports_and_scales_that_round_to_zero_are_rejected(self):
        for viewport in (None, [], {}, {"width": 40, "height": 30}, {**self.viewport, "extra": 1}):
            with self.subTest(viewport=viewport):
                self.assert_invalid("viewport", viewport=viewport)
        for key in self.viewport:
            values = [True, False, 0, -1, "1", None, float("nan"), float("inf"), 10 ** 400]
            if key != "device_scale_factor":
                values.append(1.5)
            for value in values:
                with self.subTest(key=key, value=value):
                    self.assert_invalid(key, viewport={**self.viewport, key: value})
        self.assert_invalid("nonempty", viewport={"width": 1, "height": 1, "device_scale_factor": 1},
                            export_scale=0.01)

    def test_invalid_regions_are_rejected_instead_of_clipped_or_ignored(self):
        region = {"id": "button", "x": 1, "y": 1, "width": 2, "height": 2, "max_changed_ratio": 0}
        cases = [False, {}, [None], [region, region], [{**region, "mask": True}],
                 [{key: value for key, value in region.items() if key != "max_changed_ratio"}]]
        for key, values in {"id": ["", " ", True, 4], "x": [-1, True, 40, 1.1],
                            "y": [-1, 30], "width": [0, 40, False, float("inf")],
                            "height": [0, 30, float("nan")],
                            "max_changed_ratio": [True, -1, 1, 1.1, float("nan"), float("inf")]}.items():
            cases.extend([[{**region, key: value}] for value in values])
        for regions in cases:
            with self.subTest(regions=regions):
                self.assert_invalid("region", regions=regions)

    def test_excessive_declared_dimensions_fail_without_decoding(self):
        with patch.object(Image, "open", side_effect=AssertionError("must not decode")):
            self.assert_invalid("16000000", viewport={"width": 4001, "height": 4000, "device_scale_factor": 1})
            self.assert_invalid("16000000", viewport={"width": 1000, "height": 1000, "device_scale_factor": 1},
                                export_scale=8)

    def test_excessive_png_header_dimensions_fail_before_pillow_allocation(self):
        data = self.candidate.read_bytes()
        header = struct.pack(">II", 4001, 4000) + data[24:29]
        crc = struct.pack(">I", zlib.crc32(b"IHDR" + header))
        self.candidate.write_bytes(data[:16] + header + crc + data[33:])
        with patch.object(Image, "open", side_effect=AssertionError("must not decode")):
            self.reference.write_bytes(self.candidate.read_bytes())
            self.assert_invalid("16000000")

    def test_zero_png_header_dimensions_are_rejected(self):
        data = self.candidate.read_bytes()
        header = struct.pack(">II", 0, 30) + data[24:29]
        crc = struct.pack(">I", zlib.crc32(b"IHDR" + header))
        self.candidate.write_bytes(data[:16] + header + crc + data[33:])
        self.assert_invalid("pixels")

    def test_output_existing_directory_or_file_is_never_overwritten(self):
        self.output.mkdir()
        sentinel = self.output / "keep"
        sentinel.write_text("unchanged")
        with self.assertRaisesRegex(ValueError, "new directory"):
            self.compare()
        self.assertEqual("unchanged", sentinel.read_text())
        self.output = self.reference
        before = self.reference.read_bytes()
        with self.assertRaisesRegex(ValueError, "new directory"):
            self.compare()
        self.assertEqual(before, self.reference.read_bytes())

    def test_missing_output_parent_is_not_implicitly_created(self):
        self.output = self.root / "missing" / "comparison"
        self.assert_invalid("output parent")
        self.assertFalse(self.output.parent.exists())

    def test_missing_or_directory_input_is_rejected(self):
        self.candidate.unlink()
        self.assert_invalid("regular PNG")
        self.candidate.mkdir()
        self.assert_invalid("regular PNG")

    def test_input_and_output_symlinks_are_refused(self):
        self.candidate.unlink()
        self.candidate.symlink_to(self.reference)
        self.assert_invalid("symlink")
        self.candidate.unlink()
        self.image.save(self.candidate)
        self.output.symlink_to(self.root / "nonexistent", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "new directory"):
            self.compare()
        self.assertFalse((self.root / "nonexistent").exists())
        self.output.unlink()
        parent = self.root / "linked-parent"
        parent.symlink_to(self.root, target_is_directory=True)
        self.output = parent / "new"
        self.assert_invalid("output parent")

    def test_typical_viewport_uses_histograms_and_no_python_pixel_loop(self):
        reference = Image.new("RGBA", (1440, 900), "white")
        candidate = reference.copy()
        candidate.putpixel((1439, 899), (0, 0, 0, 255))
        self.save_pair(reference, candidate)
        with patch.object(Image.Image, "getpixel", side_effect=AssertionError("per-pixel Python loop")):
            result = self.compare(viewport={"width": 1440, "height": 900, "device_scale_factor": 1})
        self.assertEqual("FAIL", result["status"])
        self.assertEqual(1, result["changed_pixels"])
        self.assertEqual([1439, 899, 1440, 900], result["bbox"])


if __name__ == "__main__":
    unittest.main()
