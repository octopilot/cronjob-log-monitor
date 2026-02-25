//! # CronJob log monitor (stuck-detector)
//!
//! Watches Pods by label, fetches their logs periodically, and deletes pods that have
//! had no log activity for a configurable period. Emits Datadog metrics and exposes
//! health endpoints. Patterns aligned with `secret-manager-controller` manager.rs.

use anyhow::{Context, Result};
use axum::{extract::State, http::StatusCode, response::Json, routing::get, Router};
use chrono::{DateTime, Utc};
use futures::{StreamExt, pin_mut};
use k8s_openapi::api::core::v1::Pod;
use cadence::prelude::*;
use kube::{
    api::{Api, ListParams, LogParams},
    runtime::watcher::{self, Config},
    Client,
};
use regex::Regex;
use serde_json::Value;
use std::{
    collections::HashMap,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tokio::sync::RwLock;
use tokio::time::sleep;
use tracing::{debug, error, info, warn};

/// Configuration from environment (§5.2 PRD).
#[derive(Debug, Clone)]
struct MonitorConfig {
    namespace: String,
    label_selector: String,
    pod_name_regex: Option<Regex>,
    label_regex: Option<(String, Regex)>,
    max_silence_minutes: u64,
    check_interval_seconds: u64,
    grace_period_seconds: u64,
    health_port: u16,
    log_timestamp_field: Option<String>,
    dd_agent_host: String,
    dd_dogstatsd_port: u16,
    dd_enabled: bool,
    rbac_max_attempts: u32,
}

impl MonitorConfig {
    fn from_env() -> Result<Self> {
        let monitor_label_selector =
            std::env::var("MONITOR_LABEL_SELECTOR").context("MONITOR_LABEL_SELECTOR is required")?;
        let pod_name_regex = std::env::var("MONITOR_POD_NAME_REGEX")
            .ok()
            .filter(|s| !s.is_empty())
            .map(|s| Regex::new(&s).context("Invalid MONITOR_POD_NAME_REGEX"))
            .transpose()?;
        let label_regex = std::env::var("MONITOR_LABEL_REGEX")
            .ok()
            .filter(|s| !s.is_empty())
            .map(|s| {
                let mut split = s.splitn(2, '=');
                let key = split.next().unwrap_or("").to_string();
                let pattern = split.next().unwrap_or("").to_string();
                let re = Regex::new(&pattern).context("Invalid MONITOR_LABEL_REGEX pattern")?;
                Ok::<_, anyhow::Error>((key, re))
            })
            .transpose()?;
        let max_silence_minutes = std::env::var("MAX_SILENCE_MINUTES")
            .unwrap_or_else(|_| "5".to_string())
            .parse()
            .context("Invalid MAX_SILENCE_MINUTES")?;
        let check_interval_seconds = std::env::var("CHECK_INTERVAL_SECONDS")
            .unwrap_or_else(|_| "30".to_string())
            .parse()
            .context("Invalid CHECK_INTERVAL_SECONDS")?;
        let grace_period_seconds = std::env::var("GRACE_PERIOD_SECONDS")
            .unwrap_or_else(|_| "120".to_string())
            .parse()
            .context("Invalid GRACE_PERIOD_SECONDS")?;
        let health_port = std::env::var("HEALTH_PORT")
            .unwrap_or_else(|_| "1234".to_string())
            .parse()
            .context("Invalid HEALTH_PORT")?;
        let log_timestamp_field = std::env::var("LOG_TIMESTAMP_FIELD").ok();
        let dd_agent_host =
            std::env::var("DD_AGENT_HOST").unwrap_or_else(|_| "127.0.0.1".to_string());
        let dd_dogstatsd_port = std::env::var("DD_DOGSTATSD_PORT")
            .unwrap_or_else(|_| "8125".to_string())
            .parse()
            .unwrap_or(8125);
        let dd_enabled = std::env::var("DD_ENABLED")
            .or_else(|_| std::env::var("DATADOG_METRICS_ENABLED"))
            .unwrap_or_else(|_| "true".to_string())
            .to_lowercase()
            != "false";
        let rbac_max_attempts = std::env::var("RBAC_MAX_ATTEMPTS")
            .unwrap_or_else(|_| "15".to_string())
            .parse()
            .unwrap_or(15);

        Ok(Self {
            namespace: std::env::var("NAMESPACE").context("NAMESPACE is required")?,
            label_selector: monitor_label_selector,
            pod_name_regex,
            label_regex,
            max_silence_minutes,
            check_interval_seconds,
            grace_period_seconds,
            health_port,
            log_timestamp_field,
            dd_agent_host,
            dd_dogstatsd_port,
            dd_enabled,
            rbac_max_attempts,
        })
    }

    fn check_interval(&self) -> Duration {
        Duration::from_secs(self.check_interval_seconds)
    }

    fn grace_period(&self) -> Duration {
        Duration::from_secs(self.grace_period_seconds)
    }

    fn max_silence(&self) -> Duration {
        Duration::from_secs(self.max_silence_minutes * 60)
    }
}

/// Shared state for health handlers.
#[derive(Clone)]
struct HealthState {
    ready: Arc<AtomicBool>,
    monitored_count: Arc<RwLock<usize>>,
}

/// Pod key: namespace/name.
fn pod_key(pod: &Pod) -> Option<String> {
    let ns = pod.metadata.namespace.as_deref()?;
    let name = pod.metadata.name.as_deref()?;
    Some(format!("{ns}/{name}"))
}

/// Check if pod passes optional regex filters (§5.1.1).
fn pod_matches_filters(config: &MonitorConfig, pod: &Pod) -> bool {
    if let Some(ref re) = config.pod_name_regex {
        let name = pod.metadata.name.as_deref().unwrap_or("");
        if !re.is_match(name) {
            return false;
        }
    }
    if let Some((ref label_key, ref re)) = config.label_regex {
        let labels = pod.metadata.labels.as_ref();
        let val = labels.and_then(|l| l.get(label_key)).map(|v| v.as_str()).unwrap_or("");
        if !re.is_match(val) {
            return false;
        }
    }
    true
}

/// Extract last activity time from log string: JSON @timestamp or "now" for last line received.
fn last_activity_from_logs(
    logs: &str,
    timestamp_field: Option<&str>,
    fallback_now: SystemTime,
) -> Option<SystemTime> {
    let field = timestamp_field.unwrap_or("@timestamp");
    let lines = logs.lines().filter(|l| !l.is_empty());
    let mut last_ts = None;
    for line in lines.rev() {
        if let Ok(v) = serde_json::from_str::<Value>(line) {
            if let Some(ts) = v.get(field).and_then(Value::as_str) {
                if let Ok(dt) = DateTime::parse_from_rfc3339(ts) {
                    last_ts = Some(dt.with_timezone(&Utc).into());
                    break;
                }
            }
        }
    }
    Some(last_ts.unwrap_or(fallback_now))
}

/// Fetch pod logs via Kubernetes API (tail only).
async fn fetch_pod_logs(
    client: &Client,
    namespace: &str,
    pod_name: &str,
    tail_lines: i32,
) -> Result<String> {
    let pods: Api<Pod> = Api::namespaced(client.clone(), namespace);
    let lp = LogParams {
        tail_lines: Some(tail_lines as i64),
        ..LogParams::default()
    };
    let logs = pods.logs(pod_name, &lp).await?;
    Ok(logs)
}

/// Pod start time from status (container running started_at).
fn pod_start_time(pod: &Pod) -> Option<SystemTime> {
    let started = pod
        .status
        .as_ref()?
        .container_statuses
        .as_ref()?
        .first()?
        .state
        .as_ref()?
        .running
        .as_ref()?
        .started_at
        .as_ref()?;
    // k8s_openapi Time is chrono::DateTime<Utc>; convert to SystemTime
    let t = started.0;
    let secs = t.timestamp();
    if secs < 0 {
        return None;
    }
    Some(UNIX_EPOCH + Duration::from_secs(secs as u64))
}

/// Wait until we can list pods (RBAC ready).
async fn wait_for_rbac(
    client: &Client,
    namespace: &str,
    label_selector: &str,
    max_attempts: u32,
) -> Result<()> {
    let pods: Api<Pod> = Api::namespaced(client.clone(), namespace);
    let lp = ListParams::default().labels(label_selector);
    let mut attempt = 0;
    let mut delay = Duration::from_secs(2);
    while attempt < max_attempts {
        match pods.list(&lp).await {
            Ok(_) => {
                info!("✅ RBAC ready – can list pods");
                return Ok(());
            }
            Err(kube::Error::Api(e)) if e.code == 401 || e.code == 403 => {
                attempt += 1;
                if attempt % 3 == 0 {
                    info!("⏳ Waiting for RBAC... (attempt {attempt}/{max_attempts})");
                }
                sleep(delay).await;
                delay = std::cmp::min(delay * 2, Duration::from_secs(30));
            }
            Err(e) => {
                return Err(e.into());
            }
        }
    }
    warn!("⚠️ RBAC check timed out after {max_attempts} attempts; continuing");
    Ok(())
}

/// Watch Pods and maintain monitored set (Apply → add, Delete → remove). Optional regex filter applied when adding.
async fn watch_pods(
    client: Client,
    config: Arc<MonitorConfig>,
    monitored: Arc<RwLock<HashMap<String, Pod>>>,
    ready: Arc<AtomicBool>,
) -> Result<()> {
    wait_for_rbac(
        &client,
        &config.namespace,
        &config.label_selector,
        config.rbac_max_attempts,
    )
    .await?;

    let pods: Api<Pod> = Api::namespaced(client.clone(), &config.namespace);
    let watcher_config = Config::default().labels(&config.label_selector);
    let watcher = watcher::watcher(pods, watcher_config);
    pin_mut!(watcher);

    info!(
        "👀 Watching Pods in {} with selector {}",
        config.namespace, config.label_selector
    );
    ready.store(true, Ordering::Relaxed);

    while let Some(event_result) = watcher.next().await {
        match event_result {
            Ok(event) => {
                match event {
                    kube::runtime::watcher::Event::Apply(pod) => {
                        if !pod_matches_filters(&config, &pod) {
                            if let Some(k) = pod_key(&pod) {
                                let mut m = monitored.write().await;
                                m.remove(&k);
                            }
                            continue;
                        }
                        if let Some(k) = pod_key(&pod) {
                            let phase = pod.status.as_ref().and_then(|s| s.phase.as_deref());
                            if phase == Some("Running") {
                                let mut m = monitored.write().await;
                                m.insert(k, pod);
                            }
                        }
                    }
                    kube::runtime::watcher::Event::Delete(pod) => {
                        if let Some(k) = pod_key(&pod) {
                            let mut m = monitored.write().await;
                            m.remove(&k);
                        }
                    }
                    kube::runtime::watcher::Event::Init
                    | kube::runtime::watcher::Event::InitApply(_)
                    | kube::runtime::watcher::Event::InitDone => {
                        debug!("Initial watch event");
                    }
                }
            }
            Err(e) => {
                let s = e.to_string();
                if s.contains("401") || s.contains("403") || s.contains("Unauthorized") {
                    warn!("⚠️ RBAC error watching Pods (will retry): {}", e);
                    sleep(Duration::from_secs(10)).await;
                } else {
                    error!("Error watching Pods: {}", e);
                    sleep(Duration::from_secs(5)).await;
                }
            }
        }
    }
    warn!("Pod watch stream ended");
    Ok(())
}

/// Log-check loop: for each monitored pod, fetch logs, compute last activity, delete if stuck and emit Datadog.
async fn check_logs_loop(
    client: Client,
    config: Arc<MonitorConfig>,
    monitored: Arc<RwLock<HashMap<String, Pod>>>,
    last_activity: Arc<RwLock<HashMap<String, SystemTime>>>,
    statsd: Option<cadence::StatsdClient>,
) -> Result<()> {
    let check_interval = config.check_interval();
    let max_silence = config.max_silence();
    let grace_period = config.grace_period();
    let namespace = config.namespace.clone();
    let timestamp_field = config.log_timestamp_field.clone();

    loop {
        sleep(check_interval).await;
        let pods: Vec<(String, Pod)> = {
            let m = monitored.read().await;
            m.iter()
                .map(|(k, p)| (k.clone(), p.clone()))
                .collect()
        };
        let now = SystemTime::now();
        for (key, pod) in pods {
            let name = pod.metadata.name.as_deref().unwrap_or("");
            let pod_start = match pod_start_time(&pod) {
                Some(t) => t,
                None => continue,
            };
            if now.duration_since(pod_start).unwrap_or(Duration::ZERO) < grace_period {
                continue;
            }
            let logs = match fetch_pod_logs(&client, &namespace, name, 100).await {
                Ok(l) => l,
                Err(e) => {
                    warn!("Failed to fetch logs for {}: {}", name, e);
                    continue;
                }
            };
            let fallback_now = now;
            let last = last_activity_from_logs(
                &logs,
                timestamp_field.as_deref(),
                fallback_now,
            );
            let mut la = last_activity.write().await;
            if let Some(t) = last {
                la.insert(key.clone(), t);
            }
            let last_ts = la.get(&key).copied().unwrap_or(pod_start);
            let silence = now.duration_since(last_ts).unwrap_or(Duration::ZERO);
            if silence >= max_silence {
                info!(
                    "stuck-detector: no activity for {} min ({} silence) – deleting pod {}",
                    config.max_silence_minutes,
                    silence.as_secs(),
                    name
                );
                let pods_api: Api<Pod> = Api::namespaced(client.clone(), &namespace);
                if let Err(e) = pods_api.delete(name, &Default::default()).await {
                    error!("Failed to delete pod {}: {}", name, e);
                    continue;
                }
                let pod_age_secs = now.duration_since(pod_start).unwrap_or(Duration::ZERO).as_secs() as f64;
                let silence_secs = silence.as_secs() as f64;
                if let Some(ref s) = statsd {
                    let _ = s.incr("cronjob_log_monitor.stuck_pod.deleted");
                    let _ = s.gauge("cronjob_log_monitor.stuck_pod.silence_duration_seconds", silence_secs);
                    let _ = s.gauge("cronjob_log_monitor.stuck_pod.pod_age_seconds", pod_age_secs);
                }
                la.remove(&key);
            }
        }
        if let Some(ref s) = statsd {
            let count = monitored.read().await.len();
            let _ = s.count("cronjob_log_monitor.pods_checked", count as i64);
        }
    }
}

/// Build DogStatsD client if DD enabled.
fn make_statsd(config: &MonitorConfig) -> Option<cadence::StatsdClient> {
    if !config.dd_enabled {
        return None;
    }
    use cadence::{StatsdClient, UdpMetricSink};
    let socket = std::net::UdpSocket::bind("0.0.0.0:0").ok()?;
    socket.set_nonblocking(true).ok()?;
    let host = (config.dd_agent_host.as_str(), config.dd_dogstatsd_port);
    let sink = UdpMetricSink::from(host, socket).ok()?;
    Some(StatsdClient::from_sink("cronjob_log_monitor", sink))
}

async fn health_handler(State(state): State<HealthState>) -> (StatusCode, Json<Value>) {
    let ready = state.ready.load(Ordering::Relaxed);
    let count = *state.monitored_count.read().await;
    let response = serde_json::json!({
        "status": if ready { "healthy" } else { "starting" },
        "ready": ready,
        "monitored_pods": count,
    });
    (StatusCode::OK, Json(response))
}

async fn liveness_handler() -> (StatusCode, Json<Value>) {
    (StatusCode::OK, Json(serde_json::json!({ "status": "alive" })))
}

async fn readiness_handler(State(state): State<HealthState>) -> (StatusCode, Json<Value>) {
    let ready = state.ready.load(Ordering::Relaxed);
    let status = if ready { "ready" } else { "not_ready" };
    let response = serde_json::json!({ "status": status, "ready": ready });
    if ready {
        (StatusCode::OK, Json(response))
    } else {
        (StatusCode::SERVICE_UNAVAILABLE, Json(response))
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .init();

    info!("🚀 Starting CronJob log monitor (stuck-detector)...");
    let config = MonitorConfig::from_env().context("Failed to load configuration")?;
    info!(
        "   namespace={} label_selector={} max_silence_min={} check_interval_s={} grace_s={} health_port={}",
        config.namespace,
        config.label_selector,
        config.max_silence_minutes,
        config.check_interval_seconds,
        config.grace_period_seconds,
        config.health_port
    );

    rustls::crypto::CryptoProvider::install_default(rustls::crypto::ring::default_provider())
        .expect("Failed to install rustls crypto provider");

    let client = Client::try_default()
        .await
        .context("Failed to create Kubernetes client")?;

    let config = Arc::new(config);
    let monitored = Arc::new(RwLock::new(HashMap::new()));
    let last_activity = Arc::new(RwLock::new(HashMap::new()));
    let ready = Arc::new(AtomicBool::new(false));
    let statsd = make_statsd(&config);

    let monitored_clone = monitored.clone();
    let ready_clone = ready.clone();
    let config_watch = config.clone();
    let client_watch = client.clone();
    tokio::spawn(async move {
        if let Err(e) = watch_pods(client_watch, config_watch, monitored_clone, ready_clone).await
        {
            error!("Pod watcher error: {}", e);
        }
    });

    let client_loop = client.clone();
    let config_loop = config.clone();
    let monitored_loop = monitored.clone();
    let last_activity_loop = last_activity.clone();
    tokio::spawn(async move {
        if let Err(e) = check_logs_loop(
            client_loop,
            config_loop,
            monitored_loop,
            last_activity_loop,
            statsd,
        )
        .await
        {
            error!("Log check loop error: {}", e);
        }
    });

    let monitored_count = Arc::new(RwLock::new(0usize));
    let health_state = HealthState {
        ready: ready.clone(),
        monitored_count: monitored_count.clone(),
    };
    let app = Router::new()
        .route("/health", get(health_handler))
        .route("/healthz", get(health_handler))
        .route("/live", get(liveness_handler))
        .route("/liveness", get(liveness_handler))
        .route("/ready", get(readiness_handler))
        .route("/readyz", get(readiness_handler))
        .with_state(health_state);

    let addr = format!("0.0.0.0:{}", config.health_port);
    info!("🏥 Health server on {}", addr);
    let listener = tokio::net::TcpListener::bind(&addr)
        .await
        .context("Failed to bind health server")?;
    axum::serve(listener, app).await?;
    Ok(())
}
