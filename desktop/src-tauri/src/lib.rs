//! ZenAgent desktop shell library.

mod rpc;

use tauri::{RunEvent, Manager};

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(rpc::RpcState::default())
        .invoke_handler(tauri::generate_handler![
            rpc::rpc_start,
            rpc::rpc_send,
            rpc::rpc_stop,
            rpc::runtime_probe,
            rpc::updates_disabled_by_env,
        ])
        .build(tauri::generate_context!())
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
