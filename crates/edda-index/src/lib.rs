//! Edda's index: BM25 search over pages with tantivy, the link graph with backlinks, orphans and broken links, and
//! URL-to-hash lookup for sources. Everything lives in `.edda/index/` and is rebuilt from the vault's files alone.

mod graph;

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use edda_store::{PageId, StoreError, Vault, sha256_hex};
use tantivy::collector::TopDocs;
use tantivy::query::{MoreLikeThisQuery, QueryParser};
use tantivy::schema::{
    Field, IndexRecordOption, OwnedValue, STORED, STRING, Schema, TantivyDocument,
    TextFieldIndexing, TextOptions, Value,
};
use tantivy::snippet::SnippetGenerator;
use tantivy::{IndexReader, ReloadPolicy, doc};

pub use graph::{BrokenLink, Graph, Node, OutLink};

/// Bumped whenever the schema or what is indexed changes, so old indexes are rebuilt.
const FORMAT: &str = "edda-index 1";
const WRITER_MEMORY: usize = 50_000_000;
const SNIPPET_CHARS: usize = 180;
/// A linked page counts for this fraction of the score of the page that links to it or is linked from it.
const LINK_DECAY: f32 = 0.5;

#[derive(Debug, thiserror::Error)]
pub enum IndexError {
    #[error(transparent)]
    Store(#[from] StoreError),
    #[error("search index: {0}")]
    Search(#[from] tantivy::TantivyError),
    #[error("{path}: {source}")]
    Io {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("{path}: {source}")]
    Graph {
        path: PathBuf,
        #[source]
        source: serde_json::Error,
    },
}

pub type Result<T, E = IndexError> = std::result::Result<T, E>;

fn io(path: &Path) -> impl FnOnce(std::io::Error) -> IndexError + '_ {
    move |source| IndexError::Io {
        path: path.to_owned(),
        source,
    }
}

/// A search result.
#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct Hit {
    pub id: String,
    pub title: String,
    pub description: String,
    pub score: f32,
    /// A passage of the body with the matched terms in `<b>`; the text is HTML-escaped.
    pub snippet: String,
}

/// A page related to a text. `via` says what gave it its score: its own BM25 match (`search`) or a link to or from a
/// page that matched (`link`).
#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct Related {
    pub id: String,
    pub title: String,
    pub score: f32,
    pub via: String,
}

/// A page's links in both directions.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct Links {
    pub outgoing: Vec<OutLink>,
    pub backlinks: Vec<String>,
}

#[derive(Clone, Copy)]
struct Fields {
    id: Field,
    title: Field,
    description: Field,
    body: Field,
    tags: Field,
}

fn schema() -> (Schema, Fields) {
    let text = TextOptions::default().set_stored().set_indexing_options(
        TextFieldIndexing::default()
            .set_tokenizer("en_stem")
            .set_index_option(IndexRecordOption::WithFreqsAndPositions),
    );
    let mut builder = Schema::builder();
    let fields = Fields {
        id: builder.add_text_field("id", STRING | STORED),
        title: builder.add_text_field("title", text.clone()),
        description: builder.add_text_field("description", text.clone()),
        body: builder.add_text_field("body", text),
        tags: builder.add_text_field("tags", STRING | STORED),
    };
    (builder.build(), fields)
}

/// An open index over one vault.
pub struct Index {
    search: tantivy::Index,
    reader: IndexReader,
    fields: Fields,
    graph: Graph,
}

impl Index {
    /// Opens the vault's index, rebuilding it first when it is missing or the files have changed since it was built.
    pub fn open(vault: &Vault) -> Result<Self> {
        let dir = vault.index_dir();
        let current = fingerprint(vault)?;
        let stored = fs::read_to_string(dir.join("fingerprint")).ok();
        if stored.as_deref() == Some(current.as_str())
            && let Ok(index) = Self::load(&dir)
        {
            return Ok(index);
        }
        Self::rebuild(vault)
    }

    /// Deletes `.edda/index/` and builds it again from the files.
    pub fn rebuild(vault: &Vault) -> Result<Self> {
        let dir = vault.index_dir();
        if dir.exists() {
            fs::remove_dir_all(&dir).map_err(io(&dir))?;
        }
        let search_dir = dir.join("search");
        fs::create_dir_all(&search_dir).map_err(io(&search_dir))?;
        let fingerprint = fingerprint(vault)?;

        let (schema, fields) = schema();
        let search = tantivy::Index::create_in_dir(&search_dir, schema)?;
        let mut writer: tantivy::IndexWriter = search.writer_with_num_threads(1, WRITER_MEMORY)?;
        let pages = vault.pages()?;
        for page in &pages {
            let fm = &page.frontmatter;
            let mut document = doc!(
                fields.id => page.id.as_str(),
                fields.title => page.title(),
                fields.description => fm.description.clone().unwrap_or_default(),
                fields.body => page.analyze().text,
            );
            for tag in &fm.tags {
                document.add_text(fields.tags, tag);
            }
            writer.add_document(document)?;
        }
        writer.commit()?;
        writer.wait_merging_threads()?;

        let graph = Graph::build(&pages, &vault.raw().list()?);
        let graph_path = dir.join("graph.json");
        let json = serde_json::to_vec_pretty(&graph).map_err(|source| IndexError::Graph {
            path: graph_path.clone(),
            source,
        })?;
        fs::write(&graph_path, json).map_err(io(&graph_path))?;
        let fingerprint_path = dir.join("fingerprint");
        fs::write(&fingerprint_path, fingerprint).map_err(io(&fingerprint_path))?;
        Self::load(&dir)
    }

    fn load(dir: &Path) -> Result<Self> {
        let search = tantivy::Index::open_in_dir(dir.join("search"))?;
        let (_, fields) = schema();
        let reader = search
            .reader_builder()
            .reload_policy(ReloadPolicy::Manual)
            .try_into()?;
        let graph_path = dir.join("graph.json");
        let bytes = fs::read(&graph_path).map_err(io(&graph_path))?;
        let graph: Graph = serde_json::from_slice(&bytes).map_err(|source| IndexError::Graph {
            path: graph_path,
            source,
        })?;
        Ok(Self {
            search,
            reader,
            fields,
            graph: graph.with_backlinks(),
        })
    }

    /// The `k` best pages for a query, best first; equal scores are ordered by page id.
    ///
    /// The query uses tantivy's syntax, read leniently: anything it cannot parse is searched as plain words.
    pub fn search(&self, query: &str, k: usize) -> Result<Vec<Hit>> {
        let f = self.fields;
        let mut parser = QueryParser::for_index(&self.search, vec![f.title, f.description, f.body]);
        parser.set_field_boost(f.title, 4.0);
        parser.set_field_boost(f.description, 1.5);
        let (parsed, _errors) = parser.parse_query_lenient(query);
        let searcher = self.reader.searcher();
        let top = searcher.search(&parsed, &TopDocs::with_limit(k.max(1)).order_by_score())?;
        let mut snippets = SnippetGenerator::create(&searcher, &*parsed, f.body)?;
        snippets.set_max_num_chars(SNIPPET_CHARS);
        let mut hits = Vec::with_capacity(top.len());
        for (score, address) in top {
            let document: TantivyDocument = searcher.doc(address)?;
            let text = |field| {
                document
                    .get_first(field)
                    .and_then(|v| v.as_str())
                    .unwrap_or_default()
                    .to_owned()
            };
            hits.push(Hit {
                id: text(f.id),
                title: text(f.title),
                description: text(f.description),
                score,
                snippet: snippets.snippet_from_doc(&document).to_html(),
            });
        }
        hits.sort_by(|a, b| b.score.total_cmp(&a.score).then_with(|| a.id.cmp(&b.id)));
        hits.truncate(k);
        Ok(hits)
    }

    /// Pages related to a text, such as a new source, best first: pages similar to it by BM25, then the pages those
    /// link to or are linked from, scored at half their neighbour's score. Equal scores are ordered by page id.
    pub fn related(&self, text: &str, k: usize) -> Result<Vec<Related>> {
        let f = self.fields;
        if text.trim().is_empty() || k == 0 {
            return Ok(Vec::new());
        }
        let query = MoreLikeThisQuery::builder()
            .with_min_doc_frequency(1)
            .with_min_term_frequency(1)
            .with_min_word_length(3)
            .with_max_query_terms(40)
            .with_document_fields(vec![(f.body, vec![OwnedValue::Str(text.to_owned())])]);
        let searcher = self.reader.searcher();
        let top = searcher.search(&query, &TopDocs::with_limit(k).order_by_score())?;
        let mut scores: BTreeMap<String, (f32, &'static str)> = BTreeMap::new();
        for (score, address) in top {
            let document: TantivyDocument = searcher.doc(address)?;
            let id = document
                .get_first(f.id)
                .and_then(|v| v.as_str())
                .unwrap_or_default()
                .to_owned();
            scores.insert(id, (score, "search"));
        }
        let direct: Vec<(String, f32)> =
            scores.iter().map(|(id, (s, _))| (id.clone(), *s)).collect();
        for (id, score) in direct {
            let page = PageId::new(&id).map_err(IndexError::Store)?;
            let neighbours = self
                .graph
                .outgoing(&page)
                .iter()
                .filter_map(|l| l.target.clone());
            for neighbour in neighbours.chain(self.graph.backlinks(&page)) {
                if !self.graph.pages.contains_key(&neighbour) {
                    continue;
                }
                let linked = score * LINK_DECAY;
                let entry = scores.entry(neighbour).or_insert((linked, "link"));
                if entry.0 < linked {
                    *entry = (linked, "link");
                }
            }
        }
        let mut related: Vec<Related> = scores
            .into_iter()
            .map(|(id, (score, via))| {
                let title = self
                    .graph
                    .pages
                    .get(&id)
                    .map(|n| n.title.clone())
                    .unwrap_or_default();
                Related {
                    id,
                    title,
                    score,
                    via: via.to_owned(),
                }
            })
            .collect();
        related.sort_by(|a, b| b.score.total_cmp(&a.score).then_with(|| a.id.cmp(&b.id)));
        related.truncate(k);
        Ok(related)
    }

    pub fn graph(&self) -> &Graph {
        &self.graph
    }

    pub fn links(&self, id: &PageId) -> Links {
        Links {
            outgoing: self.graph.outgoing(id).to_vec(),
            backlinks: self.graph.backlinks(id),
        }
    }

    pub fn orphans(&self) -> Vec<String> {
        self.graph.orphans()
    }

    pub fn broken_links(&self) -> Vec<BrokenLink> {
        self.graph.broken_links()
    }

    pub fn source_for_url(&self, url: &str) -> Option<&str> {
        self.graph.source_for_url(url)
    }
}

/// A hash of everything the index is built from, to tell when it is stale.
fn fingerprint(vault: &Vault) -> Result<String> {
    let mut input = format!("{FORMAT}\n");
    for page in vault.pages()? {
        input.push_str(&format!(
            "page {} {}\n",
            page.id,
            sha256_hex(page.to_markdown()?.as_bytes())
        ));
    }
    for entry in vault.raw().list()? {
        input.push_str(&format!(
            "raw {} {}\n",
            entry.hash,
            entry.meta.url.unwrap_or_default()
        ));
    }
    Ok(sha256_hex(input.as_bytes()))
}
