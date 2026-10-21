use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};

const FEE_BPS: f64 = 10.0;

#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Side {
    Buy,
    Sell,
    Short,
    Cover,
}

#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Mode {
    Paper,
    Live,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Quote {
    pub symbol: String,
    pub price: f64,
    pub observed_at_epoch: i64,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct SubmitOrder {
    pub idempotency_key: String,
    pub symbol: String,
    pub side: Side,
    pub notional: f64,
    pub strategy_id: String,
    pub mode: Mode,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct StrategyAuthorization {
    pub strategy_id: String,
    pub human_id: String,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Fill {
    pub order_id: u64,
    pub idempotency_key: String,
    pub symbol: String,
    pub side: Side,
    pub quantity: f64,
    pub price: f64,
    pub notional: f64,
    pub fee: f64,
    pub strategy_id: String,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Position {
    pub quantity: f64,
    pub average_price: f64,
}

#[derive(Debug, Clone, Serialize)]
pub struct Snapshot {
    pub mode: Mode,
    pub cash: f64,
    pub market_value: f64,
    pub equity: f64,
    pub positions: HashMap<String, Position>,
    /// Holdings whose server quote is missing or stale; their market value
    /// falls back to cost basis instead of being silently marked at zero.
    pub unpriced_symbols: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct GatewayError {
    pub code: String,
    pub message: String,
}

#[derive(Debug, Clone)]
pub struct RiskConfig {
    pub initial_cash: f64,
    pub max_order_notional: f64,
    pub max_leverage: f64,
    pub max_quote_age_seconds: i64,
    pub allow_uncovered_short: bool,
}

impl Default for RiskConfig {
    fn default() -> Self {
        Self {
            initial_cash: 100_000.0,
            max_order_notional: 25_000.0,
            max_leverage: 1.0,
            max_quote_age_seconds: 60,
            allow_uncovered_short: false,
        }
    }
}

#[derive(Debug)]
pub struct Gateway {
    config: RiskConfig,
    cash: f64,
    next_order_id: u64,
    quotes: HashMap<String, Quote>,
    positions: HashMap<String, Position>,
    fills: HashMap<String, Fill>,
    authorized_strategies: HashMap<String, String>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct PersistedState {
    pub cash: f64,
    pub next_order_id: u64,
    pub quotes: HashMap<String, Quote>,
    pub positions: HashMap<String, Position>,
    pub fills: HashMap<String, Fill>,
    pub authorized_strategies: HashMap<String, String>,
}

impl Gateway {
    pub fn new(config: RiskConfig) -> Self {
        Self {
            cash: config.initial_cash,
            config,
            next_order_id: 1,
            quotes: HashMap::new(),
            positions: HashMap::new(),
            fills: HashMap::new(),
            authorized_strategies: HashMap::new(),
        }
    }

    pub fn from_state(config: RiskConfig, state: PersistedState) -> Self {
        Self {
            config,
            cash: state.cash,
            next_order_id: state.next_order_id,
            quotes: state.quotes,
            positions: state.positions,
            fills: state.fills,
            authorized_strategies: state.authorized_strategies,
        }
    }

    pub fn state(&self) -> PersistedState {
        PersistedState {
            cash: self.cash,
            next_order_id: self.next_order_id,
            quotes: self.quotes.clone(),
            positions: self.positions.clone(),
            fills: self.fills.clone(),
            authorized_strategies: self.authorized_strategies.clone(),
        }
    }

    pub fn authorize_strategy(
        &mut self,
        authorization: StrategyAuthorization,
    ) -> Result<(), GatewayError> {
        if authorization.strategy_id.trim().is_empty() || authorization.human_id.trim().is_empty() {
            return Err(error(
                "invalid_authorization",
                "strategy_id and human_id are required",
            ));
        }
        self.authorized_strategies
            .insert(authorization.strategy_id, authorization.human_id);
        Ok(())
    }

    pub fn set_quote(&mut self, quote: Quote) -> Result<(), GatewayError> {
        if quote.symbol.trim().is_empty() || !quote.price.is_finite() || quote.price <= 0.0 {
            return Err(error(
                "invalid_quote",
                "quote symbol and price must be valid",
            ));
        }
        self.quotes.insert(quote.symbol.to_ascii_uppercase(), quote);
        Ok(())
    }

    fn quote_is_fresh(&self, quote: &Quote, now_epoch: i64) -> bool {
        now_epoch >= quote.observed_at_epoch
            && now_epoch - quote.observed_at_epoch <= self.config.max_quote_age_seconds
    }

    /// Best available price for exposure math: a fresh server quote, else the
    /// position's cost basis. Never silently values a holding at zero.
    fn reference_price(&self, symbol: &str, now_epoch: i64) -> f64 {
        match self.quotes.get(symbol) {
            Some(quote) if self.quote_is_fresh(quote, now_epoch) => quote.price,
            _ => self
                .positions
                .get(symbol)
                .map(|position| position.average_price)
                .unwrap_or(0.0),
        }
    }

    pub fn submit_order(
        &mut self,
        request: SubmitOrder,
        now_epoch: i64,
    ) -> Result<Fill, GatewayError> {
        if request.idempotency_key.trim().is_empty() {
            return Err(error(
                "missing_idempotency_key",
                "every order needs an idempotency key",
            ));
        }
        if let Some(fill) = self.fills.get(&request.idempotency_key) {
            return Ok(fill.clone());
        }
        if request.mode != Mode::Paper {
            return Err(error("live_disabled", "live broker mode is disabled"));
        }
        if !self
            .authorized_strategies
            .contains_key(&request.strategy_id)
        {
            return Err(error(
                "strategy_not_adopted",
                "a human-adopted strategy is required",
            ));
        }
        if request.notional <= 0.0 || request.notional > self.config.max_order_notional {
            return Err(error(
                "order_limit",
                "order notional exceeds the configured risk limit",
            ));
        }
        if matches!(request.side, Side::Short) && !self.config.allow_uncovered_short {
            return Err(error("short_disabled", "uncovered shorting is disabled"));
        }
        let symbol = request.symbol.to_ascii_uppercase();
        let quote = self
            .quotes
            .get(&symbol)
            .ok_or_else(|| error("missing_quote", "no quote is available for the symbol"))?;
        if now_epoch < quote.observed_at_epoch
            || now_epoch - quote.observed_at_epoch > self.config.max_quote_age_seconds
        {
            return Err(error(
                "stale_quote",
                "server quote is stale or from the future",
            ));
        }
        let quantity = request.notional / quote.price;
        let fee = request.notional * FEE_BPS / 10_000.0;
        let signed = if matches!(request.side, Side::Buy | Side::Cover) {
            quantity
        } else {
            -quantity
        };
        let cash_delta = if matches!(request.side, Side::Buy | Side::Cover) {
            -(request.notional + fee)
        } else {
            request.notional - fee
        };
        if matches!(request.side, Side::Buy | Side::Cover) && self.cash + cash_delta < -f64::EPSILON
        {
            return Err(error(
                "insufficient_cash",
                "paper account cash is insufficient",
            ));
        }
        let old = self.positions.get(&symbol).cloned().unwrap_or(Position {
            quantity: 0.0,
            average_price: 0.0,
        });
        if matches!(request.side, Side::Sell) && quantity > old.quantity + f64::EPSILON {
            return Err(error(
                "uncovered_sell",
                "sell quantity exceeds the held position",
            ));
        }
        let new_quantity = old.quantity + signed;
        // Risk-increasing fills must keep equity positive and inside the
        // leverage cap; pure de-risking fills always stay allowed.
        let old_gross = self.current_gross_exposure(now_epoch);
        let projected_gross = self.projected_gross_exposure(&symbol, new_quantity, quote.price, now_epoch);
        if projected_gross > old_gross + f64::EPSILON {
            let projected_equity =
                self.projected_equity(&symbol, new_quantity, quote.price, now_epoch, cash_delta);
            if projected_equity <= f64::EPSILON {
                return Err(error(
                    "equity_exhausted",
                    "paper account equity cannot fund more exposure",
                ));
            }
            if projected_gross > self.config.max_leverage * projected_equity + f64::EPSILON {
                return Err(error(
                    "leverage_exceeded",
                    "order would push gross exposure past the configured max leverage",
                ));
            }
        }
        // Opening or flipping through zero reprices at the trade; a partial
        // reduction keeps the original cost basis; adding scales the average.
        let opening_or_flip =
            old.quantity == 0.0 || (old.quantity.signum() != signed.signum() && signed.abs() > old.quantity.abs());
        let average_price = if opening_or_flip {
            quote.price
        } else if old.quantity.signum() != signed.signum() {
            old.average_price
        } else {
            (old.quantity.abs() * old.average_price + signed.abs() * quote.price)
                / (old.quantity.abs() + signed.abs())
        };
        self.cash += cash_delta;
        if new_quantity.abs() < f64::EPSILON {
            self.positions.remove(&symbol);
        } else {
            self.positions.insert(
                symbol.clone(),
                Position {
                    quantity: new_quantity,
                    average_price,
                },
            );
        }
        let fill = Fill {
            order_id: self.next_order_id,
            idempotency_key: request.idempotency_key.clone(),
            symbol,
            side: request.side,
            quantity,
            price: quote.price,
            notional: request.notional,
            fee,
            strategy_id: request.strategy_id,
        };
        self.next_order_id += 1;
        self.fills.insert(request.idempotency_key, fill.clone());
        Ok(fill)
    }

    /// Equity after a hypothetical fill in `symbol`, using fresh quotes where
    /// available and cost basis for everything else. `cash_delta` carries the
    /// paid fee or received short proceeds of the hypothetical fill.
    fn projected_equity(
        &self,
        symbol: &str,
        traded_quantity: f64,
        traded_price: f64,
        now_epoch: i64,
        cash_delta: f64,
    ) -> f64 {
        let mut value = traded_quantity * traded_price;
        for (held_symbol, position) in &self.positions {
            if held_symbol == symbol {
                continue;
            }
            value += position.quantity * self.reference_price(held_symbol, now_epoch);
        }
        self.cash + cash_delta + value
    }

    /// Gross exposure after a hypothetical fill: the sum of absolute position
    /// values, which bounds how levered the account would become.
    fn projected_gross_exposure(&self, symbol: &str, traded_quantity: f64, traded_price: f64, now_epoch: i64) -> f64 {
        let mut gross = traded_quantity.abs() * traded_price;
        for (held_symbol, position) in &self.positions {
            if held_symbol == symbol {
                continue;
            }
            gross += position.quantity.abs() * self.reference_price(held_symbol, now_epoch);
        }
        gross
    }

    fn current_gross_exposure(&self, now_epoch: i64) -> f64 {
        self.positions
            .iter()
            .map(|(symbol, position)| position.quantity.abs() * self.reference_price(symbol, now_epoch))
            .sum()
    }

    pub fn snapshot(&self, now_epoch: i64) -> Snapshot {
        let mut market_value = 0.0;
        let mut unpriced_symbols = Vec::new();
        for (symbol, position) in &self.positions {
            match self.quotes.get(symbol) {
                Some(quote) if self.quote_is_fresh(quote, now_epoch) => {
                    market_value += position.quantity * quote.price;
                }
                _ => {
                    // A missing or stale quote must not silently mark the
                    // holding at zero; fall back to cost basis and say so.
                    market_value += position.quantity * position.average_price;
                    unpriced_symbols.push(symbol.clone());
                }
            }
        }
        Snapshot {
            mode: Mode::Paper,
            cash: self.cash,
            market_value,
            equity: self.cash + market_value,
            positions: self.positions.clone(),
            unpriced_symbols,
        }
    }
}

pub fn now_epoch() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock before epoch")
        .as_secs() as i64
}

fn error(code: &str, message: &str) -> GatewayError {
    GatewayError {
        code: code.to_owned(),
        message: message.to_owned(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn order(key: &str, side: Side) -> SubmitOrder {
        SubmitOrder {
            idempotency_key: key.into(),
            symbol: "AMD".into(),
            side,
            notional: 100.0,
            strategy_id: "human-approved".into(),
            mode: Mode::Paper,
        }
    }

    #[test]
    fn paper_order_uses_server_quote_and_is_idempotent() {
        let mut gateway = Gateway::new(RiskConfig::default());
        gateway
            .set_quote(Quote {
                symbol: "AMD".into(),
                price: 100.0,
                observed_at_epoch: 100,
            })
            .unwrap();
        gateway
            .authorize_strategy(StrategyAuthorization {
                strategy_id: "human-approved".into(),
                human_id: "owner".into(),
            })
            .unwrap();
        let first = gateway.submit_order(order("one", Side::Buy), 100).unwrap();
        let duplicate = gateway.submit_order(order("one", Side::Buy), 100).unwrap();
        assert_eq!(first.order_id, duplicate.order_id);
        assert_eq!(gateway.snapshot(100).positions["AMD"].quantity, 1.0);
    }

    #[test]
    fn risk_boundary_rejects_stale_quote_and_short() {
        let mut gateway = Gateway::new(RiskConfig::default());
        gateway
            .set_quote(Quote {
                symbol: "AMD".into(),
                price: 100.0,
                observed_at_epoch: 100,
            })
            .unwrap();
        gateway
            .authorize_strategy(StrategyAuthorization {
                strategy_id: "human-approved".into(),
                human_id: "owner".into(),
            })
            .unwrap();
        assert_eq!(
            gateway
                .submit_order(order("stale", Side::Buy), 161)
                .unwrap_err()
                .code,
            "stale_quote"
        );
        gateway
            .set_quote(Quote {
                symbol: "AMD".into(),
                price: 100.0,
                observed_at_epoch: 200,
            })
            .unwrap();
        assert_eq!(
            gateway
                .submit_order(order("short", Side::Short), 200)
                .unwrap_err()
                .code,
            "short_disabled"
        );
    }

    fn gateway_with_shorts(initial_cash: f64, max_leverage: f64) -> Gateway {
        Gateway::new(RiskConfig {
            initial_cash,
            max_leverage,
            allow_uncovered_short: true,
            ..Default::default()
        })
    }

    #[test]
    fn partial_reduction_keeps_original_cost_basis() {
        let mut gateway = gateway_with_shorts(10_000.0, 1.0);
        gateway
            .authorize_strategy(StrategyAuthorization {
                strategy_id: "human-approved".into(),
                human_id: "owner".into(),
            })
            .unwrap();
        gateway.set_quote(Quote { symbol: "AMD".into(), price: 100.0, observed_at_epoch: 100 }).unwrap();
        let mut open = order("open", Side::Buy);
        open.notional = 1000.0;
        gateway.submit_order(open, 100).unwrap();
        gateway.set_quote(Quote { symbol: "AMD".into(), price: 110.0, observed_at_epoch: 110 }).unwrap();
        let mut reduce = order("reduce", Side::Sell);
        reduce.notional = 440.0;
        gateway.submit_order(reduce, 110).unwrap();
        let snapshot = gateway.snapshot(110);
        let position = snapshot.positions.get("AMD").unwrap();
        assert!((position.quantity - 6.0).abs() < 1e-9);
        assert!((position.average_price - 100.0).abs() < 1e-9);
    }

    #[test]
    fn leverage_cap_bounds_uncovered_shorts_even_when_enabled() {
        let mut gateway = gateway_with_shorts(1_000.0, 1.0);
        gateway
            .authorize_strategy(StrategyAuthorization {
                strategy_id: "human-approved".into(),
                human_id: "owner".into(),
            })
            .unwrap();
        gateway.set_quote(Quote { symbol: "AMD".into(), price: 100.0, observed_at_epoch: 100 }).unwrap();
        // Shorting 5x the account would be wildly over-leveraged.
        let mut oversized = order("big-short", Side::Short);
        oversized.notional = 5_000.0;
        assert_eq!(
            gateway.submit_order(oversized, 100).unwrap_err().code,
            "leverage_exceeded"
        );
        // A short inside the cap is accepted.
        let mut modest = order("small-short", Side::Short);
        modest.notional = 500.0;
        assert!(gateway.submit_order(modest, 100).is_ok());
    }

    #[test]
    fn snapshot_falls_back_to_cost_for_stale_quotes_and_reports_it() {
        let mut gateway = gateway_with_shorts(10_000.0, 1.0);
        gateway
            .authorize_strategy(StrategyAuthorization {
                strategy_id: "human-approved".into(),
                human_id: "owner".into(),
            })
            .unwrap();
        gateway.set_quote(Quote { symbol: "AMD".into(), price: 100.0, observed_at_epoch: 100 }).unwrap();
        gateway.submit_order(order("hold", Side::Buy), 100).unwrap();
        // Long after the quote went stale the holding must not be marked at zero.
        let snapshot = gateway.snapshot(10_000);
        assert_eq!(snapshot.unpriced_symbols, vec!["AMD".to_string()]);
        assert!((snapshot.market_value - 100.0).abs() < 1e-9);
    }
}
