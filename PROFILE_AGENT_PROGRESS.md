# Profiling agent progress

All times are Eastern on November 20, 2026.

- 6:01 p.m. Added exact saved-case selection with request hash verification. Enabled both instruction and data TLB modes by default and added per-mode availability plus host cache and page sizes to reports. All 12 profiling tests passed, and dry-run selected the expected 22-qubit benchmark request.
- 6:02 p.m. Recorded an actual 26-qubit Instruments smoke. Pipeline, L1D cache, data TLB, and instruction TLB captures all produced metrics. The 2-second CPU recording exported no matching samples after the startup cutoff. Checking its export and retrying CPU sampling with a longer window if necessary.
- 6:03 p.m. Found the CPU parser expected a nested stack element, while the installed Instruments emits frames directly in that element. Updated the parser to support both layouts and added a regression test. Reanalysis succeeded for all five capture types. Starting an 8-second CPU-only run to collect kernel samples past process startup.
- 6:04 p.m. The longer CPU capture measured 26,928 weighted samples. The installed Xcode's LLVM demangler decoded Rust symbols in the trace. Reanalysis ranked fused execution at 67.9% and specialized gate kernels at 31.9% of sampled CPU weight for this short workload. All 13 focused profiling tests and the whitespace check passed. The profiling changes and both local smoke reports are ready for integration.
