"""Server builder for inject payloads.

Dynamically constructs a FastMCP server from payload templates, registering
each template as a tool with a poisoned description and response handler.
"""

from __future__ import annotations

import keyword
import re
from collections import defaultdict
from collections.abc import Callable
from typing import Any, cast

from mcp.server.fastmcp import FastMCP

from q_ai.inject.models import PayloadTemplate

_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _validate_param_names(param_names: list[str]) -> None:
    """Validate that parameter names are safe Python identifiers.

    Args:
        param_names: Parameter names to validate.

    Raises:
        ValueError: If any name is not a valid Python identifier or is a keyword.
    """
    for name in param_names:
        if not _IDENT_RE.match(name) or keyword.iskeyword(name):
            raise ValueError(f"Invalid parameter name {name!r}: must be a valid Python identifier")


def _make_handler(
    tool_params: dict[str, dict[str, str]],
    response_template: str,
) -> Callable[..., Any]:
    """Dynamically create a typed handler function for a payload template.

    FastMCP does not support ``**kwargs``, so for tools with parameters we
    use ``exec()`` to build a function with explicit ``str`` parameters
    matching *tool_params* keys.  For zero-param tools a simple closure is
    returned instead.

    Args:
        tool_params: Mapping of parameter names to their schema dicts.
        response_template: Response string with ``{param}`` placeholders.

    Returns:
        A callable handler suitable for FastMCP tool registration.

    Raises:
        ValueError: If any parameter name is not a valid Python identifier.
    """
    param_names = list(tool_params.keys())

    if not param_names:

        def handler() -> str:
            return response_template

        return handler

    _validate_param_names(param_names)

    params_str = ", ".join(f"{p}: str" for p in param_names)
    locals_dict = ", ".join(f"'{p}': {p}" for p in param_names)
    code = (
        f"def handler({params_str}) -> str:\n"
        f"    kwargs = {{{locals_dict}}}\n"
        f"    return _response_template.format_map(defaultdict(str, **kwargs))\n"
    )
    namespace: dict[str, object] = {
        "_response_template": response_template,
        "defaultdict": defaultdict,
    }
    exec(code, namespace)  # noqa: S102  # nosec B102
    return cast(Callable[..., Any], namespace["handler"])


def build_server(
    templates: list[PayloadTemplate],
    name: str = "qai-inject",
) -> FastMCP:
    """Build a FastMCP server from payload templates.

    Each template is registered as a tool whose description carries the
    poisoned text and whose handler returns the templated response.

    Args:
        templates: Payload templates to register as tools.
        name: Server name for the FastMCP instance.

    Returns:
        A configured FastMCP server ready to run.
    """
    server = FastMCP(name=name)

    seen_names: set[str] = set()
    for template in templates:
        if template.tool_name in seen_names:
            raise ValueError(f"Duplicate tool name: {template.tool_name!r}")
        seen_names.add(template.tool_name)

        handler = _make_handler(template.tool_params, template.tool_response)
        server.tool(name=template.tool_name, description=template.tool_description)(handler)

    return server
