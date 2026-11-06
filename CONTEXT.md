# Context: copytrading-bot

Hosted web app. Users copy meme-token buys of chosen whale wallets on NEAR mainnet.

## Terms

- **Owner wallet**: the user's own NEAR account. The user logs in with it and withdraws with it. The app never holds its key.
- **Trading wallet**: the account the bot trades from for one user. The owner wallet controls it. It is a vault: it swaps only through allowed DEXes, and only the owner wallet can withdraw or pause.
- **Bot key**: the function-call key the server holds for one trading wallet. It can only ask the vault to swap.
- **Created wallet**: an owner wallet made in the browser for a user without one. The user sees the seed phrase once. Only as safe as the served page.
- **Copy target**: a whale wallet whose meme buys a user copies.
- **Curated list**: the copy targets the app suggests, each with a research note. A user can also enter any account.
- **Signal**: one meme swap buy by a copy target, seen in the feed after it executes. Only signals trigger copy buys.
- **Limit-order alert**: a notice that a copy target placed a DCL limit buy order. No copy buy follows.
- **Copy buy**: the bot's buy for one user after a signal. At most one per token per user, ever. Tokens the user holds from elsewhere don't block it.
- **Meme token**: any token that is not on the non-meme denylist (wNEAR, stables, LSTs, RHEA/REF, BRRR, bridged majors).
- **Quote token**: the token the bot pays with. v1: wNEAR.
- **Size ladder**: copy-buy size by market cap band, set by the user.
- **Price guard**: the most the bot pays, as a multiple of the copy target's fill price. Optional; the UI encourages it.
- **Slippage**: the most the bot pays above the current pool price. Required; the user may choose unlimited.
- **Exit**: the rule that sells a copied position. Not in v1: users sell by hand.
- **Host**: the person who runs the server. Treated as possibly malicious for trading power.
- **Still-active push**: an optional reminder that the user's bot key still works on their trading wallet.
- **Self-host**: a user runs the app from a clone of the public repo, with their own bot key.
- **Gas allowance**: the NEAR the bot key may spend on gas. When it runs out, the bot stops until the owner refills it.
- **Storage payment**: the small NEAR the vault pays so it can hold a new token. The user is notified each time.
