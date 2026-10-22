use std::fs;
use std::io::Write;
use std::path::Path;

use crate::error::{Result, StoreError};

/// Writes a file by writing a temporary file beside it and renaming it into place, so readers never see half of one.
pub(crate) fn write_atomic(path: &Path, contents: &[u8]) -> Result<()> {
    let dir = path.parent().unwrap_or_else(|| Path::new("."));
    fs::create_dir_all(dir).map_err(|e| StoreError::io(dir, e))?;
    let name = path
        .file_name()
        .map_or_else(Default::default, |n| n.to_string_lossy().into_owned());
    let tmp = dir.join(format!(".{name}.tmp"));
    let mut file = fs::File::create(&tmp).map_err(|e| StoreError::io(&tmp, e))?;
    file.write_all(contents)
        .and_then(|()| file.sync_all())
        .map_err(|e| StoreError::io(&tmp, e))?;
    fs::rename(&tmp, path).map_err(|e| StoreError::io(path, e))
}

pub(crate) fn read_to_string(path: &Path) -> Result<String> {
    fs::read_to_string(path).map_err(|e| StoreError::io(path, e))
}
