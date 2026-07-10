"""Deterministic ASCII guitar-tab renderer."""

from fractions import Fraction

from fretsure.tab import Tab

_NAMES = ("E", "A", "D", "G", "B", "e")  # by string index 0..5 (low -> high)


def render_ascii(tab: Tab) -> str:
    """Render a Tab as 6 ASCII lines, high-e string on top. Columns are the
    distinct onsets in ascending order; each cell is the fret or dashes."""
    onsets = sorted({n.onset for n in tab.notes})
    fret_at: dict[tuple[int, Fraction], int] = {
        (n.string, n.onset): n.fret for n in tab.notes
    }
    width = max((len(str(n.fret)) for n in tab.notes), default=1)

    lines: list[str] = []
    for string in range(5, -1, -1):
        cells = [
            str(fret_at[(string, o)]).rjust(width, "-")
            if (string, o) in fret_at
            else "-" * width
            for o in onsets
        ]
        lines.append(f"{_NAMES[string]}|" + "-".join(cells) + "-|")
    return "\n".join(lines)
