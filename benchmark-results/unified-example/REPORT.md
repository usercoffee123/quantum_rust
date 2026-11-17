# Runtime comparison

Primary times are median fresh-state request latencies in milliseconds. Ratios above one favor Rust; below one favor the other backend. Only passing cases with equal amplitude precision receive ratios.

```text
Qubits | Circuit        | Mode | Workers | Fusion | CX opt | Rust ms | Python ms* | Aer ms | Py/Rust | Aer/Rust | Check      
-------+----------------+------+---------+--------+--------+---------+------------+--------+---------+----------+------------
16     | mixed          | f64  | 4       | on     | off    | 0.725   | 7.833      | 1.618  | 10.80x  | 2.23x    | passed/full
16     | scrambled_bell | f64  | 4       | on     | off    | 3.417   | 40.239     | 7.317  | 11.78x  | 2.14x    | passed/full
20     | mixed          | f64  | 4       | on     | off    | 7.842   | 1338.244   | 12.112 | 170.65x | 1.54x    | passed/full
20     | scrambled_bell | f64  | 4       | on     | off    | 43.575  | 7031.969   | 73.218 | 161.37x | 1.68x    | passed/full
```

*Python is Qiskit quantum_info.Statevector, always FP64 with numerical library threads limited to one. Its ratios at multiple Rust workers use different requested CPU resources. Aer uses the row precision and worker count; it is the optimized simulator comparison. Aer has no FP16 mode here.

Request timing includes fresh allocation, initialization, execution and the same complex probes. Rust parsing, worker-pool creation and compilation are included; Python Statevector allocation/evolution/probe extraction and Aer job handling/fusion are included. Imports, circuit construction, process startup and reference checks are excluded. JSON request encoding precedes the Rust timer. Rust gate and compilation samples are separately recorded and are not substituted into ratios.

Engine order is shuffled deterministically then rotated per trial. Warmups are discarded. Full-state validation uses binary export outside timing through the configured cap; larger cases use complex probes only. Validation failure suppresses speed ratios. No automatic renormalization or global-phase alignment is applied.

Fusion defaults on, including structured Ry/Rz and specialized controlled/diagonal Rust kernels. Aer fusion matches the switch and retains its normal maximum block size. CX compression defaults off; enabling it changes executed gate count and is separately labelled. Clifford workloads still force Aer statevector execution.

CPU affinity and temperature are uncontrolled. Raw samples, execution order, source/binary/circuit hashes, memory snapshots, backend metadata, accuracy metrics and configuration are in results.json. Every original circuit/request is saved under cases/. Explicit skips and errors have no invented timing values.

Run complete: True

