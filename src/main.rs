use chrono::{DateTime, Local, NaiveDate, TimeDelta};
use regex::Regex;
use serde::Deserialize;
use std::collections::HashMap;
use std::env;
use std::fs;
use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::time::SystemTime;
use walkdir::WalkDir;

#[derive(Deserialize)]
struct PricingConfig {
    models: serde_json::Map<String, serde_json::Value>,
    #[serde(default = "default_ws_cost")]
    web_search_cost_per_request: f64,
}

fn default_ws_cost() -> f64 {
    0.01
}

struct ModelRates {
    input: f64,
    output: f64,
    cache_write: f64,
    cache_read: f64,
}

#[derive(Deserialize)]
struct Entry {
    #[serde(rename = "type")]
    entry_type: Option<String>,
    timestamp: Option<String>,
    message: Option<Message>,
}

#[derive(Deserialize)]
struct Message {
    model: Option<String>,
    usage: Option<Usage>,
}

#[derive(Deserialize)]
struct Usage {
    #[serde(default)]
    input_tokens: u64,
    #[serde(default)]
    output_tokens: u64,
    #[serde(default)]
    cache_creation_input_tokens: u64,
    #[serde(default)]
    cache_read_input_tokens: u64,
    #[serde(default)]
    server_tool_use: Option<ServerToolUse>,
}

#[derive(Deserialize)]
struct ServerToolUse {
    #[serde(default)]
    web_search_requests: u64,
}

struct Totals {
    input: u64,
    output: u64,
    cache_write: u64,
    cache_read: u64,
    web_search: u64,
}

impl Totals {
    fn new() -> Self {
        Self { input: 0, output: 0, cache_write: 0, cache_read: 0, web_search: 0 }
    }
}

const EMBEDDED_PRICING: &str = include_str!("../pricing.json");

const USAGE: &str = "\
Estimate Claude Code API spend by scanning local session files.

Usage:
    cccc                            today
    cccc YYYY-MM-DD                 specific date
    cccc YYYY-MM-DD YYYY-MM-DD      date range (inclusive)
    cccc -h | --help                show this help

Environment:
    CLAUDE_PROJECTS_DIR             override session-file directory
                                    (default: ~/.claude/projects)
    CLAUDE_PRICING_FILE             use external pricing.json instead of embedded rates
    CCCC_DEBUG                      print per-model token totals to stderr
";

fn die(msg: impl std::fmt::Display) -> ! {
    eprintln!("cccc: {}", msg);
    std::process::exit(1);
}

fn die_with_usage(msg: impl std::fmt::Display) -> ! {
    eprintln!("cccc: {}", msg);
    eprintln!();
    eprint!("{}", USAGE);
    std::process::exit(1);
}

fn parse_date(s: &str) -> NaiveDate {
    NaiveDate::parse_from_str(s, "%Y-%m-%d")
        .unwrap_or_else(|_| die_with_usage(format!("invalid date {:?} (expected YYYY-MM-DD)", s)))
}

fn load_pricing(pricing_path: Option<&PathBuf>) -> Result<(Vec<(Regex, ModelRates)>, f64), String> {
    let data = match pricing_path {
        Some(p) => fs::read_to_string(p)
            .map_err(|e| format!("failed to read pricing file {}: {}", p.display(), e))?,
        None => EMBEDDED_PRICING.to_string(),
    };
    let config: PricingConfig = serde_json::from_str(&data)
        .map_err(|e| format!("failed to parse pricing.json: {}", e))?;

    let mut patterns = Vec::with_capacity(config.models.len());
    for (pattern, rates) in &config.models {
        let re = Regex::new(pattern)
            .map_err(|e| format!("invalid regex {:?} in pricing.json: {}", pattern, e))?;
        let get = |field: &str| -> Result<f64, String> {
            rates.get(field).and_then(|v| v.as_f64())
                .ok_or_else(|| format!("missing or non-numeric {:?} for pattern {:?}", field, pattern))
        };
        patterns.push((re, ModelRates {
            input: get("input")?,
            output: get("output")?,
            cache_write: get("cache_write")?,
            cache_read: get("cache_read")?,
        }));
    }
    if patterns.is_empty() {
        return Err("pricing.json has no models defined".into());
    }

    Ok((patterns, config.web_search_cost_per_request))
}

fn get_rates(model: &str, patterns: &[(Regex, ModelRates)]) -> (f64, f64, f64, f64) {
    for (re, rates) in patterns {
        if re.is_match(model) {
            return (rates.input, rates.output, rates.cache_write, rates.cache_read);
        }
    }
    let r = &patterns[0].1;
    (r.input, r.output, r.cache_write, r.cache_read)
}

fn main() {
    let args: Vec<String> = env::args().skip(1).collect();

    if args.iter().any(|a| a == "-h" || a == "--help") {
        print!("{}", USAGE);
        return;
    }
    if let Some(a) = args.first() {
        if a.starts_with('-') {
            die_with_usage(format!("unknown option {:?}", a));
        }
    }

    let (start_date, end_date) = match args.as_slice() {
        [] => {
            let today = Local::now().date_naive();
            (today, today)
        }
        [d] => {
            let d = parse_date(d);
            (d, d)
        }
        [s, e] => (parse_date(s), parse_date(e)),
        _ => die_with_usage("too many arguments"),
    };

    let pricing_path = env::var("CLAUDE_PRICING_FILE").ok().map(PathBuf::from);
    let (pricing_patterns, ws_cost) = load_pricing(pricing_path.as_ref()).unwrap_or_else(|e| die(e));

    let projects_dir = match env::var("CLAUDE_PROJECTS_DIR") {
        Ok(p) => PathBuf::from(p),
        Err(_) => dirs().unwrap_or_else(|e| die(e)),
    };

    // Filter messages whose timestamp falls in [start_unix, end_unix) where the
    // boundaries are *local* midnight. Prefix-matching on UTC date strings
    // miscounted messages near UTC midnight (yesterday-evening-local rolled into today-UTC).
    let start_unix = start_date
        .and_hms_opt(0, 0, 0)
        .unwrap()
        .and_local_timezone(Local)
        .unwrap()
        .timestamp();
    let end_unix = (end_date + TimeDelta::days(1))
        .and_hms_opt(0, 0, 0)
        .unwrap()
        .and_local_timezone(Local)
        .unwrap()
        .timestamp();
    let cutoff = start_unix as u64;

    let mut totals: HashMap<String, Totals> = HashMap::new();

    for entry in WalkDir::new(&projects_dir).into_iter().filter_map(|e| e.ok()) {
        let path = entry.path();
        if !path.is_file() || path.extension().and_then(|e| e.to_str()) != Some("jsonl") {
            continue;
        }

        let mtime = match path.metadata().and_then(|m| m.modified()) {
            Ok(t) => t
                .duration_since(SystemTime::UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs(),
            Err(_) => continue,
        };
        if mtime < cutoff {
            continue;
        }

        let file = match fs::File::open(path) {
            Ok(f) => f,
            Err(_) => continue,
        };

        for line in BufReader::new(file).lines() {
            let line = match line {
                Ok(l) => l,
                Err(_) => continue,
            };
            let entry: Entry = match serde_json::from_str(&line) {
                Ok(e) => e,
                Err(_) => continue,
            };

            if entry.entry_type.as_deref() != Some("assistant") {
                continue;
            }
            let ts = match &entry.timestamp {
                Some(t) => t,
                None => continue,
            };
            let ts_unix = match DateTime::parse_from_rfc3339(ts) {
                Ok(dt) => dt.timestamp(),
                Err(_) => continue,
            };
            if ts_unix < start_unix || ts_unix >= end_unix {
                continue;
            }
            let msg = match &entry.message {
                Some(m) => m,
                None => continue,
            };
            let model = match &msg.model {
                Some(m) if !m.is_empty() => m.clone(),
                _ => continue,
            };
            let usage = match &msg.usage {
                Some(u) => u,
                None => continue,
            };

            let t = totals.entry(model).or_insert_with(Totals::new);
            t.input += usage.input_tokens;
            t.output += usage.output_tokens;
            t.cache_write += usage.cache_creation_input_tokens;
            t.cache_read += usage.cache_read_input_tokens;
            t.web_search += usage
                .server_tool_use
                .as_ref()
                .map(|s| s.web_search_requests)
                .unwrap_or(0);
        }
    }

    let debug = env::var("CCCC_DEBUG").is_ok();
    let mut total_cost = 0.0_f64;
    for (model, counts) in &totals {
        let (r_in, r_out, r_cw, r_cr) = get_rates(model, &pricing_patterns);
        total_cost += counts.input as f64 * r_in / 1_000_000.0;
        total_cost += counts.output as f64 * r_out / 1_000_000.0;
        total_cost += counts.cache_write as f64 * r_cw / 1_000_000.0;
        total_cost += counts.cache_read as f64 * r_cr / 1_000_000.0;
        total_cost += counts.web_search as f64 * ws_cost;
        if debug {
            eprintln!(
                "{} in={} out={} cw={} cr={} ws={}",
                model, counts.input, counts.output, counts.cache_write,
                counts.cache_read, counts.web_search
            );
        }
    }

    println!("${:.2}", total_cost);
}

fn dirs() -> Result<PathBuf, String> {
    env::var("HOME")
        .map(|h| PathBuf::from(h).join(".claude/projects"))
        .map_err(|_| "HOME not set; set CLAUDE_PROJECTS_DIR to override".into())
}
