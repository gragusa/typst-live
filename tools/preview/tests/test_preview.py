import argparse
import asyncio
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import zed_typst_preview as preview
from websockets.exceptions import ConnectionClosed


class Coordinates(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="typst preview test ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.file = self.root / "paper with spaces.typ"

    def test_unicode_coordinates_round_trip_at_every_character(self):
        lines = ["Café α 😀 e\u0301 end", "\tSecond line", ""]
        self.file.write_text("\n".join(lines), encoding="utf-8")
        for row, line in enumerate(lines):
            for character in range(len(line) + 1):
                with self.subTest(row=row, character=character):
                    column = len(line[:character].encode("utf-8")) + 1
                    message = preview.scroll_request(
                        self.root, str(self.file), row + 1, column
                    )
                    self.assertEqual(message["line"], row)
                    self.assertEqual(message["character"], character)
                    reverse = {"filepath": str(self.file), "start": [row, character]}
                    self.assertEqual(
                        preview.zed_location(self.root, reverse),
                        f"{self.file}:{row + 1}:{column}",
                    )

    def test_crlf_rows_and_end_of_line(self):
        self.file.write_bytes("é\r\n😀\r\n".encode())
        message = preview.scroll_request(self.root, str(self.file), 2, 5)
        self.assertEqual((message["line"], message["character"]), (1, 1))

    def test_invalid_positions_are_rejected(self):
        self.file.write_text("é", encoding="utf-8")
        for row, column in [(0, 1), (1, 0), (2, 1), (1, 4), (True, 1), (1, True)]:
            with self.subTest(row=row, column=column), self.assertRaises(ValueError):
                preview.scroll_request(self.root, str(self.file), row, column)
        with self.assertRaises(UnicodeDecodeError):
            preview.scroll_request(self.root, str(self.file), 1, 2)

    def test_invalid_inverse_positions_are_rejected(self):
        self.file.write_text("Text", encoding="utf-8")
        for start in [None, [], [0], [-1, 0], [0, -1], [0, 5], [True, 0]]:
            with self.subTest(start=start), self.assertRaises(ValueError):
                preview.zed_location(
                    self.root, {"filepath": str(self.file), "start": start}
                )

    def test_files_outside_the_worktree_are_rejected(self):
        self.file.write_text("Text", encoding="utf-8")
        with self.assertRaises(ValueError):
            preview.scroll_request(self.root / "subdirectory", str(self.file), 1, 1)

    def test_root_is_the_worktree_for_files_inside_it(self):
        chapter = self.root / "chapters" / "intro.typ"
        chapter.parent.mkdir()
        chapter.write_text("Text", encoding="utf-8")
        self.assertEqual(preview.project_root(self.root, chapter), self.root)

    def test_root_is_the_file_directory_for_files_outside_the_worktree(self):
        worktree = self.root / "project"
        outside = self.root / "downloads" / "notes.typ"
        worktree.mkdir()
        outside.parent.mkdir()
        outside.write_text("Text", encoding="utf-8")
        self.assertEqual(preview.project_root(worktree, outside), outside.parent)
        self.assertEqual(preview.project_root(worktree, str(outside)), outside.parent)

    def test_single_file_worktree_root_is_its_directory(self):
        self.file.write_text("Text", encoding="utf-8")
        self.assertEqual(preview.worktree_directory(self.file), self.root)
        self.assertEqual(preview.project_root(self.file, self.file), self.root)

    def test_missing_worktree_fails(self):
        with self.assertRaises(FileNotFoundError):
            preview.project_root(self.root / "missing", self.file)

    def test_different_worktrees_have_different_session_files(self):
        directory = self.root / "runtime"
        with patch.dict(
            "os.environ", {"ZED_TYPST_PREVIEW_RUNTIME_DIR": str(directory)}
        ):
            self.assertNotEqual(
                preview.session_path(self.root),
                preview.session_path(self.root / "other"),
            )

    def test_extra_arguments_follow_the_entry_file(self):
        args = preview.parser().parse_args(
            [
                "start",
                "--root",
                str(self.root),
                "--mode",
                "slide",
                str(self.file),
                "--",
                "--font-path",
                "/custom fonts",
                "--input",
                "draft=true",
            ]
        )
        self.assertEqual(args.mode, "slide")
        self.assertEqual(
            args.extra, ["--font-path", "/custom fonts", "--input", "draft=true"]
        )


class RunTool(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="typst run test ")
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name).resolve()
        self.worktree = base / "project"
        self.outside = base / "downloads with spaces" / "notes.typ"
        self.tools = base / "tools"
        for directory in (self.worktree, self.outside.parent, self.tools):
            directory.mkdir()
        self.outside.write_text("Text", encoding="utf-8")

    def test_root_follows_the_subcommand(self):
        root = Path("/chosen root")
        self.assertEqual(
            preview.tool_command(root, ["typst", "compile", "a.typ", "a-{p}.png"]),
            ["typst", "compile", "--root", "/chosen root", "a.typ", "a-{p}.png"],
        )
        self.assertEqual(
            preview.tool_command(root, ["/bin/tinymist", "preview", "--open", "a.typ"]),
            ["/bin/tinymist", "preview", "--root", "/chosen root", "--open", "a.typ"],
        )
        self.assertEqual(
            preview.tool_command(root, ["typst.exe", "watch", "a.typ"])[:4],
            ["typst.exe", "watch", "--root", "/chosen root"],
        )

    def test_other_commands_and_explicit_roots_are_rejected(self):
        for command in [[], ["typst"], ["typst", "fonts"], ["sh", "compile"]]:
            with self.subTest(command=command):
                with self.assertRaisesRegex(ValueError, "run accepts only"):
                    preview.tool_command(Path("/root"), command)
        for flag in [["--root", "/other"], ["--root=/other"]]:
            with self.subTest(flag=flag):
                with self.assertRaisesRegex(ValueError, "chooses the Typst root"):
                    preview.tool_command(
                        Path("/root"), ["typst", "compile", *flag, "a.typ"]
                    )

    def run_companion(self, *tool, path=None):
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "zed_typst_preview",
                "run",
                "--root",
                str(self.worktree),
                "--file",
                str(self.outside),
                "--",
                *tool,
            ],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": path or f"{self.tools}{os.pathsep}{os.environ['PATH']}",
            },
            timeout=20,
        )

    @unittest.skipIf(os.name == "nt", "the stand-in tool is a POSIX shell script")
    def test_tool_runs_with_the_file_directory_as_root(self):
        tool = self.tools / "typst"
        tool.write_text('#!/bin/sh\nprintf \'%s\\n\' "$@"\nexit 3\n', encoding="utf-8")
        tool.chmod(0o755)
        result = self.run_companion("typst", "compile", str(self.outside), "out-{p}.png")
        self.assertEqual(result.returncode, 3, result.stderr)
        lines = result.stdout.splitlines()
        self.assertIn(f"using its directory {self.outside.parent}", lines[0])
        self.assertEqual(
            lines[1:],
            ["compile", "--root", str(self.outside.parent), str(self.outside), "out-{p}.png"],
        )

    def test_missing_tool_is_reported(self):
        result = self.run_companion(
            "tinymist", "preview", str(self.outside), path=str(self.tools)
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("tinymist must be executable; check PATH", result.stderr)


class Connection(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="typst preview ipc ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.file = self.root / "source.typ"
        self.file.write_text("é target", encoding="utf-8")
        self.websocket = AsyncMock()
        self.token = "test-token-" * 4
        self.stop = asyncio.Event()
        self.server = await asyncio.start_server(
            lambda reader, writer: preview.serve_request(
                reader, writer, self.websocket, self.root, self.token, stop=self.stop
            ),
            "127.0.0.1",
            0,
        )
        self.session = {
            "token": self.token,
            "port": self.server.sockets[0].getsockname()[1],
        }

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()

    async def test_sync_sends_a_real_protocol_message(self):
        await preview.request(
            self.session,
            {
                "event": "sync",
                "filepath": str(self.file),
                "row": 1,
                "column": 4,
            },
        )
        message = json.loads(self.websocket.send.call_args.args[0])
        self.assertEqual(
            message,
            {
                "event": "panelScrollTo",
                "filepath": str(self.file),
                "line": 0,
                "character": 2,
            },
        )

    async def test_invalid_token_cannot_scroll_the_preview(self):
        session = {**self.session, "token": "different-token-" * 4}
        with self.assertRaisesRegex(ValueError, "session token"):
            await preview.request(session, {"event": "status"})
        self.websocket.send.assert_not_called()

    async def test_status_does_not_change_the_preview(self):
        await preview.request(self.session, {"event": "status"})
        self.websocket.send.assert_not_called()
        self.assertFalse(self.stop.is_set())

    async def test_stop_request_signals_the_preview_without_touching_tinymist(self):
        await preview.request(self.session, {"event": "stop"})
        self.assertTrue(self.stop.is_set())
        self.websocket.send.assert_not_called()

    async def test_invalid_token_cannot_stop_the_preview(self):
        with self.assertRaisesRegex(ValueError, "Invalid preview session token"):
            await preview.request({**self.session, "token": "x" * 44}, {"event": "stop"})
        self.assertFalse(self.stop.is_set())

    async def test_closed_tinymist_connection_asks_the_editor_to_retry(self):
        # The editor proxy treats a retry as transient; any other error stops it.
        self.websocket.send.side_effect = ConnectionClosed(None, None)
        with self.assertRaises(preview.PreviewNotReady):
            await preview.request(
                self.session,
                {"event": "buffers", "client": "editor", "files": {str(self.file): "é"}},
            )

    async def test_invalid_source_position_is_reported_to_the_task(self):
        with self.assertRaises(ValueError):
            await preview.request(
                self.session,
                {
                    "event": "sync",
                    "filepath": str(self.file),
                    "row": 1,
                    "column": 2,
                },
            )
        self.websocket.send.assert_not_called()

    async def test_preview_click_invokes_zed_without_a_shell(self):
        self.file = self.root / "source ; $(example).typ"
        self.file.write_text("é😀 target", encoding="utf-8")

        async def messages():
            yield json.dumps({"event": "syncEditorChanges"})
            yield json.dumps(
                {"event": "editorScrollTo", "filepath": str(self.file), "start": [0, 2]}
            )

        self.websocket.__aiter__.side_effect = messages
        child = AsyncMock()
        child.wait.return_value = 0
        with (
            patch.object(
                preview.asyncio, "create_subprocess_exec", return_value=child
            ) as spawn,
            self.assertRaisesRegex(RuntimeError, "closed the preview"),
        ):
            await preview.editor_messages(self.websocket, self.root, "zed")
        spawn.assert_awaited_once_with("zed", "--existing", f"{self.file}:1:7")
        self.assertEqual(
            json.loads(self.websocket.send.call_args.args[0]),
            {"event": "syncMemoryFiles", "files": {}},
        )

    async def test_unopenable_preview_clicks_are_reported_and_skipped(self):
        package = Path(self.temporary.name).parent / "packages" / "utils.typ"
        failing, working = AsyncMock(), AsyncMock()
        failing.wait.return_value = 1
        working.wait.return_value = 0

        async def messages():
            # Package code that generates slide content lies outside the worktree.
            yield json.dumps(
                {"event": "editorScrollTo", "filepath": str(package), "start": [0, 0]}
            )
            yield json.dumps(
                {"event": "editorScrollTo", "filepath": str(self.file), "start": [9, 0]}
            )
            for _ in range(2):
                yield json.dumps(
                    {"event": "editorScrollTo", "filepath": str(self.file), "start": [0, 2]}
                )

        self.websocket.__aiter__.side_effect = messages
        logs = io.StringIO()
        with (
            patch.object(
                preview.asyncio,
                "create_subprocess_exec",
                side_effect=[failing, working],
            ) as spawn,
            contextlib.redirect_stderr(logs),
            self.assertRaisesRegex(RuntimeError, "closed the preview"),
        ):
            await preview.editor_messages(self.websocket, self.root, "zed")
        self.assertEqual(spawn.await_count, 2)
        spawn.assert_awaited_with("zed", "--existing", f"{self.file}:1:4")
        reports = logs.getvalue().splitlines()
        self.assertEqual(len(reports), 3, reports)
        self.assertIn("must be a file inside", reports[0])
        self.assertIn("Row outside", reports[1])
        self.assertIn("Zed could not open", reports[2])
        self.assertTrue(all(r.startswith("Preview click not opened in Zed: ") for r in reports))

    async def test_missing_preview_has_an_actionable_error(self):
        with (
            patch.dict(
                "os.environ",
                {"ZED_TYPST_PREVIEW_RUNTIME_DIR": str(self.root / "runtime")},
            ),
            self.assertRaisesRegex(ValueError, "Synchronized Preview"),
        ):
            await preview.sync(
                argparse.Namespace(root=self.root, file=self.file, row=1, column=1)
            )

    async def test_sync_outside_the_worktree_uses_the_file_directory_session(self):
        worktree = self.root / "project"
        worktree.mkdir()
        outside = self.root / "downloads" / "notes.typ"
        outside.parent.mkdir()
        outside.write_text("Text", encoding="utf-8")
        with (
            patch.dict(
                "os.environ",
                {"ZED_TYPST_PREVIEW_RUNTIME_DIR": str(self.root / "runtime")},
            ),
            patch.object(preview, "request", AsyncMock()) as request,
        ):
            preview.publish_session(
                preview.session_path(outside.parent),
                {"port": 1, "token": "t" * 32},
            )
            with contextlib.redirect_stdout(io.StringIO()):
                await preview.sync(
                    argparse.Namespace(root=worktree, file=outside, row=1, column=1)
                )
        self.assertEqual(request.await_args.args[0], {"port": 1, "token": "t" * 32})


if __name__ == "__main__":
    unittest.main()
