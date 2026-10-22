use std::path::Path;

use serde::Deserialize;

use crate::error::{Result, StoreError};
use crate::fsutil::read_to_string;

/// The parts of `edda.toml` the store reads. Missing keys take their defaults and unknown keys are ignored.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(default)]
pub struct VaultConfig {
    /// The heading of the generated `index.md`.
    pub title: String,
    /// The page `type` values the kernel accepts.
    pub types: Vec<String>,
}

impl Default for VaultConfig {
    fn default() -> Self {
        Self {
            title: "Edda vault".to_owned(),
            types: ["Concept", "Paper", "Person", "Project"]
                .map(String::from)
                .to_vec(),
        }
    }
}

#[derive(Deserialize)]
struct File {
    #[serde(default)]
    vault: VaultConfig,
}

impl VaultConfig {
    /// Reads `edda.toml`; a vault without one uses the defaults.
    pub fn load(path: &Path) -> Result<Self> {
        if !path.exists() {
            return Ok(Self::default());
        }
        let file: File =
            toml::from_str(&read_to_string(path)?).map_err(|e| StoreError::Config {
                path: path.to_owned(),
                reason: e.to_string(),
            })?;
        Ok(file.vault)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_the_vault_table_and_defaults_the_rest() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("edda.toml");
        assert_eq!(VaultConfig::load(&path).unwrap(), VaultConfig::default());
        std::fs::write(
            &path,
            "[vault]\ntitle = \"Mine\"\n[models]\ningest = \"x\"\n",
        )
        .unwrap();
        let config = VaultConfig::load(&path).unwrap();
        assert_eq!(config.title, "Mine");
        assert!(config.types.contains(&"Paper".to_owned()));
        std::fs::write(&path, "[vault]\ntypes = 3\n").unwrap();
        assert!(VaultConfig::load(&path).is_err());
    }
}
