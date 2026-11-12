# New repository with fresh history

The extension ID and display name are both `typst-live`. To create `gragusa/typst-live` with a single initial commit, copy the current working files into a separate directory and initialize a new Git repository there. This includes uncommitted source changes and new files.

From the current checkout:

```sh
mkdir ../typst-live
rsync -a --exclude-from=.gitignore --exclude='.git/' --exclude='.ruff_cache/' --exclude='untitled.typ' ./ ../typst-live/
cd ../typst-live
```

Keep the original checkout intact. Do not copy its `.git` directory. The exclusions omit generated files and the local scratch document.

Before committing, change `repository` in `extension.toml` to `https://github.com/gragusa/typst-live` and the README clone URL to `https://github.com/gragusa/typst-live.git`. Keep the original license and attribution.

Initialize the new repository, review the files, and create its first commit:

```sh
git init -b main
git add .github .gitignore Cargo.toml Cargo.lock LICENSE README.md extension.toml languages snippets docs examples tools
git diff --cached --stat
git commit -m "Initial import of typst-live" -m "Assisted-by: Codex"
git rev-list --count HEAD
```

The commit count should be `1`.

After reviewing the staged content and commit text, create the GitHub repository and push:

```sh
gh repo create gragusa/typst-live --public --source=. --remote=origin --push
```

Use `--private` instead of `--public` for a private repository. This creates a new repository; it does not rename or change the original remote repository. It also does not publish the extension to Zed's registry.

Existing dev installations continue to use the original checkout. To use the new directory, reinstall the editable companion from that directory, reinstall the dev extension, and restart the language server and preview.
