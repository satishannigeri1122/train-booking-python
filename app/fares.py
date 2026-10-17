
"""Pure fare, refund and waiting-list rules. No database, no HTTP."""

from datetime import timedelta

# Rupees per kilometre, before any occupancy multiplier.
CLASS_RATE = {
    "SL": 0.45,
    "3A": 1.20,
    "2A": 1.75,
    "1A": 2.90,
}

CLASS_NAMES = {
    "SL": "Sleeper",
    "3A": "AC 3 Tier",
    "2A": "AC 2 Tier",
    "1A": "AC First",
}

# Minimum fare by travel class.
MIN_FARE = {
    "SL": 60,
    "3A": 180,
    "2A": 250,
    "1A": 420,
}

# (upper occupancy fraction, band name, multiplier)
FARE_BANDS = (
    (0.40, "quiet", 1.00),
    (0.70, "filling", 1.15),
    (0.90, "busy", 1.35),
    (1.01, "last_seats", 1.60),
)

# (minimum hours before departure, fraction refunded, label)
REFUND_BANDS = (
    (48, 1.00, "more than 48 hours"),
    (24, 0.75, "24 to 48 hours"),
    (12, 0.50, "12 to 24 hours"),
    (4, 0.25, "4 to 12 hours"),
)

CLERKAGE = 30.0
MAX_SEATS_PER_BOOKING = 6
HOLD_SECONDS = 300


class BookingError(ValueError):
    """Raised when a booking or fare input is invalid."""


def _round_half_up(value):
    """Round to the nearest integer, with half values rounded away from zero.

    A small tolerance compensates for floating-point representation errors,
    such as 90 * 1.15 being represented slightly below 103.5.
    """
    epsilon = 1e-9

    if value >= 0:
        return int(value + 0.5 + epsilon)

    return -int(-value + 0.5 + epsilon)


# --------------------------------------------------------------------------
# Fares
# --------------------------------------------------------------------------

def occupancy_band(sold, capacity):
    """Return the occupancy band and its pricing multiplier."""
    if capacity <= 0:
        raise BookingError("a class with no seats has no occupancy")

    sold = max(0, int(sold))
    fraction_sold = sold / float(capacity)

    for bound, name, multiplier in FARE_BANDS:
        if fraction_sold <= bound:
            return name, multiplier

    return FARE_BANDS[-1][1], FARE_BANDS[-1][2]


def base_fare(distance_km, travel_class):
    """Calculate the distance-based fare before occupancy pricing."""
    travel_class = str(travel_class).upper()

    if travel_class not in CLASS_RATE:
        raise BookingError(f"unknown class {travel_class!r}")

    if distance_km < 0:
        raise BookingError("distance cannot be negative")

    distance_fare = float(distance_km) * CLASS_RATE[travel_class]
    return max(distance_fare, float(MIN_FARE[travel_class]))


def fare(distance_km, travel_class, sold, capacity):
    """Calculate a ticket fare using the current occupancy multiplier."""
    band, multiplier = occupancy_band(sold, capacity)
    base = base_fare(distance_km, travel_class)

    amount = _round_half_up(base * multiplier)

    return {
        "fare": amount,
        "band": band,
        "multiplier": multiplier,
        "base": _round_half_up(base),
        "class": str(travel_class).upper(),
        "class_name": CLASS_NAMES.get(str(travel_class).upper(), ""),
    }


def seats_available(capacity, confirmed, live_holds=0):
    """Return available seats after confirmed bookings and live holds."""
    return max(0, int(capacity) - int(confirmed) - int(live_holds))


# --------------------------------------------------------------------------
# Refunds
# --------------------------------------------------------------------------

def hours_before(departure, now):
    """Return hours until departure; negative means the train has left."""
    return (departure - now).total_seconds() / 3600.0


def refund_rule(hours):
    """Return the refund fraction and label for the departure time."""
    for minimum_hours, fraction, label in REFUND_BANDS:
        if hours >= minimum_hours:
            return fraction, label

    if hours >= 0:
        return 0.0, "less than 4 hours"

    return 0.0, "after departure"


def refund(amount_paid, departure, now, status="CONFIRMED"):
    """Calculate a cancellation refund, including any applicable clerkage."""
    paid = float(amount_paid)

    if paid < 0:
        raise BookingError("a ticket cannot have cost less than nothing")

    hours = hours_before(departure, now)

    if str(status).upper() in ("WAITLISTED", "WL"):
        return {
            "refund": _round_half_up(paid),
            "fraction": 1.0,
            "clerkage": 0.0,
            "band": "waitlisted - never confirmed",
            "hours_before_departure": round(hours, 2),
        }

    fraction, label = refund_rule(hours)
    gross = paid * fraction
    net = max(0.0, gross - CLERKAGE if gross > 0 else 0.0)

    return {
        "refund": _round_half_up(net),
        "fraction": fraction,
        "clerkage": CLERKAGE if gross > 0 else 0.0,
        "band": label,
        "hours_before_departure": round(hours, 2),
    }


# --------------------------------------------------------------------------
# Waiting list
# --------------------------------------------------------------------------

def promotion_order(waitlist, travel_class):
    """Sort waiting passengers by position, considering only their class."""
    travel_class = str(travel_class).upper()

    same_class = [
        entry
        for entry in waitlist
        if str(entry.get("travel_class", "")).upper() == travel_class
    ]

    return sorted(
        same_class,
        key=lambda entry: (int(entry["position"]), int(entry["id"])),
    )


def promote(waitlist, travel_class, freed_seats):
    """Promote whole parties that fit, and renumber those still waiting."""
    if freed_seats < 0:
        raise BookingError("cannot free a negative number of seats")

    queue = promotion_order(waitlist, travel_class)
    remaining_seats = int(freed_seats)
    promoted = []
    remaining = []

    for entry in queue:
        requested_seats = int(entry.get("seats", 1))

        if requested_seats <= remaining_seats:
            remaining_seats -= requested_seats
            promoted.append({**entry, "status": "CONFIRMED"})
        else:
            remaining.append(entry)

    other_classes = [
        entry
        for entry in waitlist
        if str(entry.get("travel_class", "")).upper()
        != str(travel_class).upper()
    ]

    renumbered = [
        {**entry, "position": position}
        for position, entry in enumerate(remaining, start=1)
    ]

    return promoted, renumbered + other_classes


def next_position(waitlist, travel_class):
    """Return the next position in the specified class's waiting list."""
    return len(promotion_order(waitlist, travel_class)) + 1


def hold_expiry(now, seconds=HOLD_SECONDS):
    """Return the expiry time for a temporary seat hold."""
    return now + timedelta(seconds=seconds)


def validate_seats(seats):
    """Validate a booking party size from one to six seats."""
    try:
        number_of_seats = int(seats)
    except (TypeError, ValueError) as exc:
        raise BookingError("seats must be a whole number") from exc

    if number_of_seats < 1:
        raise BookingError("book at least one seat")

    if number_of_seats > MAX_SEATS_PER_BOOKING:
        raise BookingError(
            f"at most {MAX_SEATS_PER_BOOKING} seats in one booking"
        )

    return number_of_seats
