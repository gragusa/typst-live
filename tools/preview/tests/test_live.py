"""End-to-end unsaved-buffer tests with Tinymist and the stdio proxy."""

import asyncio
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

import zed_typst_preview as preview
from websockets.asyncio.client import connect
from zed_typst_preview.lsp import frame_message, read_async_frame


async def send(process, message):
    body = json.dumps({"jsonrpc": "2.0", **message}).encode()
    process.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    await process.stdin.drain()


async def response(process, request_id):
    async with asyncio.timeout(15):
        while True:
            frame = await read_async_frame(process.stdout)
            if frame is None:
                raise AssertionError("Language server closed stdout")
            message = frame_message(frame)
            if message.get("id") == request_id:
                if "error" in message:
                    raise AssertionError(message)
                return message


async def language_server(worktree, folder, environment, logs, processes):
    """Start and initialize an LSP proxy for a Zed worktree."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "zed_typst_preview",
        "lsp",
        "--root",
        str(worktree),
        "--server",
        os.environ["TINYMIST_TEST_BINARY"],
        "--",
        "lsp",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=logs,
        env=environment,
    )
    processes.append(process)
    await send(
        process,
        {
            "id": 1,
            "method": "initialize",
            "params": {
                "processId": os.getpid(),
                "rootUri": folder.as_uri(),
                "capabilities": {
                    "general": {"positionEncodings": ["utf-8", "utf-16"]}
                },
                "workspaceFolders": [{"uri": folder.as_uri(), "name": "test"}],
            },
        },
    )
    await response(process, 1)
    await send(process, {"method": "initialized", "params": {}})
    return process


@unittest.skipUnless(
    os.environ.get("TINYMIST_TEST_BINARY"),
    "set TINYMIST_TEST_BINARY for live-buffer integration",
)
class LiveBuffers(unittest.IsolatedAsyncioTestCase):
    async def test_unsaved_edits_replay_update_and_close_without_disk_writes(self):
        for outside in (False, True):
            with self.subTest(outside_the_worktree=outside):
                await self.check_unsaved_edits(outside)

    async def check_unsaved_edits(self, outside):
        # For files opened from outside every project folder, Zed's tasks name
        # an unrelated project folder and each file's language server receives
        # the file itself as its worktree.
        with tempfile.TemporaryDirectory(prefix="typst live integration ") as directory:
            root = Path(directory).resolve()
            project = root / "project"
            project.mkdir()
            entry = root / "main.typ"
            chapter = root / "chapter with spaces.typ"
            entry.write_text(
                'Introduction.\n#include "chapter with spaces.typ"', encoding="utf-8"
            )
            saved = "Saved chapter."
            chapter.write_text(saved, encoding="utf-8")
            environment = {
                **os.environ,
                "ZED_TYPST_PREVIEW_RUNTIME_DIR": str(root / "runtime"),
            }
            with tempfile.TemporaryFile(mode="w+") as logs:
                processes = []
                browser_process = None
                try:
                    # Zed starts one language server per worktree, and a file
                    # opened from outside the project folders is its own worktree.
                    servers = {}
                    for path in (entry, chapter):
                        worktree = path if outside else root
                        if worktree not in servers:
                            servers[worktree] = await language_server(
                                worktree, root, environment, logs, processes
                            )
                        servers[path] = servers[worktree]
                    unsaved = "#pagebreak()\nCafé α 😀 final text."
                    # These buffers are opened and edited before the preview is even launched.
                    for path, text in [(entry, entry.read_text()), (chapter, unsaved)]:
                        await send(
                            servers[path],
                            {
                                "method": "textDocument/didOpen",
                                "params": {
                                    "textDocument": {
                                        "uri": path.as_uri(),
                                        "languageId": "typst",
                                        "version": 1,
                                        "text": text,
                                    }
                                },
                            },
                        )
                    browser_process = await asyncio.create_subprocess_exec(
                        sys.executable,
                        "-m",
                        "zed_typst_preview",
                        "start",
                        "--root",
                        str(project if outside else root),
                        "--no-open",
                        "--zed",
                        sys.executable,
                        "--tinymist",
                        os.environ["TINYMIST_TEST_BINARY"],
                        str(entry),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=logs,
                        env=environment,
                    )
                    async with asyncio.timeout(20):
                        while True:
                            line = (await browser_process.stdout.readline()).decode()
                            if not line:
                                self.fail("Preview exited before startup")
                            match = re.search(
                                r"Synchronized preview ready: (http://127\.0\.0\.1:\d+/)",
                                line,
                            )
                            if match:
                                url = match[1]
                                break
                        while (
                            b"Live preview connected"
                            not in await browser_process.stdout.readline()
                        ):
                            if browser_process.returncode is not None:
                                self.fail(
                                    "Preview exited before live-buffer connection"
                                )
                    key = preview.session_path(
                        root
                    )  # derive using the test's explicit runtime below
                    key = root / "runtime" / key.name
                    session = preview.read_session(key)
                    # Each proxy sends its buffers separately; the preview holds both.
                    async with asyncio.timeout(5):
                        while (
                            await preview.request(session, {"event": "status"})
                        )["buffer_count"] != 2:
                            await asyncio.sleep(0.05)
                    async with connect(
                        url.replace("http://", "ws://"),
                        origin=url.rstrip("/"),
                        proxy=None,
                        ping_interval=None,
                    ) as browser:
                        await browser.send("current")

                        async def jump(row, column, expected_page):
                            async with asyncio.timeout(15):
                                while True:
                                    await preview.request(
                                        session,
                                        {
                                            "event": "sync",
                                            "filepath": str(chapter),
                                            "row": row,
                                            "column": column,
                                        },
                                    )
                                    try:
                                        async with asyncio.timeout(0.5):
                                            while True:
                                                frame = await browser.recv()
                                                if isinstance(
                                                    frame, bytes
                                                ) and frame.startswith(b"jump,"):
                                                    positions = frame.decode().split(
                                                        ","
                                                    )[1:]
                                                    if (
                                                        positions
                                                        and positions[0]
                                                        and positions[0].split()[0]
                                                        == str(expected_page)
                                                    ):
                                                        return
                                    except TimeoutError:
                                        pass

                        column = len("Café α 😀 ".encode()) + 1
                        await jump(2, column, 2)
                        # Insert a page while typing. There is no didSave and no disk write.
                        await send(
                            servers[chapter],
                            {
                                "method": "textDocument/didChange",
                                "params": {
                                    "textDocument": {
                                        "uri": chapter.as_uri(),
                                        "version": 2,
                                    },
                                    "contentChanges": [
                                        {
                                            "range": {
                                                "start": {"line": 1, "character": 0},
                                                "end": {"line": 1, "character": 0},
                                            },
                                            "text": "Second page text.\n#pagebreak()\n",
                                        }
                                    ],
                                },
                            },
                        )
                        # Wait for the publisher's new buffer rather than accepting a disk fallback.
                        await asyncio.sleep(0.05)
                        await jump(4, column, 3)
                        self.assertEqual(chapter.read_text(), saved)
                        await send(
                            servers[chapter],
                            {
                                "method": "textDocument/didClose",
                                "params": {"textDocument": {"uri": chapter.as_uri()}},
                            },
                        )
                        await asyncio.sleep(0.05)
                        await jump(1, 1, 1)
                        self.assertEqual(chapter.read_text(), saved)
                    for lsp in processes:
                        await send(lsp, {"id": 2, "method": "shutdown", "params": None})
                        await response(lsp, 2)
                        await send(lsp, {"method": "exit"})
                        await asyncio.wait_for(lsp.wait(), 10)
                        self.assertEqual(lsp.returncode, 0)
                except BaseException:
                    logs.seek(0)
                    diagnostic = (
                        Path("/private/tmp/zed-typst-live-failure.log")
                        if sys.platform == "darwin"
                        else root / "failure.log"
                    )
                    diagnostic.write_text(logs.read(), encoding="utf-8")
                    print(
                        f"Live preview diagnostics: {diagnostic}", file=sys.__stderr__
                    )
                    raise
                finally:
                    for process in [browser_process, *processes]:
                        if process is not None and process.returncode is None:
                            process.terminate()
                            try:
                                await asyncio.wait_for(process.wait(), 10)
                            except TimeoutError:
                                process.kill()
                                await process.wait()
