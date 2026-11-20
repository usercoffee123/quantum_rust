# Runtime comparison

Primary times are median fresh-state request latencies in milliseconds. Ratios above one favor Rust; below one favor the other backend. Only passing cases with equal amplitude precision receive ratios.

```text
Qubits | Circuit         | Mode | Workers | Fusion | CX opt | Rust ms | Python ms* | Aer ms  | Py/Rust | Aer/Rust | Check
-------+-----------------+------+---------+--------+--------+---------+------------+---------+---------+----------+--------------
20     | random_matching | f64  | 4       | on     | off    | 43.243  | -          | 73.798  | -       | 1.71x    | passed/full
20     | random_matching | f64  | 4       | off    | off    | 90.918  | -          | 138.685 | -       | 1.53x    | passed/full
20     | cx_stride       | f64  | 4       | on     | off    | 9.097   | -          | 10.091  | -       | 1.11x    | passed/full
20     | cx_stride       | f64  | 4       | off    | off    | 9.518   | -          | 17.382  | -       | 1.83x    | passed/full
22     | random_matching | f64  | 4       | on     | off    | 296.792 | -          | 345.826 | -       | 1.17x    | passed/probes
22     | random_matching | f64  | 4       | off    | off    | 603.502 | -          | 774.995 | -       | 1.28x    | passed/probes
22     | cx_stride       | f64  | 4       | on     | off    | 77.817  | -          | 44.244  | -       | 0.57x    | passed/probes
22     | cx_stride       | f64  | 4       | off    | off    | 84.495  | -          | 137.283 | -       | 1.62x    | passed/probes
```

*Python is Qiskit quantum_info.Statevector, always FP64 with numerical library threads limited to one. Its ratios at multiple Rust workers use different requested CPU resources. Aer uses the row precision and worker count; it is the optimized simulator comparison. Aer has no FP16 mode here.

Request timing includes fresh allocation, initialization, execution and the same complex probes. Rust parsing, worker-pool creation and compilation are included; Python Statevector allocation/evolution/probe extraction and Aer job handling/fusion are included. Imports, circuit construction, process startup and reference checks are excluded. JSON request encoding precedes the Rust timer. Rust gate and compilation samples are separately recorded and are not substituted into ratios.

Engine order is shuffled deterministically then rotated per trial. Warmups are discarded. Full-state validation uses binary export outside timing through the configured cap; larger cases use complex probes only. Validation failure suppresses speed ratios. No automatic renormalization or global-phase alignment is applied.

Fusion defaults on, including structured Ry/Rz and specialized controlled/diagonal Rust kernels. Aer fusion matches the switch and retains its normal maximum block size. CX compression defaults off; enabling it changes executed gate count and is separately labelled. Clifford workloads still force Aer statevector execution.

CPU affinity and temperature are uncontrolled. Raw samples, execution order, source/binary/circuit hashes, memory snapshots, backend metadata, accuracy metrics and configuration are in results.json. Every original circuit/request is saved under cases/. Explicit skips and errors have no invented timing values.

Run complete: True
