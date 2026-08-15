import json
import subprocess
from dataclasses import dataclass
from typing import Protocol


class ContainerNotAllowed(Exception):
    pass


class DockerGateway(Protocol):
    def status(self, name: str) -> dict: ...
    def logs(self, name: str, tail: int) -> str: ...


@dataclass(frozen=True)
class DockerCliGateway:
    """Fixed read-only Docker CLI operations; no generic command entry point."""

    timeout_seconds: int = 10

    def status(self, name: str) -> dict:
        output = self._run(["docker", "inspect", "--format", "{{json .State}}", name])
        return json.loads(output)

    def logs(self, name: str, tail: int) -> str:
        return self._run(["docker", "logs", "--tail", str(tail), name])

    def _run(self, command: list[str]) -> str:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            env={"PATH": "/usr/bin:/usr/local/bin"},
        )
        return result.stdout

