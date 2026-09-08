---
title: "Optional: NATS Setup for Fleet Messaging"
category: guide
tags: [nats, messaging, fleet, optional, setup]
status: current
source: internal
---

# Optional: NATS Setup for Fleet Messaging

## Table of Contents

- [Overview](#overview)
- [Features That Require NATS](#features-that-require-nats)
- [Features That Work Without NATS](#features-that-work-without-nats)
- [Installing NATS Server](#installing-nats-server)
  - [macOS](#macos)
  - [Linux (Debian/Ubuntu)](#linux-debianubuntu)
  - [Docker](#docker)
- [Configuring ai-cli-utils](#configuring-ai-cli-utils)
- [Enabling JetStream](#enabling-jetstream)
- [Verifying the Connection](#verifying-the-connection)
- [Running NATS as a Service](#running-nats-as-a-service)
  - [macOS (launchd)](#macos-launchd)
  - [Linux (systemd)](#linux-systemd)
- [Multi-Machine Setup](#multi-machine-setup)

---

## Overview

NATS is an optional dependency. `ai-cli-utils` uses it for real-time fleet messaging — heartbeats, session events, quota snapshots, and sync notifications. If NATS is not running, all NATS-dependent features degrade gracefully: connection failures are logged and the tool continues operating with file-based fallbacks.

---

## Features That Require NATS

| Feature | What it does |
|---------|-------------|
| `ai sync watch` | Listens for sync events (`sync.push`, `sync.pull`) to trigger automatic sync |
| `ai memory watch` | Publishes `dream.started`/`dream.completed` events to coordinate sync pausing |
| `ai quota watch` | Subscribes to `quota.*` events to track Claude token usage across sessions |
| Session heartbeats | Workers publish `heartbeat.{session}` so the dashboard knows which sessions are alive |
| Session events | `ai internal publish-session-event` notifies other sessions of starts and stops |

---

## Features That Work Without NATS

- `ai c` / `ai g` session management
- `ai sync push/pull` (direct rsync/SSH, no NATS required)
- `ai tunnel start/stop/status`
- `ai spend gemini` (historical local-log reporting)
- `ai update`
- `ai ls`, `ai attach`, `ai reconnect`

---

## Installing NATS Server

### macOS

```bash
brew install nats-server
```text

### Linux (Debian/Ubuntu)

Download the latest release from https://github.com/nats-io/nats-server/releases:

```bash
# Replace VERSION with the latest (e.g. v2.10.14)
wget https://github.com/nats-io/nats-server/releases/download/VERSION/nats-server-VERSION-linux-amd64.zip
unzip nats-server-VERSION-linux-amd64.zip
sudo mv nats-server /usr/local/bin/
nats-server --version
```text

Or via the official install script:

```bash
curl -sf https://binaries.nats.dev/nats-io/nats-server/v2@latest | sh
sudo mv nats-server /usr/local/bin/
```text

### Docker

```bash
docker run -d --name nats -p 4222:4222 nats:latest --jetstream
```text

---

## Configuring ai-cli-utils

Edit `~/.config/ai-cli/config.toml`:

```toml
[messaging]
nats_servers = ["nats://localhost:4222"]
```text

For a remote NATS server (e.g. on your dev server):

```toml
[messaging]
nats_servers = ["nats://your-server.example.com:4222"]
```text

Multiple servers (cluster):

```toml
[messaging]
nats_servers = ["nats://server1:4222", "nats://server2:4222"]
```text

---

## Enabling JetStream

`ai-cli-utils` uses NATS JetStream for durable message delivery (sync and quota events). JetStream must be enabled on your NATS server.

Start with JetStream:

```bash
nats-server --jetstream
```text

Or in your config file (`/etc/nats/nats.conf` or `~/.config/nats/nats-server.conf`):

```conf
jetstream {
  store_dir: "/tmp/nats-jetstream"
}
```text

Verify JetStream is active:

```bash
nats server info | grep -i jetstream
```text

---

## Verifying the Connection

After starting NATS and configuring `ai-cli-utils`, verify connectivity:

```bash
# Install the NATS CLI (optional but useful)
brew install nats-io/nats-tools/nats   # macOS
# or: go install github.com/nats-io/natscli/nats@latest

# Check server is reachable
nats server ping

# Publish a test message
nats pub test.hello "world"
```text

When you start an AI session with `ai c 1`, heartbeat events will appear on `heartbeat.*` if NATS is connected.

---

## Running NATS as a Service

### macOS (launchd)

After `brew install nats-server`, enable as a service:

```bash
brew services start nats-server
```text

To enable JetStream, edit the Homebrew plist or create `~/Library/LaunchAgents/nats-server.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>nats-server</string>
    <key>ProgramArguments</key>
    <array>
        <string>/opt/homebrew/bin/nats-server</string>
        <string>--jetstream</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
</dict>
</plist>
```text

```bash
launchctl load ~/Library/LaunchAgents/nats-server.plist
```text

### Linux (systemd)

Create `/etc/systemd/system/nats-server.service`:

```ini
[Unit]
Description=NATS Server
After=network.target

[Service]
ExecStart=/usr/local/bin/nats-server --jetstream
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```text

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now nats-server
sudo systemctl status nats-server
```text

---

## Multi-Machine Setup

To share NATS between your local machine and a remote dev server, run NATS on the server and connect both machines to it.

**On the server** — start NATS server listening on all interfaces:

```bash
nats-server --jetstream --addr 0.0.0.0
```text

**On both machines** — point `ai-cli-utils` at the server:

```toml
[messaging]
nats_servers = ["nats://your-server-ip:4222"]
```text

This enables session event coordination and other fleet messaging between your local and remote CC sessions.

> **Security note:** NATS by default has no authentication. For a public-facing server, configure TLS and authentication. See https://docs.nats.io/running-a-nats-service/configuration/securing_nats for details. For home/private network use, the default open config is fine.
