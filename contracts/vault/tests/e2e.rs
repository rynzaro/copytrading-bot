//! Sandbox end to end (ticket 0009): the web's owner transactions (tests/fixtures/web_plans.json, written by
//! web/src/lib/owner-txs.test.ts) signed against the real vault, the worker API confirming the vault, and one bot
//! buy by the worker's executor through the mock DEX.
//!
//! Needs `scripts/build.sh` first and `uv` (worker). Runs only with CTB_E2E=1 (`scripts/e2e.sh`), otherwise it
//! skips: `cargo test --workspace` stays independent of the worker toolchain.

use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};

use near_workspaces::network::Sandbox;
use near_workspaces::operations::Function;
use near_workspaces::result::ExecutionFinalResult;
use near_workspaces::types::{AccessKey, AccessKeyPermission, AccountDetailsPatch, Gas, KeyType, NearToken, SecretKey};
use near_workspaces::{Account, AccountId, Worker};
use serde_json::{json, Value};

const WRAP: &str = "wrap.near";
const RHEA: &str = "token.rhealab.near";
const DCL: &str = "dclv2.ref-labs.near";
const REF: &str = "v2.ref-finance.near";
const MEME: &str = "meme.near";

fn dir() -> &'static str {
    env!("CARGO_MANIFEST_DIR")
}

fn wasm(name: &str) -> Vec<u8> {
    let p = match name {
        "vault" => format!("{}/target/near/copytrade_vault.wasm", dir()),
        n => format!("{}/target/near/{n}/{n}.wasm", dir()),
    };
    std::fs::read(&p).unwrap_or_else(|_| panic!("{p} missing: run scripts/build.sh"))
}

fn near(n: u128) -> u128 {
    n * 10u128.pow(24)
}

async fn named(worker: &Worker<Sandbox>, id: &str, code: &[u8], init: Value) -> anyhow::Result<()> {
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
    Ok(())
}

async fn view(w: &Worker<Sandbox>, id: &str, f: &str, args: Value) -> Value {
    w.view(&id.parse().unwrap(), f).args_json(args).await.unwrap().json::<Value>().unwrap()
}

async fn ft_bal(w: &Worker<Sandbox>, token: &str, who: &str) -> u128 {
    view(w, token, "ft_balance_of", json!({"account_id": who})).await.as_str().unwrap().parse().unwrap()
}

/// Sign one web PlannedTx as the owner. `use_global_contract` becomes DeployContract of the same wasm:
/// near-workspaces can't build UseGlobalContract (README "Build and test").
async fn sign(owner: &Account, tx: &Value) -> ExecutionFinalResult {
    let recv: AccountId = tx["receiver_id"].as_str().unwrap().parse().unwrap();
    let mut b = owner.batch(&recv);
    for a in tx["actions"].as_array().unwrap() {
        let yocto = |k: &str| NearToken::from_yoctonear(a[k].as_str().unwrap().parse().unwrap());
        b = match a["kind"].as_str().unwrap() {
            "create_account" => b.create_account(),
            "transfer" => b.transfer(yocto("deposit")),
            "use_global_contract" => b.deploy(&wasm("vault")),
            "function_call" => b.call(
                Function::new(a["method"].as_str().unwrap())
                    .args_json(a["args"].clone())
                    .gas(Gas::from_gas(a["gas"].as_str().unwrap().parse().unwrap()))
                    .deposit(yocto("deposit")),
            ),
            k => panic!("unknown action {k}"),
        };
    }
    b.transact().await.unwrap()
}

struct WorkerProc {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
}

impl WorkerProc {
    fn cmd(&mut self, line: &str) -> Value {
        writeln!(self.stdin, "{line}").unwrap();
        self.stdin.flush().unwrap();
        let mut s = String::new();
        self.stdout.read_line(&mut s).unwrap();
        serde_json::from_str(&s).unwrap_or_else(|e| panic!("worker reply {s:?}: {e}"))
    }
}

impl Drop for WorkerProc {
    fn drop(&mut self) {
        let _ = writeln!(self.stdin, "quit");
        let _ = self.child.wait();
    }
}

#[tokio::test]
async fn web_plans_worker_api_and_bot_buy() -> anyhow::Result<()> {
    if std::env::var("CTB_E2E").as_deref() != Ok("1") {
        eprintln!("e2e skipped: run scripts/e2e.sh (CTB_E2E=1, needs uv)");
        return Ok(());
    }
    let fixture: Value =
        serde_json::from_str(&std::fs::read_to_string(format!("{}/tests/fixtures/web_plans.json", dir()))?)?;
    let w = near_workspaces::sandbox().await?;
    for t in [WRAP, RHEA, MEME] {
        named(&w, t, &wasm("mock_ft"), json!({})).await?;
    }
    for d in [DCL, REF] {
        named(&w, d, &wasm("mock_dex"), json!({"rate_num": "2", "rate_den": "1"})).await?;
        for t in [WRAP, MEME] {
            w.root_account()?
                .call(&t.parse()?, "mint")
                .args_json(json!({"account_id": d, "amount": near(1_000_000).to_string()}))
                .transact()
                .await?
                .into_result()?;
        }
    }
    let owner = w
        .root_account()?
        .create_subaccount("alice")
        .initial_balance(NearToken::from_near(100))
        .transact()
        .await?
        .into_result()?;
    assert_eq!(owner.id().as_str(), fixture["owner"], "fixture owner is the sandbox account");
    let vault = fixture["vault"].as_str().unwrap().to_string();

    // Worker: real API + executor against this sandbox.
    let data = std::env::temp_dir().join(format!("ctb-e2e-{}", std::process::id()));
    std::fs::create_dir_all(&data)?;
    let mut child = Command::new("uv")
        .args(["run", "--frozen", "python", "scripts/e2e_sandbox.py", "--rpc", &w.rpc_addr(), "--owner"])
        .arg(owner.id().as_str())
        .arg("--data")
        .arg(&data)
        .arg("--wasm")
        .arg(format!("{}/target/near/copytrade_vault.wasm", dir()))
        .current_dir(format!("{}/../../worker", dir()))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()?;
    let stdin = child.stdin.take().unwrap();
    let stdout = BufReader::new(child.stdout.take().unwrap());
    let mut wp = WorkerProc { child, stdin, stdout };

    let start = wp.cmd("start");
    let pk = start["public_key"].as_str().unwrap().to_string();
    assert!(pk.starts_with("ed25519:"));
    let confirm = format!("confirm {vault}");
    assert_ne!(wp.cmd(&confirm)["status"], 200, "no vault yet");

    // The web plans with the worker's real bot key in place of the placeholder.
    let text = serde_json::to_string(&fixture["steps"])?.replace(fixture["bot_key"].as_str().unwrap(), &pk);
    let steps: Value = serde_json::from_str(&text)?;
    let step = |name: &str| steps[name].clone();

    // 1. Setup: one owner tx.
    let r = sign(&owner, &step("setup")).await;
    assert!(r.is_success(), "setup: {r:?}");
    let keys = w.view_access_keys(&vault.parse()?).await?;
    assert_eq!(keys.len(), 1, "only the bot key");
    assert_eq!(keys[0].public_key.to_string(), pk);

    // 2. Worker confirms: owner, bot key, methods, pinned code hash. Then the bot registers wNEAR.
    // The worker reads at finality "final", a few blocks behind: retry like the web does.
    let mut c = Value::Null;
    for _ in 0..10 {
        c = wp.cmd(&confirm);
        if c["status"] == 200 {
            break;
        }
        tokio::time::sleep(std::time::Duration::from_secs(1)).await;
    }
    assert_eq!(c, json!({"status": 200, "body": {"ok": true}}), "{c}");
    let reg = view(&w, WRAP, "storage_balance_of", json!({"account_id": vault})).await;
    assert!(!reg.is_null(), "bot registered the vault on wNEAR after confirm");

    // 3. Deposit.
    let before = w.view_account(&vault.parse()?).await?.balance.as_yoctonear();
    assert!(sign(&owner, &step("deposit")).await.is_success());
    let got = w.view_account(&vault.parse()?).await?.balance.as_yoctonear() - before;
    assert!(got >= near(5) && got < near(5) + near(1) / 10, "deposit 5 NEAR (+ late gas refunds): {got}");

    // 4. One bot buy by the worker executor (DRY_RUN off): register meme, swap 1 NEAR, 2:1 mock.
    let b = wp.cmd(&format!("buy {MEME} {}", near(1)));
    assert_eq!(b["result"], "filled", "{b}");
    assert_eq!(b["row"]["amount_out"], near(2).to_string());
    assert_eq!(ft_bal(&w, MEME, &vault).await, near(2));

    // 5. Pause / unpause.
    assert!(sign(&owner, &step("pause")).await.is_success());
    assert_eq!(view(&w, &vault, "get_state", json!({})).await["paused"], true);
    assert!(sign(&owner, &step("unpause")).await.is_success());
    assert_eq!(view(&w, &vault, "get_state", json!({})).await["paused"], false);

    // 6. Refill: allowance back to 1 NEAR.
    assert!(sign(&owner, &step("refill")).await.is_success());
    let k = w.view_access_key(&vault.parse()?, &pk.parse()?).await?;
    match k.permission {
        AccessKeyPermission::FunctionCall(fc) => assert_eq!(fc.allowance, Some(NearToken::from_near(1))),
        p => panic!("{p:?}"),
    }

    // 7. Sell 1 meme -> 2 wNEAR.
    let r = sign(&owner, &step("sell")).await;
    assert!(r.is_success(), "sell: {r:?}");
    assert_eq!(ft_bal(&w, MEME, &vault).await, near(1));
    assert_eq!(ft_bal(&w, WRAP, &vault).await, near(2));

    // 8. Withdraw token (all) to the owner, registered on meme first.
    w.root_account()?
        .call(&MEME.parse()?, "mint")
        .args_json(json!({"account_id": owner.id(), "amount": "0"}))
        .transact()
        .await?
        .into_result()?;
    assert!(sign(&owner, &step("withdraw_token")).await.is_success());
    assert_eq!(ft_bal(&w, MEME, owner.id().as_str()).await, near(1));
    assert_eq!(ft_bal(&w, MEME, &vault).await, 0);

    // 9. Withdraw 1 NEAR.
    let ob = w.view_account(owner.id()).await?.balance.as_yoctonear();
    assert!(sign(&owner, &step("withdraw_near")).await.is_success());
    let oa = w.view_account(owner.id()).await?.balance.as_yoctonear();
    assert!(oa + near(1) / 100 > ob + near(1), "owner got ~1 NEAR (minus gas)");

    // 10. Remove the bot key: no keys left.
    assert!(sign(&owner, &step("remove_bot_key")).await.is_success());
    assert!(w.view_access_keys(&vault.parse()?).await?.is_empty());
    assert!(view(&w, &vault, "get_state", json!({})).await["bot_key"].is_null());

    // 11. Withdraw all: only the storage stake stays (code ~2.2 NEAR with DeployContract).
    assert!(sign(&owner, &step("withdraw_all")).await.is_success());
    assert_eq!(ft_bal(&w, WRAP, &vault).await, 0, "wNEAR unwrapped and sent");
    let left = w.view_account(&vault.parse()?).await?.balance.as_yoctonear();
    assert!(left < near(3), "left {left}");

    drop(wp);
    let _ = std::fs::remove_dir_all(&data);
    Ok(())
}
