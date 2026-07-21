"""The Human-in-the-Loop booking state machine — the single source of truth for transitions.

This is deliberately NOT reachable by the agent. Booking state only ever changes through the
REST endpoints in ``routes/booking.py`` driven by explicit human clicks, so "a human confirmed
before the write" is a structural guarantee, not something the prompt has to be trusted to honor.

    PENDING_USER_CONFIRMATION ──confirm──► CONFIRMED ──execute──► EXECUTED
            │        │                          │  │
            │        └──────── cancel ──────────┘  │
            │                                       └─► CANCELLED
            └─────────── expire (TTL) ──────────────► EXPIRED   (also from CONFIRMED)
"""

from enum import StrEnum


class BookingState(StrEnum):
    PENDING_USER_CONFIRMATION = "PENDING_USER_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    EXECUTED = "EXECUTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class BookingTransitionReason(StrEnum):
    CONFIRM = "confirm"
    EXECUTE = "execute"
    CANCEL = "cancel"
    EXPIRE = "expire"


# The only legal moves. Any move not listed here is rejected with HTTP 409, and every applied
# move writes an immutable BookingTransition row in the same transaction.
ALLOWED_TRANSITIONS: dict[BookingState, set[BookingState]] = {
    BookingState.PENDING_USER_CONFIRMATION: {
        BookingState.CONFIRMED,
        BookingState.CANCELLED,
        BookingState.EXPIRED,
    },
    BookingState.CONFIRMED: {
        BookingState.EXECUTED,
        BookingState.CANCELLED,
        BookingState.EXPIRED,
    },
    BookingState.EXECUTED: set(),  # terminal
    BookingState.CANCELLED: set(),  # terminal
    BookingState.EXPIRED: set(),  # terminal
}

TERMINAL_STATES = frozenset(
    state for state, successors in ALLOWED_TRANSITIONS.items() if not successors
)


def is_transition_allowed(from_state: BookingState, to_state: BookingState) -> bool:
    return to_state in ALLOWED_TRANSITIONS[from_state]
