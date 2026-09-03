use std::sync::Mutex;

#[cfg(target_os = "windows")]
use std::{os::windows::process::CommandExt, process::Command};

use tauri::{Manager, RunEvent};
use tauri_plugin_shell::{process::CommandChild, ShellExt};
use uuid::Uuid;

struct LocalControlService(Mutex<Option<CommandChild>>);
struct LocalApiToken(String);

#[tauri::command]
fn local_api_token(token: tauri::State<'_, LocalApiToken>) -> String {
    token.0.clone()
}

/// 把随包分发的 CLI（resourcesin\pipedeck.exe）目录写入用户 PATH（去重）。
/// NSIS 安装器已做过一次；该命令用于 PATH 被破坏后的手动修复（Settings 页按钮）。
#[tauri::command]
fn install_cli_to_path(app: tauri::AppHandle) -> Result<String, String> {
    #[cfg(windows)]
    {
        use std::path::PathBuf;

        let resource_dir = app
            .path()
            .resource_dir()
            .map_err(|error| format!("无法定位安装资源目录：{error}"))?;
        let cli_dir: PathBuf = resource_dir.join("resources").join("bin");
        if !cli_dir.join("pipedeck.exe").is_file() {
            return Err(format!("安装目录缺少 CLI：{}", cli_dir.display()));
        }
        let cli_dir = cli_dir.to_string_lossy().to_string();

        use winreg::enums::{HKEY_CURRENT_USER, KEY_READ, KEY_SET_VALUE};
        use winreg::RegKey;
        let environment =
            RegKey::predef(HKEY_CURRENT_USER).open_subkey_with_flags("Environment", KEY_READ | KEY_SET_VALUE)
                .map_err(|error| format!("无法打开用户 Environment 注册表：{error}"))?;
        let current: String = environment.get_value("Path").unwrap_or_default();
        let already_present = current
            .split(';')
            .any(|entry| entry.eq_ignore_ascii_case(&cli_dir));
        if already_present {
            return Ok(current);
        }
        let updated = if current.is_empty() {
            cli_dir.clone()
        } else {
            format!("{};{}", current.trim_end_matches(';'), cli_dir)
        };
        environment
            .set_value("Path", &updated)
            .map_err(|error| format!("无法写入用户 PATH：{error}"))?;
        broadcast_environment_change();
        Ok(updated)
    }
    #[cfg(not(windows))]
    {
        let _ = app;
        Err("CLI PATH 注册仅支持 Windows；macOS 使用首启 symlink 流程".to_string())
    }
}

/// 直接写注册表后手动广播 WM_SETTINGCHANGE，让资源管理器与已开终端感知 PATH 变化。
#[cfg(windows)]
fn broadcast_environment_change() {
    const HWND_BROADCAST: *mut core::ffi::c_void = 0xFFFF as *mut core::ffi::c_void;
    const WM_SETTINGCHANGE: u32 = 0x001A;
    const SMTO_ABORTIFHUNG: u32 = 0x0002;
    let parameter: Vec<u16> = "Environment"
        .encode_utf16()
        .chain(std::iter::once(0))
        .collect();
    unsafe {
        let mut result: usize = 0;
        windows_sys::Win32::UI::WindowsAndMessaging::SendMessageTimeoutW(
            HWND_BROADCAST,
            WM_SETTINGCHANGE,
            0,
            parameter.as_ptr() as isize,
            SMTO_ABORTIFHUNG,
            5000,
            &mut result,
        );
    }
}

fn stop_process_tree(process: CommandChild) {
    #[cfg(target_os = "windows")]
    {
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        let pid = process.pid().to_string();
        let stopped = Command::new("taskkill")
            .args(["/PID", &pid, "/T", "/F"])
            .creation_flags(CREATE_NO_WINDOW)
            .status()
            .is_ok_and(|status| status.success());
        if stopped {
            return;
        }
    }

    let _ = process.kill();
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let application = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![local_api_token, install_cli_to_path])
        .setup(|app| {
            let token = Uuid::new_v4().simple().to_string();
            let (mut events, child) = app
                .shell()
                .sidecar("pipedeckd")?
                .env("PIPEDECK_API_TOKEN", &token)
                .spawn()?;
            app.manage(LocalApiToken(token));
            app.manage(LocalControlService(Mutex::new(Some(child))));
            tauri::async_runtime::spawn(async move { while events.recv().await.is_some() {} });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build Pipedeck desktop application");

    application.run(|app_handle, event| {
        if let RunEvent::Exit = event {
            let Some(service) = app_handle.try_state::<LocalControlService>() else {
                return;
            };
            let Ok(mut child) = service.0.lock() else {
                return;
            };
            if let Some(process) = child.take() {
                stop_process_tree(process);
            }
        }
    });
}
