use std::fs;
use std::path::{Path, PathBuf};

use edda_index::{BrokenLink, Index, IndexError};
use edda_store::{PageId, Vault};

/// A copy of the example vault in a temporary directory, so building its index writes nothing in the repository.
fn example() -> (tempfile::TempDir, Vault) {
    let dir = tempfile::tempdir().unwrap();
    copy(
        &PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../examples/vault"),
        dir.path(),
    );
    let vault = Vault::open(dir.path()).unwrap();
    (dir, vault)
}

fn copy(from: &Path, to: &Path) {
    fs::create_dir_all(to).unwrap();
    for entry in fs::read_dir(from).unwrap() {
        let entry = entry.unwrap();
        let target = to.join(entry.file_name());
        if entry.file_type().unwrap().is_dir() {
            copy(&entry.path(), &target);
        } else {
            fs::copy(entry.path(), target).unwrap();
        }
    }
}

fn id(s: &str) -> PageId {
    PageId::new(s).unwrap()
}

#[test]
fn searches_by_bm25_with_snippets() {
    let (_dir, vault) = example();
    let index = Index::open(&vault).unwrap();
    let hits = index.search("attention", 10).unwrap();
    assert_eq!(hits[0].id, "attention.md");
    assert_eq!(hits[0].title, "Attention mechanism");
    assert!(hits.iter().all(|h| h.id != "bm25.md"));
    assert!(hits[0].snippet.contains("<b>"), "{}", hits[0].snippet);
    let stemmed = index.search("penalizing documents", 10).unwrap();
    assert_eq!(stemmed[0].id, "bm25.md");
    assert_eq!(index.search("attention", 1).unwrap().len(), 1);
    assert!(index.search("zebra", 5).unwrap().is_empty());
    assert_eq!(
        index.search("title:(attention", 5).unwrap()[0].id,
        "attention.md"
    );
}

#[test]
fn rebuilding_from_files_gives_the_same_results() {
    let (_dir, vault) = example();
    let queries = [
        "attention",
        "transformer encoder",
        "rank documents",
        "sine cosine positions",
        "2017",
    ];
    let before = Index::open(&vault).unwrap();
    let results: Vec<_> = queries
        .iter()
        .map(|q| before.search(q, 10).unwrap())
        .collect();
    let graph = before.graph().clone();
    drop(before);

    fs::remove_dir_all(vault.index_dir()).unwrap();
    let after = Index::open(&vault).unwrap();
    for (query, expected) in queries.iter().zip(&results) {
        assert_eq!(&after.search(query, 10).unwrap(), expected, "{query}");
    }
    assert_eq!(after.graph(), &graph);
}

#[test]
fn builds_the_link_graph() {
    let (_dir, vault) = example();
    let index = Index::open(&vault).unwrap();
    let links = index.links(&id("transformer.md"));
    assert_eq!(
        links.backlinks,
        ["attention.md", "papers/attention-is-all-you-need.md"]
    );
    let targets: Vec<_> = links
        .outgoing
        .iter()
        .filter_map(|l| l.target.as_deref())
        .collect();
    assert_eq!(
        targets,
        ["attention.md", "papers/attention-is-all-you-need.md"]
    );
    assert_eq!(index.orphans(), ["bm25.md"]);
    assert!(index.broken_links().is_empty());
    assert!(index.graph().contains(&id("bm25.md")));
    assert!(index.links(&id("missing.md")).outgoing.is_empty());
    assert_eq!(
        index.source_for_url("https://arxiv.org/abs/1706.03762"),
        Some("d9ca93c36fc0b371c30db895a9092fb9963e5b9d2c12d9122cea3676acce1444")
    );
    assert_eq!(index.source_for_url("https://nowhere.example"), None);
}

#[test]
fn reopens_when_current_and_rebuilds_when_stale() {
    let (_dir, vault) = example();
    Index::open(&vault).unwrap();
    let marker = vault.index_dir().join("search").join("meta.json");
    let built = fs::metadata(&marker).unwrap().modified().unwrap();
    Index::open(&vault).unwrap();
    assert_eq!(
        fs::metadata(&marker).unwrap().modified().unwrap(),
        built,
        "an up-to-date index was rebuilt"
    );

    let mut page = vault.read_page(&id("bm25.md")).unwrap();
    page.body
        .push_str("\nSee [Attention](/attention.md) and [Gone](/gone.md).\n");
    vault.write_page(&page).unwrap();
    let index = Index::open(&vault).unwrap();
    assert!(
        index
            .links(&id("attention.md"))
            .backlinks
            .contains(&"bm25.md".to_owned())
    );
    assert_eq!(
        index.broken_links(),
        [BrokenLink {
            from: "bm25.md".into(),
            dest: "/gone.md".into()
        }]
    );

    fs::write(vault.index_dir().join("graph.json"), "not json").unwrap();
    let repaired = Index::open(&vault).unwrap();
    assert_eq!(repaired.broken_links().len(), 1);
}

#[test]
fn reports_unreadable_vaults() {
    let (_dir, vault) = example();
    fs::write(vault.wiki_dir().join("bad.md"), "no frontmatter").unwrap();
    assert!(matches!(Index::rebuild(&vault), Err(IndexError::Store(_))));
}
