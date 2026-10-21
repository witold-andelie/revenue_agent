use revenue_gateway::{
    now_epoch, Gateway, PersistedState, Quote, StrategyAuthorization, SubmitOrder,
};
use serde::Deserialize;
use serde_json::{json, Value};
use std::fs::OpenOptions;
use std::io::{self, BufRead, Write};
use std::path::{Path, PathBuf};
use std::thread;
use std::time::Duration;

#[derive(Debug, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
enum Request {
    Health,
    Quote(QuoteRequest),
    Authorize(StrategyAuthorization),
    Submit(SubmitOrder),
    Snapshot,
    Shutdown,
}

#[derive(Debug, Deserialize)]
struct QuoteRequest {
    symbol: String,
    price: f64,
    observed_at_epoch: i64,
}

fn main() {
    let stdin = io::stdin();
    let state_path = std::env::var_os("REVENUE_GATEWAY_STATE")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("data/rust-gateway-state.json"));
    // Two gateway processes writing the same state file would clobber each
    // other's fills; hold an exclusive lock for the process lifetime.
    let _lock = match acquire_state_lock(&state_path) {
        Some(lock) => lock,
        None => {
            eprintln!(
                "another revenue-gateway instance is holding {}",
                lock_path(&state_path).display()
            );
            std::process::exit(2);
        }
    };
    let mut gateway = load_gateway(&state_path);
    for line in stdin.lock().lines() {
        let line = match line {
            Ok(line) => line,
            Err(error) => {
                write_response(
                    &json!({"ok":false,"error":{"code":"io","message":error.to_string()}}),
                );
                break;
            }
        };
        if line.trim().is_empty() {
            continue;
        }
        let request: Result<Request, _> = serde_json::from_str(&line);
        let response = match request {
            Ok(Request::Health) => json!({"ok":true,"type":"health","mode":"paper"}),
            Ok(Request::Quote(request)) => match gateway.set_quote(Quote {
                symbol: request.symbol,
                price: request.price,
                observed_at_epoch: request.observed_at_epoch,
            }) {
                Ok(()) => {
                    save_gateway(&state_path, &gateway);
                    json!({"ok":true,"type":"quote"})
                }
                Err(error) => json!({"ok":false,"error":error}),
            },
            Ok(Request::Authorize(request)) => match gateway.authorize_strategy(request) {
                Ok(()) => {
                    save_gateway(&state_path, &gateway);
                    json!({"ok":true,"type":"authorization"})
                }
                Err(error) => json!({"ok":false,"error":error}),
            },
            Ok(Request::Submit(request)) => match gateway.submit_order(request, now_epoch()) {
                Ok(fill) => {
                    save_gateway(&state_path, &gateway);
                    json!({"ok":true,"type":"fill","fill":fill})
                }
                Err(error) => json!({"ok":false,"error":error}),
            },
            Ok(Request::Snapshot) => {
                json!({"ok":true,"type":"snapshot","snapshot":gateway.snapshot(now_epoch())})
            }
            Ok(Request::Shutdown) => {
                write_response(&json!({"ok":true,"type":"shutdown"}));
                break;
            }
            Err(error) => {
                json!({"ok":false,"error":{"code":"invalid_request","message":error.to_string()}})
            }
        };
        write_response(&response);
    }
}

fn load_gateway(path: &PathBuf) -> Gateway {
    match std::fs::read_to_string(path)
        .ok()
        .and_then(|text| serde_json::from_str::<PersistedState>(&text).ok())
    {
        Some(state) => Gateway::from_state(Default::default(), state),
        None => Gateway::new(Default::default()),
    }
}

fn lock_path(state_path: &Path) -> PathBuf {
    state_path.with_extension("lock")
}

struct StateLockGuard(PathBuf);

impl Drop for StateLockGuard {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}

/// Exclusive lockfile with stale takeover: a live holder keeps the file fresh
/// (rewritten on every save), so a leftover lock older than 30 seconds belongs
/// to a crashed process and may be claimed.
fn acquire_state_lock(state_path: &Path) -> Option<StateLockGuard> {
    let path = lock_path(state_path);
    for attempt in 0..240u32 {
        if let Ok(mut file) = OpenOptions::new().write(true).create_new(true).open(&path) {
            let _ = writeln!(file, "{}", std::process::id());
            return Some(StateLockGuard(path));
        }
        if let Ok(metadata) = std::fs::metadata(&path) {
            if let Ok(modified) = metadata.modified() {
                if let Ok(age) = modified.elapsed() {
                    if age.as_secs() >= 30 {
                        let _ = std::fs::remove_file(&path);
                        continue;
                    }
                }
            }
        }
        thread::sleep(Duration::from_millis(50));
        let _ = attempt;
    }
    None
}

fn save_gateway(path: &PathBuf, gateway: &Gateway) {
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    // Keep the lockfile fresh so the 30s stale-takeover never fires while
    // this process is alive and saving.
    let _ = std::fs::write(lock_path(path), std::process::id().to_string());
    if let Ok(json) = serde_json::to_string_pretty(&gateway.state()) {
        let temporary = path.with_extension("tmp");
        if std::fs::write(&temporary, json).is_ok() {
            let _ = std::fs::rename(temporary, path);
        }
    }
}

fn write_response(response: &Value) {
    let mut stdout = io::stdout().lock();
    let _ = writeln!(stdout, "{}", response);
    let _ = stdout.flush();
}
