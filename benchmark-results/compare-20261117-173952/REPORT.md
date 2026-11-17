# Runtime comparison

Primary times are median fresh-state request latencies in milliseconds. Ratios above one favor Rust; below one favor the other backend. Only passing cases with equal amplitude precision receive ratios.

```text
Qubits | Circuit        | Mode | Workers | Fusion | CX opt | Rust ms | Python ms* | Aer ms  | Py/Rust | Aer/Rust | Check      
-------+----------------+------+---------+--------+--------+---------+------------+---------+---------+----------+------------
16     | mixed          | f64  | 1       | on     | off    | 1.388   | 8.004      | 2.426   | 5.77x   | 1.75x    | passed/full
16     | mixed          | f64  | 4       | on     | off    | 0.745   | 7.258      | 1.631   | 9.74x   | 2.19x    | passed/full
16     | scrambled_bell | f64  | 1       | on     | off    | 6.104   | 39.384     | 13.157  | 6.45x   | 2.16x    | passed/full
16     | scrambled_bell | f64  | 4       | on     | off    | 3.124   | 40.146     | 7.286   | 12.85x  | 2.33x    | passed/full
20     | mixed          | f64  | 1       | on     | off    | 22.536  | 1358.909   | 32.435  | 60.30x  | 1.44x    | passed/full
20     | mixed          | f64  | 4       | on     | off    | 8.283   | 1352.334   | 12.123  | 163.26x | 1.46x    | passed/full
20     | scrambled_bell | f64  | 1       | on     | off    | 125.564 | 5388.124   | 227.069 | 42.91x  | 1.81x    | passed/full
20     | scrambled_bell | f64  | 4       | on     | off    | 39.733  | 5428.703   | 71.122  | 136.63x | 1.79x    | passed/full
```

*Python is Qiskit quantum_info.Statevector, always FP64 with numerical library threads limited to one. Its ratios at multiple Rust workers use different requested CPU resources. Aer uses the row precision and worker count; it is the optimized simulator comparison. Aer has no FP16 mode here.

Request timing includes fresh allocation, initialization, execution and the same complex probes. Rust parsing, worker-pool creation and compilation are included; Python Statevector allocation/evolution/probe extraction and Aer job handling/fusion are included. Imports, circuit construction, process startup and reference checks are excluded. JSON request encoding precedes the Rust timer. Rust gate and compilation samples are separately recorded and are not substituted into ratios.

Engine order is shuffled deterministically then rotated per trial. Warmups are discarded. Full-state validation uses binary export outside timing through the configured cap; larger cases use complex probes only. Validation failure suppresses speed ratios. No automatic renormalization or global-phase alignment is applied.

Fusion defaults on, including structured Ry/Rz and specialized controlled/diagonal Rust kernels. Aer fusion matches the switch and retains its normal maximum block size. CX compression defaults off; enabling it changes executed gate count and is separately labelled. Clifford workloads still force Aer statevector execution.

CPU affinity and temperature are uncontrolled. Raw samples, execution order, source/binary/circuit hashes, memory snapshots, backend metadata, accuracy metrics and configuration are in results.json. Every original circuit/request is saved under cases/. Explicit skips and errors have no invented timing values.

Run complete: True

