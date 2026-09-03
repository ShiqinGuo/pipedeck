// 桌面应用必须是无控制台窗口的 GUI 子系统；否则双击启动会弹出 cmd 窗口。
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    pipedeck_desktop_lib::run();
}
