//! The hand-written example vault follows every kernel rule, and its generated files are current.
//! `EDDA_WRITE_EXAMPLES=1 cargo test -p edda-store --test example_vault` regenerates them.

use std::path::PathBuf;

use edda_store::{Vault, render_index, render_log};

fn example() -> Vault {
    Vault::open(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../examples/vault")).unwrap()
}

#[test]
fn every_page_passes_the_kernel_checks() {
    let vault = example();
    let snapshot = vault.snapshot().unwrap();
    let pages = vault.pages().unwrap();
    assert_eq!(pages.len(), 4);
    for page in &pages {
        assert_eq!(page.check(&snapshot), [], "{}", page.id);
    }
}

#[test]
fn every_raw_source_matches_its_hash() {
    let raw = example().raw();
    let entries = raw.list().unwrap();
    assert_eq!(entries.len(), 3);
    for entry in &entries {
        raw.read(entry).unwrap();
        assert!(entry.meta.title.is_some(), "{} has no title", entry.hash);
    }
}

#[test]
fn generated_files_are_current() {
    let vault = example();
    if std::env::var_os("EDDA_WRITE_EXAMPLES").is_some() {
        vault.write_index().unwrap();
        vault.write_log(&[]).unwrap();
    }
    let read = |name| std::fs::read_to_string(vault.wiki_dir().join(name)).unwrap();
    assert_eq!(
        read("index.md"),
        render_index(&vault.config().title, &vault.pages().unwrap())
    );
    assert_eq!(read("log.md"), render_log(&[]));
}
