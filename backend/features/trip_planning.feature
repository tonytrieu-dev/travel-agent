Feature: Trip planning — create, update, search flights, and generate an itinerary
  A trip's own stored fields are the single source of truth for flight search and itinerary
  generation: the client supplies structured trip criteria once, never a free-text prompt.

  Scenario: Creating a trip with valid dates succeeds
    Given a trip request with a depart date next month and no return date
    When the trip is created
    Then the response is 200 with status "created"

  Scenario: A trip cannot be created with a return date before its depart date
    Given a trip request whose return date is before its depart date
    When the trip is created
    Then the response is 422 with error code "validation_error"

  Scenario: A trip cannot be created without age or fitness level
    Given a trip request missing age and fitness level
    When the trip is created
    Then the response is 422 with error code "validation_error"

  Scenario: Searching flights for a trip with no prior search calls the provider live
    Given an existing trip with no prior flight search
    When flights are searched for the trip
    Then the response is 200 with offers sourced "live"
    And the flight provider is called exactly once
    And a "search_flights" execution event is recorded for the trip
    And a flight-search agent run with metrics is recorded for the trip
    And the flight-search agent run owns its API event

  Scenario: Searching flights again within the cache TTL reuses the earlier real results
    Given an existing trip with no prior flight search
    And flights have already been searched live for that route and those dates
    When flights are searched for the trip
    Then the response is 200 with offers sourced "cached"
    And the flight provider is never called

  Scenario: Flight search surfaces the cheapest offer first
    Given an existing trip with no prior flight search
    And the flight provider will return offers priced 812, 499, and 640 USD
    When flights are searched for the trip
    Then the response is 200 with offers ordered cheapest first

  Scenario: An outbound-only cached round trip is refreshed with its exact return
    Given an existing round-trip trip with an outbound-only cached flight search
    And the flight provider will return a paired outbound and return offer
    When flights are searched for the trip
    Then the response offer contains the outbound and return legs in travel order
    And the flight provider is called exactly once

  Scenario: Changing a trip's route after planning invalidates its cached flights and itinerary
    Given an existing trip with cached flights and a generated itinerary
    When the trip's destination airport is changed
    Then the response is 200 and the trip has no cached flight offers
    And the trip has no stored itinerary

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
