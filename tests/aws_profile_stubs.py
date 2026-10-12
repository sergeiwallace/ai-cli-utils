"""Stand-in ``aws``, ``claude`` and tool binaries for the SSO-profile strip tests.

Nothing here reaches AWS. The ``aws`` stub answers ``sts get-caller-identity`` with a
fixed ARN and records, by name only, the environment it was called with; the
``claude`` stub records what the agent process inherited and then runs a configured
tool the way an agent's shell would, so the tool's view is recorded too.
"""

from __future__ import annotations

from pathlib import Path

SSO_PROFILE = "example-sso-profile"
INSTANCE_ROLE_ARN = "arn:aws:sts::123456789012:assumed-role/example-instance-role/i-0123456789abcdef0"
SSO_ROLE_ARN = "arn:aws:sts::123456789012:assumed-role/AWSReservedSSO_ExampleAccess_0123456789abcdef/user"
NON_INSTANCE_ARN = "arn:aws:sts::123456789012:assumed-role/example-role/i-session"
IAM_USER_ARN = "arn:aws:iam::123456789012:user/example"
TOOL = "mytool"


def write_stubs(bin_dir: Path, report: Path, arn: str, aws_exit: int = 0) -> None:
    """Write ``aws``, ``claude`` and ``mytool`` stubs into ``bin_dir``; all append to ``report``."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    stubs = {
        "aws": f"""#!/bin/sh
{{
  [ -n "${{AWS_PROFILE+x}}" ] && echo "probe.AWS_PROFILE=set" || echo "probe.AWS_PROFILE=unset"
  [ -n "${{AWS_DEFAULT_PROFILE+x}}" ] && echo "probe.AWS_DEFAULT_PROFILE=set" || echo "probe.AWS_DEFAULT_PROFILE=unset"
  [ -n "$AWS_CONFIG_FILE" ] && [ ! -e "$AWS_CONFIG_FILE" ] && echo "probe.config=absent" || echo "probe.config=present"
  [ -n "$AWS_SHARED_CREDENTIALS_FILE" ] && [ ! -e "$AWS_SHARED_CREDENTIALS_FILE" ] \\
    && echo "probe.credentials=absent" || echo "probe.credentials=present"
  echo "probe.args=$*"
}} >> "{report}"
echo "{arn}"
exit {aws_exit}
""",
        "claude": f"""#!/bin/sh
{{
  [ -n "${{AWS_PROFILE+x}}" ] && echo "agent.AWS_PROFILE=${{AWS_PROFILE}}" || echo "agent.AWS_PROFILE=unset"
  [ -n "${{AI_SSO_PROFILE+x}}" ] && echo "agent.AI_SSO_PROFILE=${{AI_SSO_PROFILE}}" || echo "agent.AI_SSO_PROFILE=unset"
  [ -n "${{AWS_BEARER_TOKEN_BEDROCK+x}}" ] && echo "agent.AWS_BEARER_TOKEN_BEDROCK=set" \\
    || echo "agent.AWS_BEARER_TOKEN_BEDROCK=unset"
}} >> "{report}"
{TOOL}
echo "agent.done" >> "{report}"
""",
        TOOL: f"""#!/bin/sh
[ -n "${{AWS_PROFILE+x}}" ] && echo "tool.AWS_PROFILE=${{AWS_PROFILE}}" >> "{report}" \\
  || echo "tool.AWS_PROFILE=unset" >> "{report}"
""",
    }
    for name, body in stubs.items():
        path = bin_dir / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)


def write_config(config_home: Path, commands: list[str]) -> None:
    """An ai-cli-utils ``aws.toml`` naming ``commands`` as ``sso_profile_commands``."""
    target = config_home / "ai-cli-utils"
    target.mkdir(parents=True, exist_ok=True)
    names = ", ".join(f'"{c}"' for c in commands)
    (target / "aws.toml").write_text(f"sso_profile_commands = [{names}]\n", encoding="utf-8")


def read_report(report: Path) -> dict[str, str]:
    """The last value recorded for each key."""
    values: dict[str, str] = {}
    if report.exists():
        for line in report.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            values[key] = value if sep else ""
    return values
