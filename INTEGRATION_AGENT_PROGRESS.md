# Integration progress

Times are Eastern on November 20, 2026.

## 18:02

- The benchmark and profiling agents worked in parallel. The benchmark agent implemented the five-family FP32/FP64 suite. The profiling agent implemented saved-case selection and separate availability reporting for the CPU pipeline, L1 data cache, data TLB, and instruction TLB counters.
- The six-qubit suite passed all ten real Rust/Aer comparisons. The 26-qubit plan reported insufficient live memory headroom. The 25-qubit plan admitted all ten cases.
- Reviewed the changes, updated both guides, and verified the profiler can load a previously saved 25-qubit request by case ID. The Instruments capture is in progress.

## 18:04

- The live Instruments smoke captured L1 data-cache, data-TLB, instruction-TLB, and pipeline metrics. The CPU trace exposed a backtrace format the parser had skipped. The profiling agent fixed that format and started a longer CPU-only capture to verify kernel attribution.
- Both agents have saved their work in separate progress files. The benchmark agent completed its compatibility review. Integrated tests will run after the CPU capture.
- The longer CPU capture collected 26,928 samples and identified fused two-qubit execution as the largest CPU hotspot in that run. All 37 Rust tests and 33 Python tests passed.

## 18:05

- Verified that `src/fusion.rs:56` accounts for 39.84% of exclusive sampled CPU time in the smoke profile. The counter report lists all four requested modes as available. These are short profiling windows on one circuit.
