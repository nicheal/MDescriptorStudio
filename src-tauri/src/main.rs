// MDescriptor Studio desktop shell (Tauri 2).
// Spawns the Python sidecar (dev: project .venv python; release: bundled
// backend exe), bridges NDJSON lines between webview and sidecar stdio.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;

use tauri::{Emitter, Manager};

struct BackendState {
    child: Mutex<Option<Child>>,
    // backend.ready line cache: the webview may attach its listener after the
    // line was emitted, so it pulls the snapshot via backend_ready_line().
    ready_line: Mutex<Option<String>>,
}

#[tauri::command]
fn backend_send(state: tauri::State<BackendState>, line: String) -> Result<(), String> {
    let mut guard = state.child.lock().map_err(|e| e.to_string())?;
    match guard.as_mut() {
        Some(child) => {
            let stdin = child.stdin.as_mut().ok_or("backend stdin closed")?;
            stdin
                .write_all(line.as_bytes())
                .and_then(|_| stdin.write_all(b"\n"))
                .and_then(|_| stdin.flush())
                .map_err(|e| format!("backend write failed: {e}"))
        }
        None => Err("backend not running".into()),
    }
}

#[tauri::command]
fn backend_ready_line(state: tauri::State<BackendState>) -> Option<String> {
    state.ready_line.lock().ok().and_then(|g| g.clone())
}

#[tauri::command]
fn backend_restart(app: tauri::AppHandle, state: tauri::State<BackendState>) -> Result<(), String> {
    kill_backend(&state);
    if let Ok(mut r) = state.ready_line.lock() {
        *r = None;
    }
    spawn_backend(&app);
    Ok(())
}

fn kill_backend(state: &BackendState) {
    if let Ok(mut guard) = state.child.lock() {
        if let Some(mut child) = guard.take() {
            // graceful: close stdin, give it a moment, then kill
            drop(child.stdin.take());
            let deadline = std::time::Instant::now() + std::time::Duration::from_secs(2);
            while child.try_wait().map(|o| o.is_none()).unwrap_or(false) {
                if std::time::Instant::now() > deadline {
                    let _ = child.kill();
                    break;
                }
                thread::sleep(std::time::Duration::from_millis(50));
            }
        }
    }
}

fn spawn_backend(app: &tauri::AppHandle) {
    let state = app.state::<BackendState>();
    let (mut cmd, label) = backend_command();
    cmd.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null());
    println!("spawning backend ({label})");
    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("backend spawn failed: {e}");
            let _ = app.emit("backend-exit", ());
            return;
        }
    };
    let stdout = child.stdout.take().expect("stdout piped");
    let handle = app.clone();
    thread::spawn(move || {
        let reader = BufReader::new(stdout);
        for line in reader.lines() {
            match line {
                Ok(l) => {
                    if l.contains("\"backend.ready\"") {
                        if let Ok(mut r) = handle.state::<BackendState>().ready_line.lock() {
                            *r = Some(l.clone());
                        }
                    }
                    if handle.emit("backend-message", l).is_err() {
                        break;
                    }
                }
                Err(_) => break,
            }
        }
        let _ = handle.emit("backend-exit", ());
    });
    *state.child.lock().expect("backend state poisoned") = Some(child);
}

fn backend_command() -> (Command, &'static str) {
    if let Ok(exe) = std::env::current_exe() {
        let dir = exe.parent().unwrap().to_path_buf();
        // release layout: bundled externalBin sits next to the main executable
        let sidecar = dir.join(format!("backend-{TRIPLE}.exe"));
        if sidecar.exists() {
            let mut c = Command::new(sidecar);
            if let Ok(data_dir) = std::env::var("MDS_DATA_DIR") {
                if !data_dir.is_empty() {
                    c.env("MDS_DATA_DIR", data_dir);
                }
            }
            return (c, "sidecar");
        }
    }
    // dev: project .venv python running the backend package from ../backend
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")); // .../src-tauri
    let repo = manifest_dir.parent().expect("repo root");
    let python = repo.join(".venv").join("Scripts").join("python.exe");
    let backend_dir = repo.join("backend");
    let mut c = Command::new(python);
    c.args(["-m", "mdescriptor_studio_backend"])
        .current_dir(&backend_dir)
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUTF8", "1");
    (c, "dev-python")
}

const TRIPLE: &str = "x86_64-pc-windows-msvc";

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(BackendState {
            child: Mutex::new(None),
            ready_line: Mutex::new(None),
        })
        .invoke_handler(tauri::generate_handler![
            backend_send,
            backend_restart,
            backend_ready_line
        ])
        .setup(|app| {
            spawn_backend(app.handle());
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                let state: tauri::State<BackendState> = window.app_handle().state();
                kill_backend(&state);
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
