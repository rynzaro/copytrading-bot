//! Bakes denylist.json (copy of worker/copytrade_worker/denylist.json) into the wasm as constants.
use std::{env, fs, path::Path};

fn main() {
    println!("cargo:rerun-if-changed=denylist.json");
    let raw = fs::read_to_string("denylist.json").expect("denylist.json");
    let v: serde_json::Value = serde_json::from_str(&raw).expect("denylist.json is JSON");
    let keys = |k: &str| -> Vec<String> {
        let mut out: Vec<String> = v[k].as_object().expect(k).keys().cloned().collect();
        out.sort();
        out
    };
    let (exact, suffix) = (keys("exact"), keys("suffix"));
    assert!(exact.iter().any(|t| t == "wrap.near"), "denylist must contain wrap.near");
    for s in &suffix {
        assert!(s.starts_with('.'), "suffix must start with '.': {s}");
    }
    let code =
        format!("pub const DENY_EXACT: &[&str] = &{:?};\npub const DENY_SUFFIX: &[&str] = &{:?};\n", exact, suffix);
    fs::write(Path::new(&env::var("OUT_DIR").unwrap()).join("denylist.rs"), code).unwrap();
}
