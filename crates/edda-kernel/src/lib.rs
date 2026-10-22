//! The syscall gate: the one place a proposal becomes a change to the vault. Accepting writes the page, bumps its
//! `edda_rev`, records who generated and who verified it, appends the journal entry and regenerates `index.md` and
//! `log.md`. Rejecting records the decision. Nothing else in Edda writes pages.

use edda_journal::{Entry, Journal, JournalError, NewEntry, write_log};
use edda_store::{
    Change, Generated, OWNER, Page, Proposal, Status, StoreError, Vault, Verification,
};
use serde_json::json;

#[derive(Debug, thiserror::Error)]
pub enum KernelError {
    #[error(transparent)]
    Store(#[from] StoreError),
    #[error(transparent)]
    Journal(#[from] JournalError),
    #[error("{id} cannot be applied: {}", issues.join("; "))]
    Blocked { id: String, issues: Vec<String> },
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
    let proposal = vault.proposal(id)?;
    if proposal.status != Status::Pending {
        return Err(StoreError::NotPending(proposal.id).into());
    }
    let preview = vault.preview(&proposal)?;
    if !preview.issues.is_empty() {
        return Err(KernelError::Blocked {
            id: proposal.id,
            issues: preview.issues,
        });
    }
    let at = now();
    let mut journal = Journal::open(vault.journal_dir())?;
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
    vault.write_index()?;
    write_log(vault)?;
    Ok(Outcome {
        proposal,
        entry,
        page,
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
