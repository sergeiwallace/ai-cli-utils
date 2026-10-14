"""The one owner of Rich live displays (``Live``, ``Progress``, ``Status``).

Nothing here yet: the first live display (the reload line, AI-CLI-zss5) brings the
owner with it. The rule it will implement is set by
docs/research/rich-live-displays-in-wrapped-sessions.md, section 5. A display
starts only on a TTY this process holds in the foreground with ``OPOST`` on and no
``foreign_terminal()`` span open, it keeps the cursor visible, it is transient and
never uses the alternate screen, records print above it through the output lock,
and one idempotent teardown runs from the context manager's exit,
``flush_sinks()``, ``foreign_terminal()`` entry, the SIGTERM and SIGHUP handlers,
and before any child that inherits the terminal.

``tests/test_output_enforcement.py`` fails any import of ``rich.live``,
``rich.progress`` or ``rich.status``, and any ``Console.status`` call, outside
this module.
"""
