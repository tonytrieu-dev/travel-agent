Feature: Human-in-the-loop booking gate
  The EXECUTED write is the only gated action. A human must confirm first, the quoted fare must
  still be within its price-hold TTL, and concurrent execute attempts must never double-book or
  burn a second booking-options quota call.

  Scenario: A concurrent double-execute books exactly once
    Given a confirmed booking whose price hold is still valid
    When execute is called twice concurrently
    Then both execute responses return 200 with the same booking reference
    And the booking-options provider is called exactly once
    And the booking-options provider receives the flight's route and outbound date
    And the booking ends EXECUTED with exactly one transition into EXECUTED

  Scenario: Execute is rejected before the human confirms
    Given a booking still pending user confirmation
    When execute is called once
    Then the response is 409 with error code "invalid_transition"
    And no booking reference is stored on the booking
    And the booking-options provider is never called

  Scenario: A cancelled flight can be booked again
    Given a booking still pending user confirmation
    When the booking is cancelled and the same flight is requested again
    Then a new pending booking is returned for the same flight

  Scenario: An expired fare cannot be executed
    Given a confirmed booking whose price hold has already expired
    When execute is called once
    Then the response is 409 with error code "booking_expired"
    And the booking is left EXPIRED with an audit transition into EXPIRED
    And the booking-options provider is never called

  Scenario: A booking-options upstream failure still completes the human-confirmed execute
    Given a confirmed booking whose price hold is still valid
    And the booking-options provider will fail
    When execute is called once
    Then the response is 200 with a booking reference and no booking options stored
    And the booking ends EXECUTED with exactly one transition into EXECUTED

  Scenario: A signed Slack approval confirms the booking
    Given a booking still pending user confirmation
    And Slack is configured with a known signing secret and channel
    When a correctly signed Slack approval for that booking arrives
    Then the Slack response is an empty 200 acknowledgment
    And the booking ends CONFIRMED

  Scenario: An unsigned Slack request is rejected without touching the booking
    Given a booking still pending user confirmation
    And Slack is configured with a known signing secret and channel
    When an incorrectly signed Slack approval for that booking arrives
    Then the Slack response is 401
    And the booking is still PENDING_USER_CONFIRMATION
