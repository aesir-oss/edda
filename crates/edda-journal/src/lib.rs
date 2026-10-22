//! Edda's journal: an append-only record of every change to a vault, in JSON Lines. Each entry carries the SHA-256
//! of the one before it, so editing or dropping a past entry breaks the chain and [`Journal::verify`] finds it.

use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};

use edda_store::{LogEntry, PageId};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};

/// The `prev` of the first entry.
pub const GENESIS: &str = "0000000000000000000000000000000000000000000000000000000000000000";

/// Entries per segment file before the journal starts the next one.
pub const SEGMENT_LEN: u64 = 10_000;

#[derive(Debug, thiserror::Error)]
pub enum JournalError {
    #[error("{path}: {source}")]
    Io {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("{path} line {line}: {source}")]
    Parse {
        path: PathBuf,
        line: usize,
        #[source]
        source: serde_json::Error,
    },
    #[error("journal chain broken at entry {seq}: {reason}")]
    Broken { seq: u64, reason: String },
}

pub type Result<T, E = JournalError> = std::result::Result<T, E>;

fn io(path: &Path) -> impl FnOnce(std::io::Error) -> JournalError + '_ {
    move |source| JournalError::Io {
        path: path.to_owned(),
        source,
    }
}

/// One recorded change.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Entry {
    /// 1 for the first entry, then one more for each.
    pub seq: u64,
    /// RFC 3339, UTC, to the second.
    pub at: String,
    /// Who made the change: `owner`, or a job id.
    pub actor: String,
    /// What kind of change: `create_page`, `edit_page`, `add_source`, `verify`, ...
    pub action: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub page: Option<String>,
    /// The page's `edda_rev` after the change.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub rev: Option<u64>,
    pub summary: String,
    #[serde(default, skip_serializing_if = "Value::is_null")]
    pub data: Value,
    /// The hash of the previous entry, or [`GENESIS`].
    pub prev: String,
    /// The SHA-256 of this entry without its `hash`.
    pub hash: String,
}

/// An entry to append; the journal fills in `seq`, `prev`, `hash` and, when it is `None`, `at`.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct NewEntry {
    pub at: Option<String>,
    pub actor: String,
    pub action: String,
    pub page: Option<String>,
    pub rev: Option<u64>,
    pub summary: String,
    pub data: Value,
}

#[derive(Serialize)]
struct Hashed<'a> {
    seq: u64,
    at: &'a str,
    actor: &'a str,
    action: &'a str,
    #[serde(skip_serializing_if = "Option::is_none")]
    page: Option<&'a str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    rev: Option<u64>,
    summary: &'a str,
    #[serde(skip_serializing_if = "Value::is_null")]
    data: &'a Value,
    prev: &'a str,
}

impl Entry {
    /// The hash this entry should carry, computed from every other field.
    pub fn compute_hash(&self) -> String {
        let hashed = Hashed {
            seq: self.seq,
            at: &self.at,
            actor: &self.actor,
            action: &self.action,
            page: self.page.as_deref(),
            rev: self.rev,
            summary: &self.summary,
            data: &self.data,
            prev: &self.prev,
        };
        let bytes = serde_json::to_vec(&hashed).unwrap_or_default();
        hex::encode(Sha256::digest(bytes))
    }
}

/// `journal/`: segment files `000001.jsonl`, `000002.jsonl`, ... holding [`SEGMENT_LEN`] entries each.
///
/// One process appends at a time; M1 has a single local user, and the scheduler takes the lock from M4.
#[derive(Debug)]
pub struct Journal {
    dir: PathBuf,
    last: Option<Entry>,
}

impl Journal {
    /// Opens the journal in `dir`, creating the directory if it is missing.
    pub fn open(dir: impl Into<PathBuf>) -> Result<Self> {
        let dir = dir.into();
        fs::create_dir_all(&dir).map_err(io(&dir))?;
        let last = match segments(&dir)?.last() {
            Some(segment) => read_segment(segment)?.pop(),
            None => None,
        };
        Ok(Self { dir, last })
    }

    pub fn last(&self) -> Option<&Entry> {
        self.last.as_ref()
    }

    /// Appends an entry, chained to the last one, and syncs it to disk before returning it.
    pub fn append(&mut self, new: NewEntry) -> Result<Entry> {
        let (seq, prev) = match &self.last {
            Some(last) => (last.seq + 1, last.hash.clone()),
            None => (1, GENESIS.to_owned()),
        };
        let at = new.at.unwrap_or_else(|| {
            jiff::Timestamp::now()
                .strftime("%Y-%m-%dT%H:%M:%SZ")
                .to_string()
        });
        let mut entry = Entry {
            seq,
            at,
            actor: new.actor,
            action: new.action,
            page: new.page,
            rev: new.rev,
            summary: new.summary,
            data: new.data,
            prev,
            hash: String::new(),
        };
        entry.hash = entry.compute_hash();
        let path = self
            .dir
            .join(format!("{:06}.jsonl", (seq - 1) / SEGMENT_LEN + 1));
        let mut line = serde_json::to_string(&entry).map_err(|source| JournalError::Parse {
            path: path.clone(),
            line: 0,
            source,
        })?;
        line.push('\n');
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)
            .map_err(io(&path))?;
        file.write_all(line.as_bytes())
            .and_then(|()| file.sync_all())
            .map_err(io(&path))?;
        self.last = Some(entry.clone());
        Ok(entry)
    }

    /// Every entry, oldest first.
    pub fn entries(&self) -> Result<Vec<Entry>> {
        let mut all = Vec::new();
        for segment in segments(&self.dir)? {
            all.extend(read_segment(&segment)?);
        }
        Ok(all)
    }

    /// Checks the whole chain: sequence numbers, links and hashes. Returns the number of entries.
    pub fn verify(&self) -> Result<u64> {
        let mut prev = GENESIS.to_owned();
        let mut count = 0;
        for entry in self.entries()? {
            count += 1;
            let broken = |reason: String| {
                Err(JournalError::Broken {
                    seq: entry.seq,
                    reason,
                })
            };
            if entry.seq != count {
                return broken(format!("expected entry {count}"));
            }
            if entry.prev != prev {
                return broken("prev does not match the previous entry's hash".into());
            }
            let actual = entry.compute_hash();
            if entry.hash != actual {
                return broken(format!(
                    "content hashes to {actual}, entry says {}",
                    entry.hash
                ));
            }
            prev = entry.hash;
        }
        Ok(count)
    }

    /// The entries for one page, oldest first.
    pub fn history(&self, page: &str) -> Result<Vec<Entry>> {
        Ok(self
            .entries()?
            .into_iter()
            .filter(|e| e.page.as_deref() == Some(page))
            .collect())
    }

    /// The entries as `log.md` lists them, with page titles from `title`.
    pub fn log_entries(&self, title: impl Fn(&PageId) -> Option<String>) -> Result<Vec<LogEntry>> {
        Ok(self
            .entries()?
            .into_iter()
            .map(|e| {
                let page = e.page.as_deref().and_then(|p| PageId::new(p).ok());
                let page_title = page.as_ref().and_then(&title);
                LogEntry {
                    at: e.at,
                    page,
                    page_title,
                    summary: e.summary,
                }
            })
            .collect())
    }
}

fn segments(dir: &Path) -> Result<Vec<PathBuf>> {
    let mut found = Vec::new();
    for item in fs::read_dir(dir).map_err(io(dir))? {
        let path = item.map_err(io(dir))?.path();
        let name = path
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or_default();
        if let Some(stem) = name.strip_suffix(".jsonl")
            && stem.len() == 6
            && stem.bytes().all(|b| b.is_ascii_digit())
        {
            found.push(path);
        }
    }
    found.sort();
    Ok(found)
}

fn read_segment(path: &Path) -> Result<Vec<Entry>> {
    let file = File::open(path).map_err(io(path))?;
    let mut entries = Vec::new();
    for (n, line) in BufReader::new(file).lines().enumerate() {
        let line = line.map_err(io(path))?;
        if line.trim().is_empty() {
            continue;
        }
        let entry = serde_json::from_str(&line).map_err(|source| JournalError::Parse {
            path: path.to_owned(),
            line: n + 1,
            source,
        })?;
        entries.push(entry);
    }
    Ok(entries)
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    fn new(page: Option<&str>, summary: &str) -> NewEntry {
        NewEntry {
            at: Some(format!("2026-10-21T14:{:02}:00Z", summary.len())),
            actor: "owner".into(),
            action: "edit_page".into(),
            page: page.map(Into::into),
            rev: page.map(|_| 2),
            summary: summary.into(),
            data: json!({"z": 1, "a": [1.5, "x"]}),
        }
    }

    #[test]
    fn appends_a_chain_that_verifies_and_survives_reopening() {
        let dir = tempfile::tempdir().unwrap();
        let mut journal = Journal::open(dir.path().join("journal")).unwrap();
        assert_eq!(journal.verify().unwrap(), 0);
        assert!(journal.last().is_none());
        let first = journal.append(new(Some("attention.md"), "first")).unwrap();
        assert_eq!((first.seq, first.prev.as_str()), (1, GENESIS));
        let unstamped = NewEntry {
            at: None,
            data: Value::Null,
            ..new(None, "vault created")
        };
        let second = journal.append(unstamped).unwrap();
        assert_eq!(second.prev, first.hash);
        assert!(
            second.at.ends_with('Z') && second.at.len() == 20,
            "{}",
            second.at
        );

        let mut reopened = Journal::open(dir.path().join("journal")).unwrap();
        assert_eq!(reopened.last(), Some(&second));
        reopened.append(new(Some("attention.md"), "third")).unwrap();
        assert_eq!(reopened.verify().unwrap(), 3);
        let history = reopened.history("attention.md").unwrap();
        assert_eq!(history.iter().map(|e| e.seq).collect::<Vec<_>>(), [1, 3]);

        let log = reopened
            .log_entries(|id| (id.as_str() == "attention.md").then(|| "Attention".into()))
            .unwrap();
        assert_eq!(log[0].page_title.as_deref(), Some("Attention"));
        assert!(log[1].page.is_none());
    }

    #[test]
    fn detects_edits_drops_and_garbage() {
        let dir = tempfile::tempdir().unwrap();
        let mut journal = Journal::open(dir.path()).unwrap();
        for s in ["a", "bb", "ccc"] {
            journal.append(new(Some("p.md"), s)).unwrap();
        }
        let path = dir.path().join("000001.jsonl");
        let original = fs::read_to_string(&path).unwrap();
        let lines: Vec<&str> = original.lines().collect();

        fs::write(&path, original.replace("\"bb\"", "\"BB\"")).unwrap();
        assert!(matches!(
            journal.verify(),
            Err(JournalError::Broken { seq: 2, .. })
        ));

        fs::write(&path, format!("{}\n{}\n", lines[0], lines[2])).unwrap();
        assert!(matches!(
            journal.verify(),
            Err(JournalError::Broken { seq: 3, .. })
        ));

        let relinked = lines[1].replace(&journal.entries().unwrap()[0].hash, GENESIS);
        fs::write(&path, format!("{}\n{relinked}\n", lines[0])).unwrap();
        assert!(matches!(
            journal.verify(),
            Err(JournalError::Broken { seq: 2, .. })
        ));

        fs::write(&path, format!("{}\n\nnot json\n", lines[0])).unwrap();
        assert!(matches!(
            journal.verify(),
            Err(JournalError::Parse { line: 3, .. })
        ));
        assert!(Journal::open(dir.path()).is_err());
    }

    #[test]
    fn starts_a_new_segment_when_one_is_full() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("notes.txt"), "ignored").unwrap();
        let mut journal = Journal::open(dir.path()).unwrap();
        let mut last = journal.append(new(None, "a")).unwrap();
        last.seq = SEGMENT_LEN;
        last.hash = last.compute_hash();
        journal.last = Some(last);
        let next = journal.append(new(None, "b")).unwrap();
        assert_eq!(next.seq, SEGMENT_LEN + 1);
        assert!(dir.path().join("000002.jsonl").is_file());
        assert_eq!(journal.entries().unwrap().len(), 2);
    }
}
