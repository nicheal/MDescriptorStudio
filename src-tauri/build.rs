use std::fs;
use std::path::Path;

fn main() {
    // SHA-256 of src-tauri/resources/backend/backend-manifest.json, generated
    // by scripts/prepare_sidecar.ps1 next to the onedir backend bundle. The
    // launcher compares it against the bundled files at startup, so a release
    // without it can only ever fail closed on the user's machine: stop the
    // build instead. Debug builds keep working from a plain `cargo run`.
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR")).join("binaries/backend-bundle.sha256");
    println!("cargo:rerun-if-changed={}", manifest.display());
    let hash = fs::read_to_string(&manifest).ok().and_then(|contents| {
        contents
            .split_whitespace()
            .find(|value| {
                value.len() == 64 && value.chars().all(|character| character.is_ascii_hexdigit())
            })
            .map(str::to_owned)
    });
    match hash {
        Some(hash) => println!("cargo:rustc-env=MDS_BACKEND_BUNDLE_SHA256={hash}"),
        None if std::env::var("PROFILE").as_deref() == Ok("release") => panic!(
            "{} is missing or malformed; run scripts/prepare_sidecar.ps1 before the release build",
            manifest.display()
        ),
        None => println!("cargo:warning=backend bundle hash file is absent; startup will fail closed"),
    }

    tauri_build::build()
}
