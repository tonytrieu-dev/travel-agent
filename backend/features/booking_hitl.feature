Feature: Human-in-the-loop booking gate
  The EXECUTED write is the only gated action. A human must confirm first, the quoted fare must
  still be within its price-hold TTL, and concurrent execute attempts must never double-book or
  burn a second booking-options quota call.

  Scenario: A concurrent double-execute books exactly once
    Given a confirmed booking whose price hold is still valid
    When execute is called twice concurrently
    Then both execute responses return 200 with the same booking reference
    And the booking-options provider is called exactly once
    And the booking ends EXECUTED with exactly one transition into EXECUTED

  Scenario: Execute is rejected before the human confirms
    Given a booking still pending user confirmation
    When execute is called once
    Then the response is 409 with error code "invalid_transition"
    And no booking reference is stored on the booking
    And the booking-options provider is never called

  Scenario: An expired fare cannot be executed
    Given a confirmed booking whose price hold has already expired
    When execute is called once
    Then the response is 409 with error code "booking_expired"
    And the booking is left EXPIRED with an audit transition into EXPIRED
    And the booking-options provider is never called

  Scenario: A booking-options upstream failure surfaces as a structured error, not a crash
    Given a confirmed booking whose price hold is still valid
    And the booking-options provider will fail
    When execute is called once
    Then the response is 502 with error code "booking_options_unavailable"
    And the booking is left CONFIRMED with no booking reference stored
