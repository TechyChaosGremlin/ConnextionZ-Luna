"""Safe configuration-based selection for collaboration payment providers."""

from __future__ import annotations

from typing import NoReturn

from app.config import Settings, get_settings
from app.payments.fake_provider import FakePaymentProvider
from app.payments.provider import (
    PaymentProvider,
    ProviderResult,
    ProviderUnavailable,
)


class DisabledPaymentProvider:
    """Default adapter: rejects operations without attempting any external call."""

    name = "disabled"

    @staticmethod
    def _disabled() -> NoReturn:
        raise ProviderUnavailable(
            "Collaboration payment provider is disabled", retryable=False
        )

    async def initialize_payment(
        self,
        *,
        amount_minor_units: int,
        currency: str,
        idempotency_key: str,
    ) -> ProviderResult:
        _ = (amount_minor_units, currency, idempotency_key)
        self._disabled()

    async def authorize_hold(
        self,
        *,
        payment_reference_id: str,
        amount_minor_units: int,
        currency: str,
        idempotency_key: str,
    ) -> ProviderResult:
        _ = (payment_reference_id, amount_minor_units, currency, idempotency_key)
        self._disabled()

    async def cancel_hold(
        self, *, payment_reference_id: str, idempotency_key: str
    ) -> ProviderResult:
        _ = (payment_reference_id, idempotency_key)
        self._disabled()

    async def refund_payment(
        self,
        *,
        payment_reference_id: str,
        amount_minor_units: int,
        currency: str,
        idempotency_key: str,
    ) -> ProviderResult:
        _ = (payment_reference_id, amount_minor_units, currency, idempotency_key)
        self._disabled()

    async def retrieve_payment_status(
        self, *, payment_reference_id: str
    ) -> ProviderResult:
        _ = payment_reference_id
        self._disabled()


def get_payment_provider(settings: Settings | None = None) -> PaymentProvider:
    selected_settings = settings or get_settings()
    if selected_settings.collaboration_payment_real_money_enabled:
        raise ValueError(
            "Real-money collaboration payments are not supported by any configured provider"
        )
    if selected_settings.collaboration_payment_provider == "fake":
        if selected_settings.environment == "production":
            raise ValueError(
                "The fake collaboration payment provider cannot be selected in production"
            )
        return FakePaymentProvider()
    return DisabledPaymentProvider()
