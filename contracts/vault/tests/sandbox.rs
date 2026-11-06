//! near-workspaces sandbox tests. Contracts live at their mainnet names (wrap.near,
//! dclv2.ref-labs.near, v2.ref-finance.near, token.rhealab.near) via state patches, so the
//! vault's constants are tested as built. Build wasm first: see README "Build and test".

use near_workspaces::network::Sandbox;
use near_workspaces::types::{AccessKey, AccessKeyPermission, AccountDetailsPatch, KeyType, NearToken, SecretKey};
use near_workspaces::{Account, AccountId, Worker};
use serde_json::{json, Value};

const ONE_YOCTO: NearToken = NearToken::from_yoctonear(1);
const WRAP: &str = "wrap.near";
const RHEA: &str = "token.rhealab.near";
const DCL: &str = "dclv2.ref-labs.near";
const REF: &str = "v2.ref-finance.near";
const MEME: &str = "meme.near";
const USDT: &str = "usdt.tether-token.near";
/// Default gas allowance (interface: 1 NEAR).
const ALLOWANCE: u128 = 1;
/// Bot txs use 200 Tgas: the allowance must cover prepaid gas at the pessimistic price, and
/// 300 Tgas reserves more than 1 NEAR.
const BOT_GAS: near_workspaces::types::Gas = near_workspaces::types::Gas::from_tgas(200);

fn wasm(name: &str) -> Vec<u8> {
    let dir = env!("CARGO_MANIFEST_DIR");
    let p = match name {
        "vault" => format!("{dir}/target/near/copytrade_vault.wasm"),
        n => format!("{dir}/target/near/{n}/{n}.wasm"),
    };
    std::fs::read(&p).unwrap_or_else(|_| panic!("{p} missing: run scripts/build.sh"))
}

fn near(n: u128) -> u128 {
    n * 10u128.pow(24)
}

struct Env {
    worker: Worker<Sandbox>,
    owner: Account,
    vault: AccountId,
    bot: Account,
    bot_sk: SecretKey,
    mallory: Account,
}

/// Account at a fixed top-level name, with a contract.
async fn named(worker: &Worker<Sandbox>, id: &str, code: &[u8], init: Value) -> anyhow::Result<Account> {
    let id: AccountId = id.parse()?;
    let sk = SecretKey::from_random(KeyType::ED25519);
    worker
        .patch(&id)
        .account(AccountDetailsPatch::default().balance(NearToken::from_near(100)))
        .access_key(sk.public_key(), AccessKey::full_access())
        .transact()
        .await?;
    let acc = Account::from_secret_key(id.clone(), sk, worker);
    acc.deploy(code).await?.into_result()?;
    acc.call(&id, "new").args_json(init).transact().await?.into_result()?;
    Ok(acc)
}

async fn view<T: serde::de::DeserializeOwned>(w: &Worker<Sandbox>, id: &str, f: &str, args: Value) -> T {
    w.view(&id.parse().unwrap(), f).args_json(args).await.unwrap().json::<T>().unwrap()
}

async fn ft_bal(w: &Worker<Sandbox>, token: &str, who: &AccountId) -> u128 {
    view::<String>(w, token, "ft_balance_of", json!({"account_id": who})).await.parse().unwrap()
}

async fn near_bal(w: &Worker<Sandbox>, who: &AccountId) -> u128 {
    w.view_account(who).await.unwrap().balance.as_yoctonear()
}

fn events(logs: &[&str]) -> Vec<Value> {
    logs.iter()
        .filter_map(|l| l.strip_prefix("EVENT_JSON:"))
        .map(|j| serde_json::from_str::<Value>(j).unwrap())
        .filter(|e| e["standard"] == "copytrade_vault")
        .collect()
}

fn event<'a>(evs: &'a [Value], name: &str) -> Option<&'a Value> {
    evs.iter().find(|e| e["event"] == name)
}

/// The setup flow from README "Account creation", with DeployContract standing in for
/// UseGlobalContract (near-workspaces can't build that action yet).
async fn setup() -> anyhow::Result<Env> {
    let worker = near_workspaces::sandbox().await?;
    let ft = wasm("mock_ft");
    let dex = wasm("mock_dex");
    for t in [WRAP, RHEA, MEME, USDT] {
        named(&worker, t, &ft, json!({})).await?;
    }
    // DEX pays 2 out per 1 in on every venue.
    for d in [DCL, REF] {
        named(&worker, d, &dex, json!({"rate_num": "2", "rate_den": "1"})).await?;
        for t in [WRAP, RHEA, MEME, USDT] {
            worker
                .root_account()?
                .call(&t.parse()?, "mint")
                .args_json(json!({"account_id": d, "amount": near(1_000_000).to_string()}))
                .transact()
                .await?
                .into_result()?;
        }
    }
    let root = worker.root_account()?;
    let owner =
        root.create_subaccount("alice").initial_balance(NearToken::from_near(100)).transact().await?.into_result()?;
    let mallory =
        root.create_subaccount("mallory").initial_balance(NearToken::from_near(50)).transact().await?.into_result()?;
    let vault: AccountId = format!("vault.{}", owner.id()).parse()?;
    let bot_sk = SecretKey::from_random(KeyType::ED25519);

    // One transaction, signed by the owner. No AddKey for a full-access key: the vault never has one.
    owner
        .batch(&vault)
        .create_account()
        .transfer(NearToken::from_near(20))
        .deploy(&wasm("vault"))
        .call(near_workspaces::operations::Function::new("new").args_json(json!({"owner_id": owner.id()})))
        .call(
            near_workspaces::operations::Function::new("set_bot_key")
                .args_json(json!({"public_key": bot_sk.public_key(), "allowance": near(ALLOWANCE).to_string()}))
                .deposit(ONE_YOCTO)
                .gas(near_workspaces::types::Gas::from_tgas(50)),
        )
        .transact()
        .await?
        .into_result()?;
    let bot = Account::from_secret_key(vault.clone(), bot_sk.clone(), &worker);
    Ok(Env { worker, owner, vault, bot, bot_sk, mallory })
}

fn buy(venue: &str, out: &str, amount: u128, min_out: u128) -> Value {
    let hops = if venue == "dcl" {
        json!([{"pool_id": format!("{out}|{WRAP}|2000"), "token_in": WRAP, "token_out": out}])
    } else {
        json!([{"pool_id": "7", "token_in": WRAP, "token_out": out}])
    };
    json!({"args": {"venue": venue, "hops": hops, "amount_in": amount.to_string(), "min_out": min_out.to_string()}})
}

impl Env {
    async fn bot_call(
        &self,
        method: &str,
        args: Value,
    ) -> anyhow::Result<near_workspaces::result::ExecutionFinalResult> {
        Ok(self.bot.call(&self.vault, method).args_json(args).gas(BOT_GAS).transact().await?)
    }
    async fn register(&self, token: &str) {
        let r = self.bot_call("register_token", json!({"token_id": token})).await.unwrap();
        assert!(r.is_success(), "{r:?}");
    }
    async fn owner_call(&self, method: &str, args: Value) -> near_workspaces::result::ExecutionFinalResult {
        self.owner.call(&self.vault, method).args_json(args).deposit(ONE_YOCTO).max_gas().transact().await.unwrap()
    }
    async fn state(&self) -> Value {
        view(&self.worker, self.vault.as_str(), "get_state", json!({})).await
    }
}

// ---------------- setup ----------------

#[tokio::test]
async fn setup_leaves_only_the_bot_key() -> anyhow::Result<()> {
    let e = setup().await?;
    let st = e.state().await;
    assert_eq!(st["owner_id"], e.owner.id().as_str());
    assert_eq!(st["paused"], false);
    assert_eq!(st["bot_key"], e.bot_sk.public_key().to_string());
    assert_eq!(st["version"], "2.0.0");
    assert_eq!(
        st["storage_budget"],
        json!({"per_day": (near(1) / 10).to_string(), "spent": "0", "window_start": "0"}),
        "default 0.1 NEAR per 24 h"
    );
    let keys = e.worker.view_access_keys(&e.vault).await?;
    assert_eq!(keys.len(), 1, "exactly one key on the vault");
    match &keys[0].access_key.permission {
        AccessKeyPermission::FunctionCall(fc) => {
            assert_eq!(fc.receiver_id, e.vault.as_str());
            assert_eq!(fc.method_names, vec!["swap".to_string(), "register_token".to_string()]);
            assert_eq!(fc.allowance, Some(NearToken::from_near(ALLOWANCE)));
        }
        p => panic!("bot key must be a function-call key, got {p:?}"),
    }
    // nobody can re-init
    let r = e.mallory.call(&e.vault, "new").args_json(json!({"owner_id": e.mallory.id()})).transact().await?;
    assert!(r.is_failure());
    Ok(())
}

#[tokio::test]
async fn new_rejects_foreign_owner() -> anyhow::Result<()> {
    let worker = near_workspaces::sandbox().await?;
    let root = worker.root_account()?;
    let m = root.create_subaccount("m").initial_balance(NearToken::from_near(20)).transact().await?.into_result()?;
    let vid: AccountId = format!("v.{}", m.id()).parse()?;
    let r = m
        .batch(&vid)
        .create_account()
        .transfer(NearToken::from_near(5))
        .deploy(&wasm("vault"))
        .call(near_workspaces::operations::Function::new("new").args_json(json!({"owner_id": "victim.test.near"})))
        .transact()
        .await?;
    assert!(r.is_failure(), "init naming someone else as owner must fail");
    Ok(())
}

// ---------------- bot buys ----------------

#[tokio::test]
async fn bot_buys_on_dcl_output_lands_in_vault() -> anyhow::Result<()> {
    let e = setup().await?;
    e.register(WRAP).await;
    e.register(MEME).await;
    let r = e.bot_call("swap", buy("dcl", MEME, near(5), near(9))).await?;
    assert!(r.is_success(), "{r:?}");
    let evs = events(&r.logs());
    assert_eq!(event(&evs, "swap").unwrap()["data"]["venue"], "dcl");
    assert_eq!(event(&evs, "swap_result").unwrap()["data"]["amount_out"], near(10).to_string());
    assert_eq!(ft_bal(&e.worker, MEME, &e.vault).await, near(10));
    assert_eq!(ft_bal(&e.worker, WRAP, &e.vault).await, 0, "wrapped exactly the shortfall, all spent");
    let msg: Value = serde_json::from_str(&view::<String>(&e.worker, DCL, "last_msg", json!({})).await)?;
    assert_eq!(
        msg,
        json!({"Swap": {"pool_ids": [format!("{MEME}|{WRAP}|2000")], "output_token": MEME,
        "min_output_amount": near(9).to_string(), "skip_unwrap_near": true}})
    );
    Ok(())
}

#[tokio::test]
async fn bot_buys_on_ref_v1_via_rhea() -> anyhow::Result<()> {
    let e = setup().await?;
    e.register(WRAP).await;
    e.register(MEME).await;
    let args = json!({"args": {"venue": "ref_v1", "amount_in": near(2).to_string(), "min_out": "1",
        "hops": [{"pool_id": "12", "token_in": WRAP, "token_out": RHEA}, {"pool_id": "40", "token_in": RHEA, "token_out": MEME}]}});
    let r = e.bot_call("swap", args).await?;
    assert!(r.is_success(), "{r:?}");
    assert_eq!(ft_bal(&e.worker, MEME, &e.vault).await, near(4));
    assert_eq!(ft_bal(&e.worker, RHEA, &e.vault).await, 0, "intermediate never reaches the vault");
    let msg: Value = serde_json::from_str(&view::<String>(&e.worker, REF, "last_msg", json!({})).await)?;
    assert_eq!(msg["actions"][0]["amount_in"], near(2).to_string());
    assert_eq!(msg["actions"][1]["min_amount_out"], "1");
    Ok(())
}

#[tokio::test]
async fn existing_wnear_is_used_before_wrapping() -> anyhow::Result<()> {
    let e = setup().await?;
    e.register(WRAP).await;
    e.register(MEME).await;
    e.worker
        .root_account()?
        .call(&WRAP.parse()?, "mint")
        .args_json(json!({"account_id": e.vault, "amount": near(3).to_string()}))
        .transact()
        .await?
        .into_result()?;
    let before = near_bal(&e.worker, &e.vault).await;
    let r = e.bot_call("swap", buy("ref_v1", MEME, near(3), 1)).await?;
    assert!(r.is_success());
    assert_eq!(ft_bal(&e.worker, WRAP, &e.vault).await, 0);
    assert!(before - near_bal(&e.worker, &e.vault).await < near(1) / 10, "no NEAR wrapped, only gas");
    Ok(())
}

// ---------------- buy rules ----------------

async fn assert_bot_rejected(e: &Env, args: Value, why: &str) {
    let r = e.bot_call("swap", args).await.unwrap();
    assert!(r.is_failure(), "{why}: must fail");
    assert!(format!("{:?}", r.failures()).contains(why), "{why}: {:?}", r.failures());
}

#[tokio::test]
async fn buy_rules_reject() -> anyhow::Result<()> {
    let e = setup().await?;
    e.register(WRAP).await;
    e.register(MEME).await;
    assert_bot_rejected(&e, buy("dcl", MEME, near(1), 0), "min_out must be > 0").await;
    assert_bot_rejected(&e, buy("ref_v1", USDT, near(1), 1), "denylist").await;
    let wrong_first = json!({"args": {"venue": "ref_v1", "amount_in": "5", "min_out": "1",
        "hops": [{"pool_id": "3", "token_in": MEME, "token_out": WRAP}]}});
    assert_bot_rejected(&e, wrong_first, "buy must start with").await;
    let bad_mid = json!({"args": {"venue": "ref_v1", "amount_in": "5", "min_out": "1",
        "hops": [{"pool_id": "3", "token_in": WRAP, "token_out": USDT}, {"pool_id": "4", "token_in": USDT, "token_out": MEME}]}});
    assert_bot_rejected(&e, bad_mid, "intermediate token not allowed").await;
    let unknown = json!({"args": {"venue": "intear", "amount_in": "5", "min_out": "1",
        "hops": [{"pool_id": "3", "token_in": WRAP, "token_out": MEME}]}});
    assert_bot_rejected(&e, unknown, "unknown variant").await;
    let raw_msg = json!({"args": {"venue": "dcl", "amount_in": "5", "min_out": "1", "msg": "{}",
        "hops": [{"pool_id": format!("{MEME}|{WRAP}|2000"), "token_in": WRAP, "token_out": MEME}]}});
    assert_bot_rejected(&e, raw_msg, "unknown field").await;
    let h = |i: &str, o: &str| json!({"pool_id": "1", "token_in": i, "token_out": o});
    let four = json!({"args": {"venue": "ref_v1", "amount_in": "5", "min_out": "1",
        "hops": [h(WRAP, RHEA), h(RHEA, WRAP), h(WRAP, RHEA), h(RHEA, MEME)]}});
    assert_bot_rejected(&e, four, "hops: want 1..3").await;

    // paused
    assert!(e.owner_call("pause", json!({})).await.is_success());
    assert_bot_rejected(&e, buy("dcl", MEME, near(1), 1), "paused").await;
    let r = e.bot_call("register_token", json!({"token_id": RHEA})).await?;
    assert!(r.is_failure());
    assert!(e.owner_call("unpause", json!({})).await.is_success());

    // balance: async, so the tx succeeds but nothing moves and swap_result reports it
    let r = e.bot_call("swap", buy("dcl", MEME, near(50), 1)).await?;
    let evs = events(&r.logs());
    assert_eq!(event(&evs, "swap_result").unwrap()["data"]["error"], "amount_in above balance");
    assert_eq!(view::<u32>(&e.worker, DCL, "swaps", json!({})).await, 0);
    assert_eq!(ft_bal(&e.worker, MEME, &e.vault).await, 0);
    Ok(())
}

#[tokio::test]
async fn unregistered_output_or_slippage_moves_nothing() -> anyhow::Result<()> {
    let e = setup().await?;
    e.register(WRAP).await;
    // not registered on MEME: refuse before sending, so output can't end up in a DEX balance
    let r = e.bot_call("swap", buy("dcl", MEME, near(1), 1)).await?;
    let evs = events(&r.logs());
    assert!(event(&evs, "swap_result").unwrap()["data"]["error"].as_str().unwrap().contains("not registered"));
    assert_eq!(view::<u32>(&e.worker, DCL, "swaps", json!({})).await, 0);

    e.register(MEME).await;
    let n0 = near_bal(&e.worker, &e.vault).await;
    // DEX gives 2x; asking 3x fails at the venue, input refunded to the vault as wNEAR
    let r = e.bot_call("swap", buy("ref_v1", MEME, near(1), near(3))).await?;
    let evs = events(&r.logs());
    assert_eq!(event(&evs, "swap_result").unwrap()["data"]["error"], "venue used 0");
    assert_eq!(ft_bal(&e.worker, WRAP, &e.vault).await, near(1));
    assert_eq!(ft_bal(&e.worker, MEME, &e.vault).await, 0);
    assert!(n0 - near_bal(&e.worker, &e.vault).await < near(1) + near(1) / 10);
    Ok(())
}

#[tokio::test]
async fn register_token_pays_storage_once() -> anyhow::Result<()> {
    let e = setup().await?;
    let r = e.bot_call("register_token", json!({"token_id": MEME})).await?;
    let evs = events(&r.logs());
    assert_eq!(event(&evs, "token_registered").unwrap()["data"]["deposit"], "1250000000000000000000");
    let r = e.bot_call("register_token", json!({"token_id": MEME})).await?;
    assert!(r.is_success());
    assert!(event(&events(&r.logs()), "token_registered").is_none(), "second call skips");
    Ok(())
}

/// Mock FT storage min: 0.00125 NEAR.
const STORAGE_MIN: u128 = 1_250_000_000_000_000_000_000;

#[tokio::test]
async fn storage_budget_caps_registrations() -> anyhow::Result<()> {
    let e = setup().await?;
    // Room for exactly one registration in this window.
    assert!(e.owner_call("set_storage_budget", json!({"per_day": (2 * STORAGE_MIN - 1).to_string()})).await.is_success());
    e.register(MEME).await;
    let st = e.state().await["storage_budget"].clone();
    assert_eq!(st["spent"], STORAGE_MIN.to_string());
    assert_ne!(st["window_start"], "0");
    let r = e.bot_call("register_token", json!({"token_id": RHEA})).await?;
    assert!(r.is_failure(), "bot can't exceed the budget");
    assert!(format!("{:?}", r.failures()).contains("storage budget exceeded"));
    assert!(view::<Value>(&e.worker, RHEA, "storage_balance_of", json!({"account_id": e.vault})).await.is_null());
    assert_eq!(e.state().await["storage_budget"]["spent"], STORAGE_MIN.to_string());

    // Used up exactly: the call fails before any cross-contract call.
    assert!(e.owner_call("set_storage_budget", json!({"per_day": STORAGE_MIN.to_string()})).await.is_success());
    let r = e.bot_call("register_token", json!({"token_id": RHEA})).await?;
    assert!(r.is_failure());
    assert!(r.receipt_outcomes()[0].is_failure(), "panics in the first receipt");

    // Owner raises it: works again.
    assert!(e.owner_call("set_storage_budget", json!({"per_day": (near(1) / 10).to_string()})).await.is_success());
    e.register(RHEA).await;
    assert_eq!(e.state().await["storage_budget"]["spent"], (2 * STORAGE_MIN).to_string());
    Ok(())
}

// ---------------- the bot key can't do anything else ----------------

/// The protocol refused the tx for `why` (an InvalidAccessKeyError / ActionsValidation kind).
fn denied(r: near_workspaces::Result<near_workspaces::result::ExecutionFinalResult>, why: &str, what: &str) {
    let msg = match r {
        Err(e) => format!("{e:?}"),
        Ok(o) => panic!("{what}: tx went through to execution: {o:?}"),
    };
    assert!(msg.contains(why), "{what}: want {why}, got {msg}");
}

#[tokio::test]
async fn bot_key_limited_to_swap_and_register() -> anyhow::Result<()> {
    let e = setup().await?;
    e.register(WRAP).await;
    e.register(MEME).await;
    e.worker
        .root_account()?
        .call(&MEME.parse()?, "mint")
        .args_json(json!({"account_id": e.vault, "amount": "1000"}))
        .transact()
        .await?
        .into_result()?;
    let sell = json!({"args": {"venue": "ref_v1", "amount_in": "1000", "min_out": "1", "hops": [{"pool_id": "1", "token_in": MEME, "token_out": WRAP}]}});
    let calls: Vec<(&str, Value)> = vec![
        ("withdraw_near", json!({"amount": null})),
        ("withdraw_token", json!({"token_id": MEME, "amount": null})),
        ("sell", sell.clone()),
        ("pause", json!({})),
        ("unpause", json!({})),
        ("set_storage_budget", json!({"per_day": near(10).to_string()})),
        ("set_bot_key", json!({"public_key": SecretKey::from_random(KeyType::ED25519).public_key(), "allowance": "1"})),
        ("remove_bot_key", json!({})),
        ("owner_upgrade", json!({"code_hash": "11111111111111111111111111111111"})),
        ("on_unwrapped", json!({"want": "1"})),
        ("on_withdraw_token_balance", json!({"token_id": MEME})),
        (
            "on_bot_key_set",
            json!({"public_key": SecretKey::from_random(KeyType::ED25519).public_key(), "allowance": "1"}),
        ),
    ];
    for (m, a) in &calls {
        // key permission: the RPC refuses the tx outright
        let r = e.bot.call(&e.vault, m).args_json(a.clone()).gas(BOT_GAS).transact().await;
        denied(r, "MethodNameMismatch", m);
        let r = e.bot.call(&e.vault, m).args_json(a.clone()).deposit(ONE_YOCTO).gas(BOT_GAS).transact().await;
        denied(r, "DepositWithFunctionCall", m);
    }
    // deposit on swap, a plain transfer, calls to other contracts: all refused by the key
    let r =
        e.bot.call(&e.vault, "swap").args_json(buy("dcl", MEME, 1, 1)).deposit(ONE_YOCTO).gas(BOT_GAS).transact().await;
    denied(r, "DepositWithFunctionCall", "swap with deposit");
    let r = e.bot.batch(e.mallory.id()).transfer(ONE_YOCTO).transact().await;
    denied(r, "RequiresFullAccess", "transfer");
    let r = e
        .bot
        .call(&MEME.parse()?, "ft_transfer")
        .args_json(json!({"receiver_id": e.mallory.id(), "amount": "1000"}))
        .deposit(ONE_YOCTO)
        .transact()
        .await;
    denied(r, "DepositWithFunctionCall", "token ft_transfer");
    let r = e.bot.call(&WRAP.parse()?, "near_withdraw").args_json(json!({"amount": "1"})).transact().await;
    denied(r, "ReceiverMismatch", "wrap near_withdraw");
    let r = e
        .bot
        .batch(&e.vault)
        .add_key(SecretKey::from_random(KeyType::ED25519).public_key(), AccessKey::full_access())
        .transact()
        .await;
    denied(r, "RequiresFullAccess", "add_key");
    let r = e.bot.batch(&e.vault).delete_account(e.mallory.id()).transact().await;
    denied(r, "RequiresFullAccess", "delete_account");
    let r = e.bot.batch(&e.vault).deploy(&wasm("mock_ft")).transact().await;
    denied(r, "RequiresFullAccess", "deploy");
    assert_eq!(ft_bal(&e.worker, MEME, &e.vault).await, 1000);
    assert_eq!(e.state().await["paused"], false);
    Ok(())
}

#[tokio::test]
async fn stranger_can_not_move_funds() -> anyhow::Result<()> {
    let e = setup().await?;
    e.register(WRAP).await;
    e.register(MEME).await;
    e.worker
        .root_account()?
        .call(&MEME.parse()?, "mint")
        .args_json(json!({"account_id": e.vault, "amount": "1000"}))
        .transact()
        .await?
        .into_result()?;
    let n0 = near_bal(&e.worker, &e.vault).await;
    let sell = json!({"args": {"venue": "ref_v1", "amount_in": "1000", "min_out": "1", "hops": [{"pool_id": "1", "token_in": MEME, "token_out": WRAP}]}});
    let calls: Vec<(&str, Value)> = vec![
        ("swap", buy("dcl", MEME, near(1), 1)),
        ("register_token", json!({"token_id": RHEA})),
        ("withdraw_near", json!({"amount": null})),
        ("withdraw_token", json!({"token_id": MEME, "amount": null})),
        ("sell", sell),
        ("pause", json!({})),
        ("set_storage_budget", json!({"per_day": near(10).to_string()})),
        ("on_registered", json!({"token_id": MEME, "deposit": "1", "window": "0"})),
        ("set_bot_key", json!({"public_key": e.mallory.secret_key().public_key(), "allowance": "1"})),
        ("remove_bot_key", json!({})),
        ("owner_upgrade", json!({"code_hash": "11111111111111111111111111111111"})),
        ("on_unwrapped", json!({"want": near(5).to_string()})),
        ("on_withdraw_near_balance", json!({"amount": null})),
        ("on_withdraw_token_balance", json!({"token_id": MEME})),
        ("on_bot_key_set", json!({"public_key": e.mallory.secret_key().public_key(), "allowance": "1"})),
        ("on_swap_checked", json!({"args": buy("dcl", MEME, near(1), 1)["args"], "buy": true})),
    ];
    for (m, a) in calls {
        for dep in [NearToken::from_yoctonear(0), ONE_YOCTO] {
            let r = e.mallory.call(&e.vault, m).args_json(a.clone()).deposit(dep).max_gas().transact().await?;
            assert!(r.is_failure(), "mallory called {m}");
        }
    }
    assert_eq!(ft_bal(&e.worker, MEME, &e.vault).await, 1000);
    assert!(near_bal(&e.worker, &e.vault).await >= n0);
    assert_eq!(e.state().await["bot_key"], e.bot_sk.public_key().to_string());
    Ok(())
}

// ---------------- owner ----------------

#[tokio::test]
async fn owner_withdraws_near_and_tokens() -> anyhow::Result<()> {
    let e = setup().await?;
    e.register(WRAP).await;
    e.register(MEME).await;
    e.worker
        .root_account()?
        .call(&MEME.parse()?, "mint")
        .args_json(json!({"account_id": e.vault, "amount": "1000"}))
        .transact()
        .await?
        .into_result()?;
    e.worker
        .root_account()?
        .call(&MEME.parse()?, "mint")
        .args_json(json!({"account_id": e.owner.id(), "amount": "0"}))
        .transact()
        .await?
        .into_result()?;
    e.worker
        .root_account()?
        .call(&WRAP.parse()?, "mint")
        .args_json(json!({"account_id": e.vault, "amount": near(2).to_string()}))
        .transact()
        .await?
        .into_result()?;

    let o0 = near_bal(&e.worker, e.owner.id()).await;
    let r = e.owner_call("withdraw_near", json!({"amount": near(1).to_string()})).await;
    assert!(r.is_success(), "{r:?}");
    assert_eq!(
        event(&events(&r.logs()), "withdraw").unwrap()["data"],
        json!({"token_id": "near", "amount": near(1).to_string()})
    );
    assert!(near_bal(&e.worker, e.owner.id()).await > o0 + near(1) - near(1) / 100);

    let r = e.owner_call("withdraw_token", json!({"token_id": MEME, "amount": "400"})).await;
    assert!(r.is_success());
    assert_eq!(ft_bal(&e.worker, MEME, e.owner.id()).await, 400);
    let r = e.owner_call("withdraw_token", json!({"token_id": MEME, "amount": null})).await;
    assert!(r.is_success());
    assert_eq!(ft_bal(&e.worker, MEME, e.owner.id()).await, 1000);
    assert_eq!(ft_bal(&e.worker, MEME, &e.vault).await, 0);

    // all: unwraps wNEAR, leaves only the storage stake
    let o1 = near_bal(&e.worker, e.owner.id()).await;
    let r = e.owner_call("withdraw_near", json!({"amount": null})).await;
    assert!(r.is_success(), "{r:?}");
    assert_eq!(ft_bal(&e.worker, WRAP, &e.vault).await, 0);
    let got = near_bal(&e.worker, e.owner.id()).await - o1;
    assert!(got > near(18), "owner got {got}");
    let v = e.worker.view_account(&e.vault).await?;
    let stake = v.storage_usage as u128 * 10u128.pow(19);
    assert!(v.balance.as_yoctonear() - stake < near(1) / 1000, "vault left with storage stake only");
    Ok(())
}

#[tokio::test]
async fn owner_sells_pauses_rotates_and_removes_key() -> anyhow::Result<()> {
    let e = setup().await?;
    e.register(WRAP).await;
    e.register(MEME).await;
    e.worker
        .root_account()?
        .call(&MEME.parse()?, "mint")
        .args_json(json!({"account_id": e.vault, "amount": "1000"}))
        .transact()
        .await?
        .into_result()?;

    // pause, then sell while paused (owner action)
    assert!(event(&events(&e.owner_call("pause", json!({})).await.logs()), "paused").is_some());
    let sell = json!({"args": {"venue": "ref_v1", "amount_in": "1000", "min_out": "2000",
        "hops": [{"pool_id": "1", "token_in": MEME, "token_out": WRAP}]}});
    let r = e.owner_call("sell", sell).await;
    assert!(r.is_success(), "{r:?}");
    assert_eq!(ft_bal(&e.worker, WRAP, &e.vault).await, 2000);
    assert_eq!(ft_bal(&e.worker, MEME, &e.vault).await, 0);
    // sell needs exactly 1 yocto
    let r = e
        .owner
        .call(&e.vault, "sell")
        .args_json(json!({"args": {"venue": "dcl", "amount_in": "1", "min_out": "1",
        "hops": [{"pool_id": format!("{MEME}|{WRAP}|2000"), "token_in": MEME, "token_out": WRAP}]}}))
        .max_gas()
        .transact()
        .await?;
    assert!(r.is_failure());
    assert!(event(&events(&e.owner_call("unpause", json!({})).await.logs()), "unpaused").is_some());

    // rotate
    let new_sk = SecretKey::from_random(KeyType::ED25519);
    let r = e
        .owner_call("set_bot_key", json!({"public_key": new_sk.public_key(), "allowance": near(ALLOWANCE).to_string()}))
        .await;
    assert!(r.is_success(), "{r:?}");
    assert!(event(&events(&r.logs()), "bot_key_set").is_some());
    let keys = e.worker.view_access_keys(&e.vault).await?;
    assert_eq!(keys.len(), 1);
    assert_eq!(keys[0].public_key, new_sk.public_key());
    let old = e.bot.call(&e.vault, "register_token").args_json(json!({"token_id": RHEA})).gas(BOT_GAS).transact().await;
    denied(old, "AccessKeyNotFound", "old key");
    let bot2 = Account::from_secret_key(e.vault.clone(), new_sk, &e.worker);
    let r = bot2.call(&e.vault, "register_token").args_json(json!({"token_id": RHEA})).gas(BOT_GAS).transact().await?;
    assert!(r.is_success(), "new key works");

    // remove
    let r = e.owner_call("remove_bot_key", json!({})).await;
    assert!(r.is_success());
    assert!(event(&events(&r.logs()), "bot_key_removed").is_some());
    assert!(e.worker.view_access_keys(&e.vault).await?.is_empty());
    assert_eq!(e.state().await["bot_key"], Value::Null);
    let r = bot2.call(&e.vault, "swap").args_json(buy("dcl", MEME, 1, 1)).gas(BOT_GAS).transact().await;
    denied(r, "AccessKeyNotFound", "removed key");
    Ok(())
}

/// Measures what one swap takes from the bot key allowance (printed; run with --nocapture).
/// The allowance is charged for prepaid gas at the pessimistic price, so the worker should not
/// send max gas.
#[tokio::test]
async fn allowance_spend_per_swap() -> anyhow::Result<()> {
    let e = setup().await?;
    e.register(WRAP).await;
    e.register(MEME).await;
    let allow = |k: near_workspaces::types::AccessKey| match k.permission {
        AccessKeyPermission::FunctionCall(fc) => fc.allowance.unwrap().as_yoctonear(),
        _ => unreachable!(),
    };
    let pk = e.bot_sk.public_key();
    e.worker.fast_forward(5).await?; // let gas refunds of the setup calls land
    let a0 = allow(e.worker.view_access_key(&e.vault, &pk).await?);
    let r = e
        .bot
        .call(&e.vault, "swap")
        .args_json(buy("ref_v1", MEME, near(1), 1))
        .gas(near_workspaces::types::Gas::from_tgas(200))
        .transact()
        .await?;
    assert!(r.is_success(), "{r:?}");
    assert_eq!(ft_bal(&e.worker, MEME, &e.vault).await, near(2), "200 Tgas is enough for a buy");
    let a_now = allow(e.worker.view_access_key(&e.vault, &pk).await?);
    e.worker.fast_forward(5).await?;
    let a1 = allow(e.worker.view_access_key(&e.vault, &pk).await?);
    println!("allowance right after tx: -{} yocto", a0 as i128 - a_now as i128);
    let price = e.worker.gas_price().await?.as_yoctonear();
    println!(
        "gas price {price}, total gas burnt {} Tgas, allowance spent after refunds {} yocto",
        r.total_gas_burnt.as_tgas(),
        a0 as i128 - a1 as i128
    );
    let r = e
        .bot
        .call(&e.vault, "register_token")
        .args_json(json!({"token_id": RHEA}))
        .gas(near_workspaces::types::Gas::from_tgas(60))
        .transact()
        .await?;
    assert!(r.is_success(), "{r:?}");
    e.worker.fast_forward(5).await?;
    let a2 = allow(e.worker.view_access_key(&e.vault, &pk).await?);
    println!(
        "register_token: gas burnt {} Tgas, allowance spent after refunds {} yocto",
        r.total_gas_burnt.as_tgas(),
        a1 as i128 - a2 as i128
    );
    Ok(())
}
