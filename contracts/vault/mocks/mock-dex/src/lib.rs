//! Test-only DEX. Accepts the two `ft_transfer_call` message shapes the vault sends:
//! Rhea DCL `{"Swap": {...}}` and Ref v1 instant swap `{"force", "actions", "skip_unwrap_near"}`.
//! Parses strictly (unknown fields fail), pays `amount * rate_num / rate_den` of the output token
//! back to the sender with a detached `ft_transfer`, like both real venues. Keeps no user balances.
use near_sdk::json_types::U128;
use near_sdk::serde_json;
use near_sdk::{env, ext_contract, near, require, AccountId, Gas, NearToken, PanicOnDefault, PromiseOrValue};

#[near(serializers = [json])]
#[serde(deny_unknown_fields)]
pub struct DclSwap {
    pub pool_ids: Vec<String>,
    pub output_token: AccountId,
    pub min_output_amount: U128,
    pub skip_unwrap_near: Option<bool>,
}

#[near(serializers = [json])]
#[serde(deny_unknown_fields)]
pub struct DclMsg {
    #[serde(rename = "Swap")]
    pub swap: DclSwap,
}

#[near(serializers = [json])]
#[serde(deny_unknown_fields)]
pub struct RefAction {
    pub pool_id: u64,
    pub token_in: AccountId,
    pub token_out: AccountId,
    pub amount_in: Option<U128>,
    pub min_amount_out: U128,
}

#[near(serializers = [json])]
#[serde(deny_unknown_fields)]
pub struct RefMsg {
    pub force: u8,
    pub actions: Vec<RefAction>,
    pub skip_unwrap_near: Option<bool>,
}

#[ext_contract(ext_ft)]
#[allow(dead_code)]
trait Ft {
    fn ft_transfer(&mut self, receiver_id: AccountId, amount: U128, memo: Option<String>);
}

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct Dex {
    rate_num: u128,
    rate_den: u128,
    last_msg: String,
    swaps: u32,
}

#[near]
impl Dex {
    #[init]
    pub fn new(rate_num: U128, rate_den: U128) -> Self {
        Self { rate_num: rate_num.0, rate_den: rate_den.0, last_msg: String::new(), swaps: 0 }
    }

    pub fn last_msg(&self) -> String {
        self.last_msg.clone()
    }

    pub fn swaps(&self) -> u32 {
        self.swaps
    }

    pub fn ft_on_transfer(&mut self, sender_id: AccountId, amount: U128, msg: String) -> PromiseOrValue<U128> {
        let token_in = env::predecessor_account_id();
        let out_token: AccountId;
        let min: u128;
        let hops: u32;
        if msg.starts_with("{\"Swap\"") {
            let m: DclMsg = serde_json::from_str(&msg).unwrap_or_else(|e| env::panic_str(&format!("dcl msg: {e}")));
            require!(!m.swap.pool_ids.is_empty(), "no pools");
            let first: Vec<&str> = m.swap.pool_ids[0].split('|').collect();
            require!(first.len() == 3 && first[..2].contains(&token_in.as_str()), "first pool lacks token_in");
            out_token = m.swap.output_token;
            min = m.swap.min_output_amount.0;
            hops = m.swap.pool_ids.len() as u32;
        } else {
            let m: RefMsg = serde_json::from_str(&msg).unwrap_or_else(|e| env::panic_str(&format!("ref msg: {e}")));
            require!(!m.actions.is_empty(), "no actions");
            require!(m.actions[0].token_in == token_in, "first action token_in");
            require!(m.actions[0].amount_in == Some(amount), "first action amount_in");
            for w in m.actions.windows(2) {
                require!(w[0].token_out == w[1].token_in, "actions chain");
                require!(w[1].amount_in.is_none(), "later amount_in");
            }
            let last = m.actions.last().unwrap();
            out_token = last.token_out.clone();
            min = last.min_amount_out.0;
            hops = m.actions.len() as u32;
        }
        let _ = hops;
        let out = amount.0 * self.rate_num / self.rate_den;
        require!(out >= min, "slippage: out below min");
        self.last_msg = msg;
        self.swaps += 1;
        ext_ft::ext(out_token)
            .with_static_gas(Gas::from_tgas(10))
            .with_attached_deposit(NearToken::from_yoctonear(1))
            .ft_transfer(sender_id, U128(out), None)
            .detach();
        PromiseOrValue::Value(U128(0))
    }
}
