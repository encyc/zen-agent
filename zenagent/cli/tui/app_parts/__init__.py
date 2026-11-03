"""Per-concern mixins composing :class:`ZenAgentApp`.

One file per responsibility that must stay on the App instance (key actions
bound through Textual's ``action_*`` resolution, or state the tests address
directly). Everything with a real component boundary lives in ``screens/`` and
``widgets/`` instead; this package only hosts what cannot be an object of its
own. Methods move here verbatim, so each mixin reads like the slice of the old
``app.py`` it was cut from.
"""
