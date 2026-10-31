import unittest

from app import exception_detail


class ExceptionDiagnosticsTests(unittest.TestCase):
    def test_preserves_last_line_of_exception_message(self):
        error = RuntimeError("internal context\nThe media URL is unavailable")
        self.assertEqual(exception_detail(error), "The media URL is unavailable")

    def test_empty_exception_has_a_useful_fallback(self):
        error = RuntimeError()
        self.assertIn("RuntimeError", exception_detail(error))
        self.assertNotEqual(exception_detail(error), "Proses gagal.")

    def test_detail_is_bounded(self):
        error = RuntimeError("x" * 1000)
        self.assertEqual(len(exception_detail(error, 40)), 40)


if __name__ == "__main__":
    unittest.main()
