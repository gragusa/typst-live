# typst-live

Typst support for Zed with Tinymist completions, diagnostics, formatting, snippets, and browser previews that update while typing. Document and slide previews support cursor synchronization in both directions.

Install this project as a **dev extension**. No Zed registry publication is required. This project builds on [zed-extensions/typst](https://github.com/zed-extensions/typst).

## 1. Install the tools

You need [Zed](https://zed.dev/), Git, Rust managed by rustup, [uv](https://docs.astral.sh/uv/), Tinymist, and the Typst CLI. The preview companion needs Python 3.11 or newer; the command below lets uv provide Python 3.12 and its dependencies.

On macOS with [Homebrew](https://brew.sh/):

```sh
brew install git rustup uv tinymist typst
```

Add this line **at the end of `~/.zshrc`**, then open a new terminal:

```sh
export PATH="$(brew --prefix rustup)/bin:$HOME/.local/bin:/usr/local/bin:$PATH"
```

This puts rustup's compiler ahead of any standalone Homebrew Rust installation. Set up its toolchain:

```sh
rustup toolchain install stable --profile minimal
rustup default stable
rustup target add wasm32-wasip2
```

In Zed's command palette (`Cmd+Shift+P`), run **cli: install cli binary** to install the `zed` command used for preview-to-editor jumps. See [Zed's CLI instructions](https://zed.dev/docs/reference/cli).

On Linux or Windows, install the same tools using their platform installers, make their executables available on `PATH`, and use rustup with the `wasm32-wasip2` target. See [Zed's dev-extension requirements](https://zed.dev/docs/extensions/developing-extensions).

## 2. Install the companion and extension

Clone the repository into a permanent location:

```sh
git clone https://github.com/gragusa/typst.git typst-live
cd typst-live
uv tool install --python 3.12 --editable ./tools/preview
uv tool update-shell
```

If you already have this checkout, use it instead of cloning again. Keep the checkout in place: both the dev extension and the editable companion use its files.

Open a new terminal, fully quit Zed, then launch it with `zed .`. In Zed:

1. Disable or uninstall the original **Typst** extension. If you installed an older dev version of this fork with ID `typst`, remove that dev installation too.
2. Run **zed: install dev extension** from the command palette and select this checkout's root directory, containing `extension.toml`.
3. Wait for the build to finish. The extension is listed as **typst-live**. Zed downloads the grammar compiler automatically.
4. Open your Typst project folder and focus a `.typ` file. Run **editor: restart language server**.

From a Zed terminal, verify that the commands are available:

```sh
cargo --version
rustup target list --installed
tinymist --version
typst --version
zed --version
zed-typst-preview --version
```

The target list must include `wasm32-wasip2`.

## 3. Use live previews and synchronization

**No `settings.json` entries are required to enable synchronization.** The extension automatically connects the companion to Tinymist when `zed-typst-preview` is on Zed's shell `PATH`.

1. Open your document's main `.typ` file and save it once so it exists on disk.
2. Run **task: spawn** and choose **Typst: Synchronized Preview** or **Typst: Synchronized Slide Preview**.
3. In the task terminal, wait for **Live preview connected to Zed's unsaved buffers**. Type in the source: the browser preview updates without saving, including edits to open chapter files.
4. To move the preview to your caret, run **Typst: Sync Preview to Cursor**. This works in both document and slide mode. Use the preview's source-jump gesture on rendered text to open that position in Zed.

Cursor-to-preview scrolling runs on demand. Only one synchronized preview runs per project worktree; stop it with `Ctrl+C` in its task terminal before switching between document and slide modes. This workflow requires a local workspace.

For shortcuts, merge [examples/keymap.json](examples/keymap.json) into your Zed `keymap.json`:

| Shortcut | Task |
| --- | --- |
| `Alt+P` | Synchronized document preview |
| `Alt+Shift+P` | Synchronized slide preview |
| `Alt+J` | Sync preview to cursor |

The ordinary **Typst: Open Preview** and **Typst: Preview Slides** tasks read saved files. Choose the **Synchronized** tasks for updates while typing. PDF, PNG, SVG, watch, and font-listing tasks are also available through **task: spawn**. Type `typ-` for writing snippets.

## Settings and troubleshooting

- **Preview updates only after saving:** focus a `.typ` file and run **editor: restart language server**, then restart the synchronized preview. Check for the live-buffer confirmation above.
- **A command is missing:** check its availability in Zed's terminal, fix your shell `PATH`, fully quit Zed, and relaunch it from a new terminal.
- **Dev extension fails to build:** check that rustup's `cargo` and `rustc` appear first on `PATH` and that `rustup target list --installed` includes `wasm32-wasip2`. Use **zed: open log** for the build error.
- **Custom main file, fonts, or inputs:** preview tasks use a separate Tinymist process and do not inherit `lsp.tinymist.settings`. Use the [project task examples](examples/synchronized-preview.tasks.json) and [companion guide](tools/preview/README.md#multi-file-documents-and-custom-settings).

For optional formatting, PDF export, and compiler configuration, see the [settings guide](docs/SETTINGS.md) and [settings examples](examples/settings.jsonc). The settings keys remain `languages.Typst` and `lsp.tinymist`.

## Updating

From the checkout, after pulling changes:

```sh
uv tool install --force --python 3.12 --editable ./tools/preview
```

Rebuild **typst-live** from Zed's Extensions page, focus a `.typ` file, run **editor: restart language server**, and restart the synchronized preview.

See the [companion guide](tools/preview/README.md) for CLI options and tests, the [roadmap](docs/ROADMAP.md) for remaining features, and [repository setup](docs/REPOSITORY.md) for the planned move to `gragusa/typst-live`.
