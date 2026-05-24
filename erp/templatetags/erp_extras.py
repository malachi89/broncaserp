from decimal import Decimal, InvalidOperation

from django import template


register = template.Library()


@register.filter(name="compact_quantity")
def compact_quantity(value):
    if value in (None, ""):
        return ""

    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return value

    formatted = format(number, "f")
    if "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")

    if formatted in {"", "-0"}:
        return "0"

    return formatted

