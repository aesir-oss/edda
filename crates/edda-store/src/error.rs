use std::path::PathBuf;

/// Everything that can go wrong reading or writing a vault.
#[derive(Debug, thiserror::Error)]
pub enum StoreError {
    #[error("{path}: {source}")]
    Io {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("{0} is not an Edda vault: it has no wiki/ directory")]
    NotAVault(PathBuf),
    #[error("invalid page id {id:?}: {reason}")]
    InvalidPageId { id: String, reason: &'static str },
    #[error("no page {0}")]
    PageNotFound(String),
    #[error("{page}: {reason}")]
    Frontmatter { page: String, reason: String },
    #[error("invalid source hash {0:?}: expected hex digits of a SHA-256")]
    InvalidHash(String),
    #[error("no raw source matches {0}")]
    SourceNotFound(String),
    #[error("{prefix} matches more than one raw source")]
    AmbiguousSource { prefix: String },
    #[error("raw source {hash} is corrupt: its content hashes to {actual}")]
    CorruptSource { hash: String, actual: String },
    #[error("{path}: {reason}")]
    Config { path: PathBuf, reason: String },
    #[error("no proposal {0}")]
    ProposalNotFound(String),
    #[error("proposal {0} is no longer pending")]
    NotPending(String),
    #[error("{page} is at revision {current}, not {base_rev}; read it again and redo the edit")]
    Conflict {
        page: String,
        base_rev: u64,
        current: u64,
    },
    #[error("the text to replace appears {count} times in {page}, not once: {old:?}")]
    EditDoesNotApply {
        page: String,
        old: String,
        count: usize,
    },
    #[error("{0} already exists")]
    PageExists(String),
    #[error("the claim is not in the text this proposal adds: {0:?}")]
    ClaimNotFound(String),
}

impl StoreError {
    pub(crate) fn io(path: impl Into<PathBuf>, source: std::io::Error) -> Self {
        Self::Io {
            path: path.into(),
            source,
        }
    }
}

pub type Result<T, E = StoreError> = std::result::Result<T, E>;
