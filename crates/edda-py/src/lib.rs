//! The `edda._kernel` Python module: the kernel's read-side system calls, built by maturin.
//!
//! Results cross into Python as JSON strings that `edda.kernel` validates into Pydantic models, which keeps this
//! layer thin and the schema in one place.

use std::sync::RwLock;

use edda_index::{Index, IndexError};
use edda_journal::{Journal, JournalError};
use edda_store::{
    Change, PageId, Proposal, RawEntry, RawMeta, Resource, StoreError, TextEdit, Vault,
};
use pyo3::create_exception;
use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use serde::Serialize;
use serde_json::{Value, json};

create_exception!(
    _kernel,
    KernelError,
    PyRuntimeError,
    "Something went wrong inside the kernel."
);
create_exception!(
    _kernel,
    NotFoundError,
    KernelError,
    "No page or source by that name."
);
create_exception!(
    _kernel,
    InvalidRequestError,
    KernelError,
    "The page id or source hash is not valid."
);
create_exception!(
    _kernel,
    ConflictError,
    InvalidRequestError,
    "The page changed since the proposal's base revision."
);

fn store_err(e: StoreError) -> PyErr {
    match e {
        StoreError::PageNotFound(_)
        | StoreError::SourceNotFound(_)
        | StoreError::ProposalNotFound(_) => NotFoundError::new_err(e.to_string()),
        StoreError::Conflict { .. } => ConflictError::new_err(e.to_string()),
        StoreError::InvalidPageId { .. }
        | StoreError::InvalidHash(_)
        | StoreError::AmbiguousSource { .. }
        | StoreError::EditDoesNotApply { .. }
        | StoreError::PageExists(_)
        | StoreError::ClaimNotFound(_)
        | StoreError::NoSuchPage { .. }
        | StoreError::NotPending(_) => InvalidRequestError::new_err(e.to_string()),
        other => KernelError::new_err(other.to_string()),
    }
}

fn index_err(e: IndexError) -> PyErr {
    match e {
        IndexError::Store(e) => store_err(e),
        other => KernelError::new_err(other.to_string()),
    }
}

fn journal_err(e: JournalError) -> PyErr {
    KernelError::new_err(e.to_string())
}

fn gate_err(e: edda_kernel::KernelError) -> PyErr {
    match e {
        edda_kernel::KernelError::Store(e) => store_err(e),
        edda_kernel::KernelError::Journal(e) => journal_err(e),
        blocked @ edda_kernel::KernelError::Blocked { .. } => {
            InvalidRequestError::new_err(blocked.to_string())
        }
        missing @ edda_kernel::KernelError::NoSuchRevision { .. } => {
            NotFoundError::new_err(missing.to_string())
        }
        set @ edda_kernel::KernelError::Set(_) => InvalidRequestError::new_err(set.to_string()),
    }
}

fn to_json(value: &impl Serialize) -> PyResult<String> {
    serde_json::to_string(value).map_err(|e| KernelError::new_err(e.to_string()))
}

fn page_id(id: &str) -> PyResult<PageId> {
    PageId::new(id).map_err(store_err)
}

/// The full hash of the raw source a `resource` names: a unique `raw:` prefix, or a URL a source was fetched from.
fn resolve_source(raw: &[RawEntry], index: &Index, resource: &Resource) -> Option<String> {
    match resource {
        Resource::Raw(prefix) => {
            let prefix = prefix.to_ascii_lowercase();
            let mut found = raw
                .iter()
                .filter(|e| !prefix.is_empty() && e.hash.starts_with(&prefix));
            match (found.next(), found.next()) {
                (Some(entry), None) => Some(entry.hash.clone()),
                _ => None,
            }
        }
        Resource::Url(url) => index.source_for_url(url).map(str::to_owned),
    }
}

/// A vault opened for reading, with its index.
#[pyclass(frozen)]
struct Kernel {
    vault: Vault,
    index: RwLock<Index>,
}

impl Kernel {
    fn proposal_view(&self, proposal: &Proposal) -> PyResult<Value> {
        let mut view =
            serde_json::to_value(proposal).map_err(|e| KernelError::new_err(e.to_string()))?;
        let (diff, issues) = match self.vault.preview(proposal) {
            Ok(preview) => (preview.diff, preview.issues),
            Err(e) => (String::new(), vec![e.to_string()]),
        };
        view["preview"] = json!({"diff": diff, "issues": issues});
        Ok(view)
    }

    fn proposal_json(&self, proposal: &Proposal) -> PyResult<String> {
        to_json(&self.proposal_view(proposal)?)
    }

    fn with_index<T>(&self, f: impl FnOnce(&Index) -> PyResult<T>) -> PyResult<T> {
        let index = self
            .index
            .read()
            .map_err(|e| KernelError::new_err(e.to_string()))?;
        f(&index)
    }
}

#[pymethods]
impl Kernel {
    /// Opens the vault at `path`, building or refreshing its index.
    #[new]
    fn new(py: Python<'_>, path: &str) -> PyResult<Self> {
        py.detach(|| {
            let vault = Vault::open(path).map_err(store_err)?;
            let index = Index::open(&vault).map_err(index_err)?;
            Ok(Self {
                vault,
                index: RwLock::new(index),
            })
        })
    }

    /// The vault's title from `edda.toml`.
    #[getter]
    fn title(&self) -> &str {
        &self.vault.config().title
    }

    /// Every page's id, title, description, type, tags and status.
    fn pages(&self, py: Python<'_>) -> PyResult<String> {
        py.detach(|| {
            let pages = self.vault.pages().map_err(store_err)?;
            let raw = self.vault.raw().list().map_err(store_err)?;
            self.with_index(|index| {
                let summaries: Vec<Value> = pages
                    .iter()
                    .map(|p| {
                        let fm = &p.frontmatter;
                        let mut sources: Vec<String> = fm
                            .sources
                            .iter()
                            .filter_map(|s| resolve_source(&raw, index, &s.resource))
                            .collect();
                        sources.sort();
                        sources.dedup();
                        json!({
                            "id": p.id.as_str(),
                            "title": p.title(),
                            "description": fm.description,
                            "type": fm.kind,
                            "tags": fm.tags,
                            "status": fm.status(),
                            "stale_after": fm.stale_after,
                            "sources": sources,
                        })
                    })
                    .collect();
                to_json(&summaries)
            })
        })
    }

    /// `search(query, k)`: ranked pages for a query, with snippets.
    #[pyo3(signature = (query, k = 10))]
    fn search(&self, py: Python<'_>, query: &str, k: usize) -> PyResult<String> {
        py.detach(|| self.with_index(|index| to_json(&index.search(query, k).map_err(index_err)?)))
    }

    /// Pages related to a text, such as a new source, best first: BM25 matches, then their link neighbours.
    #[pyo3(signature = (text, k = 10))]
    fn related(&self, py: Python<'_>, text: &str, k: usize) -> PyResult<String> {
        py.detach(|| self.with_index(|index| to_json(&index.related(text, k).map_err(index_err)?)))
    }

    /// `read_page(id)`: frontmatter, body, rendered HTML, revision and citations.
    #[pyo3(signature = (id, link_prefix = "/page/"))]
    fn read_page(&self, py: Python<'_>, id: &str, link_prefix: &str) -> PyResult<String> {
        let id = page_id(id)?;
        py.detach(|| {
            let page = self.vault.read_page(&id).map_err(store_err)?;
            let fm = &page.frontmatter;
            let analysis = page.analyze();
            let raw = self.vault.raw().list().map_err(store_err)?;
            let citations: Vec<Value> = self.with_index(|index| {
                Ok(fm
                    .sources
                    .iter()
                    .map(|source| {
                        let label = source.id.clone().unwrap_or_default();
                        let hash = resolve_source(&raw, index, &source.resource);
                        json!({
                            "id": label,
                            "resource": source.resource.to_string(),
                            "title": source.title,
                            "note": analysis.footnotes.get(&label),
                            "raw": hash,
                            "cited": analysis.citations.contains(&label),
                        })
                    })
                    .collect())
            })?;
            // index.md and log.md are the kernel's own output, not pages it holds to the page rules.
            let issues: Vec<String> = if page.id.is_generated() {
                Vec::new()
            } else {
                self.vault
                    .check(&page)
                    .map_err(store_err)?
                    .into_iter()
                    .map(|issue| issue.to_string())
                    .collect()
            };
            to_json(&json!({
                "id": page.id.as_str(),
                "title": page.title(),
                "rev": fm.edda_rev,
                "frontmatter": fm,
                "body": page.body,
                "html": page.render_html(link_prefix),
                "citations": citations,
                "issues": issues,
            }))
        })
    }

    /// `read_source(hash, span)`: a raw source's text, lines `start` to `end` of it (1-based, inclusive), or one
    /// `page` of a source converted from a paged document.
    #[pyo3(signature = (hash, start = None, end = None, page = None))]
    fn read_source(
        &self,
        py: Python<'_>,
        hash: &str,
        start: Option<usize>,
        end: Option<usize>,
        page: Option<u64>,
    ) -> PyResult<String> {
        py.detach(|| {
            let raw = self.vault.raw();
            let entry = raw.resolve(hash).map_err(store_err)?;
            let full = raw.read_text(&entry, None).map_err(store_err)?;
            let marked = edda_store::pages::pages(&full);
            let lines = match (page, start, end) {
                (Some(p), _, _) => {
                    Some(edda_store::pages::page_lines(&full, p).ok_or_else(|| {
                        store_err(StoreError::NoSuchPage {
                            source_hash: entry.hash.clone(),
                            page: p,
                            last: marked.last().copied().unwrap_or(0),
                        })
                    })?)
                }
                (None, None, None) => None,
                (None, s, e) => Some(s.unwrap_or(1)..=e.unwrap_or(usize::MAX)),
            };
            let text = raw.read_text(&entry, lines).map_err(store_err)?;
            let meta = &entry.meta;
            to_json(&json!({
                "hash": entry.hash,
                "title": meta.title,
                "url": meta.url,
                "added": meta.added,
                "driver": meta.driver,
                "derived_from": meta.derived_from,
                "converter": meta.converter,
                "method": meta.method,
                "pages": marked.len(),
                "text": text,
            }))
        })
    }

    /// Every raw source: its hash, title, URL, when it was added and the driver that imported it.
    fn sources(&self, py: Python<'_>) -> PyResult<String> {
        py.detach(|| {
            let raw = self.vault.raw().list().map_err(store_err)?;
            let sources: Vec<Value> = raw
                .iter()
                .map(|e| {
                    let m = &e.meta;
                    json!({
                        "hash": e.hash,
                        "title": m.title,
                        "url": m.url,
                        "added": m.added,
                        "driver": m.driver,
                        "derived_from": m.derived_from,
                        "method": m.method,
                    })
                })
                .collect();
            to_json(&sources)
        })
    }

    /// `links(id)`: outgoing links and backlinks.
    fn links(&self, py: Python<'_>, id: &str) -> PyResult<String> {
        let id = page_id(id)?;
        py.detach(|| {
            if !self.vault.page_exists(&id) {
                return Err(NotFoundError::new_err(format!("no page {id}")));
            }
            self.with_index(|index| to_json(&index.links(&id)))
        })
    }

    /// `propose_edit(page, base_rev, edits, rationale)`: text edits to a page; `edits_json` is a list of
    /// `{"old": ..., "new": ...}`.
    #[pyo3(signature = (job, page, base_rev, edits_json, rationale))]
    fn propose_edit(
        &self,
        py: Python<'_>,
        job: &str,
        page: &str,
        base_rev: u64,
        edits_json: &str,
        rationale: &str,
    ) -> PyResult<String> {
        let edits: Vec<TextEdit> = serde_json::from_str(edits_json)
            .map_err(|e| InvalidRequestError::new_err(format!("edits: {e}")))?;
        let change = Change::Edit {
            page: page.to_owned(),
            base_rev,
            edits,
        };
        py.detach(|| {
            self.proposal_json(
                &self
                    .vault
                    .propose(job, change, rationale)
                    .map_err(store_err)?,
            )
        })
    }

    /// `propose_page(title, body, rationale)`: a new page, created as a draft.
    #[pyo3(signature = (job, page, kind, title, description, tags, body, rationale))]
    #[allow(clippy::too_many_arguments)]
    fn propose_page(
        &self,
        py: Python<'_>,
        job: &str,
        page: &str,
        kind: &str,
        title: &str,
        description: &str,
        tags: Vec<String>,
        body: &str,
        rationale: &str,
    ) -> PyResult<String> {
        let change = Change::NewPage {
            page: page.to_owned(),
            kind: kind.to_owned(),
            title: title.to_owned(),
            description: description.to_owned(),
            tags,
            body: body.to_owned(),
        };
        py.detach(|| {
            self.proposal_json(
                &self
                    .vault
                    .propose(job, change, rationale)
                    .map_err(store_err)?,
            )
        })
    }

    /// `cite(proposal, claim, source_span)`: back a claim in a proposal's added text with a span of a raw source.
    #[pyo3(signature = (proposal, claim, source, locator, source_id = None))]
    fn cite(
        &self,
        py: Python<'_>,
        proposal: &str,
        claim: &str,
        source: &str,
        locator: &str,
        source_id: Option<&str>,
    ) -> PyResult<String> {
        py.detach(|| {
            let cited = self
                .vault
                .cite(proposal, claim, source, locator, source_id)
                .map_err(store_err)?;
            self.proposal_json(&cited)
        })
    }

    /// `flag(page, kind, note)`: a contradiction, outdated claim or gap, for review.
    fn flag(
        &self,
        py: Python<'_>,
        job: &str,
        page: &str,
        kind: &str,
        note: &str,
    ) -> PyResult<String> {
        let change = Change::Flag {
            page: page.to_owned(),
            flag: kind.to_owned(),
            note: note.to_owned(),
        };
        py.detach(|| self.proposal_json(&self.vault.propose(job, change, note).map_err(store_err)?))
    }

    /// Stores a raw source and journals it, then refreshes the index. Returns the source and whether it was new.
    #[pyo3(signature = (
        data, extension = None, title = None, url = None, driver = None, by = "owner", derived_from = None,
        converter = None, method = None
    ))]
    #[allow(clippy::too_many_arguments)]
    fn add_source(
        &self,
        py: Python<'_>,
        data: &[u8],
        extension: Option<&str>,
        title: Option<String>,
        url: Option<String>,
        driver: Option<String>,
        by: &str,
        derived_from: Option<String>,
        converter: Option<String>,
        method: Option<String>,
    ) -> PyResult<String> {
        let meta = RawMeta {
            title,
            url,
            driver,
            derived_from,
            converter,
            method,
            ..RawMeta::default()
        };
        py.detach(|| {
            let added = edda_kernel::add_source(&self.vault, data, extension, &meta, by)
                .map_err(gate_err)?;
            if added.entry.is_some() {
                let fresh = Index::open(&self.vault).map_err(index_err)?;
                *self
                    .index
                    .write()
                    .map_err(|e| KernelError::new_err(e.to_string()))? = fresh;
            }
            let m = &added.source.meta;
            to_json(&json!({
                "source": {
                    "hash": added.source.hash,
                    "title": m.title,
                    "url": m.url,
                    "added": m.added,
                    "driver": m.driver,
                    "derived_from": m.derived_from,
                    "method": m.method,
                },
                "added": added.entry.is_some(),
            }))
        })
    }

    /// Accepts a pending proposal on behalf of `by` and applies it, then refreshes the index. Returns the journal entry.
    #[pyo3(signature = (proposal, by = "owner"))]
    fn accept(&self, py: Python<'_>, proposal: &str, by: &str) -> PyResult<String> {
        py.detach(|| {
            let outcome = edda_kernel::accept(&self.vault, proposal, by).map_err(gate_err)?;
            let fresh = Index::open(&self.vault).map_err(index_err)?;
            *self
                .index
                .write()
                .map_err(|e| KernelError::new_err(e.to_string()))? = fresh;
            to_json(&outcome.entry)
        })
    }

    /// Accepts several pending proposals as one change, so pages that link to each other can land together, then
    /// refreshes the index. Returns the journal entries, in the order they were written.
    #[pyo3(signature = (proposals, by = "owner"))]
    fn accept_together(
        &self,
        py: Python<'_>,
        proposals: Vec<String>,
        by: &str,
    ) -> PyResult<String> {
        py.detach(|| {
            let outcomes =
                edda_kernel::accept_together(&self.vault, &proposals, by).map_err(gate_err)?;
            let fresh = Index::open(&self.vault).map_err(index_err)?;
            *self
                .index
                .write()
                .map_err(|e| KernelError::new_err(e.to_string()))? = fresh;
            let entries: Vec<_> = outcomes.iter().map(|o| &o.entry).collect();
            to_json(&entries)
        })
    }

    /// Restores a page to an earlier journaled revision as a new revision, then refreshes the index. Returns the
    /// journal entry.
    #[pyo3(signature = (page, rev, by = "owner"))]
    fn rollback(&self, py: Python<'_>, page: &str, rev: u64, by: &str) -> PyResult<String> {
        py.detach(|| {
            let restored = edda_kernel::rollback(&self.vault, page, rev, by).map_err(gate_err)?;
            let fresh = Index::open(&self.vault).map_err(index_err)?;
            *self
                .index
                .write()
                .map_err(|e| KernelError::new_err(e.to_string()))? = fresh;
            to_json(&restored.entry)
        })
    }

    /// Rejects a pending proposal; the page is untouched. Returns the journal entry.
    #[pyo3(signature = (proposal, reason, by = "owner"))]
    fn reject(&self, py: Python<'_>, proposal: &str, reason: &str, by: &str) -> PyResult<String> {
        py.detach(|| {
            to_json(
                &edda_kernel::reject(&self.vault, proposal, by, reason)
                    .map_err(gate_err)?
                    .entry,
            )
        })
    }

    /// Every proposal with its preview, oldest first.
    fn proposals(&self, py: Python<'_>) -> PyResult<String> {
        py.detach(|| {
            let all = self.vault.proposals().map_err(store_err)?;
            let views: PyResult<Vec<Value>> = all.iter().map(|p| self.proposal_view(p)).collect();
            to_json(&views?)
        })
    }

    /// One proposal with its preview: the diff and the rules the result breaks.
    fn proposal(&self, py: Python<'_>, id: &str) -> PyResult<String> {
        py.detach(|| self.proposal_json(&self.vault.proposal(id).map_err(store_err)?))
    }

    /// Orphan pages and broken links across the vault.
    fn graph_issues(&self, py: Python<'_>) -> PyResult<String> {
        py.detach(|| {
            self.with_index(|index| {
                to_json(&json!({"orphans": index.orphans(), "broken_links": index.broken_links()}))
            })
        })
    }

    /// The journal entries for one page, oldest first.
    fn history(&self, py: Python<'_>, id: &str) -> PyResult<String> {
        let id = page_id(id)?;
        py.detach(|| {
            let journal = Journal::open(self.vault.journal_dir()).map_err(journal_err)?;
            to_json(&journal.history(id.as_str()).map_err(journal_err)?)
        })
    }

    /// Regenerates `log.md` from the journal and returns how many entries it lists.
    fn write_log(&self, py: Python<'_>) -> PyResult<usize> {
        py.detach(|| edda_journal::write_log(&self.vault).map_err(journal_err))
    }

    /// Checks the journal's hash chain and returns how many entries it has.
    fn verify_journal(&self, py: Python<'_>) -> PyResult<u64> {
        py.detach(|| {
            let journal = Journal::open(self.vault.journal_dir()).map_err(journal_err)?;
            journal.verify().map_err(journal_err)
        })
    }

    /// Rebuilds `.edda/index` from the files, or refreshes it if `force` is false and the files changed.
    #[pyo3(signature = (force = false))]
    fn refresh_index(&self, py: Python<'_>, force: bool) -> PyResult<()> {
        py.detach(|| {
            let fresh = if force {
                Index::rebuild(&self.vault)
            } else {
                Index::open(&self.vault)
            };
            let fresh = fresh.map_err(index_err)?;
            *self
                .index
                .write()
                .map_err(|e| KernelError::new_err(e.to_string()))? = fresh;
            Ok(())
        })
    }
}

/// The Open Knowledge Format version the kernel reads and writes.
#[pyfunction]
fn okf_version() -> &'static str {
    edda_store::OKF_VERSION
}

#[pymodule]
fn _kernel(m: &Bound<'_, PyModule>) -> PyResult<()> {
    let py = m.py();
    m.add_class::<Kernel>()?;
    m.add_function(wrap_pyfunction!(okf_version, m)?)?;
    m.add("KernelError", py.get_type::<KernelError>())?;
    m.add("NotFoundError", py.get_type::<NotFoundError>())?;
    m.add("InvalidRequestError", py.get_type::<InvalidRequestError>())?;
    m.add("ConflictError", py.get_type::<ConflictError>())?;
    Ok(())
}
