use std::collections::HashSet;

use crate::error::{Result, StoreError};
use crate::frontmatter::{Frontmatter, Resource};
use crate::markdown::{self, Analysis};
use crate::page_id::PageId;

/// A wiki page: frontmatter and Markdown body.
#[derive(Debug, Clone, PartialEq)]
pub struct Page {
    pub id: PageId,
    pub frontmatter: Frontmatter,
    pub body: String,
}

/// What the kernel's checks need to know about the rest of the vault.
pub trait Context {
    fn allowed_type(&self, kind: &str) -> bool;
    fn page_exists(&self, id: &PageId) -> bool;
    fn source_resolves(&self, resource: &Resource) -> bool;
}

/// One way a page falls short of the kernel's rules. Every rule still allows valid OKF.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Issue {
    MissingType,
    UnknownType(String),
    MissingTitle,
    MissingDescription,
    SourceWithoutId(String),
    DuplicateSourceId(String),
    UncitedSource(String),
    UnknownCitation(String),
    UnresolvedSource { id: String, resource: String },
    RelativeLink(String),
    BrokenLink(String),
}

impl Page {
    /// Parses a page file: `---`, YAML frontmatter, `---`, then the body.
    pub fn parse(id: PageId, text: &str) -> Result<Self> {
        let error = |reason: String| StoreError::Frontmatter {
            page: id.to_string(),
            reason,
        };
        let text = text.strip_prefix('\u{feff}').unwrap_or(text);
        let rest = text
            .strip_prefix("---\n")
            .or_else(|| text.strip_prefix("---\r\n"))
            .ok_or_else(|| error("does not start with a --- frontmatter block".into()))?;
        let (yaml, body) = split_frontmatter(rest)
            .ok_or_else(|| error("frontmatter block is not closed".into()))?;
        let frontmatter = if yaml.trim().is_empty() {
            Frontmatter::default()
        } else {
            serde_yaml_ng::from_str(yaml).map_err(|e| error(e.to_string()))?
        };
        Ok(Self {
            id,
            frontmatter,
            body: body.to_owned(),
        })
    }

    /// The page file as written to disk.
    pub fn to_markdown(&self) -> Result<String> {
        let yaml =
            serde_yaml_ng::to_string(&self.frontmatter).map_err(|e| StoreError::Frontmatter {
                page: self.id.to_string(),
                reason: e.to_string(),
            })?;
        Ok(format!("---\n{yaml}---\n{}", self.body))
    }

    /// The title, or the file name when the page has none.
    pub fn title(&self) -> &str {
        self.frontmatter
            .title
            .as_deref()
            .unwrap_or_else(|| self.id.as_str())
    }

    pub fn analyze(&self) -> Analysis {
        markdown::analyze(&self.body, &self.id)
    }

    pub fn render_html(&self, link_prefix: &str) -> String {
        markdown::render_html(&self.body, &self.id, link_prefix)
    }

    /// Checks the page against the kernel's rules, which are stricter than OKF's.
    pub fn check(&self, ctx: &impl Context) -> Vec<Issue> {
        let fm = &self.frontmatter;
        let mut issues = Vec::new();
        match fm.kind.as_deref() {
            None => issues.push(Issue::MissingType),
            Some(kind) if !ctx.allowed_type(kind) => {
                issues.push(Issue::UnknownType(kind.to_owned()))
            }
            Some(_) => {}
        }
        if fm.title.as_deref().is_none_or(str::is_empty) {
            issues.push(Issue::MissingTitle);
        }
        if fm.description.as_deref().is_none_or(str::is_empty) {
            issues.push(Issue::MissingDescription);
        }

        let analysis = self.analyze();
        let mut seen = HashSet::new();
        for source in &fm.sources {
            let Some(id) = source.id.as_deref() else {
                issues.push(Issue::SourceWithoutId(source.resource.to_string()));
                continue;
            };
            if !seen.insert(id) {
                issues.push(Issue::DuplicateSourceId(id.to_owned()));
            }
            if !analysis.citations.iter().any(|c| c == id) {
                issues.push(Issue::UncitedSource(id.to_owned()));
            }
            if !ctx.source_resolves(&source.resource) {
                issues.push(Issue::UnresolvedSource {
                    id: id.to_owned(),
                    resource: source.resource.to_string(),
                });
            }
        }
        for label in &analysis.citations {
            if fm.source(label).is_none() {
                issues.push(Issue::UnknownCitation(label.clone()));
            }
        }
        for link in &analysis.links {
            let Some(target) = &link.target else { continue };
            if !link.root_relative {
                issues.push(Issue::RelativeLink(link.dest.clone()));
            }
            if !ctx.page_exists(target) {
                issues.push(Issue::BrokenLink(link.dest.clone()));
            }
        }
        issues
    }
}

/// Splits after the opening `---` into the YAML and the body, at the first line that is `---` or `...`.
fn split_frontmatter(rest: &str) -> Option<(&str, &str)> {
    let mut offset = 0;
    for line in rest.split_inclusive('\n') {
        let bare = line.trim_end_matches(['\n', '\r']);
        if bare == "---" || bare == "..." {
            return Some((&rest[..offset], &rest[offset + line.len()..]));
        }
        offset += line.len();
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Ctx;

    impl Context for Ctx {
        fn allowed_type(&self, kind: &str) -> bool {
            kind == "Concept"
        }
        fn page_exists(&self, id: &PageId) -> bool {
            id.as_str() == "transformer.md"
        }
        fn source_resolves(&self, resource: &Resource) -> bool {
            match resource {
                Resource::Raw(h) => h == "2d77a0",
                Resource::Url(u) => u.contains("arxiv.org"),
            }
        }
    }

    const PAGE: &str = "---
type: Concept
title: Attention mechanism
description: How transformers weight parts of their input.
tags: [ml, transformers]
sources:
  - id: vaswani2017-p3
    resource: https://arxiv.org/abs/1706.03762
    title: Attention Is All You Need
    locator: page 3
  - id: notes-0412
    resource: raw:2d77a0
generated: {by: \"claude-sonnet-5-5 / ingest:7f3a\", at: 2026-10-21T14:02:00Z}
verified:
  - {by: owner, at: 2026-10-21T14:10:00Z}
status: stable
stale_after: 2027-10-21
edda_rev: 13
okf_custom: {nested: [1, 2]}
---
Scaled dot-product attention.[^vaswani2017-p3][^notes-0412]

See also [Transformer](/transformer.md).

[^vaswani2017-p3]: Section 3.2.1, page 4.
[^notes-0412]: My notes.
";

    fn page(text: &str) -> Page {
        Page::parse(PageId::new("attention.md").unwrap(), text).unwrap()
    }

    #[test]
    fn parses_every_frontmatter_key() {
        let p = page(PAGE);
        let fm = &p.frontmatter;
        assert_eq!(fm.kind.as_deref(), Some("Concept"));
        assert_eq!(p.title(), "Attention mechanism");
        assert_eq!(fm.tags, ["ml", "transformers"]);
        assert_eq!(fm.sources[1].resource, Resource::Raw("2d77a0".into()));
        assert_eq!(
            fm.sources[0].extra["locator"],
            serde_yaml_ng::Value::from("page 3")
        );
        assert_eq!(
            fm.generated.as_ref().unwrap().by,
            "claude-sonnet-5-5 / ingest:7f3a"
        );
        assert_eq!(fm.verified[0].at.as_deref(), Some("2026-10-21T14:10:00Z"));
        assert_eq!(fm.stale_after.as_deref(), Some("2027-10-21"));
        assert_eq!(fm.edda_rev, Some(13));
        assert!(fm.extra.contains_key("okf_custom"));
        assert!(p.body.starts_with("Scaled"));
        assert!(p.check(&Ctx).is_empty(), "{:?}", p.check(&Ctx));
    }

    #[test]
    fn round_trips_unknown_keys_and_body() {
        let p = page(PAGE);
        let again = page(&p.to_markdown().unwrap());
        assert_eq!(again, p);
        let written = p.to_markdown().unwrap();
        assert!(written.find("edda_rev").unwrap() < written.find("okf_custom").unwrap());
    }

    #[test]
    fn reports_every_rule_it_breaks() {
        let text = "---
type: Essay
sources:
  - resource: https://x.y
  - id: a
    resource: https://unresolved.example
  - id: a
    resource: raw:2d77a0
---
Claim.[^b] [rel](transformer.md) [gone](/missing.md) [web](https://w.x)

[^b]: nowhere
";
        let issues = page(text).check(&Ctx);
        for expected in [
            Issue::UnknownType("Essay".into()),
            Issue::MissingTitle,
            Issue::MissingDescription,
            Issue::SourceWithoutId("https://x.y".into()),
            Issue::DuplicateSourceId("a".into()),
            Issue::UncitedSource("a".into()),
            Issue::UnresolvedSource {
                id: "a".into(),
                resource: "https://unresolved.example".into(),
            },
            Issue::UnknownCitation("b".into()),
            Issue::RelativeLink("transformer.md".into()),
            Issue::BrokenLink("/missing.md".into()),
        ] {
            assert!(
                issues.contains(&expected),
                "missing {expected:?} in {issues:?}"
            );
        }
        assert!(
            page("---\ntitle: t\n---\n")
                .check(&Ctx)
                .contains(&Issue::MissingType)
        );
    }

    #[test]
    fn refuses_files_without_frontmatter() {
        let id = PageId::new("a.md").unwrap();
        assert!(Page::parse(id.clone(), "# no frontmatter").is_err());
        assert!(Page::parse(id.clone(), "---\ntitle: open\n").is_err());
        assert!(Page::parse(id.clone(), "---\n: [bad\n---\n").is_err());
        let crlf = Page::parse(id.clone(), "\u{feff}---\r\ntitle: t\r\n...\r\nbody").unwrap();
        assert_eq!(crlf.body, "body");
        let empty = Page::parse(id, "---\n---\nbody").unwrap();
        assert_eq!(empty.title(), "a.md");
        assert_eq!(empty.render_html("/p/"), "<p>body</p>\n");
    }
}
