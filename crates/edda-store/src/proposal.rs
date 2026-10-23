//! Proposals: the only way jobs change the vault. A job proposes an edit, a new page or a flag, cites sources for the
//! text it adds, and the kernel stores the proposal in `proposals/` until it is accepted or rejected. Nothing here
//! writes a page; applying an accepted proposal is a separate step.

use std::fs;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use similar::TextDiff;

use crate::error::{Result, StoreError};
use crate::frontmatter::{Frontmatter, Resource, Source};
use crate::fsutil::{read_to_string, write_atomic};
use crate::page::Page;
use crate::page_id::PageId;
use crate::vault::Vault;

/// The actor whose proposals are hand edits: they need no citations for the text they add.
pub const OWNER: &str = "owner";

/// Replace `old`, which must appear exactly once in the page body, with `new`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TextEdit {
    pub old: String,
    pub new: String,
}

/// What a proposal changes.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Change {
    /// `propose_edit`: text edits to an existing page, made against revision `base_rev`.
    Edit {
        page: String,
        base_rev: u64,
        edits: Vec<TextEdit>,
    },
    /// `propose_page`: a new page. It starts as a draft.
    NewPage {
        page: String,
        #[serde(rename = "type")]
        kind: String,
        title: String,
        description: String,
        #[serde(default)]
        tags: Vec<String>,
        body: String,
    },
    /// `flag`: a contradiction, outdated claim or gap, for a person to look at. It changes no text.
    Flag {
        page: String,
        flag: String,
        note: String,
    },
}

impl Change {
    pub fn page(&self) -> &str {
        match self {
            Self::Edit { page, .. } | Self::NewPage { page, .. } | Self::Flag { page, .. } => page,
        }
    }

    /// Writes the page id in its canonical form, so `/transformer.md` and `transformer.md` are the same page in
    /// proposals and in the journal.
    fn normalize_page(&mut self) -> Result<()> {
        let (Self::Edit { page, .. } | Self::NewPage { page, .. } | Self::Flag { page, .. }) = self;
        *page = PageId::new(page)?.to_string();
        Ok(())
    }

    /// The text this change adds, which citations attach to.
    fn added_texts(&self) -> Vec<&str> {
        match self {
            Self::Edit { edits, .. } => edits.iter().map(|e| e.new.as_str()).collect(),
            Self::NewPage { body, .. } => vec![body],
            Self::Flag { .. } => Vec::new(),
        }
    }
}

/// `cite`: a claim in the added text, backed by a span of a raw source.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Citation {
    /// The footnote label and `sources[].id` the citation becomes.
    pub id: String,
    /// Text in the added text that the footnote is placed after.
    pub claim: String,
    /// The full SHA-256 of the raw source.
    pub raw: String,
    /// What goes in `sources[].resource`: the source's URL, or `raw:<hash>` when it has none.
    pub resource: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
    /// Where in the source: a page, section or line range. It becomes the footnote text.
    pub locator: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Status {
    Pending,
    Accepted,
    Rejected,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Proposal {
    /// `prop_000001`, `prop_000002`, ...
    pub id: String,
    /// The job that made it, or [`OWNER`] for a hand edit.
    pub job: String,
    /// RFC 3339, UTC.
    pub created: String,
    pub change: Change,
    #[serde(default)]
    pub citations: Vec<Citation>,
    pub rationale: String,
    pub status: Status,
}

/// What accepting a proposal would do: the resulting page, a unified diff, and what keeps it from being applied.
#[derive(Debug, Clone, PartialEq)]
pub struct Preview {
    /// The page after the change; `None` for a flag.
    pub page: Option<Page>,
    pub diff: String,
    /// Kernel rules the result breaks. A proposal with issues cannot be applied.
    pub issues: Vec<String>,
}

impl Vault {
    pub fn proposals_dir(&self) -> PathBuf {
        self.root().join("proposals")
    }

    /// Checks a change against the vault as it is now and stores it as a pending proposal.
    pub fn propose(&self, job: &str, mut change: Change, rationale: &str) -> Result<Proposal> {
        change.normalize_page()?;
        self.check_change(&change)?;
        let proposal = Proposal {
            id: self.next_proposal_id()?,
            job: job.to_owned(),
            created: jiff::Timestamp::now()
                .strftime("%Y-%m-%dT%H:%M:%SZ")
                .to_string(),
            change,
            citations: Vec::new(),
            rationale: rationale.to_owned(),
            status: Status::Pending,
        };
        self.save_proposal(&proposal)?;
        Ok(proposal)
    }

    /// Attaches a source to a claim in a pending proposal's added text. The citation id is `source_id` when given,
    /// otherwise derived from the source's title and the locator, and made unique on the page.
    pub fn cite(
        &self,
        proposal_id: &str,
        claim: &str,
        source: &str,
        locator: &str,
        source_id: Option<&str>,
    ) -> Result<Proposal> {
        let mut proposal = self.proposal(proposal_id)?;
        if proposal.status != Status::Pending {
            return Err(StoreError::NotPending(proposal.id));
        }
        if claim.is_empty()
            || !proposal
                .change
                .added_texts()
                .iter()
                .any(|t| t.contains(claim))
        {
            return Err(StoreError::ClaimNotFound(claim.to_owned()));
        }
        let entry = self.raw().resolve(source)?;
        let cited = crate::pages::cited_pages(locator);
        if !cited.is_empty() {
            let marked = crate::pages::pages(&self.raw().read_text(&entry, None)?);
            if let (Some(&last), Some(&page)) =
                (marked.last(), cited.iter().find(|p| !marked.contains(p)))
            {
                return Err(StoreError::NoSuchPage {
                    source_hash: entry.hash.clone(),
                    page,
                    last,
                });
            }
        }
        let resource = entry
            .meta
            .url
            .clone()
            .unwrap_or_else(|| format!("raw:{}", entry.hash));
        let base = match source_id {
            Some(id) => slug(id),
            None => {
                let name = entry
                    .meta
                    .title
                    .as_deref()
                    .map_or_else(|| entry.hash[..8].to_owned(), slug);
                format!("{name}-{}", slug(locator))
            }
        };
        let taken = self.taken_source_ids(&proposal)?;
        let id = unique(&base, &taken);
        proposal.citations.push(Citation {
            id,
            claim: claim.to_owned(),
            raw: entry.hash.clone(),
            resource,
            title: entry.meta.title.clone(),
            locator: locator.to_owned(),
        });
        self.save_proposal(&proposal)?;
        Ok(proposal)
    }

    /// Every proposal, oldest first.
    pub fn proposals(&self) -> Result<Vec<Proposal>> {
        let dir = self.proposals_dir();
        let read = match fs::read_dir(&dir) {
            Ok(read) => read,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
            Err(e) => return Err(StoreError::io(&dir, e)),
        };
        let mut ids: Vec<String> = Vec::new();
        for item in read {
            let path = item.map_err(|e| StoreError::io(&dir, e))?.path();
            if let Some(id) = path
                .file_name()
                .and_then(|n| n.to_str())
                .and_then(|n| n.strip_suffix(".json"))
                && is_proposal_id(id)
            {
                ids.push(id.to_owned());
            }
        }
        ids.sort();
        ids.iter().map(|id| self.proposal(id)).collect()
    }

    pub fn proposal(&self, id: &str) -> Result<Proposal> {
        if !is_proposal_id(id) {
            return Err(StoreError::ProposalNotFound(id.to_owned()));
        }
        let path = self.proposals_dir().join(format!("{id}.json"));
        if !path.is_file() {
            return Err(StoreError::ProposalNotFound(id.to_owned()));
        }
        serde_json::from_str(&read_to_string(&path)?).map_err(|e| StoreError::Config {
            path,
            reason: e.to_string(),
        })
    }

    /// Marks a pending proposal accepted or rejected.
    pub fn set_proposal_status(&self, id: &str, status: Status) -> Result<Proposal> {
        let mut proposal = self.proposal(id)?;
        if proposal.status != Status::Pending {
            return Err(StoreError::NotPending(proposal.id));
        }
        proposal.status = status;
        self.save_proposal(&proposal)?;
        Ok(proposal)
    }

    /// The page a proposal would produce, as a diff, with the kernel rules it breaks.
    pub fn preview(&self, proposal: &Proposal) -> Result<Preview> {
        self.preview_with(proposal, &[])
    }

    /// [`Vault::preview`], with links to the pages in `also` counted as resolving: the new pages of proposals
    /// accepted together with this one.
    pub fn preview_with(&self, proposal: &Proposal, also: &[PageId]) -> Result<Preview> {
        let added = with_footnote_refs(&proposal.change, &proposal.citations);
        let (before, page) = match &proposal.change {
            Change::Flag { page, .. } => {
                let id = PageId::new(page)?;
                self.read_page(&id)?;
                return Ok(Preview {
                    page: None,
                    diff: String::new(),
                    issues: Vec::new(),
                });
            }
            Change::Edit {
                page,
                base_rev,
                edits,
            } => {
                let mut page = self.read_page(&PageId::new(page)?)?;
                check_rev(&page, *base_rev)?;
                let before = page.to_markdown()?;
                for (edit, new) in edits.iter().zip(&added) {
                    page.body = replace_once(&page.body, &edit.old, new, &page.id)?;
                }
                (before, page)
            }
            Change::NewPage {
                page,
                kind,
                title,
                description,
                tags,
                ..
            } => {
                let frontmatter = Frontmatter {
                    kind: Some(kind.clone()),
                    title: Some(title.clone()),
                    description: Some(description.clone()),
                    tags: tags.clone(),
                    status: Some("draft".to_owned()),
                    ..Frontmatter::default()
                };
                let body = added.first().cloned().unwrap_or_default();
                (
                    String::new(),
                    Page {
                        id: PageId::new(page)?,
                        frontmatter,
                        body,
                    },
                )
            }
        };
        let page = add_sources(page, &proposal.citations);
        let after = page.to_markdown()?;
        let name = page.id.to_string();
        let diff = TextDiff::from_lines(&before, &after)
            .unified_diff()
            .header(&format!("a/{name}"), &format!("b/{name}"))
            .to_string();

        let mut snapshot = self.snapshot()?;
        snapshot.add_page(page.id.clone());
        for id in also {
            snapshot.add_page(id.clone());
        }
        let mut issues: Vec<String> = Vec::new();
        if matches!(proposal.change, Change::NewPage { .. }) && self.page_exists(&page.id) {
            issues.push(format!("{} already exists", page.id));
        }
        issues.extend(page.check(&snapshot).iter().map(ToString::to_string));
        if proposal.job != OWNER {
            let replaced: Vec<&str> = match &proposal.change {
                Change::Edit { edits, .. } => edits.iter().map(|e| e.old.as_str()).collect(),
                _ => vec![""; added.len()],
            };
            for (old, text) in replaced.iter().zip(&added) {
                let new = uncited_paragraphs(text).filter(|p| !old.contains(p));
                issues.extend(new.map(|p| format!("added text cites no source: {p:?}")));
            }
        }
        Ok(Preview {
            page: Some(page),
            diff,
            issues,
        })
    }

    fn check_change(&self, change: &Change) -> Result<()> {
        let id = PageId::new(change.page())?;
        match change {
            Change::Edit {
                base_rev, edits, ..
            } => {
                let page = self.read_page(&id)?;
                check_rev(&page, *base_rev)?;
                let mut body = page.body;
                for edit in edits {
                    body = replace_once(&body, &edit.old, &edit.new, &id)?;
                }
                Ok(())
            }
            Change::NewPage { .. } if self.page_exists(&id) || id.is_generated() => {
                Err(StoreError::PageExists(id.to_string()))
            }
            Change::NewPage { .. } => Ok(()),
            Change::Flag { .. } => self.read_page(&id).map(|_| ()),
        }
    }

    fn taken_source_ids(&self, proposal: &Proposal) -> Result<Vec<String>> {
        let mut taken: Vec<String> = proposal.citations.iter().map(|c| c.id.clone()).collect();
        if let Change::Edit { page, .. } = &proposal.change {
            let page = self.read_page(&PageId::new(page)?)?;
            taken.extend(page.frontmatter.sources.iter().filter_map(|s| s.id.clone()));
        }
        Ok(taken)
    }

    fn next_proposal_id(&self) -> Result<String> {
        let last = self
            .proposals()?
            .last()
            .and_then(|p| p.id[5..].parse::<u64>().ok())
            .unwrap_or(0);
        Ok(format!("prop_{:06}", last + 1))
    }

    fn save_proposal(&self, proposal: &Proposal) -> Result<()> {
        let path = self.proposals_dir().join(format!("{}.json", proposal.id));
        let json = serde_json::to_string_pretty(proposal).map_err(|e| StoreError::Config {
            path: path.clone(),
            reason: e.to_string(),
        })?;
        write_atomic(&path, format!("{json}\n").as_bytes())
    }
}

fn is_proposal_id(id: &str) -> bool {
    id.len() == 11 && id.starts_with("prop_") && id[5..].bytes().all(|b| b.is_ascii_digit())
}

fn check_rev(page: &Page, base_rev: u64) -> Result<()> {
    let current = page.frontmatter.edda_rev.unwrap_or(0);
    if current == base_rev {
        Ok(())
    } else {
        Err(StoreError::Conflict {
            page: page.id.to_string(),
            base_rev,
            current,
        })
    }
}

/// Replaces the one place `old` appears in `body`. When it does not appear exactly as given, runs of whitespace are
/// treated as equal (a model quoting hard-wrapped Markdown often joins the lines), but it must still appear once.
fn replace_once(body: &str, old: &str, new: &str, page: &PageId) -> Result<String> {
    let exact: Vec<(usize, usize)> = if old.is_empty() {
        Vec::new()
    } else {
        body.match_indices(old)
            .map(|(i, m)| (i, i + m.len()))
            .collect()
    };
    let spans = if exact.is_empty() {
        loose_matches(body, old)
    } else {
        exact
    };
    match spans.as_slice() {
        [(start, end)] => Ok(format!("{}{new}{}", &body[..*start], &body[*end..])),
        _ => Err(StoreError::EditDoesNotApply {
            page: page.to_string(),
            old: old.to_owned(),
            count: spans.len(),
        }),
    }
}

/// Where `needle` appears in `haystack` when any run of whitespace matches any other: byte spans, not overlapping.
fn loose_matches(haystack: &str, needle: &str) -> Vec<(usize, usize)> {
    let words: Vec<&str> = needle.split_whitespace().collect();
    let Some(first) = words.first() else {
        return Vec::new();
    };
    let mut spans = Vec::new();
    let mut from = 0;
    while let Some(found) = haystack[from..].find(first) {
        let start = from + found;
        let mut end = start + first.len();
        let matched = words[1..].iter().all(|word| {
            let rest = &haystack[end..];
            let gap = rest.len() - rest.trim_start().len();
            if gap > 0 && rest[gap..].starts_with(word) {
                end += gap + word.len();
                true
            } else {
                false
            }
        });
        if matched {
            spans.push((start, end));
            from = end;
        } else {
            from = start + first.len();
        }
    }
    spans
}

/// The added texts with a footnote reference placed after each citation's claim.
fn with_footnote_refs(change: &Change, citations: &[Citation]) -> Vec<String> {
    let mut texts: Vec<String> = change
        .added_texts()
        .into_iter()
        .map(str::to_owned)
        .collect();
    for citation in citations {
        if let Some(text) = texts.iter_mut().find(|t| t.contains(&citation.claim))
            && let Some(start) = text.find(&citation.claim)
        {
            text.insert_str(start + citation.claim.len(), &format!("[^{}]", citation.id));
        }
    }
    texts
}

/// Adds each citation's `sources` entry and footnote definition to the page.
fn add_sources(mut page: Page, citations: &[Citation]) -> Page {
    for citation in citations {
        if page.frontmatter.source(&citation.id).is_none() {
            page.frontmatter.sources.push(Source {
                id: Some(citation.id.clone()),
                resource: Resource::parse(&citation.resource),
                title: citation.title.clone(),
                extra: Default::default(),
            });
        }
        if !page.body.ends_with('\n') {
            page.body.push('\n');
        }
        page.body
            .push_str(&format!("\n[^{}]: {}\n", citation.id, citation.locator));
    }
    page
}

/// Paragraphs of added prose without a footnote reference. Headings, footnote definitions and blank lines are not
/// claims.
fn uncited_paragraphs(text: &str) -> impl Iterator<Item = &str> {
    text.split("\n\n").map(str::trim).filter(|p| {
        !p.is_empty() && !p.starts_with('#') && !p.starts_with("[^") && !p.contains("[^")
    })
}

fn slug(text: &str) -> String {
    let mut out = String::new();
    for c in text.chars().flat_map(char::to_lowercase) {
        if c.is_ascii_alphanumeric() {
            out.push(c);
        } else if !out.ends_with('-') && !out.is_empty() {
            out.push('-');
        }
    }
    let out = out.trim_end_matches('-');
    if out.is_empty() {
        "source".to_owned()
    } else {
        out.to_owned()
    }
}

fn unique(base: &str, taken: &[String]) -> String {
    if !taken.iter().any(|t| t == base) {
        return base.to_owned();
    }
    (2..)
        .map(|n| format!("{base}-{n}"))
        .find(|c| !taken.contains(c))
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::raw::RawMeta;

    fn vault() -> (tempfile::TempDir, Vault, String) {
        let dir = tempfile::tempdir().unwrap();
        fs::create_dir(dir.path().join("wiki")).unwrap();
        let vault = Vault::open(dir.path()).unwrap();
        let page = "---\ntype: Concept\ntitle: Attention\ndescription: d\nedda_rev: 2\n---\nAttention weights values.\n";
        fs::write(vault.wiki_dir().join("attention.md"), page).unwrap();
        let meta = RawMeta {
            title: Some("Paper Notes".into()),
            url: Some("https://x.example/p".into()),
            ..Default::default()
        };
        let raw = vault.raw().add(b"notes", Some("md"), &meta).unwrap();
        let bare = vault.raw().add(b"bare", None, &RawMeta::default()).unwrap();
        let _ = bare;
        (dir, vault, raw.hash)
    }

    fn edit(old: &str, new: &str) -> Change {
        Change::Edit {
            page: "attention.md".into(),
            base_rev: 2,
            edits: vec![TextEdit {
                old: old.into(),
                new: new.into(),
            }],
        }
    }

    #[test]
    fn a_cited_page_must_be_one_the_source_has() {
        let (_dir, vault, notes) = vault();
        let paged = b"<!-- page 1 -->\nIntro.\n\n<!-- page 2 -->\nHeads run in parallel.\n";
        let pdf = vault
            .raw()
            .add(paged, Some("md"), &RawMeta::default())
            .unwrap()
            .hash;
        let p = vault
            .propose(
                "ingest:1",
                edit("values.", "values. Heads run in parallel."),
                "r",
            )
            .unwrap();
        let err = vault
            .cite(&p.id, "Heads run", &pdf, "page 3", None)
            .unwrap_err();
        assert_eq!(
            err.to_string(),
            format!("source {pdf} has no page 3; its pages are 1 to 2")
        );
        assert!(
            vault
                .cite(&p.id, "Heads run", &pdf, "pages 1-2", None)
                .is_ok()
        );
        assert!(
            vault
                .cite(&p.id, "Heads run", &pdf, "lines 4-5", None)
                .is_ok()
        );
        assert!(
            vault
                .cite(&p.id, "Heads run", &notes, "page 9", None)
                .is_ok(),
            "unpaged sources are not checked"
        );
    }

    #[test]
    fn an_edit_with_a_citation_previews_as_a_diff_without_issues() {
        let (_dir, vault, hash) = vault();
        let p = vault
            .propose(
                "ingest:1",
                edit("values.", "values.\n\nHeads run in parallel."),
                "adds heads",
            )
            .unwrap();
        assert_eq!((p.id.as_str(), p.status), ("prop_000001", Status::Pending));
        let issues = vault.preview(&p).unwrap().issues;
        assert_eq!(
            issues,
            ["added text cites no source: \"Heads run in parallel.\"".to_owned()]
        );

        let p = vault
            .cite(&p.id, "Heads run in parallel.", &hash[..8], "page 5", None)
            .unwrap();
        assert_eq!(p.citations[0].id, "paper-notes-page-5");
        assert_eq!(p.citations[0].resource, "https://x.example/p");
        let preview = vault.preview(&p).unwrap();
        assert_eq!(preview.issues, Vec::<String>::new());
        let page = preview.page.unwrap();
        assert!(
            page.body
                .contains("Heads run in parallel.[^paper-notes-page-5]")
        );
        assert!(page.body.ends_with("[^paper-notes-page-5]: page 5\n"));
        assert_eq!(
            page.frontmatter.sources[0].id.as_deref(),
            Some("paper-notes-page-5")
        );
        assert!(
            preview
                .diff
                .starts_with("--- a/attention.md\n+++ b/attention.md\n")
        );
        assert!(
            preview
                .diff
                .contains("+Heads run in parallel.[^paper-notes-page-5]")
        );

        let again = vault.cite(&p.id, "Heads", &hash, "page 5", None).unwrap();
        assert_eq!(again.citations[1].id, "paper-notes-page-5-2");
        assert_eq!(vault.proposals().unwrap(), [again]);
    }

    #[test]
    fn a_new_page_starts_as_a_draft_and_hand_edits_need_no_citations() {
        let (_dir, vault, hash) = vault();
        let change = Change::NewPage {
            page: "heads.md".into(),
            kind: "Concept".into(),
            title: "Heads".into(),
            description: "Many heads.".into(),
            tags: vec!["ml".into()],
            body: "# Heads\n\nSee [Attention](/attention.md). Heads split the work.\n".into(),
        };
        let p = vault.propose("ingest:1", change, "new").unwrap();
        let p = vault
            .cite(&p.id, "split the work.", &hash, "§3", Some("Notes 3"))
            .unwrap();
        let preview = vault.preview(&p).unwrap();
        assert_eq!(preview.issues, Vec::<String>::new());
        let page = preview.page.unwrap();
        assert_eq!(page.frontmatter.status.as_deref(), Some("draft"));
        assert!(page.body.contains("split the work.[^notes-3]"));
        assert!(preview.diff.contains("+title: Heads"));

        let by_hand = vault
            .propose(OWNER, edit("values.", "values, mostly."), "my words")
            .unwrap();
        assert!(vault.preview(&by_hand).unwrap().issues.is_empty());
    }

    #[test]
    fn refuses_conflicts_bad_edits_and_bad_citations() {
        let (_dir, vault, hash) = vault();
        let stale = Change::Edit {
            page: "attention.md".into(),
            base_rev: 1,
            edits: vec![],
        };
        assert!(matches!(
            vault.propose("j", stale, "r"),
            Err(StoreError::Conflict { current: 2, .. })
        ));
        assert!(matches!(
            vault.propose("j", edit("absent", "x"), "r"),
            Err(StoreError::EditDoesNotApply { count: 0, .. })
        ));
        assert!(matches!(
            vault.propose("j", edit("", "x"), "r"),
            Err(StoreError::EditDoesNotApply { .. })
        ));
        let exists = Change::NewPage {
            page: "attention.md".into(),
            kind: "Concept".into(),
            title: "t".into(),
            description: "d".into(),
            tags: vec![],
            body: String::new(),
        };
        assert!(matches!(
            vault.propose("j", exists, "r"),
            Err(StoreError::PageExists(_))
        ));
        let missing = Change::Flag {
            page: "gone.md".into(),
            flag: "gap".into(),
            note: "n".into(),
        };
        assert!(matches!(
            vault.propose("j", missing, "r"),
            Err(StoreError::PageNotFound(_))
        ));

        let p = vault
            .propose("j", edit("values.", "values. More."), "r")
            .unwrap();
        assert!(matches!(
            vault.cite(&p.id, "Absent claim", &hash, "p1", None),
            Err(StoreError::ClaimNotFound(_))
        ));
        assert!(matches!(
            vault.cite(&p.id, "More.", "ffff", "p1", None),
            Err(StoreError::SourceNotFound(_))
        ));
        let rejected = vault.set_proposal_status(&p.id, Status::Rejected).unwrap();
        assert_eq!(rejected.status, Status::Rejected);
        assert!(matches!(
            vault.cite(&p.id, "More.", &hash, "p1", None),
            Err(StoreError::NotPending(_))
        ));
        assert!(matches!(
            vault.set_proposal_status(&p.id, Status::Accepted),
            Err(StoreError::NotPending(_))
        ));
        for bad in ["../etc", "prop_1", "prop_00000x"] {
            assert!(matches!(
                vault.proposal(bad),
                Err(StoreError::ProposalNotFound(_))
            ));
        }
        assert!(matches!(
            vault.proposal("prop_000009"),
            Err(StoreError::ProposalNotFound(_))
        ));

        fs::write(
            vault.wiki_dir().join("attention.md"),
            "---\ntype: Concept\nedda_rev: 3\n---\nvalues.\n",
        )
        .unwrap();
        assert!(matches!(
            vault.preview(&p),
            Err(StoreError::Conflict { current: 3, .. })
        ));
    }

    #[test]
    fn edits_match_across_line_wrapping_but_only_once() {
        let id = PageId::new("a.md").unwrap();
        let body = "Built around\n[attention](/attention.md). It has\n  no recurrence.\n";
        let joined = "Built around [attention](/attention.md).";
        assert_eq!(
            replace_once(body, joined, "X.", &id).unwrap(),
            "X. It has\n  no recurrence.\n"
        );

        let twice = "a b\nand a\n b\n";
        assert!(matches!(
            replace_once(twice, "a  b", "c", &id),
            Err(StoreError::EditDoesNotApply { count: 2, .. })
        ));
        let exact_wins = "one two\none\ntwo\n";
        assert_eq!(
            replace_once(exact_wins, "one two", "1", &id).unwrap(),
            "1\none\ntwo\n"
        );
        assert!(matches!(
            replace_once(body, "   ", "x", &id),
            Err(StoreError::EditDoesNotApply { count: 0, .. })
        ));
        assert_eq!(loose_matches("aXa b a b", "a b"), [(2, 5), (6, 9)]);
        assert!(
            loose_matches("ab", "a b").is_empty(),
            "words must be separated by whitespace"
        );
    }

    #[test]
    fn page_ids_are_stored_in_canonical_form() {
        let (_dir, vault, _) = vault();
        let root_relative = Change::Flag {
            page: "/attention.md".into(),
            flag: "gap".into(),
            note: "n".into(),
        };
        assert_eq!(
            vault
                .propose("j", root_relative, "r")
                .unwrap()
                .change
                .page(),
            "attention.md"
        );
        let bad = Change::Flag {
            page: "../x.md".into(),
            flag: "gap".into(),
            note: "n".into(),
        };
        assert!(matches!(
            vault.propose("j", bad, "r"),
            Err(StoreError::InvalidPageId { .. })
        ));
    }

    #[test]
    fn flags_change_no_text() {
        let (_dir, vault, _) = vault();
        let flag = Change::Flag {
            page: "attention.md".into(),
            flag: "contradiction".into(),
            note: "n".into(),
        };
        let p = vault.propose("lint:1", flag, "two pages disagree").unwrap();
        assert_eq!(
            vault.preview(&p).unwrap(),
            Preview {
                page: None,
                diff: String::new(),
                issues: vec![]
            }
        );
        assert!(matches!(
            vault.cite(&p.id, "n", "ab", "x", None),
            Err(StoreError::ClaimNotFound(_))
        ));
    }

    #[test]
    fn ignores_stray_files_and_reports_corrupt_proposals() {
        let (_dir, vault, _) = vault();
        assert!(vault.proposals().unwrap().is_empty());
        fs::create_dir_all(vault.proposals_dir()).unwrap();
        fs::write(vault.proposals_dir().join("notes.txt"), "x").unwrap();
        assert!(vault.proposals().unwrap().is_empty());
        fs::write(vault.proposals_dir().join("prop_000001.json"), "{").unwrap();
        assert!(matches!(vault.proposals(), Err(StoreError::Config { .. })));
    }

    #[test]
    fn slugs_and_unique_ids() {
        assert_eq!(
            slug("Attention Is All You Need!"),
            "attention-is-all-you-need"
        );
        assert_eq!(slug("§§"), "source");
        assert_eq!(unique("a", &["a".into(), "a-2".into()]), "a-3");
        assert_eq!(
            uncited_paragraphs("# H\n\n[^a]: n\n\nok[^a]\n\nbare").collect::<Vec<_>>(),
            ["bare"]
        );
    }
}
