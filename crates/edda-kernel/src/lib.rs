//! The syscall gate: the one place a proposal becomes a change to the vault. Accepting writes the page, bumps its
//! `edda_rev`, records who generated and who verified it, appends the journal entry and regenerates `index.md` and
//! `log.md`. Rejecting records the decision. Nothing else in Edda writes pages. Adding a raw source is journaled here
//! too, so the history of a vault covers its sources as well as its pages.

use edda_journal::{Entry, Journal, JournalError, NewEntry, write_log};
use edda_store::{
    Change, Generated, OWNER, Page, PageId, Preview, Proposal, RawEntry, RawMeta, Status,
    StoreError, Vault, Verification, sha256_hex,
};
use serde_json::json;

/// What a rollback did: the journal entry and the page as restored.
#[derive(Debug, Clone, PartialEq)]
pub struct Restored {
    pub entry: Entry,
    pub page: Page,
}

/// What adding a source did.
#[derive(Debug, Clone, PartialEq)]
pub struct AddedSource {
    pub source: RawEntry,
    /// The journal entry; `None` when the same bytes were already stored, which changes nothing.
    pub entry: Option<Entry>,
}

/// Stores a raw source under its SHA-256 and journals it. `added` defaults to now. Adding bytes that are already
/// stored returns the stored source and records nothing.
pub fn add_source(
    vault: &Vault,
    bytes: &[u8],
    extension: Option<&str>,
    meta: &RawMeta,
    by: &str,
) -> Result<AddedSource> {
    let raw = vault.raw();
    if let Ok(existing) = raw.resolve(&sha256_hex(bytes)) {
        return Ok(AddedSource {
            source: existing,
            entry: None,
        });
    }
    let mut journal = Journal::open(vault.journal_dir())?;
    let mut meta = meta.clone();
    meta.added.get_or_insert_with(now);
    let source = raw.add(bytes, extension, &meta)?;
    let name = source
        .meta
        .title
        .clone()
        .unwrap_or_else(|| source.hash[..12].to_owned());
    let entry = journal.append(NewEntry {
        at: Some(now()),
        actor: by.to_owned(),
        action: "add_source".to_owned(),
        page: None,
        rev: None,
        summary: format!("added source {name}"),
        data: json!({
            "hash": source.hash,
            "url": source.meta.url,
            "driver": source.meta.driver,
            "derived_from": source.meta.derived_from,
        }),
    })?;
    write_log(vault)?;
    Ok(AddedSource {
        source,
        entry: Some(entry),
    })
}

#[derive(Debug, thiserror::Error)]
pub enum KernelError {
    #[error(transparent)]
    Store(#[from] StoreError),
    #[error(transparent)]
    Journal(#[from] JournalError),
    #[error("{id} cannot be applied: {}", issues.join("; "))]
    Blocked { id: String, issues: Vec<String> },
    #[error("{page} has no journaled revision {rev} to roll back to")]
    NoSuchRevision { page: String, rev: u64 },
    #[error("{0}")]
    Set(String),
}

pub type Result<T, E = KernelError> = std::result::Result<T, E>;

/// What a decision on a proposal did.
#[derive(Debug, Clone, PartialEq)]
pub struct Outcome {
    pub proposal: Proposal,
    pub entry: Entry,
    /// The page as written; `None` for a flag or a rejection.
    pub page: Option<Page>,
}

fn now() -> String {
    jiff::Timestamp::now()
        .strftime("%Y-%m-%dT%H:%M:%SZ")
        .to_string()
}

/// Accepts a pending proposal on behalf of `by`, a person, and applies it.
///
/// The page gets the next `edda_rev`, `generated` names the job (hand edits keep the page's existing `generated`),
/// and a `verified` entry names `by`; a page that was a draft becomes stable once a person has verified it. The
/// proposal's base revision is checked again here, so two proposals made against the same revision cannot both land.
pub fn accept(vault: &Vault, id: &str, by: &str) -> Result<Outcome> {
    apply(vault, id, by, true)
}

/// Applies a pending proposal. `verified` is false when policy applied it with no person involved: the change then
/// gets no `verified` entry, so the two can be told apart.
pub fn apply(vault: &Vault, id: &str, by: &str, verified: bool) -> Result<Outcome> {
    let proposal = pending(vault, id)?;
    let preview = vault.preview(&proposal)?;
    if !preview.issues.is_empty() {
        return Err(KernelError::Blocked {
            id: proposal.id,
            issues: preview.issues,
        });
    }
    let mut journal = Journal::open(vault.journal_dir())?;
    let outcome = write(vault, &mut journal, proposal, preview, by, verified)?;
    vault.write_index()?;
    write_log(vault)?;
    Ok(outcome)
}

/// Accepts several pending proposals as one change, on behalf of `by`: links between the new pages they create
/// resolve, so pages that link to each other can land together. Every proposal is checked against the set before
/// anything is written; if any breaks a rule, none is applied. New pages are written first, then edits, then flags,
/// each journaled as it would be on its own.
pub fn accept_together(vault: &Vault, ids: &[String], by: &str) -> Result<Vec<Outcome>> {
    if ids.len() < 2 {
        return Err(KernelError::Set(
            "accept at least two proposals together, or accept one on its own".into(),
        ));
    }
    let mut proposals: Vec<Proposal> = Vec::with_capacity(ids.len());
    for id in ids {
        let proposal = pending(vault, id)?;
        if let Some(other) = proposals.iter().find(|p| p.id == proposal.id) {
            return Err(KernelError::Set(format!("{} is listed twice", other.id)));
        }
        if let Some(other) = proposals
            .iter()
            .find(|p| p.change.page() == proposal.change.page())
        {
            return Err(KernelError::Set(format!(
                "{} and {} both change {}; accept them one at a time",
                other.id,
                proposal.id,
                proposal.change.page()
            )));
        }
        proposals.push(proposal);
    }
    let new_pages = proposals
        .iter()
        .filter(|p| matches!(p.change, Change::NewPage { .. }))
        .map(|p| PageId::new(p.change.page()))
        .collect::<std::result::Result<Vec<_>, _>>()?;
    let mut checked = Vec::with_capacity(proposals.len());
    let mut issues = Vec::new();
    for proposal in proposals {
        let preview = vault.preview_with(&proposal, &new_pages)?;
        issues.extend(
            preview
                .issues
                .iter()
                .map(|issue| format!("{}: {issue}", proposal.id)),
        );
        checked.push((proposal, preview));
    }
    if !issues.is_empty() {
        return Err(KernelError::Blocked {
            id: ids.join(", "),
            issues,
        });
    }
    checked.sort_by_key(|(p, _)| match p.change {
        Change::NewPage { .. } => 0,
        Change::Edit { .. } => 1,
        Change::Flag { .. } => 2,
    });
    let mut journal = Journal::open(vault.journal_dir())?;
    let outcomes = checked
        .into_iter()
        .map(|(proposal, preview)| write(vault, &mut journal, proposal, preview, by, true))
        .collect::<Result<Vec<_>>>()?;
    vault.write_index()?;
    write_log(vault)?;
    Ok(outcomes)
}

fn pending(vault: &Vault, id: &str) -> Result<Proposal> {
    let proposal = vault.proposal(id)?;
    if proposal.status != Status::Pending {
        return Err(StoreError::NotPending(proposal.id).into());
    }
    Ok(proposal)
}

/// Writes a checked proposal's page, journals it and marks the proposal accepted.
fn write(
    vault: &Vault,
    journal: &mut Journal,
    proposal: Proposal,
    preview: Preview,
    by: &str,
    verified: bool,
) -> Result<Outcome> {
    let at = now();
    let (action, rev, page) = match (&proposal.change, preview.page) {
        (Change::Flag { flag, .. }, _) => (format!("flag:{flag}"), None, None),
        (change, Some(mut page)) => {
            let (action, rev) = match change {
                Change::Edit { base_rev, .. } => ("edit_page", base_rev + 1),
                _ => ("create_page", 1),
            };
            let fm = &mut page.frontmatter;
            fm.edda_rev = Some(rev);
            if proposal.job != OWNER {
                fm.generated = Some(Generated {
                    by: proposal.job.clone(),
                    at: Some(at.clone()),
                    extra: Default::default(),
                });
            }
            if verified {
                fm.verified.push(Verification {
                    by: by.to_owned(),
                    at: Some(at.clone()),
                    extra: Default::default(),
                });
                if fm.status.as_deref() == Some("draft") {
                    fm.status = Some("stable".to_owned());
                }
            }
            vault.write_page(&page)?;
            (action.to_owned(), Some(rev), Some(page))
        }
        (_, None) => unreachable!("previews of edits and new pages always have a page"),
    };
    let content = page.as_ref().map(Page::to_markdown).transpose()?;
    let entry = journal.append(NewEntry {
        at: Some(at),
        actor: by.to_owned(),
        action,
        page: Some(proposal.change.page().to_owned()),
        rev,
        summary: summary(&proposal),
        data: json!({ "proposal": proposal.id, "job": proposal.job, "verified": verified, "content": content }),
    })?;
    let proposal = vault.set_proposal_status(&proposal.id, Status::Accepted)?;
    Ok(Outcome {
        proposal,
        entry,
        page,
    })
}

/// Restores a page to an earlier revision, on behalf of `by`. History is never rewritten: the restored text becomes a
/// new revision, journaled as `rollback`, with a `verified` entry for `by`.
///
/// Only revisions the kernel wrote are available, because the journal keeps the page as written for each of them;
/// hand-written history (such as the example vault's) has nothing to restore from.
pub fn rollback(vault: &Vault, page: &str, rev: u64, by: &str) -> Result<Restored> {
    let id = PageId::new(page)?;
    let current = vault.read_page(&id)?;
    let current_rev = current.frontmatter.edda_rev.unwrap_or(0);
    let mut journal = Journal::open(vault.journal_dir())?;
    let content = journal
        .history(id.as_str())?
        .into_iter()
        .rev()
        .filter(|e| e.rev == Some(rev) && rev < current_rev)
        .find_map(|e| {
            e.data
                .get("content")
                .and_then(|c| c.as_str())
                .map(str::to_owned)
        })
        .ok_or_else(|| KernelError::NoSuchRevision {
            page: id.to_string(),
            rev,
        })?;
    let mut restored = Page::parse(id.clone(), &content)?;
    let at = now();
    let fm = &mut restored.frontmatter;
    fm.edda_rev = Some(current_rev + 1);
    fm.verified.push(Verification {
        by: by.to_owned(),
        at: Some(at.clone()),
        extra: Default::default(),
    });
    vault.write_page(&restored)?;
    let entry = journal.append(NewEntry {
        at: Some(at),
        actor: by.to_owned(),
        action: "rollback".to_owned(),
        page: Some(id.to_string()),
        rev: Some(current_rev + 1),
        summary: format!("rolled back to revision {rev}"),
        data: json!({ "from_rev": current_rev, "to_rev": rev, "content": restored.to_markdown()? }),
    })?;
    vault.write_index()?;
    write_log(vault)?;
    Ok(Restored {
        entry,
        page: restored,
    })
}

/// Rejects a pending proposal; the page is untouched and the journal records why.
pub fn reject(vault: &Vault, id: &str, by: &str, reason: &str) -> Result<Outcome> {
    let mut journal = Journal::open(vault.journal_dir())?;
    let proposal = vault.set_proposal_status(id, Status::Rejected)?;
    let entry = journal.append(NewEntry {
        at: Some(now()),
        actor: by.to_owned(),
        action: "reject_proposal".to_owned(),
        page: Some(proposal.change.page().to_owned()),
        rev: None,
        summary: format!("rejected {}: {reason}", proposal.id),
        data: json!({ "proposal": proposal.id, "job": proposal.job }),
    })?;
    write_log(vault)?;
    Ok(Outcome {
        proposal,
        entry,
        page: None,
    })
}

/// The first line of the rationale, which is what `log.md` shows.
fn summary(proposal: &Proposal) -> String {
    let line = proposal.rationale.lines().next().unwrap_or_default().trim();
    if line.is_empty() {
        format!("applied {}", proposal.id)
    } else {
        line.to_owned()
    }
}

#[cfg(test)]
mod tests {
    use std::fs;

    use edda_store::{OWNER, PageId, RawMeta, TextEdit};

    use super::*;

    fn vault() -> (tempfile::TempDir, Vault, String) {
        let dir = tempfile::tempdir().unwrap();
        fs::create_dir(dir.path().join("wiki")).unwrap();
        let vault = Vault::open(dir.path()).unwrap();
        let page = "---\ntype: Concept\ntitle: Attention\ndescription: d\ngenerated: {by: earlier}\nedda_rev: 2\n---\nAttention weights values.\n";
        fs::write(vault.wiki_dir().join("attention.md"), page).unwrap();
        let meta = RawMeta {
            title: Some("Notes".into()),
            ..Default::default()
        };
        let hash = vault.raw().add(b"notes", Some("md"), &meta).unwrap().hash;
        (dir, vault, hash)
    }

    fn edit(base_rev: u64, new: &str) -> Change {
        Change::Edit {
            page: "attention.md".into(),
            base_rev,
            edits: vec![TextEdit {
                old: "values.".into(),
                new: new.into(),
            }],
        }
    }

    fn read(vault: &Vault) -> Page {
        vault
            .read_page(&PageId::new("attention.md").unwrap())
            .unwrap()
    }

    fn new_page(id: &str, links_to: &str) -> Change {
        Change::NewPage {
            page: id.into(),
            kind: "Concept".into(),
            title: id.trim_end_matches(".md").into(),
            description: "d".into(),
            tags: vec![],
            body: format!("See [the other](/{links_to})."),
        }
    }

    #[test]
    fn pages_that_link_to_each_other_are_accepted_together() {
        let (_dir, vault, _hash) = vault();
        let a = vault
            .propose(OWNER, new_page("devin.md", "deepwiki.md"), "a")
            .unwrap();
        let b = vault
            .propose(OWNER, new_page("deepwiki.md", "devin.md"), "b")
            .unwrap();
        let e = vault
            .propose(OWNER, edit(2, "values. See [Devin](/devin.md)."), "e")
            .unwrap();
        assert!(matches!(
            accept(&vault, &a.id, "owner"),
            Err(KernelError::Blocked { .. })
        ));

        let ids = vec![e.id.clone(), a.id.clone(), b.id.clone()];
        let outcomes = accept_together(&vault, &ids, "owner").unwrap();
        let actions: Vec<_> = outcomes
            .iter()
            .map(|o| (o.proposal.id.as_str(), o.entry.action.as_str()))
            .collect();
        assert_eq!(
            actions,
            [
                (a.id.as_str(), "create_page"),
                (b.id.as_str(), "create_page"),
                (e.id.as_str(), "edit_page")
            ]
        );
        assert!(vault.page_exists(&PageId::new("devin.md").unwrap()));
        assert!(read(&vault).body.contains("[Devin](/devin.md)"));
        assert!(
            outcomes
                .iter()
                .all(|o| o.proposal.status == Status::Accepted)
        );
        let log = fs::read_to_string(vault.wiki_dir().join("log.md")).unwrap();
        assert!(log.contains("/devin.md") && log.contains("/deepwiki.md"));
        assert_eq!(
            Journal::open(vault.journal_dir())
                .unwrap()
                .verify()
                .unwrap(),
            3
        );
    }

    #[test]
    fn a_set_with_any_issue_writes_nothing() {
        let (_dir, vault, _hash) = vault();
        let a = vault
            .propose(OWNER, new_page("devin.md", "deepwiki.md"), "a")
            .unwrap();
        let b = vault
            .propose(OWNER, new_page("fusion.md", "nowhere.md"), "b")
            .unwrap();
        let ids = vec![a.id.clone(), b.id.clone()];
        let Err(KernelError::Blocked { id, issues }) = accept_together(&vault, &ids, "owner")
        else {
            panic!("expected the set to be blocked");
        };
        assert_eq!(id, format!("{}, {}", a.id, b.id));
        assert_eq!(
            issues,
            [
                format!(
                    "{}: link /deepwiki.md points to a page that does not exist",
                    a.id
                ),
                format!(
                    "{}: link /nowhere.md points to a page that does not exist",
                    b.id
                ),
            ]
        );
        assert!(!vault.page_exists(&PageId::new("devin.md").unwrap()));
        assert_eq!(vault.proposal(&a.id).unwrap().status, Status::Pending);
        assert!(!vault.journal_dir().join("000001.jsonl").exists());
    }

    #[test]
    fn refuses_sets_it_cannot_apply_in_one_step() {
        let (_dir, vault, _hash) = vault();
        let one = vault.propose(OWNER, edit(2, "values!"), "one").unwrap();
        let two = vault.propose(OWNER, edit(2, "values?"), "two").unwrap();
        let set = |ids: &[&String]| ids.iter().map(|s| (*s).clone()).collect::<Vec<_>>();
        let message = |r: Result<Vec<Outcome>>| match r {
            Err(KernelError::Set(m)) => m,
            other => panic!("expected a set error, got {:?}", other.map(|o| o.len())),
        };
        assert!(
            message(accept_together(&vault, &set(&[&one.id]), "owner")).contains("at least two")
        );
        assert_eq!(
            message(accept_together(&vault, &set(&[&one.id, &one.id]), "owner")),
            format!("{} is listed twice", one.id)
        );
        assert!(
            message(accept_together(&vault, &set(&[&one.id, &two.id]), "owner"))
                .contains("both change attention.md")
        );
        reject(&vault, &two.id, "owner", "no").unwrap();
        let flag = vault
            .propose(
                "lint",
                Change::Flag {
                    page: "attention.md".into(),
                    flag: "gap".into(),
                    note: "n".into(),
                },
                "f",
            )
            .unwrap();
        assert!(matches!(
            accept_together(&vault, &set(&[&one.id, &two.id]), "owner"),
            Err(KernelError::Store(StoreError::NotPending(_)))
        ));
        assert!(
            message(accept_together(&vault, &set(&[&one.id, &flag.id]), "owner"))
                .contains("both change")
        );
    }

    #[test]
    fn a_new_page_that_exists_by_now_is_not_overwritten() {
        let (_dir, vault, _hash) = vault();
        let first = vault
            .propose(OWNER, new_page("devin.md", "attention.md"), "first")
            .unwrap();
        let second = vault
            .propose(OWNER, new_page("devin.md", "attention.md"), "second")
            .unwrap();
        accept(&vault, &first.id, "owner").unwrap();
        let Err(KernelError::Blocked { issues, .. }) = accept(&vault, &second.id, "owner") else {
            panic!("expected the second page to be blocked");
        };
        assert_eq!(issues, ["devin.md already exists"]);
    }

    #[test]
    fn accepting_writes_the_page_journal_index_and_log() {
        let (_dir, vault, hash) = vault();
        let p = vault
            .propose(
                "ingest:7",
                edit(2, "values. Heads split."),
                "adds heads\nmore detail",
            )
            .unwrap();
        vault
            .cite(&p.id, "Heads split.", &hash, "line 3", None)
            .unwrap();
        let outcome = accept(&vault, &p.id, "owner").unwrap();

        let page = read(&vault);
        assert_eq!(outcome.page.as_ref(), Some(&page));
        assert_eq!(page.frontmatter.edda_rev, Some(3));
        assert_eq!(page.frontmatter.generated.as_ref().unwrap().by, "ingest:7");
        assert_eq!(page.frontmatter.verified[0].by, "owner");
        assert!(page.body.contains("Heads split.[^notes-line-3]"));
        assert_eq!(outcome.proposal.status, Status::Accepted);
        assert_eq!(
            (outcome.entry.action.as_str(), outcome.entry.rev),
            ("edit_page", Some(3))
        );
        assert_eq!(outcome.entry.summary, "adds heads");
        assert_eq!(outcome.entry.data["content"], page.to_markdown().unwrap());
        let log = fs::read_to_string(vault.wiki_dir().join("log.md")).unwrap();
        assert!(log.contains("[Attention](/attention.md): adds heads"));
        assert!(
            fs::read_to_string(vault.wiki_dir().join("index.md"))
                .unwrap()
                .contains("/attention.md")
        );
        assert!(matches!(
            accept(&vault, &p.id, "owner"),
            Err(KernelError::Store(StoreError::NotPending(_)))
        ));
    }

    #[test]
    fn two_proposals_against_one_revision_cannot_both_land() {
        let (_dir, vault, _) = vault();
        let first = vault
            .propose(OWNER, edit(2, "values, first."), "first")
            .unwrap();
        let second = vault
            .propose(OWNER, edit(2, "values, second."), "second")
            .unwrap();
        accept(&vault, &first.id, "owner").unwrap();
        let page = read(&vault);
        assert_eq!(
            page.frontmatter.generated.as_ref().unwrap().by,
            "earlier",
            "hand edits keep generated"
        );
        assert!(matches!(
            accept(&vault, &second.id, "owner"),
            Err(KernelError::Store(StoreError::Conflict { current: 3, .. }))
        ));
    }

    #[test]
    fn uncited_job_text_blocks_and_auto_applied_changes_stay_unverified() {
        let (_dir, vault, hash) = vault();
        let p = vault
            .propose("ingest:1", edit(2, "values. Uncited claim."), "r")
            .unwrap();
        assert!(matches!(
            accept(&vault, &p.id, "owner"),
            Err(KernelError::Blocked { .. })
        ));
        let err = accept(&vault, &p.id, "owner").unwrap_err().to_string();
        assert!(err.contains("cites no source"), "{err}");

        let new = Change::NewPage {
            page: "heads.md".into(),
            kind: "Concept".into(),
            title: "Heads".into(),
            description: "d".into(),
            tags: vec![],
            body: "Heads split the work.\n".into(),
        };
        let p = vault.propose("lint:1", new, "").unwrap();
        vault
            .cite(&p.id, "split the work.", &hash, "line 1", None)
            .unwrap();
        let outcome = apply(&vault, &p.id, "policy", false).unwrap();
        let page = outcome.page.unwrap();
        assert_eq!(page.frontmatter.edda_rev, Some(1));
        assert!(page.frontmatter.verified.is_empty());
        assert_eq!(page.frontmatter.status.as_deref(), Some("draft"));
        assert_eq!(outcome.entry.summary, format!("applied {}", p.id));
        assert_eq!(outcome.entry.action, "create_page");
    }

    #[test]
    fn verifying_a_draft_makes_it_stable() {
        let (_dir, vault, _) = vault();
        let new = Change::NewPage {
            page: "notes.md".into(),
            kind: "Concept".into(),
            title: "Notes".into(),
            description: "d".into(),
            tags: vec![],
            body: "My own words.\n".into(),
        };
        let p = vault.propose(OWNER, new, "mine").unwrap();
        let page = accept(&vault, &p.id, "owner").unwrap().page.unwrap();
        assert_eq!(page.frontmatter.status.as_deref(), Some("stable"));
        assert!(page.frontmatter.generated.is_none());
    }

    #[test]
    fn flags_and_rejections_are_journaled_without_touching_the_page() {
        let (_dir, vault, _) = vault();
        let before = read(&vault);
        let flag = Change::Flag {
            page: "attention.md".into(),
            flag: "gap".into(),
            note: "n".into(),
        };
        let p = vault.propose("lint:1", flag, "missing a section").unwrap();
        let outcome = accept(&vault, &p.id, "owner").unwrap();
        assert_eq!(
            (outcome.entry.action.as_str(), outcome.page),
            ("flag:gap", None)
        );
        assert_eq!(outcome.entry.data["content"], serde_json::Value::Null);

        let p = vault
            .propose("ingest:1", edit(2, "values. x"), "r")
            .unwrap();
        let outcome = reject(&vault, &p.id, "owner", "not true").unwrap();
        assert_eq!(outcome.proposal.status, Status::Rejected);
        assert_eq!(
            outcome.entry.summary,
            format!("rejected {}: not true", p.id)
        );
        assert_eq!(read(&vault), before);
        let journal = Journal::open(vault.journal_dir()).unwrap();
        assert_eq!(journal.verify().unwrap(), 2);
        assert!(matches!(
            reject(&vault, &p.id, "owner", "again"),
            Err(KernelError::Store(_))
        ));
    }

    #[test]
    fn a_broken_journal_stops_the_apply() {
        let (_dir, vault, _) = vault();
        let p = vault.propose(OWNER, edit(2, "values!"), "r").unwrap();
        fs::create_dir_all(vault.journal_dir()).unwrap();
        fs::write(vault.journal_dir().join("000001.jsonl"), "garbage\n").unwrap();
        assert!(matches!(
            accept(&vault, &p.id, "owner"),
            Err(KernelError::Journal(_))
        ));
        assert!(matches!(
            reject(&vault, &p.id, "owner", "r"),
            Err(KernelError::Journal(_))
        ));
        assert_eq!(
            vault.proposal(&p.id).unwrap().status,
            Status::Pending,
            "nothing decided without a journal"
        );
        assert_eq!(read(&vault).frontmatter.edda_rev, Some(2));
    }
}

#[cfg(test)]
mod source_tests {
    use std::fs;

    use super::*;

    #[test]
    fn adding_a_source_journals_it_once() {
        let dir = tempfile::tempdir().unwrap();
        fs::create_dir(dir.path().join("wiki")).unwrap();
        let vault = Vault::open(dir.path()).unwrap();
        let meta = RawMeta {
            title: Some("Notes".into()),
            driver: Some("markdown".into()),
            ..Default::default()
        };
        let first = add_source(&vault, b"# Notes\n", Some("md"), &meta, "owner").unwrap();
        let entry = first.entry.unwrap();
        assert_eq!(
            (entry.action.as_str(), entry.summary.as_str()),
            ("add_source", "added source Notes")
        );
        assert_eq!(entry.data["driver"], "markdown");
        assert!(first.source.meta.added.is_some());
        let again = add_source(
            &vault,
            b"# Notes\n",
            Some("md"),
            &RawMeta::default(),
            "owner",
        )
        .unwrap();
        assert_eq!((again.source, again.entry), (first.source, None));

        let untitled = add_source(&vault, b"x", None, &RawMeta::default(), "owner").unwrap();
        assert!(untitled.entry.unwrap().summary.starts_with("added source "));
        assert!(
            fs::read_to_string(vault.wiki_dir().join("log.md"))
                .unwrap()
                .contains("added source Notes")
        );

        fs::write(vault.journal_dir().join("000001.jsonl"), "garbage\n").unwrap();
        assert!(matches!(
            add_source(&vault, b"y", None, &RawMeta::default(), "owner"),
            Err(KernelError::Journal(_))
        ));
        assert_eq!(
            vault.raw().list().unwrap().len(),
            2,
            "nothing stored without a journal entry"
        );
    }
}

#[cfg(test)]
mod rollback_tests {
    use std::fs;

    use edda_store::{OWNER, TextEdit};

    use super::*;

    #[test]
    fn rolls_back_by_appending_a_revision() {
        let dir = tempfile::tempdir().unwrap();
        fs::create_dir(dir.path().join("wiki")).unwrap();
        let vault = Vault::open(dir.path()).unwrap();
        fs::write(
            vault.wiki_dir().join("a.md"),
            "---\ntype: Concept\ntitle: A\ndescription: d\nedda_rev: 1\n---\none\n",
        )
        .unwrap();
        for (rev, text) in [(1, "two"), (2, "three")] {
            let old = if rev == 1 { "one" } else { "two" };
            let change = Change::Edit {
                page: "a.md".into(),
                base_rev: rev,
                edits: vec![TextEdit {
                    old: old.into(),
                    new: text.into(),
                }],
            };
            let p = vault.propose(OWNER, change, text).unwrap();
            accept(&vault, &p.id, "owner").unwrap();
        }
        let restored = rollback(&vault, "a.md", 2, "owner").unwrap();
        assert_eq!(restored.page.body, "two\n");
        assert_eq!(restored.page.frontmatter.edda_rev, Some(4));
        assert_eq!(
            (restored.entry.action.as_str(), restored.entry.rev),
            ("rollback", Some(4))
        );
        assert_eq!(restored.entry.summary, "rolled back to revision 2");
        assert_eq!(
            vault.read_page(&PageId::new("a.md").unwrap()).unwrap(),
            restored.page
        );
        assert!(
            fs::read_to_string(vault.wiki_dir().join("log.md"))
                .unwrap()
                .contains("rolled back to revision 2")
        );

        let again = rollback(&vault, "a.md", 3, "owner").unwrap();
        assert_eq!(again.page.body, "three\n");
        assert_eq!(
            Journal::open(vault.journal_dir())
                .unwrap()
                .verify()
                .unwrap(),
            4
        );

        for (rev, why) in [
            (1, "never journaled"),
            (5, "the current revision"),
            (9, "never existed"),
        ] {
            assert!(
                matches!(
                    rollback(&vault, "a.md", rev, "owner"),
                    Err(KernelError::NoSuchRevision { .. })
                ),
                "{why}"
            );
        }
        assert!(matches!(
            rollback(&vault, "../x.md", 1, "owner"),
            Err(KernelError::Store(_))
        ));
    }
}
