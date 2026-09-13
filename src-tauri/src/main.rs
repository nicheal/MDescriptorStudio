// MDescriptor Studio desktop shell (Tauri 2).
// Spawns the Python sidecar and exposes a typed, request-oriented IPC bridge.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::collections::HashSet;
use std::fs::{self, File};
use std::io::{self, BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;

use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::sync::atomic::{AtomicU64, Ordering};
use tauri::{Emitter, EventTarget, Manager};

const PROTOCOL_VERSION: u64 = 1;
const MAX_FRAME_BYTES: usize = 8 * 1024 * 1024;
const MAX_PENDING_REQUESTS: usize = 1024;
const BACKEND_EVENT: &str = "backend-message";
const BACKEND_EXIT_EVENT: &str = "backend-exit";
const MAIN_WEBVIEW: &str = "main";
const EMBEDDED_BUNDLE_SHA256: Option<&str> = option_env!("MDS_BACKEND_BUNDLE_SHA256");

// Request ids only need to be unique among pending requests, which are
// cleared on restart, so a monotonic counter cannot collide within a run.
static NEXT_REQUEST_ID: AtomicU64 = AtomicU64::new(1);

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
        let candidate = NEXT_REQUEST_ID.fetch_add(1, Ordering::Relaxed);
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
    hide_backend_console(&mut command);
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

fn clean_command_environment(command: &mut Command, temp_dir: &Path) {
    for (key, _) in std::env::vars() {
        let upper = key.to_ascii_uppercase();
        if matches!(
            upper.as_str(),
            "PYTHONPATH" | "PYTHONHOME" | "PYTHONSTARTUP" | "MDS_DATA_DIR"
        ) || upper.starts_with("PIP_")
        {
            command.env_remove(key);
        }
    }
    // Keep backend scratch files (tempfile users like dpdata) inside the
    // app-local directory instead of the shared user TEMP. One variable set
    // consistently, because the runtimes that read these names differ.
    command
        .env("TEMP", temp_dir)
        .env("TMP", temp_dir)
        .env("TMPDIR", temp_dir);
}

#[cfg(target_os = "windows")]
fn hide_backend_console(command: &mut Command) {
    use std::os::windows::process::CommandExt;

    // Keep the console executable so redirected stdio remains available, but
    // do not create a visible console window for the desktop child process.
    command.creation_flags(0x0800_0000);
}

#[cfg(not(target_os = "windows"))]
fn hide_backend_console(_command: &mut Command) {}

fn backend_temp_dir() -> Result<PathBuf, String> {
    #[cfg(target_os = "windows")]
    let root = PathBuf::from(
        std::env::var_os("LOCALAPPDATA")
            .ok_or_else(|| "LOCALAPPDATA is unavailable".to_string())?,
    )
    .join("MDescriptorStudio");
    #[cfg(not(target_os = "windows"))]
    let root = std::env::temp_dir().join("MDescriptorStudio");

    let temp_dir = root.join("backend-temp");
    fs::create_dir_all(&temp_dir).map_err(|error| {
        format!(
            "could not create backend temp directory {}: {error}",
            temp_dir.display()
        )
    })?;
    Ok(temp_dir)
}

fn backend_command() -> Result<(Command, &'static str), String> {
    let temp_dir = backend_temp_dir()?;

    // A release build must use a verified bundled backend. Falling back to a
    // developer Python environment would make the released trust boundary
    // depend on the user's PATH and site packages.
    if !cfg!(debug_assertions) {
        let exe = std::env::current_exe().map_err(|error| error.to_string())?;
        let dir = exe
            .parent()
            .ok_or_else(|| "application directory is unavailable".to_string())?;
        let bundle = dir.join("backend");
        if bundle.join("backend.exe").is_file() {
            verify_backend_bundle(&bundle, EMBEDDED_BUNDLE_SHA256)?;
            let mut command = Command::new(bundle.join("backend.exe"));
            clean_command_environment(&mut command, &temp_dir);
            return Ok((command, "verified-sidecar"));
        }
        return Err("verified backend bundle was not found".into());
    }

    // dev: project .venv python running the backend package from ../backend
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")); // .../src-tauri
    let repo = manifest_dir
        .parent()
        .ok_or_else(|| "repository root is unavailable".to_string())?;
    let python = repo.join(".venv").join("Scripts").join("python.exe");
    let backend_dir = repo.join("backend");
    let mut command = Command::new(python);
    clean_command_environment(&mut command, &temp_dir);
    command
        .args(["-m", "mdescriptor_studio_backend"])
        .current_dir(backend_dir)
        .env("PYTHONNOUSERSITE", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUTF8", "1");
    Ok((command, "dev-python"))
}

fn verify_backend_bundle(bundle: &Path, expected: Option<&str>) -> Result<(), String> {
    let expected = expected
        .map(str::trim)
        .filter(|value| value.len() == 64 && value.chars().all(|character| character.is_ascii_hexdigit()))
        .map(str::to_ascii_lowercase)
        .ok_or_else(|| "embedded backend bundle hash is missing or malformed".to_string())?;

    // The build embeds the manifest's SHA-256; matching it first makes the
    // per-file entries below trustworthy even though they travel in the clear.
    let manifest_path = bundle.join("backend-manifest.json");
    let manifest = fs::read(&manifest_path)
        .map_err(|error| format!("could not read backend bundle manifest: {error}"))?;
    if format!("{:x}", Sha256::digest(&manifest)) != expected {
        return Err(format!(
            "backend bundle manifest hash mismatch: {}",
            manifest_path.display()
        ));
    }
    let entries: Vec<Value> = serde_json::from_slice(&manifest)
        .map_err(|error| format!("backend bundle manifest is malformed: {error}"))?;

    // Every file, every launch: the release replaces the onefile exe hash with
    // a whole-bundle hash. ~1s for the ~470 MB bundle, paid instead of the
    // several-second onefile extraction it removes.
    let mut buffer = vec![0_u8; 1024 * 1024];
    for entry in &entries {
        let path = entry
            .get("path")
            .and_then(Value::as_str)
            .ok_or_else(|| "backend bundle manifest entry is missing 'path'".to_string())?;
        let size = entry
            .get("size")
            .and_then(Value::as_u64)
            .ok_or_else(|| format!("backend bundle manifest entry {path} is missing 'size'"))?;
        let expected_hash = entry
            .get("sha256")
            .and_then(Value::as_str)
            .ok_or_else(|| format!("backend bundle manifest entry {path} is missing 'sha256'"))?;
        if path.is_empty() || path.split('/').any(|segment| segment.is_empty() || segment == "..") {
            return Err("backend bundle manifest contains an unsafe path".to_string());
        }
        let file_path = bundle.join(path);
        let metadata = fs::metadata(&file_path)
            .map_err(|_| format!("backend bundle is missing {path}"))?;
        if !metadata.is_file() || metadata.len() != size {
            return Err(format!("backend bundle file {path} has an unexpected size"));
        }
        let mut file = File::open(&file_path)
            .map_err(|error| format!("could not open backend bundle file {path}: {error}"))?;
        let mut hasher = Sha256::new();
        loop {
            let count = file
                .read(&mut buffer)
                .map_err(|error| format!("could not hash backend bundle file {path}: {error}"))?;
            if count == 0 {
                break;
            }
            hasher.update(&buffer[..count]);
        }
        if format!("{:x}", hasher.finalize()) != expected_hash {
            return Err(format!("backend bundle file {path} hash mismatch"));
        }
    }
    Ok(())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
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

#[cfg(test)]
mod tests {
    use super::*;

    fn write_bundle(dir: &Path, files: &[(&str, &[u8])]) -> String {
        let entries: Vec<Value> = files
            .iter()
            .map(|(path, contents)| {
                let file_path = dir.join(path);
                fs::create_dir_all(file_path.parent().unwrap()).unwrap();
                fs::write(&file_path, contents).unwrap();
                json!({
                    "path": path,
                    "size": contents.len(),
                    "sha256": format!("{:x}", Sha256::digest(*contents)),
                })
            })
            .collect();
        let manifest = serde_json::to_vec(&entries).unwrap();
        fs::write(dir.join("backend-manifest.json"), &manifest).unwrap();
        format!("{:x}", Sha256::digest(&manifest))
    }

    #[test]
    fn bundle_verification_fits_a_small_stack_and_rejects_mismatches() {
        let root = std::env::temp_dir().join(format!("mds-bundle-test-{}", std::process::id()));
        thread::Builder::new()
            .stack_size(256 * 1024)
            .spawn(move || {
                fs::create_dir_all(&root).unwrap();
                let expected = write_bundle(&root, &[("backend.exe", b"bootloader"), ("_internal/app.py", b"print(1)")]);
                assert!(verify_backend_bundle(&root, Some(&expected)).is_ok());
                assert!(verify_backend_bundle(&root, Some(&"0".repeat(64))).is_err());
                assert!(verify_backend_bundle(&root, None).is_err());
                // a same-length tamper in a bundled file is caught per entry
                fs::write(root.join("_internal/app.py"), b"print(2)").unwrap();
                assert!(verify_backend_bundle(&root, Some(&expected)).is_err());
                drop(fs::remove_dir_all(&root));
            })
            .unwrap()
            .join()
            .unwrap();
    }
}
