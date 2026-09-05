use std::fs;
use std::path::Path;

fn main() {
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("binaries/backend-x86_64-pc-windows-msvc.exe.sha256");
    println!("cargo:rerun-if-changed={}", manifest.display());
    if let Ok(contents) = fs::read_to_string(&manifest) {
        if let Some(hash) = contents.split_whitespace().next().filter(|value| {
            value.len() == 64 && value.chars().all(|character| character.is_ascii_hexdigit())
        }) {
            println!("cargo:rustc-env=MDS_SIDECAR_SHA256={hash}");
        } else {
            println!("cargo:warning=sidecar hash manifest is malformed");
        }
    } else {
        println!("cargo:warning=sidecar hash manifest is absent; release startup will fail closed");
    }
    tauri_build::build()
}
