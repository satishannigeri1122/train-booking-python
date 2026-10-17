
from datetime import datetime, timedelta, timezone

import pytest

from app.fares import (
    BookingError,
    base_fare,
    fare,
    hold_expiry,
    next_position,
    occupancy_band,
    promote,
    promotion_order,
    refund,
    refund_rule,
    seats_available,
    validate_seats,
)


# -------------------- Base fares --------------------

def test_base_fare_uses_minimum_fare():
    assert base_fare(10, "SL") == 60


def test_base_fare_uses_distance_rate():
    assert base_fare(200, "SL") == 90


def test_base_fare_accepts_lowercase_class():
    assert base_fare(200, "sl") == 90


@pytest.mark.parametrize("travel_class", ["SL", "3A", "2A", "1A"])
def test_base_fare_supports_all_classes(travel_class):
    assert base_fare(100, travel_class) > 0


def test_base_fare_rejects_unknown_class():
    with pytest.raises(BookingError):
        base_fare(100, "INVALID")


def test_base_fare_rejects_negative_distance():
    with pytest.raises(BookingError):
        base_fare(-10, "SL")


# -------------------- Occupancy and fares --------------------

@pytest.mark.parametrize(
    ("sold", "capacity", "expected_band"),
    [
        (0, 100, "quiet"),
        (40, 100, "quiet"),
        (41, 100, "filling"),
        (70, 100, "filling"),
        (71, 100, "busy"),
        (90, 100, "busy"),
        (91, 100, "last_seats"),
        (100, 100, "last_seats"),
    ],
)
def test_occupancy_band_boundaries(sold, capacity, expected_band):
    band, _ = occupancy_band(sold, capacity)
    assert band == expected_band


def test_occupancy_band_rejects_zero_capacity():
    with pytest.raises(BookingError):
        occupancy_band(0, 0)


def test_occupancy_band_rejects_negative_capacity():
    with pytest.raises(BookingError):
        occupancy_band(0, -1)


def test_fare_returns_expected_details():
    result = fare(100, "SL", 10, 100)

    assert result["fare"] == 60
    assert result["band"] == "quiet"
    assert result["multiplier"] == 1.0
    assert result["base"] == 60
    assert result["class"] == "SL"


def test_fare_increases_when_train_is_fuller():
    quiet_fare = fare(200, "SL", 20, 100)["fare"]
    busy_fare = fare(200, "SL", 80, 100)["fare"]

    assert busy_fare > quiet_fare


def test_fare_rounds_half_up():
    # Base fare is Rs. 90; filling multiplier gives Rs. 103.50.
    result = fare(200, "SL", 41, 100)

    assert result["fare"] == 104


# -------------------- Seat availability --------------------

def test_seats_available_accounts_for_live_holds():
    assert seats_available(100, 80, 5) == 15


def test_seats_available_never_returns_negative():
    assert seats_available(10, 9, 5) == 0


def test_seats_available_without_holds():
    assert seats_available(10, 4) == 6


# -------------------- Refund rules --------------------

@pytest.mark.parametrize(
    ("hours", "expected_fraction"),
    [
        (49, 1.0),
        (48, 1.0),
        (47, 0.75),
        (24, 0.75),
        (23, 0.50),
        (12, 0.50),
        (11, 0.25),
        (4, 0.25),
        (3, 0.0),
        (0, 0.0),
        (-1, 0.0),
    ],
)
def test_refund_rule_boundaries(hours, expected_fraction):
    fraction, _ = refund_rule(hours)
    assert fraction == expected_fraction


def test_confirmed_refund_includes_clerkage():
    now = datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc)
    departure = now + timedelta(hours=48)

    result = refund(500, departure, now)

    assert result["refund"] == 470
    assert result["fraction"] == 1.0
    assert result["clerkage"] == 30.0


def test_waitlisted_ticket_gets_full_refund():
    now = datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc)
    departure = now + timedelta(hours=2)

    result = refund(500, departure, now, status="WL")

    assert result["refund"] == 500
    assert result["clerkage"] == 0.0
    assert result["fraction"] == 1.0


def test_refund_is_zero_after_departure():
    now = datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc)
    departure = now - timedelta(hours=1)

    result = refund(500, departure, now)

    assert result["refund"] == 0
    assert result["band"] == "after departure"


def test_refund_never_becomes_negative():
    now = datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc)
    departure = now + timedelta(hours=4)

    result = refund(10, departure, now)

    assert result["refund"] == 0


def test_refund_rejects_negative_amount():
    now = datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc)
    departure = now + timedelta(days=2)

    with pytest.raises(BookingError):
        refund(-100, departure, now)


# -------------------- Waiting-list ordering --------------------

def test_promotion_order_sorts_by_position():
    waitlist = [
        {"id": 3, "position": 3, "travel_class": "SL", "seats": 1},
        {"id": 1, "position": 1, "travel_class": "SL", "seats": 1},
        {"id": 2, "position": 2, "travel_class": "SL", "seats": 1},
    ]

    ordered = promotion_order(waitlist, "SL")

    assert [entry["id"] for entry in ordered] == [1, 2, 3]


def test_promotion_order_only_includes_requested_class():
    waitlist = [
        {"id": 1, "position": 1, "travel_class": "SL", "seats": 1},
        {"id": 2, "position": 1, "travel_class": "3A", "seats": 1},
    ]

    ordered = promotion_order(waitlist, "SL")

    assert [entry["id"] for entry in ordered] == [1]


def test_promote_respects_freed_seats():
    waitlist = [
        {"id": 1, "position": 1, "travel_class": "SL", "seats": 2},
        {"id": 2, "position": 2, "travel_class": "SL", "seats": 1},
    ]

    promoted, remaining = promote(waitlist, "SL", 2)

    assert [entry["id"] for entry in promoted] == [1]
    assert [entry["id"] for entry in remaining] == [2]
    assert promoted[0]["status"] == "CONFIRMED"
    assert remaining[0]["position"] == 1


def test_promote_skips_party_that_does_not_fit():
    waitlist = [
        {"id": 1, "position": 1, "travel_class": "SL", "seats": 3},
        {"id": 2, "position": 2, "travel_class": "SL", "seats": 1},
    ]

    promoted, remaining = promote(waitlist, "SL", 1)

    assert [entry["id"] for entry in promoted] == [2]
    assert [entry["id"] for entry in remaining] == [1]


def test_promote_preserves_other_classes():
    waitlist = [
        {"id": 1, "position": 1, "travel_class": "SL", "seats": 1},
        {"id": 2, "position": 1, "travel_class": "3A", "seats": 1},
    ]

    promoted, remaining = promote(waitlist, "SL", 1)

    assert [entry["id"] for entry in promoted] == [1]
    assert [entry["id"] for entry in remaining] == [2]


def test_promote_empty_waitlist():
    promoted, remaining = promote([], "SL", 2)

    assert promoted == []
    assert remaining == []


def test_promote_rejects_negative_freed_seats():
    with pytest.raises(BookingError):
        promote([], "SL", -1)


def test_next_position_is_class_specific():
    waitlist = [
        {"id": 1, "position": 1, "travel_class": "SL"},
        {"id": 2, "position": 2, "travel_class": "SL"},
        {"id": 3, "position": 1, "travel_class": "3A"},
    ]

    assert next_position(waitlist, "SL") == 3
    assert next_position(waitlist, "3A") == 2


# -------------------- Booking validation and holds --------------------

@pytest.mark.parametrize("seats", [1, 2, 3, 4, 5, 6])
def test_validate_seats_accepts_valid_party_sizes(seats):
    assert validate_seats(seats) == seats


@pytest.mark.parametrize("seats", [0, 7, -1])
def test_validate_seats_rejects_invalid_party_sizes(seats):
    with pytest.raises(BookingError):
        validate_seats(seats)


def test_validate_seats_rejects_non_numeric_input():
    with pytest.raises(BookingError):
        validate_seats("abc")


def test_hold_expiry_adds_five_minutes_by_default():
    now = datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc)

    assert hold_expiry(now) == now + timedelta(seconds=300)
