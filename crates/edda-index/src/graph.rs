use std::collections::{BTreeMap, BTreeSet};

use edda_store::{Page, PageId, RawEntry};
use serde::{Deserialize, Serialize};

/// A link out of a page, as written, with the page it resolves to.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OutLink {
    pub dest: String,
    /// The page id, for links to a `.md` file inside the bundle; `None` for external links.
    pub target: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Node {
    pub title: String,
    pub links: Vec<OutLink>,
}

/// A link to a page that does not exist.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct BrokenLink {
    pub from: String,
    pub dest: String,
}

/// The link graph and the source URL table, built from the files and saved as `graph.json`.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Graph {
    pub pages: BTreeMap<String, Node>,
    /// Source URL to the SHA-256 of the raw file fetched from it.
    pub sources: BTreeMap<String, String>,
    #[serde(skip)]
    backlinks: BTreeMap<String, BTreeSet<String>>,
}

impl Graph {
    pub fn build(pages: &[Page], raw: &[RawEntry]) -> Self {
        let pages = pages
            .iter()
            .map(|page| {
                let links = page
                    .analyze()
                    .links
                    .into_iter()
                    .map(|l| OutLink {
                        dest: l.dest,
                        target: l.target.map(|t| t.to_string()),
                    })
                    .collect();
                (
                    page.id.to_string(),
                    Node {
                        title: page.title().to_owned(),
                        links,
                    },
                )
            })
            .collect();
        let sources = raw
            .iter()
            .filter_map(|e| Some((e.meta.url.clone()?, e.hash.clone())))
            .collect();
        Self {
            pages,
            sources,
            backlinks: BTreeMap::new(),
        }
        .with_backlinks()
    }

    pub(crate) fn with_backlinks(mut self) -> Self {
        let mut backlinks: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
        for (from, node) in &self.pages {
            for target in node.links.iter().filter_map(|l| l.target.as_ref()) {
                if target != from {
                    backlinks
                        .entry(target.clone())
                        .or_default()
                        .insert(from.clone());
                }
            }
        }
        self.backlinks = backlinks;
        self
    }

    pub fn contains(&self, id: &PageId) -> bool {
        self.pages.contains_key(id.as_str())
    }

    pub fn outgoing(&self, id: &PageId) -> &[OutLink] {
        self.pages.get(id.as_str()).map_or(&[], |n| &n.links)
    }

    /// The pages that link to this one, sorted.
    pub fn backlinks(&self, id: &PageId) -> Vec<String> {
        self.backlinks
            .get(id.as_str())
            .map(|s| s.iter().cloned().collect())
            .unwrap_or_default()
    }

    /// Pages no other page links to.
    pub fn orphans(&self) -> Vec<String> {
        self.pages
            .keys()
            .filter(|id| !self.backlinks.contains_key(*id))
            .cloned()
            .collect()
    }

    /// Links to pages that do not exist, in page order.
    pub fn broken_links(&self) -> Vec<BrokenLink> {
        self.pages
            .iter()
            .flat_map(|(from, node)| {
                node.links
                    .iter()
                    .filter(|l| {
                        l.target
                            .as_ref()
                            .is_some_and(|t| !self.pages.contains_key(t))
                    })
                    .map(move |l| BrokenLink {
                        from: from.clone(),
                        dest: l.dest.clone(),
                    })
            })
            .collect()
    }

    /// The raw source fetched from a URL.
    pub fn source_for_url(&self, url: &str) -> Option<&str> {
        self.sources.get(url).map(String::as_str)
    }
}
