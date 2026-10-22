use indexmap::IndexMap;
use pulldown_cmark::{CowStr, Event, Options, Parser, Tag, TagEnd, html};

use crate::page_id::PageId;

fn options() -> Options {
    Options::ENABLE_FOOTNOTES
        | Options::ENABLE_TABLES
        | Options::ENABLE_STRIKETHROUGH
        | Options::ENABLE_TASKLISTS
}

/// A Markdown link in a page body.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Link {
    /// The destination exactly as written.
    pub dest: String,
    /// The page it points to, when it is a link to a `.md` file inside the bundle.
    pub target: Option<PageId>,
    /// True when written from the bundle root (`/transformer.md`), the only form the kernel writes.
    pub root_relative: bool,
}

/// What the kernel reads out of a page body: links, footnote citations and the text that search indexes.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Analysis {
    pub links: Vec<Link>,
    /// Footnote labels in the order the body cites them, each once.
    pub citations: Vec<String>,
    /// Footnote definitions: label to the text of the note (the location in the source).
    pub footnotes: IndexMap<String, String>,
    /// The body as plain text.
    pub text: String,
}

/// Reads links, citations and text out of a page body.
pub fn analyze(body: &str, page: &PageId) -> Analysis {
    let mut out = Analysis::default();
    let mut in_footnote: Option<(String, String)> = None;
    for event in Parser::new_ext(body, options()) {
        match event {
            Event::Start(Tag::Link { dest_url, .. }) => {
                out.links.push(resolve_link(&dest_url, page))
            }
            Event::FootnoteReference(label) => {
                if !out.citations.iter().any(|c| c == label.as_ref()) {
                    out.citations.push(label.to_string());
                }
            }
            Event::Start(Tag::FootnoteDefinition(label)) => {
                in_footnote = Some((label.to_string(), String::new()))
            }
            Event::End(TagEnd::FootnoteDefinition) => {
                if let Some((label, text)) = in_footnote.take() {
                    out.footnotes.insert(label, text.trim().to_owned());
                }
            }
            Event::Text(t) | Event::Code(t) => {
                if let Some((_, note)) = in_footnote.as_mut() {
                    note.push_str(&t);
                }
                out.text.push_str(&t);
            }
            Event::SoftBreak
            | Event::HardBreak
            | Event::End(TagEnd::Paragraph | TagEnd::Heading(_) | TagEnd::Item) => {
                if let Some((_, note)) = in_footnote.as_mut() {
                    note.push(' ');
                }
                out.text.push('\n');
            }
            _ => {}
        }
    }
    out
}

/// Renders a page body to HTML that is safe to show as is.
///
/// Raw HTML in the Markdown is escaped rather than passed through, links with a scheme other than http, https or
/// mailto are dropped, and links to pages are rewritten to `link_prefix` followed by the page id, so the shell can
/// route them.
pub fn render_html(body: &str, page: &PageId, link_prefix: &str) -> String {
    let events = Parser::new_ext(body, options()).map(|event| match event {
        Event::Html(raw) | Event::InlineHtml(raw) => Event::Text(raw),
        Event::Start(Tag::Link {
            link_type,
            dest_url,
            title,
            id,
        }) => {
            let dest_url = rewrite_dest(&dest_url, page, link_prefix);
            Event::Start(Tag::Link {
                link_type,
                dest_url,
                title,
                id,
            })
        }
        Event::Start(Tag::Image {
            link_type,
            dest_url,
            title,
            id,
        }) => {
            let dest_url = if is_safe_scheme(&dest_url) {
                dest_url
            } else {
                CowStr::from("")
            };
            Event::Start(Tag::Image {
                link_type,
                dest_url,
                title,
                id,
            })
        }
        other => other,
    });
    let mut out = String::with_capacity(body.len() * 3 / 2);
    html::push_html(&mut out, events);
    out
}

fn rewrite_dest<'a>(dest: &CowStr<'a>, page: &PageId, link_prefix: &str) -> CowStr<'a> {
    if !is_safe_scheme(dest) {
        return CowStr::from("#");
    }
    match resolve_link(dest, page).target {
        Some(target) => {
            let fragment = dest.find('#').map_or("", |i| &dest[i..]);
            CowStr::from(format!("{link_prefix}{target}{fragment}"))
        }
        None => dest.clone(),
    }
}

fn scheme(dest: &str) -> Option<&str> {
    let colon = dest.find(':')?;
    let candidate = &dest[..colon];
    let valid = !candidate.is_empty()
        && candidate.starts_with(|c: char| c.is_ascii_alphabetic())
        && candidate
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '+' | '-' | '.'));
    valid.then_some(candidate)
}

fn is_safe_scheme(dest: &str) -> bool {
    scheme(dest)
        .is_none_or(|s| matches!(s.to_ascii_lowercase().as_str(), "http" | "https" | "mailto"))
}

fn resolve_link(dest: &str, page: &PageId) -> Link {
    let path = dest.split(['#', '?']).next().unwrap_or_default();
    let root_relative = path.starts_with('/');
    let target = if scheme(dest).is_some() || !path.ends_with(".md") {
        None
    } else if root_relative {
        PageId::new(path).ok()
    } else {
        join(page.dir(), path).and_then(|p| PageId::new(&p).ok())
    };
    Link {
        dest: dest.to_owned(),
        target,
        root_relative,
    }
}

/// Joins a relative link to the directory of the page it is in, refusing to climb above the bundle root.
fn join(dir: &str, rel: &str) -> Option<String> {
    let mut parts: Vec<&str> = dir.split('/').filter(|p| !p.is_empty()).collect();
    for part in rel.split('/') {
        match part {
            "" | "." => {}
            ".." => {
                parts.pop()?;
            }
            _ => parts.push(part),
        }
    }
    Some(parts.join("/"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn id(s: &str) -> PageId {
        PageId::new(s).unwrap()
    }

    const BODY: &str = "Scaled attention divides by a root.[^p3] Again.[^p3] And[^n]\n\n\
        See [Transformer](/transformer.md), [Ada](../people/ada.md#early), [up](../../x.md), \
        [web](https://arxiv.org/abs/1706.03762), [pic](diagram.png).\n\n\
        [^p3]: Section 3.2.1,\n  page 4.\n\n[^n]: `notes` p1.\n";

    #[test]
    fn reads_links_citations_and_text() {
        let a = analyze(BODY, &id("ml/attention.md"));
        let targets: Vec<_> = a
            .links
            .iter()
            .map(|l| l.target.as_ref().map(PageId::as_str))
            .collect();
        assert_eq!(
            targets,
            [
                Some("transformer.md"),
                Some("people/ada.md"),
                None,
                None,
                None
            ]
        );
        assert!(a.links[0].root_relative);
        assert!(!a.links[1].root_relative);
        assert_eq!(a.citations, ["p3", "n"]);
        assert_eq!(a.footnotes["p3"], "Section 3.2.1, page 4.");
        assert_eq!(a.footnotes["n"], "notes p1.");
        assert!(a.text.contains("Scaled attention divides by a root."));
    }

    #[test]
    fn renders_safe_html_with_routed_page_links() {
        let body = "Hi <script>alert(1)</script> [t](/transformer.md#x) [j](javascript:alert(1)) \
            [w](https://a.b) ![i](data:image/png;base64,AA) ![ok](/img.png)[^a]\n\n<div>raw</div>\n\n[^a]: note\n";
        let html = render_html(body, &id("a.md"), "/page/");
        assert!(html.contains("&lt;script&gt;"));
        assert!(!html.contains("<script>"));
        assert!(html.contains("&lt;div&gt;raw&lt;/div&gt;"));
        assert!(html.contains(r#"href="/page/transformer.md#x""#));
        assert!(html.contains(r##"href="#""##));
        assert!(html.contains(r#"href="https://a.b""#));
        assert!(html.contains(r#"src="""#));
        assert!(html.contains(r#"src="/img.png""#));
        assert!(html.contains("footnote-reference"));
    }

    #[test]
    fn recognises_schemes() {
        assert_eq!(scheme("https://x"), Some("https"));
        assert_eq!(scheme("/a:b"), None);
        assert_eq!(scheme("1a:b"), None);
        assert_eq!(scheme(":x"), None);
        assert!(is_safe_scheme("MAILTO:a@b"));
        assert!(!is_safe_scheme("vbscript:x"));
    }
}
