import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import organize_screenshots as organizer


class ScreenshotOrganizerTests(unittest.TestCase):
    def test_safe_slug_normalizes_human_title(self):
        self.assertEqual(
            organizer.safe_slug(" Acme Admin: Campaigns / Budget? "),
            "acme-admin-campaigns-budget",
        )

    def test_destination_uses_month_folder_and_date_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "Screenshot 2026-05-28 at 11.24.51 AM.png"
            source.write_bytes(b"image")

            destination = organizer.destination_for(
                source,
                "Acme admin campaigns budget",
                Path(tmp) / "Screenshots",
            )

            self.assertEqual(destination.parent.name, "2026-05")
            self.assertEqual(
                destination.name,
                "2026-05-28-acme-admin-campaigns-budget.png",
            )

    def test_unique_path_does_not_overwrite_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            existing = directory / "2026-05-28-acme-admin.png"
            existing.write_bytes(b"existing")

            result = organizer.unique_path(existing)

            self.assertEqual(result.name, "2026-05-28-acme-admin-2.png")

    def test_claude_environment_keeps_everything_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            env = organizer.clean_claude_environment({"PATH": "/usr/bin", "HOME": "/h", "OPENAI_API_KEY": "x"})
        self.assertEqual(env, {"PATH": "/usr/bin", "HOME": "/h", "OPENAI_API_KEY": "x"})

    def test_claude_environment_strips_configured_vars(self):
        with mock.patch.dict(os.environ, {"SCREENSHOT_ORGANIZER_STRIP_ENV": "FOO, BAR"}, clear=True):
            env = organizer.clean_claude_environment({"PATH": "/usr/bin", "HOME": "/h", "FOO": "1", "BAR": "2"})
        self.assertEqual(env, {"PATH": "/usr/bin", "HOME": "/h"})

    def test_claude_environment_sets_home_when_missing(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            env = organizer.clean_claude_environment({"PATH": "/usr/bin"})
        self.assertIn("HOME", env)

    def test_parse_claude_stream_result_extracts_filename(self):
        stream = "\n".join(
            [
                json_line({"type": "system", "subtype": "init"}),
                json_line(
                    {
                        "type": "result",
                        "result": '{"filename": "Acme admin campaigns budget"}',
                    }
                ),
            ]
        )

        self.assertEqual(
            organizer.parse_claude_stream_result(stream),
            "Acme admin campaigns budget",
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


    def test_claude_command_lean_flags_are_optional(self):
        lean = organizer.claude_command("claude", model="haiku", lean=True)
        plain = organizer.claude_command("claude", model="haiku", lean=False)
        for flag in organizer.LEAN_FLAGS:
            self.assertIn(flag, lean)
            self.assertNotIn(flag, plain)
        self.assertEqual(lean[lean.index("--model") + 1], "haiku")

    def test_generate_title_retries_without_lean_flags_when_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "Screenshot x.png"
            source.write_bytes(organizer.tiny_png())
            ok_stdout = json_line({"type": "result", "structured_output": {"filename": "Test"}})
            calls = []

            def fake_run(cmd, **kwargs):
                calls.append(cmd)
                if "--strict-mcp-config" in cmd:
                    return mock.Mock(returncode=1, stdout="", stderr="error: unknown option '--strict-mcp-config'")
                return mock.Mock(returncode=0, stdout=ok_stdout, stderr="")

            with mock.patch.object(organizer, "prepare_image", return_value=(b"x", "image/png")), \
                    mock.patch.object(organizer, "load_api_key", return_value=None), \
                    mock.patch.object(organizer.subprocess, "run", side_effect=fake_run):
                self.assertEqual(organizer.generate_title(source, "claude"), "Test")
            self.assertEqual(len(calls), 2)

    def test_prepare_image_falls_back_to_png_without_sips(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "Screenshot x.png"
            source.write_bytes(organizer.tiny_png())
            with mock.patch.object(organizer.shutil, "which", return_value=None):
                data, media_type = organizer.prepare_image(source)
            self.assertEqual(media_type, "image/png")
            self.assertTrue(data.startswith(b"\x89PNG"))

    def test_prepare_image_rejects_huge_png_without_sips(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "Screenshot x.png"
            source.write_bytes(b"0" * (organizer.MAX_IMAGE_BYTES + 1))
            with mock.patch.object(organizer.shutil, "which", return_value=None):
                with self.assertRaises(RuntimeError):
                    organizer.prepare_image(source)

    def test_process_skips_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Screenshots"
            result = organizer.process_file(
                root / "_Inbox" / "Screenshot gone.png",
                screenshots_root=root,
                log_path=root / "test.log",
            )
            self.assertTrue(result.ok)
            self.assertIsNone(result.destination)

    def test_process_handles_file_vanishing_mid_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Screenshots"
            inbox = root / "_Inbox"
            inbox.mkdir(parents=True)
            source = inbox / "Screenshot 2026-05-28 at 11.24.51 AM.png"
            source.write_bytes(b"image")

            def vanish(path, *args, **kwargs):
                path.unlink()
                raise RuntimeError("claude failed")

            with mock.patch.object(organizer, "wait_until_stable"), \
                    mock.patch.object(organizer, "generate_title", side_effect=vanish):
                result = organizer.process_file(source, screenshots_root=root, log_path=root / "t.log")
            self.assertFalse(result.ok)
            self.assertIn("disappeared", result.message)

    def test_retry_review_leaves_failures_in_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Screenshots"
            review = root / "_Needs Review"
            review.mkdir(parents=True)
            source = review / "Screenshot 2026-05-28 at 11.24.51 AM.png"
            source.write_bytes(b"image")
            with mock.patch.object(organizer, "wait_until_stable"), \
                    mock.patch.object(organizer, "generate_title", side_effect=RuntimeError("nope")):
                code = organizer.main(["--retry-review", "--screenshots-root", str(root),
                                       "--log-path", str(root / "t.log")])
            self.assertEqual(code, 1)
            self.assertTrue(source.exists())
            self.assertEqual(len(list(review.iterdir())), 1)

    def test_tiny_png_is_valid_header(self):
        self.assertTrue(organizer.tiny_png().startswith(b"\x89PNG\r\n\x1a\n"))


    def test_load_api_key_prefers_file_then_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            key_file = Path(tmp) / "key"
            with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "from-env"}, clear=True):
                self.assertEqual(organizer.load_api_key(key_file), "from-env")
                key_file.write_text("from-file\n")
                self.assertEqual(organizer.load_api_key(key_file), "from-file")
            with mock.patch.dict(os.environ, {}, clear=True):
                key_file.unlink()
                self.assertIsNone(organizer.load_api_key(key_file))

    def test_api_model_id_maps_aliases(self):
        self.assertEqual(organizer.api_model_id("haiku"), "claude-haiku-5-5")
        self.assertEqual(organizer.api_model_id("claude-custom-1"), "claude-custom-1")

    def test_generate_title_uses_api_when_key_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "Screenshot x.png"
            source.write_bytes(organizer.tiny_png())
            captured = {}

            class FakeResponse:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def read(self):
                    return json_line({"content": [{"type": "text", "text": '{"filename": "Api Title"}'}]}).encode()

            def fake_urlopen(request, timeout):
                captured["body"] = json.loads(request.data)
                captured["key"] = request.headers["X-api-key"]
                return FakeResponse()

            with mock.patch.object(organizer, "prepare_image", return_value=(b"x", "image/jpeg")), \
                    mock.patch.object(organizer, "load_api_key", return_value="sk-test"), \
                    mock.patch.object(organizer.urllib.request, "urlopen", side_effect=fake_urlopen), \
                    mock.patch.object(organizer.subprocess, "run") as cli:
                self.assertEqual(organizer.generate_title(source, "claude", model="haiku"), "Api Title")
            cli.assert_not_called()
            self.assertEqual(captured["key"], "sk-test")
            self.assertEqual(captured["body"]["model"], "claude-haiku-5-5")
            image = captured["body"]["messages"][0]["content"][0]["source"]
            self.assertEqual(image["media_type"], "image/jpeg")

    def test_api_errors_include_status_and_message(self):
        import io
        import urllib.error

        error = urllib.error.HTTPError(
            organizer.API_URL, 401, "Unauthorized", {},
            io.BytesIO(b'{"error": {"message": "invalid x-api-key"}}'),
        )
        with mock.patch.object(organizer.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(RuntimeError) as ctx:
                organizer._generate_title_api(b"x", "image/png", "bad", "haiku")
        self.assertIn("401", str(ctx.exception))
        self.assertIn("invalid x-api-key", str(ctx.exception))

    def test_parse_title_text_tolerates_fences_and_chatter(self):
        self.assertEqual(
            organizer.parse_title_text('Sure!\n```json\n{"filename": "Release Notes"}\n```'),
            "Release Notes",
        )


def json_line(value):
    import json

    return json.dumps(value)


if __name__ == "__main__":
    unittest.main()
