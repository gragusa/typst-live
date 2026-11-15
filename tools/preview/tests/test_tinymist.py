"""Run with TINYMIST_TEST_BINARY=/path/to/tinymist to exercise the real preview."""

import asyncio
import contextlib
import io
import json
import os
import re
import signal
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import zed_typst_preview as preview
from websockets.asyncio.client import connect


async def launch(root, entry, environment, logs, mode="document"):
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "zed_typst_preview",
        "start",
        "--root",
        str(root),
        "--no-open",
        "--mode",
        mode,
        "--tinymist",
        os.environ["TINYMIST_TEST_BINARY"],
        "--zed",
        sys.executable,
        str(entry),
        stdout=logs,
        stderr=logs,
        env=environment,
        # A Zed task runs as a session leader with Tinymist in its process group.
        start_new_session=os.name != "nt",
    )


def output(logs):
    logs.seek(0)
    return logs.read()


async def assert_closed(port):
    with unittest.TestCase().assertRaises(OSError):
        _reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.close()
        await writer.wait_closed()


@unittest.skipUnless(
    os.environ.get("TINYMIST_TEST_BINARY"),
    "set TINYMIST_TEST_BINARY for the real preview test",
)
class Tinymist(unittest.IsolatedAsyncioTestCase):
    async def ready_port(self, process, logs):
        """Return the data-plane port once the companion reports it."""
        async with asyncio.timeout(20):
            while True:
                match = re.search(
                    r"Synchronized preview ready: http://127\.0\.0\.1:(\d+)/",
                    output(logs),
                )
                if match:
                    return int(match[1])
                if process.returncode is not None:
                    self.fail(output(logs))
                await asyncio.sleep(0.02)

    @unittest.skipIf(
        os.name == "nt", "Windows cannot deliver SIGTERM or SIGHUP to a child process"
    )
    async def test_cli_termination_cleans_up_session_and_server(self):
        for number in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=number.name):
                await self.check_termination(number)

    async def check_termination(self, number):
        with tempfile.TemporaryDirectory(prefix="zed typst lifecycle ") as directory:
            root = Path(directory).resolve()
            entry = root / "main.typ"
            entry.write_text("Lifecycle check.", encoding="utf-8")
            runtime = root / "runtime"
            environment = {**os.environ, "ZED_TYPST_PREVIEW_RUNTIME_DIR": str(runtime)}
            with tempfile.TemporaryFile(mode="w+") as logs:
                process = await launch(root, entry, environment, logs)
                try:
                    port = await self.ready_port(process, logs)
                    process.send_signal(number)
                    await asyncio.wait_for(process.wait(), 10)
                    self.assertEqual(list(runtime.glob("*.json")), [])
                    await assert_closed(port)
                finally:
                    if process.returncode is None:
                        process.kill()
                        await process.wait()

    @unittest.skipIf(os.name == "nt", "Windows cannot deliver SIGTERM to a child process")
    async def test_starting_a_preview_replaces_the_running_one(self):
        # Closing a task terminal makes Zed send SIGTERM and, 100 ms later,
        # SIGKILL. A preview killed during cleanup leaves its record behind.
        for closed in (False, True):
            with self.subTest(closed_by_zed=closed):
                await self.check_replacement(closed)

    async def check_replacement(self, closed):
        with tempfile.TemporaryDirectory(prefix="zed typst replacement ") as directory:
            root = Path(directory).resolve()
            entry = root / "main.typ"
            entry.write_text("Replacement check.", encoding="utf-8")
            runtime = root / "runtime"
            environment = {**os.environ, "ZED_TYPST_PREVIEW_RUNTIME_DIR": str(runtime)}
            with (
                tempfile.TemporaryFile(mode="w+") as first_logs,
                tempfile.TemporaryFile(mode="w+") as second_logs,
            ):
                first = await launch(root, entry, environment, first_logs)
                processes = [first]
                try:
                    first_port = await self.ready_port(first, first_logs)
                    [record] = runtime.glob("*.json")
                    old = preview.read_session(record)
                    if closed:
                        os.killpg(first.pid, signal.SIGTERM)
                        await asyncio.sleep(0.1)
                        if first.returncode is None:
                            first.kill()
                    second = await launch(
                        root, entry, environment, second_logs, mode="slide"
                    )
                    processes.append(second)
                    second_port = await self.ready_port(second, second_logs)
                    code = await asyncio.wait_for(first.wait(), 10)
                    if closed:
                        self.assertIn(code, (130, -signal.SIGKILL), output(first_logs))
                    else:
                        self.assertEqual(code, 0, output(first_logs))
                        self.assertIn("replaced", output(first_logs))
                        self.assertIn("Stopping the synchronized preview", output(second_logs))
                    await assert_closed(first_port)
                    await assert_closed(old["port"])
                    [record] = runtime.glob("*.json")
                    new = preview.read_session(record)
                    self.assertNotEqual(new["token"], old["token"])
                    await preview.request(new, {"event": "status"})
                    second.terminate()
                    await asyncio.wait_for(second.wait(), 10)
                    self.assertEqual(list(runtime.glob("*.json")), [])
                    await assert_closed(second_port)
                finally:
                    for process in processes:
                        # Kill the group, or a failed check leaves Tinymist running.
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(process.pid, signal.SIGKILL)
                        await process.wait()

    async def test_jumps_in_both_directions_and_process_cleanup(self):
        for mode in ["document", "slide"]:
            with self.subTest(mode=mode):
                await self.check_mode(mode)

    async def check_mode(self, mode):
        with tempfile.TemporaryDirectory(prefix="zed typst real preview ") as directory:
            root = Path(directory).resolve()
            entry = root / "main with spaces.typ"
            chapter = root / "chapter with spaces.typ"
            chapter.write_text(
                "= Synchronization\nCafé α target text.", encoding="utf-8"
            )
            entry.write_text(
                '#include "chapter with spaces.typ"\n#pagebreak()\nSecond page.',
                encoding="utf-8",
            )
            args = preview.parser().parse_args(
                [
                    "start",
                    "--root",
                    str(root),
                    "--no-open",
                    "--mode",
                    mode,
                    "--tinymist",
                    os.environ["TINYMIST_TEST_BINARY"],
                    "--zed",
                    sys.executable,
                    str(entry),
                ]
            )
            output, logs = io.StringIO(), io.StringIO()
            opened = asyncio.Queue()
            children = []
            real_spawn = asyncio.create_subprocess_exec

            async def spawn(*arguments, **kwargs):
                if arguments[:2] == (sys.executable, "--existing"):
                    await opened.put(arguments)
                    child = AsyncMock()
                    child.wait.return_value = 0
                    return child
                child = await real_spawn(*arguments, **kwargs)
                children.append(child)
                return child

            with (
                patch.dict(
                    os.environ, {"ZED_TYPST_PREVIEW_RUNTIME_DIR": str(root / "runtime")}
                ),
                patch.object(
                    preview.asyncio, "create_subprocess_exec", side_effect=spawn
                ),
                contextlib.redirect_stdout(output),
                contextlib.redirect_stderr(logs),
            ):
                task = asyncio.create_task(preview.start(args))
                try:
                    async with asyncio.timeout(20):
                        while "Synchronized preview ready: " not in output.getvalue():
                            if task.done():
                                task.result()
                            await asyncio.sleep(0.02)
                    url = (
                        output.getvalue()
                        .split("Synchronized preview ready: ")[1]
                        .splitlines()[0]
                    )
                    async with connect(
                        url.replace("http://", "ws://"),
                        origin=url.rstrip("/"),
                        proxy=None,
                        ping_interval=None,
                    ) as browser:
                        await browser.send("current")
                        async with asyncio.timeout(15):
                            while True:
                                frame = await browser.recv()
                                if isinstance(frame, bytes) and frame.startswith(
                                    (b"new,", b"diff-v1,")
                                ):
                                    break
                        await preview.request(
                            preview.read_session(preview.session_path(root)),
                            {
                                "event": "sync",
                                "filepath": str(chapter),
                                "row": 2,
                                "column": len("Café α ".encode()) + 1,
                            },
                        )
                        async with asyncio.timeout(10):
                            while True:
                                frame = await browser.recv()
                                if isinstance(frame, bytes) and frame.startswith(
                                    b"jump,"
                                ):
                                    positions = frame.decode().split(",")[1:]
                                    if positions and positions[0]:
                                        break
                        page, x, y = positions[0].split()
                        self.assertEqual(page, "1")
                        # Tinymist receives this same message for a browser click.
                        await browser.send(
                            "src-point "
                            + json.dumps(
                                {"page_no": int(page), "x": float(x), "y": float(y)},
                                separators=(",", ":"),
                            )
                        )
                        arguments = await asyncio.wait_for(opened.get(), 10)
                        self.assertEqual(arguments[:2], (sys.executable, "--existing"))
                        self.assertTrue(
                            arguments[2].startswith(f"{chapter}:2:"), arguments
                        )
                except BaseException:
                    print(logs.getvalue(), file=sys.__stderr__)
                    raise
                finally:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                self.assertFalse(preview.session_path(root).exists())
                self.assertTrue(children)
                self.assertTrue(all(child.returncode is not None for child in children))


if __name__ == "__main__":
    unittest.main()
