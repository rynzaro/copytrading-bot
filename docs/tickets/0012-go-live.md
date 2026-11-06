# 0012: Mainnet go-live

Each step needs the owner's explicit OK.

1. Dry run on the VPS: DRY_RUN=1, live feeds, 24 h. Check signals vs the targets' real buys; check plans and skip reasons.
2. Deploy one vault on a test owner wallet (normal deploy, ~1 NEAR locked + < 1 NEAR setup). Small deposit (amount set by owner).
3. One live copy buy with a small ladder size. Verify: output in vault, activity, notifications, Sell and Withdraw from the web.
4. Pause, unpause, remove bot key from the web.
5. Only then: publish the global contract (~10 NEAR per 100 KB, burned) and open to other users.
