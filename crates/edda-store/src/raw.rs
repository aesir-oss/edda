use std::fs;
use std::ops::RangeInclusive;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::error::{Result, StoreError};
use crate::frontmatter::Resource;
use crate::fsutil::{read_to_string, write_atomic};

const META_SUFFIX: &str = ".meta.toml";

/// The sidecar `<hash>.meta.toml` that describes a raw source. Unknown keys are kept.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct RawMeta {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
    /// The URL the source was fetched from, which pages cite as their `resource`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub url: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub added: Option<String>,
    /// The importer that produced the file.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub driver: Option<String>,
    /// For text converted from another source, such as Markdown from a PDF: that source's hash.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub derived_from: Option<String>,
    /// The converter and its version, such as `docling 2.96.1`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub converter: Option<String>,
    /// How the text was got: `native` (the file's own structure), `layout` (a layout engine) or `ocr`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub method: Option<String>,
    #[serde(flatten)]
    pub extra: toml::Table,
}

/// One stored source: its SHA-256, the file holding it and its metadata.
#[derive(Debug, Clone, PartialEq)]
pub struct RawEntry {
    pub hash: String,
    pub path: PathBuf,
    pub meta: RawMeta,
}

/// `raw/`: sources stored under the SHA-256 of their content. A stored file is never rewritten.
#[derive(Debug, Clone)]
pub struct RawStore {
    dir: PathBuf,
}

/// The SHA-256 of some bytes, in lowercase hex.
pub fn sha256_hex(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

impl RawStore {
    pub fn new(dir: impl Into<PathBuf>) -> Self {
        Self { dir: dir.into() }
    }

    pub fn dir(&self) -> &Path {
        &self.dir
    }

    /// Stores a source and its metadata. Adding content that is already stored changes nothing.
    pub fn add(&self, bytes: &[u8], extension: Option<&str>, meta: &RawMeta) -> Result<RawEntry> {
        let hash = sha256_hex(bytes);
        if let Ok(existing) = self.resolve(&hash) {
            return Ok(existing);
        }
        let name = match extension
            .map(|e| e.trim_start_matches('.'))
            .filter(|e| !e.is_empty())
        {
            Some(ext) => format!("{hash}.{ext}"),
            None => hash.clone(),
        };
        let path = self.dir.join(name);
        write_atomic(&path, bytes)?;
        let meta_path = self.meta_path(&hash);
        let toml = toml::to_string(meta).map_err(|e| StoreError::Config {
            path: meta_path.clone(),
            reason: e.to_string(),
        })?;
        write_atomic(&meta_path, toml.as_bytes())?;
        Ok(RawEntry {
            hash,
            path,
            meta: meta.clone(),
        })
    }

    /// Every stored source, sorted by hash. Files whose name is not a SHA-256 are ignored.
    pub fn list(&self) -> Result<Vec<RawEntry>> {
        let mut entries = Vec::new();
        let read = match fs::read_dir(&self.dir) {
            Ok(read) => read,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(entries),
            Err(e) => return Err(StoreError::io(&self.dir, e)),
        };
        for item in read {
            let path = item.map_err(|e| StoreError::io(&self.dir, e))?.path();
            let Some(name) = path.file_name().and_then(|n| n.to_str()) else {
                continue;
            };
            if name.ends_with(META_SUFFIX) || !path.is_file() {
                continue;
            }
            let hash = name.split('.').next().unwrap_or_default();
            if hash.len() == 64 && is_hex(hash) {
                let meta = self.read_meta(hash)?;
                entries.push(RawEntry {
                    hash: hash.to_owned(),
                    path,
                    meta,
                });
            }
        }
        entries.sort_by(|a, b| a.hash.cmp(&b.hash));
        Ok(entries)
    }

    /// Finds the source a full hash or a unique prefix of one names.
    pub fn resolve(&self, prefix: &str) -> Result<RawEntry> {
        let prefix = prefix.to_ascii_lowercase();
        if prefix.is_empty() || prefix.len() > 64 || !is_hex(&prefix) {
            return Err(StoreError::InvalidHash(prefix));
        }
        let mut found = self
            .list()?
            .into_iter()
            .filter(|e| e.hash.starts_with(&prefix));
        match (found.next(), found.next()) {
            (Some(entry), None) => Ok(entry),
            (None, _) => Err(StoreError::SourceNotFound(prefix)),
            (Some(_), Some(_)) => Err(StoreError::AmbiguousSource { prefix }),
        }
    }

    /// The source whose metadata records this URL.
    pub fn find_url(&self, url: &str) -> Result<Option<RawEntry>> {
        Ok(self
            .list()?
            .into_iter()
            .find(|e| e.meta.url.as_deref() == Some(url)))
    }

    /// True when a page's `resource` names a stored source, by URL or by `raw:<hash>`.
    pub fn resolves(&self, resource: &Resource) -> bool {
        match resource {
            Resource::Raw(prefix) => self.resolve(prefix).is_ok(),
            Resource::Url(url) => matches!(self.find_url(url), Ok(Some(_))),
        }
    }

    /// The source's bytes, checked against its hash.
    pub fn read(&self, entry: &RawEntry) -> Result<Vec<u8>> {
        let bytes = fs::read(&entry.path).map_err(|e| StoreError::io(&entry.path, e))?;
        let actual = sha256_hex(&bytes);
        if actual != entry.hash {
            return Err(StoreError::CorruptSource {
                hash: entry.hash.clone(),
                actual,
            });
        }
        Ok(bytes)
    }

    /// The source as text, or the given 1-based, inclusive range of its lines.
    pub fn read_text(
        &self,
        entry: &RawEntry,
        lines: Option<RangeInclusive<usize>>,
    ) -> Result<String> {
        let bytes = self.read(entry)?;
        let text = String::from_utf8_lossy(&bytes);
        Ok(match lines {
            None => text.into_owned(),
            Some(range) => {
                let (start, end) = (*range.start().max(&1), *range.end());
                text.split_inclusive('\n')
                    .skip(start - 1)
                    .take((end + 1).saturating_sub(start))
                    .collect()
            }
        })
    }

    fn meta_path(&self, hash: &str) -> PathBuf {
        self.dir.join(format!("{hash}{META_SUFFIX}"))
    }

    fn read_meta(&self, hash: &str) -> Result<RawMeta> {
        let path = self.meta_path(hash);
        if !path.exists() {
            return Ok(RawMeta::default());
        }
        toml::from_str(&read_to_string(&path)?).map_err(|e| StoreError::Config {
            path,
            reason: e.to_string(),
        })
    }
}

fn is_hex(s: &str) -> bool {
    s.bytes().all(|b| b.is_ascii_hexdigit())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn store() -> (tempfile::TempDir, RawStore) {
        let dir = tempfile::tempdir().unwrap();
        let store = RawStore::new(dir.path().join("raw"));
        (dir, store)
    }

    fn meta(url: &str) -> RawMeta {
        RawMeta {
            title: Some("Notes".into()),
            url: Some(url.into()),
            ..RawMeta::default()
        }
    }

    #[test]
    fn stores_content_under_its_hash_once() {
        let (_dir, store) = store();
        assert!(store.list().unwrap().is_empty());
        let a = store
            .add(
                b"line 1\nline 2\nline 3\n",
                Some(".md"),
                &meta("https://a.example"),
            )
            .unwrap();
        assert_eq!(a.hash, sha256_hex(b"line 1\nline 2\nline 3\n"));
        assert!(a.path.ends_with(format!("{}.md", a.hash)));
        let again = store
            .add(b"line 1\nline 2\nline 3\n", None, &RawMeta::default())
            .unwrap();
        assert_eq!(
            again, a,
            "re-adding must not rewrite the file or its metadata"
        );
        let b = store.add(b"other", None, &RawMeta::default()).unwrap();
        assert!(b.path.ends_with(&b.hash));
        assert_eq!(store.list().unwrap().len(), 2);
        assert_eq!(
            store
                .resolve(&a.hash[..8].to_uppercase())
                .unwrap()
                .meta
                .title
                .as_deref(),
            Some("Notes")
        );
        assert_eq!(
            store.find_url("https://a.example").unwrap().unwrap().hash,
            a.hash
        );
        assert!(store.resolves(&Resource::Url("https://a.example".into())));
        assert!(store.resolves(&Resource::Raw(a.hash[..6].into())));
        assert!(!store.resolves(&Resource::Url("https://b.example".into())));
        assert_eq!(
            store.read_text(&a, Some(2..=3)).unwrap(),
            "line 2\nline 3\n"
        );
        assert_eq!(store.read_text(&a, Some(0..=1)).unwrap(), "line 1\n");
        assert_eq!(store.read_text(&a, None).unwrap().lines().count(), 3);
    }

    #[test]
    fn refuses_bad_ambiguous_and_missing_hashes() {
        let (_dir, store) = store();
        assert!(matches!(
            store.resolve("xyz"),
            Err(StoreError::InvalidHash(_))
        ));
        assert!(matches!(store.resolve(""), Err(StoreError::InvalidHash(_))));
        assert!(matches!(
            store.resolve("ab"),
            Err(StoreError::SourceNotFound(_))
        ));
        let mut n = 0u32;
        let first = store.add(b"0", None, &RawMeta::default()).unwrap();
        let clash = loop {
            n += 1;
            let bytes = n.to_string();
            if sha256_hex(bytes.as_bytes()).as_bytes()[0] == first.hash.as_bytes()[0] {
                break bytes;
            }
        };
        store
            .add(clash.as_bytes(), None, &RawMeta::default())
            .unwrap();
        assert!(matches!(
            store.resolve(&first.hash[..1]),
            Err(StoreError::AmbiguousSource { .. })
        ));
    }

    #[test]
    fn detects_corruption_and_ignores_stray_files() {
        let (_dir, store) = store();
        let entry = store
            .add(b"original", Some("txt"), &RawMeta::default())
            .unwrap();
        fs::write(store.dir().join("README"), "not a source").unwrap();
        fs::create_dir(store.dir().join(format!("{}x", "a".repeat(63)))).unwrap();
        assert_eq!(store.list().unwrap().len(), 1);
        fs::write(&entry.path, "tampered").unwrap();
        assert!(matches!(
            store.read(&entry),
            Err(StoreError::CorruptSource { .. })
        ));
        fs::write(
            store.dir().join(format!("{}.meta.toml", entry.hash)),
            "not = [toml",
        )
        .unwrap();
        assert!(matches!(store.list(), Err(StoreError::Config { .. })));
    }
}
