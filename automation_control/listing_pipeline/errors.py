class CardValidationError(Exception):
    def __init__(self, row: int, column: str, message: str):
        self.row = row
        self.column = column
        super().__init__(f"row {row}, column '{column}': {message}")


class GroupingError(Exception):
    def __init__(self, *, orphaned: list[str], duplicated: list[str], unknown: list[str] = ()):
        self.orphaned = orphaned
        self.duplicated = duplicated
        self.unknown = list(unknown)
        parts = []
        if orphaned:
            parts.append(f"cards not assigned to any listing: {', '.join(orphaned)}")
        if duplicated:
            parts.append(f"cards assigned to more than one listing: {', '.join(duplicated)}")
        if unknown:
            parts.append(f"listing card_ids not found in inventory: {', '.join(unknown)}")
        super().__init__("; ".join(parts))


class SalePlanMatchError(Exception):
    pass
