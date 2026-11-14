"""The skill/tool recomposition slice lives beside Runtime, not inside it.

Audit P1-9 (Task 31, first cut): ``Runtime`` was the god class — recompose,
refresh/reconcile/rediscover, limits, session switching and lifecycle all in
one file. This slice moves the discovery/rebuild half into
``zenagent/app.skills_recompose`` as module functions taking the runtime;
the methods stay as one-line delegates so callers (ops mixins, fleet wiring,
``/reload``) keep their surface.
"""

from __future__ import annotations

import inspect

from zenagent.app import runtime as runtime_module
from zenagent.app import skills_recompose


def test_recomposition_functions_live_in_the_slice_module():
    for name in (
        "recompose",
        "reconcile",
        "reconcile_async",
        "refresh_skills",
        "rediscover",
        "signature",
        "change_note",
        "active_skills",
    ):
        assert callable(getattr(skills_recompose, name, None)), name


def test_runtime_methods_delegate_into_the_slice():
    for method in (
        "_recompose",
        "reconcile",
        "reconcile_async",
        "refresh_skills",
        "rediscover",
    ):
        source = inspect.getsource(getattr(runtime_module.Runtime, method))
        assert "skills_recompose." in source, method


def test_signature_and_change_note_helpers_left_runtime():
    # They became module functions in the slice (shared by skills and
    # templates); no copy stays behind on the class.
    assert not hasattr(runtime_module.Runtime, "_signature")
    assert not hasattr(runtime_module.Runtime, "_change_note")
