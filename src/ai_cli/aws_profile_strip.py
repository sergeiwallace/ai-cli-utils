"""Keep an inherited federated ``AWS_PROFILE`` out of a Claude Code agent on an EC2 host.

On an EC2 host whose instance role can call the model provider, a shell that exports
``AWS_PROFILE`` naming an SSO-backed profile hands that profile to every agent the
launcher starts. The agent's model calls then ride a token that only a human browser
approval can renew, and the session dies when it expires, even though the instance
role would have answered unattended.

Immediately before the agent starts, and only when the ambient identity is proved to
be an EC2 instance role, the launcher moves ``AWS_PROFILE`` to ``AI_SSO_PROFILE`` and
unsets it. Everywhere else the variable is left exactly as it was.

The proof is a capability probe, not a configuration read: ``aws sts get-caller-identity``
with both profile variables unset and both AWS config files pointed at a path that does
not exist. It must return an assumed-role ARN that is not an IAM Identity Center
(``AWSReservedSSO_``) role and whose role-session-name is an EC2 instance id. Every
failure direction (no CLI, a non-zero exit, an SSO ARN, an IAM user, a session name that
is not an instance id) leaves the profile alone.

Tools that need the federated profile can be listed as ``sso_profile_commands`` in
``aws.toml`` beside ``config.toml``. For each one found on PATH, the strip writes a small
shim that restores ``AWS_PROFILE`` from ``AI_SSO_PROFILE`` and execs the real binary, and
puts the shim directory first on the agent's PATH. Nothing else sees the profile. The
list has its own file, which this package only reads, so whatever provisions a host can
own it outright instead of editing a file the user maintains by hand.

The logic is shell rather than Python because the agent's environment is the pane's,
not the launcher's: a tmux pane inherits from the tmux server and a project ``.envrc``
can add variables after the launcher has exited. It therefore runs where the agent
starts, after direnv, on every launch path.
"""

import re
import shlex
import tomllib

from .config import get_xdg_config_home

SSO_PROFILE_VAR = "AI_SSO_PROFILE"
STRIP_FUNCTION = "_ai_strip_inherited_sso_profile"
SSO_PROFILE_COMMANDS_FILE = "aws.toml"
_COMMAND_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}")


def configured_sso_profile_commands() -> tuple[str, ...]:
    """Commands named by ``sso_profile_commands`` in ``aws.toml``; empty when absent or invalid.

    Every launch script is generated through here, so nothing about this file may stop
    one: resolving the config directory can itself raise, not only reading the file.
    """
    try:
        with (get_xdg_config_home() / SSO_PROFILE_COMMANDS_FILE).open("rb") as fh:
            data = tomllib.load(fh)
    except Exception:
        return ()
    names = data.get("sso_profile_commands")
    if not isinstance(names, list):
        return ()
    return tuple(dict.fromkeys(n for n in names if isinstance(n, str) and _COMMAND_NAME.fullmatch(n)))


def strip_function_definitions(commands: tuple[str, ...] = ()) -> str:
    """Shell definitions (bash 3.2+ and zsh) of the strip function and its probe cache."""
    shim_call = f"\n  _ai_publish_sso_profile_shims {shlex.join(commands)}" if commands else ""
    return f"""_ai_ambient_instance_role=""
_ai_publish_sso_profile_shims() {{
  local _ai_dir _ai_cmd _ai_real _ai_tmp
  _ai_dir="${{XDG_STATE_HOME:-$HOME/.local/state}}/ai-cli-utils/sso-profile-shims"
  mkdir -p "$_ai_dir" 2>/dev/null || return 0
  for _ai_cmd in "$@"; do
    _ai_real="$(command -v "$_ai_cmd" 2>/dev/null)" || continue
    [[ "$_ai_real" == /* && "$_ai_real" != *"'"* && "$_ai_real" != "$_ai_dir/$_ai_cmd" ]] || continue
    _ai_tmp="$_ai_dir/.$_ai_cmd.$$"
    {{
      printf '%s\\n' '#!/bin/sh' \\
        '# Written by ai-cli-utils: restores the AWS profile the session launcher set aside.' \\
        'if [ -z "${{AWS_PROFILE:-}}" ] && [ -n "${{{SSO_PROFILE_VAR}:-}}" ]; then' \\
        '  AWS_PROFILE="${SSO_PROFILE_VAR}"; export AWS_PROFILE' \\
        'fi'
      printf "exec '%s' \\"\\$@\\"\\n" "$_ai_real"
    }} > "$_ai_tmp" && chmod 755 "$_ai_tmp" && mv -f "$_ai_tmp" "$_ai_dir/$_ai_cmd"
  done
  [[ ":$PATH:" == *":$_ai_dir:"* ]] || {{ PATH="$_ai_dir:$PATH"; export PATH; }}
}}
{STRIP_FUNCTION}() {{
  [[ -n "${{AWS_PROFILE:-}}" ]] || return 0
  if [[ -z "$_ai_ambient_instance_role" ]]; then
    _ai_ambient_instance_role=0
    local _ai_arn="" _ai_absent
    _ai_absent="${{TMPDIR:-/tmp}}/ai-cli-no-aws-config-$$-$RANDOM$RANDOM"
    if command -v aws >/dev/null 2>&1 && [[ ! -e "$_ai_absent" ]]; then
      _ai_arn="$(unset AWS_PROFILE AWS_DEFAULT_PROFILE
        AWS_CONFIG_FILE="$_ai_absent" AWS_SHARED_CREDENTIALS_FILE="$_ai_absent" \\
        AWS_METADATA_SERVICE_TIMEOUT=2 AWS_METADATA_SERVICE_NUM_ATTEMPTS=1 \\
          aws sts get-caller-identity --query Arn --output text 2>/dev/null)" || _ai_arn=""
    fi
    case "$_ai_arn" in
      (*:assumed-role/AWSReservedSSO_*) ;;
      (arn:*:sts::*:assumed-role/*/i-[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]*)
        _ai_ambient_instance_role=1 ;;
    esac
  fi
  [[ "$_ai_ambient_instance_role" == 1 ]] || return 0
  {SSO_PROFILE_VAR}="$AWS_PROFILE"
  export {SSO_PROFILE_VAR}
  unset AWS_PROFILE{shim_call}
}}
"""


def wrap_command(command: list[str], shell: str, commands: tuple[str, ...] | None = None) -> list[str]:
    """Run ``command`` through ``shell`` so the strip applies to the process it execs into.

    For the launch paths that exec the agent directly instead of through the session
    script. POSIX shells only; Windows launches are not wrapped.
    """
    if commands is None:
        commands = configured_sso_profile_commands()
    script = strip_function_definitions(commands) + f'{STRIP_FUNCTION}\nexec "$@"\n'
    return [shell, "-c", script, "ai-cli", *command]
