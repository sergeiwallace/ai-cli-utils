---
title: "SELinux blocks SSH port forwarding on Fedora 44"
category: bugs
tags: [ssh, selinux, port-forwarding, fedora, nats]
status: host-configuration-remedy
severity: P1
---

# SELinux blocks SSH port forwarding on Fedora 44

## Symptoms

An SSH connection to an affected Fedora 44 host succeeds, but a forwarded connection fails,
including VS Code Remote-SSH and plain local (`ssh -L`) or dynamic (`ssh -D`) forwarding:

```text
channel N: open failed: connect failed: open failed
```

This can also prevent ai-cli-utils' automatic NATS tunnel from becoming ready. That tunnel uses
`ssh -fNL 4222:localhost:4222`, so its remote-side connection has the same SELinux requirement.

## Confirmed cause

On Fedora 44 with `selinux-policy-44.7-1.fc44`, an SSH connection's forwarding child runs in
the `sshd_session_t` SELinux domain. The policy denies its outbound connection to an unreserved
port, even for a loopback target. An affected host records an AVC like:

```text
avc: denied { name_connect } ... comm="sshd-session" dest=4222 \
scontext=system_u:system_r:sshd_session_t:s0-s0:c0.c1023 \
tcontext=system_u:object_r:unreserved_port_t:s0 tclass=tcp_socket permissive=0
```

No matching `sshd_session_t` network-connect SELinux boolean is available on that policy
version. This is host policy configuration, not a VS Code-specific failure.

## Remedy

An administrator must generate and install a local SELinux policy module on the affected host:

```console
sudo ausearch -c 'sshd-session' --raw | audit2allow -M sshd_session_local
sudo semodule -i sshd_session_local.pp
```

Run the commands only after reproducing the denial so `audit2allow` uses the relevant AVCs.
The module requires root access and cannot be installed by ai-cli-utils. Retry the forwarded
connection after installation.

## Verification

Confirm that the original `ssh -L` or `ssh -D` connection can reach its known-listening target.
For the automatic NATS tunnel, confirm that the local port 4222 becomes reachable after the
client starts.
