"""Scanner base class and re-exports from mcp.models.

BaseScanner is scan-specific. Severity, ScanFinding, and ScanContext are
re-exported from q_ai.mcp.models for backward compatibility.
"""

from abc import ABC, abstractmethod

from q_ai.mcp.models import ScanContext, ScanFinding, Severity

__all__ = ["BaseScanner", "ScanContext", "ScanFinding", "Severity"]


class BaseScanner(ABC):
    """Abstract base class for all scanner modules.

    Each scanner targets a specific security category.
    Subclasses must implement `scan()` which returns a list of ScanFindings.

    Attributes:
        name: Human-readable scanner name.
        category: Security category this scanner covers.
        description: What this scanner checks for.

    Example:
        >>> class InjectionScanner(BaseScanner):
        ...     name = "injection"
        ...     category = "command_injection"
        ...     description = "Tests for command injection via MCP tools"
        ...
        ...     async def scan(self, context: ScanContext) -> list[ScanFinding]:
        ...         findings = []
        ...         # ... test each tool for injection ...
        ...         return findings
    """

    name: str = ""
    category: str = ""
    description: str = ""

    @abstractmethod
    async def scan(self, context: ScanContext) -> list[ScanFinding]:
        """Execute the scanner against the target MCP server.

        Args:
            context: ScanContext containing server metadata, tools,
                resources, and configuration.

        Returns:
            List of ScanFinding objects for any vulnerabilities detected.

        Raises:
            ScanError: If the scanner encounters an unrecoverable error.
        """
        ...
