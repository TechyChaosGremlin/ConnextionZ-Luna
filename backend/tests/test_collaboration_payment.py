from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.models.collaboration import CollaborationStatus
from app.models.collaboration_payment import (
    CollaborationPayment,
    CollaborationPaymentState as State,
)
from services.collaboration_payment_service import (
    VALID_PAYMENT_TRANSITIONS,
    CollaborationPaymentService,
)


class MemoryPaymentRepository:
    def __init__(
        self,
        *,
        status: CollaborationStatus = CollaborationStatus.PROPOSED,
        initiator_id: uuid.UUID | None = None,
        participants: set[uuid.UUID] | None = None,
        payment: CollaborationPayment | None = None,
    ):
        self.initiator_id = initiator_id or uuid.uuid4()
        self.collaboration = SimpleNamespace(
            id=uuid.uuid4(),
            initiator_id=self.initiator_id,
            status=status,
            completed_at=None,
            deleted_at=None,
        )
        self.participants = participants or {self.initiator_id, uuid.uuid4()}
        self.accepted_participants = self.participants - {self.initiator_id}
        self.payment = payment
        self.create_calls = 0
        self.lock_requests = []

    async def get_collaboration(self, collaboration_id, *, for_update=False):
        self.lock_requests.append(("collaboration", for_update))
        if collaboration_id == self.collaboration.id and self.collaboration.deleted_at is None:
            return self.collaboration
        return None

    async def get_participant_user_ids(self, collaboration_id):
        return self.participants

    async def get_accepted_participant_user_ids(self, collaboration_id):
        return self.accepted_participants

    async def get_for_collaboration(self, collaboration_id, *, for_update=False):
        self.lock_requests.append(("payment", for_update))
        if (
            self.payment is not None
            and self.payment.collaboration_id == collaboration_id
        ):
            return self.payment
        return None

    async def get_by_hold_idempotency_key(self, idempotency_key, *, for_update=False):
        if (
            self.payment is not None
            and self.payment.hold_idempotency_key == idempotency_key
        ):
            return self.payment
        return None

    async def create(self, payment):
        if self.payment is not None:
            raise ValueError("duplicate collaboration payment")
        self.create_calls += 1
        payment.id = uuid.uuid4()
        self.payment = payment
        return payment

    async def save(self, payment):
        self.payment = payment
        return payment


class SerializedMemoryPaymentRepository(MemoryPaymentRepository):
    """Model transaction-scoped row locks for concurrent service tests."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.row_lock = asyncio.Lock()
        self.lock_owner = None

    async def get_collaboration(self, collaboration_id, *, for_update=False):
        if for_update:
            task = asyncio.current_task()
            if self.lock_owner is not task:
                await self.row_lock.acquire()
                self.lock_owner = task
        return await super().get_collaboration(
            collaboration_id, for_update=for_update
        )

    async def save(self, payment):
        return await super().save(payment)

    def finish_transaction(self):
        if self.row_lock.locked() and self.lock_owner is asyncio.current_task():
            self.lock_owner = None
            self.row_lock.release()


async def run_locked_transaction(repository, operation):
    try:
        return await operation
    finally:
        repository.finish_transaction()


def make_payment(
    repository: MemoryPaymentRepository,
    state: State,
) -> CollaborationPayment:
    participant_ids = list(repository.participants)
    payer_id = repository.collaboration.initiator_id
    recipient_id = next(user_id for user_id in participant_ids if user_id != payer_id)
    payment = CollaborationPayment(
        id=uuid.uuid4(),
        collaboration_id=repository.collaboration.id,
        payer_user_id=payer_id,
        recipient_user_id=recipient_id,
        amount_minor_units=1250,
        currency="USD",
        state=state,
    )
    if state != State.PENDING:
        payment.hold_idempotency_key = "fixture-hold-key"
        payment.authorization_reference = "fixture-auth-reference"
        payment.authorized_by_operation = "internal_hold_authorization"
        payment.authorized_at = datetime.now(timezone.utc)
    return payment


@pytest.mark.asyncio
async def test_creates_one_pending_payment_for_initiator_and_collaboration_participant():
    repository = MemoryPaymentRepository()
    service = CollaborationPaymentService(repository)
    recipient_id = next(
        user_id
        for user_id in repository.participants
        if user_id != repository.initiator_id
    )

    payment = await service.create_pending_payment(
        repository.collaboration.id,
        repository.initiator_id,
        recipient_id,
        1250,
        "USD",
    )

    assert payment.collaboration_id == repository.collaboration.id
    assert payment.payer_user_id == repository.initiator_id
    assert payment.recipient_user_id == recipient_id
    assert payment.amount_minor_units == 1250
    assert payment.currency == "USD"
    assert payment.state == State.PENDING
    assert repository.payment is payment


@pytest.mark.asyncio
async def test_rejects_duplicate_payment_and_invalid_collaboration_parties():
    repository = MemoryPaymentRepository()
    service = CollaborationPaymentService(repository)
    recipient_id = next(
        user_id
        for user_id in repository.participants
        if user_id != repository.initiator_id
    )
    repository.payment = make_payment(repository, State.PENDING)

    with pytest.raises(ValueError, match="already exists"):
        await service.create_pending_payment(
            repository.collaboration.id,
            repository.initiator_id,
            recipient_id,
            1250,
            "USD",
        )

    outsider_id = uuid.uuid4()
    repository.payment = None
    with pytest.raises(PermissionError, match="belong to the collaboration"):
        await service.create_pending_payment(
            repository.collaboration.id,
            repository.initiator_id,
            outsider_id,
            1250,
            "USD",
        )


@pytest.mark.asyncio
async def test_only_initiator_can_create_and_payer_must_be_a_collaboration_party():
    repository = MemoryPaymentRepository()
    recipient_id = next(
        user_id
        for user_id in repository.participants
        if user_id != repository.initiator_id
    )

    with pytest.raises(PermissionError, match="initiator"):
        await CollaborationPaymentService(repository).create_pending_payment(
            repository.collaboration.id,
            recipient_id,
            repository.initiator_id,
            1250,
            "USD",
        )

    repository.participants.remove(repository.initiator_id)
    with pytest.raises(PermissionError, match="belong to the collaboration"):
        await CollaborationPaymentService(repository).create_pending_payment(
            repository.collaboration.id,
            repository.initiator_id,
            recipient_id,
            1250,
            "USD",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "target", "collaboration_status"),
    [
        (State.PENDING, State.AUTHORIZED_HELD, CollaborationStatus.PROPOSED),
        (State.PENDING, State.CANCELLED, CollaborationStatus.DECLINED),
        (State.AUTHORIZED_HELD, State.COLLABORATION_ACTIVE, CollaborationStatus.ACCEPTED),
        (State.AUTHORIZED_HELD, State.CANCELLED, CollaborationStatus.CANCELLED),
        (State.AUTHORIZED_HELD, State.REFUNDED, CollaborationStatus.ACCEPTED),
        (State.AUTHORIZED_HELD, State.DISPUTED, CollaborationStatus.ACCEPTED),
        (State.COLLABORATION_ACTIVE, State.COMPLETED, CollaborationStatus.COMPLETED),
        (State.COLLABORATION_ACTIVE, State.CANCELLED, CollaborationStatus.CANCELLED),
        (State.COLLABORATION_ACTIVE, State.REFUNDED, CollaborationStatus.IN_PROGRESS),
        (State.COLLABORATION_ACTIVE, State.DISPUTED, CollaborationStatus.IN_PROGRESS),
        (State.COMPLETED, State.RELEASE_PENDING, CollaborationStatus.COMPLETED),
        (State.COMPLETED, State.REFUNDED, CollaborationStatus.COMPLETED),
        (State.COMPLETED, State.DISPUTED, CollaborationStatus.COMPLETED),
        (State.RELEASE_PENDING, State.RELEASED, CollaborationStatus.COMPLETED),
        (State.RELEASE_PENDING, State.REFUNDED, CollaborationStatus.COMPLETED),
        (State.RELEASE_PENDING, State.DISPUTED, CollaborationStatus.COMPLETED),
        (State.DISPUTED, State.CANCELLED, CollaborationStatus.CANCELLED),
        (State.DISPUTED, State.REFUNDED, CollaborationStatus.IN_PROGRESS),
    ],
)
async def test_every_declared_system_transition_is_supported(
    source, target, collaboration_status
):
    repository = MemoryPaymentRepository(status=collaboration_status)
    repository.payment = make_payment(repository, source)

    service = CollaborationPaymentService(repository)
    if target == State.AUTHORIZED_HELD:
        result = await service.authorize_hold(
            repository.collaboration.id,
            repository.payment.payer_user_id,
            "hold-auth-transition-test",
            "internal-auth-transition-reference",
        )
    elif target == State.COMPLETED:
        repository.collaboration.completed_at = datetime.now(timezone.utc).isoformat()
        result = await service.confirm_collaboration_completion(
            repository.collaboration.id
        )
    elif target == State.RELEASE_PENDING:
        repository.collaboration.completed_at = datetime.now(timezone.utc).isoformat()
        repository.payment.completed_at = datetime.now(timezone.utc)
        repository.payment.completion_confirmed_by_operation = (
            service._COMPLETION_OPERATION
        )
        result = await service.authorize_release(repository.collaboration.id)
    elif source == State.DISPUTED and target in {
        State.CANCELLED,
        State.REFUNDED,
    }:
        repository.payment.state = State.DISPUTED
        repository.payment.disputed_at = datetime.now(timezone.utc)
        repository.payment.disputed_by_operation = service._DISPUTE_OPERATION
        repository.payment.disputed_by_user_id = repository.payment.recipient_user_id
        result = await service.resolve_dispute(repository.collaboration.id, target)
    elif target == State.CANCELLED:
        repository.collaboration.status = collaboration_status
        result = await service.cancel_for_collaboration_resolution(
            repository.collaboration.id
        )
    elif target == State.REFUNDED:
        result = await service.authorize_refund(repository.collaboration.id)
    elif target == State.DISPUTED:
        result = await service.transition_by_party(
            repository.collaboration.id,
            repository.payment.recipient_user_id,
            target,
        )
    else:
        result = await service.transition_by_system(
            repository.collaboration.id, target
        )

    assert result.state == target
    assert result is repository.payment
    timestamp_by_state = {
        State.AUTHORIZED_HELD: "authorized_at",
        State.COLLABORATION_ACTIVE: "collaboration_started_at",
        State.COMPLETED: "completed_at",
        State.RELEASE_PENDING: "release_pending_at",
        State.RELEASED: "released_at",
        State.CANCELLED: "cancelled_at",
        State.REFUNDED: "refunded_at",
        State.DISPUTED: "disputed_at",
    }
    timestamp_field = timestamp_by_state.get(target)
    if timestamp_field is not None:
        assert getattr(result, timestamp_field) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "target"),
    [
        (State.PENDING, State.RELEASED),
        (State.AUTHORIZED_HELD, State.RELEASED),
        (State.COLLABORATION_ACTIVE, State.RELEASED),
        (State.RELEASED, State.REFUNDED),
        (State.REFUNDED, State.COLLABORATION_ACTIVE),
        (State.REFUNDED, State.RELEASED),
        (State.DISPUTED, State.COLLABORATION_ACTIVE),
    ],
)
async def test_invalid_transitions_are_rejected(source, target):
    repository = MemoryPaymentRepository()
    repository.payment = make_payment(repository, source)

    expected_error = (
        PermissionError if target == State.REFUNDED else ValueError
    )
    expected_message = (
        "dedicated payment failure"
        if target == State.REFUNDED
        else "Invalid payment transition"
    )
    with pytest.raises(expected_error, match=expected_message):
        await CollaborationPaymentService(repository).transition_by_system(
            repository.collaboration.id, target
        )


@pytest.mark.asyncio
async def test_completion_and_release_require_collaboration_source_of_truth():
    repository = MemoryPaymentRepository(status=CollaborationStatus.IN_PROGRESS)
    repository.payment = make_payment(repository, State.COLLABORATION_ACTIVE)
    service = CollaborationPaymentService(repository)

    with pytest.raises(ValueError, match="completed collaboration"):
        await service.confirm_collaboration_completion(repository.collaboration.id)

    assert repository.collaboration.status == CollaborationStatus.IN_PROGRESS
    assert repository.payment.state == State.COLLABORATION_ACTIVE


@pytest.mark.asyncio
async def test_completion_is_confirmed_only_for_active_payment_after_collaboration_completion():
    repository = MemoryPaymentRepository(status=CollaborationStatus.COMPLETED)
    repository.collaboration.completed_at = datetime.now(timezone.utc).isoformat()
    repository.payment = make_payment(repository, State.COLLABORATION_ACTIVE)

    completed = await CollaborationPaymentService(
        repository
    ).confirm_collaboration_completion(repository.collaboration.id)

    assert completed is repository.payment
    assert completed.state == State.COMPLETED
    assert completed.completed_at is not None
    assert (
        completed.completion_confirmed_by_operation
        == "internal_collaboration_completion_confirmation"
    )
    assert completed.release_pending_at is None


@pytest.mark.asyncio
async def test_completion_requires_authorized_hold_and_active_payment():
    repository = MemoryPaymentRepository(status=CollaborationStatus.COMPLETED)
    repository.collaboration.completed_at = datetime.now(timezone.utc).isoformat()
    repository.payment = make_payment(repository, State.PENDING)

    with pytest.raises(ValueError, match="active payment with an authorized hold"):
        await CollaborationPaymentService(
            repository
        ).confirm_collaboration_completion(repository.collaboration.id)
    assert repository.payment.state == State.PENDING


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payment_state",
    [State.CANCELLED, State.REFUNDED, State.RELEASED, State.DISPUTED],
)
async def test_cancelled_refunded_released_or_disputed_payment_cannot_complete(
    payment_state,
):
    repository = MemoryPaymentRepository(status=CollaborationStatus.COMPLETED)
    repository.collaboration.completed_at = datetime.now(timezone.utc).isoformat()
    repository.payment = make_payment(repository, payment_state)

    with pytest.raises(ValueError, match="active payment with an authorized hold"):
        await CollaborationPaymentService(
            repository
        ).confirm_collaboration_completion(repository.collaboration.id)

    assert repository.payment.state == payment_state


@pytest.mark.asyncio
async def test_collaboration_without_payment_keeps_existing_completion_behavior():
    repository = MemoryPaymentRepository(status=CollaborationStatus.COMPLETED)
    repository.collaboration.completed_at = datetime.now(timezone.utc).isoformat()

    result = await CollaborationPaymentService(
        repository
    ).confirm_collaboration_completion(repository.collaboration.id)

    assert result is None


@pytest.mark.asyncio
async def test_completion_rejects_wrong_collaboration_and_forged_payment_state():
    repository = MemoryPaymentRepository(status=CollaborationStatus.COMPLETED)
    repository.collaboration.completed_at = datetime.now(timezone.utc).isoformat()
    repository.payment = make_payment(repository, State.COLLABORATION_ACTIVE)
    service = CollaborationPaymentService(repository)

    with pytest.raises(ValueError, match="Collaboration not found"):
        await service.confirm_collaboration_completion(uuid.uuid4())
    with pytest.raises(PermissionError, match="dedicated payment failure"):
        await service.transition_by_system(repository.collaboration.id, State.COMPLETED)
    with pytest.raises(PermissionError, match="directly change"):
        await service.transition_by_party(
            repository.collaboration.id,
            repository.payment.payer_user_id,
            State.COMPLETED,
        )
    with pytest.raises(PermissionError, match="dedicated payment failure"):
        await service.transition_by_system(
            repository.collaboration.id, State.RELEASE_PENDING
        )
    with pytest.raises(PermissionError, match="directly change"):
        await service.transition_by_party(
            repository.collaboration.id,
            repository.payment.payer_user_id,
            State.RELEASE_PENDING,
        )
    assert repository.payment.state == State.COLLABORATION_ACTIVE


@pytest.mark.asyncio
async def test_completion_confirmation_is_idempotent_and_preserves_timestamp():
    repository = MemoryPaymentRepository(status=CollaborationStatus.COMPLETED)
    repository.collaboration.completed_at = datetime.now(timezone.utc).isoformat()
    repository.payment = make_payment(repository, State.COLLABORATION_ACTIVE)
    service = CollaborationPaymentService(repository)

    first = await service.confirm_collaboration_completion(repository.collaboration.id)
    completed_at = first.completed_at
    replay = await service.confirm_collaboration_completion(repository.collaboration.id)

    assert replay is first
    assert replay.state == State.COMPLETED
    assert replay.completed_at == completed_at


@pytest.mark.asyncio
async def test_completion_and_release_operations_lock_collaboration_and_payment_rows():
    repository = MemoryPaymentRepository(status=CollaborationStatus.COMPLETED)
    repository.collaboration.completed_at = datetime.now(timezone.utc).isoformat()
    repository.payment = make_payment(repository, State.COLLABORATION_ACTIVE)
    service = CollaborationPaymentService(repository)

    await service.confirm_collaboration_completion(repository.collaboration.id)
    await service.authorize_release(repository.collaboration.id)

    assert repository.lock_requests == [
        ("collaboration", True),
        ("payment", True),
        ("collaboration", True),
        ("payment", True),
    ]


@pytest.mark.asyncio
async def test_release_authorization_requires_completion_and_is_idempotent():
    repository = MemoryPaymentRepository(status=CollaborationStatus.IN_PROGRESS)
    repository.collaboration.completed_at = None
    repository.payment = make_payment(repository, State.COLLABORATION_ACTIVE)
    service = CollaborationPaymentService(repository)

    with pytest.raises(ValueError, match="completed collaboration"):
        await service.authorize_release(repository.collaboration.id)

    repository.collaboration.status = CollaborationStatus.COMPLETED
    repository.collaboration.completed_at = datetime.now(timezone.utc).isoformat()
    await service.confirm_collaboration_completion(repository.collaboration.id)
    first = await service.authorize_release(repository.collaboration.id)
    release_pending_at = first.release_pending_at
    replay = await service.authorize_release(repository.collaboration.id)

    assert replay is first
    assert replay.state == State.RELEASE_PENDING
    assert replay.release_pending_at == release_pending_at
    assert replay.release_authorized_by_operation == "internal_release_authorization"
    assert replay.released_at is None


@pytest.mark.asyncio
async def test_release_authorization_rejects_missing_hold_and_terminal_states():
    repository = MemoryPaymentRepository(status=CollaborationStatus.COMPLETED)
    repository.collaboration.completed_at = datetime.now(timezone.utc).isoformat()
    repository.payment = make_payment(repository, State.COMPLETED)
    repository.payment.completed_at = datetime.now(timezone.utc)
    repository.payment.completion_confirmed_by_operation = (
        "internal_collaboration_completion_confirmation"
    )
    repository.payment.authorized_at = None
    repository.payment.hold_idempotency_key = None
    repository.payment.authorized_by_operation = None

    with pytest.raises(ValueError, match="valid held authorization"):
        await CollaborationPaymentService(
            repository
        ).authorize_release(repository.collaboration.id)


@pytest.mark.asyncio
async def test_release_authorization_rejects_wrong_collaboration():
    repository = MemoryPaymentRepository(status=CollaborationStatus.COMPLETED)
    repository.collaboration.completed_at = datetime.now(timezone.utc).isoformat()
    repository.payment = make_payment(repository, State.COMPLETED)
    repository.payment.completed_at = datetime.now(timezone.utc)
    repository.payment.completion_confirmed_by_operation = (
        "internal_collaboration_completion_confirmation"
    )

    with pytest.raises(ValueError, match="Collaboration not found"):
        await CollaborationPaymentService(repository).authorize_release(uuid.uuid4())

    for state in (State.CANCELLED, State.REFUNDED, State.RELEASED, State.DISPUTED):
        repository.payment = make_payment(repository, state)
        with pytest.raises(ValueError, match="completed, held payment"):
            await CollaborationPaymentService(
                repository
            ).authorize_release(repository.collaboration.id)


@pytest.mark.asyncio
async def test_parties_can_read_own_payment_but_outsiders_are_denied():
    repository = MemoryPaymentRepository()
    repository.payment = make_payment(repository, State.PENDING)
    service = CollaborationPaymentService(repository)

    assert (
        await service.get_payment_for_collaboration(
            repository.collaboration.id, repository.payment.payer_user_id
        )
    ) is repository.payment
    with pytest.raises(PermissionError, match="access denied"):
        await service.get_payment_for_collaboration(
            repository.collaboration.id, uuid.uuid4()
        )


@pytest.mark.asyncio
async def test_mismatched_payment_parties_fail_closed_on_retrieval():
    repository = MemoryPaymentRepository()
    repository.payment = make_payment(repository, State.PENDING)
    repository.payment.payer_user_id = uuid.uuid4()

    with pytest.raises(ValueError, match="do not match"):
        await CollaborationPaymentService(
            repository
        ).get_payment_for_collaboration(
            repository.collaboration.id, repository.payment.recipient_user_id
        )


@pytest.mark.asyncio
async def test_party_cannot_release_or_set_arbitrary_state_and_refund_is_immutable():
    repository = MemoryPaymentRepository(status=CollaborationStatus.COMPLETED)
    repository.payment = make_payment(repository, State.RELEASE_PENDING)
    service = CollaborationPaymentService(repository)

    with pytest.raises(PermissionError, match="cannot directly change"):
        await service.transition_by_party(
            repository.collaboration.id,
            repository.payment.payer_user_id,
            State.RELEASED,
        )
    assert repository.payment.state == State.RELEASE_PENDING

    repository.payment.state = State.RELEASED
    with pytest.raises(ValueError, match="Invalid payment transition"):
        await service.transition_by_system(repository.collaboration.id, State.PENDING)

    repository.payment.state = State.REFUNDED
    with pytest.raises(ValueError, match="Invalid payment transition"):
        await service.transition_by_system(
            repository.collaboration.id, State.COLLABORATION_ACTIVE
        )


@pytest.mark.asyncio
async def test_party_cancellation_and_dispute_are_limited_to_allowed_requests():
    repository = MemoryPaymentRepository()
    repository.payment = make_payment(repository, State.PENDING)
    service = CollaborationPaymentService(repository)
    recipient_id = repository.payment.recipient_user_id

    with pytest.raises(PermissionError, match="collaboration lifecycle"):
        await service.transition_by_party(
            repository.collaboration.id,
            repository.payment.payer_user_id,
            State.CANCELLED,
        )
    assert repository.payment.state == State.PENDING
    with pytest.raises(PermissionError, match="cannot directly change"):
        await service.transition_by_party(
            repository.collaboration.id,
            repository.payment.payer_user_id,
            State.AUTHORIZED_HELD,
        )

    repository.payment.state = State.AUTHORIZED_HELD
    repository.payment.hold_idempotency_key = "fixture-held-key"
    repository.payment.authorization_reference = "fixture-held-reference"
    repository.payment.authorized_by_operation = "internal_hold_authorization"
    repository.payment.authorized_at = datetime.now(timezone.utc)
    result = await service.transition_by_party(
        repository.collaboration.id, recipient_id, State.DISPUTED
    )
    assert result.state == State.DISPUTED
    assert result.disputed_by_user_id == recipient_id
    assert result.disputed_by_operation == service._DISPUTE_OPERATION
    assert result.disputed_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("collaboration_status", "payment_state"),
    [
        (CollaborationStatus.DECLINED, State.PENDING),
        (CollaborationStatus.CANCELLED, State.AUTHORIZED_HELD),
        (CollaborationStatus.DECLINED, State.COLLABORATION_ACTIVE),
    ],
)
async def test_collaboration_rejection_or_cancellation_cancels_payment(
    collaboration_status, payment_state
):
    repository = MemoryPaymentRepository(status=collaboration_status)
    repository.payment = make_payment(repository, payment_state)
    service = CollaborationPaymentService(repository)

    cancelled = await service.cancel_for_collaboration_resolution(
        repository.collaboration.id
    )

    assert cancelled.state == State.CANCELLED
    assert cancelled.cancelled_at is not None
    assert cancelled.cancelled_by_operation == service._CANCELLATION_OPERATION
    if payment_state == State.PENDING:
        assert cancelled.hold_reversed_at is None
    else:
        assert cancelled.hold_reversed_at is not None
        assert cancelled.hold_reversed_by_operation == service._CANCELLATION_OPERATION


@pytest.mark.asyncio
async def test_cancellation_replay_preserves_cancellation_and_reversal_timestamps():
    repository = MemoryPaymentRepository(status=CollaborationStatus.CANCELLED)
    repository.payment = make_payment(repository, State.AUTHORIZED_HELD)
    service = CollaborationPaymentService(repository)

    first = await service.cancel_for_collaboration_resolution(
        repository.collaboration.id
    )
    cancelled_at = first.cancelled_at
    reversed_at = first.hold_reversed_at
    replay = await service.cancel_for_collaboration_resolution(
        repository.collaboration.id
    )

    assert replay is first
    assert replay.cancelled_at == cancelled_at
    assert replay.hold_reversed_at == reversed_at


@pytest.mark.asyncio
async def test_cancellation_requires_authoritative_collaboration_and_exact_relationship():
    repository = MemoryPaymentRepository(status=CollaborationStatus.IN_PROGRESS)
    repository.payment = make_payment(repository, State.PENDING)
    service = CollaborationPaymentService(repository)

    with pytest.raises(ValueError, match="cancelled or declined collaboration"):
        await service.cancel_for_collaboration_resolution(repository.collaboration.id)
    with pytest.raises(ValueError, match="Collaboration not found"):
        await service.cancel_for_collaboration_resolution(uuid.uuid4())

    repository.collaboration.status = CollaborationStatus.CANCELLED
    repository.payment.collaboration_id = uuid.uuid4()
    async def return_mismatched_payment(collaboration_id, *, for_update=False):
        return repository.payment

    repository.get_for_collaboration = return_mismatched_payment
    with pytest.raises(ValueError, match="relationship is invalid"):
        await service.cancel_for_collaboration_resolution(repository.collaboration.id)


@pytest.mark.asyncio
async def test_decline_can_cancel_held_payment_after_invitee_participant_is_removed():
    repository = MemoryPaymentRepository(status=CollaborationStatus.DECLINED)
    repository.payment = make_payment(repository, State.AUTHORIZED_HELD)
    repository.accepted_participants.clear()
    service = CollaborationPaymentService(repository)
    await service.cancel_for_invitee_decline(
        repository.collaboration.id, repository.payment.recipient_user_id
    )
    repository.participants.remove(repository.payment.recipient_user_id)

    cancelled = await service.cancel_for_collaboration_resolution(
        repository.collaboration.id
    )

    assert cancelled.state == State.CANCELLED
    assert cancelled.hold_reversed_at is not None


@pytest.mark.asyncio
async def test_rejected_payment_recipient_is_cancelled_even_when_other_invites_remain():
    repository = MemoryPaymentRepository(status=CollaborationStatus.PROPOSED)
    repository.payment = make_payment(repository, State.AUTHORIZED_HELD)
    repository.accepted_participants.clear()

    cancelled = await CollaborationPaymentService(
        repository
    ).cancel_for_invitee_decline(
        repository.collaboration.id, repository.payment.recipient_user_id
    )

    assert repository.collaboration.status == CollaborationStatus.PROPOSED
    assert cancelled.state == State.CANCELLED
    assert cancelled.hold_reversed_at is not None


@pytest.mark.asyncio
async def test_accepted_collaborator_cannot_use_invitee_rejection_cancellation():
    repository = MemoryPaymentRepository(status=CollaborationStatus.PROPOSED)
    repository.payment = make_payment(repository, State.PENDING)

    with pytest.raises(PermissionError, match="pending collaboration invitee"):
        await CollaborationPaymentService(
            repository
        ).cancel_for_invitee_decline(
            repository.collaboration.id, repository.payment.recipient_user_id
        )
    assert repository.payment.state == State.PENDING


@pytest.mark.asyncio
async def test_cancellation_of_release_pending_authorizes_refund_not_release():
    repository = MemoryPaymentRepository(status=CollaborationStatus.CANCELLED)
    repository.payment = make_payment(repository, State.RELEASE_PENDING)

    refunded = await CollaborationPaymentService(
        repository
    ).cancel_for_collaboration_resolution(repository.collaboration.id)

    assert refunded.state == State.REFUNDED
    assert refunded.refund_authorized_at is not None
    assert refunded.released_at is None


@pytest.mark.asyncio
async def test_refund_authorization_is_trusted_idempotent_and_audited():
    repository = MemoryPaymentRepository(status=CollaborationStatus.COMPLETED)
    repository.payment = make_payment(repository, State.RELEASE_PENDING)
    service = CollaborationPaymentService(repository)

    with pytest.raises(PermissionError, match="dedicated payment failure"):
        await service.transition_by_system(repository.collaboration.id, State.REFUNDED)
    with pytest.raises(PermissionError, match="directly change"):
        await service.transition_by_party(
            repository.collaboration.id,
            repository.payment.payer_user_id,
            State.REFUNDED,
        )

    first = await service.authorize_refund(repository.collaboration.id)
    authorized_at = first.refund_authorized_at
    reversed_at = first.hold_reversed_at
    replay = await service.authorize_refund(repository.collaboration.id)

    assert replay is first
    assert replay.state == State.REFUNDED
    assert replay.refunded_at is not None
    assert replay.refund_authorized_at == authorized_at
    assert replay.refund_authorized_by_operation == service._REFUND_OPERATION
    assert replay.hold_reversed_at == reversed_at


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [State.PENDING, State.CANCELLED, State.RELEASED])
async def test_refund_rejects_pending_and_terminal_states(state):
    repository = MemoryPaymentRepository(status=CollaborationStatus.COMPLETED)
    repository.payment = make_payment(repository, state)

    with pytest.raises(ValueError, match="cannot be refunded"):
        await CollaborationPaymentService(repository).authorize_refund(
            repository.collaboration.id
        )


@pytest.mark.asyncio
async def test_dispute_is_idempotent_and_requires_payment_party_authorization():
    repository = MemoryPaymentRepository(status=CollaborationStatus.IN_PROGRESS)
    repository.payment = make_payment(repository, State.COLLABORATION_ACTIVE)
    service = CollaborationPaymentService(repository)
    actor_id = repository.payment.recipient_user_id

    first = await service.transition_by_party(
        repository.collaboration.id, actor_id, State.DISPUTED
    )
    disputed_at = first.disputed_at
    replay = await service.transition_by_party(
        repository.collaboration.id, actor_id, State.DISPUTED
    )

    assert replay is first
    assert replay.state == State.DISPUTED
    assert replay.disputed_at == disputed_at
    assert replay.disputed_by_user_id == actor_id

    with pytest.raises(PermissionError, match="Payment access denied"):
        await service.transition_by_party(
            repository.collaboration.id, uuid.uuid4(), State.DISPUTED
        )


@pytest.mark.asyncio
async def test_disputed_payment_cannot_release_and_resolves_only_through_trusted_service():
    repository = MemoryPaymentRepository(status=CollaborationStatus.COMPLETED)
    repository.collaboration.completed_at = datetime.now(timezone.utc).isoformat()
    repository.payment = make_payment(repository, State.COMPLETED)
    repository.payment.completed_at = datetime.now(timezone.utc)
    repository.payment.completion_confirmed_by_operation = (
        "internal_collaboration_completion_confirmation"
    )
    service = CollaborationPaymentService(repository)
    await service.transition_by_party(
        repository.collaboration.id,
        repository.payment.recipient_user_id,
        State.DISPUTED,
    )

    with pytest.raises(ValueError, match="completed, held payment"):
        await service.authorize_release(repository.collaboration.id)
    with pytest.raises(PermissionError, match="dedicated payment failure"):
        await service.transition_by_system(
            repository.collaboration.id, State.REFUNDED
        )

    resolved = await service.resolve_dispute(
        repository.collaboration.id, State.REFUNDED
    )
    resolved_at = resolved.dispute_resolved_at
    replay = await service.resolve_dispute(
        repository.collaboration.id, State.REFUNDED
    )
    assert replay is resolved
    assert resolved.state == State.REFUNDED
    assert resolved.dispute_resolved_at == resolved_at
    assert (
        resolved.dispute_resolved_by_operation
        == service._DISPUTE_RESOLUTION_OPERATION
    )
    assert resolved.released_at is None


@pytest.mark.asyncio
async def test_dispute_resolution_rejects_release_and_conflicting_replay():
    repository = MemoryPaymentRepository(status=CollaborationStatus.IN_PROGRESS)
    repository.payment = make_payment(repository, State.COLLABORATION_ACTIVE)
    service = CollaborationPaymentService(repository)
    await service.transition_by_party(
        repository.collaboration.id,
        repository.payment.recipient_user_id,
        State.DISPUTED,
    )

    with pytest.raises(ValueError, match="only to cancelled or refunded"):
        await service.resolve_dispute(
            repository.collaboration.id, State.RELEASE_PENDING
        )
    await service.resolve_dispute(repository.collaboration.id, State.CANCELLED)
    with pytest.raises(ValueError, match="already resolved differently"):
        await service.resolve_dispute(repository.collaboration.id, State.REFUNDED)


@pytest.mark.asyncio
async def test_failed_financial_persistence_restores_state_and_audit():
    repository = MemoryPaymentRepository(status=CollaborationStatus.COMPLETED)
    repository.payment = make_payment(repository, State.COMPLETED)

    async def fail_save(payment):
        raise RuntimeError("simulated persistence failure")

    repository.save = fail_save
    with pytest.raises(RuntimeError, match="simulated persistence failure"):
        await CollaborationPaymentService(repository).authorize_refund(
            repository.collaboration.id
        )

    assert repository.payment.state == State.COMPLETED
    assert repository.payment.refunded_at is None
    assert repository.payment.refund_authorized_at is None
    assert repository.payment.refund_authorized_by_operation is None
    assert repository.payment.hold_reversed_at is None


@pytest.mark.asyncio
async def test_concurrent_refund_and_release_cannot_produce_both_outcomes():
    repository = SerializedMemoryPaymentRepository(
        status=CollaborationStatus.COMPLETED
    )
    repository.collaboration.completed_at = datetime.now(timezone.utc).isoformat()
    repository.payment = make_payment(repository, State.COMPLETED)
    repository.payment.completed_at = datetime.now(timezone.utc)
    repository.payment.completion_confirmed_by_operation = (
        "internal_collaboration_completion_confirmation"
    )
    service = CollaborationPaymentService(repository)

    results = await asyncio.gather(
        run_locked_transaction(
            repository, service.authorize_refund(repository.collaboration.id)
        ),
        run_locked_transaction(
            repository, service.authorize_release(repository.collaboration.id)
        ),
        return_exceptions=True,
    )

    assert any(isinstance(result, CollaborationPayment) for result in results)
    assert repository.payment.state == State.REFUNDED
    assert repository.payment.released_at is None


@pytest.mark.asyncio
async def test_concurrent_refund_and_release_transition_have_one_terminal_outcome():
    repository = SerializedMemoryPaymentRepository(
        status=CollaborationStatus.COMPLETED
    )
    repository.payment = make_payment(repository, State.RELEASE_PENDING)
    service = CollaborationPaymentService(repository)

    results = await asyncio.gather(
        run_locked_transaction(
            repository, service.authorize_refund(repository.collaboration.id)
        ),
        run_locked_transaction(
            repository,
            service.transition_by_system(
                repository.collaboration.id, State.RELEASED
            ),
        ),
        return_exceptions=True,
    )

    assert any(isinstance(result, CollaborationPayment) for result in results)
    assert repository.payment.state in {State.REFUNDED, State.RELEASED}
    if repository.payment.state == State.REFUNDED:
        assert repository.payment.refunded_at is not None
        assert repository.payment.released_at is None
    else:
        assert repository.payment.released_at is not None
        assert repository.payment.refunded_at is None


@pytest.mark.asyncio
async def test_concurrent_collaboration_cancellation_and_release_cannot_authorize_release():
    repository = SerializedMemoryPaymentRepository(
        status=CollaborationStatus.IN_PROGRESS
    )
    repository.payment = make_payment(repository, State.COLLABORATION_ACTIVE)
    service = CollaborationPaymentService(repository)

    async def cancel_lifecycle():
        collaboration = await repository.get_collaboration(
            repository.collaboration.id, for_update=True
        )
        try:
            collaboration.status = CollaborationStatus.CANCELLED
            return await service.cancel_for_collaboration_resolution(
                repository.collaboration.id
            )
        finally:
            repository.finish_transaction()

    results = await asyncio.gather(
        cancel_lifecycle(),
        run_locked_transaction(
            repository, service.authorize_release(repository.collaboration.id)
        ),
        return_exceptions=True,
    )

    assert any(isinstance(result, CollaborationPayment) for result in results)
    assert repository.payment.state == State.CANCELLED
    assert repository.payment.release_pending_at is None


@pytest.mark.asyncio
async def test_concurrent_dispute_and_release_cannot_release_a_disputed_payment():
    repository = SerializedMemoryPaymentRepository(
        status=CollaborationStatus.COMPLETED
    )
    repository.collaboration.completed_at = datetime.now(timezone.utc).isoformat()
    repository.payment = make_payment(repository, State.COMPLETED)
    repository.payment.completed_at = datetime.now(timezone.utc)
    repository.payment.completion_confirmed_by_operation = (
        "internal_collaboration_completion_confirmation"
    )
    service = CollaborationPaymentService(repository)

    results = await asyncio.gather(
        run_locked_transaction(
            repository,
            service.transition_by_party(
                repository.collaboration.id,
                repository.payment.recipient_user_id,
                State.DISPUTED,
            ),
        ),
        run_locked_transaction(
            repository, service.authorize_release(repository.collaboration.id)
        ),
        return_exceptions=True,
    )

    assert any(isinstance(result, CollaborationPayment) for result in results)
    assert repository.payment.state == State.DISPUTED
    with pytest.raises(ValueError, match="completed, held payment"):
        await service.authorize_release(repository.collaboration.id)


@pytest.mark.asyncio
async def test_concurrent_duplicate_refunds_are_serialized_and_idempotent():
    repository = SerializedMemoryPaymentRepository(
        status=CollaborationStatus.COMPLETED
    )
    repository.payment = make_payment(repository, State.COMPLETED)
    service = CollaborationPaymentService(repository)

    results = await asyncio.gather(
        run_locked_transaction(
            repository, service.authorize_refund(repository.collaboration.id)
        ),
        run_locked_transaction(
            repository, service.authorize_refund(repository.collaboration.id)
        ),
        return_exceptions=True,
    )

    assert all(isinstance(result, CollaborationPayment) for result in results)
    assert results[0] is results[1] is repository.payment
    assert repository.payment.state == State.REFUNDED


@pytest.mark.asyncio
async def test_concurrent_duplicate_disputes_are_serialized_and_idempotent():
    repository = SerializedMemoryPaymentRepository(
        status=CollaborationStatus.IN_PROGRESS
    )
    repository.payment = make_payment(repository, State.COLLABORATION_ACTIVE)
    service = CollaborationPaymentService(repository)
    actor_id = repository.payment.recipient_user_id

    results = await asyncio.gather(
        run_locked_transaction(
            repository,
            service.transition_by_party(
                repository.collaboration.id, actor_id, State.DISPUTED
            ),
        ),
        run_locked_transaction(
            repository,
            service.transition_by_party(
                repository.collaboration.id, actor_id, State.DISPUTED
            ),
        ),
        return_exceptions=True,
    )

    assert all(isinstance(result, CollaborationPayment) for result in results)
    assert results[0] is results[1] is repository.payment
    assert repository.payment.state == State.DISPUTED
    assert repository.payment.disputed_at is not None


@pytest.mark.asyncio
async def test_acceptance_activates_held_payment_idempotently_in_lock_order():
    repository = MemoryPaymentRepository(status=CollaborationStatus.ACCEPTED)
    repository.payment = make_payment(repository, State.AUTHORIZED_HELD)
    service = CollaborationPaymentService(repository)

    activated = await service.activate_for_collaboration_acceptance(
        repository.collaboration.id
    )
    assert activated is not None
    activated_at = activated.collaboration_started_at
    replay = await service.activate_for_collaboration_acceptance(
        repository.collaboration.id
    )

    assert replay is not None
    assert activated is replay is repository.payment
    assert activated.state == State.COLLABORATION_ACTIVE
    assert activated_at is not None
    assert replay.collaboration_started_at == activated_at
    assert repository.lock_requests == [
        ("collaboration", True),
        ("payment", True),
        ("collaboration", True),
        ("payment", True),
    ]


@pytest.mark.asyncio
async def test_authorization_after_acceptance_activates_held_payment():
    repository = MemoryPaymentRepository(status=CollaborationStatus.ACCEPTED)
    repository.payment = make_payment(repository, State.PENDING)

    payment = await CollaborationPaymentService(repository).authorize_hold(
        repository.collaboration.id,
        repository.payment.payer_user_id,
        "accepted-hold-key",
        "accepted-hold-reference",
    )

    assert payment.state == State.COLLABORATION_ACTIVE
    assert payment.authorized_at is not None
    assert payment.collaboration_started_at is not None


@pytest.mark.asyncio
async def test_acceptance_activation_is_a_noop_without_a_payment():
    repository = MemoryPaymentRepository(status=CollaborationStatus.ACCEPTED)

    result = await CollaborationPaymentService(
        repository
    ).activate_for_collaboration_acceptance(repository.collaboration.id)

    assert result is None
    assert repository.payment is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal_state", [State.CANCELLED, State.REFUNDED, State.RELEASED]
)
async def test_terminal_payment_rejects_same_key_authorization_replay(terminal_state):
    repository = MemoryPaymentRepository()
    repository.payment = make_payment(repository, terminal_state)
    hold_key = repository.payment.hold_idempotency_key
    authorization_reference = repository.payment.authorization_reference
    assert hold_key is not None
    assert authorization_reference is not None

    with pytest.raises(ValueError, match=f"in {terminal_state.value} state"):
        await CollaborationPaymentService(repository).authorize_hold(
            repository.collaboration.id,
            repository.payment.payer_user_id,
            hold_key,
            authorization_reference,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["authorize", "cancel", "complete", "release"])
async def test_concurrent_duplicate_payment_operations_are_idempotent(operation):
    if operation == "authorize":
        repository = SerializedMemoryPaymentRepository(
            status=CollaborationStatus.PROPOSED
        )
        repository.payment = make_payment(repository, State.PENDING)
        service = CollaborationPaymentService(repository)
        operations = [
            service.authorize_hold(
                repository.collaboration.id,
                repository.payment.payer_user_id,
                "concurrent-hold-key",
                "concurrent-hold-reference",
            )
            for _ in range(2)
        ]
        expected_state = State.AUTHORIZED_HELD
    elif operation == "cancel":
        repository = SerializedMemoryPaymentRepository(
            status=CollaborationStatus.CANCELLED
        )
        repository.payment = make_payment(repository, State.AUTHORIZED_HELD)
        service = CollaborationPaymentService(repository)
        operations = [
            service.cancel_for_collaboration_resolution(
                repository.collaboration.id
            )
            for _ in range(2)
        ]
        expected_state = State.CANCELLED
    elif operation == "complete":
        repository = SerializedMemoryPaymentRepository(
            status=CollaborationStatus.COMPLETED
        )
        repository.collaboration.completed_at = datetime.now(timezone.utc).isoformat()
        repository.payment = make_payment(repository, State.COLLABORATION_ACTIVE)
        service = CollaborationPaymentService(repository)
        operations = [
            service.confirm_collaboration_completion(
                repository.collaboration.id
            )
            for _ in range(2)
        ]
        expected_state = State.COMPLETED
    else:
        repository = SerializedMemoryPaymentRepository(
            status=CollaborationStatus.COMPLETED
        )
        repository.collaboration.completed_at = datetime.now(timezone.utc).isoformat()
        repository.payment = make_payment(repository, State.COMPLETED)
        repository.payment.completed_at = datetime.now(timezone.utc)
        repository.payment.completion_confirmed_by_operation = (
            "internal_collaboration_completion_confirmation"
        )
        service = CollaborationPaymentService(repository)
        operations = [
            service.authorize_release(repository.collaboration.id)
            for _ in range(2)
        ]
        expected_state = State.RELEASE_PENDING

    results = await asyncio.gather(
        *(
            run_locked_transaction(repository, operation)
            for operation in operations
        ),
        return_exceptions=True,
    )

    assert all(isinstance(result, CollaborationPayment) for result in results)
    assert results[0] is results[1] is repository.payment
    assert repository.payment.state == expected_state


@pytest.mark.asyncio
async def test_authorizes_hold_once_and_returns_existing_state_on_replay():
    repository = MemoryPaymentRepository(status=CollaborationStatus.ACCEPTED)
    repository.payment = make_payment(repository, State.PENDING)
    service = CollaborationPaymentService(repository)

    first = await service.authorize_hold(
        repository.collaboration.id,
        repository.payment.payer_user_id,
        "auth-operation-1",
        "internal-auth-reference-1",
    )
    authorized_at = first.authorized_at
    active = await service.activate_for_collaboration_acceptance(
        repository.collaboration.id
    )

    replay = await service.authorize_hold(
        repository.collaboration.id,
        repository.payment.payer_user_id,
        "auth-operation-1",
        "internal-auth-reference-1",
    )

    assert first is active is replay is repository.payment
    assert replay.state == State.COLLABORATION_ACTIVE
    assert replay.authorized_at == authorized_at
    assert replay.hold_reversed_at is None
    assert repository.create_calls == 0


@pytest.mark.asyncio
async def test_authorization_rejects_wrong_payer_wrong_collaboration_and_invalid_state():
    repository = MemoryPaymentRepository()
    repository.payment = make_payment(repository, State.PENDING)
    service = CollaborationPaymentService(repository)
    recipient_id = repository.payment.recipient_user_id

    with pytest.raises(PermissionError, match="Only the payment payer"):
        await service.authorize_hold(
            repository.collaboration.id, recipient_id, "wrong-payer-key"
        )
    with pytest.raises(ValueError, match="Collaboration not found"):
        await service.authorize_hold(
            uuid.uuid4(),
            repository.payment.payer_user_id,
            "wrong-collaboration-key",
        )

    repository.payment = make_payment(repository, State.CANCELLED)
    with pytest.raises(ValueError, match="Cannot authorize hold"):
        await service.authorize_hold(
            repository.collaboration.id,
            repository.payment.payer_user_id,
            "cancelled-key",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_state", [State.CANCELLED, State.REFUNDED, State.RELEASED])
async def test_terminal_payment_cannot_be_authorized_or_held_again(terminal_state):
    repository = MemoryPaymentRepository()
    repository.payment = make_payment(repository, terminal_state)

    with pytest.raises(ValueError, match="Cannot authorize hold"):
        await CollaborationPaymentService(repository).authorize_hold(
            repository.collaboration.id,
            repository.payment.payer_user_id,
            "terminal-replay-key",
        )


@pytest.mark.asyncio
async def test_client_party_cannot_forge_authorized_held_state():
    repository = MemoryPaymentRepository()
    repository.payment = make_payment(repository, State.PENDING)
    service = CollaborationPaymentService(repository)

    with pytest.raises(PermissionError, match="directly change"):
        await service.transition_by_party(
            repository.collaboration.id,
            repository.payment.payer_user_id,
            State.AUTHORIZED_HELD,
        )
    with pytest.raises(PermissionError, match="dedicated payment failure"):
        await service.transition_by_system(
            repository.collaboration.id, State.AUTHORIZED_HELD
        )
    assert repository.payment.state == State.PENDING


@pytest.mark.asyncio
async def test_hold_authorization_and_reversal_audit_timestamps_are_consistent():
    repository = MemoryPaymentRepository()
    repository.payment = make_payment(repository, State.PENDING)
    service = CollaborationPaymentService(repository)

    authorized = await service.authorize_hold(
        repository.collaboration.id,
        repository.payment.payer_user_id,
        "audit-hold-key",
        "provider-neutral-reference",
    )
    assert authorized.state == State.AUTHORIZED_HELD
    assert authorized.authorized_at is not None
    assert authorized.authorized_by_operation == "internal_hold_authorization"
    assert authorized.authorization_reference == "provider-neutral-reference"
    assert authorized.hold_reversed_at is None
    assert authorized.hold_reversed_by_operation is None

    repository.collaboration.status = CollaborationStatus.CANCELLED
    cancelled = await service.cancel_for_collaboration_resolution(
        repository.collaboration.id
    )
    assert cancelled.state == State.CANCELLED
    assert cancelled.authorized_at <= cancelled.hold_reversed_at
    assert (
        cancelled.hold_reversed_by_operation
        == service._CANCELLATION_OPERATION
    )


@pytest.mark.asyncio
async def test_idempotency_key_cannot_be_reused_for_another_reference():
    repository = MemoryPaymentRepository()
    repository.payment = make_payment(repository, State.PENDING)
    service = CollaborationPaymentService(repository)

    await service.authorize_hold(
        repository.collaboration.id,
        repository.payment.payer_user_id,
        "reused-key",
        "first-reference",
    )
    with pytest.raises(ValueError, match="already used"):
        await service.authorize_hold(
            repository.collaboration.id,
            repository.payment.payer_user_id,
            "reused-key",
            "different-reference",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("amount", "currency"),
    [
        (0, "USD"),
        (-1, "USD"),
        (True, "USD"),
        (1.5, "USD"),
        (9_223_372_036_854_775_808, "USD"),
        (100, "usd"),
        (100, "US"),
        (100, "US1"),
    ],
)
async def test_rejects_invalid_amount_or_currency(amount, currency):
    repository = MemoryPaymentRepository()
    recipient_id = next(
        user_id
        for user_id in repository.participants
        if user_id != repository.initiator_id
    )

    with pytest.raises(ValueError, match="Amount|Currency"):
        await CollaborationPaymentService(repository).create_pending_payment(
            repository.collaboration.id,
            repository.initiator_id,
            recipient_id,
            amount,
            currency,
        )


def test_payment_schema_enforces_one_to_one_relationship_and_financial_constraints():
    table = CollaborationPayment.__table__
    unique_constraints = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }

    assert ("collaboration_id",) in unique_constraints
    assert ("payment_provider", "provider_reference") in unique_constraints
    assert {
        "ck_collaboration_payments_positive_amount",
        "ck_collaboration_payments_currency",
        "ck_collaboration_payments_distinct_parties",
        "ck_collaboration_payments_provider_reference_pair",
        "ck_collaboration_payments_hold_authorization_audit",
        "ck_collaboration_payments_held_states_authorized",
        "ck_collaboration_payments_hold_reversal_audit",
        "ck_collaboration_payments_completion_audit",
        "ck_collaboration_payments_release_authorization_audit",
        "ck_collaboration_payments_refund_authorization_audit",
        "ck_collaboration_payments_refunded_has_authorization",
        "ck_collaboration_payments_dispute_initiation_audit",
        "ck_collaboration_payments_dispute_resolution_audit",
        "ck_collaboration_payments_cancellation_audit",
    }.issubset(
        {
            constraint.name
            for constraint in table.constraints
            if constraint.__class__.__name__ == "CheckConstraint"
        }
    )
    assert CollaborationPayment.collaboration.property.uselist is False
    assert set(VALID_PAYMENT_TRANSITIONS) == set(State)
