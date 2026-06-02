//! `lob-game` — a playable trading-sim. Replay a real captured order-book tape,
//! trade against it, and ask an AI coach for strategy help.
//!
//! ```text
//! cargo run -p tui -- /tmp/play.tape --symbol BTC --speed 30 --cash 10000
//! ```
//! Keys: b buy · s sell · f flatten · +/- speed · space pause · ? ask coach · q quit.
//! The coach needs `ANTHROPIC_API_KEY`; without one the game is fully playable and
//! the coach panel just shows a hint.

use std::collections::VecDeque;
use std::fs::File;
use std::io::BufReader;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;

use agent::{Agent, Client};
use book::Side;
use crossterm::event::{self, Event, KeyCode};
use ratatui::layout::{Constraint, Direction, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Paragraph, Sparkline, Wrap};
use ratatui::Frame;
use replay::{pace, Replay};
use sim::{Report, Sim};
use tokio::sync::mpsc;

const LADDER_DEPTH: usize = 8;
const CHART_WIDTH: usize = 160;
const COACH_SYSTEM: &str = "You are a terse trading coach for a fast order-book game \
(paper trading, no real money). Given the live market microstructure and the player's \
position, recommend a concrete next action — BUY, SELL, HOLD, or FLATTEN — with a one-line \
reason grounded in the spread, book imbalance, and their PnL/inventory. Be decisive and \
brief: at most two sentences.";

/// Market state pushed from the replay clock task to the UI.
#[derive(Clone, Default)]
struct MarketSnapshot {
    symbol: String,
    bids: Vec<(f64, f64)>,
    asks: Vec<(f64, f64)>,
    mid: Option<f64>,
    last_trade: Option<(f64, bool)>,
    done: bool,
}

struct App {
    latest: MarketSnapshot,
    mids: VecDeque<f64>,
    trades: VecDeque<(f64, bool)>,
    sim: Sim,
    notional: f64,
    speed_x10: Arc<AtomicU64>,
    paused: Arc<AtomicBool>,
    coach: Option<Client>,
    coach_text: String,
    coaching: bool,
    log: VecDeque<String>,
    done: bool,
}

impl App {
    fn mark(&self) -> f64 {
        self.latest.mid.or(self.latest.last_trade.map(|(p, _)| p)).unwrap_or(0.0)
    }

    fn apply(&mut self, snap: MarketSnapshot) {
        if let Some(mid) = snap.mid {
            self.mids.push_back(mid);
            while self.mids.len() > CHART_WIDTH {
                self.mids.pop_front();
            }
            self.sim.mark(mid);
        }
        if let Some(t) = snap.last_trade {
            self.trades.push_back(t);
            while self.trades.len() > 6 {
                self.trades.pop_front();
            }
        }
        if snap.done {
            self.done = true;
            self.push_log("— session over —".into());
        }
        self.latest = snap;
    }

    fn push_log(&mut self, s: String) {
        self.log.push_back(s);
        while self.log.len() > 6 {
            self.log.pop_front();
        }
    }

    /// Handle a key; returns true to quit.
    fn on_key(&mut self, code: KeyCode, coach_tx: &mpsc::Sender<String>) -> bool {
        match code {
            KeyCode::Char('q') | KeyCode::Esc => return true,
            KeyCode::Char('b') => {
                if let Some((ask, _)) = self.latest.asks.first().copied() {
                    let qty = self.notional / ask;
                    self.sim.market_buy(qty, Some(ask));
                    self.push_log(format!("BUY  {qty:.6} @ {ask:.2}"));
                }
            }
            KeyCode::Char('s') => {
                if let Some((bid, _)) = self.latest.bids.first().copied() {
                    let qty = self.notional / bid;
                    self.sim.market_sell(qty, Some(bid));
                    self.push_log(format!("SELL {qty:.6} @ {bid:.2}"));
                }
            }
            KeyCode::Char('f') => {
                let bid = self.latest.bids.first().map(|&(p, _)| p);
                let ask = self.latest.asks.first().map(|&(p, _)| p);
                if self.sim.position().abs() > 0.0 {
                    self.sim.flatten(bid, ask);
                    self.push_log("FLATTEN".into());
                }
            }
            KeyCode::Char('+') | KeyCode::Char('=') => self.bump_speed(2.0),
            KeyCode::Char('-') | KeyCode::Char('_') => self.bump_speed(0.5),
            KeyCode::Char(' ') => {
                let p = !self.paused.load(Ordering::Relaxed);
                self.paused.store(p, Ordering::Relaxed);
            }
            KeyCode::Char('?') | KeyCode::Char('c') => self.ask_coach(coach_tx),
            _ => {}
        }
        false
    }

    fn bump_speed(&mut self, factor: f64) {
        let cur = self.speed_x10.load(Ordering::Relaxed) as f64 / 10.0;
        let next = (cur * factor).clamp(0.5, 5000.0);
        self.speed_x10.store((next * 10.0).round() as u64, Ordering::Relaxed);
    }

    fn ask_coach(&mut self, coach_tx: &mpsc::Sender<String>) {
        if self.coaching {
            return;
        }
        let Some(client) = self.coach.clone() else {
            self.coach_text = "set ANTHROPIC_API_KEY to enable the coach".into();
            return;
        };
        self.coaching = true;
        self.coach_text = "thinking…".into();
        let prompt = coach_prompt(&self.latest, &self.sim.snapshot(self.mark()));
        let tx = coach_tx.clone();
        tokio::spawn(async move {
            let agent = Agent::new(client, COACH_SYSTEM, vec![]).max_turns(2);
            let advice = match agent.run(&prompt, |_| {}).await {
                Ok(o) => o.text,
                Err(e) => format!("(coach error: {e})"),
            };
            let _ = tx.send(advice).await;
        });
    }
}

fn coach_prompt(m: &MarketSnapshot, r: &Report) -> String {
    let lvls = |v: &[(f64, f64)]| {
        v.iter().take(3).map(|(p, s)| format!("{p:.2}×{s:.3}")).collect::<Vec<_>>().join(", ")
    };
    let spread = match (m.bids.first(), m.asks.first()) {
        (Some((b, _)), Some((a, _))) => a - b,
        _ => 0.0,
    };
    format!(
        "Market {sym}: mid {mid:.2}, spread {spread:.2}.\n\
         Bids (px×sz): {bids}\nAsks (px×sz): {asks}\n\
         My position: {pos:.6} @ avg {avg:.2}; unrealized {unrl:+.2}; equity {eq:.2} \
         ({ret:+.2}%); max drawdown {dd:.2}%.\n\
         What should I do next?",
        sym = m.symbol,
        mid = m.mid.unwrap_or(0.0),
        bids = lvls(&m.bids),
        asks = lvls(&m.asks),
        pos = r.position,
        avg = r.avg_price,
        unrl = r.unrealized,
        eq = r.equity,
        ret = r.return_pct,
        dd = r.max_drawdown_pct,
    )
}

/// Replay clock: step the tape, push a snapshot, sleep per `pace` (honoring
/// live speed / pause), until end of tape.
async fn clock(
    mut replay: Replay<BufReader<File>>,
    tx: mpsc::Sender<MarketSnapshot>,
    speed_x10: Arc<AtomicU64>,
    paused: Arc<AtomicBool>,
) {
    let mut prev_ts: Option<u64> = None;
    loop {
        if paused.load(Ordering::Relaxed) {
            tokio::time::sleep(Duration::from_millis(50)).await;
            continue;
        }
        match replay.step() {
            Ok(Some(tick)) => {
                let m = replay.market();
                let snap = MarketSnapshot {
                    symbol: m.symbol,
                    bids: replay.ladder(Side::Bid, LADDER_DEPTH),
                    asks: replay.ladder(Side::Ask, LADDER_DEPTH),
                    mid: m.mid,
                    last_trade: match tick {
                        replay::Tick::Trade { px, buy, .. } => Some((px, buy)),
                        _ => None,
                    },
                    done: false,
                };
                if tx.send(snap).await.is_err() {
                    return;
                }
                let speed = speed_x10.load(Ordering::Relaxed) as f64 / 10.0;
                if let Some(p) = prev_ts {
                    tokio::time::sleep(pace(p, tick.ts_ms(), speed)).await;
                }
                prev_ts = Some(tick.ts_ms());
            }
            Ok(None) => {
                let _ = tx.send(MarketSnapshot { done: true, ..Default::default() }).await;
                return;
            }
            Err(_) => return,
        }
    }
}

fn pnl_color(v: f64) -> Color {
    if v >= 0.0 {
        Color::Green
    } else {
        Color::Red
    }
}

fn ui(f: &mut Frame, app: &App) {
    let root = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(1), Constraint::Min(0), Constraint::Length(1)])
        .split(f.area());
    render_title(f, root[0], app);
    let main = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(42), Constraint::Percentage(58)])
        .split(root[1]);
    render_ladder(f, main[0], app);
    let right = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Percentage(45), Constraint::Percentage(30), Constraint::Percentage(25)])
        .split(main[1]);
    render_chart(f, right[0], app);
    render_position(f, right[1], app);
    render_coach(f, right[2], app);
    render_help(f, root[2], app);
}

fn render_title(f: &mut Frame, area: Rect, app: &App) {
    let speed = app.speed_x10.load(Ordering::Relaxed) as f64 / 10.0;
    let paused = app.paused.load(Ordering::Relaxed);
    let state = if app.done {
        "OVER"
    } else if paused {
        "PAUSED"
    } else {
        "LIVE"
    };
    let mid = app.latest.mid.unwrap_or(0.0);
    let line = Line::from(vec![
        Span::styled(" lob-game ", Style::default().bg(Color::Cyan).fg(Color::Black).add_modifier(Modifier::BOLD)),
        Span::raw(format!(" {} ", app.latest.symbol)),
        Span::styled(format!("{mid:.2}"), Style::default().add_modifier(Modifier::BOLD)),
        Span::raw(format!("   {state}   {speed:.1}×")),
    ]);
    f.render_widget(Paragraph::new(line), area);
}

fn render_ladder(f: &mut Frame, area: Rect, app: &App) {
    let mut lines: Vec<Line> = Vec::new();
    // asks: show furthest first so best ask sits just above the mid line
    for &(px, sz) in app.latest.asks.iter().take(LADDER_DEPTH).rev() {
        lines.push(Line::from(vec![
            Span::styled(format!("{px:>12.2}", ), Style::default().fg(Color::Red)),
            Span::raw(format!("  {sz:>10.4}")),
        ]));
    }
    let mid = app.latest.mid.unwrap_or(0.0);
    lines.push(Line::from(Span::styled(
        format!("──────── {mid:.2} ────────"),
        Style::default().fg(Color::DarkGray),
    )));
    for &(px, sz) in app.latest.bids.iter().take(LADDER_DEPTH) {
        lines.push(Line::from(vec![
            Span::styled(format!("{px:>12.2}"), Style::default().fg(Color::Green)),
            Span::raw(format!("  {sz:>10.4}")),
        ]));
    }
    let block = Block::default().borders(Borders::ALL).title(" order book (price × size) ");
    f.render_widget(Paragraph::new(lines).block(block), area);
}

fn render_chart(f: &mut Frame, area: Rect, app: &App) {
    let block = Block::default().borders(Borders::ALL).title(" mid price ");
    if app.mids.len() < 2 {
        f.render_widget(block, area);
        return;
    }
    let min = app.mids.iter().cloned().fold(f64::INFINITY, f64::min);
    let max = app.mids.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    let span = (max - min).max(1e-9);
    let data: Vec<u64> = app.mids.iter().map(|&m| (((m - min) / span) * 100.0).round() as u64).collect();
    let last = app.latest.mid.unwrap_or(0.0);
    let spark = Sparkline::default()
        .block(block.title(format!(" mid {last:.2}  (lo {min:.2} / hi {max:.2}) ")))
        .data(&data)
        .max(100)
        .style(Style::default().fg(Color::Cyan));
    f.render_widget(spark, area);
}

fn render_position(f: &mut Frame, area: Rect, app: &App) {
    let r = app.sim.snapshot(app.mark());
    let pnl = r.total_pnl;
    let lines = vec![
        Line::from(format!("cash      {:>12.2}", r.cash)),
        Line::from(format!("position  {:>12.6} @ {:.2}", r.position, r.avg_price)),
        Line::from(vec![
            Span::raw("equity    "),
            Span::styled(format!("{:>12.2}", r.equity), Style::default().add_modifier(Modifier::BOLD)),
        ]),
        Line::from(vec![
            Span::raw("PnL       "),
            Span::styled(
                format!("{pnl:>+12.2}  ({:+.3}%)", r.return_pct),
                Style::default().fg(pnl_color(pnl)).add_modifier(Modifier::BOLD),
            ),
        ]),
        Line::from(format!("unreal    {:>+12.2}   real {:+.2}", r.unrealized, r.realized)),
        Line::from(format!("maxDD {:.2}%   fees {:.2}   fills {}", r.max_drawdown_pct, r.fees, r.fills)),
    ];
    let block = Block::default().borders(Borders::ALL).title(" position ");
    f.render_widget(Paragraph::new(lines).block(block), area);
}

fn render_coach(f: &mut Frame, area: Rect, app: &App) {
    let title = if app.coaching { " coach (thinking…) " } else { " coach  [?] " };
    let text = if app.coach_text.is_empty() {
        "press ? for strategy help".to_string()
    } else {
        app.coach_text.clone()
    };
    let block = Block::default().borders(Borders::ALL).title(title);
    f.render_widget(
        Paragraph::new(text).block(block).wrap(Wrap { trim: true }).style(Style::default().fg(Color::Yellow)),
        area,
    );
}

fn render_help(f: &mut Frame, area: Rect, app: &App) {
    let last = app.log.back().cloned().unwrap_or_default();
    let line = Line::from(vec![
        Span::styled(
            " b buy  s sell  f flat  +/- speed  space pause  ? coach  q quit ",
            Style::default().fg(Color::DarkGray),
        ),
        Span::raw("  "),
        Span::styled(last, Style::default().fg(Color::White)),
    ]);
    f.render_widget(Paragraph::new(line), area);
}

#[tokio::main]
async fn main() {
    // args
    let mut a = std::env::args().skip(1);
    let path = match a.next() {
        Some(p) => p,
        None => {
            eprintln!("usage: lob-game <tape> [--symbol S] [--speed N] [--cash N] [--fee-bps N]");
            std::process::exit(2);
        }
    };
    let (mut symbol, mut speed, mut cash, mut fee) = (None, 30.0_f64, 10_000.0_f64, 5.0_f64);
    while let Some(x) = a.next() {
        match x.as_str() {
            "--symbol" => symbol = a.next(),
            "--speed" => speed = a.next().and_then(|s| s.parse().ok()).unwrap_or(speed),
            "--cash" => cash = a.next().and_then(|s| s.parse().ok()).unwrap_or(cash),
            "--fee-bps" => fee = a.next().and_then(|s| s.parse().ok()).unwrap_or(fee),
            _ => {}
        }
    }

    let file = match File::open(&path) {
        Ok(f) => f,
        Err(e) => {
            eprintln!("open {path}: {e}");
            std::process::exit(1);
        }
    };
    let replay = Replay::new(BufReader::new(file), replay::DEFAULT_TICK_SCALE, symbol);

    let speed_x10 = Arc::new(AtomicU64::new((speed * 10.0) as u64));
    let paused = Arc::new(AtomicBool::new(false));

    let (market_tx, mut market_rx) = mpsc::channel::<MarketSnapshot>(256);
    let (coach_tx, mut coach_rx) = mpsc::channel::<String>(4);
    let (input_tx, mut input_rx) = mpsc::channel::<KeyCode>(32);

    // input thread (blocking crossterm reads -> channel)
    std::thread::spawn(move || loop {
        if let Ok(Event::Key(k)) = event::read() {
            if k.kind == event::KeyEventKind::Press && input_tx.blocking_send(k.code).is_err() {
                return;
            }
        }
    });

    // replay clock task
    tokio::spawn(clock(replay, market_tx, Arc::clone(&speed_x10), Arc::clone(&paused)));

    let mut app = App {
        latest: MarketSnapshot::default(),
        mids: VecDeque::new(),
        trades: VecDeque::new(),
        sim: Sim::new(cash, fee),
        notional: cash * 0.2,
        speed_x10,
        paused,
        coach: Client::from_env().ok(),
        coach_text: String::new(),
        coaching: false,
        log: VecDeque::new(),
        done: false,
    };

    let mut terminal = ratatui::init();
    let mut render_tick = tokio::time::interval(Duration::from_millis(100));
    loop {
        let _ = terminal.draw(|f| ui(f, &app));
        tokio::select! {
            Some(snap) = market_rx.recv() => app.apply(snap),
            Some(key) = input_rx.recv() => {
                if app.on_key(key, &coach_tx) { break; }
            }
            Some(advice) = coach_rx.recv() => {
                app.coach_text = advice;
                app.coaching = false;
            }
            _ = render_tick.tick() => {}
        }
    }
    ratatui::restore();

    // final report to stdout (after restoring the terminal)
    let r = app.sim.snapshot(app.mark());
    println!(
        "final: equity {:.2}  PnL {:+.2} ({:+.3}%)  fills {}  maxDD {:.2}%",
        r.equity, r.total_pnl, r.return_pct, r.fills, r.max_drawdown_pct
    );
}
