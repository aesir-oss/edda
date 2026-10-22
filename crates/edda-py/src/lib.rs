//! The `edda._kernel` Python module: Edda's system calls, built by maturin.

use pyo3::prelude::*;

#[pymodule]
mod _kernel {
    use pyo3::prelude::*;

    /// The Open Knowledge Format version the kernel reads and writes.
    #[pyfunction]
    fn okf_version() -> &'static str {
        edda_store::OKF_VERSION
    }
}
