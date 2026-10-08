"""End-to-end MCP self-test over a real stdio session. No LLM involved.

Usage (from the repo root, with the Falcon node running):
    .venv/bin/python tests/selftest_mcp.py
"""
from __future__ import annotations
import argparse
import asyncio
import json
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Via run_mcp.sh: a directly invoked backend dies on `import rclpy`.
_LAUNCHER = (Path(__file__).resolve().parent.parent
             / "falcon_robot" / "scripts" / "run_mcp.sh")


def _dump(label, obj):
    print(f"\n=== {label} ===")
    print(obj if isinstance(obj, str) else json.dumps(obj, indent=2, default=str))


def _pick_probe(caps: dict, current: dict) -> tuple[str, float, float, float | None]:
    """Choose a parameter and three probe values from the robot's own capabilities."""
    params = caps.get("parameters", [])
    # Prefer a bounded parameter that also has a rate cap, so all three probes work.
    ranked = sorted(
        params,
        key=lambda p: (p.get("max_delta") is None,
                       p.get("min") is None or p.get("max") is None),
    )
    meta = ranked[0]
    name = meta["name"]
    lo, hi = meta.get("min", 0.0), meta.get("max", 1.0)
    cur = float(current.get(name, lo + 0.5 * (hi - lo)))
    md = meta.get("max_delta")

    # A small, legal step away from wherever the parameter currently sits.
    delta = 0.4 * md if md is not None else 0.25 * (hi - lo)
    valid = cur + delta if cur + delta <= hi else cur - delta
    valid = min(hi, max(lo, valid))
    # Snap onto the step grid, which the schema enforces as multipleOf.
    if (grid := meta.get("step")):
        valid = round(round(valid / grid) * grid, 6)
        valid = min(hi, max(lo, valid))
    valid = round(valid, 6)

    out_of_bounds = hi + max(1.0, abs(hi - lo))

    # In range but beyond the rate cap: the case a schema cannot express.
    over_delta = None
    if md is not None:
        far = hi if abs(hi - cur) > abs(cur - lo) else lo
        if abs(far - cur) > md:
            over_delta = round(far, 6)
    return name, valid, out_of_bounds, over_delta


async def _await_telemetry(session, timeout: float = 5.0) -> bool:
    """Wait for the first device state; ROS 2 discovery takes a few hundred ms."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        res = await session.call_tool("get_state", {})
        state = json.loads(res.content[0].text).get("state", {})
        status = state.get("status", {})
        if status.get("connected") is not False:
            return True
        await asyncio.sleep(0.25)
    return False


async def run() -> None:
    params = StdioServerParameters(command=str(_LAUNCHER))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            _dump("tools", [t.name for t in tools.tools])

            resources = await session.list_resources()
            _dump("resources", [str(r.uri) for r in resources.resources])

            caps_uri = next(str(r.uri) for r in resources.resources if "capabilities" in str(r.uri))
            caps_text = (await session.read_resource(caps_uri)).contents[0].text
            _dump("capabilities", caps_text)
            caps = json.loads(caps_text)

            # Resources are application-controlled, so state also needs a tool.
            assert "get_state" in {t.name for t in tools.tools}, \
                "get_state tool missing — the agentic loop cannot observe the robot"

            connected = await _await_telemetry(session)
            _dump("telemetry connected", str(connected))

            state = await session.call_tool("get_state", {})
            _dump("get_state (tool)", state.content[0].text)
            current = json.loads(state.content[0].text)["state"].get("parameters", {})

            name, valid, oob, over_delta = _pick_probe(caps, current)
            _dump("probe parameter chosen from capabilities",
                  f"{name}: valid={valid}, out_of_bounds={oob}, over_max_delta={over_delta}")

            # 1) valid change
            r1 = await session.call_tool("set_parameters", {name: valid})
            _dump(f"set_parameters {name}={valid} (valid)", r1.content[0].text)

            # 2) out-of-bounds -> rejected (usually by the client's schema check)
            try:
                r2 = await session.call_tool("set_parameters", {name: oob})
                _dump(f"set_parameters {name}={oob} (out of bounds -> reject)", r2.content[0].text)
            except Exception as e:
                _dump(f"set_parameters {name}={oob} (out of bounds)",
                      f"rejected by the MCP schema before reaching the server: {e}")

            # 3) inside range but over max_delta -> rejected by the validator
            if over_delta is not None:
                r3 = await session.call_tool("set_parameters", {name: over_delta})
                _dump(f"set_parameters {name}={over_delta} (exceeds max_delta -> reject)",
                      r3.content[0].text)
            else:
                _dump("max_delta probe", f"(skipped: no reachable rate-cap violation for {name})")

            # 4) a declared action, if this robot has one
            reserved = {"set_parameters", "get_state", "e_stop", "resume"}
            # An action with no required arguments, so this stays robot-generic.
            action_tools = [t for t in tools.tools
                            if t.name not in reserved
                            and not (t.inputSchema or {}).get("required")]
            if action_tools:
                act = action_tools[0].name
                r4 = await session.call_tool(act, {})
                _dump(f"action {act}", r4.content[0].text)
            else:
                _dump("action", "(this robot declares no argument-free actions)")

            # 5) emergency stop
            r5 = await session.call_tool("e_stop", {})
            _dump("e_stop", r5.content[0].text)

            # 6) set while e-stopped -> must be refused
            r6 = await session.call_tool("set_parameters", {name: valid})
            _dump("set while e-stopped (-> status error)", r6.content[0].text)

            # 7) resume -> clears the latch
            r7 = await session.call_tool("resume", {})
            _dump("resume", r7.content[0].text)

            # 8) set after resume -> applies again
            r8 = await session.call_tool("set_parameters", {name: valid})
            _dump("set after resume (-> applied)", r8.content[0].text)

            # Final state through both surfaces; both must work.
            state_uri = next(str(r.uri) for r in resources.resources
                             if str(r.uri).endswith("/state"))
            st = await session.read_resource(state_uri)
            _dump("final state (resource)", st.contents[0].text)

            st_tool = await session.call_tool("get_state", {})
            _dump("final state (tool)", st_tool.content[0].text)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.parse_args()
    asyncio.run(run())


if __name__ == "__main__":
    main()
