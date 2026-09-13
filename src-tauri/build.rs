use std::fs;
use std::path::Path;

fn main() {
    // SHA-256 of src-tauri/resources/backend/backend-manifest.json, generated
    // by scripts/prepare_sidecar.ps1 next to the onedir backend bundle.
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("binaries/backend-bundle.sha256");
    println!("cargo:rerun-if-changed={}", manifest.display());
    if let Ok(contents) = fs::read_to_string(&manifest) {
        if let Some(hash) = contents.split_whitespace().next().filter(|value| {
            value.len() == 64 && value.chars().all(|character| character.is_ascii_hexdigit())
        }) {
            println!("cargo:rustc-env=MDS_BACKEND_BUNDLE_SHA256={hash}");
        } else {
            println!("cargo:warning=backend bundle hash file is malformed");
        }
    } else {
        println!("cargo:warning=backend bundle hash file is absent; release startup will fail closed");
    }
    tauri_build::build()
}
