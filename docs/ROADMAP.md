# Toward Tinymist feature parity

The extension already uses Tinymist as its language server. The goal is to expose more of that service in Zed and improve the writing workflow. Reimplementing the Typst compiler or language analysis would duplicate the existing backend.

## Current coverage

| Capability | Integration in this repository | Remaining work |
| --- | --- | --- |
| Completions, diagnostics, hover, navigation, rename, signature help, inlay hints | Tinymist via Zed's LSP client | Verify behavior in Zed with representative multi-file documents. |
| Formatting | Tinymist's formatter and Zed's format actions | Verify range formatting and configuration changes interactively. |
| Syntax, outline, indentation, embedded languages | Tree-sitter grammar and queries | Expand outlines beyond headings; add query fixtures before changing the grammar. |
| Live document and slide preview | Synchronized previews receive unsaved buffers through an LSP proxy; ordinary tasks read saved files | Share compiler settings and improve preview lifecycle integration. |
| PDF, PNG, SVG export | CLI tasks; LSP PDF export on save | Shared project settings and export commands through the running server. |
| Writing snippets | Twelve snippets with tab stops | Add examples and refine based on writing workflows. |
| Font discovery | CLI font listing task | A font gallery requires a richer UI or browser companion. |
| Server startup | Custom path, arguments, environment, automatic download, offline fallback | Test automatic installation on macOS, Linux, and Windows. |
| Cursor synchronization | Companion: task-triggered source-to-preview jumps and preview-click-to-source jumps, using unsaved buffers | Continuous cursor following needs editor movement events. |
| Embedded preview | No integration | Requires an embedded UI API. |
| Template, symbol, package, and document-summary views | No integration | Evaluate browser interfaces and available Tinymist commands. |
| Profiling and Typst debugging | No integration | Investigate Tinymist's profiler and DAP support as separate features. |

LSP features in this table depend on the installed Tinymist version and Zed's client support. Registering the server does not establish parity with every VS Code feature. In particular, code lenses and code actions that call VS Code commands need separate validation.

## Next steps

1. **Validate the editing workflow.** Install the dev extension and check completions in markup, code, and math; bibliography and label references; multi-file diagnostics; formatting; and unsaved background preview. Add reproducible fixtures for failures. Exercise installation and tasks on all three operating systems.
2. **Improve project handling.** Verify Tinymist's `lockDatabase` project resolution against multi-file papers and slide decks. Define how entry points, fonts, and `sys.inputs` are shared across language service, preview, and export before adding project controls. Keep configuration errors visible.
3. **Improve native editing.** Extend Tree-sitter outlines and text objects, refine snippet defaults, and investigate completion labels and semantic-token mappings. Test every query against the pinned grammar.
4. **Close editor API gaps.** Investigate custom server commands, editor selection/change events, and embedded UI support with Zed. Replace the [companion's LSP proxy](../tools/preview/README.md) with native buffer events if Zed exposes them, and add continuous cursor following when movement events become available. Treat an embedded preview as a separate integration project.

## Platform constraints and references

Zed's extension API provides language server setup, completion/symbol labels, debug adapters, snippets, and related services. Its public extension trait currently has no general editor-selection subscription or webview-panel interface. Tasks expose a cursor snapshot through `ZED_FILE`, `ZED_ROW`, and `ZED_COLUMN`; the optional companion uses those values for source-to-preview jumps and invokes the Zed CLI for the return direction. Unsaved document text is available through the companion's LSP proxy. Continuous cursor following remains unavailable to this integration.

- [Zed extension development](https://zed.dev/docs/extensions/developing-extensions)
- [Zed extension API](https://docs.rs/zed_extension_api/latest/zed_extension_api/trait.Extension.html)
- [Zed task definitions](https://zed.dev/docs/tasks)
- [Zed snippet extensions](https://zed.dev/docs/extensions/snippets)
- [Tinymist language and editor features](https://github.com/Myriad-Dreamin/tinymist#features)
- [Tinymist server configuration](https://myriad-dreamin.github.io/tinymist/config/neovim.html)
