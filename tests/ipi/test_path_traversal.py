import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from q_ai.ipi.generate_service import generate_documents
from q_ai.ipi.models import Format, Technique


class TestPathTraversal(unittest.TestCase):
    def test_path_traversal_prevention(self):
        with TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "payloads"
            output_dir.mkdir()

            # Case 1: Path traversal attempt
            generate_documents(
                callback_url="http://localhost",
                output=output_dir,
                format_name=Format.PDF,
                techniques=[Technique.WHITE_INK],
                base_name="../../PWNED",
                seed=42,
            )

            # Verify file is in output_dir, not outside
            expected_file = output_dir / "PWNED_white_ink.pdf"
            self.assertTrue(expected_file.exists(), "Sanitized file should exist in output dir")

            # Verify no file in tmpdir root (simulating outside directory)
            pwned_file = Path(tmpdir) / "PWNED_white_ink.pdf"
            self.assertFalse(pwned_file.exists(), "File should not exist outside output dir")

    def test_invalid_base_names(self):
        with TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            with self.assertRaises(ValueError):
                generate_documents(
                    callback_url="http://localhost",
                    output=output_dir,
                    format_name=Format.PDF,
                    techniques=[Technique.WHITE_INK],
                    base_name="..",
                )

            with self.assertRaises(ValueError):
                generate_documents(
                    callback_url="http://localhost",
                    output=output_dir,
                    format_name=Format.PDF,
                    techniques=[Technique.WHITE_INK],
                    base_name=".",
                )

            with self.assertRaises(ValueError):
                generate_documents(
                    callback_url="http://localhost",
                    output=output_dir,
                    format_name=Format.PDF,
                    techniques=[Technique.WHITE_INK],
                    base_name="",
                )


if __name__ == "__main__":
    unittest.main()
