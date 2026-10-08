from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.models.collaboration import Collaboration, CollaborationStatus
from app.models.collaboration_payment import (
    CollaborationPayment,
    CollaborationPaymentState as State,
)
from app.payments.factory import (
    DisabledPaymentProvider,
    get_payment_provider,
)
from app.payments.fake_provider import FakePaymentProvider
from app.payments.provider import (
    PaymentProvider,
    ProviderAuthorizationFailure,
    ProviderDuplicateOperation,
    ProviderEvent,
    ProviderInvalidState,
    ProviderPaymentFailure,
    ProviderRefundFailure,
    ProviderResult,
    ProviderStatus,
    ProviderUnavailable,
)
from repositories.collaboration_payment_repository import (
    CollaborationPaymentRepository,
)
from services.collaboration_payment_service import CollaborationPaymentService


class MemoryRepository(CollaborationPaymentRepository):
    def __init__(self, *, status=CollaborationStatus.PROPOSED):
        payer_id = uuid.uuid4()
        recipient_id = uuid.uuid4()
        self.collaboration = Collaboration(
            id=uuid.uuid4(),
            initiator_id=payer_id,
            status=status,
            title="Provider boundary test",
            deleted_at=None,
        )
        self.payment = CollaborationPayment(
            id=uuid.uuid4(),
            collaboration_id=self.collaboration.id,
            payer_user_id=payer_id,
            recipient_user_id=recipient_id,
            amount_minor_units=1250,
            currency="USD",
            state=State.PENDING,
        )
        self.participants = {payer_id, recipient_id}
        self.save_calls = 0

    async def get_collaboration(self, collaboration_id, *, for_update=False):
        _ = for_update
        if collaboration_id == self.collaboration.id:
            return self.collaboration
        return None

    async def get_for_collaboration(self, collaboration_id, *, for_update=False):
        _ = for_update
        if self.payment.collaboration_id == collaboration_id:
            return self.payment
        return None

    async def get_participant_user_ids(self, collaboration_id):
        _ = collaboration_id
        return self.participants

    async def get_accepted_participant_user_ids(self, collaboration_id):
        _ = collaboration_id
        return self.participants - {self.collaboration.initiator_id}

    async def get_by_hold_idempotency_key(self, idempotency_key, *, for_update=False):
        _ = for_update
        if self.payment.hold_idempotency_key == idempotency_key:
            return self.payment
        return None

    async def get_by_provider_reference(self, provider_name, provider_reference):
        if (
            self.payment.payment_provider == provider_name
            and self.payment.provider_reference == provider_reference
        ):
            return self.payment
        return None

    async def save(self, payment):
        self.save_calls += 1
        return payment


def make_service(provider=None):
    repository = MemoryRepository()
    provider = provider or FakePaymentProvider()
    service = CollaborationPaymentService(repository, provider=provider)
    return repository, provider, service


@pytest.mark.asyncio
async def test_fake_provider_satisfies_neutral_interface_and_authorizes_idempotently():
    provider: PaymentProvider = FakePaymentProvider()
    created = await provider.initialize_payment(
        amount_minor_units=1250,
        currency="USD",
        idempotency_key="initialize-1",
    )
    assert created.status == ProviderStatus.CREATED

    result = await provider.authorize_hold(
        payment_reference_id=created.payment_reference_id,
        amount_minor_units=1250,
        currency="USD",
        idempotency_key="authorize-1",
    )
    replay = await provider.authorize_hold(
        payment_reference_id=created.payment_reference_id,
        amount_minor_units=1250,
        currency="USD",
        idempotency_key="authorize-1",
    )

    assert result.status == ProviderStatus.AUTHORIZED
    assert replay == result


@pytest.mark.asyncio
async def test_fake_provider_normalizes_authorization_payment_and_refund_failures():
    provider = FakePaymentProvider(authorization_failure=True)
    created = await provider.initialize_payment(
        amount_minor_units=1250, currency="USD", idempotency_key="create-fail-auth"
    )
    with pytest.raises(ProviderAuthorizationFailure) as authorization_error:
        await provider.authorize_hold(
            payment_reference_id=created.payment_reference_id,
            amount_minor_units=1250,
            currency="USD",
            idempotency_key="fail-auth",
        )
    assert authorization_error.value.retryable is False

    with pytest.raises(ProviderPaymentFailure):
        await FakePaymentProvider(payment_failure=True).initialize_payment(
            amount_minor_units=1250,
            currency="USD",
            idempotency_key="fail-payment",
        )
    with pytest.raises(ProviderRefundFailure) as refund_error:
        await FakePaymentProvider(refund_failure=True).refund_payment(
            payment_reference_id="fake-ref",
            amount_minor_units=1250,
            currency="USD",
            idempotency_key="fail-refund",
        )
    assert refund_error.value.retryable is False


@pytest.mark.asyncio
async def test_fake_provider_cancellation_and_refund_paths():
    provider = FakePaymentProvider()
    cancellable = await provider.initialize_payment(
        amount_minor_units=1250, currency="USD", idempotency_key="create-cancel"
    )
    cancelled = await provider.cancel_hold(
        payment_reference_id=cancellable.payment_reference_id,
        idempotency_key="cancel",
    )
    assert cancelled.status == ProviderStatus.CANCELLED

    refundable = await provider.initialize_payment(
        amount_minor_units=1250, currency="USD", idempotency_key="create-refund"
    )
    await provider.authorize_hold(
        payment_reference_id=refundable.payment_reference_id,
        amount_minor_units=1250,
        currency="USD",
        idempotency_key="authorize-refund",
    )
    refunded = await provider.refund_payment(
        payment_reference_id=refundable.payment_reference_id,
        amount_minor_units=1250,
        currency="USD",
        idempotency_key="refund",
    )
    assert refunded.status == ProviderStatus.REFUNDED


@pytest.mark.asyncio
async def test_fake_provider_rejects_changed_idempotent_request_and_invalid_state():
    provider = FakePaymentProvider()
    created = await provider.initialize_payment(
        amount_minor_units=1250, currency="USD", idempotency_key="same-key"
    )
    with pytest.raises(ProviderInvalidState, match="different operation data"):
        await provider.initialize_payment(
            amount_minor_units=2000, currency="USD", idempotency_key="same-key"
        )
    with pytest.raises(ProviderInvalidState):
        await provider.refund_payment(
            payment_reference_id=created.payment_reference_id,
            amount_minor_units=1250,
            currency="USD",
            idempotency_key="refund-before-authorization",
        )


@pytest.mark.asyncio
async def test_fake_provider_reports_unavailability_as_retryable():
    with pytest.raises(ProviderUnavailable) as error:
        await FakePaymentProvider(unavailable=True).initialize_payment(
            amount_minor_units=1250, currency="USD", idempotency_key="unavailable"
        )
    assert error.value.retryable is True


def test_provider_factory_is_disabled_by_default_and_fake_is_configurable(
    monkeypatch,
):
    monkeypatch.setenv("COLLABORATION_PAYMENT_PROVIDER", "disabled")
    monkeypatch.setenv("COLLABORATION_PAYMENT_REAL_MONEY_ENABLED", "false")
    settings = Settings()
    assert settings.collaboration_payment_provider == "disabled"
    assert settings.collaboration_payment_real_money_enabled is False
    assert isinstance(get_payment_provider(settings), DisabledPaymentProvider)

    monkeypatch.setenv("COLLABORATION_PAYMENT_PROVIDER", "fake")
    fake_settings = Settings()
    assert isinstance(get_payment_provider(fake_settings), FakePaymentProvider)

    monkeypatch.setenv("COLLABORATION_PAYMENT_REAL_MONEY_ENABLED", "true")
    with pytest.raises(ValueError, match="Real-money"):
        Settings()
    monkeypatch.setenv("COLLABORATION_PAYMENT_REAL_MONEY_ENABLED", "false")
    with pytest.raises(ValueError, match="cannot be selected in production"):
        Settings(
            environment="production",
            collaboration_payment_provider="fake",
            jwt_secret_key=SecretStr(
                "a-unique-secure-production-secret-of-at-least-32-chars"
            ),
        )


@pytest.mark.asyncio
async def test_disabled_provider_fails_closed_without_credentials():
    provider = get_payment_provider(
        Settings(
            collaboration_payment_provider="disabled",
            collaboration_payment_real_money_enabled=False,
        )
    )
    with pytest.raises(ProviderUnavailable) as error:
        await provider.initialize_payment(
            amount_minor_units=1250,
            currency="USD",
            idempotency_key="disabled",
        )
    assert error.value.retryable is False


@pytest.mark.asyncio
async def test_service_initializes_and_authorizes_only_through_injected_interface():
    repository, provider, service = make_service()
    assert isinstance(provider, PaymentProvider)
    initialized = await service.initialize_provider_payment(
        repository.collaboration.id,
        repository.payment.payer_user_id,
        "service-init",
    )
    assert repository.payment.payment_provider == "fake"
    assert repository.payment.provider_reference == initialized.payment_reference_id

    payment = await service.authorize_hold_with_provider(
        repository.collaboration.id,
        repository.payment.payer_user_id,
        "service-authorize",
    )
    assert payment.state == State.AUTHORIZED_HELD
    assert payment.authorization_reference == "fake-op-service-authorize"


@pytest.mark.asyncio
async def test_provider_failure_does_not_transition_internal_payment():
    repository, _, service = make_service(
        FakePaymentProvider(authorization_failure=True)
    )
    await service.initialize_provider_payment(
        repository.collaboration.id,
        repository.payment.payer_user_id,
        "failure-init",
    )
    with pytest.raises(ProviderAuthorizationFailure):
        await service.authorize_hold_with_provider(
            repository.collaboration.id,
            repository.payment.payer_user_id,
            "failure-auth",
        )
    assert repository.payment.state == State.PENDING
    assert repository.payment.authorized_at is None


@pytest.mark.asyncio
async def test_provider_authorization_replay_returns_current_state_without_reauthorizing():
    class CountingProvider(FakePaymentProvider):
        def __init__(self):
            super().__init__()
            self.authorization_calls = 0

        async def authorize_hold(self, **kwargs):
            self.authorization_calls += 1
            return await super().authorize_hold(**kwargs)

    repository = MemoryRepository(status=CollaborationStatus.ACCEPTED)
    provider = CountingProvider()
    service = CollaborationPaymentService(repository, provider=provider)
    await service.initialize_provider_payment(
        repository.collaboration.id,
        repository.payment.payer_user_id,
        "replay-init",
    )

    authorized = await service.authorize_hold_with_provider(
        repository.collaboration.id,
        repository.payment.payer_user_id,
        "replay-authorize",
    )
    replay = await service.authorize_hold_with_provider(
        repository.collaboration.id,
        repository.payment.payer_user_id,
        "replay-authorize",
    )

    assert authorized is replay is repository.payment
    assert replay.state == State.COLLABORATION_ACTIVE
    assert provider.authorization_calls == 1

    with pytest.raises(ValueError, match="another idempotency key"):
        await service.authorize_hold_with_provider(
            repository.collaboration.id,
            repository.payment.payer_user_id,
            "different-authorize",
        )
    assert provider.authorization_calls == 1


@pytest.mark.asyncio
async def test_provider_failures_leave_internal_payment_state_unchanged():
    class FailingCancellationProvider(FakePaymentProvider):
        async def cancel_hold(self, *, payment_reference_id, idempotency_key):
            _ = payment_reference_id, idempotency_key
            raise ProviderPaymentFailure("simulated cancellation failure")

    cancellation_repository, _, cancellation_service = make_service(
        FailingCancellationProvider()
    )
    await cancellation_service.initialize_provider_payment(
        cancellation_repository.collaboration.id,
        cancellation_repository.payment.payer_user_id,
        "cancel-failure-init",
    )
    await cancellation_service.authorize_hold_with_provider(
        cancellation_repository.collaboration.id,
        cancellation_repository.payment.payer_user_id,
        "cancel-failure-auth",
    )
    cancellation_repository.collaboration.status = CollaborationStatus.CANCELLED

    with pytest.raises(ProviderPaymentFailure, match="cancellation failure"):
        await cancellation_service.cancel_provider_hold(
            cancellation_repository.collaboration.id, "cancel-failure"
        )
    assert cancellation_repository.payment.state == State.AUTHORIZED_HELD
    assert cancellation_repository.payment.cancelled_at is None
    assert cancellation_repository.payment.hold_reversed_at is None

    refund_repository, _, refund_service = make_service(
        FakePaymentProvider(refund_failure=True)
    )
    await refund_service.initialize_provider_payment(
        refund_repository.collaboration.id,
        refund_repository.payment.payer_user_id,
        "refund-failure-init",
    )
    await refund_service.authorize_hold_with_provider(
        refund_repository.collaboration.id,
        refund_repository.payment.payer_user_id,
        "refund-failure-auth",
    )

    with pytest.raises(ProviderRefundFailure):
        await refund_service.refund_with_provider(
            refund_repository.collaboration.id, "refund-failure"
        )
    assert refund_repository.payment.state == State.AUTHORIZED_HELD
    assert refund_repository.payment.refund_authorized_at is None
    assert refund_repository.payment.hold_reversed_at is None


@pytest.mark.asyncio
async def test_provider_unavailability_does_not_bind_or_authorize_payment():
    repository, _, service = make_service(FakePaymentProvider(unavailable=True))

    with pytest.raises(ProviderUnavailable):
        await service.initialize_provider_payment(
            repository.collaboration.id,
            repository.payment.payer_user_id,
            "service-unavailable",
        )

    assert repository.payment.state == State.PENDING
    assert repository.payment.payment_provider is None
    assert repository.payment.provider_reference is None


@pytest.mark.asyncio
async def test_service_rejects_provider_reference_bound_to_another_payment():
    repository, _, service = make_service()
    existing_payment = CollaborationPayment(
        id=uuid.uuid4(),
        collaboration_id=uuid.uuid4(),
        payer_user_id=uuid.uuid4(),
        recipient_user_id=uuid.uuid4(),
        amount_minor_units=1250,
        currency="USD",
        state=State.PENDING,
    )

    async def get_reference_owner(
        provider_name: str, provider_reference: str
    ) -> CollaborationPayment:
        _ = provider_name, provider_reference
        return existing_payment

    repository.get_by_provider_reference = get_reference_owner

    with pytest.raises(ValueError, match="already bound to another payment"):
        await service.initialize_provider_payment(
            repository.collaboration.id,
            repository.payment.payer_user_id,
            "duplicate-provider-reference",
        )

    assert repository.payment.payment_provider is None
    assert repository.payment.provider_reference is None
    assert repository.save_calls == 0


@pytest.mark.asyncio
async def test_service_rejects_provider_release_status_and_reference_mismatch():
    repository, _, service = make_service()
    await service.initialize_provider_payment(
        repository.collaboration.id,
        repository.payment.payer_user_id,
        "malicious-init",
    )

    class ReleasingProvider(FakePaymentProvider):
        async def authorize_hold(self, **kwargs):
            return ProviderResult(
                provider_name="fake",
                payment_reference_id=kwargs["payment_reference_id"],
                operation_id="forged-operation",
                status=ProviderStatus.RELEASED,
                amount_minor_units=kwargs["amount_minor_units"],
                currency=kwargs["currency"],
                idempotency_key=kwargs["idempotency_key"],
            )

    releasing_service = CollaborationPaymentService(
        repository, provider=ReleasingProvider()
    )
    with pytest.raises(ValueError, match="returned RELEASED"):
        await releasing_service.authorize_hold_with_provider(
            repository.collaboration.id,
            repository.payment.payer_user_id,
            "release-attempt",
        )
    assert repository.payment.state == State.PENDING
    assert repository.payment.released_at is None

    repository.payment.payment_provider = "fake"
    mismatched_result = ProviderResult(
        provider_name="fake",
        payment_reference_id="some-other-payment",
        operation_id="operation",
        status=ProviderStatus.CREATED,
        amount_minor_units=repository.payment.amount_minor_units,
        currency=repository.payment.currency,
        idempotency_key="mismatch",
    )

    class MismatchedReferenceProvider(FakePaymentProvider):
        async def initialize_payment(
            self,
            *,
            amount_minor_units,
            currency,
            idempotency_key,
        ):
            _ = (amount_minor_units, currency, idempotency_key)
            return mismatched_result

    mismatched_service = CollaborationPaymentService(
        repository, provider=MismatchedReferenceProvider()
    )
    repository.payment.provider_reference = "correct-payment"
    with pytest.raises(ValueError, match="reference does not match"):
        await mismatched_service.initialize_provider_payment(
            repository.collaboration.id,
            repository.payment.payer_user_id,
            "mismatch",
        )


@pytest.mark.asyncio
async def test_service_validates_payer_before_provider_call_and_keeps_reference_isolated():
    repository, _, service = make_service()
    with pytest.raises(PermissionError, match="payer"):
        await service.initialize_provider_payment(
            repository.collaboration.id,
            repository.payment.recipient_user_id,
            "wrong-payer",
        )
    assert repository.payment.provider_reference is None

    await service.initialize_provider_payment(
        repository.collaboration.id,
        repository.payment.payer_user_id,
        "right-payer",
    )
    with pytest.raises(PermissionError, match="payer"):
        await service.authorize_hold_with_provider(
            repository.collaboration.id,
            repository.payment.recipient_user_id,
            "wrong-authorizer",
        )
    assert repository.payment.state == State.PENDING


@pytest.mark.asyncio
async def test_service_cancellation_and_refund_follow_provider_success():
    repository, _, service = make_service()
    await service.initialize_provider_payment(
        repository.collaboration.id,
        repository.payment.payer_user_id,
        "cancel-init",
    )
    await service.authorize_hold_with_provider(
        repository.collaboration.id,
        repository.payment.payer_user_id,
        "cancel-authorize",
    )
    repository.collaboration.status = CollaborationStatus.CANCELLED

    cancelled = await service.cancel_provider_hold(
        repository.collaboration.id, "cancel-provider"
    )
    assert cancelled is not None
    assert cancelled.state == State.CANCELLED
    assert cancelled.cancelled_at is not None

    refund_repository, _, refund_service = make_service()
    await refund_service.initialize_provider_payment(
        refund_repository.collaboration.id,
        refund_repository.payment.payer_user_id,
        "refund-init",
    )
    await refund_service.authorize_hold_with_provider(
        refund_repository.collaboration.id,
        refund_repository.payment.payer_user_id,
        "refund-authorize",
    )
    refunded = await refund_service.refund_with_provider(
        refund_repository.collaboration.id, "refund-provider"
    )
    assert refunded.state == State.REFUNDED
    assert refunded.refund_authorized_at is not None


@pytest.mark.asyncio
async def test_provider_service_fails_closed_on_forged_payment_relationship():
    repository, _, service = make_service()
    repository.payment.collaboration_id = uuid.uuid4()
    with pytest.raises(ValueError, match="Payment not found"):
        await service.initialize_provider_payment(
            repository.collaboration.id,
            repository.payment.payer_user_id,
            "wrong-collaboration",
        )
    assert repository.save_calls == 0


@pytest.mark.asyncio
async def test_provider_retrieval_cannot_mutate_internal_release_state():
    repository, _, service = make_service()
    await service.initialize_provider_payment(
        repository.collaboration.id,
        repository.payment.payer_user_id,
        "retrieve-init",
    )

    class ReleasedStatusProvider(FakePaymentProvider):
        async def retrieve_payment_status(self, *, payment_reference_id):
            return ProviderResult(
                provider_name="fake",
                payment_reference_id=payment_reference_id,
                operation_id=None,
                status=ProviderStatus.RELEASED,
                amount_minor_units=repository.payment.amount_minor_units,
                currency=repository.payment.currency,
                idempotency_key=None,
            )

    read_service = CollaborationPaymentService(
        repository, provider=ReleasedStatusProvider()
    )
    status = await read_service.retrieve_provider_payment_status(
        repository.collaboration.id,
        repository.payment.payer_user_id,
    )
    assert status.status == ProviderStatus.RELEASED
    assert repository.payment.state == State.PENDING
    assert repository.payment.released_at is None


def test_normalized_event_replay_uses_same_deduplication_identity():
    event = ProviderEvent(
        provider_event_id="event-1",
        provider_payment_reference_id="payment-1",
        event_type="payment.authorized",
        event_timestamp=datetime.now(timezone.utc),
        normalized_status=ProviderStatus.AUTHORIZED,
        idempotency_key="event-replay-key",
    )
    replay = ProviderEvent(
        provider_event_id="event-1",
        provider_payment_reference_id="payment-1",
        event_type="payment.authorized",
        event_timestamp=event.event_timestamp,
        normalized_status=ProviderStatus.AUTHORIZED,
        idempotency_key="event-replay-key",
    )
    assert event.replay_identity == replay.replay_identity
    assert event == replay
    with pytest.raises(ValueError, match="timezone-aware"):
        ProviderEvent(
            provider_event_id="event-2",
            provider_payment_reference_id="payment-1",
            event_type="payment.authorized",
            event_timestamp=datetime.now(),
            normalized_status=ProviderStatus.AUTHORIZED,
            idempotency_key="event-replay-key-2",
        )


def test_normalized_duplicate_operation_error_is_non_retryable():
    error = ProviderDuplicateOperation()
    assert error.retryable is False
    assert isinstance(error, Exception)
