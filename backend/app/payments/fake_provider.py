"""Deterministic in-memory provider for tests; never performs network I/O."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Callable

from app.payments.provider import (
    ProviderAuthorizationFailure,
    ProviderInvalidState,
    ProviderPaymentFailure,
    ProviderRefundFailure,
    ProviderResult,
    ProviderStatus,
    ProviderUnavailable,
)


@dataclass(frozen=True, slots=True)
class _Replay:
    request: tuple[object, ...]
    result: ProviderResult


class FakePaymentProvider:
    """Sandbox-only in-memory behavior with deterministic idempotent results."""

    name = "fake"

    def __init__(
        self,
        *,
        unavailable: bool = False,
        authorization_failure: bool = False,
        payment_failure: bool = False,
        refund_failure: bool = False,
    ):
        self.unavailable = unavailable
        self.authorization_failure = authorization_failure
        self.payment_failure = payment_failure
        self.refund_failure = refund_failure
        self._payments: dict[str, tuple[int, str, ProviderStatus]] = {}
        self._operations: dict[tuple[str, str], _Replay] = {}
        self._lock = asyncio.Lock()

    async def initialize_payment(
        self,
        *,
        amount_minor_units: int,
        currency: str,
        idempotency_key: str,
    ) -> ProviderResult:
        if self.payment_failure:
            raise ProviderPaymentFailure()
        reference = "fake_" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:32]

        def operation() -> ProviderResult:
            self._payments[reference] = (
                amount_minor_units,
                currency,
                ProviderStatus.CREATED,
            )
            return self._result(
                reference,
                f"fake-op-{idempotency_key}",
                ProviderStatus.CREATED,
                amount_minor_units,
                currency,
                idempotency_key,
            )

        return await self._execute(
            "initialize",
            idempotency_key,
            (amount_minor_units, currency),
            operation,
        )

    async def authorize_hold(
        self,
        *,
        payment_reference_id: str,
        amount_minor_units: int,
        currency: str,
        idempotency_key: str,
    ) -> ProviderResult:
        if self.authorization_failure:
            raise ProviderAuthorizationFailure()

        def operation() -> ProviderResult:
            payment = self._get_payment(payment_reference_id)
            if payment[0:2] != (amount_minor_units, currency):
                raise ProviderInvalidState("Provider amount or currency does not match")
            if payment[2] not in {ProviderStatus.CREATED, ProviderStatus.AUTHORIZED}:
                raise ProviderInvalidState()
            self._payments[payment_reference_id] = (
                payment[0],
                payment[1],
                ProviderStatus.AUTHORIZED,
            )
            return self._result(
                payment_reference_id,
                f"fake-op-{idempotency_key}",
                ProviderStatus.AUTHORIZED,
                amount_minor_units,
                currency,
                idempotency_key,
            )

        return await self._execute(
            "authorize",
            idempotency_key,
            (payment_reference_id, amount_minor_units, currency),
            operation,
        )

    async def cancel_hold(
        self, *, payment_reference_id: str, idempotency_key: str
    ) -> ProviderResult:
        def operation() -> ProviderResult:
            payment = self._get_payment(payment_reference_id)
            if payment[2] not in {ProviderStatus.CREATED, ProviderStatus.AUTHORIZED}:
                raise ProviderInvalidState()
            self._payments[payment_reference_id] = (
                payment[0],
                payment[1],
                ProviderStatus.CANCELLED,
            )
            return self._result(
                payment_reference_id,
                f"fake-op-{idempotency_key}",
                ProviderStatus.CANCELLED,
                payment[0],
                payment[1],
                idempotency_key,
            )

        return await self._execute(
            "cancel", idempotency_key, (payment_reference_id,), operation
        )

    async def refund_payment(
        self,
        *,
        payment_reference_id: str,
        amount_minor_units: int,
        currency: str,
        idempotency_key: str,
    ) -> ProviderResult:
        if self.refund_failure:
            raise ProviderRefundFailure()

        def operation() -> ProviderResult:
            payment = self._get_payment(payment_reference_id)
            if payment[0:2] != (amount_minor_units, currency):
                raise ProviderInvalidState("Provider amount or currency does not match")
            if payment[2] != ProviderStatus.AUTHORIZED:
                raise ProviderInvalidState()
            self._payments[payment_reference_id] = (
                payment[0],
                payment[1],
                ProviderStatus.REFUNDED,
            )
            return self._result(
                payment_reference_id,
                f"fake-op-{idempotency_key}",
                ProviderStatus.REFUNDED,
                amount_minor_units,
                currency,
                idempotency_key,
            )

        return await self._execute(
            "refund",
            idempotency_key,
            (payment_reference_id, amount_minor_units, currency),
            operation,
        )

    async def retrieve_payment_status(
        self, *, payment_reference_id: str
    ) -> ProviderResult:
        self._ensure_available()
        async with self._lock:
            amount, currency, status = self._get_payment(payment_reference_id)
            return self._result(
                payment_reference_id, None, status, amount, currency, None
            )

    async def _execute(
        self,
        operation_name: str,
        idempotency_key: str,
        request: tuple[object, ...],
        execute: Callable[[], ProviderResult],
    ) -> ProviderResult:
        if (
            not isinstance(idempotency_key, str)
            or not idempotency_key.strip()
            or len(idempotency_key) > 128
        ):
            raise ProviderInvalidState("Invalid provider idempotency key")
        self._ensure_available()
        async with self._lock:
            replay_key = (operation_name, idempotency_key)
            previous = self._operations.get(replay_key)
            if previous is not None:
                if previous.request != request:
                    raise ProviderInvalidState(
                        "Idempotency key was reused with different operation data"
                    )
                return previous.result
            result = execute()
            self._operations[replay_key] = _Replay(request, result)
            return result

    def _get_payment(self, reference: str) -> tuple[int, str, ProviderStatus]:
        try:
            return self._payments[reference]
        except KeyError as exc:
            raise ProviderInvalidState("Unknown provider payment reference") from exc

    def _ensure_available(self) -> None:
        if self.unavailable:
            raise ProviderUnavailable()

    def _result(
        self,
        reference: str,
        operation_id: str | None,
        status: ProviderStatus,
        amount_minor_units: int,
        currency: str,
        idempotency_key: str | None,
    ) -> ProviderResult:
        return ProviderResult(
            provider_name=self.name,
            payment_reference_id=reference,
            operation_id=operation_id,
            status=status,
            amount_minor_units=amount_minor_units,
            currency=currency,
            idempotency_key=idempotency_key,
        )
