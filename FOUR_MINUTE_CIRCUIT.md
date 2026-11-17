# Four-minute 25-qubit workload

On the same Apple M4 host, a 350-layer scrambled-Bell circuit took **245.306 seconds (4 min 5 sec) in Rust** using FP64 and four threads. Qiskit Aer 0.18.2 took **161.789 seconds (2 min 42 sec)** with the same precision/thread count and default fusion enabled (reported applied). Aer was 1.52× faster.

The circuit has 25 active qubits, 21,725 gates including 4,212 CX gates, and seed 42. It prepares 12 Bell pairs across a 12|13 partition and scrambles within each side, preserving exactly 12 ebits across that partition. FP64 state storage is 512 MiB.

These are single measured requests, without a workload warmup or repeated trials. Timers include fresh state allocation/initialization and execution, excluding process startup and circuit construction. They follow the small-output request scope described in [the matched Aer report](AER_25_QUBIT_COMPARISON.md). Four amplitude probes passed parity with maximum error 4.06e-18; this deeper circuit was not subjected to a full-state parity comparison. Runtime varies with host load and hardware.

- [Exact Rust request](benchmark-results/four-minute-25q/request.json)
- [Raw measurements and Aer metadata](benchmark-results/four-minute-25q/results.json)
- [Runner](tools/find_four_minute_circuit.py)

Run only the saved Rust workload:

```sh
cargo build --release --locked
target/release/statevector < benchmark-results/four-minute-25q/request.json
```

Regenerate and measure both engines:

```sh
PYTHONPYCACHEPREFIX=/private/tmp/qiskit-rust-aer-pycache \
  .venv/bin/python tools/find_four_minute_circuit.py
```
