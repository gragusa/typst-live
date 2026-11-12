# Settings for typst-live

No `settings.json` entries are required for the installed extension, synchronized document previews, synchronized slide previews, or cursor-sync tasks. Tinymist starts automatically. The companion is used automatically when `zed-typst-preview` is on Zed's shell `PATH`.

The examples below are optional preferences. Merge their objects into your existing configuration. **typst-live** is the extension's ID and display name; configuration continues to use the language name `Typst` and server name `tinymist`.

## Where configuration belongs

| Location | Scope | Examples |
| --- | --- | --- |
| User `settings.json` | Preferences shared by your projects | Wrapping, indentation, formatting, inlay hints. |
| Project `.zed/settings.json` | Settings specific to a document or repository | Main file, root directory, compiler inputs, fonts, PDF export. |
| `languages.Typst` inside either settings file | Zed's editor behavior for Typst | `format_on_save`, `soft_wrap`, `inlay_hints`. |
| `lsp.tinymist.settings` inside either settings file | Tinymist's language service and its own exports | Formatter options, `exportPdf`, `rootPath`, `fontPaths`. |
| `lsp.tinymist.initialization_options` | Server options that should be present at initialization | Optional LSP background preview. |
| Project `.zed/tasks.json` | Arguments for separate preview and export processes | Fixed entry point, slide mode, preview fonts and inputs. |
| User `keymap.json` | Keyboard shortcuts | Launch preview or scroll it to the cursor. |

Open user settings with **zed: open settings file** and project settings with **zed: open project settings**. On macOS the usual user file is `~/.config/zed/settings.json`. Zed accepts JSON comments. Project settings override user preferences for that project; keep document paths and compiler inputs in the project file. See [Zed's configuration guide](https://zed.dev/docs/configuring-zed).

## A useful starting configuration

Copy or merge [examples/settings.jsonc](../examples/settings.jsonc):

```jsonc
{
  "languages": {
    "Typst": {
      "formatter": "language_server",
      "format_on_save": "on",
      "soft_wrap": "editor_width",
      "tab_size": 2,
      "inlay_hints": {"enabled": true}
    }
  },
  "lsp": {
    "tinymist": {
      "settings": {
        "formatterMode": "typstyle",
        "formatterIndentSize": 2,
        "formatterPrintWidth": 100,
        "formatterProseWrap": false,
        "exportPdf": "onSave",
        "outputPath": "$dir/$name"
      }
    }
  }
}
```

This formats on save, uses two-space indentation, wraps long lines visually, enables inlay hints, and writes a PDF beside Tinymist's selected main file when you save. `formatterProseWrap: false` preserves existing prose line breaks; the editor's visual wrapping does not insert line breaks into the source. The width of 100 is a suggested formatter preference, not a requirement.

`exportPdf` controls writing PDFs to disk. It does not control the synchronized browser preview's refresh rate. Live preview does not require autosave, `exportPdf: "onType"`, or background preview.

## Editor options: `languages.Typst`

| Setting | Values and effect |
| --- | --- |
| `formatter` | `"language_server"` explicitly selects the LSP formatter. `"auto"` lets Zed choose. |
| `format_on_save` | `"on"` formats when saving; `"off"` leaves formatting to explicit commands. |
| `soft_wrap` | `"editor_width"` wraps to the editor; `"none"` keeps long lines on one visual line; `"bounded"` also respects `preferred_line_length`. |
| `preferred_line_length` | Width for bounded wrapping and Zed's rewrap command. This is separate from Tinymist's `formatterPrintWidth`. |
| `tab_size` | Spaces per indentation level; the extension's language configuration uses 2. |
| `hard_tabs` | `false` inserts spaces. |
| `inlay_hints.enabled` | `true` displays hints supplied by Tinymist. Availability and hint content depend on the server. |
| `semantic_tokens` | `"combined"` adds LSP semantic highlighting to Tree-sitter; `"off"` uses syntax highlighting without semantic tokens. Restart the server after changing this. |
| `enable_language_server` | Keep `true` for language intelligence and unsaved-buffer forwarding. |
| `language_servers` | Normally unnecessary. `["tinymist", "..."]` explicitly includes Tinymist while retaining other configured servers. |
| `show_completions_on_input` | Controls whether completion suggestions appear as you type. |

For example, to add optional semantic highlighting, merge `"semantic_tokens": "combined"` into `languages.Typst`. Tinymist's corresponding server option is `"semanticTokens": "enable"`. See [Zed's language configuration guide](https://zed.dev/docs/configuring-languages) and [default settings](https://github.com/zed-industries/zed/blob/main/assets/settings/default.json).

## Tinymist options: `lsp.tinymist.settings`

The server options below are described in [Tinymist's v0.15.12 configuration reference](https://github.com/Myriad-Dreamin/tinymist/blob/v0.15.12/editors/neovim/Configuration.md). Use the JSON option names, without a `tinymist.` prefix, inside Zed's `settings` object. The Neovim setup syntax surrounding examples in that reference does not apply to Zed.

| Setting | Values and purpose |
| --- | --- |
| `formatterMode` | `"typstyle"`, `"typstfmt"`, or `"disable"`. Current Tinymist defaults to typstyle. |
| `formatterIndentSize` | Formatter indentation width; default 2. Keep it consistent with Zed's `tab_size`. |
| `formatterPrintWidth` | Formatter's preferred line width; default 120. |
| `formatterProseWrap` | `false` preserves prose breaks; `true` reflows prose. The referenced server version also supports `"none"`, `"fill"`, and `"sentence"`. |
| `exportPdf` | `"never"`, `"onSave"`, or `"onType"`. Default `"never"`. `"onSave"` avoids writing a new PDF for each edit. |
| `outputPath` | Artifact path template, without an explicit PDF suffix. `$dir/$name` places output beside the main source; `$root/build/$name` uses a build directory. |
| `rootPath` | Typst root for absolute imports. Use an absolute project path. |
| `typstExtraArgs` | Compiler arguments, such as `["--input", "draft=true", "/absolute/project/main.typ"]`. Put the entry file last. Inputs are strings available through `sys.inputs`. |
| `fontPaths` | Array of additional font files or directories. Absolute paths are simplest when configuring Zed. |
| `systemFonts` | `true` allows system fonts. Set `false` and supply explicit font paths when you need controlled font selection. Restart Tinymist after changing it. |
| `projectResolution` | `"singleFile"` is the default. `"lockDatabase"` uses Tinymist's project/task history. An explicit main file is easier to inspect for a fixed paper or slide deck. |
| `semanticTokens` | `"enable"` or `"disable"`. Zed must also enable semantic tokens to display them. |
| `completion.symbol` | Inside a `completion` object: `"step"` or `"stepless"` controls math-symbol completion. Restart Tinymist after changing it. |
| `completion.postfix` | Inside `completion`: enables postfix completions. Restart Tinymist after changing it. |
| `completion.postfixUfcs`, `completion.postfixUfcsLeft`, `completion.postfixUfcsRight` | Inside `completion`: control the corresponding function-call completion styles. Restart Tinymist after changing them. |
| `lint.enabled`, `lint.when` | Inside a `lint` object: enable checks and choose `"onSave"` or `"onType"`. Restart Tinymist after changing them. Linting is disabled by default. |
| `syntaxOnly` | `"auto"`, `"disable"`, `"enable"`, or `"onPowerSaving"`. Syntax-only operation reduces language analysis; it does not configure the separate preview process. |
| `exportTarget` | `"paged"`, `"html"`, or `"bundle"` sets the server's document target. Ordinary documents and slides use `"paged"`. |

Nested options use objects rather than dotted JSON keys:

```jsonc
{
  "lsp": {
    "tinymist": {
      "settings": {
        "completion": {
          "symbol": "stepless",
          "postfix": true
        },
        "lint": {
          "enabled": true,
          "when": "onSave"
        }
      }
    }
  }
}
```

Restart using **editor: restart language server**, with a `.typ` file active, after changing startup-sensitive options. Normal Typst compilation diagnostics do not require enabling the optional lint checks.

## A paper or slide deck with several source files

Use [examples/project-settings.jsonc](../examples/project-settings.jsonc) as `.zed/settings.json`. Replace every `/absolute/path/to/project` placeholder with the project's actual path. Its configuration pins `main.typ`, sets the root, adds a fonts directory, passes `draft=true` through `sys.inputs`, and exports PDFs under `build` on save. Remove font or input entries you do not use. Create the output directory if your Tinymist version does not create it automatically.

Setting `rootPath` only chooses the root for imports; it does not select `main.typ`. The final entry in `typstExtraArgs` selects the server's main file. This affects diagnostics and server-generated PDF exports when you edit included chapters.

The synchronized preview remains a separate compiler process. It receives unsaved source text through the LSP proxy, but it does not currently inherit `rootPath`, the main file, `fontPaths`, `systemFonts`, `typstExtraArgs`, or `preview` options from the server settings. Match those compiler choices in `.zed/tasks.json`.

[examples/synchronized-preview.tasks.json](../examples/synchronized-preview.tasks.json) provides document and slide tasks for the same `main.typ`, `fonts` directory, and `draft=true` input. The example assumes the Zed worktree is the Typst root. Remove unused flags. For projects using `systemFonts: false`, also add `--ignore-system-fonts` after the task's `--` separator. For a custom root, change the preview task's `--root` argument to match `rootPath`.

This distinction also applies to custom package directories and compiler features: configure the language server through `typstExtraArgs`, and pass the corresponding arguments to the preview task. The preview's selected entry point stays fixed when you edit an included file.

## Preview settings and cursor sync

For **Synchronized Preview** and **Synchronized Slide Preview**:

- Updates while typing are enabled by the companion's LSP connection; no additional settings switch is needed.
- Document versus slide mode is selected by the task's `--mode` argument.
- Cursor-to-preview jumps use **Typst: Sync Preview to Cursor** or a shortcut in `keymap.json`; no `settings.json` flag enables them.
- Continuous following on every cursor movement is not implemented. VS Code settings such as `preview.scrollSync` do not add that capability to Zed.
- The synchronized preview's dark-color and rendering options are CLI arguments. For example, add `--invert-colors=auto` after the task's `--` separator.
- `preview.background.enabled` and `preview.refresh` configure Tinymist's own LSP preview, not the synchronized companion process.

The optional LSP background preview can be enabled through initialization options as described in the [README](../README.md#background-preview-of-unsaved-edits). Its `preview` object supports background enablement/arguments, refresh timing, color inversion, and partial rendering. It is an alternative preview server with its own port; synchronized tasks do not require it.

Copy or merge [examples/keymap.json](../examples/keymap.json) into your user `keymap.json` to bind document preview, slide preview, and cursor sync. That file is an array, whereas `settings.json` is an object.

## Custom binaries and environment

Normally the extension finds Tinymist on `PATH` and finds the installed companion automatically. To choose a specific server binary, merge this into settings:

```jsonc
{
  "lsp": {
    "tinymist": {
      "binary": {
        "path": "/absolute/path/to/tinymist",
        "arguments": ["lsp"],
        "env": {
          "TYPST_FONT_PATHS": "/absolute/path/to/fonts"
        }
      }
    }
  }
}
```

`path` selects the actual Tinymist executable. The extension adds the live-buffer forwarding layer when the companion is available. `arguments` replace the default `["lsp"]`, and `env` overrides the language-server process environment. These `binary.env` entries do not set the environment of separately launched task terminals. Use a task's `env` object or your shell environment when the preview needs the same variable.

The full server configuration reference includes some client-specific controls that Zed does not implement, such as VS Code command-triggered completion behavior and compilation status UI. A server accepting an option does not mean Zed provides its associated UI. The examples here cover the existing extension and companion integration.
