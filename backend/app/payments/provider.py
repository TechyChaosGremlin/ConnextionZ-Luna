"""Provider-neutral payment operations, results, events, and errors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable


class ProviderStatus(StrEnum):
    CREATED = "CREATED"
    PENDING = "PENDING"
    AUTHORIZED = "AUTHORIZED"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"
    RELEASED = "RELEASED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ProviderResult:
    provider_name: str
    payment_reference_id: str
    operation_id: str | None
    status: ProviderStatus
    amount_minor_units: int
    currency: str
    idempotency_key: str | None
    failure_code: str | None = None
    failure_message: str | None = None

    def __post_init__(self) -> None:
        if not self.provider_name.strip() or len(self.provider_name) > 64:
            raise ValueError("provider_name must contain 1 to 64 characters")
        if (
            not self.payment_reference_id.strip()
            or len(self.payment_reference_id) > 255
        ):
            raise ValueError(
                "payment_reference_id must contain 1 to 255 characters"
            )
        if (
            isinstance(self.amount_minor_units, bool)
            or not isinstance(self.amount_minor_units, int)
            or self.amount_minor_units <= 0
        ):
            raise ValueError("Provider amount must be a positive integer")
        if (
            len(self.currency) != 3
            or not all("A" <= character <= "Z" for character in self.currency)
        ):
            raise ValueError("Provider currency must be a three-letter uppercase code")
        if not isinstance(self.status, ProviderStatus):
            raise ValueError("Provider status must be normalized")
        if self.idempotency_key is not None and not self.idempotency_key.strip():
            raise ValueError("idempotency_key must not be empty")


@dataclass(frozen=True, slots=True)
class ProviderEvent:
    """Normalized event envelope for a future authenticated webhook adapter."""

    provider_event_id: str
    provider_payment_reference_id: str
    event_type: str
    event_timestamp: datetime
    normalized_status: ProviderStatus
    idempotency_key: str

    def __post_init__(self) -> None:
        for field_name in (
            "provider_event_id",
            "provider_payment_reference_id",
            "event_type",
            "idempotency_key",
        ):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must not be empty")
        if self.event_timestamp.tzinfo is None:
            raise ValueError("event_timestamp must be timezone-aware")

    @property
    def replay_identity(self) -> str:
        """Stable deduplication identity for a persistent future event inbox."""
        return self.idempotency_key


class PaymentProviderError(Exception):
    """Base normalized provider error; messages must never include secrets."""

    def __init__(self, message: str, *, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


class ProviderAuthorizationFailure(PaymentProviderError):
    def __init__(self, message: str = "Provider authorization failed"):
        super().__init__(message, retryable=False)


class ProviderPaymentFailure(PaymentProviderError):
    def __init__(self, message: str = "Provider payment operation failed"):
        super().__init__(message, retryable=False)


class ProviderRefundFailure(PaymentProviderError):
    def __init__(self, message: str = "Provider refund operation failed"):
        super().__init__(message, retryable=False)


class ProviderUnavailable(PaymentProviderError):
    def __init__(
        self,
        message: str = "Payment provider is unavailable",
        *,
        retryable: bool = True,
    ):
        super().__init__(message, retryable=retryable)


class ProviderInvalidState(PaymentProviderError):
    def __init__(self, message: str = "Provider payment is in an invalid state"):
        super().__init__(message, retryable=False)


class ProviderDuplicateOperation(PaymentProviderError):
    def __init__(self, message: str = "Provider operation was already processed"):
        super().__init__(message, retryable=False)


@runtime_checkable
class PaymentProvider(Protocol):
    """Only external-operation boundary; it cannot mutate internal payment state."""

    @property
    def name(self) -> str: ...

    async def initialize_payment(
        self,
        *,
        amount_minor_units: int,
        currency: str,
        idempotency_key: str,
    ) -> ProviderResult: ...

    async def authorize_hold(
        self,
        *,
        payment_reference_id: str,
        amount_minor_units: int,
        currency: str,
        idempotency_key: str,
    ) -> ProviderResult: ...

    async def cancel_hold(
        self, *, payment_reference_id: str, idempotency_key: str
    ) -> ProviderResult: ...

    async def refund_payment(
        self,
        *,
        payment_reference_id: str,
        amount_minor_units: int,
        currency: str,
        idempotency_key: str,
    ) -> ProviderResult: ...

    async def retrieve_payment_status(
        self, *, payment_reference_id: str
    ) -> ProviderResult: ...
