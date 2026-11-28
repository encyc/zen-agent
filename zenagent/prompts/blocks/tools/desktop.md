See and operate desktop apps through the OS accessibility tree, not screenshots
(agent-desktop {{version}}). Loop:
`snapshot` an app to get elements each with a `ref`, act on a ref, read again to confirm. A ref
belongs to the snapshot that produced it, so after any change re-snapshot instead of reusing it
(`STALE_REF`). Read actions: snapshot|find|get|is|screenshot|status|permissions|list_apps|
list_windows. Act actions: click|type|set_value|clear|select|toggle|press|hover|drag|scroll|
wait|launch|close_app|focus_window|clipboard_get|clipboard_set. `app` picks the target; `ref`/
`query`/`text`/`value`/`combo` carry the payload. Call action=help first: it returns the
version-matched guide (refs, skeleton drill-down, error recovery). If an app publishes no
accessibility tree, say so rather than clicking blind. Success does not prove the UI changed —
verify with a second snapshot or `wait`.
