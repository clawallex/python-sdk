# clawallex-sdk (Python)

Python SDK for the Clawallex Payment API. Requires Python 3.10+.

## Installation

```bash
pip install clawallex-sdk
```

## Quick Start

```python
from clawallex import ClawallexClient

# First run — SDK auto-resolves client_id via whoami/bootstrap
client = ClawallexClient.create(
    api_key="your-api-key",
    api_secret="your-api-secret",
    base_url="https://api.clawallex.com",
)

# ⬇️ Persist client.client_id to your config/database/env
# e.g. "ca_8f0d2c3e5a1b4c7d"
print(client.client_id)

# Subsequent runs — pass the stored client_id to skip network calls
client = ClawallexClient.create(
    api_key="your-api-key",
    api_secret="your-api-secret",
    base_url="https://api.clawallex.com",
    client_id="ca_8f0d2c3e5a1b4c7d",  # the value you persisted
)
```

## Client ID

`client_id` is your application's stable identity on Clawallex, separate from the API Key.

- You can rotate API Keys (revoke old, create new) without losing access to existing cards and transactions — just keep using the same `client_id`
- When a new API Key sends its first request with an existing `client_id`, the server auto-binds the new key to that identity
- Once bound, a `client_id` cannot be changed for that API Key (TOFU — Trust On First Use)
- Cards and transactions are isolated by `client_id` — different `client_id`s cannot see each other's data
- Wallet balance is shared at the user level (across all `client_id`s under the same user)

### Resolution

If `client_id` is provided at initialization, the SDK uses it directly (no network calls). If omitted, the SDK calls `GET /auth/whoami` — if already bound, uses the existing `client_id`; if not, calls `POST /auth/bootstrap` to generate and bind a new one.

### Best Practice

Persist the resolved `client_id` after the first initialization and pass it explicitly on subsequent sessions. This avoids unnecessary network calls and ensures identity continuity across API Key rotations.

### Data Isolation

| Scope | Isolation Level |
|-------|----------------|
| Wallet balance | User-level — shared across all `client_id`s under the same user |
| Cards | `client_id`-scoped — only visible to the `client_id` that created them |
| Transactions | `client_id`-scoped — only visible to the `client_id` that owns the card |
| Recharge addresses | User-level — shared |

## API

```python
# Wallet
client.wallet_detail()
client.recharge_addresses(wallet_id)

# X402 — chain_code defaults to "ETH" if omitted
client.x402_payee_address("USDC")
client.x402_asset_address("USDC", chain_code="BASE")

# Cards
client.new_card(params)
client.card_list(page=1, page_size=20)
client.card_balance(card_id)
client.card_details(card_id)

# Transactions
client.transaction_list(params)

# Refill
client.refill_card(card_id, params)
```

## Mode A — Wallet Funded Card

Mode A is the simplest path: cards are paid from your Clawallex wallet balance. No blockchain interaction needed.

### Create a Card

```python
import uuid
from clawallex import NewCardParams

order = client.new_card(NewCardParams(
    mode_code=100,          # Mode A
    card_type=100,          # 100=flash (single-use), 200=stream (rechargeable)
    amount="50.0000",       # card face value in USD
    client_request_id=str(uuid.uuid4()),  # idempotency key
))

# order.card_order_id — always present
# order.card_id       — present if card created synchronously
# order.status        — 200=active, 120=pending_async (issuer processing)
```

### Handling Async Card Creation (status=120)

Card creation may be asynchronous — the issuer accepts the request but hasn't finished yet. **This is normal**, not an error. The wallet has already been charged.

```python
import time

if order.status == 120 or not order.card_id:
    # Poll card list until the new card appears
    before = client.card_list(page=1, page_size=100)
    existing_ids = {c.card_id for c in before.data}

    card_id = None
    for _ in range(30):
        time.sleep(2)
        cards = client.card_list(page=1, page_size=100)
        new_cards = [c for c in cards.data if c.card_id not in existing_ids]
        if new_cards:
            card_id = new_cards[0].card_id
            break
```

> **Tip**: You can also retry `new_card` with the same `client_request_id`. The server will safely retry the issuer call without re-charging your wallet.

### Mode A Refill

```python
from clawallex import RefillCardParams

refill = client.refill_card(card_id, RefillCardParams(
    amount="30.0000",
    client_request_id=str(uuid.uuid4()),  # idempotency key for Mode A
))
```

## Fee Structure

Fees are calculated server-side. For Mode B, the 402 response breaks them down:

| Fee field | Applies to | Description |
|-----------|-----------|-------------|
| `issue_fee_amount` | All cards | One-time card issuance fee |
| `monthly_fee_amount` | Stream cards only | First month fee (included in initial charge) |
| `fx_fee_amount` | All cards | Foreign exchange fee |
| `fee_amount` | — | `= issue_fee_amount + monthly_fee_amount + fx_fee_amount` |
| `payable_amount` | — | `= amount + fee_amount` (total to pay) |

- Flash cards: `fee_amount = issue_fee + fx_fee`
- Stream cards: `fee_amount = issue_fee + monthly_fee + fx_fee`
- Mode A refill: **no fees** — the refill amount goes directly to the card
- Mode B refill: **no fees** — same as Mode A

## Mode B — x402 On-Chain Payment (Two-Step)

Mode B is for Agents that hold their own wallet and private key. The card is funded by an on-chain USDC transfer via the EIP-3009 `transferWithAuthorization` standard — no human intervention needed.

> **Mode B currently only supports USDC** (6 decimals) on ETH and BASE chains. `token_code` must be `"USDC"`.

### Flow

```
Agent → POST /card-orders (mode_code=200)     → 402 + quote details
Agent → sign EIP-3009 with private key
Agent → POST /card-orders (same client_request_id) → 200 + card created
```

### Stage 1 — Request Quote (402 is expected, not an error)

```python
import uuid
from clawallex import ClawallexClient, ClawallexPaymentRequiredError, NewCardParams

client = ClawallexClient.create(...)

client_request_id = str(uuid.uuid4())
details = None

try:
    client.new_card(NewCardParams(
        mode_code=200,
        card_type=200,           # 100=flash, 200=stream
        amount="200.0000",
        client_request_id=client_request_id,
        chain_code="ETH",       # or "BASE"
        token_code="USDC",
    ))
except ClawallexPaymentRequiredError as e:
    details = e.details
    # details is a dict containing:
    #   details["payee_address"]     — system receiving address
    #   details["asset_address"]     — USDC contract address
    #   details["payable_amount"]    — total including fees (e.g. "207.5900")
    #   details["x402_reference_id"] — must be echoed in Stage 2
    #   details["final_card_amount"], details["fee_amount"],
    #   details["issue_fee_amount"], details["monthly_fee_amount"], details["fx_fee_amount"]
```

### EIP-3009 Signing (using eth_account)

```python
import os, math, time
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_account.messages import encode_typed_data

account: LocalAccount = Account.from_key(PRIVATE_KEY)
max_amount_required = str(math.floor(float(details["payable_amount"]) * 1_000_000))
now = int(time.time())
nonce = "0x" + os.urandom(32).hex()

typed_data = {
    "types": {
        "EIP712Domain": [
            {"name": "name", "type": "string"},
            {"name": "version", "type": "string"},
            {"name": "chainId", "type": "uint256"},
            {"name": "verifyingContract", "type": "address"},
        ],
        "TransferWithAuthorization": [
            {"name": "from", "type": "address"},
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "validAfter", "type": "uint256"},
            {"name": "validBefore", "type": "uint256"},
            {"name": "nonce", "type": "bytes32"},
        ],
    },
    "primaryType": "TransferWithAuthorization",
    "domain": {
        "name": "USDC",                           # query via contract.name() — varies by chain
        "version": "2",
        "chainId": 11155111,                       # Sepolia; ETH mainnet=1, BASE=8453
        "verifyingContract": details["asset_address"],
    },
    "message": {
        "from": account.address,
        "to": details["payee_address"],
        "value": int(max_amount_required),
        "validAfter": now - 60,
        "validBefore": now + 3600,
        "nonce": nonce,
    },
}

signed = account.sign_typed_data(full_message=typed_data)
signature = signed.signature.hex()
```

> **Note**: The EIP-712 domain `name` depends on the USDC contract deployment.
> On Sepolia testnet it is `"USDC"`, on mainnet it may be `"USD Coin"`.
> Query the contract's `name()` method to confirm.

### Stage 2 — Submit Payment

> **IMPORTANT**: Stage 2 **must** use the same `client_request_id` as Stage 1.
> A different `client_request_id` will create a **new** card order instead of completing the current one.

The SDK provides typed helpers `X402Authorization`, `X402PaymentPayload`, and `X402PaymentRequirements` with a `.to_dict()` method for full structure support:

```python
from clawallex import X402PaymentPayload, X402PaymentRequirements, X402Authorization

authorization = X402Authorization(
    from_address=account.address,
    to=details["payee_address"],
    value=max_amount_required,
    valid_after=str(now - 60),
    valid_before=str(now + 3600),
    nonce=nonce,
)

payload = X402PaymentPayload(
    scheme="exact",
    network="ETH",
    signature=signature,
    authorization=authorization,
)

requirements = X402PaymentRequirements(
    scheme="exact",
    network="ETH",                               # must equal payload.network
    asset=details["asset_address"],              # must equal 402 asset_address
    pay_to=details["payee_address"],             # must equal authorization.to
    max_amount_required=max_amount_required,     # must equal authorization.value
    reference_id=details["x402_reference_id"],
)

order = client.new_card(NewCardParams(
    mode_code=200,
    card_type=200,
    amount="200.0000",
    client_request_id=client_request_id,          # MUST reuse from Stage 1
    x402_version=1,
    payment_payload=payload.to_dict(),
    payment_requirements=requirements.to_dict(),
    extra={"card_amount": details["final_card_amount"], "paid_amount": details["payable_amount"]},
    payer_address=account.address,
))
# order: { "card_order_id": ..., "card_id": ..., "status": ... }
```

### Mode B Refill (No 402 — Direct Submit)

Refill has **no 402 challenge**. Query addresses first, then submit directly:

```python
# 1. query addresses
payee = client.x402_payee_address("USDC", chain_code="ETH")
asset = client.x402_asset_address("USDC", chain_code="ETH")

# 2. sign EIP-3009 (same as above, but amount has no fee)
refill_amount = "30.0000"
max_amt = str(math.floor(float(refill_amount) * 1_000_000))
# ... sign with account ...

# 3. submit refill
refill = client.refill_card(card_id, RefillCardParams(
    amount=refill_amount,
    x402_reference_id=str(uuid.uuid4()),       # unique per refill
    x402_version=1,
    payment_payload=payload.to_dict(),
    payment_requirements=requirements.to_dict(),
    payer_address=account.address,
))
```

### Consistency Rules (Server Rejects if Any Fail)

| # | Rule |
|---|------|
| 1 | `payment_payload.network` == `payment_requirements.network` |
| 2 | `authorization.to` == `payTo` == 402 `payee_address` |
| 3 | `authorization.value` == `maxAmountRequired` == `payable_amount × 10^6` |
| 4 | `payment_requirements.asset` == 402 `asset_address` |
| 5 | `extra.referenceId` == 402 `x402_reference_id` |
| 6 | `extra.card_amount` == original `amount` |
| 7 | `extra.paid_amount` == 402 `payable_amount` |

## Card Details — Decrypting PAN/CVV

`card_details` returns encrypted sensitive data. The server encrypts with a key derived from your `api_secret`.

```python
import base64
import json
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

details = client.card_details(card_id)
enc = details.encrypted_sensitive_data
# enc.version = "v1", enc.algorithm = "AES-256-GCM", enc.kdf = "HKDF-SHA256"

# 1. Derive 32-byte key from api_secret using HKDF-SHA256
hkdf_kdf = HKDF(
    algorithm=hashes.SHA256(),
    length=32,
    salt=None,
    info=b"clawallex-card-sensitive-data",
)
derived_key = hkdf_kdf.derive(API_SECRET.encode())

# 2. Decrypt with AES-256-GCM
nonce = base64.b64decode(enc.nonce)
ciphertext = base64.b64decode(enc.ciphertext)

aesgcm = AESGCM(derived_key)
plaintext = aesgcm.decrypt(nonce, ciphertext, None)
card_data = json.loads(plaintext)

pan = card_data["pan"]   # "4111111111111111"
cvv = card_data["cvv"]   # "123"
```

> **Security**: Never log or persist the decrypted PAN/CVV in plaintext. The `api_secret` must be at least 16 bytes. Install: `pip install cryptography`.

## Error Handling

```python
from clawallex import ClawallexApiError, ClawallexPaymentRequiredError

try:
    client.new_card(params)
except ClawallexPaymentRequiredError as e:
    print(e.details)          # Mode B challenge — normal flow
except ClawallexApiError as e:
    print(e.status_code, e.code, e.message)
```

## Enums Reference

| Constant | Value | Description |
|----------|-------|-------------|
| `mode_code` | `100` | Mode A — wallet funded |
| `mode_code` | `200` | Mode B — x402 on-chain |
| `card_type` | `100` | Flash card |
| `card_type` | `200` | Stream card (subscription) |
| `card.status` | `200` | Active |
| `card.status` | `220` | Closing |
| `card.status` | `230` | Expired |
| `card.status` | `250` | Cancelled |
| `wallet.status` | `100` | Normal |
| `wallet.status` | `210` | Frozen |
