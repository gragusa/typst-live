# Historical publishing audit for typst-live

Registry publication is no longer the distribution plan. Follow the [dev installation guide](../README.md) instead. The audit below is retained for reference if that decision changes.

Audited on November 12, 2026 against Zed's [publishing prerequisites](https://zed.dev/docs/extensions/publishing/prerequisites), [license requirements](https://zed.dev/docs/extensions/publishing/license-requirements), [publishing guide](https://zed.dev/docs/extensions/publishing/publishing-guide), and [FAQ](https://zed.dev/docs/extensions/publishing/faq).

The checkout satisfies several structural requirements, but it is not yet ready to claim full registry compliance. Registry acceptance depends on upstream collaboration, review of the proxy architecture, and validation of the exact submitted commit. Automated checks and a successful local dev installation cannot establish maintainer acceptance.

## Findings

| Requirement | Assessment | Evidence or remaining work |
| --- | --- | --- |
| Unique, descriptive ID without `zed` or `extension` | Naming addressed; availability checked | ID and display name are `typst-live`. The ID was absent from the registry's `extensions.toml` at the audit date. This check does not reserve the ID or establish whether another submission is pending. |
| Appropriate language name and scope | Structurally met | Typst is the only registered language. Language settings continue to use `Typst`. |
| Grammar for every provided language | Met | The manifest registers `grammars.typst` and pins its revision. |
| Rust code associated with a language server | Met | The Rust extension configures and launches Tinymist, optionally through the companion. |
| Language server discovered or downloaded through Zed's API | Met in the current adapter | The adapter uses `worktree.which`, release lookup, and `download_file`. Tinymist binaries are not committed to the repository. |
| Accepted license at the extension root | Met | Root `LICENSE` contains Apache License 2.0, which is on Zed's accepted list. Existing attribution is retained. This is a check of the registry's stated license requirement, not a comprehensive redistribution audit. |
| English user-facing text | Met for the reviewed extension files | Manifest, task labels, snippets, companion messages, and documentation are in English. |
| Appropriate extension filesystem access | Partially verified; architecture review needed | Rust filesystem operations use paths in the extension's work directory. The native companion reads project sources, stores private session records in the user's temporary directory, and launches the Zed CLI. Disclose that separate process and its access during review; do not assume externalizing behavior establishes compliance. |
| No misuse of APIs to bypass limitations | Maintainer decision needed | The companion forwards LSP traffic to obtain unsaved buffers and uses task cursor snapshots plus `zed --existing` for source jumps. This is a workaround for missing native buffer and cursor APIs. Zed's requirements make acceptance of workarounds discretionary. |
| Functionality not already in the registry | Needs review with upstream | Live previews and synchronization add behavior to the original Typst extension, but the fork also duplicates its grammar and language-server integration. A new ID alone does not satisfy the policy against competing extensions. |
| Propose improvements to the original extension first | Unverified and still required | No upstream issue, proposal, contribution attempt, or maintainer response has been recorded in this work. Present the implementation to `zed-extensions/typst` before proposing a separate registry entry. |
| Manual testing of the exact submitted commit | Pending | Local dev testing has established working live previews and cursor synchronization on macOS. The renamed ID needs migration and a fresh manual check. Test again at the final commit selected for submission. |
| Include only necessary release resources | Needs a release-content check | Build products, grammar checkouts, virtual environments, and bytecode are ignored. The companion, examples, docs, and other new files must be included deliberately. Keep personal scratch documents, such as `untitled.typ`, out of the release commit. |
| Public repository and a commit reachable from a branch | Repository public; release commit pending | `https://github.com/gragusa/typst` was public at the audit date. The new implementation has not yet been recorded in a final tested release commit in this audit. |

The registry and repository checks use the current [extension registry](https://github.com/zed-industries/extensions/blob/main/extensions.toml) and [source repository](https://github.com/gragusa/typst). Recheck both before submitting.

## Companion installation and distribution

Dev mode is not a synchronization requirement. A registry-installed extension can use the same companion through Zed's language-server command API.

The current extension looks for `zed-typst-preview` on the worktree's shell `PATH`; it does not install it. Preview tasks also need `tinymist` and `zed` on the terminal's `PATH`. The companion currently requires Python 3.11 or newer and the `websockets` dependency.

Manual external-tool installation is not itself prohibited by the listed publishing prerequisites: those rules explicitly permit discovering tools in the user's environment. However, a marketplace user will not automatically have a checkout containing `tools/preview`. Before release, provide a documented installation route from an available source commit or a separately distributed companion package, and validate it on a clean installation. Automatic companion setup would improve installation, but is not a stated universal registry requirement.

The extension's Rust `publish = false` setting only prevents publishing the Rust crate to crates.io. It does not prevent publishing the Zed extension.

## Review material to prepare

Prepare an upstream proposal that describes the user-visible additions, identifies the existing Typst functionality reused by the fork, and explains the LSP proxy and task/CLI cursor bridge. Include the external dependencies, native filesystem access, supported platforms, and the checks actually performed. Ask whether the maintainers prefer an upstream contribution or support a distinct extension.

If a separate extension is appropriate, prepare a registry submission with the chosen public branch commit, matching manifest version, installation instructions, and a manual test record. A proposed registry entry would use `typst-live` and `extensions/typst-live`.

No upstream contact or registry submission has been made by this audit. GitHub messages and submission text require the author's approval before posting.

## Updating an existing dev installation

Changing the extension ID from `typst` to `typst-live` creates a different extension identity. It does not replace the old installation automatically.

1. Stop the synchronized preview with Ctrl-C in its task terminal.
2. Remove the old dev extension through Zed's Extensions page. It may appear as **Typst** or **Typst Live** and has ID `typst`.
3. Keep the original marketplace **Typst** extension disabled or uninstalled while testing this fork, so both extensions do not register competing Typst support.
4. Run **zed: install dev extension** and select this checkout. The new ID and display name are both **typst-live**.
5. Focus a `.typ` file and run **editor: restart language server**.
6. Start **Typst: Synchronized Preview** or **Typst: Synchronized Slide Preview** and confirm **Live preview connected to Zed's unsaved buffers** appears.
7. Check typing updates, a jump from source to preview, and a preview click back to source. Repeat with slides and an included chapter.

The companion remains `zed-typst-preview`, the language remains `Typst`, and the language server remains `tinymist`. Existing settings and task keybindings continue to use those names.
