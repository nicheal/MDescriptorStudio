// MDescriptor Studio desktop shell (Tauri 2).
// Spawns the Python sidecar and exposes a typed, request-oriented IPC bridge.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::collections::HashSet;
use std::fs::File;
use std::io::{self, BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;

use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tauri::{Emitter, EventTarget, Manager};
use uuid::Uuid;

const PROTOCOL_VERSION: u64 = 1;
const MAX_FRAME_BYTES: usize = 8 * 1024 * 1024;
const MAX_PENDING_REQUESTS: usize = 1024;
const MAX_REQUEST_ID: u64 = (1 << 53) - 1;
const TRIPLE: &str = "x86_64-pc-windows-msvc";
const BACKEND_EVENT: &str = "backend-message";
const BACKEND_EXIT_EVENT: &str = "backend-exit";
const MAIN_WEBVIEW: &str = "main";
const EMBEDDED_SIDECAR_SHA256: Option<&str> = option_env!("MDS_SIDECAR_SHA256");

struct BackendState {
    child: Mutex<Option<Child>>,
    // backend.ready line cache: the webview may attach its listener after the
    // line was emitted, so it pulls the snapshot via backend_ready_line().
    ready_line: Mutex<Option<String>>,
    // Only response IDs allocated by backend_request are forwarded to the
    // webview. This prevents arbitrary sidecar stdout from becoming a browser
    // event.
    pending_ids: Mutex<HashSet<u64>>,
}

#[tauri::command]
fn backend_request(
    state: tauri::State<BackendState>,
    method: String,
    params: Value,
) -> Result<u64, String> {
    if method.is_empty()
        || method.len() > 256
        || method.chars().any(|character| character.is_control())
    {
        return Err("invalid backend method".into());
    }
    if !params.is_object() {
        return Err("backend params must be an object".into());
    }

    let id = {
        let mut pending = state
            .pending_ids
            .lock()
            .map_err(|_| "backend request state poisoned".to_string())?;
        if pending.len() >= MAX_PENDING_REQUESTS {
            return Err("backend request queue is full".into());
        }
        let mut candidate = (Uuid::new_v4().as_u128() as u64) & MAX_REQUEST_ID;
        if candidate == 0 {
            candidate = 1;
        }
        while pending.contains(&candidate) {
            candidate = (candidate + 1) & MAX_REQUEST_ID;
            if candidate == 0 {
                candidate = 1;
            }
        }
        pending.insert(candidate);
        candidate
    };

    let frame = json!({
        "protocol_version": PROTOCOL_VERSION,
        "id": id,
        "method": method,
        "params": params,
    });
    let mut encoded =
        serde_json::to_vec(&frame).map_err(|_| "could not encode backend request".to_string())?;
    encoded.push(b'\n');
    if encoded.len() > MAX_FRAME_BYTES + 1 {
        remove_pending(&state, id);
        return Err("backend request is too large".into());
    }

    let write_result = (|| {
        let mut guard = state
            .child
            .lock()
            .map_err(|_| "backend state poisoned".to_string())?;
        let child = guard
            .as_mut()
            .ok_or_else(|| "backend not running".to_string())?;
        let stdin = child
            .stdin
            .as_mut()
            .ok_or_else(|| "backend stdin closed".to_string())?;
        stdin
            .write_all(&encoded)
            .and_then(|_| stdin.flush())
            .map_err(|error| format!("backend write failed: {error}"))
    })();
    if write_result.is_err() {
        remove_pending(&state, id);
    }
    write_result.map(|_| id)
}

#[tauri::command]
fn backend_ready_line(state: tauri::State<BackendState>) -> Option<String> {
    state.ready_line.lock().ok().and_then(|guard| guard.clone())
}

#[tauri::command]
fn backend_restart(app: tauri::AppHandle, state: tauri::State<BackendState>) -> Result<(), String> {
    kill_backend(&state);
    clear_pending(&state);
    if let Ok(mut ready) = state.ready_line.lock() {
        *ready = None;
    }
    let _ = app.emit_to(
        EventTarget::webview_window(MAIN_WEBVIEW),
        BACKEND_EXIT_EVENT,
        (),
    );
    spawn_backend(&app);
    Ok(())
}

fn remove_pending(state: &BackendState, id: u64) {
    if let Ok(mut pending) = state.pending_ids.lock() {
        pending.remove(&id);
    }
}

fn clear_pending(state: &BackendState) {
    if let Ok(mut pending) = state.pending_ids.lock() {
        pending.clear();
    }
}

fn kill_backend(state: &BackendState) {
    if let Ok(mut guard) = state.child.lock() {
        if let Some(mut child) = guard.take() {
            // graceful: close stdin, give it a moment, then kill
            drop(child.stdin.take());
            let deadline = std::time::Instant::now() + std::time::Duration::from_secs(2);
            while child
                .try_wait()
                .map(|status| status.is_none())
                .unwrap_or(false)
            {
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
    let (mut command, label) = match backend_command() {
        Ok(command) => command,
        Err(error) => {
            eprintln!("backend command rejected: {error}");
            let _ = app.emit_to(
                EventTarget::webview_window(MAIN_WEBVIEW),
                BACKEND_EXIT_EVENT,
                (),
            );
            return;
        }
    };
    command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    println!("spawning backend ({label})");
    let mut child = match command.spawn() {
        Ok(child) => child,
        Err(error) => {
            eprintln!("backend spawn failed: {error}");
            let _ = app.emit_to(
                EventTarget::webview_window(MAIN_WEBVIEW),
                BACKEND_EXIT_EVENT,
                (),
            );
            return;
        }
    };
    let stdout = child.stdout.take().expect("stdout piped");
    let child_id = child.id();
    *state.child.lock().expect("backend state poisoned") = Some(child);

    let handle = app.clone();
    thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        loop {
            let line = match read_line_bounded(&mut reader, MAX_FRAME_BYTES + 1) {
                Ok(Some(bytes)) => match String::from_utf8(bytes) {
                    Ok(line) => line.trim_end_matches(['\r', '\n']).to_string(),
                    Err(_) => {
                        eprintln!("backend emitted non-UTF-8 output");
                        break;
                    }
                },
                Ok(None) => break,
                Err(error) => {
                    eprintln!("backend output rejected: {error}");
                    break;
                }
            };
            if line.is_empty() {
                continue;
            }
            route_backend_line(&handle, line);
        }

        let should_notify = if let Ok(mut guard) = handle.state::<BackendState>().child.lock() {
            // The reader owns the authoritative exit transition. Avoid
            // replacing a newly spawned child if restart raced with EOF.
            if guard.as_ref().map(|child| child.id()) == Some(child_id) {
                *guard = None;
                true
            } else {
                false
            }
        } else {
            true
        };
        if should_notify {
            clear_pending(&handle.state::<BackendState>());
            let _ = handle.emit_to(
                EventTarget::webview_window(MAIN_WEBVIEW),
                BACKEND_EXIT_EVENT,
                (),
            );
        }
    });
}

fn route_backend_line(handle: &tauri::AppHandle, line: String) {
    let Ok(frame) = serde_json::from_str::<Value>(&line) else {
        eprintln!("backend emitted a non-JSON frame");
        return;
    };
    if frame.get("protocol_version").and_then(Value::as_u64) != Some(PROTOCOL_VERSION) {
        eprintln!("backend emitted an unsupported protocol frame");
        return;
    }

    if let Some(event) = frame.get("event").and_then(Value::as_str) {
        if event == "backend.ready" {
            if let Ok(mut ready) = handle.state::<BackendState>().ready_line.lock() {
                *ready = Some(line.clone());
            }
        }
        let _ = handle.emit_to(
            EventTarget::webview_window(MAIN_WEBVIEW),
            BACKEND_EVENT,
            line,
        );
        return;
    }

    let Some(id) = frame.get("id").and_then(Value::as_u64) else {
        eprintln!("backend emitted an unaddressed frame");
        return;
    };
    if id > MAX_REQUEST_ID {
        return;
    }
    let is_pending = handle
        .state::<BackendState>()
        .pending_ids
        .lock()
        .map(|mut pending| pending.remove(&id))
        .unwrap_or(false);
    if is_pending {
        let _ = handle.emit_to(
            EventTarget::webview_window(MAIN_WEBVIEW),
            BACKEND_EVENT,
            line,
        );
    }
}

fn read_line_bounded<R: BufRead>(
    reader: &mut R,
    max_bytes_including_newline: usize,
) -> io::Result<Option<Vec<u8>>> {
    let mut output = Vec::new();
    loop {
        let available = reader.fill_buf()?;
        if available.is_empty() {
            return if output.is_empty() {
                Ok(None)
            } else {
                Ok(Some(output))
            };
        }
        let take = available
            .iter()
            .position(|byte| *byte == b'\n')
            .map(|index| index + 1)
            .unwrap_or(available.len());
        if output.len() + take > max_bytes_including_newline {
            reader.consume(take);
            loop {
                let rest = reader.fill_buf()?;
                if rest.is_empty() {
                    break;
                }
                let discard = rest
                    .iter()
                    .position(|byte| *byte == b'\n')
                    .map(|index| index + 1)
                    .unwrap_or(rest.len());
                let has_newline = rest[..discard].contains(&b'\n');
                reader.consume(discard);
                if has_newline {
                    break;
                }
            }
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "backend frame exceeds the size limit",
            ));
        }
        output.extend_from_slice(&available[..take]);
        reader.consume(take);
        if output.last() == Some(&b'\n') {
            return Ok(Some(output));
        }
    }
}

fn clean_command_environment(command: &mut Command) {
    for (key, _) in std::env::vars() {
        let upper = key.to_ascii_uppercase();
        if matches!(
            upper.as_str(),
            "PYTHONPATH"
                | "PYTHONHOME"
                | "PYTHONSTARTUP"
                | "MDS_DATA_DIR"
                | "TEMP"
                | "TMP"
                | "TMPDIR"
        ) || upper.starts_with("PIP_")
        {
            command.env_remove(key);
        }
    }
}

fn backend_command() -> Result<(Command, &'static str), String> {
    // A release build must use a verified bundled sidecar. Falling back to a
    // developer Python environment would make the released trust boundary
    // depend on the user's PATH and site packages.
    if !cfg!(debug_assertions) {
        let exe = std::env::current_exe().map_err(|error| error.to_string())?;
        let dir = exe
            .parent()
            .ok_or_else(|| "application directory is unavailable".to_string())?;
        for name in [format!("backend-{TRIPLE}.exe"), "backend.exe".to_string()] {
            let sidecar = dir.join(&name);
            if sidecar.is_file() {
                verify_sidecar(&sidecar, EMBEDDED_SIDECAR_SHA256)?;
                let mut command = Command::new(&sidecar);
                clean_command_environment(&mut command);
                return Ok((command, "verified-sidecar"));
            }
        }
        return Err("verified backend sidecar was not found".into());
    }

    // dev: project .venv python running the backend package from ../backend
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")); // .../src-tauri
    let repo = manifest_dir
        .parent()
        .ok_or_else(|| "repository root is unavailable".to_string())?;
    let python = repo.join(".venv").join("Scripts").join("python.exe");
    let backend_dir = repo.join("backend");
    let mut command = Command::new(python);
    clean_command_environment(&mut command);
    command
        .args(["-m", "mdescriptor_studio_backend"])
        .current_dir(backend_dir)
        .env("PYTHONNOUSERSITE", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUTF8", "1");
    Ok((command, "dev-python"))
}

fn verify_sidecar(sidecar: &Path, expected: Option<&str>) -> Result<(), String> {
    let expected = expected
        .map(str::trim)
        .filter(|value| {
            value.len() == 64 && value.chars().all(|character| character.is_ascii_hexdigit())
        })
        .map(str::to_ascii_lowercase)
        .ok_or_else(|| "embedded sidecar hash is missing or malformed".to_string())?;
    if expected.len() != 64
        || !expected
            .chars()
            .all(|character| character.is_ascii_hexdigit())
    {
        return Err("sidecar hash manifest is malformed".into());
    }

    let mut file =
        File::open(sidecar).map_err(|error| format!("could not open backend sidecar: {error}"))?;
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 1024 * 1024];
    loop {
        let count = file
            .read(&mut buffer)
            .map_err(|error| format!("could not hash backend sidecar: {error}"))?;
        if count == 0 {
            break;
        }
        hasher.update(&buffer[..count]);
    }
    let actual = format!("{:x}", hasher.finalize());
    if actual != expected {
        return Err(format!(
            "backend sidecar hash mismatch: {}",
            sidecar.display()
        ));
    }
    Ok(())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(BackendState {
            child: Mutex::new(None),
            ready_line: Mutex::new(None),
            pending_ids: Mutex::new(HashSet::new()),
        })
        .invoke_handler(tauri::generate_handler![
            backend_request,
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
                clear_pending(&state);
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
