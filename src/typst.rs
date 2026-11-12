use std::fs;
use zed_extension_api::{
    self as zed,
    settings::{CommandSettings, LspSettings},
    Result,
};

struct TypstExtension {
    cached_binary_path: Option<String>,
}

fn is_installed_binary(path: &str) -> bool {
    fs::metadata(path).is_ok_and(|stat| stat.is_file() && stat.len() > 0)
}

fn local_binary_path(
    settings: Option<&CommandSettings>,
    find_in_path: impl FnOnce() -> Option<String>,
) -> Result<Option<String>> {
    match settings.and_then(|settings| settings.path.as_ref()) {
        Some(path) if path.trim().is_empty() => {
            Err("lsp.tinymist.binary.path must not be empty".to_string())
        }
        Some(path) => Ok(Some(path.clone())),
        None => Ok(find_in_path()),
    }
}

fn server_command(
    path: String,
    settings: Option<CommandSettings>,
    shell_env: Vec<(String, String)>,
) -> zed::Command {
    let mut env: std::collections::BTreeMap<_, _> = shell_env.into_iter().collect();
    let mut args = vec!["lsp".to_string()];
    if let Some(settings) = settings {
        if let Some(arguments) = settings.arguments {
            args = arguments;
        }
        env.extend(settings.env.unwrap_or_default());
    }
    zed::Command {
        command: path,
        args,
        env: env.into_iter().collect(),
    }
}

fn preview_proxy(command: zed::Command, helper: String, root: String) -> zed::Command {
    let mut args = vec![
        "lsp".to_string(),
        "--root".to_string(),
        root,
        "--server".to_string(),
        command.command,
        "--".to_string(),
    ];
    args.extend(command.args);
    zed::Command {
        command: helper,
        args,
        env: command.env,
    }
}

impl TypstExtension {
    fn language_server_binary(
        &mut self,
        language_server_id: &zed::LanguageServerId,
    ) -> Result<String> {
        if let Some(path) = &self.cached_binary_path {
            if is_installed_binary(path) {
                return Ok(path.clone());
            }
        }

        zed::set_language_server_installation_status(
            language_server_id,
            &zed::LanguageServerInstallationStatus::CheckingForUpdate,
        );
        let release = zed::latest_github_release(
            "Myriad-Dreamin/tinymist",
            zed::GithubReleaseOptions {
                require_assets: true,
                pre_release: false,
            },
        );

        let release = match release {
            Ok(r) => r,
            Err(e) => {
                if let Ok(path) = fs::read_to_string("latest-binary") {
                    if is_installed_binary(&path) {
                        eprintln!("Could not check Tinymist releases ({e}); using {path}");
                        self.cached_binary_path = Some(path.clone());
                        return Ok(path);
                    }
                }
                return Err(e);
            }
        };

        let (platform, arch) = zed::current_platform();
        let mut asset_name = format!(
            "tinymist-{os}-{arch}",
            arch = match arch {
                zed::Architecture::Aarch64 => "arm64",
                zed::Architecture::X86 => "x86",
                zed::Architecture::X8664 => "x64",
            },
            os = match platform {
                zed::Os::Mac => "darwin",
                zed::Os::Linux => "linux",
                zed::Os::Windows => "win32",
            },
        );

        if platform == zed::Os::Windows {
            asset_name = format!("{}.exe", asset_name);
        }

        let asset = release
            .assets
            .iter()
            .find(|asset| asset.name == asset_name)
            .ok_or_else(|| format!("no asset found matching {:?}", asset_name))?;

        let version_dir = format!("tinymist-{}", release.version);
        fs::create_dir_all(&version_dir).map_err(|e| format!("failed to create directory: {e}"))?;

        let executable = if platform == zed::Os::Windows {
            "tinymist.exe"
        } else {
            "tinymist"
        };
        let binary_path = format!("{version_dir}/{executable}");

        if !is_installed_binary(&binary_path) {
            zed::set_language_server_installation_status(
                language_server_id,
                &zed::LanguageServerInstallationStatus::Downloading,
            );

            // A failed download must not become the executable on the next launch.
            let download_path = format!("{binary_path}.download");
            zed::download_file(
                &asset.download_url,
                &download_path,
                zed::DownloadedFileType::Uncompressed,
            )
            .map_err(|e| format!("failed to download file: {e}"))?;

            if !is_installed_binary(&download_path) {
                return Err("downloaded Tinymist binary is empty or missing".to_string());
            }
            zed::make_file_executable(&download_path)?;
            fs::rename(&download_path, &binary_path)
                .map_err(|e| format!("failed to install Tinymist binary: {e}"))?;
        }

        // Also repair the marker when the binary was installed in a previous session.
        fs::write("latest-binary", &binary_path)
            .map_err(|e| format!("failed to remember installed Tinymist binary: {e}"))?;
        self.cached_binary_path = Some(binary_path.clone());
        Ok(binary_path)
    }
}

impl zed::Extension for TypstExtension {
    fn new() -> Self {
        Self {
            cached_binary_path: None,
        }
    }

    fn language_server_command(
        &mut self,
        language_server_id: &zed::LanguageServerId,
        worktree: &zed::Worktree,
    ) -> Result<zed::Command> {
        let settings = LspSettings::for_worktree(language_server_id.as_ref(), worktree)?;
        let path = match local_binary_path(settings.binary.as_ref(), || worktree.which("tinymist"))?
        {
            Some(path) => path,
            None => {
                let downloaded = self.language_server_binary(language_server_id)?;
                std::env::current_dir()
                    .map_err(|error| format!("failed to locate Tinymist installation: {error}"))?
                    .join(downloaded)
                    .to_string_lossy()
                    .into_owned()
            }
        };
        let command = server_command(path, settings.binary, worktree.shell_env());
        Ok(match worktree.which("zed-typst-preview") {
            Some(helper) => preview_proxy(command, helper, worktree.root_path()),
            None => command,
        })
    }

    fn language_server_initialization_options(
        &mut self,
        server_id: &zed_extension_api::LanguageServerId,
        worktree: &zed_extension_api::Worktree,
    ) -> Result<Option<zed_extension_api::serde_json::Value>> {
        Ok(LspSettings::for_worktree(server_id.as_ref(), worktree)?.initialization_options)
    }

    fn language_server_workspace_configuration(
        &mut self,
        server_id: &zed_extension_api::LanguageServerId,
        worktree: &zed_extension_api::Worktree,
    ) -> Result<Option<zed_extension_api::serde_json::Value>> {
        Ok(LspSettings::for_worktree(server_id.as_ref(), worktree)?.settings)
    }
}

zed::register_extension!(TypstExtension);

#[cfg(test)]
mod tests {
    use super::*;
    use zed::serde_json::json;

    fn binary_settings(value: zed::serde_json::Value) -> CommandSettings {
        zed::serde_json::from_value(value).unwrap()
    }

    #[test]
    fn configured_path_takes_precedence_without_searching_path() {
        let settings = binary_settings(json!({"path": "/custom tools/tinymist"}));
        let path = local_binary_path(Some(&settings), || panic!("must not search PATH")).unwrap();
        assert_eq!(path.as_deref(), Some("/custom tools/tinymist"));
    }

    #[test]
    fn empty_configured_path_is_an_error_without_fallback() {
        for path in ["", "  "] {
            let settings = binary_settings(json!({"path": path}));
            assert!(local_binary_path(Some(&settings), || panic!("must not fall back")).is_err());
        }
    }

    #[test]
    fn arguments_only_settings_still_search_path() {
        let settings = binary_settings(json!({"arguments": ["lsp", "--verbose"]}));
        let path = local_binary_path(Some(&settings), || Some("/bin/tinymist".into())).unwrap();
        assert_eq!(path.as_deref(), Some("/bin/tinymist"));
    }

    #[test]
    fn missing_local_binary_requests_installation() {
        assert_eq!(local_binary_path(None, || None).unwrap(), None);
    }

    #[test]
    fn default_command_preserves_the_shell_environment() {
        let env = vec![("TYPST_FONT_PATHS".into(), "/project/fonts".into())];
        let command = server_command("downloaded/tinymist".into(), None, env.clone());
        assert_eq!(command.command, "downloaded/tinymist");
        assert_eq!(command.args, ["lsp"]);
        assert_eq!(command.env, env);
    }

    #[test]
    fn configured_arguments_and_environment_override_defaults() {
        let settings = binary_settings(json!({
            "arguments": ["lsp", "--font-path", "/custom fonts"],
            "env": {"TYPST_FONT_PATHS": "/custom fonts", "CUSTOM": "enabled"}
        }));
        let command = server_command(
            "/bin/tinymist".into(),
            Some(settings),
            vec![
                ("TYPST_FONT_PATHS".into(), "/system/fonts".into()),
                ("PATH".into(), "/bin".into()),
            ],
        );
        assert_eq!(command.args, ["lsp", "--font-path", "/custom fonts"]);
        let env: std::collections::BTreeMap<_, _> = command.env.into_iter().collect();
        assert_eq!(env["TYPST_FONT_PATHS"], "/custom fonts");
        assert_eq!(env["CUSTOM"], "enabled");
        assert_eq!(env["PATH"], "/bin");
        assert_eq!(env.len(), 3);
    }

    #[test]
    fn explicitly_empty_arguments_are_preserved() {
        let settings = binary_settings(json!({"arguments": []}));
        let command = server_command("/bin/tinymist".into(), Some(settings), vec![]);
        assert!(command.args.is_empty());
    }

    #[test]
    fn preview_proxy_preserves_server_arguments_and_environment() {
        let command = zed::Command {
            command: "/tools with spaces/tinymist".into(),
            args: vec!["lsp".into(), "--font-path".into(), "/custom fonts".into()],
            env: vec![("TYPST_FONT_PATHS".into(), "/custom fonts".into())],
        };
        let wrapped = preview_proxy(
            command,
            "/bin/zed-typst-preview".into(),
            "/paper project".into(),
        );
        assert_eq!(wrapped.command, "/bin/zed-typst-preview");
        assert_eq!(
            wrapped.args,
            [
                "lsp",
                "--root",
                "/paper project",
                "--server",
                "/tools with spaces/tinymist",
                "--",
                "lsp",
                "--font-path",
                "/custom fonts"
            ]
        );
        assert_eq!(
            wrapped.env,
            [("TYPST_FONT_PATHS".into(), "/custom fonts".into())]
        );
    }

    #[test]
    fn empty_downloads_and_directories_are_not_installed_binaries() {
        let directory = std::env::temp_dir().join(format!(
            "zed-typst-test-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir(&directory).unwrap();
        let binary = directory.join("tinymist");
        let path = binary.to_str().unwrap();
        assert!(!is_installed_binary(path));
        assert!(!is_installed_binary(directory.to_str().unwrap()));
        fs::write(&binary, []).unwrap();
        assert!(!is_installed_binary(path));
        fs::write(&binary, b"binary").unwrap();
        assert!(is_installed_binary(path));
        fs::remove_file(binary).unwrap();
        fs::remove_dir(directory).unwrap();
    }
}
