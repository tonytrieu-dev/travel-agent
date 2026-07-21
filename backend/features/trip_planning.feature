Feature: Trip planning — create, update, search flights, and generate an itinerary
  A trip's own stored fields are the single source of truth for flight search and itinerary
  generation: the client supplies structured trip criteria once, never a free-text prompt.

  Scenario: Creating a trip with valid dates succeeds
    Given a trip request with a depart date next month and no return date
    When the trip is created
    Then the response is 200 with status "created"

  Scenario: A trip cannot be created with a depart date in the past
    Given a trip request with a depart date in the past
    When the trip is created
    Then the response is 422 with error code "validation_error"

  Scenario: A trip cannot be created with a return date before its depart date
    Given a trip request whose return date is before its depart date
    When the trip is created
    Then the response is 422 with error code "validation_error"

  Scenario: Updating a trip's budget leaves its dates unchanged
    Given an existing trip
    When the trip is updated with only a new budget
    Then the response is 200 with the new budget and the original dates

  Scenario: Updating a trip to a return date before its existing depart date is rejected
    Given an existing trip
    When the trip is updated with a return date before its existing depart date
    Then the response is 422 with error code "validation_error"
    And the trip's stored return date is unchanged

  Scenario: Updating a trip that does not exist returns 404
    When a nonexistent trip is updated
    Then the response is 404 with error code "trip_not_found"

  Scenario: Searching flights for a trip with no prior search calls the provider live
    Given an existing trip with no prior flight search
    When flights are searched for the trip
    Then the response is 200 with offers sourced "live"
    And the flight provider is called exactly once

  Scenario: Searching flights again within the cache TTL reuses the earlier real results
    Given an existing trip with no prior flight search
    And flights have already been searched live for that route and those dates
    When flights are searched for the trip
    Then the response is 200 with offers sourced "cached"
    And the flight provider is never called

  Scenario: Planning a trip whose criteria are clear returns a ready itinerary
    Given an existing trip
    And the planner will produce a ready itinerary
    When the trip is planned
    Then the response is 200 with status "ready" and an itinerary
    And the trip's status becomes "itinerary_ready"

  Scenario: Planning a trip whose criteria are ambiguous returns clarifying questions
    Given an existing trip
    And the planner will ask a clarifying question
    When the trip is planned
    Then the response is 200 with status "needs_clarification" and no itinerary stored

  Scenario: Planning a trip that already has an itinerary returns it without running the agent twice
    Given an existing trip that already has a generated itinerary
    And the planner will produce a ready itinerary
    When the trip is planned
    Then the response is 200 with status "ready" and an itinerary
    And the planner is never called
