use std::io::{BufRead, BufReader, Write};
use std::process::{Command, Stdio};
use std::time::{SystemTime, UNIX_EPOCH};

#[test]
fn stdin_protocol_enforces_authorization_before_fill() {
    let state = std::env::temp_dir().join(format!("revenue-gateway-{}.json", std::process::id()));
    let _ = std::fs::remove_file(&state);
    let mut child = Command::new(env!("CARGO_BIN_EXE_revenue-gateway"))
        .env("REVENUE_GATEWAY_STATE", &state)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .expect("gateway binary should start");
    let mut input = child.stdin.take().unwrap();
    let stdout = child.stdout.take().unwrap();
    let mut output = BufReader::new(stdout);
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs();

    send(
        &mut input,
        &mut output,
        r#"{"type":"submit","idempotency_key":"unauthorized","symbol":"AMD","side":"buy","notional":100,"strategy_id":"s1","mode":"paper"}"#,
    );
    let response = send(
        &mut input,
        &mut output,
        r#"{"type":"authorize","strategy_id":"s1","human_id":"owner"}"#,
    );
    assert!(response["ok"].as_bool().unwrap());
    send(
        &mut input,
        &mut output,
        &format!(r#"{{"type":"quote","symbol":"AMD","price":100,"observed_at_epoch":{now}}}"#),
    );
    let fill = send(
        &mut input,
        &mut output,
        r#"{"type":"submit","idempotency_key":"authorized","symbol":"AMD","side":"buy","notional":100,"strategy_id":"s1","mode":"paper"}"#,
    );
    assert_eq!(fill["type"], "fill");
    send(&mut input, &mut output, r#"{"type":"shutdown"}"#);
    let _ = child.wait();
    let _ = std::fs::remove_file(state);
}

fn send(input: &mut impl Write, output: &mut impl BufRead, request: &str) -> serde_json::Value {
    writeln!(input, "{request}").unwrap();
    input.flush().unwrap();
    let mut line = String::new();
    output.read_line(&mut line).unwrap();
    serde_json::from_str(&line).unwrap()
}
