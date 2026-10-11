"""Xiaohongshu OCR image decoding and reading-order tests."""

from io import BytesIO
import unittest

from PIL import Image

from app.fetchers.xhs_ocr import recognize_image_bytes


class _FakeEngine:
    def __call__(self, _image):
        return (
            [
                ([[10, 40], [80, 40], [80, 60], [10, 60]], "第二行", 0.98),
                ([[100, 5], [180, 5], [180, 25], [100, 25]], "右上", 0.99),
                ([[5, 5], [80, 5], [80, 25], [5, 25]], "第一行", 0.99),
            ],
            None,
        )


class XhsOcrTests(unittest.TestCase):
    def test_recognized_lines_are_sorted_top_to_bottom_and_left_to_right(self):
        image_buffer = BytesIO()
        Image.new("RGB", (220, 80), "white").save(image_buffer, format="PNG")

        text = recognize_image_bytes(image_buffer.getvalue(), engine=_FakeEngine())

        self.assertEqual(text, "第一行\n右上\n第二行")

    def test_empty_ocr_result_returns_empty_text(self):
        class _EmptyEngine:
            def __call__(self, _image):
                return None, None

        image_buffer = BytesIO()
        Image.new("RGB", (80, 40), "white").save(image_buffer, format="PNG")
        self.assertEqual(recognize_image_bytes(image_buffer.getvalue(), engine=_EmptyEngine()), "")

    def test_invalid_image_bytes_raise_a_decode_error(self):
        with self.assertRaisesRegex(ValueError, "无法解码图片"):
            recognize_image_bytes(b"not-an-image", engine=_FakeEngine())


if __name__ == "__main__":
    unittest.main(verbosity=2)