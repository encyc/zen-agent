//! JSONL bridge between the webview and one `zen rpc` subprocess.
//!
//! The protocol mirrors `zenagent/cli/rpc.py`: the frontend writes request
//! objects (each carrying a client-chosen `id`), the subprocess answers with
//! exactly one `{"type":"result"}` or `{"type":"error"}` frame per request,
//! and any number of `{"type":"event", ...}` frames may arrive between them.
//! This module owns the child process, correlates result frames with pending
//! callers by id, and forwards everything else to the webview as Tauri
//! events (`rpc:frame`, `rpc:stderr`, `rpc:exit`).

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::path::Path;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::{AppHandle, Emitter, State};

/// Shared pending-request registry: request id → one-shot result sender.
type Pending = Arc<Mutex<HashMap<String, Sender<Value>>>>;

struct Proc {
    /// Shared with the waiter thread (it polls `try_wait` under the lock so a
    /// concurrent kill from `stop_proc` can never race `wait`).
    child: Arc<Mutex<Child>>,
    stdin: Mutex<ChildStdin>,
    pending: Pending,
    /// Flipped false once the child is gone (reader EOF or waiter).
    alive: Arc<AtomicBool>,
}

#[derive(Default)]
pub struct RpcState {
    proc: Mutex<Option<Arc<Proc>>>,
}

#[derive(Deserialize)]
pub struct StartOptions {
    /// Binary to launch; defaults to `zen`.
    #[serde(default)]
    pub bin: Option<String>,
    /// Working directory handed to `--cwd`.
    #[serde(default)]
    pub cwd: Option<String>,
    /// Extra CLI flags after `rpc` (e.g. `["--mode","plan","--no-mcp"]`).
    #[serde(default)]
    pub args: Vec<String>,
}

#[derive(Serialize)]
pub struct StartReport {
    pub pid: u32,
}

/// Why `rpc_start` could not run the CLI. `runtime_missing` is the one the UI
/// turns into a "no runtime" card; the JSON keys are camelCase for the webview.
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct StartError {
    pub code: &'static str,
    pub bin: String,
    pub os_error: String,
}

/// What the shell found when asked where the runtime is (Settings → diagnostics).
#[derive(Serialize)]
pub struct RuntimeProbe {
    pub resolved: Option<String>,
    pub exists: bool,
    pub version: Option<String>,
}

/// One pending send: registered before the request hits stdin so an answer
/// racing the write can never miss its receiver.
struct Guard {
    pending: Pending,
    id: String,
}

impl Guard {
    fn register(pending: &Pending, id: String) -> (Self, Receiver<Value>) {
        let (tx, rx) = std::sync::mpsc::channel();
        pending.lock().unwrap().insert(id.clone(), tx);
        (Self {
            pending: Arc::clone(pending),
            id,
        }, rx)
    }
}

impl Drop for Guard {
    fn drop(&mut self) {
        self.pending.lock().unwrap().remove(&self.id);
    }
}

fn fail_all_pending(pending: &Pending, message: &str) {
    let mut map = pending.lock().unwrap();
    let ids: Vec<String> = map.keys().cloned().collect();
    for id in ids {
        if let Some(tx) = map.remove(&id) {
            let _ = tx.send(serde_json::json!({
                "type": "error", "id": id, "message": message,
            }));
        }
    }
}

fn kill_quiet(child: &mut Child) {
    let _ = child.kill();
    let _ = child.wait();
}

/// Stop the running subprocess: ask `shutdown` first, then force-kill after a
/// grace period. `zen rpc` settles pending approvals and finishes its run on
/// its own shutdown path; a hard kill only happens when it is stuck.
fn stop_proc(proc: &Arc<Proc>) {
    proc.alive.store(false, Ordering::SeqCst);
    {
        let mut stdin = proc.stdin.lock().unwrap();
        let request = serde_json::json!({"type": "shutdown", "id": "__stop__"}).to_string();
        let _ = writeln!(stdin, "{request}");
        let _ = stdin.flush();
    }
    for _ in 0..30 {
        {
            let mut child = proc.child.lock().unwrap();
            match child.try_wait() {
                Ok(Some(_)) | Err(_) => return,
                Ok(None) => {}
            }
        }
        thread::sleep(Duration::from_millis(100));
    }
    let mut child = proc.child.lock().unwrap();
    kill_quiet(&mut child);
}

/// A debug build runs from the repo checkout, so prefer its venv CLI: the
/// frontend needs RPC methods that may not exist in the installed release yet.
fn dev_checkout_bin() -> Option<String> {
    if !cfg!(debug_assertions) {
        return None;
    }
    let candidate = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../.venv/bin/zen");
    candidate
        .is_file()
        .then(|| candidate.to_string_lossy().into_owned())
}

/// The sidecar Tauri bundles sits next to the shell's own executable: macOS
/// keeps it in `Contents/MacOS/`, deb/AppImage put both in `usr/bin/`, and the
/// Windows installers drop it beside `ZenAgent.exe`.
fn exe_sibling_name() -> &'static str {
    if cfg!(windows) {
        "zen.exe"
    } else {
        "zen"
    }
}

/// Every place a CLI can plausibly live, in priority order: the bundled
/// sidecar first (the shipped app must run its own runtime), then the usual
/// user-local prefixes because a GUI app inherits launchd's minimal PATH.
pub fn candidate_paths(bin: &str, exe_dir: Option<&Path>, home: Option<&str>) -> Vec<String> {
    let mut paths = Vec::new();
    if let Some(dir) = exe_dir {
        paths.push(dir.join(bin).to_string_lossy().into_owned());
    }
    if let Some(home) = home {
        paths.push(format!("{home}/.local/bin/{bin}"));
    }
    paths.push(format!("/opt/homebrew/bin/{bin}"));
    paths.push(format!("/usr/local/bin/{bin}"));
    paths
}

/// Resolve the CLI binary: the `ZEN_DESKTOP_BIN` override wins, then an
/// explicit path, then the bundled sidecar beside this executable, then (in
/// dev) the checkout's venv, then the user-local prefixes.
fn resolve_bin(bin: &str) -> String {
    if let Ok(override_bin) = std::env::var("ZEN_DESKTOP_BIN") {
        if !override_bin.trim().is_empty() {
            return override_bin;
        }
    }
    if bin.contains('/') {
        return bin.to_string();
    }
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(Path::to_path_buf));
    let home = std::env::var("HOME").ok();
    let candidates = candidate_paths(exe_sibling_name(), exe_dir.as_deref(), home.as_deref());
    let sibling = exe_dir.is_some();
    if sibling {
        if let Some(first) = candidates.first() {
            if Path::new(first).is_file() {
                return first.clone();
            }
        }
    }
    if let Some(dev_bin) = dev_checkout_bin() {
        return dev_bin;
    }
    for candidate in candidates.iter().skip(if sibling { 1 } else { 0 }) {
        if Path::new(candidate).is_file() {
            return candidate.clone();
        }
    }
    bin.to_string()
}

/// Collect the user's login-shell environment (dsh desktop does the same):
/// a GUI app inherits launchd's minimal env, so API keys and PATH entries the
/// user configures in their shell profile would be invisible to `zen rpc`.
/// Best-effort: on any failure return an empty map and spawn as-is.
fn login_shell_env() -> HashMap<String, String> {
    let shell = std::env::var("SHELL").unwrap_or_else(|_| "/bin/zsh".to_string());
    let output = Command::new(&shell)
        .arg("-l")
        .arg("-i")
        .arg("-c")
        .arg("/usr/bin/env -0")
        .stderr(Stdio::null())
        .output();
    let mut map = HashMap::new();
    let Ok(output) = output else {
        return map;
    };
    if !output.status.success() {
        return map;
    }
    for entry in output.stdout.split(|byte| *byte == 0) {
        let text = String::from_utf8_lossy(entry);
        if let Some((key, value)) = text.split_once('=') {
            if !key.is_empty() && key != "_" && key != "PWD" {
                map.insert(key.to_string(), value.to_string());
            }
        }
    }
    map
}

#[tauri::command]
pub fn rpc_start(
    app: AppHandle,
    state: State<'_, RpcState>,
    options: StartOptions,
) -> Result<StartReport, StartError> {
    let mut slot = state.proc.lock().unwrap();
    if let Some(existing) = slot.take() {
        stop_proc(&existing);
    }

    let bin = resolve_bin(options.bin.as_deref().unwrap_or("zen"));
    let mut command = Command::new(&bin);
    command.arg("rpc").args(&options.args);
    if let Some(cwd) = &options.cwd {
        command.arg("--cwd").arg(cwd).current_dir(cwd);
    }
    command
        .envs(login_shell_env())
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    let mut child = command.spawn().map_err(|error| StartError {
        code: if matches!(
            error.kind(),
            std::io::ErrorKind::NotFound | std::io::ErrorKind::PermissionDenied
        ) {
            "runtime_missing"
        } else {
            "spawn_failed"
        },
        bin: bin.clone(),
        os_error: error.to_string(),
    })?;
    let pid = child.id();
    let stdin = child
        .stdin
        .take()
        .ok_or_else(|| StartError {
            code: "spawn_failed",
            bin: bin.clone(),
            os_error: "no stdin handle".to_string(),
        })?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| StartError {
            code: "spawn_failed",
            bin: bin.clone(),
            os_error: "no stdout handle".to_string(),
        })?;
    let stderr = child
        .stderr
        .take()
        .ok_or_else(|| StartError {
            code: "spawn_failed",
            bin: bin.clone(),
            os_error: "no stderr handle".to_string(),
        })?;

    let pending: Pending = Arc::new(Mutex::new(HashMap::new()));
    let alive = Arc::new(AtomicBool::new(true));
    let child = Arc::new(Mutex::new(child));

    // stdout: correlate result frames, forward everything else.
    {
        let pending = Arc::clone(&pending);
        let app = app.clone();
        let alive = Arc::clone(&alive);
        thread::spawn(move || {
            let reader = BufReader::new(stdout);
            for line in reader.lines() {
                let Ok(line) = line else { break };
                if line.trim().is_empty() {
                    continue;
                }
                let Ok(frame) = serde_json::from_str::<Value>(&line) else {
                    let _ = app.emit("rpc:stderr", serde_json::json!({ "line": line }));
                    continue;
                };
                let kind = frame.get("type").and_then(Value::as_str);
                if matches!(kind, Some("result") | Some("error")) {
                    let id = frame.get("id").map(|id| match id {
                        Value::String(text) => text.clone(),
                        other => other.to_string(),
                    });
                    if let Some(id) = id {
                        let tx = pending.lock().unwrap().remove(&id);
                        if let Some(tx) = tx {
                            let _ = tx.send(frame);
                            continue;
                        }
                    }
                }
                let _ = app.emit("rpc:frame", serde_json::json!({ "frame": frame }));
            }
            alive.store(false, Ordering::SeqCst);
        });
    }

    // stderr: diagnostics for the UI (zenagent: warnings land here).
    {
        let app = app.clone();
        thread::spawn(move || {
            let reader = BufReader::new(stderr);
            for line in reader.lines() {
                let Ok(line) = line else { break };
                let _ = app.emit("rpc:stderr", serde_json::json!({ "line": line }));
            }
        });
    }

    // Waiter: poll the shared child, surface the exit, fail pending callers.
    {
        let pending = Arc::clone(&pending);
        let app = app.clone();
        let child = Arc::clone(&child);
        let alive = Arc::clone(&alive);
        thread::spawn(move || loop {
            let status = {
                let mut guard = child.lock().unwrap();
                guard.try_wait()
            };
            match status {
                Ok(Some(status)) => {
                    alive.store(false, Ordering::SeqCst);
                    fail_all_pending(&pending, "the zen rpc process exited");
                    let _ = app.emit(
                        "rpc:exit",
                        serde_json::json!({ "code": status.code(), "pid": pid }),
                    );
                    break;
                }
                Ok(None) => thread::sleep(Duration::from_millis(150)),
                Err(_) => {
                    alive.store(false, Ordering::SeqCst);
                    break;
                }
            }
        });
    }

    let proc = Arc::new(Proc {
        child,
        stdin: Mutex::new(stdin),
        pending,
        alive,
    });
    *slot = Some(Arc::clone(&proc));
    Ok(StartReport { pid })
}

#[tauri::command]
pub async fn rpc_send(
    state: State<'_, RpcState>,
    payload: Value,
    timeout_ms: Option<u64>,
) -> Result<Value, String> {
    let proc = {
        let slot = state.proc.lock().unwrap();
        match slot.as_ref() {
            Some(proc) => Arc::clone(proc),
            None => return Err("no zen rpc process; call rpc_start first".into()),
        }
    };
    let request = payload;
    let id = match request.get("id") {
        Some(Value::String(text)) if !text.is_empty() => text.clone(),
        _ => return Err("request needs a string id".into()),
    };
    if !proc.alive.load(Ordering::SeqCst) {
        return Err("the zen rpc process is not running".into());
    }

    let line = request.to_string();
    let (guard, rx) = Guard::register(&proc.pending, id.clone());
    {
        let mut stdin = proc.stdin.lock().unwrap();
        writeln!(stdin, "{line}").map_err(|error| format!("stdin write failed: {error}"))?;
        stdin
            .flush()
            .map_err(|error| format!("stdin flush failed: {error}"))?;
    }

    let timeout = Duration::from_millis(timeout_ms.unwrap_or(900_000));
    tauri::async_runtime::spawn_blocking(move || {
        match rx.recv_timeout(timeout) {
            Ok(frame) => Ok(frame),
            Err(_) => {
                drop(guard);
                Err(format!("no answer within timeout for request {id}"))
            }
        }
    })
    .await
    .map_err(|error| format!("rpc_send join failed: {error}"))?
}

#[tauri::command]
pub fn rpc_stop(state: State<'_, RpcState>) -> Result<(), String> {
    let slot = state.proc.lock().unwrap();
    if let Some(proc) = slot.as_ref() {
        stop_proc(proc);
    }
    Ok(())
}

/// Best-effort teardown on app exit.
/// Restart the shell after an update installed itself. The child `zen rpc`
/// dies with the process, so the new bundle and its sidecar start together.
#[tauri::command]
pub fn relaunch_app(app: AppHandle) {
    app.restart();
}

/// True when the environment asks every zen frontend to stop checking for
/// updates. Kept alongside the settings switch: either one silences it.
#[tauri::command]
pub fn updates_disabled_by_env() -> bool {
    let value = std::env::var("ZENAGENT_NO_UPDATE_CHECK").unwrap_or_default();
    matches!(value.trim().to_lowercase().as_str(), "1" | "true" | "yes")
}

/// Where the shell would look for the runtime, and what it reports there.
/// Settings shows this so a user can tell a missing sidecar from a broken one.
#[tauri::command]
pub fn runtime_probe() -> RuntimeProbe {
    let resolved = resolve_bin("zen");
    let exists = Path::new(&resolved).is_file();
    let version = if exists {
        Command::new(&resolved)
            .arg("--version")
            .output()
            .ok()
            .filter(|output| output.status.success())
            .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_string())
            .filter(|text| !text.is_empty())
    } else {
        None
    };
    RuntimeProbe {
        resolved: Some(resolved),
        exists,
        version,
    }
}

pub fn kill_all(state: &RpcState) {
    if let Some(proc) = state.proc.lock().unwrap().take() {
        proc.alive.store(false, Ordering::SeqCst);
        let mut child = proc.child.lock().unwrap();
        kill_quiet(&mut child);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn candidate_paths_puts_the_executable_sibling_first() {
        let paths = candidate_paths(
            "zen",
            Some(Path::new("/Applications/ZenAgent.app/Contents/MacOS")),
            Some("/Users/u"),
        );
        assert_eq!(paths[0], "/Applications/ZenAgent.app/Contents/MacOS/zen");
        assert_eq!(paths[1], "/Users/u/.local/bin/zen");
        assert_eq!(paths[2], "/opt/homebrew/bin/zen");
        assert_eq!(paths[3], "/usr/local/bin/zen");
    }

    #[test]
    fn candidate_paths_skips_what_it_does_not_know() {
        assert_eq!(
            candidate_paths("zen", None, None),
            vec!["/opt/homebrew/bin/zen", "/usr/local/bin/zen"]
        );
    }

    #[test]
    fn candidate_paths_keeps_the_windows_extension() {
        let paths = candidate_paths("zen.exe", Some(Path::new("C:\\Zen")), None);
        assert!(paths[0].contains("Zen"));
        assert!(paths[0].ends_with("zen.exe"));
    }
}
