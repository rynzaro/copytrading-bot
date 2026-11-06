//! Test-only NEP-141/145 token that also behaves like wrap.near (near_deposit / near_withdraw).
//! `mint` is open: never deploy outside the sandbox.
use near_sdk::json_types::U128;
use near_sdk::serde_json::{json, Value};
use near_sdk::store::LookupMap;
use near_sdk::{env, ext_contract, near, require, AccountId, Gas, NearToken, PanicOnDefault, Promise, PromiseOrValue};

const STORAGE: u128 = 1_250_000_000_000_000_000_000; // 0.00125 NEAR, like wrap.near

#[ext_contract(ext_receiver)]
#[allow(dead_code)]
trait Receiver {
    fn ft_on_transfer(&mut self, sender_id: AccountId, amount: U128, msg: String) -> PromiseOrValue<U128>;
}

#[ext_contract(ext_self)]
#[allow(dead_code)]
trait SelfCb {
    fn ft_resolve_transfer(&mut self, sender_id: AccountId, receiver_id: AccountId, amount: U128) -> U128;
}

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct Token {
    balances: LookupMap<AccountId, u128>,
}

#[near]
impl Token {
    #[init]
    pub fn new() -> Self {
        Self { balances: LookupMap::new(b"b") }
    }

    fn bal(&self, a: &AccountId) -> u128 {
        *self.balances.get(a).unwrap_or_else(|| env::panic_str(&format!("{a} not registered")))
    }

    fn move_tokens(&mut self, from: &AccountId, to: &AccountId, amount: u128) {
        require!(amount > 0, "amount 0");
        let f = self.bal(from);
        require!(f >= amount, "not enough balance");
        let t = self.bal(to);
        self.balances.insert(from.clone(), f - amount);
        self.balances.insert(to.clone(), t + amount);
    }

    /// Test helper: credit tokens (registers if needed).
    pub fn mint(&mut self, account_id: AccountId, amount: U128) {
        let b = self.balances.get(&account_id).copied().unwrap_or(0);
        self.balances.insert(account_id, b + amount.0);
    }

    /// For the worker's route reader (e2e): 24 decimals, like wNEAR.
    pub fn ft_metadata(&self) -> Value {
        json!({"spec": "ft-1.0.0", "name": "Mock", "symbol": "MOCK", "decimals": 24})
    }

    pub fn ft_balance_of(&self, account_id: AccountId) -> U128 {
        U128(self.balances.get(&account_id).copied().unwrap_or(0))
    }

    #[payable]
    pub fn ft_transfer(&mut self, receiver_id: AccountId, amount: U128, memo: Option<String>) {
        let _ = memo;
        require!(env::attached_deposit() == NearToken::from_yoctonear(1), "1 yocto");
        self.move_tokens(&env::predecessor_account_id(), &receiver_id, amount.0);
    }

    #[payable]
    pub fn ft_transfer_call(
        &mut self,
        receiver_id: AccountId,
        amount: U128,
        memo: Option<String>,
        msg: String,
    ) -> Promise {
        let _ = memo;
        require!(env::attached_deposit() == NearToken::from_yoctonear(1), "1 yocto");
        let sender = env::predecessor_account_id();
        self.move_tokens(&sender, &receiver_id, amount.0);
        ext_receiver::ext(receiver_id.clone())
            .with_static_gas(Gas::from_tgas(30))
            .with_unused_gas_weight(1)
            .ft_on_transfer(sender.clone(), amount, msg)
            .then(ext_self::ext(env::current_account_id()).with_static_gas(Gas::from_tgas(10)).ft_resolve_transfer(
                sender,
                receiver_id,
                amount,
            ))
    }

    #[private]
    pub fn ft_resolve_transfer(&mut self, sender_id: AccountId, receiver_id: AccountId, amount: U128) -> U128 {
        let unused = match env::promise_result_checked(0, 100) {
            Ok(b) => near_sdk::serde_json::from_slice::<U128>(&b).map(|u| u.0.min(amount.0)).unwrap_or(amount.0),
            Err(_) => amount.0,
        };
        if unused > 0 {
            let r = self.bal(&receiver_id).min(unused);
            if r > 0 {
                self.move_tokens(&receiver_id, &sender_id, r);
            }
            return U128(amount.0 - r);
        }
        amount
    }

    #[payable]
    pub fn storage_deposit(&mut self, account_id: Option<AccountId>, registration_only: Option<bool>) -> Value {
        let _ = registration_only;
        let a = account_id.unwrap_or_else(env::predecessor_account_id);
        let dep = env::attached_deposit().as_yoctonear();
        if self.balances.contains_key(&a) {
            if dep > 0 {
                Promise::new(env::predecessor_account_id()).transfer(NearToken::from_yoctonear(dep)).detach();
            }
        } else {
            require!(dep >= STORAGE, "deposit below storage min");
            self.balances.insert(a, 0);
            if dep > STORAGE {
                Promise::new(env::predecessor_account_id()).transfer(NearToken::from_yoctonear(dep - STORAGE)).detach();
            }
        }
        json!({"total": STORAGE.to_string(), "available": "0"})
    }

    pub fn storage_balance_of(&self, account_id: AccountId) -> Option<Value> {
        self.balances.contains_key(&account_id).then(|| json!({"total": STORAGE.to_string(), "available": "0"}))
    }

    pub fn storage_balance_bounds(&self) -> Value {
        json!({"min": STORAGE.to_string(), "max": STORAGE.to_string()})
    }

    /// wrap.near: registers on first deposit, taking the storage cost out of the amount.
    #[payable]
    pub fn near_deposit(&mut self) {
        let mut amount = env::attached_deposit().as_yoctonear();
        let a = env::predecessor_account_id();
        if !self.balances.contains_key(&a) {
            require!(amount >= STORAGE, "ERR_DEPOSIT_TOO_SMALL");
            amount -= STORAGE;
            self.balances.insert(a.clone(), 0);
        }
        let b = self.bal(&a);
        self.balances.insert(a, b + amount);
    }

    #[payable]
    pub fn near_withdraw(&mut self, amount: U128) -> Promise {
        require!(env::attached_deposit() == NearToken::from_yoctonear(1), "1 yocto");
        let a = env::predecessor_account_id();
        let b = self.bal(&a);
        require!(b >= amount.0, "not enough wNEAR");
        self.balances.insert(a.clone(), b - amount.0);
        Promise::new(a).transfer(NearToken::from_yoctonear(amount.0))
    }
}
