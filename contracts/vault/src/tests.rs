#![allow(unused_must_use)]
use super::*;
use near_sdk::test_utils::{get_logs, VMContextBuilder};
use near_sdk::{testing_env, NearToken, PromiseResult};

const VAULT: &str = "bot.alice.near";
const OWNER: &str = "alice.near";
const MEME: &str = "meme.tkn.near";
const BOT_PK: &str = "ed25519:6E8sCci9badyRkXb3JoRpBj5p8C6Tw41ELDZoiihKEtp";
const OTHER_PK: &str = "ed25519:DcA2MzgpJbrUATQLLceocVckhhAqrkingax4oJ9kZ847";

fn acc(s: &str) -> AccountId {
    s.parse().unwrap()
}

fn pk(s: &str) -> PublicKey {
    s.parse().unwrap()
}

fn ctx(predecessor: &str, signer_pk: &str, deposit: u128) {
    testing_env!(VMContextBuilder::new()
        .current_account_id(acc(VAULT))
        .predecessor_account_id(acc(predecessor))
        .signer_account_id(acc(predecessor))
        .signer_account_pk(pk(signer_pk))
        .account_balance(NearToken::from_near(20))
        .attached_deposit(NearToken::from_yoctonear(deposit))
        .build());
}

fn vault() -> Vault {
    ctx(OWNER, OTHER_PK, 0);
    let mut v = Vault::new(acc(OWNER));
    v.s.bot_key = Some(pk(BOT_PK));
    v
}

fn hop(pool: &str, i: &str, o: &str) -> Hop {
    Hop { pool_id: pool.into(), token_in: acc(i), token_out: acc(o) }
}

fn buy_dcl() -> SwapArgs {
    SwapArgs {
        venue: Venue::Dcl,
        hops: vec![hop(&format!("{MEME}|{WRAP}|2000"), WRAP, MEME)],
        amount_in: U128(10),
        min_out: U128(1),
    }
}

fn buy_ref_via_rhea() -> SwapArgs {
    SwapArgs {
        venue: Venue::RefV1,
        hops: vec![hop("12", WRAP, RHEA), hop("4021", RHEA, MEME)],
        amount_in: U128(10),
        min_out: U128(7),
    }
}

fn err(args: &SwapArgs, side: Side) -> String {
    validate(args, side).unwrap_err()
}

// ---------------- rules ----------------

#[test]
fn valid_buys_pass() {
    validate(&buy_dcl(), Side::Buy).unwrap();
    validate(&buy_ref_via_rhea(), Side::Buy).unwrap();
    let mut a = buy_dcl();
    a.hops = vec![hop(&format!("{WRAP}|{RHEA}|100"), WRAP, RHEA), hop(&format!("{RHEA}|{MEME}|2000"), RHEA, MEME)];
    validate(&a, Side::Buy).unwrap();
}

#[test]
fn min_out_zero_rejected() {
    let mut a = buy_dcl();
    a.min_out = U128(0);
    assert!(err(&a, Side::Buy).contains("min_out"));
}

#[test]
fn amount_zero_rejected() {
    let mut a = buy_dcl();
    a.amount_in = U128(0);
    assert!(err(&a, Side::Buy).contains("amount_in"));
}

#[test]
fn buy_must_start_with_wnear() {
    let mut a = buy_ref_via_rhea();
    a.hops = vec![hop("4021", RHEA, MEME)];
    assert!(err(&a, Side::Buy).contains("start with"));
    // a sell shaped request through swap is a buy that doesn't start with wNEAR
    let s = SwapArgs { venue: Venue::RefV1, hops: vec![hop("9", MEME, WRAP)], amount_in: U128(5), min_out: U128(1) };
    assert!(err(&s, Side::Buy).contains("start with"));
}

#[test]
fn denylisted_output_rejected() {
    for t in [
        "usdt.tether-token.near",
        "17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1",
        "linear-protocol.near",
        RHEA,
        "token.burrow.near",
        "a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48.factory.bridge.near",
        "eth.omft.near",
        "nbtc.bridge.near",
    ] {
        let a = SwapArgs { venue: Venue::RefV1, hops: vec![hop("1", WRAP, t)], amount_in: U128(1), min_out: U128(1) };
        assert!(err(&a, Side::Buy).contains("denylist"), "{t}");
    }
    assert!(!is_denylisted(MEME));
    assert!(!is_denylisted("bridge.near.meme.near"));
}

#[test]
fn bad_intermediate_rejected() {
    let a = SwapArgs {
        venue: Venue::RefV1,
        hops: vec![hop("1", WRAP, "usdt.tether-token.near"), hop("2", "usdt.tether-token.near", MEME)],
        amount_in: U128(1),
        min_out: U128(1),
    };
    assert!(err(&a, Side::Buy).contains("intermediate"));
    let b = SwapArgs {
        venue: Venue::RefV1,
        hops: vec![hop("1", WRAP, "other.meme.near"), hop("2", "other.meme.near", MEME)],
        amount_in: U128(1),
        min_out: U128(1),
    };
    assert!(err(&b, Side::Buy).contains("intermediate"));
}

#[test]
fn hop_count_and_chain() {
    let mut a = buy_dcl();
    a.hops.clear();
    assert!(err(&a, Side::Buy).contains("hops"));
    let a = SwapArgs {
        venue: Venue::RefV1,
        hops: vec![hop("1", WRAP, RHEA), hop("2", RHEA, WRAP), hop("3", WRAP, RHEA), hop("4", RHEA, MEME)],
        amount_in: U128(1),
        min_out: U128(1),
    };
    assert!(err(&a, Side::Buy).contains("hops"));
    let a = SwapArgs {
        venue: Venue::RefV1,
        hops: vec![hop("1", WRAP, RHEA), hop("2", WRAP, MEME)],
        amount_in: U128(1),
        min_out: U128(1),
    };
    assert!(err(&a, Side::Buy).contains("chain"));
    let a = SwapArgs {
        venue: Venue::RefV1,
        hops: vec![hop("1", WRAP, RHEA), hop("2", RHEA, WRAP), hop("3", WRAP, MEME)],
        amount_in: U128(1),
        min_out: U128(1),
    };
    assert!(err(&a, Side::Buy).contains("repeats"));
}

#[test]
fn pool_id_format() {
    let mut a = buy_dcl();
    for bad in ["7", "a|b|2000", &format!("{MEME}|{WRAP}"), &format!("{MEME}|{WRAP}|x"), &format!("{RHEA}|{WRAP}|2000")]
    {
        a.hops[0].pool_id = bad.to_string();
        assert!(err(&a, Side::Buy).contains("dcl pool_id"), "{bad}");
    }
    a.hops[0].pool_id = format!("{WRAP}|{MEME}|2000"); // either order
    validate(&a, Side::Buy).unwrap();
    let mut r = buy_ref_via_rhea();
    for bad in ["", "-1", "01", "1.5", &format!("{WRAP}|{RHEA}|100"), "99999999999999999999"] {
        r.hops[0].pool_id = bad.to_string();
        assert!(err(&r, Side::Buy).contains("ref_v1 pool_id"), "{bad}");
    }
}

#[test]
fn unknown_venue_rejected_by_json() {
    let j = r#"{"venue":"intear","hops":[{"pool_id":"1","token_in":"wrap.near","token_out":"meme.near"}],"amount_in":"1","min_out":"1"}"#;
    assert!(serde_json::from_str::<SwapArgs>(j).is_err());
    let j = r#"{"venue":"dcl","hops":[],"amount_in":"1","min_out":"1","msg":"x"}"#;
    assert!(serde_json::from_str::<SwapArgs>(j).is_err(), "no raw msg field");
    assert_eq!(Venue::Dcl.receiver().as_str(), "dclv2.ref-labs.near");
    assert_eq!(Venue::RefV1.receiver().as_str(), "v2.ref-finance.near");
}

#[test]
fn sell_rules() {
    let s = SwapArgs { venue: Venue::RefV1, hops: vec![hop("9", MEME, WRAP)], amount_in: U128(5), min_out: U128(1) };
    validate(&s, Side::Sell).unwrap();
    let s2 = SwapArgs { venue: Venue::RefV1, hops: vec![hop("9", MEME, RHEA)], amount_in: U128(5), min_out: U128(1) };
    assert!(err(&s2, Side::Sell).contains("end with"));
    // sell may sell a non-meme too (owner's choice)
    let s3 = SwapArgs {
        venue: Venue::RefV1,
        hops: vec![hop("9", "usdt.tether-token.near", WRAP)],
        amount_in: U128(5),
        min_out: U128(1),
    };
    validate(&s3, Side::Sell).unwrap();
}

#[test]
fn dcl_msg_shape() {
    let m: Value = serde_json::from_str(&build_msg(&buy_dcl())).unwrap();
    assert_eq!(
        m,
        serde_json::json!({"Swap": {"pool_ids": [format!("{MEME}|{WRAP}|2000")], "output_token": MEME,
            "min_output_amount": "1", "skip_unwrap_near": true}})
    );
}

#[test]
fn ref_msg_shape() {
    let m: Value = serde_json::from_str(&build_msg(&buy_ref_via_rhea())).unwrap();
    assert_eq!(
        m,
        serde_json::json!({"force": 0, "skip_unwrap_near": true, "actions": [
            {"pool_id": 12, "token_in": WRAP, "token_out": RHEA, "amount_in": "10", "min_amount_out": "0"},
            {"pool_id": 4021, "token_in": RHEA, "token_out": MEME, "min_amount_out": "7"}]})
    );
}

#[test]
fn denylist_matches_worker_copy() {
    // worker/copytrade_worker/denylist.json is the source (ticket 0005). Skip if not there yet.
    let p = concat!(env!("CARGO_MANIFEST_DIR"), "/../../worker/copytrade_worker/denylist.json");
    let Ok(worker) = std::fs::read_to_string(p) else { return };
    let ours = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/denylist.json")).unwrap();
    let keys = |s: &str, k: &str| -> Vec<String> {
        let v: Value = serde_json::from_str(s).unwrap();
        let mut ks: Vec<String> = v[k].as_object().unwrap().keys().cloned().collect();
        ks.sort();
        ks
    };
    for k in ["exact", "suffix"] {
        assert_eq!(keys(&worker, k), keys(&ours, k), "denylist {k} out of sync with worker");
    }
}

// ---------------- access ----------------

#[test]
#[should_panic(expected = "caller must be owner_id")]
fn new_only_by_owner() {
    ctx("mallory.near", OTHER_PK, 0);
    Vault::new(acc(OWNER));
}

#[test]
fn state_after_new() {
    let v = vault();
    let s = v.get_state();
    assert_eq!(s.owner_id, acc(OWNER));
    assert!(!s.paused);
    assert_eq!(s.bot_key.as_deref(), Some(BOT_PK));
    assert_eq!(s.version, "2.0.0");
}

#[test]
fn bot_swap_accepted() {
    let mut v = vault();
    ctx(VAULT, BOT_PK, 0);
    v.swap(buy_dcl());
    let logs = get_logs();
    assert!(logs[0].starts_with("EVENT_JSON:"));
    let e: Value = serde_json::from_str(&logs[0]["EVENT_JSON:".len()..]).unwrap();
    assert_eq!(e["standard"], "copytrade_vault");
    assert_eq!(e["version"], "1.0.0");
    assert_eq!(e["event"], "swap");
    assert_eq!(e["data"]["token_out"], MEME);
    assert_eq!(e["data"]["venue"], "dcl");
}

#[test]
#[should_panic(expected = "only the bot key")]
fn swap_from_stranger() {
    let mut v = vault();
    ctx("mallory.near", BOT_PK, 0);
    v.swap(buy_dcl());
}

#[test]
#[should_panic(expected = "only the bot key")]
fn swap_from_owner_is_not_bot() {
    let mut v = vault();
    ctx(OWNER, BOT_PK, 0);
    v.swap(buy_dcl());
}

#[test]
#[should_panic(expected = "only the bot key")]
fn swap_from_stale_key() {
    let mut v = vault();
    ctx(VAULT, OTHER_PK, 0);
    v.swap(buy_dcl());
}

#[test]
#[should_panic(expected = "only the bot key")]
fn swap_after_key_removed() {
    let mut v = vault();
    ctx(OWNER, OTHER_PK, 1);
    v.remove_bot_key();
    ctx(VAULT, BOT_PK, 0);
    v.swap(buy_dcl());
}

#[test]
#[should_panic(expected = "paused")]
fn swap_when_paused() {
    let mut v = vault();
    ctx(OWNER, OTHER_PK, 1);
    v.pause();
    ctx(VAULT, BOT_PK, 0);
    v.swap(buy_dcl());
}

#[test]
#[should_panic(expected = "paused")]
fn register_when_paused() {
    let mut v = vault();
    ctx(OWNER, OTHER_PK, 1);
    v.pause();
    ctx(VAULT, BOT_PK, 0);
    v.register_token(acc(MEME));
}

#[test]
#[should_panic(expected = "min_out")]
fn swap_min_out_zero_panics() {
    let mut v = vault();
    ctx(VAULT, BOT_PK, 0);
    let mut a = buy_dcl();
    a.min_out = U128(0);
    v.swap(a);
}

/// Every owner method, called by the bot (self) or a stranger, panics.
#[test]
fn owner_methods_reject_non_owner() {
    type Call = fn(&mut Vault);
    let calls: Vec<(&str, Call)> = vec![
        ("sell", |v| {
            v.sell(SwapArgs {
                venue: Venue::RefV1,
                hops: vec![hop("9", MEME, WRAP)],
                amount_in: U128(5),
                min_out: U128(1),
            });
        }),
        ("withdraw_near", |v| {
            v.withdraw_near(None);
        }),
        ("withdraw_token", |v| {
            v.withdraw_token(acc(MEME), None);
        }),
        ("pause", |v| v.pause()),
        ("unpause", |v| v.unpause()),
        ("set_bot_key", |v| {
            v.set_bot_key(pk(OTHER_PK), U128(1));
        }),
        ("remove_bot_key", |v| {
            v.remove_bot_key();
        }),
        ("set_storage_budget", |v| v.set_storage_budget(U128(5))),
        ("owner_upgrade", |v| {
            v.owner_upgrade(Base58CryptoHash::from([7u8; 32]));
        }),
    ];
    for (name, f) in calls {
        for (who, dep) in [(VAULT, 1), (VAULT, 0), ("mallory.near", 1), (WRAP, 1), (DCL, 1)] {
            let mut v = vault();
            ctx(who, BOT_PK, dep);
            let r = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| f(&mut v)));
            assert!(r.is_err(), "{name} by {who} must panic");
        }
        // owner without exactly 1 yocto (FC key can't attach deposit)
        for dep in [0, 2] {
            let mut v = vault();
            ctx(OWNER, OTHER_PK, dep);
            let r = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| f(&mut v)));
            assert!(r.is_err(), "{name} by owner with {dep} yocto must panic");
        }
    }
}

#[test]
fn owner_methods_work() {
    let mut v = vault();
    ctx(OWNER, OTHER_PK, 1);
    v.pause();
    assert!(v.get_state().paused);
    v.unpause();
    assert!(!v.get_state().paused);
    v.withdraw_near(Some(U128(5)));
    v.withdraw_token(acc(MEME), Some(U128(5)));
    v.withdraw_token(acc(MEME), None);
    v.sell(SwapArgs {
        venue: Venue::Dcl,
        hops: vec![hop(&format!("{MEME}|{WRAP}|2000"), MEME, WRAP)],
        amount_in: U128(5),
        min_out: U128(1),
    });
    v.set_bot_key(pk(OTHER_PK), U128(10u128.pow(24)));
    assert_eq!(v.get_state().bot_key, None, "old key loses power before the new one lands");
    v.owner_upgrade(Base58CryptoHash::from([7u8; 32]));
}

#[test]
fn sell_works_while_paused() {
    let mut v = vault();
    ctx(OWNER, OTHER_PK, 1);
    v.pause();
    v.sell(SwapArgs { venue: Venue::RefV1, hops: vec![hop("9", MEME, WRAP)], amount_in: U128(5), min_out: U128(1) });
}

#[test]
#[should_panic(expected = "allowance must be > 0")]
fn bot_key_zero_allowance() {
    let mut v = vault();
    ctx(OWNER, OTHER_PK, 1);
    v.set_bot_key(pk(OTHER_PK), U128(0));
}

#[test]
#[should_panic]
fn callbacks_are_private() {
    let mut v = vault();
    ctx("mallory.near", BOT_PK, 0);
    v.on_bot_key_set(pk(OTHER_PK), U128(1));
}

#[test]
#[should_panic]
fn unwrap_callback_private() {
    let mut v = vault();
    ctx(OWNER, OTHER_PK, 0);
    v.on_unwrapped(U128(1));
}

// ---------------- storage budget ----------------

const MILLI: u128 = 10u128.pow(21);
const T0: u64 = 1_700_000_000_000_000_000;

/// Callback context at `ts` with the given promise results.
fn cb(ts: u64, results: Vec<PromiseResult>) {
    testing_env!(
        VMContextBuilder::new()
            .current_account_id(acc(VAULT))
            .predecessor_account_id(acc(VAULT))
            .signer_account_id(acc(VAULT))
            .signer_account_pk(pk(BOT_PK))
            .account_balance(NearToken::from_near(20))
            .block_timestamp(ts)
            .build(),
        near_sdk::test_vm_config(),
        near_sdk::RuntimeFeesConfig::test(),
        Default::default(),
        results,
    );
}

fn ok(b: &str) -> PromiseResult {
    PromiseResult::Successful(b.as_bytes().to_vec())
}

/// on_register_checked for an unregistered token whose storage min is `min`.
fn register_cb(v: &mut Vault, ts: u64, min: u128) {
    cb(ts, vec![ok("null"), ok(&format!(r#"{{"min":"{min}","max":null}}"#))]);
    v.on_register_checked(acc(MEME));
}

fn budget(v: &Vault) -> StorageBudgetView {
    v.get_state().storage_budget
}

#[test]
fn storage_budget_default() {
    let v = vault();
    let b = budget(&v);
    assert_eq!(b.per_day.0, DEFAULT_STORAGE_BUDGET.as_yoctonear());
    assert_eq!(b.per_day.0, 100 * MILLI);
    assert_eq!((b.spent.0, b.window_start.0), (0, 0));
}

#[test]
fn register_counts_against_budget() {
    let mut v = vault();
    register_cb(&mut v, T0, 10 * MILLI);
    let b = budget(&v);
    assert_eq!((b.spent.0, b.window_start.0), (10 * MILLI, T0));
    register_cb(&mut v, T0 + 1, 12 * MILLI);
    assert_eq!(budget(&v).spent.0, 22 * MILLI);
    assert_eq!(budget(&v).window_start.0, T0, "window starts at its first payment");
}

#[test]
fn register_up_to_budget_exactly() {
    let mut v = vault();
    for i in 0..10 {
        register_cb(&mut v, T0 + i, 10 * MILLI);
    }
    assert_eq!(budget(&v).spent.0, 100 * MILLI);
}

#[test]
#[should_panic(expected = "storage budget exceeded")]
fn register_over_budget_panics() {
    let mut v = vault();
    for i in 0..9 {
        register_cb(&mut v, T0 + i, 10 * MILLI);
    }
    register_cb(&mut v, T0 + 10, 11 * MILLI);
}

#[test]
fn budget_window_resets_after_24h() {
    let mut v = vault();
    for i in 0..10 {
        register_cb(&mut v, T0 + i, 10 * MILLI);
    }
    let later = T0 + STORAGE_WINDOW_NS - 1;
    let r = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| register_cb(&mut v, later, MILLI)));
    assert!(r.is_err(), "still the same window");
    let mut v = vault();
    for i in 0..10 {
        register_cb(&mut v, T0 + i, 10 * MILLI);
    }
    register_cb(&mut v, T0 + STORAGE_WINDOW_NS, 5 * MILLI);
    let b = budget(&v);
    assert_eq!((b.spent.0, b.window_start.0), (5 * MILLI, T0 + STORAGE_WINDOW_NS));
}

#[test]
fn view_shows_zero_spent_after_window() {
    let mut v = vault();
    register_cb(&mut v, T0, 10 * MILLI);
    cb(T0 + STORAGE_WINDOW_NS, vec![]);
    assert_eq!(budget(&v).spent.0, 0);
}

#[test]
fn failed_storage_deposit_gives_budget_back() {
    let mut v = vault();
    register_cb(&mut v, T0, 10 * MILLI);
    cb(T0 + 1, vec![PromiseResult::Failed]);
    assert!(!v.on_registered(acc(MEME), U128(10 * MILLI), near_sdk::json_types::U64(T0)));
    assert_eq!(budget(&v).spent.0, 0);
}

#[test]
fn already_registered_spends_nothing() {
    let mut v = vault();
    cb(T0, vec![ok(r#"{"total":"1","available":"0"}"#), ok(r#"{"min":"5","max":null}"#)]);
    v.on_register_checked(acc(MEME));
    assert_eq!(budget(&v).spent.0, 0);
}

#[test]
#[should_panic(expected = "storage budget exceeded")]
fn register_token_panics_early_when_budget_used_up() {
    let mut v = vault();
    for i in 0..10 {
        register_cb(&mut v, T0 + i, 10 * MILLI);
    }
    cb(T0 + 20, vec![]);
    v.register_token(acc(MEME));
}

#[test]
fn owner_sets_budget() {
    let mut v = vault();
    ctx(OWNER, OTHER_PK, 1);
    v.set_storage_budget(U128(3 * MILLI));
    assert_eq!(budget(&v).per_day.0, 3 * MILLI);
    let r = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| register_cb(&mut v, T0, 4 * MILLI)));
    assert!(r.is_err());
    let mut v = vault();
    ctx(OWNER, OTHER_PK, 1);
    v.set_storage_budget(U128(0));
    let r = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| register_cb(&mut v, T0, 1)));
    assert!(r.is_err(), "0 = no registrations");
}

// ---------------- swap_result errors ----------------

fn sent_error(r: PromiseResult) -> Value {
    let mut v = vault();
    cb(T0, vec![r]);
    v.on_swap_sent(acc(MEME), U128(0));
    let logs = get_logs();
    let e: Value = serde_json::from_str(&logs.last().unwrap()["EVENT_JSON:".len()..]).unwrap();
    assert_eq!(e["event"], "swap_result");
    e["data"]["error"].clone()
}

#[test]
fn swap_result_distinguishes_transfer_failure_from_zero_used() {
    assert_eq!(sent_error(PromiseResult::Failed), "ft_transfer_call failed");
    assert_eq!(sent_error(ok("junk")), "ft_transfer_call failed");
    assert_eq!(sent_error(ok(r#""0""#)), "venue used 0");
}

// ---------------- state versioning (README "Upgrades") ----------------

fn stored(v: &Vault) -> Vec<u8> {
    near_sdk::borsh::to_vec(v).unwrap()
}

#[test]
fn state_is_stored_as_versioned_v2() {
    let v = vault();
    let bytes = stored(&v);
    assert_eq!(bytes, near_sdk::borsh::to_vec(&VersionedState::V2(v.s.clone())).unwrap());
    assert_eq!(bytes[0], 0, "V2 is tag 0");
    let back = Vault::try_from_slice(&bytes).unwrap();
    assert_eq!(back.s, v.s);
}

/// Test-only "V3 code": a new layout appended as a new variant, as README "Upgrades" prescribes.
mod v3 {
    use super::*;
    use near_sdk::borsh::{BorshDeserialize, BorshSerialize};

    #[derive(BorshSerialize, BorshDeserialize, Debug, PartialEq)]
    #[borsh(crate = "near_sdk::borsh")]
    pub struct StateV3 {
        pub owner_id: AccountId,
        pub paused: bool,
        pub bot_key: Option<PublicKey>,
        pub storage_budget: StorageBudget,
        pub max_buy: u128,
    }

    #[derive(BorshSerialize, BorshDeserialize, Debug, PartialEq)]
    #[borsh(crate = "near_sdk::borsh")]
    pub enum VersionedStateV3 {
        V2(StateV2),
        V3(StateV3),
    }

    impl VersionedStateV3 {
        pub fn into_current(self) -> StateV3 {
            match self {
                Self::V2(s) => StateV3 {
                    owner_id: s.owner_id,
                    paused: s.paused,
                    bot_key: s.bot_key,
                    storage_budget: s.storage_budget,
                    max_buy: u128::MAX,
                },
                Self::V3(s) => s,
            }
        }
    }
}

#[test]
fn v2_state_blob_loads_with_v3_code() {
    let mut v = vault();
    ctx(OWNER, OTHER_PK, 1);
    v.pause();
    v.set_storage_budget(U128(42));
    let blob = stored(&v);

    let s3 = v3::VersionedStateV3::try_from_slice(&blob).unwrap().into_current();
    assert_eq!(s3.owner_id, acc(OWNER));
    assert!(s3.paused);
    assert_eq!(s3.bot_key, Some(pk(BOT_PK)));
    assert_eq!(s3.storage_budget, v.s.storage_budget);
    assert_eq!(s3.max_buy, u128::MAX);

    // V3 code writes tag 1; V2 code (this build) refuses it instead of misreading it.
    let v3_blob = near_sdk::borsh::to_vec(&v3::VersionedStateV3::V3(s3)).unwrap();
    assert_eq!(v3_blob[0], 1);
    assert!(Vault::try_from_slice(&v3_blob).is_err(), "downgrade must fail to load");
}

#[test]
fn migrate_reloads_and_keeps_state() {
    let mut v = vault();
    ctx(OWNER, OTHER_PK, 1);
    v.set_storage_budget(U128(7));
    let before = v.get_state();
    env::state_write(&v);
    ctx(VAULT, OTHER_PK, 0);
    let m = Vault::migrate();
    assert_eq!(m.get_state(), before);
    assert_eq!(stored(&m), stored(&v));
}

#[test]
#[should_panic(expected = "migrate: no state")]
fn migrate_without_state_panics() {
    ctx(VAULT, OTHER_PK, 0);
    Vault::migrate();
}

#[test]
#[should_panic(expected = "Cannot deserialize")]
fn migrate_unknown_layout_panics() {
    ctx(VAULT, OTHER_PK, 0);
    env::storage_write(b"STATE", &[9, 1, 2, 3]);
    Vault::migrate();
}

#[test]
fn owner_upgrade_switches_code_and_migrates_in_one_receipt() {
    use near_sdk::mock::MockAction;
    let mut v = vault();
    ctx(OWNER, OTHER_PK, 1);
    v.owner_upgrade(Base58CryptoHash::from([7u8; 32]));
    let receipts = near_sdk::test_utils::get_created_receipts();
    assert_eq!(receipts.len(), 1, "one receipt: both actions revert together");
    let r = &receipts[0];
    assert_eq!(r.receiver_id, acc(VAULT));
    assert_eq!(r.actions.len(), 2);
    assert!(matches!(r.actions[0], MockAction::UseGlobalContract { .. }), "{:?}", r.actions[0]);
    match &r.actions[1] {
        MockAction::FunctionCallWeight { method_name, args, attached_deposit, .. } => {
            assert_eq!(method_name, b"migrate");
            assert_eq!(args, b"{}");
            assert_eq!(attached_deposit.as_yoctonear(), 0);
        }
        a => panic!("expected migrate call, got {a:?}"),
    }
}
