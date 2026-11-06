//! Pure swap rules and DEX message builders. No env access: unit-testable.

use near_sdk::json_types::U128;
use near_sdk::serde_json::{self, json};
use near_sdk::{near, AccountId};

pub const WRAP: &str = "wrap.near";
pub const RHEA: &str = "token.rhealab.near";
pub const DCL: &str = "dclv2.ref-labs.near";
pub const REF_V1: &str = "v2.ref-finance.near";
pub const MAX_HOPS: usize = 3;

include!(concat!(env!("OUT_DIR"), "/denylist.rs"));

#[near(serializers = [json])]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Venue {
    Dcl,
    RefV1,
}

impl Venue {
    /// The only contract that ever receives `ft_transfer_call` from the vault.
    pub fn receiver(&self) -> AccountId {
        match self {
            Venue::Dcl => DCL,
            Venue::RefV1 => REF_V1,
        }
        .parse()
        .unwrap()
    }
}

#[near(serializers = [json])]
#[derive(Clone, Debug, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Hop {
    pub pool_id: String,
    pub token_in: AccountId,
    pub token_out: AccountId,
}

#[near(serializers = [json])]
#[derive(Clone, Debug, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct SwapArgs {
    pub venue: Venue,
    pub hops: Vec<Hop>,
    pub amount_in: U128,
    pub min_out: U128,
}

impl SwapArgs {
    pub fn token_in(&self) -> &AccountId {
        &self.hops[0].token_in
    }
    pub fn token_out(&self) -> &AccountId {
        &self.hops[self.hops.len() - 1].token_out
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Side {
    /// Bot `swap`: wNEAR in, meme out.
    Buy,
    /// Owner `sell`: any token in, wNEAR out.
    Sell,
}

pub fn is_denylisted(token: &str) -> bool {
    DENY_EXACT.contains(&token) || DENY_SUFFIX.iter().any(|s| token.ends_with(s))
}

fn is_allowed_intermediate(token: &str) -> bool {
    token == WRAP || token == RHEA
}

/// DCL pool id "<token_x>|<token_y>|<fee>". Must name exactly the hop's two tokens.
fn check_dcl_pool(hop: &Hop) -> Result<(), String> {
    let parts: Vec<&str> = hop.pool_id.split('|').collect();
    if parts.len() != 3 {
        return Err(format!("dcl pool_id {}: want x|y|fee", hop.pool_id));
    }
    if parts[2].is_empty() || !parts[2].bytes().all(|b| b.is_ascii_digit()) || parts[2].len() > 6 {
        return Err(format!("dcl pool_id {}: bad fee", hop.pool_id));
    }
    let (a, b) = (parts[0], parts[1]);
    let (i, o) = (hop.token_in.as_str(), hop.token_out.as_str());
    if !((a == i && b == o) || (a == o && b == i)) {
        return Err(format!("dcl pool_id {}: tokens don't match hop", hop.pool_id));
    }
    Ok(())
}

/// Ref v1 pool id: decimal u64, no sign, no leading zeros.
fn check_ref_pool(hop: &Hop) -> Result<u64, String> {
    let p = &hop.pool_id;
    let ok =
        !p.is_empty() && p.len() <= 20 && p.bytes().all(|b| b.is_ascii_digit()) && (p == "0" || !p.starts_with('0'));
    if !ok {
        return Err(format!("ref_v1 pool_id {p}: want a number"));
    }
    p.parse::<u64>().map_err(|_| format!("ref_v1 pool_id {p}: too big"))
}

/// Every rule that does not need chain state. Balance and registration are checked later, async.
pub fn validate(args: &SwapArgs, side: Side) -> Result<(), String> {
    if args.min_out.0 == 0 {
        return Err("min_out must be > 0".into());
    }
    if args.amount_in.0 == 0 {
        return Err("amount_in must be > 0".into());
    }
    let n = args.hops.len();
    if n == 0 || n > MAX_HOPS {
        return Err(format!("hops: want 1..{MAX_HOPS}, got {n}"));
    }
    for w in args.hops.windows(2) {
        if w[0].token_out != w[1].token_in {
            return Err("hops don't chain".into());
        }
    }
    let mut path: Vec<&AccountId> = vec![args.token_in()];
    path.extend(args.hops.iter().map(|h| &h.token_out));
    for (i, t) in path.iter().enumerate() {
        if path[..i].contains(t) {
            return Err(format!("token repeats: {t}"));
        }
    }
    for t in &path[1..n] {
        if !is_allowed_intermediate(t.as_str()) {
            return Err(format!("intermediate token not allowed: {t}"));
        }
    }
    match side {
        Side::Buy => {
            if args.token_in().as_str() != WRAP {
                return Err(format!("buy must start with {WRAP}"));
            }
            if is_denylisted(args.token_out().as_str()) {
                return Err(format!("output on non-meme denylist: {}", args.token_out()));
            }
        }
        Side::Sell => {
            if args.token_out().as_str() != WRAP {
                return Err(format!("sell must end with {WRAP}"));
            }
        }
    }
    for h in &args.hops {
        match args.venue {
            Venue::Dcl => check_dcl_pool(h)?,
            Venue::RefV1 => {
                check_ref_pool(h)?;
            }
        }
    }
    Ok(())
}

/// The `msg` for `ft_transfer_call` to the venue. Built here only, never taken from callers.
/// Neither shape has a recipient field: both venues send the output back to the sender (the vault).
pub fn build_msg(args: &SwapArgs) -> String {
    let min_out = args.min_out.0.to_string();
    let v = match args.venue {
        Venue::Dcl => json!({
            "Swap": {
                "pool_ids": args.hops.iter().map(|h| h.pool_id.clone()).collect::<Vec<_>>(),
                "output_token": args.token_out(),
                "min_output_amount": min_out,
                "skip_unwrap_near": true,
            }
        }),
        Venue::RefV1 => {
            let last = args.hops.len() - 1;
            let actions: Vec<_> = args
                .hops
                .iter()
                .enumerate()
                .map(|(i, h)| {
                    let mut a = json!({
                        "pool_id": check_ref_pool(h).expect("validated"),
                        "token_in": h.token_in,
                        "token_out": h.token_out,
                        "min_amount_out": if i == last { min_out.clone() } else { "0".to_string() },
                    });
                    if i == 0 {
                        a["amount_in"] = json!(args.amount_in.0.to_string());
                    }
                    a
                })
                .collect();
            json!({ "force": 0, "actions": actions, "skip_unwrap_near": true })
        }
    };
    serde_json::to_string(&v).unwrap()
}
