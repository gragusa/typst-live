# Synchronized Typst preview for Zed

This optional companion for **typst-live** connects Zed task cursor positions to Tinymist's browser preview. Its package and command name are `zed-typst-preview`. It supports:

- **Source to preview:** run a task or press a shortcut to scroll to the current cursor position.
- **Preview to source:** use Tinymist's source-jump gesture on rendered text to open the matching location in Zed.
- Document and slide previews, including source locations in included files.
- Preview updates while typing, including unsaved edits in open chapter files.

Preview content updates while typing. Cursor-to-preview scrolling runs when you invoke the task; Zed's public extension API does not expose continuous cursor movement events.

## Install

Follow the [dev installation guide](../../README.md#1-install-the-tools) for Rust, Zed, Tinymist, and shell setup. From this repository, install the companion with [uv](https://docs.astral.sh/uv/):

```sh
uv tool install --python 3.12 --editable ./tools/preview
uv tool update-shell
```

uv provides Python and the package dependencies. Open a new terminal and ensure `zed-typst-preview`, `tinymist`, and `zed` are on Zed's shell `PATH`. Rebuild the dev extension, focus a `.typ` file, and run **editor: restart language server** after installing the companion. The extension automatically uses the companion's `lsp` command to forward its Tinymist connection and mirror open buffers. The ordinary preview tasks remain available without this companion.

With the dev extension installed, open your document's main file and run **Typst: Synchronized Preview** or **Typst: Synchronized Slide Preview** from **task: spawn**. The main file must exist on disk, but subsequent edits do not need to be saved. The task prints **Live preview connected to Zed's unsaved buffers** when the language-server connection is active. Move your cursor in any file belonging to that document and run **Typst: Sync Preview to Cursor** to scroll the preview.

To upgrade an earlier installation:

```sh
uv tool install --force --python 3.12 --editable ./tools/preview
```

Then rebuild the dev extension, restart Tinymist, and restart the synchronized preview. Without the new language-server proxy, a standalone preview continues to refresh from saved files.

If the preview still updates only after saving, focus a `.typ` file and run **editor: restart language server** from Zed's command palette after rebuilding. Extension reloads can stop the previous server without starting a replacement for an already-open buffer. The preview must print **Live preview connected to Zed's unsaved buffers**. If the connection remains missing, its terminal reports that it is reading saved files and shows the restart instruction.

Add these bindings to Zed's `keymap.json`:

```json
[
  {
    "context": "Editor && language == Typst",
    "bindings": {
      "alt-p": ["task::Spawn", {"task_name": "Typst: Synchronized Preview"}],
      "alt-shift-p": ["task::Spawn", {"task_name": "Typst: Synchronized Slide Preview"}],
      "alt-j": ["task::Spawn", {"task_name": "Typst: Sync Preview to Cursor"}]
    }
  }
]
```

Synchronized preview and cursor-sync tasks do not save buffers. Cursor jumps use the same unsaved text that is sent to the preview. Use **task: spawn** or the shortcut for a fresh cursor position; Zed's default **task: rerun** can reuse a previous task's captured position.

## Multi-file documents and custom settings

The launch file remains the preview's entry point. Editing and syncing from an included chapter does not switch the entry point. Only one synchronized preview can run for a worktree at a time; stop its terminal before switching between document and slide modes. Different worktrees have separate sessions.

For a fixed entry point, define a project task in `.zed/tasks.json`:

```json
[
  {
    "label": "Typst: Synchronized Main Preview",
    "command": "zed-typst-preview",
    "args": ["start", "--root", "$ZED_WORKTREE_ROOT", "$ZED_WORKTREE_ROOT/main.typ"],
    "save": "none",
    "allow_concurrent_runs": true,
    "reveal": "no_focus"
  }
]
```

The companion launches its own Tinymist process. It inherits the terminal environment, including `TYPST_FONT_PATHS`, but does not read `lsp.tinymist.settings`. Pass additional Tinymist compiler arguments after the entry file and `--`:

```sh
zed-typst-preview start --root /path/to/project /path/to/project/main.typ -- --font-path /path/to/fonts --input draft=true
```

Use `--mode slide` for slides, `--tinymist /path/to/tinymist` or `--zed /path/to/zed` for custom executables, and `--no-open` to print the preview URL without opening a browser. All companion options must precede the entry file. Network ports and preview lifecycle options are managed by the companion.

Preview clicks invoke `zed --existing file:row:column` on the machine running the companion. Use a local Zed workspace for this workflow. Opening source in the correct local window from an SSH host or container is not implemented.

## Behavior and failure reporting

The companion holds Tinymist's control WebSocket open and receives preview source locations. Cursor tasks send authenticated requests to a separate localhost socket. Session records live in a private temporary directory, keyed by the canonical worktree path. Stale records are replaced when the previous process is unreachable.

The `lsp` command forwards Zed's LSP frames to Tinymist and Tinymist's replies back to Zed without changing their bytes. It keeps open document text in memory, applies incremental edits using the negotiated UTF-8, UTF-16, or UTF-32 position encoding, and mirrors changed files into the preview through Tinymist's memory-file protocol. Launching a preview after editing replays the current buffers. Closing a buffer removes its override; stopping the language-server proxy detaches its buffers. Unsaved text is not written to the session files or source files.

Zed task positions use one-based rows and UTF-8 byte columns. Tinymist's current preview implementation uses zero-based rows and Unicode character columns. The companion converts both directions, including accented letters, emoji, and combining marks; it rejects out-of-range positions and byte offsets inside a character.

The sync task confirms that a scroll request was sent. Tinymist does not acknowledge a successful source-to-rendered-position match. A position in comments, whitespace, or unrendered code may have no matching preview position. The renderer may also be catching up with the latest edit.

Use Ctrl-C in the task terminal to stop the companion, terminate its Tinymist child, and remove its session record. Startup, protocol, and editor-launch failures appear in the task terminal. If the sync task says a session is unreachable, restart the synchronized preview.

The implementation uses Tinymist's standalone preview control protocol and compatibility log messages. Those interfaces may change between releases; unsupported behavior produces an error. It has been tested against the locally installed Tinymist build using Typst 0.15.1.

## Development and tests

```sh
uv run --project tools/preview --locked python -m unittest discover -s tools/preview/tests -v
```

To run integration tests with a real Tinymist binary, set `TINYMIST_TEST_BINARY` to its executable path before running the same command. Tests run headlessly: they launch document and slide previews, check jumps in both directions from an included chapter, and check process and session cleanup. A live-buffer test launches the LSP proxy, opens unsaved text before preview startup, applies an incremental edit without saving, verifies the resulting rendered pages, and checks that closing the buffer restores its disk content. The Zed invocation is intercepted so tests do not open an editor window.

Protocol and coordinate references:

- [Tinymist editor control messages](https://github.com/Myriad-Dreamin/tinymist/blob/main/crates/typst-preview/src/actor/editor.rs)
- [Tinymist source-location conversion](https://github.com/Myriad-Dreamin/tinymist/blob/main/crates/tinymist/src/tool/preview/compile.rs)
- [Typst line and column semantics](https://github.com/typst/typst/blob/main/crates/typst-syntax/src/lines.rs)
- [Zed task cursor coordinates](https://github.com/zed-industries/zed/blob/main/crates/project/src/task_inventory.rs)
- [LSP document synchronization](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/#textDocument_synchronization)
