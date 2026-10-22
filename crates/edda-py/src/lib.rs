//! The `edda._kernel` Python module: the kernel's read-side system calls, built by maturin.
//!
//! Results cross into Python as JSON strings that `edda.kernel` validates into Pydantic models, which keeps this
//! layer thin and the schema in one place.

use std::sync::RwLock;

use edda_index::{Index, IndexError};
use edda_journal::{Journal, JournalError};
use edda_store::{PageId, RawEntry, Resource, StoreError, Vault};
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

fn store_err(e: StoreError) -> PyErr {
    match e {
        StoreError::PageNotFound(_) | StoreError::SourceNotFound(_) => {
            NotFoundError::new_err(e.to_string())
        }
        StoreError::InvalidPageId { .. }
        | StoreError::InvalidHash(_)
        | StoreError::AmbiguousSource { .. } => InvalidRequestError::new_err(e.to_string()),
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

    /// `read_source(hash, span)`: a raw source's text, or lines `start` to `end` of it (1-based, inclusive).
    #[pyo3(signature = (hash, start = None, end = None))]
    fn read_source(
        &self,
        py: Python<'_>,
        hash: &str,
        start: Option<usize>,
        end: Option<usize>,
    ) -> PyResult<String> {
        py.detach(|| {
            let raw = self.vault.raw();
            let entry = raw.resolve(hash).map_err(store_err)?;
            let lines = match (start, end) {
                (None, None) => None,
                (s, e) => Some(s.unwrap_or(1)..=e.unwrap_or(usize::MAX)),
            };
            let text = raw.read_text(&entry, lines).map_err(store_err)?;
            let meta = &entry.meta;
            to_json(&json!({
                "hash": entry.hash,
                "title": meta.title,
                "url": meta.url,
                "added": meta.added,
                "driver": meta.driver,
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
                    json!({"hash": e.hash, "title": m.title, "url": m.url, "added": m.added, "driver": m.driver})
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
    Ok(())
}
