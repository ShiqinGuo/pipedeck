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
        .invoke_handler(tauri::generate_handler![local_api_token])
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
