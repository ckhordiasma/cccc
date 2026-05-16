use chrono::{Local, NaiveDate, TimeDelta};
use regex::Regex;
use serde::Deserialize;
use std::collections::{HashMap, HashSet};
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

const EMBEDDED_PRICING: &str = include_str!("../../pricing.json");

fn load_pricing(pricing_path: Option<&PathBuf>) -> (Vec<(Regex, ModelRates)>, f64) {
    let data = match pricing_path {
        Some(p) => fs::read_to_string(p).expect("failed to read pricing.json"),
        None => EMBEDDED_PRICING.to_string(),
    };
    let config: PricingConfig = serde_json::from_str(&data).expect("failed to parse pricing.json");

    let patterns: Vec<(Regex, ModelRates)> = config
        .models
        .iter()
        .map(|(pattern, rates)| {
            let re = Regex::new(pattern).expect("invalid regex in pricing.json");
            let r = ModelRates {
                input: rates["input"].as_f64().unwrap(),
                output: rates["output"].as_f64().unwrap(),
                cache_write: rates["cache_write"].as_f64().unwrap(),
                cache_read: rates["cache_read"].as_f64().unwrap(),
            };
            (re, r)
        })
        .collect();

    (patterns, config.web_search_cost_per_request)
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
    let args: Vec<String> = env::args().collect();

    let (start_date, end_date) = match args.len() {
        3.. => {
            let s = NaiveDate::parse_from_str(&args[1], "%Y-%m-%d").expect("invalid start date");
            let e = NaiveDate::parse_from_str(&args[2], "%Y-%m-%d").expect("invalid end date");
            (s, e)
        }
        2 => {
            let d = NaiveDate::parse_from_str(&args[1], "%Y-%m-%d").expect("invalid date");
            (d, d)
        }
        _ => {
            let today = Local::now().date_naive();
            (today, today)
        }
    };

    let pricing_path = env::var("CLAUDE_PRICING_FILE").ok().map(PathBuf::from);
    let (pricing_patterns, ws_cost) = load_pricing(pricing_path.as_ref());

    let projects_dir = match env::var("CLAUDE_PROJECTS_DIR") {
        Ok(p) => PathBuf::from(p),
        Err(_) => dirs(),
    };

    // Build date prefixes (include end_date+1 for UTC overlap)
    let mut prefixes = HashSet::new();
    let mut cur = start_date;
    let end_plus1 = end_date + TimeDelta::days(1);
    while cur <= end_plus1 {
        prefixes.insert(cur.format("%Y-%m-%d").to_string());
        cur += TimeDelta::days(1);
    }

    // Cutoff: midnight of start_date as Unix timestamp
    let cutoff = start_date
        .and_hms_opt(0, 0, 0)
        .unwrap()
        .and_local_timezone(Local)
        .unwrap()
        .timestamp() as u64;

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
            if !prefixes.iter().any(|p| ts.starts_with(p.as_str())) {
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

fn dirs() -> PathBuf {
    let home = env::var("HOME").expect("HOME not set");
    PathBuf::from(home).join(".claude/projects")
}
