//! The example vault's journal verifies, records the hand-written history of its pages, and `log.md` is generated
//! from it. `EDDA_WRITE_EXAMPLES=1 cargo test -p edda-journal --test example_vault` rewrites both.

use std::fs;
use std::path::PathBuf;

use edda_journal::{Journal, NewEntry, write_log};
use edda_store::{Vault, render_log};
use serde_json::json;

fn example() -> Vault {
    Vault::open(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../examples/vault")).unwrap()
}

/// The history the example vault's pages and sources were written with, oldest first.
fn history() -> Vec<NewEntry> {
    let source = |at: &str, hash: &str, title: &str| NewEntry {
        at: Some(at.into()),
        actor: "owner".into(),
        action: "add_source".into(),
        summary: format!("added source {title}"),
        data: json!({ "hash": hash }),
        ..NewEntry::default()
    };
    let page = |at: &str, action: &str, page: &str, summary: &str| NewEntry {
        at: Some(at.into()),
        actor: "owner".into(),
        action: action.into(),
        page: Some(page.into()),
        rev: Some(1),
        summary: summary.into(),
        ..NewEntry::default()
    };
    vec![
        source(
            "2026-10-21T13:40:00Z",
            "887e1bdcdcebb311e0cf0502d78b69a47b68ce78e359e22abc3dc8e182c0b1b4",
            "Reading notes: attention",
        ),
        source(
            "2026-10-21T13:45:00Z",
            "d9ca93c36fc0b371c30db895a9092fb9963e5b9d2c12d9122cea3676acce1444",
            "Notes on Attention Is All You Need",
        ),
        source(
            "2026-10-21T13:50:00Z",
            "d4d4516d4e3d5c860101b0b41d59f7516646aec14bb11b274e14df4addf82e16",
            "BM25 in one page",
        ),
        page(
            "2026-10-21T14:00:00Z",
            "create_page",
            "attention.md",
            "created",
        ),
        page(
            "2026-10-21T14:02:00Z",
            "create_page",
            "transformer.md",
            "created",
        ),
        page(
            "2026-10-21T14:04:00Z",
            "create_page",
            "papers/attention-is-all-you-need.md",
            "created",
        ),
        page("2026-10-21T14:10:00Z", "verify", "attention.md", "verified"),
        page(
            "2026-10-21T14:20:00Z",
            "create_page",
            "bm25.md",
            "created as a draft",
        ),
    ]
}

fn rewrite(vault: &Vault) {
    let dir = vault.journal_dir();
    for entry in fs::read_dir(&dir).unwrap() {
        let path = entry.unwrap().path();
        if path.extension().is_some_and(|e| e == "jsonl") {
            fs::remove_file(path).unwrap();
        }
    }
    let mut journal = Journal::open(&dir).unwrap();
    for entry in history() {
        journal.append(entry).unwrap();
    }
    write_log(vault).unwrap();
}

#[test]
fn journal_verifies_and_log_md_is_current() {
    let vault = example();
    if std::env::var_os("EDDA_WRITE_EXAMPLES").is_some() {
        rewrite(&vault);
    }
    let journal = Journal::open(vault.journal_dir()).unwrap();
    assert_eq!(journal.verify().unwrap(), history().len() as u64);
    let entries = journal.entries().unwrap();
    for (written, expected) in entries.iter().zip(history()) {
        assert_eq!(
            (written.at.as_str(), written.summary.as_str()),
            (expected.at.as_deref().unwrap(), expected.summary.as_str())
        );
    }
    let titles = |id: &edda_store::PageId| vault.read_page(id).ok().map(|p| p.title().to_owned());
    let log = render_log(&journal.log_entries(titles).unwrap());
    assert_eq!(
        fs::read_to_string(vault.wiki_dir().join("log.md")).unwrap(),
        log,
        "rerun with EDDA_WRITE_EXAMPLES=1"
    );
    assert!(log.contains("- 14:10 [Attention mechanism](/attention.md): verified"));
}

#[test]
fn write_log_reports_how_many_entries_it_wrote() {
    let dir = tempfile::tempdir().unwrap();
    fs::create_dir(dir.path().join("wiki")).unwrap();
    let vault = Vault::open(dir.path()).unwrap();
    assert_eq!(write_log(&vault).unwrap(), 0);
    let mut journal = Journal::open(vault.journal_dir()).unwrap();
    journal
        .append(NewEntry {
            page: Some("gone.md".into()),
            summary: "deleted".into(),
            ..NewEntry::default()
        })
        .unwrap();
    assert_eq!(write_log(&vault).unwrap(), 1);
    let log = fs::read_to_string(vault.wiki_dir().join("log.md")).unwrap();
    assert!(log.contains("[gone.md](/gone.md): deleted"), "{log}");

    fs::write(vault.journal_dir().join("000001.jsonl"), "garbage\n").unwrap();
    assert!(write_log(&vault).is_err());
    fs::remove_dir_all(vault.wiki_dir()).unwrap();
    fs::write(vault.wiki_dir(), "a file where the bundle should be").unwrap();
    fs::write(vault.journal_dir().join("000001.jsonl"), "").unwrap();
    assert!(matches!(
        write_log(&vault),
        Err(edda_journal::JournalError::Store(_))
    ));
}
