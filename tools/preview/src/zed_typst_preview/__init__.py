"""Connect Zed's task cursor locations to Tinymist's preview control protocol."""

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import stat
import sys
import tempfile
import webbrowser
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed


def source_file(root: Path, filename: str, buffers: dict | None = None) -> Path:
    path = Path(filename).resolve()
    in_memory = buffers is not None and str(path) in buffers
    if not path.is_relative_to(root) or not (in_memory or path.is_file()):
        raise ValueError(f"Source must be a file inside {root}: {path}")
    return path


def source_line(path: Path, row: int, buffers: dict | None = None) -> str:
    text = buffers.get(str(path)) if buffers is not None else None
    if text is None:
        text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    if type(row) is not int or not 0 <= row < len(lines):
        raise ValueError(f"Row outside the saved source: {row}")
    return lines[row]


def scroll_request(
    root: Path, filename: str, row: int, column: int, buffers: dict | None = None
) -> dict:
    """Convert one-based Zed byte columns to zero-based Typst character columns."""
    if type(row) is not int or type(column) is not int or row < 1 or column < 1:
        raise ValueError("Zed row and column must be positive integers")
    path = source_file(root, filename, buffers)
    line = source_line(path, row - 1, buffers).encode("utf-8")
    if column - 1 > len(line):
        raise ValueError(
            "Column outside the saved source; save the file before syncing"
        )
    # Strict decoding rejects a byte column inside a multibyte character.
    character = len(line[: column - 1].decode("utf-8"))
    return {
        "event": "panelScrollTo",
        "filepath": str(path),
        "line": row - 1,
        "character": character,
    }


def zed_location(root: Path, message: dict, buffers: dict | None = None) -> str:
    """Convert Tinymist's zero-based character coordinates to Zed CLI coordinates."""
    path = source_file(root, message["filepath"], buffers)
    start = message.get("start")
    if not isinstance(start, list) or len(start) != 2:
        raise ValueError("Preview click has no source position")
    row, character = start
    if type(character) is not int or character < 0:
        raise ValueError("Invalid preview source column")
    line = source_line(path, row, buffers)
    if character > len(line):
        raise ValueError(
            "Preview location is outside the saved source; preview may be stale"
        )
    column = len(line[:character].encode("utf-8")) + 1
    return f"{path}:{row + 1}:{column}"


def session_path(root: Path) -> Path:
    """Keep session credentials in a private, per-user temporary directory."""
    user = hashlib.sha256(os.fsencode(Path.home())).hexdigest()[:16]
    directory = Path(
        os.environ.get(
            "ZED_TYPST_PREVIEW_RUNTIME_DIR",
            str(Path(tempfile.gettempdir()) / f"zed-typst-preview-{user}"),
        )
    )
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError(
            f"Preview runtime directory must not be a symlink: {directory}"
        )
    if os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise ValueError(
            f"Preview runtime directory must be owned by you with mode 700: {directory}"
        )
    key = hashlib.sha256(os.fsencode(root)).hexdigest()
    return directory / f"{key}.json"


def read_session(path: Path) -> dict:
    session = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(session, dict)
        or type(session.get("port")) is not int
        or not 0 < session["port"] < 65536
        or not isinstance(session.get("token"), str)
        or len(session["token"]) < 32
    ):
        raise ValueError(f"Invalid preview session file: {path}")
    return session


def publish_session(path: Path, session: dict) -> None:
    # Publish a complete record atomically; the LSP proxy may discover it immediately.
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".session-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as record:
            json.dump(session, record)
        os.link(temporary, path)
    finally:
        os.unlink(temporary)


class PreviewNotReady(ValueError):
    """The preview is running but its initial compilation is still in progress."""


class PreviewBuffers:
    def __init__(self, wait_for_compile: bool = False):
        self.files = {}
        self.connected = False
        self.live_connection = asyncio.Event()
        self.lock = asyncio.Lock()
        self.initial_compile = asyncio.Event()
        if not wait_for_compile:
            self.initial_compile.set()

    async def update(self, websocket, root: Path, files: dict) -> None:
        # A snapshot sent before Tinymist's initial compile can be superseded by
        # that in-flight disk compilation. Replay after its completion status.
        if not self.initial_compile.is_set():
            raise PreviewNotReady("Preview is still compiling its initial document")
        async with self.lock:
            await self._update(websocket, root, files)

    async def _update(self, websocket, root: Path, files: dict) -> None:
        if not isinstance(files, dict) or not all(
            isinstance(path, str) and isinstance(text, str)
            for path, text in files.items()
        ):
            raise TypeError("Invalid preview memory files")
        normalized = {}
        for filename, text in files.items():
            path = Path(filename).resolve()
            if not path.is_relative_to(root):
                raise ValueError("Preview memory file is outside the worktree")
            normalized[str(path)] = text
        if not self.connected:
            await websocket.send(json.dumps({"event": "syncMemoryFiles", "files": {}}))
            removed = []
            updates = normalized
        else:
            removed = list(self.files.keys() - normalized.keys())
            updates = {
                path: text
                for path, text in normalized.items()
                if self.files.get(path) != text
            }
        # Keep per-file invalidation events separate when replaying a mixture
        # of saved and unsaved buffers into Tinymist's compiler.
        for path in removed:
            await websocket.send(
                json.dumps({"event": "removeMemoryFiles", "files": [path]})
            )
        for path, text in updates.items():
            await websocket.send(
                json.dumps({"event": "updateMemoryFiles", "files": {path: text}})
            )
        self.files = normalized
        if not self.connected:
            print("Live preview connected to Zed's unsaved buffers", flush=True)
        self.connected = True
        self.live_connection.set()


async def request(session: dict, payload: dict) -> dict:
    async with asyncio.timeout(5):
        reader, writer = await asyncio.open_connection("127.0.0.1", session["port"])
        try:
            writer.write(
                (json.dumps({**payload, "token": session["token"]}) + "\n").encode()
            )
            await writer.drain()
            response = json.loads(await reader.readline())
            if response.get("ok") is not True:
                if response.get("retry") is True:
                    raise PreviewNotReady(response["error"])
                raise ValueError(response.get("error", "Preview rejected the request"))
            return response
        finally:
            writer.close()
            await writer.wait_closed()


async def sync(args) -> None:
    root = args.root.resolve(strict=True)
    path = session_path(root)
    if not path.exists():
        raise ValueError(
            "No synchronized preview for this worktree. Run 'Typst: Synchronized Preview' first."
        )
    try:
        await request(
            read_session(path),
            {
                "event": "sync",
                "filepath": str(args.file.resolve()),
                "row": args.row,
                "column": args.column,
            },
        )
    except (ConnectionError, TimeoutError) as error:
        raise ValueError(
            "Preview session is no longer reachable. Restart 'Typst: Synchronized Preview'."
        ) from error
    print(f"Preview scroll requested at {args.file}:{args.row}:{args.column}")


async def editor_messages(
    websocket, root: Path, zed: str, buffers: PreviewBuffers | None = None
) -> None:
    buffers = buffers if buffers is not None else PreviewBuffers()
    async for raw in websocket:
        message = json.loads(raw)
        event = message.get("event")
        if event == "syncEditorChanges":
            async with buffers.lock:
                await websocket.send(
                    json.dumps({"event": "syncMemoryFiles", "files": {}})
                )
                for path, text in buffers.files.items():
                    await websocket.send(
                        json.dumps(
                            {"event": "updateMemoryFiles", "files": {path: text}}
                        )
                    )
        elif event == "editorScrollTo":
            location = zed_location(root, message, buffers.files)
            process = await asyncio.create_subprocess_exec(zed, "--existing", location)
            if await process.wait() != 0:
                raise RuntimeError("Zed could not open the preview's source location")
        elif event == "compileStatus":
            if message.get("kind") in {"CompileSuccess", "CompileError"}:
                buffers.initial_compile.set()
        elif event not in {"compileStatus", "outline", "viewerWindowState"}:
            raise ValueError(f"Unexpected Tinymist preview event: {event!r}")
    raise RuntimeError("Tinymist closed the preview control connection")


async def serve_request(
    reader,
    writer,
    websocket,
    root: Path,
    token: str,
    buffers: PreviewBuffers | None = None,
) -> None:
    buffers = buffers if buffers is not None else PreviewBuffers()
    try:
        async with asyncio.timeout(5):
            payload = json.loads(await reader.readline())
            if not isinstance(payload, dict) or not isinstance(
                payload.get("token"), str
            ):
                raise TypeError("Invalid preview request")
            if not secrets.compare_digest(payload["token"], token):
                raise ValueError("Invalid preview session token")
            if payload.get("event") == "sync":
                async with buffers.lock:
                    message = scroll_request(
                        root,
                        payload["filepath"],
                        payload["row"],
                        payload["column"],
                        buffers.files,
                    )
                    await websocket.send(json.dumps(message))
            elif payload.get("event") == "buffers":
                await buffers.update(websocket, root, payload["files"])
            elif payload.get("event") == "disconnect":
                async with buffers.lock:
                    await buffers._update(websocket, root, {})
                    buffers.connected = False
                    buffers.live_connection.clear()
                print(
                    "Live editor buffers disconnected; preview reads saved files",
                    flush=True,
                )
            elif payload.get("event") != "status":
                raise ValueError("Unknown preview request")
            response = {
                "ok": True,
                "buffer_count": len(buffers.files),
                "live": buffers.connected,
            }
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        TimeoutError,
        ConnectionClosed,
    ) as error:
        response = {"ok": False, "error": str(error)}
        if isinstance(error, PreviewNotReady):
            response["retry"] = True
    try:
        writer.write((json.dumps(response) + "\n").encode())
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


async def preview_addresses(process, ready) -> None:
    addresses = {}
    while raw := await process.stderr.readline():
        line = raw.decode("utf-8", errors="replace")
        print(line, end="", file=sys.stderr, flush=True)
        match = re.search(
            r"(Control panel|Data plane) server listening on: (127\.0\.0\.1:\d+)", line
        )
        if match:
            addresses[match[1]] = match[2]
            if len(addresses) == 2 and not ready.done():
                ready.set_result(addresses)
    if not ready.done():
        ready.set_exception(
            RuntimeError("Tinymist exited before its preview servers were ready")
        )


async def stop_process(process) -> None:
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 5)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()


async def report_missing_live_connection(
    buffers: PreviewBuffers, timeout: float = 3
) -> None:
    await buffers.initial_compile.wait()
    if buffers.live_connection.is_set():
        return
    try:
        await asyncio.wait_for(buffers.live_connection.wait(), timeout)
    except TimeoutError:
        print(
            "Preview is reading saved files: Zed's live buffer connection is missing. "
            "Focus a .typ file and run 'editor: restart language server' in Zed. "
            "Wait for 'Live preview connected to Zed's unsaved buffers' before testing typing updates.",
            file=sys.stderr,
            flush=True,
        )


async def start(args) -> None:
    root = args.root.resolve(strict=True)
    entry = source_file(root, str(args.file))
    tinymist = shutil.which(args.tinymist)
    zed = shutil.which(args.zed)
    if not tinymist or not zed:
        raise ValueError(
            "Both tinymist and zed must be executable; check PATH or --tinymist/--zed"
        )
    path = session_path(root)
    if path.exists():
        old = read_session(path)
        try:
            await request(old, {"event": "status"})
        except (ConnectionError, TimeoutError):
            # Remove only the stale record we inspected.
            if read_session(path) == old:
                path.unlink()
        else:
            raise ValueError(
                "A synchronized preview already runs for this worktree. Stop its task terminal first."
            )

    extra = args.extra[1:] if args.extra[:1] == ["--"] else args.extra
    reserved = {
        "--open",
        "--no-open",
        "--host",
        "--data-plane-host",
        "--control-plane-host",
        "--static-file-host",
        "--root",
        "--preview-mode",
        "--refresh-style",
    }
    if any(argument.split("=", 1)[0] in reserved for argument in extra):
        raise ValueError(
            "Use the companion's --root/--mode options; preview network options are managed automatically"
        )
    process = await asyncio.create_subprocess_exec(
        tinymist,
        "preview",
        "--no-open",
        "--verbose",
        "--data-plane-host",
        "127.0.0.1:0",
        "--control-plane-host",
        "127.0.0.1:0",
        "--preview-mode",
        args.mode,
        "--refresh-style",
        "on-type",
        "--root",
        str(root),
        str(entry),
        *extra,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    ready = asyncio.get_running_loop().create_future()
    logs = asyncio.create_task(preview_addresses(process, ready))
    token = secrets.token_urlsafe(32)
    tasks = [logs]
    connection_report = None
    published = False
    try:
        addresses = await asyncio.wait_for(asyncio.shield(ready), timeout=15)
        control = addresses["Control panel"]
        async with connect(
            f"ws://{control}/",
            origin=f"http://{control}",
            proxy=None,
            ping_interval=None,
            close_timeout=1,
            max_size=8 * 1024 * 1024,
        ) as websocket:
            buffers = PreviewBuffers(wait_for_compile=True)
            server = await asyncio.start_server(
                lambda reader, writer: serve_request(
                    reader, writer, websocket, root, token, buffers
                ),
                "127.0.0.1",
                0,
                limit=32 * 1024 * 1024,
            )
            async with server:
                session = {"port": server.sockets[0].getsockname()[1], "token": token}
                # Exclusive creation prevents two previews from claiming the same worktree.
                publish_session(path, session)
                published = True
                url = f"http://{addresses['Data plane']}/"
                print(f"Synchronized preview ready: {url}", flush=True)
                if not args.no_open and not webbrowser.open(url):
                    raise RuntimeError(
                        "Could not open the browser. Rerun with --no-open and open the printed URL manually."
                    )
                messages = asyncio.create_task(
                    editor_messages(websocket, root, zed, buffers)
                )
                connection_report = asyncio.create_task(
                    report_missing_live_connection(buffers)
                )
                child = asyncio.create_task(process.wait())
                tasks.extend([messages, child])
                try:
                    done, _ = await asyncio.wait(
                        tasks, return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in done:
                        task.result()
                    raise RuntimeError(
                        f"Tinymist preview stopped (exit code {process.returncode})"
                    )
                finally:
                    # Stop the child before closing its control socket. Otherwise the
                    # HTTP server's graceful shutdown can delay terminal cancellation.
                    await stop_process(process)
    finally:
        try:
            if published and path.exists() and read_session(path).get("token") == token:
                path.unlink()
        except (OSError, ValueError) as error:
            print(f"Could not clean up the preview session: {error}", file=sys.stderr)
        for task in tasks:
            task.cancel()
        if connection_report is not None:
            connection_report.cancel()
            await asyncio.gather(connection_report, return_exceptions=True)
        await asyncio.gather(*tasks, return_exceptions=True)
        await stop_process(process)
        if ready.done() and not ready.cancelled():
            ready.exception()  # Retrieve startup errors even when cancellation raced with EOF.


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--version", action="version", version="zed-typst-preview 0.2.0"
    )
    commands = result.add_subparsers(dest="command", required=True)
    launch = commands.add_parser(
        "start", help="Start a preview that supports jumps to and from Zed"
    )
    launch.add_argument("--root", type=Path, required=True)
    launch.add_argument("--mode", choices=["document", "slide"], default="document")
    launch.add_argument("--tinymist", default="tinymist")
    launch.add_argument("--zed", default="zed")
    launch.add_argument("--no-open", action="store_true")
    launch.add_argument("file", type=Path)
    launch.add_argument(
        "extra", nargs=argparse.REMAINDER, help="Additional Tinymist arguments after --"
    )
    jump = commands.add_parser(
        "sync", help="Scroll the worktree's preview to a source position"
    )
    jump.add_argument("--root", type=Path, required=True)
    jump.add_argument("--file", type=Path, required=True)
    jump.add_argument("--row", type=int, required=True, help="One-based ZED_ROW")
    jump.add_argument(
        "--column", type=int, required=True, help="One-based UTF-8 byte ZED_COLUMN"
    )
    language_server = commands.add_parser(
        "lsp", help="Forward Tinymist LSP traffic and mirror unsaved edits"
    )
    language_server.add_argument("--root", type=Path, required=True)
    language_server.add_argument("--server", required=True)
    language_server.add_argument("arguments", nargs=argparse.REMAINDER)
    return result


async def run(args) -> None:
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    previous = signal.signal(
        signal.SIGTERM, lambda *_: loop.call_soon_threadsafe(task.cancel)
    )
    try:
        if args.command == "lsp":
            from .lsp import proxy

            await proxy(args)
        else:
            await (start(args) if args.command == "start" else sync(args))
    finally:
        signal.signal(signal.SIGTERM, previous)


def main() -> int:
    try:
        asyncio.run(run(parser().parse_args()))
    except (KeyboardInterrupt, asyncio.CancelledError):
        return 130
    except (OSError, ValueError, RuntimeError, TimeoutError, ConnectionClosed) as error:
        print(f"zed-typst-preview: {error}", file=sys.stderr)
        return 1
    return 0
