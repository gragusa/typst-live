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
import subprocess
import sys
import tempfile
import webbrowser
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed


def project_root(worktree: Path, filename: str | Path) -> Path:
    """Choose the Typst root for a source file opened from a Zed worktree.

    The worktree is the root when it contains the file. Otherwise the root is
    the file's directory, Typst's default root. For a file opened from outside
    every project folder, Zed's `ZED_WORKTREE_ROOT` names the first project
    folder, and the file's language server receives the file itself as its
    worktree.
    """
    worktree = worktree_directory(worktree)
    path = Path(filename).resolve()
    return worktree if path.is_relative_to(worktree) else path.parent


def worktree_directory(worktree: Path) -> Path:
    """Return a worktree's directory; a single-file worktree's is the file's parent."""
    worktree = worktree.resolve(strict=True)
    return worktree if worktree.is_dir() else worktree.parent


def source_file(root: Path, filename: str, buffers: dict | None = None) -> Path:
    path = Path(filename).resolve()
    in_memory = buffers is not None and str(path) in buffers
    if not path.is_relative_to(root) or not (in_memory or path.is_file()):
        raise ValueError(f"Source must be a file inside {root}: {path}")
    return path


def typst_source(worktree: Path, filename: str | Path) -> tuple[Path, Path]:
    """Return the Typst root and entry file, reporting a root outside the worktree."""
    worktree = worktree_directory(worktree)
    root = project_root(worktree, filename)
    entry = source_file(root, str(filename))
    if root != worktree:
        print(
            f"{entry} is outside the worktree {worktree}; "
            f"using its directory {root} as the Typst root",
            flush=True,
        )
    return root, entry


TOOLS = {("typst", "compile"), ("typst", "watch"), ("tinymist", "preview")}


def check_tool(command: list[str]) -> None:
    if len(command) < 2 or (Path(command[0]).stem, command[1]) not in TOOLS:
        raise ValueError(
            "run accepts only 'typst compile', 'typst watch', and 'tinymist preview'"
        )
    if any(argument.split("=", 1)[0] == "--root" for argument in command):
        raise ValueError(
            "Pass the worktree to the companion's --root; it chooses the Typst root"
        )


def tool_command(root: Path, command: list[str]) -> list[str]:
    """Insert the Typst root after the subcommand of a Typst or Tinymist command."""
    check_tool(command)
    return [command[0], command[1], "--root", str(root), *command[2:]]


def run_tool(args) -> int:
    tool = args.tool[1:] if args.tool[:1] == ["--"] else args.tool
    check_tool(tool)
    executable = shutil.which(tool[0])
    if executable is None:
        raise ValueError(f"{tool[0]} must be executable; check PATH")
    root, _entry = typst_source(args.root, args.file)
    command = tool_command(root, tool)
    if os.name == "nt":
        return subprocess.run([executable, *command[1:]]).returncode
    # Replacing the process delivers the task terminal's signals to the tool.
    os.execv(executable, command)


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


def editor_id(client) -> str:
    if not isinstance(client, str) or not client:
        raise TypeError("Preview buffer request has no editor ID")
    return client


class PreviewBuffers:
    """Unsaved buffers from every connected editor proxy.

    Zed starts one language server, and so one proxy, per worktree, and gives
    each file opened from outside the project folders its own worktree. One
    preview can therefore receive buffers from several proxies. Tinymist holds
    the union; when two proxies send the same file, the latest update wins.
    """

    def __init__(self, wait_for_compile: bool = False):
        self.files = {}
        self.editors = {}
        self.connected = False
        self.live_connection = asyncio.Event()
        self.lock = asyncio.Lock()
        self.initial_compile = asyncio.Event()
        if not wait_for_compile:
            self.initial_compile.set()

    async def update(self, websocket, root: Path, client: str, files: dict) -> None:
        # A snapshot sent before Tinymist's initial compile can be superseded by
        # that in-flight disk compilation. Replay after its completion status.
        if not self.initial_compile.is_set():
            raise PreviewNotReady("Preview is still compiling its initial document")
        client = editor_id(client)
        async with self.lock:
            await self._update(websocket, root, client, files)

    async def disconnect(self, websocket, client: str) -> None:
        client = editor_id(client)
        async with self.lock:
            if self.editors.pop(client, None) is None:
                return
            await self._mirror(websocket)
            if not self.editors:
                self.connected = False
                self.live_connection.clear()
                print(
                    "Live editor buffers disconnected; preview reads saved files",
                    flush=True,
                )

    async def _update(self, websocket, root: Path, client: str, files: dict) -> None:
        if not isinstance(files, dict) or not all(
            isinstance(path, str) and isinstance(text, str)
            for path, text in files.items()
        ):
            raise TypeError("Invalid preview memory files")
        normalized = {}
        for filename, text in files.items():
            path = Path(filename).resolve()
            if not path.is_relative_to(root):
                raise ValueError("Preview memory file is outside the preview root")
            normalized[str(path)] = text
        # Reinsert so that this editor's text wins for files several editors hold.
        self.editors.pop(client, None)
        self.editors[client] = normalized
        await self._mirror(websocket)

    async def _mirror(self, websocket) -> None:
        union = {}
        for files in self.editors.values():
            union.update(files)
        if not self.connected:
            await websocket.send(json.dumps({"event": "syncMemoryFiles", "files": {}}))
            removed = []
            updates = union
        else:
            removed = list(self.files.keys() - union.keys())
            updates = {
                path: text
                for path, text in union.items()
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
        self.files = union
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


def current_session(path: Path) -> dict | None:
    try:
        return read_session(path)
    except FileNotFoundError:
        return None


async def stop_previous(path: Path, timeout: float = 15) -> None:
    """Stop the worktree's running preview and wait until it removes its record."""
    old = current_session(path)
    if old is None:
        return
    try:
        await request(old, {"event": "stop"})
    except (ConnectionError, TimeoutError):
        # No preview answers for this record. Remove it only if it is still the one inspected.
        if current_session(path) == old:
            path.unlink(missing_ok=True)
        return
    print(
        "Stopping the synchronized preview already running for this Typst root",
        flush=True,
    )
    try:
        async with asyncio.timeout(timeout):
            while current_session(path) == old:
                await asyncio.sleep(0.05)
    except TimeoutError:
        raise ValueError(
            "The running synchronized preview did not stop. Close its task terminal and retry."
        ) from None


async def sync(args) -> None:
    root = project_root(args.root, args.file)
    path = session_path(root)
    if not path.exists():
        raise ValueError(
            f"No synchronized preview for {root}. Run 'Typst: Synchronized Preview' first."
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


async def open_in_zed(root: Path, message: dict, zed: str, buffers: dict) -> None:
    """Open a preview click's source location in Zed.

    A click that cannot be opened is reported and skipped; the preview keeps
    running. Such clicks include content generated by package code outside the
    worktree, positions from a render that is behind the buffers, and a failed
    `zed` call.
    """
    try:
        location = zed_location(root, message, buffers)
        process = await asyncio.create_subprocess_exec(zed, "--existing", location)
        if await process.wait() != 0:
            raise RuntimeError(f"Zed could not open {location}")
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Preview click not opened in Zed: {error}", file=sys.stderr, flush=True)


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
            await open_in_zed(root, message, zed, buffers.files)
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
    stop: asyncio.Event | None = None,
) -> None:
    """Answer one request; a `stop` request sets `stop` for the running preview."""
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
                await buffers.update(
                    websocket, root, payload.get("client"), payload["files"]
                )
            elif payload.get("event") == "disconnect":
                await buffers.disconnect(websocket, payload.get("client"))
            elif payload.get("event") == "stop" and stop is not None:
                stop.set()
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
        # A closed Tinymist connection means this preview is exiting. The editor
        # proxy retries, then follows the session record to the next preview.
        if isinstance(error, (PreviewNotReady, ConnectionClosed)):
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
    root, entry = typst_source(args.root, args.file)
    tinymist = shutil.which(args.tinymist)
    zed = shutil.which(args.zed)
    if not tinymist or not zed:
        raise ValueError(
            "Both tinymist and zed must be executable; check PATH or --tinymist/--zed"
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
    # Validate this invocation before replacing a working preview.
    path = session_path(root)
    await stop_previous(path)
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
            stop = asyncio.Event()
            server = await asyncio.start_server(
                lambda reader, writer: serve_request(
                    reader, writer, websocket, root, token, buffers, stop
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
                replaced = asyncio.create_task(stop.wait())
                tasks.extend([messages, child, replaced])
                try:
                    done, _ = await asyncio.wait(
                        tasks, return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in done:
                        task.result()
                    if replaced in done:
                        print(
                            "Preview stopped: a new synchronized preview replaced it",
                            flush=True,
                        )
                        return
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
        "start",
        help="Start a preview that supports jumps to and from Zed, "
        "replacing the worktree's running preview",
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
    tool = commands.add_parser(
        "run",
        help="Run 'typst compile', 'typst watch', or 'tinymist preview' with "
        "the Typst root chosen for a file in a Zed worktree",
    )
    tool.add_argument("--root", type=Path, required=True)
    tool.add_argument("--file", type=Path, required=True)
    tool.add_argument(
        "tool", nargs=argparse.REMAINDER, help="The command to run, after --"
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
    # Closing a terminal sends SIGHUP. Cancelling instead of exiting immediately
    # lets the preview stop Tinymist and remove its session record.
    numbers = [signal.SIGTERM]
    if hasattr(signal, "SIGHUP"):
        numbers.append(signal.SIGHUP)
    previous = {
        number: signal.signal(number, lambda *_: loop.call_soon_threadsafe(task.cancel))
        for number in numbers
    }
    try:
        if args.command == "lsp":
            from .lsp import proxy

            await proxy(args)
        else:
            await (start(args) if args.command == "start" else sync(args))
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "run":
            return run_tool(args)
        asyncio.run(run(args))
    except (KeyboardInterrupt, asyncio.CancelledError):
        return 130
    except (OSError, ValueError, RuntimeError, TimeoutError, ConnectionClosed) as error:
        print(f"zed-typst-preview: {error}", file=sys.stderr)
        return 1
    return 0
