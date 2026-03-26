"""Clawallex Payment API SDK"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import requests

# ─── Exceptions ───────────────────────────────────────────────────────────────


class ClawallexApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message

    def __repr__(self) -> str:
        return f"ClawallexApiError(status={self.status_code}, code={self.code!r}, message={self.message!r})"


class ClawallexPaymentRequiredError(Exception):
    """Raised when Mode B card order returns HTTP 402.

    ``details`` contains the payment challenge fields:
    ``payee_address``, ``asset_address``, ``payable_amount``,
    ``x402_reference_id``, fee breakdown, etc.
    """

    def __init__(self, code: str, message: str, details: dict[str, Any]) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: dict[str, Any] = details

    def __repr__(self) -> str:
        return f"ClawallexPaymentRequiredError(code={self.code!r}, details={self.details})"


# ─── Constants ────────────────────────────────────────────────────────────────


class ModeCode:
    """Funding source for card creation."""
    WALLET = 100  # Mode A: deduct from wallet balance
    X402   = 200  # Mode B: on-chain x402 USDC payment


class CardType:
    """Card lifecycle."""
    FLASH  = 100  # One-time use, auto-destroyed after a single transaction
    STREAM = 200  # Reloadable, suitable for recurring payments


# ─── Request / Response Types ─────────────────────────────────────────────────


@dataclass
class WalletDetail:
    wallet_id: str
    wallet_type: int
    currency: str
    available_balance: str
    frozen_balance: str
    low_balance_threshold: str
    status: int
    updated_at: str


@dataclass
class RechargeAddress:
    recharge_address_id: str
    wallet_id: str
    chain_code: str
    token_code: str
    address: str
    memo_tag: str
    status: int
    updated_at: str


@dataclass
class RechargeAddressesResponse:
    wallet_id: str
    total: int
    data: list[RechargeAddress]


@dataclass
class PayeeAddressResponse:
    chain_code: str
    token_code: str
    address: str


@dataclass
class AssetAddressResponse:
    chain_code: str
    token_code: str
    asset_address: str


@dataclass
class X402Authorization:
    """EIP-3009 ``transferWithAuthorization`` fields.

    See https://eips.ethereum.org/EIPS/eip-3009
    """
    from_address: str   # ``from`` — agent wallet address (payer)
    to: str             # must equal 402 ``payee_address``
    value: str          # ``payable_amount × 10^decimals`` (USDC=6, e.g. ``"207590000"``)
    valid_after: str    # unix seconds, recommended ``now - 60``
    valid_before: str   # unix seconds, recommended ``now + 3600``
    nonce: str          # random 32-byte hex with ``0x`` prefix

    def to_dict(self) -> dict[str, str]:
        return {
            "from": self.from_address, "to": self.to, "value": self.value,
            "validAfter": self.valid_after, "validBefore": self.valid_before,
            "nonce": self.nonce,
        }


@dataclass
class X402PaymentPayload:
    """x402 payment payload — wraps the EIP-3009 signature + authorization."""
    scheme: str                      # fixed ``"exact"``
    network: str                     # ``"ETH"`` / ``"BASE"``
    signature: str                   # EIP-3009 typed-data signature hex
    authorization: X402Authorization

    def to_dict(self) -> dict[str, Any]:
        return {
            "scheme": self.scheme, "network": self.network,
            "payload": {
                "signature": self.signature,
                "authorization": self.authorization.to_dict(),
            },
        }


@dataclass
class X402PaymentRequirements:
    """x402 payment requirements — what the payment must satisfy."""
    scheme: str              # fixed ``"exact"``
    network: str             # must equal ``payment_payload.network``
    asset: str               # token contract address — must equal 402 ``asset_address``
    pay_to: str              # must equal 402 ``payee_address`` and ``authorization.to``
    max_amount_required: str # must equal ``authorization.value``
    reference_id: str        # must equal 402 ``x402_reference_id``

    def to_dict(self) -> dict[str, Any]:
        return {
            "scheme": self.scheme, "network": self.network,
            "asset": self.asset, "payTo": self.pay_to,
            "maxAmountRequired": self.max_amount_required,
            "extra": {"referenceId": self.reference_id},
        }


@dataclass
class NewCardParams:
    mode_code: int
    card_type: int
    amount: str
    client_request_id: str
    fee_amount: Optional[str] = None
    issuer_card_currency: Optional[str] = None
    tx_limit: Optional[str] = None
    allowed_mcc: Optional[str] = None
    blocked_mcc: Optional[str] = None
    chain_code: Optional[str] = None          # Mode B Stage 1
    token_code: Optional[str] = None          # Mode B Stage 1
    x402_reference_id: Optional[str] = None
    x402_version: Optional[int] = None        # Mode B Stage 2: fixed ``1``
    payment_payload: Optional[dict[str, Any]] = None       # Mode B Stage 2: use ``X402PaymentPayload.to_dict()``
    payment_requirements: Optional[dict[str, Any]] = None  # Mode B Stage 2: use ``X402PaymentRequirements.to_dict()``
    extra: Optional[dict[str, str]] = None    # Mode B Stage 2: ``{"card_amount": ..., "paid_amount": ...}``
    payer_address: Optional[str] = None


@dataclass
class CardOrderResponse:
    card_order_id: str
    status: int
    card_id: Optional[str] = None
    reference_id: Optional[str] = None
    idempotent: Optional[bool] = None


@dataclass
class Card:
    card_id: str
    mode_code: int
    card_type: int
    status: int
    masked_pan: str
    card_currency: str
    available_balance: str
    expiry_month: int
    expiry_year: int
    issuer_card_status: str
    updated_at: str


@dataclass
class CardListResponse:
    total: int
    page: int
    page_size: int
    data: list[Card]


@dataclass
class CardBalanceResponse:
    card_id: str
    card_currency: str
    available_balance: str
    status: int
    updated_at: str


@dataclass
class EncryptedSensitiveData:
    version: str
    algorithm: str
    kdf: str
    nonce: str
    ciphertext: str


@dataclass
class CardDetailsResponse:
    card_id: str
    masked_pan: str
    encrypted_sensitive_data: EncryptedSensitiveData
    expiry_month: int
    expiry_year: int
    tx_limit: str
    allowed_mcc: str
    blocked_mcc: str
    card_currency: str
    available_balance: str
    first_name: str
    last_name: str
    delivery_address: str  # billing address JSON or text
    status: int
    issuer_card_status: str
    updated_at: str


@dataclass
class TransactionListParams:
    card_tx_id: Optional[str] = None
    issuer_tx_id: Optional[str] = None
    card_id: Optional[str] = None
    page: Optional[int] = None
    page_size: Optional[int] = None


@dataclass
class Transaction:
    card_id: str
    card_tx_id: str
    issuer_tx_id: str
    issuer_ori_tx_id: str
    action_type: int
    tx_type: int
    process_status: str
    amount: str
    fee_amount: str
    fee_currency: str
    billing_amount: str
    billing_currency: str
    transaction_amount: str
    transaction_currency: str
    status: int
    card_fund_applied: int
    is_in_progress: int
    merchant_name: str
    mcc: str
    decline_reason: str
    description: str
    issuer_card_available_balance: str
    occurred_at: str
    settled_at: Optional[str]
    webhook_event_id: str


@dataclass
class TransactionListResponse:
    card_tx_id: str
    issuer_tx_id: str
    card_id: str
    page: int
    page_size: int
    total: int
    data: list[Transaction]


@dataclass
class UpdateCardParams:
    client_request_id: str
    tx_limit: Optional[str] = None
    allowed_mcc: Optional[str] = None
    blocked_mcc: Optional[str] = None


@dataclass
class UpdateCardResponse:
    card_id: str
    card_order_id: str
    status: str


@dataclass
class BatchCardBalanceResponse:
    data: list[CardBalanceResponse]


@dataclass
class RefillCardParams:
    amount: str
    client_request_id: Optional[str] = None           # Mode A: idempotency key
    x402_reference_id: Optional[str] = None            # Mode B: idempotency key (unique per refill)
    x402_version: Optional[int] = None                 # Mode B: fixed ``1``
    payment_payload: Optional[dict[str, Any]] = None   # Mode B: use ``X402PaymentPayload.to_dict()``
    payment_requirements: Optional[dict[str, Any]] = None  # Mode B: use ``X402PaymentRequirements.to_dict()``
    payer_address: Optional[str] = None


@dataclass
class RefillResponse:
    card_id: str
    refill_order_id: str
    refilled_amount: str
    status: str
    related_transfer_id: Optional[str] = None
    x402_payment_id: Optional[str] = None


# ─── HTTP Client ──────────────────────────────────────────────────────────────


class _HttpClient:
    _BASE_PATH = "/api/v1"

    def __init__(self, api_key: str, api_secret: str, base_url: str, client_id: str) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._base_url = base_url.rstrip("/")
        self._client_id = client_id
        self._session = requests.Session()

    def _sign(self, method: str, path: str, body: str, include_client_id: bool = True) -> dict[str, str]:
        timestamp = str(int(time.time()))
        body_hash = hashlib.sha256(body.encode()).hexdigest()
        canonical = f"{method}\n{path}\n{timestamp}\n{body_hash}"
        signature = hmac.new(
            self._api_secret.encode(),
            canonical.encode(),
            hashlib.sha256,
        ).digest()
        sig_b64 = base64.b64encode(signature).decode()
        headers = {
            "X-API-Key": self._api_key,
            "X-Timestamp": timestamp,
            "X-Signature": sig_b64,
            "Content-Type": "application/json",
        }
        if include_client_id:
            headers["X-Client-Id"] = self._client_id
        return headers

    def _handle_response(self, resp: requests.Response) -> Any:
        if resp.status_code == 402:
            try:
                body = resp.json()
                raise ClawallexPaymentRequiredError(
                    code=body.get("code", "PAYMENT_REQUIRED"),
                    message=body.get("message", "Payment required"),
                    details=body.get("details", {}),
                )
            except (ValueError, KeyError):
                raise ClawallexPaymentRequiredError("PAYMENT_REQUIRED", "Payment required", {})
        if not resp.ok:
            try:
                body = resp.json()
                raise ClawallexApiError(resp.status_code, body.get("code", "UNKNOWN_ERROR"), body.get("message", resp.text))
            except ValueError:
                raise ClawallexApiError(resp.status_code, "UNKNOWN_ERROR", resp.text)
        return resp.json()

    def get(self, path: str, params: Optional[dict[str, Any]] = None, auth: bool = False) -> Any:
        full_path = f"{self._BASE_PATH}{path}"
        url = f"{self._base_url}{full_path}"
        headers = self._sign("GET", full_path, "", include_client_id=not auth)
        resp = self._session.get(url, headers=headers, params={k: v for k, v in (params or {}).items() if v is not None})
        return self._handle_response(resp)

    def post(self, path: str, body: Any, auth: bool = False) -> Any:
        full_path = f"{self._BASE_PATH}{path}"
        url = f"{self._base_url}{full_path}"
        raw = json.dumps(body)
        headers = self._sign("POST", full_path, raw, include_client_id=not auth)
        resp = self._session.post(url, headers=headers, data=raw)
        return self._handle_response(resp)


# ─── Client ───────────────────────────────────────────────────────────────────


class ClawallexClient:
    """Synchronous Clawallex Payment API client.

    Use the ``create`` class method to instantiate:

    .. code-block:: python

        client = ClawallexClient.create(
            api_key="...",
            api_secret="...",
            base_url="https://...",
        )
    """

    def __init__(self, http: _HttpClient, client_id: str) -> None:
        self._http = http
        self.client_id = client_id

    @classmethod
    def create(
        cls,
        api_key: str,
        api_secret: str,
        base_url: str,
        client_id: Optional[str] = None,
    ) -> "ClawallexClient":
        """Create a fully initialised client.

        - If *client_id* is provided it is used directly.
        - Otherwise calls ``GET /auth/whoami``; if already bound uses the
          existing ``bound_client_id``, else calls ``POST /auth/bootstrap``.
        """
        http = _HttpClient(api_key, api_secret, base_url, client_id or "")
        if client_id:
            http._client_id = client_id
            return cls(http, client_id)

        whoami = http.get("/auth/whoami", auth=True)
        if whoami.get("client_id_bound"):
            resolved = whoami["bound_client_id"]
        else:
            bootstrap = http.post("/auth/bootstrap", {}, auth=True)
            resolved = bootstrap["client_id"]

        http._client_id = resolved
        return cls(http, resolved)

    # ── Wallet ────────────────────────────────────────────────────────────────

    def wallet_detail(self) -> WalletDetail:
        d = self._http.get("/payment/wallets/detail")
        return WalletDetail(**d)

    def recharge_addresses(self, wallet_id: str) -> RechargeAddressesResponse:
        d = self._http.get(f"/payment/wallets/{wallet_id}/recharge-addresses")
        return RechargeAddressesResponse(
            wallet_id=d["wallet_id"],
            total=d["total"],
            data=[RechargeAddress(**a) for a in d["data"]],
        )

    # ── X402 ──────────────────────────────────────────────────────────────────

    def x402_payee_address(self, token_code: str, chain_code: str = "ETH") -> PayeeAddressResponse:
        d = self._http.get("/payment/x402/payee-address", {"chain_code": chain_code, "token_code": token_code})
        return PayeeAddressResponse(**d)

    def x402_asset_address(self, token_code: str, chain_code: str = "ETH") -> AssetAddressResponse:
        d = self._http.get("/payment/x402/asset-address", {"chain_code": chain_code, "token_code": token_code})
        return AssetAddressResponse(**d)

    # ── Cards ─────────────────────────────────────────────────────────────────

    def new_card(self, params: NewCardParams) -> CardOrderResponse:
        """Create a card order.

        For Mode B, the first call raises ``ClawallexPaymentRequiredError``.
        Use ``error.details`` to build the x402 payment, then call again with
        the same ``client_request_id`` and the payment fields filled in.
        """
        body = {k: v for k, v in vars(params).items() if v is not None}
        d = self._http.post("/payment/card-orders", body)
        return CardOrderResponse(**{k: v for k, v in d.items() if k in CardOrderResponse.__dataclass_fields__})

    def card_list(self, page: Optional[int] = None, page_size: Optional[int] = None) -> CardListResponse:
        d = self._http.get("/payment/cards", {"page": page, "page_size": page_size})
        return CardListResponse(
            total=d["total"],
            page=d["page"],
            page_size=d["page_size"],
            data=[Card(**c) for c in d["data"]],
        )

    def card_balance(self, card_id: str) -> CardBalanceResponse:
        d = self._http.get(f"/payment/cards/{card_id}/balance")
        return CardBalanceResponse(**d)

    def card_details(self, card_id: str) -> CardDetailsResponse:
        d = self._http.get(f"/payment/cards/{card_id}/details")
        enc = EncryptedSensitiveData(**d["encrypted_sensitive_data"])
        return CardDetailsResponse(
            card_id=d["card_id"],
            masked_pan=d["masked_pan"],
            encrypted_sensitive_data=enc,
            expiry_month=d["expiry_month"],
            expiry_year=d["expiry_year"],
            tx_limit=d.get("tx_limit", ""),
            allowed_mcc=d.get("allowed_mcc", ""),
            blocked_mcc=d.get("blocked_mcc", ""),
            card_currency=d["card_currency"],
            available_balance=d["available_balance"],
            first_name=d.get("first_name", ""),
            last_name=d.get("last_name", ""),
            delivery_address=d.get("delivery_address", ""),
            status=d["status"],
            issuer_card_status=d["issuer_card_status"],
            updated_at=d["updated_at"],
        )

    def batch_card_balances(self, card_ids: list[str]) -> BatchCardBalanceResponse:
        d = self._http.post("/payment/cards/balances", {"card_ids": card_ids})
        return BatchCardBalanceResponse(
            data=[CardBalanceResponse(**item) for item in d["data"]],
        )

    def update_card(self, card_id: str, params: UpdateCardParams) -> UpdateCardResponse:
        body = {k: v for k, v in vars(params).items() if v is not None}
        d = self._http.post(f"/payment/cards/{card_id}/update", body)
        return UpdateCardResponse(**{k: v for k, v in d.items() if k in UpdateCardResponse.__dataclass_fields__})

    # ── Transactions ──────────────────────────────────────────────────────────

    def transaction_list(self, params: Optional[TransactionListParams] = None) -> TransactionListResponse:
        q: dict[str, Any] = {}
        if params:
            if params.card_tx_id: q["card_tx_id"] = params.card_tx_id
            if params.issuer_tx_id: q["issuer_tx_id"] = params.issuer_tx_id
            if params.card_id: q["card_id"] = params.card_id
            if params.page is not None: q["page"] = params.page
            if params.page_size is not None: q["page_size"] = params.page_size
        d = self._http.get("/payment/transactions", q)
        return TransactionListResponse(
            card_tx_id=d.get("card_tx_id", ""),
            issuer_tx_id=d.get("issuer_tx_id", ""),
            card_id=d.get("card_id", ""),
            page=d["page"],
            page_size=d["page_size"],
            total=d["total"],
            data=[Transaction(**{k: v for k, v in t.items() if k in Transaction.__dataclass_fields__}) for t in d["data"]],
        )

    # ── Refill ────────────────────────────────────────────────────────────────

    def refill_card(self, card_id: str, params: RefillCardParams) -> RefillResponse:
        body = {k: v for k, v in vars(params).items() if v is not None}
        d = self._http.post(f"/payment/cards/{card_id}/refill", body)
        return RefillResponse(**{k: v for k, v in d.items() if k in RefillResponse.__dataclass_fields__})


__all__ = [
    "ClawallexClient",
    "ClawallexApiError",
    "ClawallexPaymentRequiredError",
    "WalletDetail",
    "RechargeAddress",
    "RechargeAddressesResponse",
    "PayeeAddressResponse",
    "AssetAddressResponse",
    "X402Authorization",
    "X402PaymentPayload",
    "X402PaymentRequirements",
    "NewCardParams",
    "CardOrderResponse",
    "Card",
    "CardListResponse",
    "CardBalanceResponse",
    "EncryptedSensitiveData",
    "CardDetailsResponse",
    "TransactionListParams",
    "Transaction",
    "TransactionListResponse",
    "UpdateCardParams",
    "UpdateCardResponse",
    "BatchCardBalanceResponse",
    "RefillCardParams",
    "RefillResponse",
]
