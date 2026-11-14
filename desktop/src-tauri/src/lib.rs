//! ZenAgent desktop shell library.

mod rpc;

use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Manager, RunEvent, WindowEvent,
};

/// Bring the main window back from the background (tray click, dock reopen).
fn show_main(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.set_focus();
    }
}

pub fn run() {
    let context = tauri::generate_context!();
    // An empty `plugins.updater.pubkey` means this build has no release key:
    // the updater plugin cannot verify anything, so it is never registered and
    // the frontend hides the update UI (see `__UPDATE_ENABLED__` in vite.config).
    let updater_configured = context
        .config()
        .plugins
        .0
        .get("updater")
        .and_then(|updater| updater.get("pubkey"))
        .and_then(|pubkey| pubkey.as_str())
        .is_some_and(|pubkey| !pubkey.trim().is_empty());

    let mut builder = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(rpc::RpcState::default())
        // Closing the window goes to the background: the zen runtime (and any
        // background task it is running) stays alive, and the tray (or the
        // macOS dock) brings the window back. Quitting is the tray menu's
        // job — that is the only path that ends the runtime.
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .invoke_handler(tauri::generate_handler![
            rpc::rpc_start,
            rpc::rpc_send,
            rpc::rpc_stop,
            rpc::runtime_probe,
            rpc::get_initial_state,
            rpc::relaunch_app,
            rpc::open_external,
        ])
        .setup(|app| {
            // Warm the login-shell env cache off the main thread so the first
            // rpc_start does not pay the (up to 3s) harvest cost. The window
            // starts hidden (see tauri.conf.json) and the webview calls show()
            // once its first paint is ready.
            std::thread::spawn(rpc::prewarm_shell_env);
            let open = MenuItem::with_id(app, "open", "Open ZenAgent", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit ZenAgent", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open, &quit])?;
            TrayIconBuilder::with_id("main")
                .icon(app.default_window_icon().expect("bundle icon").clone())
                .tooltip("ZenAgent")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_tray_icon_event(|tray, event| {
                    // Left click opens the window; the context menu lives on
                    // the right click (Windows/Linux convention).
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        show_main(tray.app_handle());
                    }
                })
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "open" => show_main(app),
                    "quit" => app.exit(0),
                    _ => {}
                })
                .build(app)?;
            Ok(())
        });
    if updater_configured {
        builder = builder.plugin(tauri_plugin_updater::Builder::new().build());
    }

    builder
        .build(context)
        .expect("error while building tauri application")
        .run(|app_handle, event| match event {
            RunEvent::Exit => {
                if let Some(state) = app_handle.try_state::<rpc::RpcState>() {
                    rpc::kill_all(state.inner());
                }
            }
            // macOS: clicking the dock icon re-opens the hidden window.
            #[cfg(target_os = "macos")]
            RunEvent::Reopen { .. } => show_main(app_handle),
            _ => {}
        })
}
