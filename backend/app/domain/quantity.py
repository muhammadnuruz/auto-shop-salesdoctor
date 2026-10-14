"""Order quantity math: ``quantity = blocks * pack_quantity + pieces`` (all in pieces)."""

from decimal import ROUND_HALF_UP, Decimal

MAX_BLOCKS = 100_000
MAX_PIECES = Decimal(1_000_000)
QTY_STEP = Decimal("0.001")
MONEY_STEP = Decimal("0.01")
PRICE_STEP = Decimal("0.0001")


class QuantityError(ValueError):
    """Invalid quantity input; ``code`` is a stable machine-readable reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def has_pack(pack_quantity: Decimal | None) -> bool:
    return pack_quantity is not None and pack_quantity > 0


def compute_quantity(blocks: int, pieces: Decimal, pack_quantity: Decimal | None) -> Decimal:
    """Total pieces for a line; raises ``QuantityError`` on invalid input.

    ``blocks`` are only allowed for products with a positive ``pack_quantity``.
    """
    if not pieces.is_finite():  # NaN/Infinity must be rejected before any comparison
        raise QuantityError("bad_pieces", "Pieces must be a finite number")
    if blocks < 0 or pieces < 0:
        raise QuantityError("negative_quantity", "Quantity must not be negative")
    if blocks > MAX_BLOCKS or pieces > MAX_PIECES:
        raise QuantityError("quantity_too_large", "Quantity is too large")
    if pieces != pieces.quantize(QTY_STEP):
        raise QuantityError("bad_pieces", "Pieces allow at most 3 decimal places")
    if blocks and not has_pack(pack_quantity):
        raise QuantityError("blocks_not_allowed", "This product has no pack size; use pieces")
    pack = pack_quantity if has_pack(pack_quantity) and pack_quantity is not None else Decimal(0)
    return (Decimal(blocks) * pack + pieces).quantize(QTY_STEP, rounding=ROUND_HALF_UP)


def line_sum(quantity: Decimal, price: Decimal) -> Decimal:
    return (quantity * price).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)
