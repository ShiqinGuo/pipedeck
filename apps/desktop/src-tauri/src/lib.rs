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

fn validate_local_application_url(value: &str) -> Result<String, String> {
    let url = tauri::Url::parse(value).map_err(|_| "Invalid application URL".to_string())?;
    if !matches!(url.scheme(), "http" | "https") {
        return Err("Only HTTP or HTTPS applications can be opened".to_string());
    }
    // Url normalizes an empty userinfo component away, so also inspect the raw authority.
    let authority = value
        .split_once("://")
        .map(|(_, rest)| rest.split(['/', '\\', '?', '#']).next().unwrap_or_default())
        .ok_or_else(|| "Application URL must contain an authority".to_string())?;
    if !url.username().is_empty() || url.password().is_some() || authority.contains('@') {
        return Err("Application URL cannot contain credentials".to_string());
    }
    let host = url.host_str().unwrap_or_default();
    let loopback = host == "localhost"
        || host
            .trim_start_matches('[')
            .trim_end_matches(']')
            .parse::<std::net::IpAddr>()
            .is_ok_and(|address| address.is_loopback());
    if !loopback {
        return Err("Application URL must use a loopback host".to_string());
    }
    Ok(url.to_string())
}

#[tauri::command]
#[allow(deprecated)] // Reuse the bundled shell plugin; the URL boundary is checked above.
fn open_local_application(app: tauri::AppHandle, url: String) -> Result<(), String> {
    let validated = validate_local_application_url(&url)?;
    app.shell()
        .open(validated, None)
        .map_err(|error| format!("Could not open the local application: {error}"))
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
        let environment = RegKey::predef(HKEY_CURRENT_USER)
            .open_subkey_with_flags("Environment", KEY_READ | KEY_SET_VALUE)
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

/// %LOCALAPPDATA%/Pipedeck,与 Python LocalSettings 的默认状态目录一致。
fn local_state_dir() -> std::path::PathBuf {
    let base = std::env::var("LOCALAPPDATA").unwrap_or_else(|_| '.'.to_string());
    std::path::Path::new(&base).join("Pipedeck")
}

/// 7421 端口可连通即认为控制面存活(端口由本产品独占)。
fn control_plane_alive() -> bool {
    std::net::TcpStream::connect("127.0.0.1:7421").is_ok()
}

fn read_or_create_token(state_dir: &std::path::Path) -> Result<String, String> {
    let path = state_dir.join("cli-token");
    if let Ok(existing) = std::fs::read_to_string(&path) {
        let trimmed = existing.trim().to_string();
        if !trimmed.is_empty() {
            return Ok(trimmed);
        }
    }
    let token = Uuid::new_v4().simple().to_string();
    std::fs::create_dir_all(state_dir).map_err(|error| error.to_string())?;
    std::fs::write(&path, &token).map_err(|error| error.to_string())?;
    Ok(token)
}

/// spawn 后 sidecar 需要一点时间写出 cli-token;轮询至多 5 秒。
fn wait_for_token(state_dir: &std::path::Path) -> Result<String, String> {
    for _ in 0..50 {
        if let Ok(existing) = std::fs::read_to_string(state_dir.join("cli-token")) {
            let trimmed = existing.trim().to_string();
            if !trimmed.is_empty() {
                return Ok(trimmed);
            }
        }
        std::thread::sleep(std::time::Duration::from_millis(100));
    }
    Err("sidecar 未在超时内写出 cli-token".to_string())
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
        // 单实例锁:第二次启动聚焦已有窗口,避免新 sidecar 绑不上端口导致旧 token 失配(写操作 401)
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![
            local_api_token,
            install_cli_to_path,
            open_local_application
        ])
        .setup(|app| {
            // token 单一事实源是 cli-token 文件(sidecar 负责生成/写盘)。
            // 若 7421 上已有存活 sidecar(孤儿/上次会话遗留),复用它,不重复 spawn。
            let state_dir = local_state_dir();
            let reuse = control_plane_alive();
            let (mut events, child) = if reuse {
                (None, None)
            } else {
                let token = read_or_create_token(&state_dir)?;
                let (events, child) = app
                    .shell()
                    .sidecar("pipedeckd")?
                    .env("PIPEDECK_API_TOKEN", &token)
                    .spawn()?;
                (Some(events), Some(child))
            };
            app.manage(LocalApiToken(wait_for_token(&state_dir)?));
            app.manage(LocalControlService(Mutex::new(child)));
            if let Some(mut events) = events {
                tauri::async_runtime::spawn(async move { while events.recv().await.is_some() {} });
            }
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

#[cfg(test)]
mod tests {
    use super::validate_local_application_url;

    #[test]
    fn accepts_http_applications_on_loopback_hosts() {
        for value in [
            "http://127.0.0.1:8080/app",
            "https://localhost:8443/?email=dev@example.com",
            "http://[::1]:8080/",
        ] {
            assert!(validate_local_application_url(value).is_ok(), "{value}");
        }
    }

    #[test]
    fn rejects_nonlocal_schemes_hosts_and_userinfo() {
        for value in [
            "file:///C:/Windows/System32/cmd.exe",
            "javascript:alert(1)",
            "https://example.com/",
            "http://192.168.1.1/",
            "http://localhost.example.com/",
            "http://user:secret@127.0.0.1/",
            "http://@localhost/",
            "http://:secret@localhost/",
            "not a URL",
        ] {
            assert!(validate_local_application_url(value).is_err(), "{value}");
        }
    }
}
