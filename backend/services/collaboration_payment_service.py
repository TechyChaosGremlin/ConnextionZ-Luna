"""Server-side collaboration payment ownership and state transition rules."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from app.models.collaboration import Collaboration, CollaborationStatus
from app.models.collaboration_payment import (
    CollaborationPayment,
    CollaborationPaymentState,
)
from app.payments.factory import get_payment_provider
from app.payments.provider import (
    PaymentProvider,
    ProviderResult,
    ProviderStatus,
)
from repositories.collaboration_payment_repository import CollaborationPaymentRepository

_MAX_MINOR_UNITS = 9_223_372_036_854_775_807
_CURRENCY_CODE = re.compile(r"^[A-Z]{3}$", re.ASCII)

VALID_PAYMENT_TRANSITIONS: dict[
    CollaborationPaymentState, frozenset[CollaborationPaymentState]
] = {
    CollaborationPaymentState.PENDING: frozenset(
        {
            CollaborationPaymentState.AUTHORIZED_HELD,
            CollaborationPaymentState.CANCELLED,
        }
    ),
    CollaborationPaymentState.AUTHORIZED_HELD: frozenset(
        {
            CollaborationPaymentState.COLLABORATION_ACTIVE,
            CollaborationPaymentState.CANCELLED,
            CollaborationPaymentState.REFUNDED,
            CollaborationPaymentState.DISPUTED,
        }
    ),
    CollaborationPaymentState.COLLABORATION_ACTIVE: frozenset(
        {
            CollaborationPaymentState.COMPLETED,
            CollaborationPaymentState.CANCELLED,
            CollaborationPaymentState.REFUNDED,
            CollaborationPaymentState.DISPUTED,
        }
    ),
    CollaborationPaymentState.COMPLETED: frozenset(
        {
            CollaborationPaymentState.RELEASE_PENDING,
            CollaborationPaymentState.REFUNDED,
            CollaborationPaymentState.DISPUTED,
        }
    ),
    CollaborationPaymentState.RELEASE_PENDING: frozenset(
        {
            CollaborationPaymentState.RELEASED,
            CollaborationPaymentState.REFUNDED,
            CollaborationPaymentState.DISPUTED,
        }
    ),
    CollaborationPaymentState.RELEASED: frozenset(),
    CollaborationPaymentState.CANCELLED: frozenset(),
    CollaborationPaymentState.REFUNDED: frozenset(),
    CollaborationPaymentState.DISPUTED: frozenset(
        {
            CollaborationPaymentState.CANCELLED,
            CollaborationPaymentState.REFUNDED,
        }
    ),
}


class CollaborationPaymentService:
    """Own internal payment state and validate every provider-bound operation."""

    _COMPLETION_OPERATION = "internal_collaboration_completion_confirmation"
    _RELEASE_OPERATION = "internal_release_authorization"
    _CANCELLATION_OPERATION = "internal_collaboration_failure_cancellation"
    _INVITEE_REJECTION_OPERATION = "internal_invitee_rejection_cancellation"
    _REFUND_OPERATION = "internal_refund_authorization"
    _DISPUTE_OPERATION = "collaboration_party_dispute"
    _DISPUTE_RESOLUTION_OPERATION = "internal_dispute_resolution"
    _LEGACY_DISPUTE_OPERATION = "legacy_internal_dispute_state"

    def __init__(
        self,
        repository: CollaborationPaymentRepository,
        provider: PaymentProvider | None = None,
    ):
        self.repository = repository
        self.provider = provider or get_payment_provider()

    async def initialize_provider_payment(
        self,
        collaboration_id: uuid.UUID,
        actor_id: uuid.UUID,
        idempotency_key: str,
    ) -> ProviderResult:
        """Initialize through the adapter and bind its reference to this payment."""
        self._validate_provider_idempotency_key(idempotency_key)
        collaboration, payment = await self._get_locked_relationship(collaboration_id)
        await self._validate_payment_relationship(collaboration, payment)
        if actor_id != payment.payer_user_id:
            raise PermissionError("Only the payment payer can initialize payment")
        if payment.state != CollaborationPaymentState.PENDING:
            raise ValueError("Only a pending payment can be initialized")
        result = await self.provider.initialize_payment(
            amount_minor_units=payment.amount_minor_units,
            currency=payment.currency,
            idempotency_key=idempotency_key,
        )
        self._validate_provider_result(
            result,
            expected_status={ProviderStatus.CREATED, ProviderStatus.PENDING},
            payment=payment,
            expected_idempotency_key=idempotency_key,
        )
        if payment.payment_provider is not None and (
            payment.payment_provider != self.provider.name
            or payment.provider_reference != result.payment_reference_id
        ):
            raise ValueError("Provider reference is already bound to another payment")
        reference_owner = await self.repository.get_by_provider_reference(
            self.provider.name, result.payment_reference_id
        )
        if reference_owner is not None and reference_owner.id != payment.id:
            raise ValueError("Provider reference is already bound to another payment")
        payment.payment_provider = self.provider.name
        payment.provider_reference = result.payment_reference_id
        await self.repository.save(payment)
        return result

    async def authorize_hold_with_provider(
        self,
        collaboration_id: uuid.UUID,
        payer_user_id: uuid.UUID,
        idempotency_key: str,
    ) -> CollaborationPayment:
        """Authorize externally, then request the existing internal hold transition."""
        self._validate_provider_idempotency_key(idempotency_key)
        collaboration, payment = await self._get_locked_relationship(collaboration_id)
        await self._validate_payment_relationship(collaboration, payment)
        if payer_user_id != payment.payer_user_id:
            raise PermissionError("Only the payment payer can authorize a hold")
        if payment.state == CollaborationPaymentState.PENDING and (
            collaboration.status
            not in {
                CollaborationStatus.PROPOSED,
                CollaborationStatus.ACCEPTED,
                CollaborationStatus.IN_PROGRESS,
            }
        ):
            raise ValueError(
                "Cannot authorize a provider hold for a resolved collaboration"
            )
        if payment.state != CollaborationPaymentState.PENDING:
            if payment.state in {
                CollaborationPaymentState.CANCELLED,
                CollaborationPaymentState.REFUNDED,
                CollaborationPaymentState.RELEASED,
            }:
                raise ValueError(
                    f"Cannot authorize provider hold for payment in {payment.state.value} state"
                )
            self._require_provider_reference(payment)
            if payment.payment_provider != self.provider.name:
                raise ValueError("Payment belongs to a different provider")
            if (
                payment.hold_idempotency_key != idempotency_key
                or payment.authorized_at is None
                or payment.authorized_by_operation != "internal_hold_authorization"
            ):
                raise ValueError(
                    "Payment was already authorized with another idempotency key"
                )
            return await self._activate_held_payment_if_eligible(
                collaboration, payment
            )
        provider_reference = self._require_provider_reference(payment)
        if payment.payment_provider != self.provider.name:
            raise ValueError("Payment belongs to a different provider")
        result = await self.provider.authorize_hold(
            payment_reference_id=provider_reference,
            amount_minor_units=payment.amount_minor_units,
            currency=payment.currency,
            idempotency_key=idempotency_key,
        )
        self._validate_provider_result(
            result,
            expected_status={ProviderStatus.AUTHORIZED},
            payment=payment,
            expected_idempotency_key=idempotency_key,
        )
        if result.operation_id is None:
            raise ValueError("Provider authorization is missing its operation reference")
        return await self.authorize_hold(
            collaboration_id,
            payer_user_id,
            idempotency_key,
            authorization_reference=result.operation_id,
        )

    async def cancel_provider_hold(
        self, collaboration_id: uuid.UUID, idempotency_key: str
    ) -> CollaborationPayment | None:
        """Cancel provider authorization only after collaboration cancellation."""
        self._validate_provider_idempotency_key(idempotency_key)
        collaboration, payment = await self._get_locked_relationship_if_present(
            collaboration_id
        )
        if payment is None:
            return None
        await self._validate_payment_relationship(collaboration, payment)
        if collaboration.status not in {
            CollaborationStatus.CANCELLED,
            CollaborationStatus.DECLINED,
        }:
            raise ValueError("Provider hold cancellation requires a resolved collaboration")
        if payment.state in {
            CollaborationPaymentState.CANCELLED,
            CollaborationPaymentState.REFUNDED,
            CollaborationPaymentState.DISPUTED,
        }:
            return await self.cancel_for_collaboration_resolution(collaboration_id)
        if payment.state == CollaborationPaymentState.RELEASED:
            raise ValueError("A released payment cannot be cancelled or reversed")
        if payment.state not in {
            CollaborationPaymentState.PENDING,
            CollaborationPaymentState.AUTHORIZED_HELD,
            CollaborationPaymentState.COLLABORATION_ACTIVE,
            CollaborationPaymentState.COMPLETED,
            CollaborationPaymentState.RELEASE_PENDING,
        }:
            raise ValueError(
                f"Payment in {payment.state.value} state cannot be cancelled by provider"
            )
        if (
            payment.state == CollaborationPaymentState.PENDING
            and payment.provider_reference is None
        ):
            return await self.cancel_for_collaboration_resolution(collaboration_id)
        provider_reference = self._require_provider_reference(payment)
        if payment.payment_provider != self.provider.name:
            raise ValueError("Payment belongs to a different provider")
        if payment.state in {
            CollaborationPaymentState.COMPLETED,
            CollaborationPaymentState.RELEASE_PENDING,
        }:
            result = await self.provider.refund_payment(
                payment_reference_id=provider_reference,
                amount_minor_units=payment.amount_minor_units,
                currency=payment.currency,
                idempotency_key=idempotency_key,
            )
            expected_status = {ProviderStatus.REFUNDED}
        else:
            result = await self.provider.cancel_hold(
                payment_reference_id=provider_reference,
                idempotency_key=idempotency_key,
            )
            expected_status = {ProviderStatus.CANCELLED}
        self._validate_provider_result(
            result,
            expected_status=expected_status,
            payment=payment,
            expected_idempotency_key=idempotency_key,
        )
        return await self.cancel_for_collaboration_resolution(collaboration_id)

    async def refund_with_provider(
        self, collaboration_id: uuid.UUID, idempotency_key: str
    ) -> CollaborationPayment:
        """Refund via the adapter, then apply the trusted internal refund path."""
        self._validate_provider_idempotency_key(idempotency_key)
        collaboration, payment = await self._get_locked_relationship(collaboration_id)
        await self._validate_payment_relationship(collaboration, payment)
        if payment.state == CollaborationPaymentState.REFUNDED:
            self._require_refund_audit(payment)
            return payment
        if payment.state not in {
            CollaborationPaymentState.AUTHORIZED_HELD,
            CollaborationPaymentState.COLLABORATION_ACTIVE,
            CollaborationPaymentState.COMPLETED,
            CollaborationPaymentState.RELEASE_PENDING,
            CollaborationPaymentState.DISPUTED,
        } or payment.authorized_at is None:
            raise ValueError(
                f"Payment in {payment.state.value} state cannot be refunded"
            )
        provider_reference = self._require_provider_reference(payment)
        if payment.payment_provider != self.provider.name:
            raise ValueError("Payment belongs to a different provider")
        result = await self.provider.refund_payment(
            payment_reference_id=provider_reference,
            amount_minor_units=payment.amount_minor_units,
            currency=payment.currency,
            idempotency_key=idempotency_key,
        )
        self._validate_provider_result(
            result,
            expected_status={ProviderStatus.REFUNDED},
            payment=payment,
            expected_idempotency_key=idempotency_key,
        )
        return await self.authorize_refund(collaboration_id)

    async def retrieve_provider_payment_status(
        self, collaboration_id: uuid.UUID, actor_id: uuid.UUID
    ) -> ProviderResult:
        """Read provider status for a party without applying it to internal state."""
        collaboration = await self.repository.get_collaboration(collaboration_id)
        if collaboration is None:
            raise ValueError("Collaboration not found")
        payment = await self.repository.get_for_collaboration(collaboration_id)
        if payment is None:
            raise ValueError("Payment not found")
        await self._validate_payment_relationship(collaboration, payment)
        self._require_party(payment, actor_id)
        provider_reference = self._require_provider_reference(payment)
        if payment.payment_provider != self.provider.name:
            raise ValueError("Payment belongs to a different provider")
        result = await self.provider.retrieve_payment_status(
            payment_reference_id=provider_reference
        )
        self._validate_provider_result(
            result,
            expected_status=set(ProviderStatus),
            payment=payment,
            expected_idempotency_key=None,
        )
        return result

    def _require_provider_reference(self, payment: CollaborationPayment) -> str:
        if not payment.payment_provider or not payment.provider_reference:
            raise ValueError("Payment has no bound provider reference")
        return payment.provider_reference

    @staticmethod
    def _validate_provider_idempotency_key(idempotency_key: str) -> None:
        if (
            not isinstance(idempotency_key, str)
            or not idempotency_key.strip()
            or len(idempotency_key) > 128
        ):
            raise ValueError(
                "Provider idempotency key must contain 1 to 128 characters"
            )

    def _validate_provider_result(
        self,
        result: ProviderResult,
        *,
        expected_status: set[ProviderStatus],
        payment: CollaborationPayment,
        expected_idempotency_key: str | None,
    ) -> None:
        if result.provider_name != self.provider.name:
            raise ValueError("Provider result does not match selected provider")
        if (
            result.amount_minor_units != payment.amount_minor_units
            or result.currency != payment.currency
        ):
            raise ValueError("Provider result amount or currency does not match payment")
        if payment.provider_reference is not None and (
            result.payment_reference_id != payment.provider_reference
        ):
            raise ValueError("Provider result reference does not match payment")
        if result.status not in expected_status:
            raise ValueError(
                f"Provider returned {result.status.value} for an operation requiring "
                f"{', '.join(sorted(status.value for status in expected_status))}"
            )
        if (
            expected_idempotency_key is not None
            and (
                result.failure_code is not None
                or result.failure_message is not None
            )
        ):
            raise ValueError("Provider returned failure details for a successful operation")
        if result.idempotency_key != expected_idempotency_key:
            raise ValueError("Provider result idempotency key does not match request")

    async def create_pending_payment(
        self,
        collaboration_id: uuid.UUID,
        actor_id: uuid.UUID,
        recipient_user_id: uuid.UUID,
        amount_minor_units: int,
        currency: str,
    ) -> CollaborationPayment:
        """Create the collaboration's sole pending payment as its initiator."""
        self._validate_amount_and_currency(amount_minor_units, currency)
        collaboration = await self.repository.get_collaboration(
            collaboration_id, for_update=True
        )
        if collaboration is None:
            raise ValueError("Collaboration not found")
        if collaboration.status not in {
            CollaborationStatus.PROPOSED,
            CollaborationStatus.ACCEPTED,
            CollaborationStatus.IN_PROGRESS,
        }:
            raise ValueError("Cannot create a payment for a resolved collaboration")
        if actor_id != collaboration.initiator_id:
            raise PermissionError("Only the collaboration initiator can create its payment")
        if recipient_user_id == actor_id:
            raise ValueError("Payer and recipient must be different users")

        participant_ids = await self.repository.get_participant_user_ids(collaboration_id)
        if actor_id not in participant_ids or recipient_user_id not in participant_ids:
            raise PermissionError("Payment parties must belong to the collaboration")

        existing = await self.repository.get_for_collaboration(
            collaboration_id, for_update=True
        )
        if existing is not None:
            raise ValueError("A payment already exists for this collaboration")

        payment = CollaborationPayment(
            collaboration_id=collaboration_id,
            payer_user_id=actor_id,
            recipient_user_id=recipient_user_id,
            amount_minor_units=amount_minor_units,
            currency=currency,
            state=CollaborationPaymentState.PENDING,
        )
        return await self.repository.create(payment)

    async def get_payment_for_collaboration(
        self, collaboration_id: uuid.UUID, actor_id: uuid.UUID
    ) -> CollaborationPayment:
        """Return a payment only to a verified payer or recipient."""
        collaboration = await self.repository.get_collaboration(collaboration_id)
        if collaboration is None:
            raise ValueError("Collaboration not found")
        payment = await self.repository.get_for_collaboration(collaboration_id)
        if payment is None:
            raise ValueError("Payment not found")
        await self._validate_payment_relationship(collaboration, payment)
        self._require_party(payment, actor_id)
        return payment

    async def authorize_hold(
        self,
        collaboration_id: uuid.UUID,
        payer_user_id: uuid.UUID,
        idempotency_key: str,
        authorization_reference: str | None = None,
    ) -> CollaborationPayment:
        """Record an internal held authorization; no external funds are moved.

        This server-only operation is intentionally separate from party-requested
        transitions. The caller must first establish payer authorization through
        a trusted backend path before invoking it.
        """
        self._validate_hold_request(idempotency_key, authorization_reference)
        collaboration, payment = await self._get_locked_relationship(collaboration_id)
        await self._validate_payment_relationship(collaboration, payment)

        if payer_user_id != payment.payer_user_id:
            raise PermissionError("Only the payment payer can authorize a hold")

        existing_key_payment = await self.repository.get_by_hold_idempotency_key(
            idempotency_key, for_update=True
        )
        if existing_key_payment is not None:
            if (
                existing_key_payment.id != payment.id
                or existing_key_payment.authorization_reference
                != authorization_reference
            ):
                raise ValueError("Hold idempotency key was already used")
            if (
                existing_key_payment.state == CollaborationPaymentState.PENDING
                or existing_key_payment.authorized_at is None
                or existing_key_payment.authorized_by_operation
                != "internal_hold_authorization"
            ):
                raise ValueError("Stored hold idempotency data is inconsistent")
            if existing_key_payment.state in {
                CollaborationPaymentState.CANCELLED,
                CollaborationPaymentState.REFUNDED,
                CollaborationPaymentState.RELEASED,
            }:
                raise ValueError(
                    f"Cannot authorize hold for payment in "
                    f"{existing_key_payment.state.value} state"
                )
            return await self._activate_held_payment_if_eligible(
                collaboration, existing_key_payment
            )

        if payment.state != CollaborationPaymentState.PENDING:
            if (
                payment.hold_idempotency_key == idempotency_key
                and payment.authorization_reference == authorization_reference
                and payment.authorized_at is not None
            ):
                if payment.state in {
                    CollaborationPaymentState.CANCELLED,
                    CollaborationPaymentState.REFUNDED,
                    CollaborationPaymentState.RELEASED,
                }:
                    raise ValueError(
                        f"Cannot authorize hold for payment in {payment.state.value} state"
                    )
                return await self._activate_held_payment_if_eligible(
                    collaboration, payment
                )
            raise ValueError(
                f"Cannot authorize hold for payment in {payment.state.value} state"
            )
        if payment.authorized_at is not None or payment.hold_idempotency_key is not None:
            raise ValueError("Pending payment contains conflicting hold authorization data")
        if collaboration.status not in {
            CollaborationStatus.PROPOSED,
            CollaborationStatus.ACCEPTED,
            CollaborationStatus.IN_PROGRESS,
        }:
            raise ValueError(
                "Cannot authorize a hold for a resolved collaboration"
            )

        payment.hold_idempotency_key = idempotency_key
        payment.authorization_reference = authorization_reference
        payment.authorized_by_operation = "internal_hold_authorization"
        try:
            authorized = await self._transition(
                collaboration,
                payment,
                CollaborationPaymentState.AUTHORIZED_HELD,
                authorization_key=idempotency_key,
            )
            return await self._activate_held_payment_if_eligible(
                collaboration, authorized
            )
        except IntegrityError:
            # Concurrent duplicate requests are serialized by the collaboration
            # row lock; this lookup also handles a key uniqueness race safely.
            await self.repository.db.rollback()
            replay = await self.repository.get_by_hold_idempotency_key(idempotency_key)
            if (
                replay is not None
                and replay.collaboration_id == collaboration_id
                and replay.payer_user_id == payer_user_id
                and replay.authorization_reference == authorization_reference
            ):
                return await self._activate_held_payment_if_eligible(
                    collaboration, replay
                )
            raise

    async def activate_for_collaboration_acceptance(
        self, collaboration_id: uuid.UUID
    ) -> CollaborationPayment | None:
        """Activate an authorized payment when its collaboration is accepted."""
        collaboration, payment = await self._get_locked_relationship_if_present(
            collaboration_id
        )
        if payment is None:
            return None
        await self._validate_payment_relationship(collaboration, payment)
        if collaboration.status not in {
            CollaborationStatus.ACCEPTED,
            CollaborationStatus.IN_PROGRESS,
        }:
            raise ValueError(
                "Payment activation requires an accepted or in-progress collaboration"
            )
        return await self._activate_held_payment_if_eligible(
            collaboration, payment
        )

    async def _activate_held_payment_if_eligible(
        self,
        collaboration: Collaboration,
        payment: CollaborationPayment,
    ) -> CollaborationPayment:
        if collaboration.status not in {
            CollaborationStatus.ACCEPTED,
            CollaborationStatus.IN_PROGRESS,
        }:
            return payment
        if payment.state == CollaborationPaymentState.AUTHORIZED_HELD:
            return await self._transition(
                collaboration,
                payment,
                CollaborationPaymentState.COLLABORATION_ACTIVE,
            )
        if (
            payment.state == CollaborationPaymentState.COLLABORATION_ACTIVE
            and payment.collaboration_started_at is None
        ):
            raise ValueError("Active payment has no collaboration activation audit")
        return payment

    async def transition_by_party(
        self,
        collaboration_id: uuid.UUID,
        actor_id: uuid.UUID,
        new_state: CollaborationPaymentState | str,
    ) -> CollaborationPayment:
        """Allow payment parties to request a dispute, never alter money state."""
        collaboration, payment = await self._get_locked_relationship(collaboration_id)
        await self._validate_payment_relationship(collaboration, payment)
        self._require_party(payment, actor_id)

        target = self._coerce_state(new_state)
        if target == CollaborationPaymentState.DISPUTED:
            return await self._initiate_dispute_locked(
                collaboration, payment, actor_id
            )
        if target != CollaborationPaymentState.CANCELLED:
            raise PermissionError("Parties cannot directly change financial state")
        raise PermissionError(
            "Payment cancellation must follow the collaboration lifecycle"
        )

    async def transition_by_system(
        self,
        collaboration_id: uuid.UUID,
        new_state: CollaborationPaymentState | str,
    ) -> CollaborationPayment:
        """Apply a trusted backend transition; never expose this as a client API."""
        collaboration, payment = await self._get_locked_relationship(collaboration_id)
        await self._validate_payment_relationship(collaboration, payment)
        target = self._coerce_state(new_state)
        if target in {
            CollaborationPaymentState.AUTHORIZED_HELD,
            CollaborationPaymentState.COMPLETED,
            CollaborationPaymentState.RELEASE_PENDING,
            CollaborationPaymentState.CANCELLED,
            CollaborationPaymentState.REFUNDED,
            CollaborationPaymentState.DISPUTED,
        }:
            raise PermissionError(
                "Use the dedicated payment failure or lifecycle operation"
            )
        return await self._transition(
            collaboration, payment, target
        )

    async def cancel_for_collaboration_resolution(
        self, collaboration_id: uuid.UUID
    ) -> CollaborationPayment | None:
        """Cancel/reverse payment state after an authoritative decline/cancel."""
        collaboration, payment = await self._get_locked_relationship_if_present(
            collaboration_id
        )
        if collaboration.status not in {
            CollaborationStatus.CANCELLED,
            CollaborationStatus.DECLINED,
        }:
            raise ValueError(
                "Payment cancellation requires a cancelled or declined collaboration"
            )
        if payment is None:
            return None
        await self._validate_payment_relationship(collaboration, payment)

        if payment.state == CollaborationPaymentState.DISPUTED:
            self._require_dispute_audit(payment)
            return payment
        if payment.state == CollaborationPaymentState.CANCELLED:
            self._require_cancellation_audit(payment)
            return payment
        if payment.state == CollaborationPaymentState.REFUNDED:
            self._require_refund_audit(payment)
            return payment
        if payment.state == CollaborationPaymentState.RELEASED:
            raise ValueError("A released payment cannot be cancelled or reversed")
        if payment.state in {
            CollaborationPaymentState.COMPLETED,
            CollaborationPaymentState.RELEASE_PENDING,
        }:
            return await self._authorize_refund_locked(
                collaboration,
                payment,
                operation="internal_cancellation_refund_authorization",
                resolve_dispute=False,
            )
        if payment.state not in {
            CollaborationPaymentState.PENDING,
            CollaborationPaymentState.AUTHORIZED_HELD,
            CollaborationPaymentState.COLLABORATION_ACTIVE,
        }:
            raise ValueError(
                f"Payment in {payment.state.value} state cannot follow cancellation"
            )
        return await self._transition(
            collaboration,
            payment,
            CollaborationPaymentState.CANCELLED,
            operation=self._CANCELLATION_OPERATION,
            audit_updates={
                "cancelled_by_operation": self._CANCELLATION_OPERATION,
            },
        )

    async def cancel_for_invitee_decline(
        self, collaboration_id: uuid.UUID, declined_user_id: uuid.UUID
    ) -> CollaborationPayment | None:
        """Cancel payment if its invitee recipient rejects before collaboration acceptance."""
        collaboration, payment = await self._get_locked_relationship_if_present(
            collaboration_id
        )
        if collaboration.status not in {
            CollaborationStatus.PROPOSED,
            CollaborationStatus.DECLINED,
        }:
            raise ValueError(
                "Invitee payment cancellation requires a pending or declined collaboration"
            )
        participant_ids = await self.repository.get_participant_user_ids(
            collaboration_id
        )
        accepted_participant_ids = (
            await self.repository.get_accepted_participant_user_ids(
                collaboration_id
            )
        )
        if (
            declined_user_id == collaboration.initiator_id
            or declined_user_id not in participant_ids
            or declined_user_id in accepted_participant_ids
        ):
            raise PermissionError("Only a pending collaboration invitee may decline")
        if payment is None or payment.recipient_user_id != declined_user_id:
            return None
        await self._validate_payment_relationship(collaboration, payment)
        if payment.state == CollaborationPaymentState.CANCELLED:
            self._require_cancellation_audit(payment)
            return payment
        if payment.state == CollaborationPaymentState.REFUNDED:
            self._require_refund_audit(payment)
            return payment
        if payment.state == CollaborationPaymentState.DISPUTED:
            self._require_dispute_audit(payment)
            return payment
        if payment.state not in {
            CollaborationPaymentState.PENDING,
            CollaborationPaymentState.AUTHORIZED_HELD,
        }:
            raise ValueError(
                f"Payment in {payment.state.value} state cannot follow invitee rejection"
            )
        return await self._transition(
            collaboration,
            payment,
            CollaborationPaymentState.CANCELLED,
            operation=self._INVITEE_REJECTION_OPERATION,
            audit_updates={
                "cancelled_by_operation": self._INVITEE_REJECTION_OPERATION,
            },
        )

    async def authorize_refund(
        self, collaboration_id: uuid.UUID
    ) -> CollaborationPayment:
        """Authorize an internal refund state; no external refund is performed."""
        collaboration, payment = await self._get_locked_relationship(collaboration_id)
        await self._validate_payment_relationship(collaboration, payment)
        return await self._authorize_refund_locked(
            collaboration, payment, operation=self._REFUND_OPERATION
        )

    async def _authorize_refund_locked(
        self,
        collaboration: Collaboration,
        payment: CollaborationPayment,
        *,
        operation: str,
        resolve_dispute: bool = True,
    ) -> CollaborationPayment:
        if payment.state == CollaborationPaymentState.REFUNDED:
            self._require_refund_audit(payment)
            return payment
        if payment.state not in {
            CollaborationPaymentState.AUTHORIZED_HELD,
            CollaborationPaymentState.COLLABORATION_ACTIVE,
            CollaborationPaymentState.COMPLETED,
            CollaborationPaymentState.RELEASE_PENDING,
            CollaborationPaymentState.DISPUTED,
        }:
            raise ValueError(
                f"Payment in {payment.state.value} state cannot be refunded"
            )
        if payment.authorized_at is None:
            raise ValueError("Refund authorization requires a prior held authorization")

        audit_updates: dict[str, object] = {
            "refund_authorized_by_operation": operation,
        }
        if (
            payment.state == CollaborationPaymentState.DISPUTED
            and resolve_dispute
        ):
            audit_updates["dispute_resolved_by_operation"] = (
                self._DISPUTE_RESOLUTION_OPERATION
            )
        return await self._transition(
            collaboration,
            payment,
            CollaborationPaymentState.REFUNDED,
            operation=operation,
            audit_updates=audit_updates,
        )

    async def resolve_dispute(
        self,
        collaboration_id: uuid.UUID,
        resolution: CollaborationPaymentState | str,
    ) -> CollaborationPayment:
        """Resolve an internal dispute to cancellation or refund only."""
        collaboration, payment = await self._get_locked_relationship(collaboration_id)
        await self._validate_payment_relationship(collaboration, payment)
        target = self._coerce_state(resolution)
        if target not in {
            CollaborationPaymentState.CANCELLED,
            CollaborationPaymentState.REFUNDED,
        }:
            raise ValueError("Disputes may resolve only to cancelled or refunded")
        if payment.state in {
            CollaborationPaymentState.CANCELLED,
            CollaborationPaymentState.REFUNDED,
        }:
            if payment.dispute_resolved_by_operation != self._DISPUTE_RESOLUTION_OPERATION:
                raise ValueError("Payment has no valid dispute resolution audit")
            if payment.state != target:
                raise ValueError("Dispute was already resolved differently")
            return payment
        if payment.state != CollaborationPaymentState.DISPUTED:
            raise ValueError("Only a disputed payment can be resolved")
        self._require_dispute_audit(payment)

        audit_updates: dict[str, object] = {
            "dispute_resolved_by_operation": self._DISPUTE_RESOLUTION_OPERATION,
        }
        if target == CollaborationPaymentState.CANCELLED:
            audit_updates["cancelled_by_operation"] = (
                self._DISPUTE_RESOLUTION_OPERATION
            )
        else:
            if payment.authorized_at is None:
                raise ValueError("Refund authorization requires a prior held authorization")
            audit_updates["refund_authorized_by_operation"] = (
                self._DISPUTE_RESOLUTION_OPERATION
            )
        return await self._transition(
            collaboration,
            payment,
            target,
            operation=self._DISPUTE_RESOLUTION_OPERATION,
            audit_updates=audit_updates,
        )

    async def _initiate_dispute_locked(
        self,
        collaboration: Collaboration,
        payment: CollaborationPayment,
        actor_id: uuid.UUID,
    ) -> CollaborationPayment:
        self._require_party(payment, actor_id)
        accepted_participants = (
            await self.repository.get_accepted_participant_user_ids(collaboration.id)
        )
        if actor_id != collaboration.initiator_id and actor_id not in accepted_participants:
            raise PermissionError(
                "Only an accepted collaboration party can initiate a dispute"
            )
        if payment.state == CollaborationPaymentState.DISPUTED:
            self._require_dispute_audit(payment)
            return payment
        if payment.state not in {
            CollaborationPaymentState.AUTHORIZED_HELD,
            CollaborationPaymentState.COLLABORATION_ACTIVE,
            CollaborationPaymentState.COMPLETED,
            CollaborationPaymentState.RELEASE_PENDING,
        }:
            raise PermissionError("Payment cannot be disputed in its current state")
        return await self._transition(
            collaboration,
            payment,
            CollaborationPaymentState.DISPUTED,
            operation=self._DISPUTE_OPERATION,
            audit_updates={
                "disputed_by_user_id": actor_id,
                "disputed_by_operation": self._DISPUTE_OPERATION,
            },
        )

    async def confirm_collaboration_completion(
        self, collaboration_id: uuid.UUID
    ) -> CollaborationPayment | None:
        """Confirm payment completion only after the collaboration is completed.

        A collaboration without a payment keeps its existing completion behavior.
        Payment and collaboration updates share the caller's database transaction.
        """
        collaboration, payment = await self._get_locked_relationship_if_present(
            collaboration_id
        )
        if (
            collaboration.status != CollaborationStatus.COMPLETED
            or not collaboration.completed_at
        ):
            raise ValueError(
                "Payment completion requires an authoritatively completed collaboration"
            )
        if payment is None:
            return None

        await self._validate_payment_relationship(collaboration, payment)
        if payment.state == CollaborationPaymentState.COMPLETED:
            self._require_completion_audit(payment)
            return payment
        if payment.state != CollaborationPaymentState.COLLABORATION_ACTIVE:
            raise ValueError(
                "Payment completion requires an active payment with an authorized hold"
            )
        if payment.authorized_at is None:
            raise ValueError("Payment completion requires a prior held authorization")

        payment.completion_confirmed_by_operation = self._COMPLETION_OPERATION
        return await self._transition(
            collaboration, payment, CollaborationPaymentState.COMPLETED
        )

    async def authorize_release(
        self, collaboration_id: uuid.UUID
    ) -> CollaborationPayment:
        """Mark a completed payment release-eligible without transferring funds."""
        collaboration, payment = await self._get_locked_relationship(collaboration_id)
        await self._validate_payment_relationship(collaboration, payment)
        if (
            collaboration.status != CollaborationStatus.COMPLETED
            or not collaboration.completed_at
        ):
            raise ValueError(
                "Release authorization requires an authoritatively completed collaboration"
            )

        if payment.state == CollaborationPaymentState.RELEASE_PENDING:
            self._require_completion_audit(payment)
            if (
                payment.release_pending_at is None
                or payment.release_authorized_by_operation != self._RELEASE_OPERATION
            ):
                raise ValueError("Stored release authorization audit is inconsistent")
            return payment
        if payment.state != CollaborationPaymentState.COMPLETED:
            raise ValueError(
                "Release authorization requires a completed, held payment"
            )
        self._require_completion_audit(payment)
        if payment.authorized_at is None:
            raise ValueError("Release authorization requires a prior held authorization")

        payment.release_authorized_by_operation = self._RELEASE_OPERATION
        return await self._transition(
            collaboration, payment, CollaborationPaymentState.RELEASE_PENDING
        )

    async def _get_locked_relationship(
        self, collaboration_id: uuid.UUID
    ) -> tuple[Collaboration, CollaborationPayment]:
        collaboration, payment = await self._get_locked_relationship_if_present(
            collaboration_id
        )
        if payment is None:
            raise ValueError("Payment not found")
        return collaboration, payment

    async def _get_locked_relationship_if_present(
        self, collaboration_id: uuid.UUID
    ) -> tuple[Collaboration, CollaborationPayment | None]:
        collaboration = await self.repository.get_collaboration(
            collaboration_id, for_update=True
        )
        if collaboration is None:
            raise ValueError("Collaboration not found")
        payment = await self.repository.get_for_collaboration(
            collaboration_id, for_update=True
        )
        return collaboration, payment

    def _require_completion_audit(self, payment: CollaborationPayment) -> None:
        if (
            payment.completed_at is None
            or payment.completion_confirmed_by_operation
            != self._COMPLETION_OPERATION
            or payment.authorized_at is None
            or payment.authorized_by_operation != "internal_hold_authorization"
        ):
            raise ValueError("Payment has no valid held completion confirmation")

    def _require_cancellation_audit(self, payment: CollaborationPayment) -> None:
        if payment.cancelled_at is None or payment.cancelled_by_operation is None:
            raise ValueError("Payment has no valid cancellation audit")
        if payment.authorized_at is not None and (
            payment.hold_reversed_at is None
            or payment.hold_reversed_by_operation is None
        ):
            raise ValueError("Cancelled held payment has no reversal audit")

    @staticmethod
    def _require_refund_audit(payment: CollaborationPayment) -> None:
        if (
            payment.refund_authorized_at is None
            or payment.refund_authorized_by_operation is None
            or payment.refunded_at is None
        ):
            raise ValueError("Payment has no valid refund authorization audit")

    def _require_dispute_audit(self, payment: CollaborationPayment) -> None:
        if (
            payment.disputed_at is None
            or payment.disputed_by_operation
            not in {self._DISPUTE_OPERATION, self._LEGACY_DISPUTE_OPERATION}
        ):
            raise ValueError("Payment has no valid dispute initiation audit")

    async def _validate_payment_relationship(
        self, collaboration: Collaboration, payment: CollaborationPayment
    ) -> None:
        if payment.collaboration_id != collaboration.id:
            raise ValueError("Payment collaboration relationship is invalid")
        participant_ids = await self.repository.get_participant_user_ids(
            collaboration.id
        )
        declined_recipient_resolution = False
        if collaboration.status == CollaborationStatus.DECLINED:
            declined_recipient_resolution = (
                payment.state == CollaborationPaymentState.CANCELLED
                and payment.cancelled_at is not None
                and payment.cancelled_by_operation is not None
            ) or (
                payment.state == CollaborationPaymentState.REFUNDED
                and payment.refund_authorized_at is not None
                and payment.refund_authorized_by_operation is not None
            ) or (
                payment.state == CollaborationPaymentState.DISPUTED
                and payment.disputed_at is not None
                and payment.disputed_by_operation == self._DISPUTE_OPERATION
            )
        if (
            payment.payer_user_id != collaboration.initiator_id
            or payment.payer_user_id not in participant_ids
            or (
                payment.recipient_user_id not in participant_ids
                and not declined_recipient_resolution
            )
            or payment.payer_user_id == payment.recipient_user_id
        ):
            raise ValueError("Payment parties do not match the collaboration")
        if (
            payment.state
            not in {
                CollaborationPaymentState.PENDING,
                CollaborationPaymentState.CANCELLED,
            }
            and (
                payment.authorized_at is None
                or not payment.hold_idempotency_key
                or payment.authorized_by_operation != "internal_hold_authorization"
            )
        ):
            raise ValueError("Payment state has no valid held authorization")

    @staticmethod
    def _require_party(payment: CollaborationPayment, actor_id: uuid.UUID) -> None:
        if actor_id not in {payment.payer_user_id, payment.recipient_user_id}:
            raise PermissionError("Payment access denied")

    async def _transition(
        self,
        collaboration: Collaboration,
        payment: CollaborationPayment,
        target: CollaborationPaymentState,
        *,
        authorization_key: str | None = None,
        operation: str | None = None,
        audit_updates: dict[str, object] | None = None,
    ) -> CollaborationPayment:
        managed_fields = (
            "state",
            "cancelled_at",
            "cancelled_by_operation",
            "refund_authorized_at",
            "refund_authorized_by_operation",
            "refunded_at",
            "hold_reversed_at",
            "hold_reversed_by_operation",
            "disputed_at",
            "disputed_by_user_id",
            "disputed_by_operation",
            "dispute_resolved_at",
            "dispute_resolved_by_operation",
        )
        previous_values = {
            field: getattr(payment, field) for field in managed_fields
        }
        try:
            for field, value in (audit_updates or {}).items():
                if field not in managed_fields:
                    raise ValueError(f"Unsupported payment audit field: {field}")
                setattr(payment, field, value)
            return await self._apply_transition(
                collaboration,
                payment,
                target,
                authorization_key=authorization_key,
                operation=operation,
            )
        except Exception:
            for field, value in previous_values.items():
                setattr(payment, field, value)
            raise

    async def _apply_transition(
        self,
        collaboration: Collaboration,
        payment: CollaborationPayment,
        target: CollaborationPaymentState,
        *,
        authorization_key: str | None = None,
        operation: str | None = None,
    ) -> CollaborationPayment:
        allowed = VALID_PAYMENT_TRANSITIONS[payment.state]
        if target not in allowed:
            raise ValueError(
                f"Invalid payment transition from {payment.state.value} to {target.value}"
            )
        if target == CollaborationPaymentState.AUTHORIZED_HELD and (
            authorization_key is None
            or payment.hold_idempotency_key != authorization_key
            or payment.authorized_by_operation != "internal_hold_authorization"
        ):
            raise PermissionError("A trusted hold authorization is required")
        if (
            target == CollaborationPaymentState.COMPLETED
            and payment.completion_confirmed_by_operation
            != self._COMPLETION_OPERATION
        ):
            raise PermissionError("Use trusted collaboration completion confirmation")
        if (
            target == CollaborationPaymentState.RELEASE_PENDING
            and payment.release_authorized_by_operation != self._RELEASE_OPERATION
        ):
            raise PermissionError("Use trusted release authorization")
        if target == CollaborationPaymentState.CANCELLED and (
            operation
            not in {
                self._CANCELLATION_OPERATION,
                self._INVITEE_REJECTION_OPERATION,
                self._DISPUTE_RESOLUTION_OPERATION,
            }
            or payment.cancelled_by_operation != operation
        ):
            raise PermissionError("Use collaboration cancellation or dispute resolution")
        if target == CollaborationPaymentState.REFUNDED and (
            operation is None
            or payment.refund_authorized_by_operation != operation
        ):
            raise PermissionError("Use trusted refund authorization")
        if target == CollaborationPaymentState.DISPUTED and (
            operation != self._DISPUTE_OPERATION
            or payment.disputed_by_operation != self._DISPUTE_OPERATION
            or payment.disputed_by_user_id is None
        ):
            raise PermissionError("Use authorized party dispute initiation")
        if target in {
            CollaborationPaymentState.COLLABORATION_ACTIVE,
            CollaborationPaymentState.COMPLETED,
            CollaborationPaymentState.RELEASE_PENDING,
            CollaborationPaymentState.RELEASED,
        } and (
            payment.authorized_at is None
            or payment.hold_idempotency_key is None
            or payment.authorized_by_operation is None
        ):
            raise ValueError("Payment has no valid held authorization")

        if target == CollaborationPaymentState.COLLABORATION_ACTIVE and (
            collaboration.status
            not in {CollaborationStatus.ACCEPTED, CollaborationStatus.IN_PROGRESS}
        ):
            raise ValueError("Collaboration must be accepted before payment activation")
        if target in {
            CollaborationPaymentState.COMPLETED,
            CollaborationPaymentState.RELEASE_PENDING,
            CollaborationPaymentState.RELEASED,
        } and collaboration.status != CollaborationStatus.COMPLETED:
            raise ValueError(
                "Payment completion and release require a completed collaboration"
            )
        if target == CollaborationPaymentState.CANCELLED and (
            operation == self._CANCELLATION_OPERATION
            and collaboration.status
            not in {CollaborationStatus.CANCELLED, CollaborationStatus.DECLINED}
        ):
            raise ValueError(
                "Payment cancellation requires a cancelled or declined collaboration"
            )
        if (
            target == CollaborationPaymentState.CANCELLED
            and operation == self._INVITEE_REJECTION_OPERATION
            and collaboration.status
            not in {CollaborationStatus.PROPOSED, CollaborationStatus.DECLINED}
        ):
            raise ValueError(
                "Invitee rejection cancellation requires a pending collaboration"
            )
        if (
            payment.state == CollaborationPaymentState.DISPUTED
            and target == CollaborationPaymentState.CANCELLED
            and operation != self._DISPUTE_RESOLUTION_OPERATION
        ):
            raise ValueError("Disputed payment cancellation requires trusted resolution")
        if (
            payment.state == CollaborationPaymentState.DISPUTED
            and target == CollaborationPaymentState.REFUNDED
            and operation != self._DISPUTE_RESOLUTION_OPERATION
            and operation != self._REFUND_OPERATION
        ):
            raise ValueError("Disputed payment refund requires trusted resolution")

        now = datetime.now(timezone.utc)
        payment.state = target
        timestamp_by_state = {
            CollaborationPaymentState.AUTHORIZED_HELD: "authorized_at",
            CollaborationPaymentState.COLLABORATION_ACTIVE: "collaboration_started_at",
            CollaborationPaymentState.COMPLETED: "completed_at",
            CollaborationPaymentState.RELEASE_PENDING: "release_pending_at",
            CollaborationPaymentState.RELEASED: "released_at",
            CollaborationPaymentState.CANCELLED: "cancelled_at",
            CollaborationPaymentState.REFUNDED: "refunded_at",
            CollaborationPaymentState.DISPUTED: "disputed_at",
        }
        timestamp_field = timestamp_by_state.get(target)
        if timestamp_field is not None:
            setattr(payment, timestamp_field, now)
        if target == CollaborationPaymentState.REFUNDED:
            payment.refund_authorized_at = now
        if (
            payment.state in {
                CollaborationPaymentState.CANCELLED,
                CollaborationPaymentState.REFUNDED,
            }
            and payment.disputed_at is not None
            and payment.dispute_resolved_by_operation
            == self._DISPUTE_RESOLUTION_OPERATION
            and payment.dispute_resolved_at is None
        ):
            payment.dispute_resolved_at = now
        if (
            payment.authorized_at is not None
            and target
            in {
                CollaborationPaymentState.CANCELLED,
                CollaborationPaymentState.REFUNDED,
            }
        ):
            if payment.hold_reversed_at is None:
                payment.hold_reversed_at = now
                payment.hold_reversed_by_operation = operation or (
                    f"internal_payment_transition_{target.value.lower()}"
                )
        return await self.repository.save(payment)

    @staticmethod
    def _validate_amount_and_currency(amount_minor_units: int, currency: str) -> None:
        if (
            isinstance(amount_minor_units, bool)
            or not isinstance(amount_minor_units, int)
            or not 0 < amount_minor_units <= _MAX_MINOR_UNITS
        ):
            raise ValueError("Amount must be a positive integer in minor units")
        if not isinstance(currency, str) or _CURRENCY_CODE.fullmatch(currency) is None:
            raise ValueError("Currency must be a three-letter uppercase currency code")

    @staticmethod
    def _coerce_state(
        new_state: CollaborationPaymentState | str,
    ) -> CollaborationPaymentState:
        try:
            return CollaborationPaymentState(new_state)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid payment state: {new_state}") from exc

    @staticmethod
    def _validate_hold_request(
        idempotency_key: str, authorization_reference: str | None
    ) -> None:
        if (
            not isinstance(idempotency_key, str)
            or not idempotency_key.strip()
            or len(idempotency_key) > 128
        ):
            raise ValueError("Hold idempotency key must contain 1 to 128 characters")
        if authorization_reference is not None and (
            not isinstance(authorization_reference, str)
            or not authorization_reference.strip()
            or len(authorization_reference) > 255
        ):
            raise ValueError("Authorization reference must contain 1 to 255 characters")
