import asyncio
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import zed_typst_preview as preview
from zed_typst_preview.lsp import (
    Buffers,
    frame_message,
    position_offset,
    read_async_frame,
    read_frame,
)


def encode(message, extra=b""):
    body = json.dumps(message, ensure_ascii=False).encode("utf-8")
    return (
        b"Content-Length: " + str(len(body)).encode() + b"\r\n" + extra + b"\r\n" + body
    )


class Frames(unittest.IsolatedAsyncioTestCase):
    async def test_both_readers_preserve_headers_and_unicode_bytes(self):
        first = encode(
            {"method": "example", "params": {"text": "α😀"}},
            b"Content-Type: application/vscode-jsonrpc; charset=utf-8\r\n",
        )
        second = encode({"id": 2, "result": None})
        sync = io.BytesIO(first + second)
        asynchronous = asyncio.StreamReader()
        asynchronous.feed_data(first[:17])
        asynchronous.feed_data(first[17:] + second)
        asynchronous.feed_eof()
        for expected in [first, second, None]:
            self.assertEqual(read_frame(sync), expected)
            self.assertEqual(await read_async_frame(asynchronous), expected)
        self.assertEqual(frame_message(first)["params"]["text"], "α😀")

    async def test_truncated_or_invalid_messages_fail(self):
        for data in [
            b"Content-Length: 4\r\n\r\n{}",
            b"Content-Length: -1\r\n\r\n",
            b"Content-Length: 2\r\n",
            b"Content-Length: 2\r\nContent-Length: 2\r\n\r\n{}",
        ]:
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    read_frame(io.BytesIO(data))
                reader = asyncio.StreamReader()
                reader.feed_data(data)
                reader.feed_eof()
                with self.assertRaises(ValueError):
                    await read_async_frame(reader)


class TextChanges(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="typst live buffer ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.file = self.root / "chapter α with spaces.typ"
        self.buffers = Buffers(self.root)

    def open(self, text="Café α 😀 text.\r\nSecond line."):
        self.buffers.client_message(
            {
                "method": "textDocument/didOpen",
                "params": {
                    "textDocument": {
                        "uri": self.file.as_uri(),
                        "version": 1,
                        "languageId": "typst",
                        "text": text,
                    }
                },
            }
        )

    def change(self, changes, version=2):
        self.buffers.client_message(
            {
                "method": "textDocument/didChange",
                "params": {
                    "textDocument": {"uri": self.file.as_uri(), "version": version},
                    "contentChanges": changes,
                },
            }
        )

    def test_incremental_edits_use_the_negotiated_encoding(self):
        for encoding in ["utf-8", "utf-16", "utf-32"]:
            with self.subTest(encoding=encoding):
                self.buffers = Buffers(self.root)
                self.buffers.client_message(
                    {"id": 1, "method": "initialize", "params": {}}
                )
                self.buffers.server_message(
                    {
                        "id": 1,
                        "result": {"capabilities": {"positionEncoding": encoding}},
                    }
                )
                self.open()
                prefix = "Café α "
                units = lambda text, encoding=encoding: (
                    len(text.encode(encoding))
                    if encoding == "utf-8"
                    else len(text.encode("utf-16-le")) // 2
                    if encoding == "utf-16"
                    else len(text)
                )
                self.change(
                    [
                        {
                            "range": {
                                "start": {"line": 0, "character": units(prefix)},
                                "end": {"line": 0, "character": units(prefix + "😀")},
                            },
                            "text": "β",
                        }
                    ]
                )
                self.assertEqual(
                    self.buffers.files[str(self.file)], "Café α β text.\r\nSecond line."
                )
                self.assertFalse(self.file.exists())

    def test_multiple_changes_apply_sequentially_and_full_replacements_work(self):
        self.open("abc")
        self.change(
            [
                {
                    "range": {
                        "start": {"line": 0, "character": 1},
                        "end": {"line": 0, "character": 2},
                    },
                    "text": "XYZ",
                },
                {
                    "range": {
                        "start": {"line": 0, "character": 4},
                        "end": {"line": 0, "character": 5},
                    },
                    "text": "!",
                },
            ]
        )
        self.assertEqual(self.buffers.files[str(self.file)], "aXYZ!")
        self.change([{"text": "A new document.\n"}], version=3)
        self.assertEqual(self.buffers.files[str(self.file)], "A new document.\n")

    def test_close_removes_the_unsaved_override(self):
        self.open()
        self.buffers.client_message(
            {
                "method": "textDocument/didClose",
                "params": {"textDocument": {"uri": self.file.as_uri()}},
            }
        )
        self.assertEqual(self.buffers.files, {})

    def test_stale_versions_and_split_surrogates_fail(self):
        self.open("😀")
        with self.assertRaisesRegex(ValueError, "out of order"):
            self.change([{"text": "old"}], version=1)
        with self.assertRaisesRegex(ValueError, "Unicode"):
            self.change(
                [
                    {
                        "range": {
                            "start": {"line": 0, "character": 1},
                            "end": {"line": 0, "character": 2},
                        },
                        "text": "x",
                    }
                ]
            )

    def test_nonlocal_and_outside_worktree_documents_are_not_mirrored(self):
        self.assertIsNone(self.buffers.filepath("untitled:Untitled-1"))
        self.assertIsNone(self.buffers.filepath("file://remote/share/main.typ"))
        self.assertIsNone(
            self.buffers.filepath((self.root.parent / "other.typ").as_uri())
        )

    def test_positions_clip_at_line_end_as_required_by_lsp(self):
        self.assertEqual(
            position_offset("é\r\nNext", {"line": 0, "character": 99}, "utf-16"), 1
        )
        self.assertEqual(
            position_offset("é\r\nNext", {"line": 1, "character": 0}, "utf-16"), 3
        )


class LivePreview(unittest.IsolatedAsyncioTestCase):
    async def test_missing_live_connection_has_an_actionable_terminal_message(self):
        buffers = preview.PreviewBuffers()
        with patch("sys.stderr", new_callable=io.StringIO) as output:
            await preview.report_missing_live_connection(buffers, timeout=0)
        self.assertIn("reading saved files", output.getvalue())
        self.assertIn("editor: restart language server", output.getvalue())

    async def test_connected_preview_does_not_report_a_missing_connection(self):
        buffers = preview.PreviewBuffers()
        buffers.live_connection.set()
        with patch("sys.stderr", new_callable=io.StringIO) as output:
            await preview.report_missing_live_connection(buffers, timeout=0)
        self.assertEqual(output.getvalue(), "")

    async def test_initial_compilation_reports_a_retry_without_blocking_lsp(self):
        with tempfile.TemporaryDirectory() as directory:
            buffers = preview.PreviewBuffers(wait_for_compile=True)
            websocket = AsyncMock()
            with self.assertRaises(preview.PreviewNotReady):
                await buffers.update(websocket, Path(directory).resolve(), {})
            websocket.send.assert_not_called()

    async def test_startup_sync_cannot_erase_a_concurrent_buffer_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            files = {str(root / "main.typ"): "Unsaved text"}
            buffers, websocket = preview.PreviewBuffers(), AsyncMock()
            started, release = asyncio.Event(), asyncio.Event()
            messages = []

            async def send(raw):
                messages.append(json.loads(raw))
                if len(messages) == 1:
                    started.set()
                    await release.wait()

            async def incoming():
                yield json.dumps({"event": "syncEditorChanges"})

            websocket.send.side_effect = send
            websocket.__aiter__.side_effect = incoming
            update = asyncio.create_task(buffers.update(websocket, root, files))
            await started.wait()
            startup = asyncio.create_task(
                preview.editor_messages(websocket, root, "zed", buffers)
            )
            await asyncio.sleep(0)
            release.set()
            await update
            with self.assertRaisesRegex(RuntimeError, "closed the preview"):
                await startup
            self.assertEqual(
                [message["files"] for message in messages], [{}, files, {}, files]
            )

    async def test_memory_updates_are_incremental_and_closed_files_are_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            first, second = str(root / "first.typ"), str(root / "second.typ")
            buffers, websocket = preview.PreviewBuffers(), AsyncMock()
            await buffers.update(websocket, root, {first: "Old", second: "Same"})
            await buffers.update(websocket, root, {first: "New", second: "Same"})
            await buffers.update(websocket, root, {first: "New"})
            messages = [
                json.loads(call.args[0]) for call in websocket.send.await_args_list
            ]
            self.assertEqual(
                messages,
                [
                    {"event": "syncMemoryFiles", "files": {}},
                    {"event": "updateMemoryFiles", "files": {first: "Old"}},
                    {"event": "updateMemoryFiles", "files": {second: "Same"}},
                    {"event": "updateMemoryFiles", "files": {first: "New"}},
                    {"event": "removeMemoryFiles", "files": [second]},
                ],
            )

    async def test_cursor_jumps_use_unsaved_text_without_saving_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / "main.typ"
            path.write_text("Saved.", encoding="utf-8")
            files = {str(path): "An unsaved line.\nα 😀 target"}
            column = len("α 😀 ".encode()) + 1
            forward = preview.scroll_request(root, str(path), 2, column, files)
            self.assertEqual((forward["line"], forward["character"]), (1, 4))
            reverse = preview.zed_location(
                root, {"filepath": str(path), "start": [1, 4]}, files
            )
            self.assertEqual(reverse, f"{path}:2:{column}")
            self.assertEqual(path.read_text(), "Saved.")

    async def test_session_publication_is_complete_and_exclusive(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            session = {"port": 1234, "token": "a" * 40}
            preview.publish_session(path, session)
            self.assertEqual(preview.read_session(path), session)
            with self.assertRaises(FileExistsError):
                preview.publish_session(path, {"port": 5678, "token": "b" * 40})
            self.assertEqual(preview.read_session(path), session)
