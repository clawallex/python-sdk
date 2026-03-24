import os
import unittest

from clawallex import (
    ClawallexClient,
    ClawallexApiError,
    ClawallexPaymentRequiredError,
    NewCardParams,
    TransactionListParams,
)

API_KEY = os.environ.get("CLAWALLEX_API_KEY")
API_SECRET = os.environ.get("CLAWALLEX_API_SECRET")
BASE_URL = os.environ.get("CLAWALLEX_BASE_URL")

SKIP = not all([API_KEY, API_SECRET, BASE_URL])
SKIP_REASON = "Set CLAWALLEX_API_KEY, CLAWALLEX_API_SECRET, CLAWALLEX_BASE_URL to run"


@unittest.skipIf(SKIP, SKIP_REASON)
class TestAuth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = ClawallexClient.create(
            api_key=API_KEY, api_secret=API_SECRET, base_url=BASE_URL,
        )

    def test_client_id_resolved(self):
        self.assertIsNotNone(self.client.client_id)
        self.assertIsInstance(self.client.client_id, str)
        self.assertTrue(len(self.client.client_id) > 0)

    def test_second_create_reuses_client_id(self):
        client2 = ClawallexClient.create(
            api_key=API_KEY, api_secret=API_SECRET, base_url=BASE_URL,
        )
        self.assertEqual(client2.client_id, self.client.client_id)

    def test_explicit_client_id(self):
        client3 = ClawallexClient.create(
            api_key=API_KEY, api_secret=API_SECRET, base_url=BASE_URL,
            client_id=self.client.client_id,
        )
        self.assertEqual(client3.client_id, self.client.client_id)


@unittest.skipIf(SKIP, SKIP_REASON)
class TestWallet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = ClawallexClient.create(
            api_key=API_KEY, api_secret=API_SECRET, base_url=BASE_URL,
        )
        cls.wallet = cls.client.wallet_detail()

    def test_wallet_detail_fields(self):
        w = self.wallet
        self.assertTrue(w.wallet_id)
        self.assertIsInstance(w.wallet_type, int)
        self.assertTrue(w.currency)
        self.assertIsNotNone(w.available_balance)
        self.assertIsInstance(w.status, int)
        self.assertTrue(w.updated_at)

    def test_recharge_addresses(self):
        result = self.client.recharge_addresses(self.wallet.wallet_id)
        self.assertEqual(result.wallet_id, self.wallet.wallet_id)
        self.assertIsInstance(result.total, int)
        self.assertIsInstance(result.data, list)
        if result.data:
            addr = result.data[0]
            self.assertTrue(addr.chain_code)
            self.assertTrue(addr.token_code)
            self.assertTrue(addr.address)


@unittest.skipIf(SKIP, SKIP_REASON)
class TestX402(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = ClawallexClient.create(
            api_key=API_KEY, api_secret=API_SECRET, base_url=BASE_URL,
        )

    def test_payee_address_default_chain(self):
        result = self.client.x402_payee_address("USDC")
        self.assertTrue(result.address)
        self.assertTrue(result.chain_code)
        self.assertEqual(result.token_code, "USDC")

    def test_payee_address_explicit_chain(self):
        result = self.client.x402_payee_address("USDC", chain_code="ETH")
        self.assertTrue(result.address)
        self.assertEqual(result.chain_code, "ETH")

    def test_asset_address_default_chain(self):
        result = self.client.x402_asset_address("USDC")
        self.assertTrue(result.asset_address)
        self.assertEqual(result.token_code, "USDC")

    def test_asset_address_explicit_chain(self):
        result = self.client.x402_asset_address("USDC", chain_code="ETH")
        self.assertTrue(result.asset_address)
        self.assertEqual(result.chain_code, "ETH")


@unittest.skipIf(SKIP, SKIP_REASON)
class TestCards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = ClawallexClient.create(
            api_key=API_KEY, api_secret=API_SECRET, base_url=BASE_URL,
        )
        cls.card_list = cls.client.card_list(page=1, page_size=5)

    def test_card_list_pagination(self):
        cl = self.card_list
        self.assertIsInstance(cl.total, int)
        self.assertEqual(cl.page, 1)
        self.assertEqual(cl.page_size, 5)
        self.assertIsInstance(cl.data, list)

    def test_card_list_defaults(self):
        result = self.client.card_list()
        self.assertIsInstance(result.total, int)
        self.assertIsInstance(result.data, list)

    def test_card_list_fields(self):
        if not self.card_list.data:
            self.skipTest("no cards")
        card = self.card_list.data[0]
        self.assertTrue(card.card_id)
        self.assertIsInstance(card.mode_code, int)
        self.assertIsInstance(card.card_type, int)
        self.assertTrue(card.masked_pan)
        self.assertTrue(card.card_currency)

    def test_card_balance(self):
        if not self.card_list.data:
            self.skipTest("no cards")
        card = self.card_list.data[0]
        balance = self.client.card_balance(card.card_id)
        self.assertEqual(balance.card_id, card.card_id)
        self.assertTrue(balance.card_currency)
        self.assertIsNotNone(balance.available_balance)
        self.assertIsInstance(balance.status, int)

    def test_card_details(self):
        if not self.card_list.data:
            self.skipTest("no cards")
        card = self.card_list.data[0]
        details = self.client.card_details(card.card_id)
        self.assertEqual(details.card_id, card.card_id)
        self.assertTrue(details.masked_pan)
        self.assertEqual(details.encrypted_sensitive_data.version, "v1")
        self.assertEqual(details.encrypted_sensitive_data.algorithm, "AES-256-GCM")
        self.assertTrue(details.encrypted_sensitive_data.ciphertext)

    def test_card_balance_not_found(self):
        with self.assertRaises(ClawallexApiError) as ctx:
            self.client.card_balance("non_existent_card_id")
        self.assertGreaterEqual(ctx.exception.status_code, 400)


@unittest.skipIf(SKIP, SKIP_REASON)
class TestTransactions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = ClawallexClient.create(
            api_key=API_KEY, api_secret=API_SECRET, base_url=BASE_URL,
        )
        cls.card_list = cls.client.card_list(page=1, page_size=5)

    def test_transaction_list_pagination(self):
        result = self.client.transaction_list(TransactionListParams(page=1, page_size=5))
        self.assertIsInstance(result.total, int)
        self.assertIsInstance(result.data, list)

    def test_transaction_list_default(self):
        result = self.client.transaction_list()
        self.assertIsInstance(result.data, list)

    def test_transaction_list_fields(self):
        result = self.client.transaction_list(TransactionListParams(page=1, page_size=5))
        if not result.data:
            self.skipTest("no transactions")
        tx = result.data[0]
        self.assertTrue(tx.card_id)
        self.assertTrue(tx.card_tx_id)
        self.assertIsInstance(tx.action_type, int)
        self.assertIsInstance(tx.status, int)

    def test_transaction_filter_by_card(self):
        if not self.card_list.data:
            self.skipTest("no cards")
        card_id = self.card_list.data[0].card_id
        result = self.client.transaction_list(
            TransactionListParams(card_id=card_id, page=1, page_size=5)
        )
        self.assertIsInstance(result.data, list)
        for tx in result.data:
            self.assertEqual(tx.card_id, card_id)


@unittest.skipIf(SKIP, SKIP_REASON)
class TestModeALifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = ClawallexClient.create(
            api_key=API_KEY, api_secret=API_SECRET, base_url=BASE_URL,
        )

    def test_create_verify_close_flash_card(self):
        import uuid
        import time as _time
        req_id = str(uuid.uuid4())

        # 1. create card
        params = NewCardParams(
            mode_code=100,
            card_type=100,
            amount="5.0000",
            client_request_id=req_id,
        )
        order = self.client.new_card(params)
        self.assertTrue(order.card_order_id)

        # snapshot existing card ids
        before = self.client.card_list(page=1, page_size=100)
        existing_ids = {c.card_id for c in before.data}

        # card creation may be async (status=120), poll card list for new card
        card_id = order.card_id
        if not card_id:
            for _ in range(30):
                _time.sleep(2)
                cl = self.client.card_list(page=1, page_size=100)
                new_cards = [c for c in cl.data if c.card_id not in existing_ids and c.mode_code == 100]
                if new_cards:
                    card_id = new_cards[0].card_id
                    break
        self.assertTrue(card_id, "new card not found after polling")

        # 3. check balance
        balance = self.client.card_balance(card_id)
        self.assertEqual(balance.card_id, card_id)

        # 4. check details
        details = self.client.card_details(card_id)
        self.assertEqual(details.card_id, card_id)
        self.assertTrue(details.encrypted_sensitive_data.ciphertext)



@unittest.skipIf(SKIP, SKIP_REASON)
class TestModeB402(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = ClawallexClient.create(
            api_key=API_KEY, api_secret=API_SECRET, base_url=BASE_URL,
        )

    def test_mode_b_returns_402(self):
        import uuid
        params = NewCardParams(
            mode_code=200,
            card_type=200,
            amount="100.0000",
            client_request_id=str(uuid.uuid4()),
            chain_code="ETH",
            token_code="USDC",
        )
        with self.assertRaises(ClawallexPaymentRequiredError) as ctx:
            self.client.new_card(params)
        err = ctx.exception
        self.assertEqual(err.code, "PAYMENT_REQUIRED")
        self.assertTrue(err.details.get("card_order_id"))
        self.assertTrue(err.details.get("x402_reference_id"))
        self.assertTrue(err.details.get("payee_address"))
        self.assertTrue(err.details.get("asset_address"))
        self.assertTrue(err.details.get("payable_amount"))
        self.assertTrue(err.details.get("fee_amount"))


if __name__ == "__main__":
    unittest.main()
