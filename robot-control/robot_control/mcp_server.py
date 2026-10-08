"""MCP adapter over the core: python -m robot_control.mcp_server --backend <robot>."""
from __future__ import annotations
import argparse
import asyncio
import importlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
import mcp.types as types
import yaml
from mcp.server import Server
from mcp.server.stdio import stdio_server
from .audit import AuditLog, InteractionLog, configure_logging, logger
from .registry import RobotRegistry, default_robot, robot_entry, robot_names
from .server import RobotServer

SERVER_NAME = "robot-control"
PROMPTS_DIR = Path(__file__).parent / "prompts"
log = logging.getLogger("robot_control.mcp")


@dataclass(frozen=True)
class PromptArg:
    name: str
    description: str = ""
    required: bool = False


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    description: str
    template: str
    arguments: list[PromptArg] = field(default_factory=list)

    def render(self, values: dict[str, str]) -> str:
        return self.template.format(**values)


def load_prompts() -> dict[str, PromptTemplate]:
    """Load every prompts/*.yaml"""
    prompts: dict[str, PromptTemplate] = {}
    for path in sorted(PROMPTS_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text())
        if not isinstance(data, dict) or "template" not in data:
            continue
        prompts[data["name"]] = PromptTemplate(
            name=data["name"],
            description=data["description"].strip(),
            template=data["template"].strip(),
            arguments=[PromptArg(**a) for a in data.get("arguments", [])],
        )
    return prompts


def build_registry(backend_name: str) -> RobotRegistry:
    """Register the backend declared as BACKEND by the module the entry point names."""
    registry = RobotRegistry()
    try:
        entry = robot_entry(backend_name)
    except KeyError as exc:
        raise SystemExit(str(exc)) from exc
    try:
        module = importlib.import_module(entry.module("backend"))
    except ImportError as exc:
        raise SystemExit(f"backend {backend_name!r} ({entry.module('backend')}) will not import: {exc}") from exc
    registry.add(module.BACKEND())
    return registry


def _exercise_modules(folder: Path) -> list[str]:
    """Importable names under a robot's exercises/, one entry per exercise."""
    if not folder.is_dir():
        return []
    names = [d.name for d in sorted(folder.iterdir())
             if d.is_dir() and not d.name.startswith((".", "_"))]
    names += [f.stem for f in sorted(folder.glob("*.py"))
              if not f.stem.startswith("_")]
    return names


def build_exercises(registry: RobotRegistry) -> dict:
    """Each registered robot's own exercises, from robots/<name>/exercises/*.py."""
    out: dict[str, dict] = {}
    for robot in registry.names():
        entry = robot_entry(robot)
        folder = entry.path() / "exercises"
        found = {}
        for name in _exercise_modules(folder):
            module = importlib.import_module(f"{entry.module('exercises')}.{name}")
            if hasattr(module, "EXERCISE"):
                ex = module.EXERCISE()
                found[ex.spec.name] = ex
        out[robot] = found
        log.info("robot %r offers %d exercise(s): %s", robot, len(found), sorted(found))
    return out


def build_mcp(rs: RobotServer, interactions: InteractionLog | None = None) -> Server:
    server = Server(SERVER_NAME)
    ai = interactions or InteractionLog()

    # tools
    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        tools = [
            types.Tool(name=t.name, description=t.description, inputSchema=t.input_schema)
            for t in rs.list_tools()
        ]
        ai.record("to_ai", "list_tools",
                  tools=[{"name": t.name, "description": t.description,
                          "inputSchema": t.inputSchema} for t in tools])
        log.info("list_tools: exposed %d tool(s) to AI: %s",
                 len(tools), [t.name for t in tools])
        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
        args = arguments or {}
        ai.record("from_ai", "call_tool", tool=name, arguments=args)
        log.info("AI → call_tool %r args=%s", name, args)
        td = next((t for t in rs.list_tools() if t.name == name), None)
        if td is None:
            result = {"status": "error", "message": f"unknown tool {name!r}"}
        elif td.kind == "set_parameters":
            result = rs.set_parameters(td.robot, args)
        elif td.kind == "action":
            result = rs.do_action(td.robot, td.action, args)
        elif td.kind == "e_stop":
            result = rs.emergency_stop(td.robot)
        elif td.kind == "resume":
            result = rs.resume(td.robot)
        elif td.kind == "start_exercise":
            result = rs.start_exercise(td.robot, td.exercise, args)
        elif td.kind == "configure_exercise":
            result = rs.configure_exercise(td.robot, td.exercise, args)
        elif td.kind == "stop_exercise":
            result = rs.stop_exercise(td.robot)
        elif td.kind == "get_state":
            result = {"status": "ok", "state": rs.get_state(td.robot)}
        else:
            result = {"status": "error", "message": f"unhandled tool kind {td.kind!r}"}
        ai.record("to_ai", "tool_result", tool=name, result=result)
        log.info("call_tool %r → status=%s", name, result.get("status"))
        return [types.TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    # resources
    def _uris(robot: str) -> tuple[str, str]:
        return f"robot://{robot}/state", f"robot://{robot}/capabilities"

    @server.list_resources()
    async def list_resources() -> list[types.Resource]:
        out: list[types.Resource] = []
        for robot in rs.registry.names():
            state_uri, caps_uri = _uris(robot)
            out.append(types.Resource(
                uri=state_uri, name=f"{robot} live state",
                description=f"Current parameters, telemetry and status of {robot}.",
                mimeType="application/json"))
            out.append(types.Resource(
                uri=caps_uri, name=f"{robot} capabilities",
                description=f"Parameters, ranges and safety limits of {robot}.",
                mimeType="application/json"))
        ai.record("to_ai", "list_resources",
                  resources=[{"uri": str(r.uri), "description": r.description} for r in out])
        log.info("list_resources: exposed %d resource(s) to AI", len(out))
        return out

    @server.read_resource()
    async def read_resource(uri) -> str:
        s = str(uri)
        ai.record("from_ai", "read_resource", uri=s)
        log.info("AI → read_resource %s", s)
        # robot://<name>/<kind>
        body = s.split("://", 1)[1]
        robot, _, kind = body.partition("/")
        if kind == "state":
            content = json.dumps(rs.get_state(robot), indent=2, default=str)
        elif kind == "capabilities":
            content = json.dumps(_spec_to_dict(rs.get_spec(robot)), indent=2, default=str)
        else:
            log.warning("AI requested unknown resource %r", s)
            raise ValueError(f"unknown resource {s!r}")
        ai.record("to_ai", "resource", uri=s, content=content)
        return content

    # prompts (therapist templates, loaded from prompts/*.yaml)
    prompts = load_prompts()

    @server.list_prompts()
    async def list_prompts() -> list[types.Prompt]:
        out = [
            types.Prompt(
                name=p.name,
                description=p.description,
                arguments=[
                    types.PromptArgument(
                        name=a.name, description=a.description, required=a.required)
                    for a in p.arguments
                ],
            )
            for p in prompts.values()
        ]
        ai.record("to_ai", "list_prompts",
                  prompts=[{"name": p.name, "description": p.description} for p in out])
        log.info("list_prompts: exposed %d prompt(s) to AI: %s",
                 len(out), [p.name for p in out])
        return out

    @server.get_prompt()
    async def get_prompt(name: str, arguments: dict | None) -> types.GetPromptResult:
        ai.record("from_ai", "get_prompt", prompt=name, arguments=arguments or {})
        log.info("AI → get_prompt %r args=%s", name, arguments or {})
        prompt = prompts.get(name)
        if prompt is None:
            log.warning("AI requested unknown prompt %r", name)
            raise ValueError(f"unknown prompt {name!r}")
        values = dict(arguments or {})
        if not values.get("robot"):
            values["robot"] = rs.registry.names()[0] if rs.registry.names() else "the robot"
        text = prompt.render(values)
        # the full prompt text handed to the AI
        ai.record("to_ai", "prompt", prompt=name, arguments=values, text=text)
        log.info("get_prompt %r → served %d chars to AI", name, len(text))
        return types.GetPromptResult(
            messages=[types.PromptMessage(
                role="user", content=types.TextContent(type="text", text=text))])

    return server


def _spec_to_dict(spec) -> dict:
    return {
        "name": spec.name,
        "description": spec.description,
        "parameters": [
            {"name": p.name, "type": p.type, "unit": p.unit,
             "min": p.minimum, "max": p.maximum, "step": p.step,
             "max_delta": p.max_delta, "default": p.default,
             "description": p.description}
            for p in spec.parameters
        ],
        "actions": [{"name": a.name, "description": a.description} for a in spec.actions],
        **({"max_force": spec.max_force} if spec.max_force is not None else {}),
    }


async def _run_stdio(server: Server) -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    ap = argparse.ArgumentParser(description="Robot-control MCP server")
    ap.add_argument("--backend", default=default_robot(), choices=robot_names(),
                    help="robot to control; discovered via the robot_control.robots entry points")
    ap.add_argument("--transport", default="stdio", choices=["stdio"])
    ap.add_argument("--no-window", action="store_true",
                    help="do not open the exercise window when an exercise starts")
    args = ap.parse_args()

    configure_logging()
    registry = build_registry(args.backend)
    rs = RobotServer(registry, AuditLog(), exercises=build_exercises(registry),
                     open_window=not args.no_window)
    try:
        rs.start()
    except RuntimeError as e:
        logger.error("cannot start backend %r: %s", args.backend, e)
        raise SystemExit(1)
    logger.info("robot-control MCP server up: backend=%s robots=%s",
                args.backend, registry.names())
    interactions = InteractionLog()
    interactions.record("to_ai", "server_up", backend=args.backend,
                        robots=registry.names())
    server = build_mcp(rs, interactions)
    try:
        asyncio.run(_run_stdio(server))
    finally:
        rs.stop()


if __name__ == "__main__":
    main()
