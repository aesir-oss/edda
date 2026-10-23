//! Page markers in sources converted from paged documents: a line `<!-- page 5 -->` before each page's text, so a
//! citation can name a page and a reader can read one.

use std::ops::RangeInclusive;

/// The marker line that opens page `page`.
pub fn marker(page: u64) -> String {
    format!("<!-- page {page} -->")
}

fn page_of(line: &str) -> Option<u64> {
    line.trim()
        .strip_prefix("<!-- page ")?
        .strip_suffix(" -->")?
        .parse()
        .ok()
}

/// The pages a text marks, in order; empty for a text without markers.
pub fn pages(text: &str) -> Vec<u64> {
    text.lines().filter_map(page_of).collect()
}

/// The lines (1-based, inclusive) of page `page`, from its marker to the line before the next one.
pub fn page_lines(text: &str, page: u64) -> Option<RangeInclusive<usize>> {
    let mut start = None;
    let mut last = 0;
    for (index, line) in text.lines().enumerate() {
        let number = index + 1;
        if let Some(found) = page_of(line) {
            if start.is_some() {
                return start.map(|s| s..=number - 1);
            }
            if found == page {
                start = Some(number);
            }
        }
        last = number;
    }
    start.map(|s| s..=last)
}

/// The pages a locator names: "page 5", "pages 3-4", "pp. 3 to 5", "p. 7" (case and dash style ignored).
pub fn cited_pages(locator: &str) -> Vec<u64> {
    let text = locator.to_lowercase().replace(['–', '—'], "-");
    let words: Vec<&str> = text
        .split(|c: char| c.is_whitespace() || c == ',' || c == ';' || c == '(' || c == ')')
        .filter(|w| !w.is_empty())
        .collect();
    let mut cited = Vec::new();
    let mut i = 0;
    while i < words.len() {
        if matches!(words[i], "page" | "pages" | "p." | "pp." | "p" | "pp") {
            let Some(next) = words.get(i + 1) else { break };
            if let Some((a, b)) = next.split_once('-') {
                if let (Ok(a), Ok(b)) = (a.parse::<u64>(), b.parse::<u64>()) {
                    cited.extend(a.min(b)..=a.max(b));
                }
            } else if let Ok(a) = next.parse::<u64>() {
                let to = words.get(i + 2).filter(|w| **w == "to" || **w == "-");
                match to
                    .and_then(|_| words.get(i + 3))
                    .and_then(|w| w.parse::<u64>().ok())
                {
                    Some(b) => {
                        cited.extend(a.min(b)..=a.max(b));
                        i += 2;
                    }
                    None => cited.push(a),
                }
            }
            i += 1;
        }
        i += 1;
    }
    cited
}

#[cfg(test)]
mod tests {
    use super::*;

    const TEXT: &str =
        "<!-- page 1 -->\nOne.\n\n<!-- page 2 -->\nTwo.\nMore two.\n<!-- page 3 -->\nThree.";

    #[test]
    fn finds_pages_and_their_lines() {
        assert_eq!(pages(TEXT), [1, 2, 3]);
        assert_eq!(page_lines(TEXT, 1), Some(1..=3));
        assert_eq!(page_lines(TEXT, 2), Some(4..=6));
        assert_eq!(page_lines(TEXT, 3), Some(7..=8));
        assert_eq!(page_lines(TEXT, 4), None);
        assert!(pages("no markers").is_empty());
        assert_eq!(marker(12), "<!-- page 12 -->");
    }

    #[test]
    fn reads_the_pages_a_locator_names() {
        assert_eq!(cited_pages("page 5"), [5]);
        assert_eq!(cited_pages("Pages 3-4, table 2"), [3, 4]);
        assert_eq!(cited_pages("pp. 3 to 5"), [3, 4, 5]);
        assert_eq!(cited_pages("p. 7 (figure 1)"), [7]);
        assert_eq!(cited_pages("pages 6–5"), [5, 6]);
        assert_eq!(cited_pages("lines 3-5"), Vec::<u64>::new());
        assert_eq!(cited_pages("section 2"), Vec::<u64>::new());
        assert_eq!(cited_pages("page"), Vec::<u64>::new());
        assert_eq!(cited_pages("page one"), Vec::<u64>::new());
    }
}
