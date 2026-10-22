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
