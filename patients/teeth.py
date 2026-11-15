"""FDI two-digit tooth numbers. First digit = quadrant (1 to 4 adult, 5 to 8 child), second = tooth from the midline."""
import re

from rest_framework import serializers

ADULT = {f"{q}{n}" for q in (1, 2, 3, 4) for n in range(1, 9)}   # 11-18, 21-28, 31-38, 41-48
PRIMARY = {f"{q}{n}" for q in (5, 6, 7, 8) for n in range(1, 6)}  # 51-55, 61-65, 71-75, 81-85
FDI_TEETH = ADULT | PRIMARY


def clean_teeth(value):
    """'12 14,16' -> '12, 14, 16'. Raises a readable error for anything that is not a real tooth."""
    tokens = [t for t in re.split(r"[,\s;/]+", str(value or "").strip()) if t]
    bad = [t for t in tokens if t not in FDI_TEETH]
    if bad:
        raise serializers.ValidationError(
            f"{', '.join(bad)}: not a valid tooth number. Use FDI numbers: 11 to 18, 21 to 28, 31 to 38, 41 to 48 "
            "(children 51 to 55, 61 to 65, 71 to 75, 81 to 85). Separate several teeth with commas."
        )
    seen = []
    for t in tokens:
        if t not in seen:
            seen.append(t)
    return ", ".join(seen)
