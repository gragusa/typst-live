"""Forward LSP traffic unchanged and mirror open buffers into a running preview."""

import asyncio
import contextlib
import json
import os
import secrets
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

MAX_MESSAGE = 32 * 1024 * 1024


def content_length(headers: bytes) -> int:
    lengths = []
    for line in headers.splitlines():
        name, separator, value = line.partition(b":")
        if separator and name.lower() == b"content-length":
            lengths.append(int(value.strip()))
    if len(lengths) != 1 or not 0 <= lengths[0] <= MAX_MESSAGE:
        raise ValueError("Invalid LSP Content-Length")
    return lengths[0]


def read_frame(stream) -> bytes | None:
    headers = bytearray()
    while True:
        line = stream.readline(8193)
        if not line and not headers:
            return None
        if not line or len(headers) + len(line) > 8192:
            raise ValueError("Truncated or oversized LSP headers")
        headers.extend(line)
        if line == b"\r\n":
            break
    remaining = content_length(headers)
    body = bytearray()
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise ValueError("Truncated LSP message body")
        body.extend(chunk)
        remaining -= len(chunk)
    return bytes(headers + body)


async def read_async_frame(stream) -> bytes | None:
    headers = bytearray()
    while True:
        line = await stream.readline()
        if not line and not headers:
            return None
        if not line or len(headers) + len(line) > 8192:
            raise ValueError("Truncated or oversized LSP headers")
        headers.extend(line)
        if line == b"\r\n":
            break
    try:
        body = await stream.readexactly(content_length(headers))
    except asyncio.IncompleteReadError as error:
        raise ValueError("Truncated LSP message body") from error
    return bytes(headers) + body


def frame_message(frame: bytes) -> dict:
    message = json.loads(frame.split(b"\r\n\r\n", 1)[1])
    if not isinstance(message, dict):
        raise TypeError("An LSP message must be a JSON object")
    return message


def position_offset(text: str, position: dict, encoding: str) -> int:
    row, column = position["line"], position["character"]
    if type(row) is not int or type(column) is not int or row < 0 or column < 0:
        raise ValueError("Invalid LSP text position")
    lines = text.split("\n")
    if row >= len(lines):
        raise ValueError("LSP text position is outside the document")
    line = lines[row].removesuffix("\r")
    offset = sum(len(part) + 1 for part in lines[:row])
    units = 0
    for index, character in enumerate(line):
        if units == column:
            return offset + index
        if encoding == "utf-8":
            units += len(character.encode("utf-8"))
        elif encoding == "utf-16":
            units += len(character.encode("utf-16-le")) // 2
        elif encoding == "utf-32":
            units += 1
        else:
            raise ValueError(f"Unsupported LSP position encoding: {encoding}")
        if units > column:
            raise ValueError("LSP text position splits a Unicode character")
    # The LSP specification clips character positions beyond the end of a line.
    return offset + len(line)


class Buffers:
    def __init__(self, root: Path):
        self.root = root
        self.files = {}
        self.versions = {}
        self.encoding = "utf-16"
        self.initialize_id = None
        self.revision = 0
        self.preview_session = None
        # Identifies this proxy to a preview that also receives other proxies' buffers.
        self.client = secrets.token_urlsafe(16)

    def filepath(self, uri: str) -> str | None:
        parsed = urlsplit(uri)
        if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
            return None
        path = Path(url2pathname(parsed.path)).resolve()
        return str(path) if path.is_relative_to(self.root) else None

    def server_message(self, message: dict) -> None:
        if self.initialize_id is not None and message.get("id") == self.initialize_id:
            capabilities = message.get("result", {}).get("capabilities", {})
            encoding = capabilities.get("positionEncoding", "utf-16")
            if encoding not in {"utf-8", "utf-16", "utf-32"}:
                raise ValueError(f"Unsupported LSP position encoding: {encoding}")
            self.encoding = encoding
            self.initialize_id = None

    def client_message(self, message: dict) -> bool:
        method = message.get("method")
        if method == "initialize":
            self.initialize_id = message["id"]
            return False
        if method not in {
            "textDocument/didOpen",
            "textDocument/didChange",
            "textDocument/didClose",
        }:
            return False
        params = message["params"]
        document = params["textDocument"]
        path = self.filepath(document["uri"])
        if path is None:
            return False
        if method == "textDocument/didClose":
            self.files.pop(path, None)
            self.versions.pop(path, None)
        else:
            version = document["version"]
            if type(version) is not int:
                raise ValueError("Invalid LSP document version")
            if method == "textDocument/didOpen":
                text = document["text"]
            else:
                if path not in self.files or version <= self.versions[path]:
                    raise ValueError("LSP document change is unopened or out of order")
                text = self.files[path]
                for change in params["contentChanges"]:
                    if "range" not in change:
                        text = change["text"]
                        continue
                    start = position_offset(
                        text, change["range"]["start"], self.encoding
                    )
                    end = position_offset(text, change["range"]["end"], self.encoding)
                    if start > end:
                        raise ValueError("Reversed LSP edit range")
                    text = text[:start] + change["text"] + text[end:]
            if not isinstance(text, str):
                raise TypeError("LSP document text must be a string")
            self.files[path] = text
            self.versions[path] = version
        self.revision += 1
        return True


async def mirror_buffers(buffers: Buffers, changed: asyncio.Event) -> None:
    from . import PreviewNotReady, read_session, request, session_path

    path = session_path(buffers.root)
    sent = None
    was_connected = False
    while True:
        changed.clear()
        if path.exists():
            session = read_session(path)
            current = (session["token"], buffers.revision)
            if current != sent:
                try:
                    await request(
                        session,
                        {
                            "event": "buffers",
                            "client": buffers.client,
                            "files": dict(buffers.files),
                        },
                    )
                except (ConnectionError, TimeoutError, PreviewNotReady) as error:
                    if was_connected:
                        print(
                            f"Preview buffer connection lost: {error}",
                            file=sys.stderr,
                            flush=True,
                        )
                    was_connected = False
                    sent = None
                else:
                    sent = current
                    was_connected = True
                    buffers.preview_session = session
        else:
            sent = None
            was_connected = False
        try:
            await asyncio.wait_for(changed.wait(), 0.2)
        except TimeoutError:
            pass


def stdin_frames(loop, queue) -> None:
    # A daemon thread avoids leaving asyncio.run waiting on an uninterruptible stdin read
    # if the language server dies while Zed still holds stdin open. It works on Windows too.
    try:
        while True:
            frame = read_frame(sys.stdin.buffer)
            loop.call_soon_threadsafe(queue.put_nowait, frame)
            if frame is None or frame_message(frame).get("method") == "exit":
                return
    except (OSError, ValueError, TypeError) as error:
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(queue.put_nowait, error)


async def proxy(args) -> None:
    from . import worktree_directory

    buffers = Buffers(worktree_directory(args.root))
    changed = asyncio.Event()
    arguments = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
    # Windows stdio must carry the LSP bytes without newline translation.
    if os.name == "nt":
        import msvcrt

        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    process = await asyncio.create_subprocess_exec(
        args.server,
        *arguments,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        # Leave Tinymist diagnostics on stderr, never in the LSP output stream.
        stderr=None,
        limit=MAX_MESSAGE + 8192,
    )
    queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    threading.Thread(target=stdin_frames, args=(loop, queue), daemon=True).start()

    async def to_server():
        while (frame := await queue.get()) is not None:
            if isinstance(frame, Exception):
                raise frame
            message = frame_message(frame)
            if buffers.client_message(message):
                changed.set()
            process.stdin.write(frame)
            await process.stdin.drain()
            if message.get("method") == "exit":
                break
        process.stdin.close()

    async def to_editor():
        while (frame := await read_async_frame(process.stdout)) is not None:
            buffers.server_message(frame_message(frame))
            sys.stdout.buffer.write(frame)
            sys.stdout.buffer.flush()

    tasks = [
        asyncio.create_task(to_server()),
        asyncio.create_task(to_editor()),
        asyncio.create_task(mirror_buffers(buffers, changed)),
    ]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
        if tasks[2] in done:
            raise RuntimeError("Preview buffer forwarding stopped")
        # Drain the final shutdown response after client EOF/exit, or wait for server EOF.
        await asyncio.wait_for(tasks[1], 5)
        code = await asyncio.wait_for(process.wait(), 5)
        if code != 0:
            raise RuntimeError(f"Tinymist exited with code {code}")
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if buffers.preview_session is not None:
            from . import request

            try:
                await request(
                    buffers.preview_session,
                    {"event": "disconnect", "client": buffers.client},
                )
            except (OSError, ValueError, TimeoutError) as error:
                print(
                    f"Could not detach preview buffers: {error}",
                    file=sys.stderr,
                    flush=True,
                )
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
