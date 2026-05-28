import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import organize_screenshots as organizer


class ScreenshotOrganizerTests(unittest.TestCase):
    def test_safe_slug_normalizes_human_title(self):
        self.assertEqual(
            organizer.safe_slug(" Shopify Admin: Campaigns / Budget? "),
            "shopify-admin-campaigns-budget",
        )

    def test_destination_uses_month_folder_and_date_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "Screenshot 2026-05-28 at 11.24.51 AM.png"
            source.write_bytes(b"image")

            destination = organizer.destination_for(
                source,
                "Shopify admin campaigns budget",
                Path(tmp) / "Screenshots",
            )

            self.assertEqual(destination.parent.name, "2026-05")
            self.assertEqual(
                destination.name,
                "2026-05-28-shopify-admin-campaigns-budget.png",
            )

    def test_unique_path_does_not_overwrite_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            existing = directory / "2026-05-28-shopify-admin.png"
            existing.write_bytes(b"existing")

            result = organizer.unique_path(existing)

            self.assertEqual(result.name, "2026-05-28-shopify-admin-2.png")

    def test_claude_environment_removes_shopify_proxy_values(self):
        env = organizer.clean_claude_environment(
            {
                "PATH": "/usr/bin",
                "HOME": "/Users/example",
                "OPENAI_BASE_URL": "https://proxy.shopify.ai/v1",
                "OPENAI_API_BASE": "https://proxy.shopify.ai/v1",
                "OPENAI_API_KEY": "shopify-secret",
            }
        )

        self.assertEqual(env["PATH"], "/usr/bin")
        self.assertEqual(env["HOME"], "/Users/example")
        self.assertNotIn("OPENAI_BASE_URL", env)
        self.assertNotIn("OPENAI_API_BASE", env)
        self.assertNotIn("OPENAI_API_KEY", env)

    def test_parse_claude_stream_result_extracts_filename(self):
        stream = "\n".join(
            [
                json_line({"type": "system", "subtype": "init"}),
                json_line(
                    {
                        "type": "result",
                        "result": '{"filename": "Shopify admin campaigns budget"}',
                    }
                ),
            ]
        )

        self.assertEqual(
            organizer.parse_claude_stream_result(stream),
            "Shopify admin campaigns budget",
        )

    def test_parse_claude_stream_result_prefers_structured_output(self):
        stream = json_line(
            {
                "type": "result",
                "subtype": "success",
                "result": "",
                "structured_output": {"filename": "Google Payment Receipt May 2026"},
            }
        )

        self.assertEqual(
            organizer.parse_claude_stream_result(stream),
            "Google Payment Receipt May 2026",
        )

    def test_claude_command_uses_verbose_stream_json(self):
        command = organizer.claude_command("claude")

        self.assertIn("--verbose", command)
        self.assertIn("stream-json", command)

    def test_process_moves_to_needs_review_when_title_generation_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Screenshots"
            inbox = root / "_Inbox"
            inbox.mkdir(parents=True)
            source = inbox / "Screenshot 2026-05-28 at 11.24.51 AM.png"
            source.write_bytes(b"image")

            with mock.patch.object(
                organizer,
                "generate_title",
                side_effect=RuntimeError("claude failed"),
            ):
                result = organizer.process_file(
                    source,
                    screenshots_root=root,
                    claude_bin="claude",
                    log_path=root / ".automation" / "test.log",
                )

            review_file = root / "_Needs Review" / source.name
            self.assertFalse(result.ok)
            self.assertFalse(source.exists())
            self.assertTrue(review_file.exists())


def json_line(value):
    import json

    return json.dumps(value)


if __name__ == "__main__":
    unittest.main()
