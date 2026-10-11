"""Hermetic futures venue simulator for runtime tests; not real venue acceptance evidence."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from tests.execution.test_paper_futures_books import _PERP, _START
from thytrader.exchanges.coinbase_futures_broker import CoinbaseFuturesBroker
from thytrader.execution.futures_live import FuturesLiveAccount
from thytrader.risk.futures_live import FuturesVenueEvidence

if TYPE_CHECKING:
    from collections.abc import Mapping

ORDERS = "/api/v3/brokerage/orders"


class FakeVenue:
    """An explicit test transport with FUTURE order history, real adapter parsing and fees.

    JSON dictionaries intentionally model the untrusted adapter boundary. No sockets, SDK
    credentials, real account identifiers or authenticated venue requests are involved.
    """

    def __init__(self) -> None:
        """Start a flat test account; entry orders rest and reductions fill immediately."""
        self.now = _START + timedelta(hours=31)
        self.orders: dict[str, dict[str, Any]] = {}
        self.fills: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str, object]] = []
        self.net = Decimal(0)
        self.size = Decimal("0.01")
        self.price = Decimal(130)
        self.margin = Decimal(100)
        self.threshold = Decimal(20)
        self.killswitch = False
        self.external: frozenset[str] | None = frozenset()
        self.account_reads = 0
        self.timeout_after_post = False
        self.timeout_before_post = False
        self.partial_contracts: Decimal | None = None
        self.cancel_confirmed = True

    async def contract_size(self, product_id: str) -> Decimal | None:
        """The same catalog size used in evidence and by the actual futures broker."""
        return self.size if product_id == _PERP else None

    def broker(self) -> CoinbaseFuturesBroker:
        """A fresh real adapter, so no submit-time identity cache can rescue a restart."""
        return CoinbaseFuturesBroker(self, self, cancel_confirm_delay_seconds=0)

    def post(self, path: str, data: Mapping[str, object] | None = None) -> dict[str, Any]:
        """Implement only synthetic create, explicit reduction and cancel responses."""
        assert data is not None
        self.calls.append(("POST", path, dict(data)))
        if self.timeout_before_post:
            self.timeout_before_post = False
            raise TimeoutError("synthetic pre-send interruption")
        if path == ORDERS + "/batch_cancel":
            ids = data["order_ids"]
            assert isinstance(ids, list)
            for identity in ids:
                if self.cancel_confirmed:
                    self.orders[str(identity)]["status"] = "CANCELLED"
            return {"results": [{"order_id": i, "success": True} for i in ids]}
        identity = f"test-order-{len(self.orders)}"
        close = path == ORDERS + "/close_position"
        if close:
            contracts = Decimal(str(data["size"]))
            side = "SELL" if self.net > 0 else "BUY"
        else:
            configuration = data["order_configuration"]
            assert isinstance(configuration, dict)
            values = next(iter(configuration.values()))
            assert isinstance(values, dict)
            fields = {str(key): item for key, item in values.items()}
            contracts = Decimal(str(fields["base_size"]))
            side = str(data["side"])
        row: dict[str, Any] = {
            "order_id": identity,
            "client_order_id": data["client_order_id"],
            "product_id": _PERP,
            "product_type": "FUTURE",
            "status": "OPEN",
            "filled_size": "0",
            "average_filled_price": str(self.price),
            "size_in_quote": False,
            "side": side,
            "base_size": str(contracts),
        }
        self.orders[identity] = row
        if close:
            assert self.net != 0 and contracts <= abs(self.net), "test reduction must not flip"
            self.fill(identity, self.partial_contracts or contracts)
            row["status"] = "FILLED" if self.partial_contracts is None else "CANCELLED"
        if self.timeout_after_post:
            self.timeout_after_post = False
            raise TimeoutError("synthetic lost acknowledgement")
        return {"success": True, "success_response": {"order_id": identity}}

    def get(self, path: str, params: Mapping[str, object] | None = None) -> dict[str, Any]:
        """Return complete bounded history and canonical FUTURE fills."""
        self.calls.append(("GET", path, dict(params or {})))
        if path.endswith("/product_book"):
            return {
                "pricebook": {
                    "product_id": _PERP,
                    "bids": [{"price": str(self.price)}],
                    "asks": [{"price": str(self.price)}],
                }
            }
        if path.endswith("/historical/fills"):
            requested = (params or {}).get("order_ids")
            assert requested is None or isinstance(requested, list)
            return {
                "fills": [f for f in self.fills if not requested or f["order_id"] in requested],
                "has_next": False,
            }
        if path.endswith("/historical/batch"):
            return {"orders": list(self.orders.values()), "has_next": False}
        return {"order": dict(self.orders[path.rsplit("/", 1)[-1]])}

    def fill(self, identity: str, contracts: Decimal | None = None) -> None:
        """Execute test inventory and create canonical commission-bearing fill JSON."""
        row = self.orders[identity]
        count = Decimal(row["base_size"]) if contracts is None else contracts
        row["filled_size"] = str(count)
        row["status"] = "FILLED"
        self.net += count if row["side"] == "BUY" else -count
        self.fills.append(
            {
                "entry_id": f"test-fill-{len(self.fills)}",
                "trade_id": f"test-trade-{len(self.fills)}",
                "order_id": identity,
                "product_id": _PERP,
                "product_type": "FUTURE",
                "trade_type": "FILL",
                "price": str(self.price),
                "size": str(count),
                "size_in_quote": False,
                "commission": "0.15",
                "side": row["side"],
                "trade_time": self.now.isoformat(),
                "sequence_timestamp": self.now.isoformat(),
                "future_legs": [],
                "option_legs": [],
            }
        )

    async def read_account(
        self,
        managed_client_ids: frozenset[tuple[str, str]],
        product_ids: frozenset[str] = frozenset(),
    ) -> FuturesLiveAccount:
        """Return test account facts; foreign orders remain explicitly foreign."""
        del managed_client_ids, product_ids
        self.account_reads += 1
        return FuturesLiveAccount(
            venue=FuturesVenueEvidence(
                observed_at=self.now,
                buying_power_usd=Decimal(10000),
                initial_margin_usd=abs(self.net) * self.size * self.price * Decimal("0.25"),
                positions={_PERP: self.net},
                external_order_products=self.external,
                killswitch_enabled=self.killswitch,
                maintenance={},
                contract_sizes={_PERP: self.size},
            ),
            marks={_PERP: self.price},
            available_margin_usd=self.margin,
            liquidation_threshold_usd=self.threshold,
        )

    @property
    def creates(self) -> list[tuple[str, str, object]]:
        """Mutation calls that could create an entry, protective order or reduction."""
        return [
            call
            for call in self.calls
            if call[0] == "POST" and not call[1].endswith("batch_cancel")
        ]

    def advance(self, seconds: int = 6) -> datetime:
        """Move the fake observation clock past the fill settlement guard."""
        self.now += timedelta(seconds=seconds)
        return self.now
