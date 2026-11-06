//! Copytrade vault: one per owner. The bot key may only `swap` (buy memes with wNEAR through
//! Rhea DCL or Ref v1) and `register_token`. Only the owner moves funds out.
//! Interface: docs/interfaces/vault.md. Security argument: README.md.

use near_sdk::borsh::{BorshDeserialize, BorshSerialize};
use near_sdk::json_types::{Base58CryptoHash, U128, U64};
use near_sdk::serde_json::{self, Value};
use near_sdk::{
    env, ext_contract, near, near_bindgen, require, AccountId, Allowance, CryptoHash, Gas, GasWeight, NearToken,
    PanicOnDefault, Promise, PromiseOrValue, PublicKey,
};

mod rules;
pub use rules::*;

const GAS_VIEW: Gas = Gas::from_tgas(5);
const GAS_CB: Gas = Gas::from_tgas(10);
const GAS_NEAR_DEPOSIT: Gas = Gas::from_tgas(10);
const GAS_NEAR_WITHDRAW: Gas = Gas::from_tgas(10);
const GAS_FT_TRANSFER: Gas = Gas::from_tgas(15);
const GAS_STORAGE_DEPOSIT: Gas = Gas::from_tgas(10);
const GAS_FT_TRANSFER_CALL: Gas = Gas::from_tgas(100);
const GAS_AFTER_SEND: Gas = Gas::from_tgas(25);
/// Static gas for `migrate` in the upgrade batch; it also gets all unused gas.
const GAS_MIGRATE: Gas = Gas::from_tgas(20);
/// Most a single `register_token` may pay (interface: 0.0125 NEAR).
pub const MAX_STORAGE_DEPOSIT: NearToken = NearToken::from_micronear(12_500);
/// Default most `register_token` may pay per window (interface: 0.1 NEAR per 24 h).
pub const DEFAULT_STORAGE_BUDGET: NearToken = NearToken::from_millinear(100);
/// Storage budget window: 24 h, starting at the first payment after the last window ended.
pub const STORAGE_WINDOW_NS: u64 = 24 * 60 * 60 * 1_000_000_000;
const BUDGET_EXCEEDED: &str = "storage budget exceeded";
/// NEAR kept back from swaps so the bot key can still pay gas.
pub const SWAP_GAS_RESERVE: NearToken = NearToken::from_millinear(50);
/// Results from untrusted contracts are read with this bound.
const MAX_RESULT: usize = 512;
const BOT_METHODS: &str = "swap,register_token";
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

#[ext_contract(ext_ft)]
#[allow(dead_code)]
trait FungibleToken {
    fn ft_balance_of(&self, account_id: AccountId) -> U128;
    fn ft_transfer(&mut self, receiver_id: AccountId, amount: U128, memo: Option<String>);
    fn ft_transfer_call(&mut self, receiver_id: AccountId, amount: U128, memo: Option<String>, msg: String) -> U128;
    fn storage_balance_of(&self, account_id: AccountId) -> Option<Value>;
    fn storage_balance_bounds(&self) -> Value;
    fn storage_deposit(&mut self, account_id: Option<AccountId>, registration_only: Option<bool>) -> Value;
    fn near_deposit(&mut self);
    fn near_withdraw(&mut self, amount: U128);
}

#[near(event_json(standard = "copytrade_vault"))]
pub enum VaultEvent {
    #[event_version("1.0.0")]
    Swap { venue: Venue, token_in: AccountId, token_out: AccountId, amount_in: U128, min_out: U128 },
    #[event_version("1.0.0")]
    SwapResult {
        token_out: AccountId,
        #[serde(skip_serializing_if = "Option::is_none")]
        amount_out: Option<U128>,
        #[serde(skip_serializing_if = "Option::is_none")]
        error: Option<String>,
    },
    #[event_version("1.0.0")]
    TokenRegistered { token_id: AccountId, deposit: U128 },
    #[event_version("1.0.0")]
    Paused,
    #[event_version("1.0.0")]
    Unpaused,
    #[event_version("1.0.0")]
    BotKeySet { public_key: PublicKey, allowance: U128 },
    #[event_version("1.0.0")]
    BotKeyRemoved,
    #[event_version("1.0.0")]
    Withdraw { token_id: String, amount: U128 },
}

#[near(serializers = [json])]
#[derive(Debug, PartialEq)]
pub struct VaultState {
    pub owner_id: AccountId,
    pub paused: bool,
    pub bot_key: Option<String>,
    pub version: String,
    pub storage_budget: StorageBudgetView,
}

/// `spent` counts only the current window: 0 once `window_start` + 24 h has passed.
#[near(serializers = [json])]
#[derive(Debug, PartialEq)]
pub struct StorageBudgetView {
    pub per_day: U128,
    pub spent: U128,
    pub window_start: U64,
}

#[near(serializers = [borsh])]
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct StorageBudget {
    per_day: u128,
    spent: u128,
    /// Block timestamp (ns) of the window's first payment. 0 = none yet.
    window_start: u64,
}

impl StorageBudget {
    fn new() -> Self {
        Self { per_day: DEFAULT_STORAGE_BUDGET.as_yoctonear(), spent: 0, window_start: 0 }
    }

    fn expired(&self, now: u64) -> bool {
        now >= self.window_start.saturating_add(STORAGE_WINDOW_NS)
    }

    fn spent_now(&self, now: u64) -> u128 {
        if self.expired(now) {
            0
        } else {
            self.spent
        }
    }

    /// Book `amount` in the current window, opening a new one if the last ended. Panics over budget.
    fn spend(&mut self, amount: u128, now: u64) {
        if self.expired(now) {
            self.spent = 0;
            self.window_start = now;
        }
        let total = self.spent.checked_add(amount).unwrap_or_else(|| env::panic_str(BUDGET_EXCEEDED));
        require!(total <= self.per_day, BUDGET_EXCEEDED);
        self.spent = total;
    }

    /// A deposit came back (storage_deposit failed). Only within the window it was booked in.
    fn refund(&mut self, amount: u128, booked_in: u64) {
        if booked_in == self.window_start {
            self.spent = self.spent.saturating_sub(amount);
        }
    }
}

/// State layout 2. Frozen: never change a stored layout. A new layout is a new struct and a new
/// `VersionedState` variant, plus a conversion from every older one (README "Upgrades").
#[near(serializers = [borsh])]
#[derive(Debug, Clone, PartialEq)]
pub struct StateV2 {
    owner_id: AccountId,
    paused: bool,
    bot_key: Option<PublicKey>,
    storage_budget: StorageBudget,
}

/// What is stored under `STATE`: a Borsh enum tag, then the layout. Variants are only appended,
/// never changed, removed or reordered, so every version loads every older state.
#[near(serializers = [borsh])]
#[derive(Debug, Clone, PartialEq)]
pub enum VersionedState {
    V2(StateV2),
}

impl VersionedState {
    /// The current layout, converted from whichever version was stored.
    fn into_current(self) -> StateV2 {
        match self {
            VersionedState::V2(s) => s,
        }
    }
}

/// Stored as `VersionedState` (manual Borsh below); in memory always the current layout.
#[near_bindgen(contract_state)]
#[derive(PanicOnDefault)]
pub struct Vault {
    s: StateV2,
}

impl BorshSerialize for Vault {
    fn serialize<W: std::io::Write>(&self, w: &mut W) -> std::io::Result<()> {
        // Same bytes as VersionedState::V2(self.s), without the clone.
        BorshSerialize::serialize(&0u8, w)?;
        BorshSerialize::serialize(&self.s, w)
    }
}

impl BorshDeserialize for Vault {
    fn deserialize_reader<R: std::io::Read>(r: &mut R) -> std::io::Result<Self> {
        Ok(Self { s: VersionedState::deserialize_reader(r)?.into_current() })
    }
}

fn me() -> AccountId {
    env::current_account_id()
}

fn wrap() -> AccountId {
    WRAP.parse().unwrap()
}

/// Bytes of promise result `i`, or None if it failed or is too long.
fn result_bytes(i: u64) -> Option<Vec<u8>> {
    env::promise_result_checked(i, MAX_RESULT).ok()
}

fn result_u128(i: u64) -> Option<u128> {
    let b = result_bytes(i)?;
    serde_json::from_slice::<U128>(&b).ok().map(|v| v.0)
}

/// NEP-145 `storage_balance_of`: registered unless the call failed or returned null.
fn result_registered(i: u64) -> bool {
    match result_bytes(i).and_then(|b| serde_json::from_slice::<Value>(&b).ok()) {
        Some(Value::Null) | None => false,
        Some(_) => true,
    }
}

/// NEAR the account may spend: balance minus what storage staking locks, minus `extra`.
fn spendable(extra: NearToken) -> u128 {
    let locked = env::storage_byte_cost().as_yoctonear() * env::storage_usage() as u128;
    env::account_balance().as_yoctonear().saturating_sub(locked).saturating_sub(extra.as_yoctonear())
}

fn swap_failed(token_out: &AccountId, error: String) -> PromiseOrValue<bool> {
    VaultEvent::SwapResult { token_out: token_out.clone(), amount_out: None, error: Some(error) }.emit();
    PromiseOrValue::Value(false)
}

#[near]
impl Vault {
    /// Only the owner can initialise, so nobody can create a vault that names someone else's
    /// account, and a front-run `new` can't steal an un-initialised vault (owner would be the caller).
    #[init]
    pub fn new(owner_id: AccountId) -> Self {
        require!(env::predecessor_account_id() == owner_id, "new: caller must be owner_id");
        require!(owner_id != me(), "new: owner can't be the vault");
        Self { s: StateV2 { owner_id, paused: false, bot_key: None, storage_budget: StorageBudget::new() } }
    }

    // ---------------- bot ----------------

    /// Bot buy: wNEAR in, meme out, through a constant venue. See rules::validate.
    pub fn swap(&mut self, args: SwapArgs) -> Promise {
        self.assert_bot();
        validate(&args, Side::Buy).unwrap_or_else(|e| env::panic_str(&e));
        self.start_swap(args, Side::Buy)
    }

    /// Bot: NEP-145 registration of the vault on `token_id`, at most 0.0125 NEAR, within the
    /// storage budget. Fails here when the window's budget is used up, else in the callback.
    pub fn register_token(&mut self, token_id: AccountId) -> Promise {
        self.assert_bot();
        let b = &self.s.storage_budget;
        require!(b.spent_now(env::block_timestamp()) < b.per_day, BUDGET_EXCEEDED);
        ext_ft::ext(token_id.clone())
            .with_static_gas(GAS_VIEW)
            .storage_balance_of(me())
            .and(ext_ft::ext(token_id.clone()).with_static_gas(GAS_VIEW).storage_balance_bounds())
            .then(Self::ext(me()).with_static_gas(Gas::from_tgas(25)).on_register_checked(token_id))
    }

    // ---------------- owner ----------------

    /// Owner sell: any token in, wNEAR out. Works while paused.
    #[payable]
    pub fn sell(&mut self, args: SwapArgs) -> Promise {
        self.assert_owner();
        validate(&args, Side::Sell).unwrap_or_else(|e| env::panic_str(&e));
        self.start_swap(args, Side::Sell)
    }

    /// Send NEAR to the owner, unwrapping wNEAR if NEAR alone is short. `None` = everything
    /// except the storage stake.
    #[payable]
    pub fn withdraw_near(&mut self, amount: Option<U128>) -> Promise {
        self.assert_owner();
        if let Some(a) = amount {
            require!(a.0 > 0, "amount must be > 0");
        }
        ext_ft::ext(wrap())
            .with_static_gas(GAS_VIEW)
            .ft_balance_of(me())
            .then(Self::ext(me()).with_static_gas(Gas::from_tgas(30)).on_withdraw_near_balance(amount))
    }

    /// `ft_transfer` to the owner. `None` = full balance.
    #[payable]
    pub fn withdraw_token(&mut self, token_id: AccountId, amount: Option<U128>) -> Promise {
        self.assert_owner();
        match amount {
            Some(a) => {
                require!(a.0 > 0, "amount must be > 0");
                self.send_token(token_id, a.0)
            }
            None => ext_ft::ext(token_id.clone())
                .with_static_gas(GAS_VIEW)
                .ft_balance_of(me())
                .then(Self::ext(me()).with_static_gas(Gas::from_tgas(35)).on_withdraw_token_balance(token_id)),
        }
    }

    #[payable]
    pub fn pause(&mut self) {
        self.assert_owner();
        self.s.paused = true;
        VaultEvent::Paused.emit();
    }

    #[payable]
    pub fn unpause(&mut self) {
        self.assert_owner();
        self.s.paused = false;
        VaultEvent::Unpaused.emit();
    }

    /// Replace the bot key. The old key loses contract power at once (state cleared now) and is
    /// deleted in the same batch that adds the new one. New key: FC key on this account,
    /// methods swap + register_token only.
    #[payable]
    pub fn set_bot_key(&mut self, public_key: PublicKey, allowance: U128) -> Promise {
        self.assert_owner();
        let allow = Allowance::limited(NearToken::from_yoctonear(allowance.0))
            .unwrap_or_else(|| env::panic_str("allowance must be > 0"));
        let mut p = Promise::new(me());
        if let Some(old) = self.s.bot_key.take() {
            p = p.delete_key(old);
        }
        p.add_access_key_allowance(public_key.clone(), allow, me(), BOT_METHODS)
            .then(Self::ext(me()).with_static_gas(GAS_CB).on_bot_key_set(public_key, allowance))
    }

    /// Delete the bot key. Contract power ends at once, even before the key delete lands.
    #[payable]
    pub fn remove_bot_key(&mut self) -> Promise {
        self.assert_owner();
        let old = self.s.bot_key.take().unwrap_or_else(|| env::panic_str("no bot key"));
        VaultEvent::BotKeyRemoved.emit();
        Promise::new(me()).delete_key(old)
    }

    /// Most `register_token` may pay per 24 h window. 0 = no registrations. Takes effect at once,
    /// for the current window too.
    #[payable]
    pub fn set_storage_budget(&mut self, per_day: U128) {
        self.assert_owner();
        self.s.storage_budget.per_day = per_day.0;
    }

    /// Switch this account to the global contract with `code_hash`, then run the new code's `migrate`
    /// in the same receipt. If the new code has no `migrate` or can't load this state, the whole
    /// receipt reverts and the old code stays, so a bad upgrade can't lock the funds.
    #[payable]
    pub fn owner_upgrade(&mut self, code_hash: Base58CryptoHash) -> Promise {
        self.assert_owner();
        let h: CryptoHash = code_hash.into();
        Promise::new(me()).use_global_contract(h).function_call_weight(
            "migrate".to_string(),
            b"{}".to_vec(),
            NearToken::from_yoctonear(0),
            GAS_MIGRATE,
            GasWeight(1),
        )
    }

    /// Load the stored state in any older layout and store it in the current one. Called only by
    /// `owner_upgrade`'s batch on this account (the bot key can't name this method).
    #[private]
    #[init(ignore_state)]
    pub fn migrate() -> Self {
        env::state_read::<Self>().unwrap_or_else(|| env::panic_str("migrate: no state"))
    }

    pub fn get_state(&self) -> VaultState {
        VaultState {
            owner_id: self.s.owner_id.clone(),
            paused: self.s.paused,
            bot_key: self.s.bot_key.as_ref().map(String::from),
            version: VERSION.to_string(),
            storage_budget: StorageBudgetView {
                per_day: U128(self.s.storage_budget.per_day),
                spent: U128(self.s.storage_budget.spent_now(env::block_timestamp())),
                window_start: U64(self.s.storage_budget.window_start),
            },
        }
    }

    // ---------------- callbacks (predecessor must be this account) ----------------

    /// Results: [0] out.storage_balance_of, [1] out.ft_balance_of,
    /// buy only: [2] wrap.storage_balance_of, [3] wrap.ft_balance_of.
    #[private]
    pub fn on_swap_checked(&mut self, args: SwapArgs, buy: bool) -> PromiseOrValue<bool> {
        let out = args.token_out().clone();
        if buy && self.s.paused {
            return swap_failed(&out, "paused".into());
        }
        if !result_registered(0) {
            return swap_failed(&out, format!("vault not registered on {out}"));
        }
        let Some(out_before) = result_u128(1) else {
            return swap_failed(&out, format!("ft_balance_of failed on {out}"));
        };
        let amount = args.amount_in.0;
        let mut wrap_first: Option<Promise> = None;
        if buy {
            if !result_registered(2) {
                return swap_failed(&out, format!("vault not registered on {WRAP}"));
            }
            let Some(wnear) = result_u128(3) else {
                return swap_failed(&out, format!("ft_balance_of failed on {WRAP}"));
            };
            let short = amount.saturating_sub(wnear);
            if short > 0 {
                if short > spendable(SWAP_GAS_RESERVE) {
                    return swap_failed(&out, "amount_in above balance".into());
                }
                wrap_first = Some(
                    ext_ft::ext(wrap())
                        .with_static_gas(GAS_NEAR_DEPOSIT)
                        .with_attached_deposit(NearToken::from_yoctonear(short))
                        .near_deposit(),
                );
            }
        }
        // Constant receiver: venue.receiver() is DCL or REF_V1, nothing else.
        let send = ext_ft::ext(args.token_in().clone())
            .with_static_gas(GAS_FT_TRANSFER_CALL)
            .with_unused_gas_weight(1)
            .with_attached_deposit(NearToken::from_yoctonear(1))
            .ft_transfer_call(args.venue.receiver(), args.amount_in, None, build_msg(&args));
        // If near_deposit fails its NEAR comes back and the transfer fails on balance: input stays.
        let send = match wrap_first {
            Some(w) => w.then(send),
            None => send,
        };
        PromiseOrValue::Promise(
            send.then(Self::ext(me()).with_static_gas(GAS_AFTER_SEND).on_swap_sent(out, U128(out_before))),
        )
    }

    /// Result [0]: amount of input the venue used (ft_transfer_call). 0 = swap failed, all input
    /// refunded. Unreadable = the transfer or its resolve failed: where the input is isn't known.
    #[private]
    pub fn on_swap_sent(&mut self, token_out: AccountId, out_before: U128) -> PromiseOrValue<bool> {
        match result_u128(0) {
            Some(used) if used > 0 => PromiseOrValue::Promise(
                ext_ft::ext(token_out.clone())
                    .with_static_gas(GAS_VIEW)
                    .ft_balance_of(me())
                    .then(Self::ext(me()).with_static_gas(Gas::from_tgas(5)).on_swap_done(token_out, out_before)),
            ),
            Some(_) => swap_failed(&token_out, "venue used 0".into()),
            None => swap_failed(&token_out, "ft_transfer_call failed".into()),
        }
    }

    /// Output = balance now minus balance before. Best effort: a concurrent swap or a late
    /// output transfer skews it. The worker confirms from receipts.
    #[private]
    pub fn on_swap_done(&mut self, token_out: AccountId, out_before: U128) -> bool {
        let Some(after) = result_u128(0) else {
            VaultEvent::SwapResult { token_out, amount_out: None, error: Some("output balance unknown".into()) }.emit();
            return false;
        };
        let amount_out = U128(after.saturating_sub(out_before.0));
        VaultEvent::SwapResult { token_out, amount_out: Some(amount_out), error: None }.emit();
        true
    }

    /// Results: [0] storage_balance_of(vault), [1] storage_balance_bounds.
    #[private]
    pub fn on_register_checked(&mut self, token_id: AccountId) -> PromiseOrValue<bool> {
        require!(!self.s.paused, "paused");
        if result_registered(0) {
            return PromiseOrValue::Value(false);
        }
        let min = result_bytes(1)
            .and_then(|b| serde_json::from_slice::<Value>(&b).ok())
            .and_then(|v| serde_json::from_value::<U128>(v["min"].clone()).ok())
            .unwrap_or_else(|| env::panic_str("storage_balance_bounds unreadable"))
            .0;
        require!(min <= MAX_STORAGE_DEPOSIT.as_yoctonear(), "storage deposit above 0.0125 NEAR");
        require!(min <= spendable(SWAP_GAS_RESERVE), "storage deposit above balance");
        let now = env::block_timestamp();
        self.s.storage_budget.spend(min, now);
        let window = self.s.storage_budget.window_start;
        PromiseOrValue::Promise(
            ext_ft::ext(token_id.clone())
                .with_static_gas(GAS_STORAGE_DEPOSIT)
                .with_attached_deposit(NearToken::from_yoctonear(min))
                .storage_deposit(Some(me()), Some(true))
                .then(Self::ext(me()).with_static_gas(Gas::from_tgas(5)).on_registered(token_id, U128(min), U64(window))),
        )
    }

    #[private]
    pub fn on_registered(&mut self, token_id: AccountId, deposit: U128, window: U64) -> bool {
        if result_bytes(0).is_none() {
            // A failed storage_deposit refunds its deposit to us.
            self.s.storage_budget.refund(deposit.0, window.0);
            return false;
        }
        VaultEvent::TokenRegistered { token_id, deposit }.emit();
        true
    }

    /// Result [0]: wrap.ft_balance_of(vault).
    #[private]
    pub fn on_withdraw_near_balance(&mut self, amount: Option<U128>) -> PromiseOrValue<bool> {
        let wnear = result_u128(0).unwrap_or(0);
        let avail = spendable(NearToken::from_yoctonear(0));
        let want = amount.map(|a| a.0).unwrap_or(avail + wnear);
        require!(want > 0, "nothing to withdraw");
        if want <= avail {
            return PromiseOrValue::Value(self.send_near(want));
        }
        let short = want - avail;
        require!(short <= wnear, "amount above NEAR + wNEAR balance");
        PromiseOrValue::Promise(
            ext_ft::ext(wrap())
                .with_static_gas(GAS_NEAR_WITHDRAW)
                .with_attached_deposit(NearToken::from_yoctonear(1))
                .near_withdraw(U128(short))
                .then(Self::ext(me()).with_static_gas(GAS_CB).on_unwrapped(U128(want))),
        )
    }

    #[private]
    pub fn on_unwrapped(&mut self, want: U128) -> bool {
        require!(result_bytes(0).is_some(), "near_withdraw failed");
        // The 1 yocto attached to near_withdraw may leave us 1 short of `want`.
        let send = want.0.min(spendable(NearToken::from_yoctonear(0)));
        self.send_near(send)
    }

    #[private]
    pub fn on_withdraw_token_balance(&mut self, token_id: AccountId) -> PromiseOrValue<bool> {
        let bal = result_u128(0).unwrap_or_else(|| env::panic_str("ft_balance_of failed"));
        require!(bal > 0, "nothing to withdraw");
        PromiseOrValue::Promise(self.send_token(token_id, bal))
    }

    #[private]
    pub fn on_token_sent(&mut self, token_id: AccountId, amount: U128) -> bool {
        if result_bytes(0).is_none() {
            return false;
        }
        VaultEvent::Withdraw { token_id: token_id.to_string(), amount }.emit();
        true
    }

    #[private]
    pub fn on_bot_key_set(&mut self, public_key: PublicKey, allowance: U128) -> bool {
        if result_bytes(0).is_none() {
            return false;
        }
        self.s.bot_key = Some(public_key.clone());
        VaultEvent::BotKeySet { public_key, allowance }.emit();
        true
    }
}

impl Vault {
    /// Bot = a tx signed by the stored bot key on this account. FC key permissions already limit
    /// it to swap/register_token with zero deposit; this also blocks any stale key.
    fn assert_bot(&self) {
        require!(env::predecessor_account_id() == me(), "only the bot key");
        require!(self.s.bot_key.as_ref() == Some(&env::signer_account_pk()), "only the bot key");
        require!(!self.s.paused, "paused");
    }

    /// Owner = predecessor is owner_id and exactly 1 yocto attached, so a full-access key signed it.
    fn assert_owner(&self) {
        require!(env::predecessor_account_id() == self.s.owner_id, "only the owner");
        require!(env::attached_deposit() == NearToken::from_yoctonear(1), "attach exactly 1 yoctoNEAR");
    }

    fn start_swap(&self, args: SwapArgs, side: Side) -> Promise {
        let out = args.token_out().clone();
        let mut checks = ext_ft::ext(out.clone())
            .with_static_gas(GAS_VIEW)
            .storage_balance_of(me())
            .and(ext_ft::ext(out.clone()).with_static_gas(GAS_VIEW).ft_balance_of(me()));
        let buy = side == Side::Buy;
        if buy {
            checks = checks
                .and(ext_ft::ext(wrap()).with_static_gas(GAS_VIEW).storage_balance_of(me()))
                .and(ext_ft::ext(wrap()).with_static_gas(GAS_VIEW).ft_balance_of(me()));
        }
        VaultEvent::Swap {
            venue: args.venue,
            token_in: args.token_in().clone(),
            token_out: out,
            amount_in: args.amount_in,
            min_out: args.min_out,
        }
        .emit();
        checks.then(
            Self::ext(me()).with_static_gas(Gas::from_tgas(150)).with_unused_gas_weight(1).on_swap_checked(args, buy),
        )
    }

    fn send_near(&self, amount: u128) -> bool {
        Promise::new(self.s.owner_id.clone()).transfer(NearToken::from_yoctonear(amount)).detach();
        VaultEvent::Withdraw { token_id: "near".into(), amount: U128(amount) }.emit();
        true
    }

    fn send_token(&self, token_id: AccountId, amount: u128) -> Promise {
        ext_ft::ext(token_id.clone())
            .with_static_gas(GAS_FT_TRANSFER)
            .with_attached_deposit(NearToken::from_yoctonear(1))
            .ft_transfer(self.s.owner_id.clone(), U128(amount), None)
            .then(Self::ext(me()).with_static_gas(Gas::from_tgas(5)).on_token_sent(token_id, U128(amount)))
    }
}

#[cfg(test)]
mod tests;
