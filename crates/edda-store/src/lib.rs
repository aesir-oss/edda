//! Reads and writes an Edda vault. This crate is the only code that knows the page format: the OKF v0.2 frontmatter,
//! footnote citations, root-relative links and the generated `index.md` and `log.md`.

mod config;
mod error;
mod frontmatter;
mod fsutil;
mod generated;
pub mod markdown;
mod page;
mod page_id;
mod proposal;
mod raw;
mod vault;

pub use config::VaultConfig;
pub use error::{Result, StoreError};
pub use frontmatter::{Extra, Frontmatter, Generated, Resource, Source, Verification};
pub use generated::{LogEntry, render_index, render_log};
pub use page::{Context, Issue, Page};
pub use page_id::PageId;
pub use proposal::{Change, Citation, OWNER, Preview, Proposal, Status, TextEdit};
pub use raw::{RawEntry, RawMeta, RawStore, sha256_hex};
pub use vault::{Snapshot, Vault};

/// The Open Knowledge Format version this store reads and writes.
pub const OKF_VERSION: &str = "0.2";
