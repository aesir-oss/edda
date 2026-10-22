use std::fmt;

use indexmap::IndexMap;
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use serde_yaml_ng::Value;

/// Keys the kernel does not know, kept in the order they were read and written back unchanged.
pub type Extra = IndexMap<String, Value>;

/// A page's YAML frontmatter: the OKF v0.2 keys Edda uses, the `edda_rev` extension, and everything else.
///
/// Parsing is as lenient as OKF itself; [`crate::Page::check`] applies the kernel's stricter rules.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Frontmatter {
    #[serde(rename = "type", default, skip_serializing_if = "Option::is_none")]
    pub kind: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub tags: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub sources: Vec<Source>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub generated: Option<Generated>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub verified: Vec<Verification>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub status: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub stale_after: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub edda_rev: Option<u64>,
    #[serde(flatten)]
    pub extra: Extra,
}

impl Frontmatter {
    /// The page status; OKF reads an absent one as `stable`.
    pub fn status(&self) -> &str {
        self.status.as_deref().unwrap_or("stable")
    }

    /// The source a footnote label cites.
    pub fn source(&self, id: &str) -> Option<&Source> {
        self.sources.iter().find(|s| s.id.as_deref() == Some(id))
    }
}

/// One entry of `sources`. The kernel requires `id`, which footnote labels refer to.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Source {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub id: Option<String>,
    pub resource: Resource,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
    #[serde(flatten)]
    pub extra: Extra,
}

/// Where a source lives: its original URL, or `raw:<hash>` for a file in `raw/` that has none.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Resource {
    Url(String),
    /// A SHA-256 in hex, possibly abbreviated to a unique prefix.
    Raw(String),
}

impl Resource {
    pub fn parse(s: &str) -> Self {
        match s.strip_prefix("raw:") {
            Some(hash) => Self::Raw(hash.to_owned()),
            None => Self::Url(s.to_owned()),
        }
    }
}

impl fmt::Display for Resource {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Url(url) => f.write_str(url),
            Self::Raw(hash) => write!(f, "raw:{hash}"),
        }
    }
}

impl Serialize for Resource {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.collect_str(self)
    }
}

impl<'de> Deserialize<'de> for Resource {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        Ok(Self::parse(&String::deserialize(deserializer)?))
    }
}

/// Who or what wrote a page or change: a model and job id for pages a job writes.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Generated {
    pub by: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub at: Option<String>,
    #[serde(flatten)]
    pub extra: Extra,
}

/// A person's review of the page, added when they accept a proposal.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Verification {
    pub by: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub at: Option<String>,
    #[serde(flatten)]
    pub extra: Extra,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resources_round_trip_through_their_string_form() {
        assert_eq!(
            Resource::parse("raw:2d77a0"),
            Resource::Raw("2d77a0".into())
        );
        assert_eq!(
            Resource::parse("https://a.b/c").to_string(),
            "https://a.b/c"
        );
        assert_eq!(Resource::Raw("ab".into()).to_string(), "raw:ab");
    }

    #[test]
    fn absent_status_reads_as_stable() {
        let mut fm = Frontmatter::default();
        assert_eq!(fm.status(), "stable");
        fm.status = Some("draft".into());
        assert_eq!(fm.status(), "draft");
    }
}
