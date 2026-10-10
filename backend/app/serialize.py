import datetime
import decimal
import json
import math
import uuid

MAX_SAFE_INTEGER = 2**53 - 1


def to_json_value(value):
    """Convert a value loaded by psycopg into something JSON (and JavaScript) can hold."""
    if value is None or isinstance(value, (str, bool)):
        return value

    if isinstance(value, int):
        return value if abs(value) <= MAX_SAFE_INTEGER else str(value)

    if isinstance(value, float):
        return value if math.isfinite(value) else None

    if isinstance(value, decimal.Decimal):
        if not value.is_finite():
            return None
        return int(value) if value == value.to_integral_value() and abs(value) <= MAX_SAFE_INTEGER else float(value)

    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()

    if isinstance(value, datetime.timedelta):
        return str(value)

    if isinstance(value, uuid.UUID):
        return str(value)

    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        try:
            text = raw.decode("utf-8")
            if text.isprintable():
                return text
        except UnicodeDecodeError:
            pass
        return "\\x" + raw.hex()

    if isinstance(value, dict):
        return {str(k): to_json_value(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [to_json_value(v) for v in value]

    return str(value)


def to_csv_value(value):
    value = to_json_value(value)

    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)

    return value
