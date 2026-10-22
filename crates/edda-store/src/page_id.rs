use std::fmt;
use std::path::{Component, Path, PathBuf};

use crate::error::{Result, StoreError};

/// A page's path inside the bundle, with `/` separators and no leading slash: `attention.md`, `people/ada.md`.
///
/// Construction is the only check against path traversal, so every path that reaches the file system from outside
/// (the API, a link in a page) goes through [`PageId::new`].
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct PageId(String);

impl PageId {
    /// Validates a page id. A leading `/` (a root-relative link) is accepted and dropped.
    pub fn new(id: &str) -> Result<Self> {
        let invalid = |reason| StoreError::InvalidPageId {
            id: id.to_owned(),
            reason,
        };
        let trimmed = id.strip_prefix('/').unwrap_or(id);
        if trimmed.is_empty() {
            return Err(invalid("empty"));
        }
        if !trimmed.ends_with(".md") {
            return Err(invalid("pages are .md files"));
        }
        if trimmed.contains('\\') || trimmed.contains('\0') {
            return Err(invalid("contains a backslash or NUL"));
        }
        for part in trimmed.split('/') {
            match part {
                "" => return Err(invalid("empty path segment")),
                "." | ".." => return Err(invalid("contains . or .. segments")),
                _ if part.starts_with('.') => return Err(invalid("hidden path segment")),
                _ => {}
            }
        }
        Ok(Self(trimmed.to_owned()))
    }

    pub(crate) fn from_relative(path: &Path) -> Option<Self> {
        let parts: Option<Vec<&str>> = path
            .components()
            .map(|c| match c {
                Component::Normal(s) => s.to_str(),
                _ => None,
            })
            .collect();
        Self::new(&parts?.join("/")).ok()
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }

    /// The root-relative link to this page, as written in Markdown: `/attention.md`.
    pub fn link(&self) -> String {
        format!("/{}", self.0)
    }

    /// The directory part of the id, `""` at the bundle root.
    pub fn dir(&self) -> &str {
        self.0.rsplit_once('/').map_or("", |(dir, _)| dir)
    }

    /// True for the files the kernel generates at the bundle root.
    pub fn is_generated(&self) -> bool {
        self.0 == "index.md" || self.0 == "log.md"
    }

    pub(crate) fn to_path(&self, wiki: &Path) -> PathBuf {
        self.0
            .split('/')
            .fold(wiki.to_path_buf(), |p, part| p.join(part))
    }
}

impl fmt::Display for PageId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_root_relative_and_nested_ids() {
        assert_eq!(
            PageId::new("/attention.md").unwrap().as_str(),
            "attention.md"
        );
        let nested = PageId::new("people/ada.md").unwrap();
        assert_eq!(nested.dir(), "people");
        assert_eq!(nested.link(), "/people/ada.md");
        assert_eq!(nested.to_string(), "people/ada.md");
        assert_eq!(PageId::new("a.md").unwrap().dir(), "");
    }

    #[test]
    fn refuses_traversal_and_non_pages() {
        for bad in [
            "",
            "/",
            "../x.md",
            "a/../b.md",
            "a//b.md",
            "x.txt",
            ".hidden/a.md",
            "a\\b.md",
            "./a.md",
        ] {
            assert!(PageId::new(bad).is_err(), "{bad} was accepted");
        }
    }

    #[test]
    fn knows_the_generated_files() {
        assert!(PageId::new("index.md").unwrap().is_generated());
        assert!(PageId::new("log.md").unwrap().is_generated());
        assert!(!PageId::new("people/index.md").unwrap().is_generated());
    }

    #[test]
    fn maps_relative_paths() {
        assert_eq!(
            PageId::from_relative(Path::new("a/b.md")).unwrap().as_str(),
            "a/b.md"
        );
        assert!(PageId::from_relative(Path::new("../b.md")).is_none());
    }
}
