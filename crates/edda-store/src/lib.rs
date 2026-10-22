//! Reads and writes an Edda vault. This crate is the only code that knows the page format.

/// The Open Knowledge Format version this store reads and writes.
pub const OKF_VERSION: &str = "0.2";

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn okf_version_is_pinned() {
        assert_eq!(OKF_VERSION, "0.2");
    }
}
