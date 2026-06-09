"""Shared labelled-field helper + focus cue.

The app's one standard control style: a label above a control whose label turns
the accent colour (and underlines) while the control has focus. Use :func:`field`
to build the control and mix :class:`LabelledFields` into the screen so the cue
fires. Styling lives in the ``.field`` / ``.field Label.active-label`` CSS rules
(see ``EchoBooksApp.CSS``).
"""

from __future__ import annotations

from textual.containers import Vertical
from textual.widgets import Input, Label, Select, TextArea


def field(label: str, widget: Input | Select | TextArea, *, classes: str = "") -> Vertical:
    """A labelled field; its label lights up when the field has focus.

    ``classes`` adds extra classes to the wrapper (e.g. ``"narrow"`` for a
    fixed-width toolbar select).
    """
    if isinstance(widget, Input):
        # Don't select-all on focus — that draws a box over the value, which
        # reads as "highlighted". The label cue is the focus indicator instead.
        widget.select_on_focus = False
    return Vertical(Label(label), widget, classes=f"field {classes}".strip())


class LabelledFields:
    """Screen mixin: colour the label of whichever ``.field`` holds focus.

    Add to any screen that composes :func:`field` controls. Relies on the
    ``.field Label.active-label`` CSS rule for the actual colour/underline.
    """

    def on_descendant_focus(self) -> None:
        self.call_after_refresh(self._highlight_active_field)  # type: ignore[attr-defined]

    def on_descendant_blur(self) -> None:
        self.call_after_refresh(self._highlight_active_field)  # type: ignore[attr-defined]

    def _highlight_active_field(self) -> None:
        for fld in self.query(".field"):  # type: ignore[attr-defined]
            active = "focus-within" in fld.pseudo_classes
            for label in fld.query(Label):
                label.set_class(active, "active-label")
