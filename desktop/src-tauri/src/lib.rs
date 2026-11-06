//! ZenAgent desktop shell library.

mod rpc;

use tauri::{Manager, RunEvent};

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
        .invoke_handler(tauri::generate_handler![
            rpc::rpc_start,
            rpc::rpc_send,
            rpc::rpc_stop,
            rpc::runtime_probe,
            rpc::updates_disabled_by_env,
            rpc::relaunch_app,
        ]);
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
            _ => {}
        })
}
