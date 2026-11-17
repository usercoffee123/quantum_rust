use serde_json::{Value, json};
use std::{
    io::Write,
    process::{Command, Stdio},
};

fn run(requests: &str) -> (bool, Vec<Value>) {
    let mut child = Command::new(env!("CARGO_BIN_EXE_statevector"))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    child
        .stdin
        .take()
        .unwrap()
        .write_all(requests.as_bytes())
        .unwrap();
    let output = child.wait_with_output().unwrap();
    let rows = String::from_utf8(output.stdout)
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect();
    (output.status.success(), rows)
}

#[test]
fn json_roundtrip_and_benchmark_summary() {
    let request = json!({"num_qubits": 2, "gates": [{"gate": "x", "target": 1}],
                         "include_state": false, "probe_indices": [0, 2],
                         "benchmark": {"repetitions": 3, "warmup": 1}});
    let (ok, rows) = run(&format!("{request}\n"));
    assert!(ok);
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0]["norm_sqr"], 1.0);
    assert_eq!(rows[0]["probes"], json!([[0.0, 0.0], [1.0, 0.0]]));
    assert!(rows[0].get("amplitudes").is_none());
    let times = rows[0]["seconds"].as_array().unwrap();
    assert_eq!(times.len(), 3);
    assert!(times.iter().all(|t| t.as_f64().unwrap() >= 0.0));
}

#[test]
fn invalid_requests_fail_and_following_lines_still_run() {
    let (ok, rows) = run(concat!(
        "{\"num_qubits\":2,\"gates\":[{\"gate\":\"cx\",\"control\":0,\"target\":0}]}\n",
        "{\"num_qubits\":0,\"gates\":[]}\n",
        "{\"num_qubits\":1,\"gates\":[],\"typo\":true}\n",
        "{\"num_qubits\":1,\"gates\":[],\"benchmark\":{\"repetitions\":0,\"warmup\":0}}\n",
        "{\"num_qubits\":1,\"gates\":[],\"probe_indices\":[2]}\n",
    ));
    assert!(!ok);
    assert_eq!(rows.len(), 5);
    assert!(rows[0]["error"].is_string());
    assert_eq!(rows[1]["amplitudes"], json!([[1.0, 0.0]]));
    for row in &rows[2..] {
        assert!(row["error"].is_string());
    }
}

#[test]
fn precision_selects_storage_and_rejects_unsupported_modes() {
    for (precision, bytes, compute) in [("f16", 16, 32), ("f32", 32, 32), ("f64", 64, 64)] {
        let request = json!({"num_qubits": 2, "precision": precision,
                             "gates": [{"gate": "h", "target": 0}],
                             "benchmark": {"repetitions": 1, "warmup": 0}});
        let (ok, rows) = run(&format!("{request}\n"));
        assert!(ok);
        assert_eq!(rows[0]["precision"], precision);
        assert_eq!(rows[0]["state_bytes"], bytes);
        assert_eq!(rows[0]["compute_bits"], compute);
        assert_eq!(rows[0]["accumulation_bits"], 64);
    }
    let (ok, rows) = run("{\"num_qubits\":1,\"precision\":\"bf16\",\"gates\":[]}\n");
    assert!(!ok);
    assert!(rows[0]["error"].is_string());
}

#[test]
fn cli_thread_selection_and_validation() {
    let request = json!({"num_qubits": 3, "threads": 2, "parallel_threshold": 0,
                         "precision": "f16", "gates": [{"gate":"x", "target":2}],
                         "benchmark": {"repetitions": 2, "warmup": 1}});
    let (ok, rows) = run(&format!("{request}\n"));
    assert!(ok);
    assert_eq!(rows[0]["threads"], 2);
    assert_eq!(rows[0]["parallel_threshold"], 0);
    assert_eq!(rows[0]["amplitudes"][4], json!([1.0, 0.0]));
    assert_eq!(rows[0]["seconds"].as_array().unwrap().len(), 2);
    let (ok, rows) = run("{\"num_qubits\":1,\"gates\":[],\"threads\":0}\n");
    assert!(!ok);
    assert!(rows[0]["error"].is_string());
}

#[test]
fn binary_state_export_and_optional_norm() {
    let path = std::env::temp_dir().join(format!("qiskit-rust-export-{}.bin", std::process::id()));
    let request = json!({"num_qubits":2,"gates":[{"gate":"x","target":1}],
                         "include_state":false,"include_norm":false,"amplitudes_output":path});
    let (ok, rows) = run(&format!("{request}\n"));
    assert!(ok);
    assert!(rows[0]["norm_sqr"].is_null());
    assert_eq!(rows[0]["amplitudes_output"]["bytes"], 64);
    let data = std::fs::read(&path).unwrap();
    std::fs::remove_file(path).unwrap();
    assert_eq!(data.len(), 64);
    let values: Vec<_> = data
        .as_chunks::<8>()
        .0
        .iter()
        .map(|bytes| f64::from_le_bytes(*bytes))
        .collect();
    assert_eq!(values, vec![0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]);
}

#[test]
fn optional_compilation_reports_counts_and_reuses_execution() {
    let gates = json!([{"gate":"h","target":0},
                      {"gate":"cx","control":0,"target":1},
                      {"gate":"cx","control":0,"target":1}]);
    let raw = json!({"num_qubits":2,"gates":gates});
    let optimized = json!({"num_qubits":2,"gates":gates,"optimize":true,
                            "benchmark":{"repetitions":2,"warmup":1}});
    let (ok, rows) = run(&format!("{raw}\n{optimized}\n"));
    assert!(ok);
    assert!(rows[0].get("compilation").is_none());
    assert_eq!(rows[0]["amplitudes"], rows[1]["amplitudes"]);
    assert_eq!(rows[1]["compilation"]["stats"]["input_gates"], 3);
    assert_eq!(rows[1]["compilation"]["stats"]["output_gates"], 1);
    assert_eq!(rows[1]["seconds"].as_array().unwrap().len(), 2);
    assert!(rows[1]["compilation"]["seconds"].as_f64().unwrap() >= 0.0);
    let bad = json!({"num_qubits":1,"optimize":true,"gates":[
        {"gate":"cx","control":0,"target":0},{"gate":"cx","control":0,"target":0}]});
    let (ok, rows) = run(&format!("{bad}\n"));
    assert!(!ok);
    assert!(rows[0]["error"].is_string());
}

#[test]
fn fusion_flag_executes_fused_blocks_independently_of_cx_optimization() {
    let gates = json!([{"gate":"h","target":0},{"gate":"ry","target":1,"theta":0.3},
        {"gate":"cx","control":1,"target":0},{"gate":"rz","target":0,"theta":0.7},
        {"gate":"cx","control":0,"target":1},{"gate":"rx","target":1,"theta":0.5}]);
    for optimize in [false, true] {
        let raw = json!({"num_qubits":2,"gates":gates});
        let fused = json!({"num_qubits":2,"gates":gates,"fusion":true,"optimize":optimize,
            "threads":4,"parallel_threshold":0,"benchmark":{"repetitions":2,"warmup":0}});
        let (ok, rows) = run(&format!("{raw}\n{fused}\n"));
        assert!(ok);
        assert_eq!(rows[1]["compilation"]["stats"]["fused_blocks"], 1);
        assert_eq!(rows[1]["compilation"]["stats"]["execution_operations"], 1);
        assert_eq!(rows[1]["seconds"].as_array().unwrap().len(), 2);
        for (a, b) in rows[0]["amplitudes"]
            .as_array()
            .unwrap()
            .iter()
            .zip(rows[1]["amplitudes"].as_array().unwrap())
        {
            for i in 0..2 {
                assert!((a[i].as_f64().unwrap() - b[i].as_f64().unwrap()).abs() < 1e-12);
            }
        }
    }
}
