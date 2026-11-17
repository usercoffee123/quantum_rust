# Runtime comparison

Primary times are median fresh-state request latencies in milliseconds. Ratios above one favor Rust; below one favor the other backend. Only passing cases with equal amplitude precision receive ratios.

```text
Qubits | Circuit   | Mode | Workers | Fusion | CX opt | Rust ms | Python ms* | Aer ms | Py/Rust | Aer/Rust | Check        
-------+-----------+------+---------+--------+--------+---------+------------+--------+---------+----------+--------------
10     | cx_stride | f32  | 4       | on     | off    | 0.113   | -          | 0.745  | -       | 6.62x    | passed/probes
10     | cx_stride | f32  | 4       | off    | off    | 0.102   | -          | 0.701  | -       | 6.84x    | passed/probes
10     | cx_stride | f16  | 4       | on     | off    | 0.130   | -          | -      | -       | -        | passed/probes
10     | cx_stride | f16  | 4       | off    | off    | 0.098   | -          | -      | -       | -        | passed/probes
10     | diagonal  | f32  | 4       | on     | off    | 0.135   | -          | 0.804  | -       | 5.95x    | passed/probes
10     | diagonal  | f32  | 4       | off    | off    | 0.121   | -          | 0.822  | -       | 6.79x    | passed/probes
10     | diagonal  | f16  | 4       | on     | off    | 0.130   | -          | -      | -       | -        | passed/probes
10     | diagonal  | f16  | 4       | off    | off    | 0.130   | -          | -      | -       | -        | passed/probes
```

*Python is Qiskit quantum_info.Statevector, always FP64 with numerical library threads limited to one. Its ratios at multiple Rust workers use different requested CPU resources. Aer uses the row precision and worker count; it is the optimized simulator comparison. Aer has no FP16 mode here.

Request timing includes fresh allocation, initialization, execution and the same complex probes. Rust parsing, worker-pool creation and compilation are included; Python Statevector allocation/evolution/probe extraction and Aer job handling/fusion are included. Imports, circuit construction, process startup and reference checks are excluded. JSON request encoding precedes the Rust timer. Rust gate and compilation samples are separately recorded and are not substituted into ratios.

Engine order is shuffled deterministically then rotated per trial. Warmups are discarded. Full-state validation uses binary export outside timing through the configured cap; larger cases use complex probes only. Validation failure suppresses speed ratios. No automatic renormalization or global-phase alignment is applied.

Fusion defaults on, including structured Ry/Rz and specialized controlled/diagonal Rust kernels. Aer fusion matches the switch and retains its normal maximum block size. CX compression defaults off; enabling it changes executed gate count and is separately labelled. Clifford workloads still force Aer statevector execution.

CPU affinity and temperature are uncontrolled. Raw samples, execution order, source/binary/circuit hashes, memory snapshots, backend metadata, accuracy metrics and configuration are in results.json. Every original circuit/request is saved under cases/. Explicit skips and errors have no invented timing values.

Run complete: True

