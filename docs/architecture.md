# Phase 1A architecture

The control plane owns job, approval, permission, and audit records. External
systems are available only through typed adapters. AI-facing code must call the
control plane and must not import the Docker gateway or receive credentials.

The Docker broker is a separate trust boundary. Its public contract has four
read-only operations: list allowlisted names, status, health, and bounded logs.
It has no restart, exec, create, update, delete, or generic Docker operation.

Phase 1A includes no deployment configuration and publishes no ports.

