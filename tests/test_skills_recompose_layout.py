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


def test_bootstrap_assembly_lives_in_the_bootstrap_module():
    import inspect

    from zenagent.app import bootstrap

    assert callable(bootstrap.assemble)
    source = inspect.getsource(runtime_module.Runtime.bootstrap)
    assert "zenagent.app.bootstrap" in source and "assemble" in source
    # 回滚留在 assemble 里，委托不得吞掉失败语义
    assert "except BaseException" not in source
