def ensure(condition, message="Condition failed"):
    if not condition:
        raise AssertionError(message)


def ensure_equal(actual, expected):
    if actual != expected:
        raise AssertionError(f"Expected {expected!r}, got {actual!r}")
