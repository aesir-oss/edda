use crate::OKF_VERSION;
use crate::page::Page;
use crate::page_id::PageId;

/// One change, as `log.md` lists it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LogEntry {
    /// RFC 3339, UTC: `2026-10-21T14:10:00Z`.
    pub at: String,
    pub page: Option<PageId>,
    pub page_title: Option<String>,
    pub summary: String,
}

/// The bundle root `index.md`: `okf_version`, a heading, and every page with its description, by title.
pub fn render_index(title: &str, pages: &[Page]) -> String {
    let mut pages: Vec<&Page> = pages.iter().filter(|p| !p.id.is_generated()).collect();
    pages.sort_by_cached_key(|p| (p.title().to_lowercase(), p.id.clone()));
    let mut out = format!("---\nokf_version: \"{OKF_VERSION}\"\n---\n# {title}\n\n");
    for page in pages {
        out.push_str(&format!(
            "- [{}]({})",
            escape_link_text(page.title()),
            page.id.link()
        ));
        if let Some(description) = page
            .frontmatter
            .description
            .as_deref()
            .filter(|d| !d.is_empty())
        {
            out.push_str(&format!(": {description}"));
        }
        out.push('\n');
    }
    out
}

/// `log.md`: changes under dated headings, newest first.
pub fn render_log(entries: &[LogEntry]) -> String {
    let mut entries: Vec<&LogEntry> = entries.iter().collect();
    entries.sort_by(|a, b| b.at.cmp(&a.at));
    let mut out = String::from(
        "---\ntype: Log\ntitle: Log\ndescription: Changes to this vault, newest first. Generated from the journal.\n\
         ---\n# Log\n",
    );
    let mut day = "";
    for entry in entries {
        let (date, time) = entry.at.split_once('T').unwrap_or((&entry.at, ""));
        if date != day {
            out.push_str(&format!("\n## {date}\n\n"));
            day = date;
        }
        out.push_str("- ");
        if time.len() >= 5 {
            out.push_str(&format!("{} ", &time[..5]));
        }
        if let Some(page) = &entry.page {
            let title = entry.page_title.as_deref().unwrap_or_else(|| page.as_str());
            out.push_str(&format!("[{}]({}): ", escape_link_text(title), page.link()));
        }
        out.push_str(&entry.summary);
        out.push('\n');
    }
    out
}

fn escape_link_text(text: &str) -> String {
    text.replace('\\', "\\\\")
        .replace('[', "\\[")
        .replace(']', "\\]")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn page(id: &str, text: &str) -> Page {
        Page::parse(PageId::new(id).unwrap(), text).unwrap()
    }

    #[test]
    fn index_lists_pages_by_title_with_descriptions() {
        let pages = [
            page(
                "t.md",
                "---\ntitle: Transformer\ndescription: A model.\n---\n",
            ),
            page("a.md", "---\ntitle: attention [core]\n---\n"),
            page("index.md", "---\nokf_version: \"0.2\"\n---\n"),
        ];
        assert_eq!(
            render_index("Vault", &pages),
            "---\nokf_version: \"0.2\"\n---\n# Vault\n\n\
             - [attention \\[core\\]](/a.md)\n- [Transformer](/t.md): A model.\n"
        );
    }

    #[test]
    fn log_groups_by_day_newest_first() {
        let entry = |at: &str, page: Option<&str>, summary: &str| LogEntry {
            at: at.into(),
            page: page.map(|p| PageId::new(p).unwrap()),
            page_title: page.map(|_| "Attention".into()),
            summary: summary.into(),
        };
        let log = render_log(&[
            entry("2026-10-20T09:00:00Z", None, "Vault created"),
            entry("2026-10-21T14:10:00Z", Some("a.md"), "verified"),
            entry("2026-10-21T14:02:00Z", Some("a.md"), "edited"),
            entry("someday", None, "undated"),
        ]);
        let body = log.split_once("# Log\n").unwrap().1;
        assert_eq!(
            body,
            "\n## someday\n\n- undated\n\n## 2026-10-21\n\n- 14:10 [Attention](/a.md): verified\n\
             - 14:02 [Attention](/a.md): edited\n\n## 2026-10-20\n\n- 09:00 Vault created\n"
        );
        assert!(Page::parse(PageId::new("log.md").unwrap(), &log).is_ok());
    }
}
