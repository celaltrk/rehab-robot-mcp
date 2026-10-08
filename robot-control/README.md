# LLM ↔ Robot Integration via MCP

Driving robotic devices from a multimodal **LLM interface** through the
**Model Context Protocol (MCP)**, for the EU **TAILOR** neuro-rehabilitation
project. A therapist speaks or types an intent; an LLM turns it into a validated
tool call; a robot-agnostic server enforces safety and dispatches it to a device.

The design goal is **robot-agnostic scalability**: the model side and the robot
side never depend on each other, and adding a device is a plug-in, not a rewrite.
This repository is the core. It holds no robot, and names none.

---

> **Three parts.** This one holds the robot-agnostic core — specs, the safety
> validator, the exercise runner and the MCP server. It contains no robot. Each
> device is a separate installable package, and the model side is a third.
> Install this one first; the others depend on it.
>
> ```bash
> pip install -e .                   # robot-control  (here)
> pip install -e ../falcon           # one package per robot
> ```
>
> The model side, `adjustsettingsdemo`, is the lab's own demonstrator extended
> for this project. It is not published here — see the root README.

## What it does

- **Robot-agnostic control server** — one declarative `spec.yaml` per robot
  generates the LLM tool surface, the safety-validator rules and the capabilities
  resource. The server, the protocol and other robots never change when a device
  is added.
- **MCP boundary** — the LLM (a desktop client, or a model hosted locally)
  discovers and calls typed tools over MCP. The server **never imports a model**;
  the model side **never imports ROS 2**.
- **Safety by construction** — every command passes a deterministic validator
  server-side before it can reach hardware, with a latching `e_stop`, a
  deliberate `resume` and a full audit log.
- **Structured commands beyond scalars** — a robot may accept input no numeric
  range can express: a scene of objects to render, a path drawn from points. An
  exercise declares it as `extra_schema`, the core carries it through, and the
  device clamps it. The core never learns what the payload means.
- **Exercises over MCP** — an exercise declares its settings the way a robot
  declares parameters, so they reach the model as validated tools. The core ticks
  it at 30 Hz and reports progress through `get_state`. Exercises are owned by
  robots: what is offered, and within what limits, is the device's business.
- **Viewers belong to the robot** — an exercise names a program in its YAML and
  the server runs it when the exercise starts. The core never learns what that
  program is, so a device may show an exercise however suits it.
- **Observable state** — `get_state` reports the commanded parameters, whatever
  the device senses, and its status flags, so the model can tell a stopped device
  from a faulted one without knowing what kind of machine it is.

---

## Architecture

Two hard walls are the whole design in one line:

- **The MCP server never imports a model** — no LLM, no prompt logic in the
  trusted core.
- **The model side never imports ROS 2** — no device or physics knowledge in the
  host.

Each side is therefore independently replaceable.

```
┌────────────────────────────────────────────────────────────────────────────┐
│ (A) MULTIMODAL INPUT                  [upstream, orthogonal — not built here]│
│      voice → STT · gesture · text  →  normalized therapist utterance         │
└──────────────────────────────┬─────────────────────────────────────────────┘
                               ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ (B) LLM HOST · MCP CLIENT             "knows the model, not the robot"       │
│      interpret → read state/capabilities → choose tool → act → reply         │
│      a tool-calling desktop client, or a locally hosted model               │
└──────────────────────────────┬─────────────────────────────────────────────┘
                               │   MCP (stdio today · HTTP+SSE later)
                               ▼            ◄── the decoupling boundary ──►
┌────────────────────────────────────────────────────────────────────────────┐
│ (C) robot_control — ROBOT-AGNOSTIC SERVER  "knows the protocol + safety"     │
│                                                                             │
│   mcp_server.py ── tools · resources · prompt  (generated from each spec)    │
│        ▼                                                                    │
│   server.py (RobotServer) ── validate → dispatch → audit → run exercises     │
│        │        ▲  validator.py : bounds · type · rate-cap (all-or-nothing)  │
│        ▼        │  audit.py     : logs/audit.jsonl                           │
│   registry.py ──►  RobotBackend        (the ONLY device-aware layer)         │
└──────────┬─────────────────────────────────────────────────────────────────┘
           │  backend contract: get_state · apply_parameters · do_action ·
           │                    emergency_stop · resume · start/stop
           ▼
 <Name>Backend                        one per device, in its own repository
      │ however that device is spoken to — ROS 2 topics, a socket, a driver
      ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ (D) ROBOT / HARDWARE                  "knows the physics, not the LLM"     │
│   The robot's own process owns the device and closes its control loop:     │
│     • renders whatever scene the exercise asked for, if it can             │
│     • clamps to the device's rated limits, drives the actuators            │
│   Nothing above this line knows what any of that means.                    │
└────────────────────────────────────────────────────────────────────────────┘
```

| Layer | Responsibility | Knows | Does **not** know |
|---|---|---|---|
| **A. Input** | voice/gesture/text → utterance | sensors, ASR | robots, MCP |
| **B. LLM host** | interpret intent, run the agentic loop, call tools | the model, MCP | ROS 2, device physics, which robot |
| **C. Server** (`robot_control/`) | expose tools, **validate**, dispatch, audit, run exercises | the protocol, safety rules | the model, prompt wording |
| **D. Robot** (`<robot-repo>/<module>/`) | execute validated commands, report state | device physics, control law | the LLM, natural language |

### What the model sees

- **Tools**, generated per robot from its spec: `set_parameters`, the declared
  actions declared by the robot, `get_state`, `e_stop`,
  `resume`, plus `start_<exercise>` / `configure_<exercise>` / `stop_exercise`.
  With several robots registered the names are namespaced (`<name>_…`).
- **Resources**: `robot://<name>/state` and `robot://<name>/capabilities`. State
  is *also* a tool, because resources are application-controlled and a model in an
  agentic loop cannot read one unprompted.
- **Prompt**: `operate_robot`, a therapist-facing template.

Model-facing wording lives in YAML, not Python: `robot_control/prompts/` holds
robot policy — safety rules, what `e_stop` latches — and serves it over MCP to
any client that asks for it, rendered for whichever robot is connected.

### Command flow

```
Therapist   LLM host (B)          RobotServer (C)          Backend        Robot (D)
  │ "firmer      │                     │                      │              │
  │  guidance" ──►                     │                      │              │
  │              │ get_state ─────────────────────────────────► read         │
  │              │ set_parameters {<param>: <value>}           │              │
  │              │ ──────────────────► VALIDATE bounds/type/Δ  │              │
  │              │                     ok → apply_parameters ──► command
  │              │ ◄── {applied|rejected, state} ◄── audit ◄────  ◄ measurement
```

The validator sits **server-side on the command path**: an unsafe value is
rejected before any backend runs, and a robot is expected to clamp again in its
own process. An exercise is just another client of that validator — it issues
setpoints through the same checks.

---

## Safety

Defence in depth spans both repositories. **In this one**, and so true of every
robot:

| Concern | Where | What it does |
|---|---|---|
| **Validation** | `validator.py`, rules from each spec | bounds · type · rate cap, all-or-nothing |
| **Rate cap** | `max_delta` per parameter | bounds the gap between a command and the device's *measured* value — the one guarantee a JSON schema cannot express |
| **Emergency stop** | `e_stop` / `resume` | zeroes force, clears the scene, **latches**; resume is deliberate and leaves the device passive |
| **Audit** | `logs/audit.jsonl` | every command and verdict, for traceability |

**In the robot**, because only the device knows its own limits. A robot is
expected to enforce, in its own process and below anything Python can bypass:
a ceiling on whatever it actuates, at the device's rated maxima; a deadman, if
the device can move on its own; and single-controller arbitration, so a second
client refuses to start rather than issue conflicting commands. Each robot's
repository documents what it does and where.

Whatever a robot clamps or disables on its own, it should report in
`status`, so the layer is visible rather than silent.

---

## Repository layout

Three repositories, each installable, each with one job.

```
robot-control/                  THIS ONE — knows no robot
├── robot_control/
│   ├── spec.py                 RobotSpec / ParamDef / ActionDef, loaded from YAML
│   ├── validator.py            bounds · type · rate cap, all-or-nothing
│   ├── backend.py              the RobotBackend contract
│   ├── registry.py             finds installed robots; hosts live backends
│   ├── server.py               validate → dispatch → audit; generates the tools
│   ├── audit.py                JSONL trail of every command and verdict
│   ├── mcp_server.py           the MCP adapter; imports no model
│   ├── exercise.py             the Exercise contract; no exercise implements it here
│   └── prompts/                model-facing wording as data
└── tests/                      53 tests: no robot, no hardware, no model

<robot-repo>/                   ONE DEVICE — extends this repo; one repo per robot
├── <name>_robot/               spec.yaml  backend.py
│                               exercises/<exercise>/   its own, mechanics included
│                               whatever driver, workspace or launcher it needs
└── tests/                      its own; the device-bound ones skip themselves

adjustsettingsdemo/             THE MODEL SIDE — imports no ROS, no backend
                                not published here: it extends the lab's own
                                demonstrator. Speaks MCP over stdio and has no
                                other route to a device.
```

**How the repositories find each other.** Each robot is its own distribution and
advertises itself through the `robot_control.robots` entry-point group:

```toml
[project.entry-points."robot_control.robots"]
<name> = "<name>_robot"
```

`registry.known_robots()` reads those entry points, so this repository needs no
list of robots and no path to their checkouts — **installing a robot package is
what makes that robot discoverable**. Reading the list imports none of their
code, so specs, bounds and axes are readable on a machine with none of a robot's
dependencies present.

Each robot names its own top-level package, so two robots ship separately and
install side by side without either knowing about the other. Adding a robot is a
new repository plus one entry point; nothing here changes and no existing robot
is touched.

**Name the module after the distribution, not after the repository directory**
— `<name>-robot` becomes `<name>_robot`, exactly as `robot-control` becomes
`robot_control`. A package that shares a name with the directory containing it
is shadowed by that directory whenever the parent is on the import path, which
is what happens the moment anyone runs Python from the folder holding the
checkouts. The failure is silent: the import succeeds, resolves to the
repository root as an implicit namespace package, and `spec.yaml` is simply not
found.

## Prerequisites

- **Ubuntu 24.04** with **ROS 2 Jazzy** (or 22.04 / Humble — substitute the distro name).
- Python 3.12.
- Whatever hardware, SDKs and drivers a robot needs are that robot's own; its
  repository states them. Nothing here needs a device.

---

## Setup from scratch

These three steps set up the Python side, which is all this repository has. A
device needs more — an SDK, a driver, a ROS 2 workspace — and all of it belongs
to that robot's repository, not here.

### 1. Clone

Robots sit beside the core, not inside it:

```bash
git clone https://github.com/celaltrk/rehab-robot-mcp.git
cd rehab-robot-mcp          # robot-control/ and falcon/ sit side by side
```

### 2. ROS 2 Jazzy

Install `ros-jazzy-desktop` and `ros-dev-tools` per the
[official guide](https://docs.ros.org/en/jazzy/Installation.html), then always
source it before running anything here:

```bash
source /opt/ros/jazzy/setup.bash
```

### 3. Python virtualenv + MCP SDK

`rclpy` comes from the system ROS install, so the venv is created with
`--system-site-packages` and only adds the `mcp` SDK:

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install mcp        # if pip is missing, bootstrap with get-pip.py
```

At this point the whole test suite runs — `.venv/bin/python -m pytest -q` —
because nothing here needs a robot.

### 4. The robot's own device setup

Whatever a device needs — an SDK, a driver, device permissions, a compiled workspace —
is documented by that device, in its own repository. Then:

```bash
pip install -e .                 # this repo, first
pip install -e ../<robot-repo>   # the robot; installing it is the registration
```

---

## Running

### Connect an LLM

The server speaks MCP over stdio, so any MCP client can drive it. Register it
once and the client launches it.

#### Claude Desktop

Edit `~/.config/Claude/claude_desktop_config.json` (create it if absent) and add
the servers under `mcpServers`, keeping any other keys in the file. Substitute
the repository's own absolute path for `<REPO>` — `pwd` at the repository root
prints it:

```json
{
  "mcpServers": {
    "robot-<name>": {
      "command": "<ROBOT-REPO>/<name>_robot/scripts/run_mcp.sh",
      "args": []
    }
  }
}
```

Notes that save time:

- **Use an absolute path.** The config is not read relative to the project, and
  `~` is not expanded.
- **Point at `run_mcp.sh`, not at Python.** Claude Desktop spawns servers with a
  sanitised environment and drops `LD_LIBRARY_PATH`, so a direct
  `python -m robot_control.mcp_server` fails on `import rclpy` and shows only
  *"Connection closed"*. The launcher sources ROS 2 first.
- **Restart Claude Desktop after any change** to the config, a robot spec, an
  exercise or a tool schema — all are read once at startup. A stale client is the
  usual reason a change appears to have no effect, and the exercise window says
  *"robot is not publishing the exercise"* when the server predates it.
- **Start the robot's own device process first**, if it has one. A robot that
  owns hardware usually runs it separately, with the MCP server talking to it
  rather than opening the device itself.
- **One controller at a time,** where the backend enforces it: a robot that
  takes a lock makes a second client refuse to start rather than issue
  conflicting commands.

Verify by asking *"what can you control on this robot?"*; it should list the
parameters and their ranges without touching the hardware.

#### Claude Code

```bash
claude mcp add robot-<name> -- <ROBOT-REPO>/<name>_robot/scripts/run_mcp.sh
```

Tools appear as `mcp__robot-<name>__set_parameters`, `…__get_state`,
`…__start_<exercise>`, `…__e_stop`.

#### A locally hosted model

The lab's parameter-adjustment demonstrator is the intended LLM host and lives
in its own repository. Upstream it adjusts imaginary walking parameters held in
a dictionary; there, the parameters, their ranges and their rate caps are read
from a robot's spec over MCP instead. Inference stays on the machine, which is
the requirement a hosted desktop client does not meet.

Installing it, choosing a model and pointing it at a server are that
repository's business — including which model, on what hardware, at which
address. See its README.

Two things about it are this server's business, though:

**Validation runs twice by design** — the host's own rules client-side, then
this server's, which is the one that decides. A host can accept a value this
server then refuses, most often on the rate cap, because the cap is judged
against the state the device actually reports rather than against the last
command. Asking again moves another step.

**What it cannot do.** The demonstrator's protocol is one JSON object of
parameter values, which expresses exactly one tool — `set_parameters`. `e_stop`,
the exercises and any structured action are out of reach from it; those need a
tool-calling client such as Claude Desktop or Claude Code, above.

### The robot itself

Bringing a device up, and anything that must be rebuilt when its driver changes,
is documented by the robot, in its own repository.

### The exercise window

A window belongs to the robot whose exercise it draws, so this repo ships none.
An exercise names one in its YAML — `viewer: <path>`, relative to the robot's
package — and the server runs that program when the exercise starts:

```yaml
# in the exercise's own YAML, in the robot's repository
viewer: scripts/run_exercise.sh
```

It is an ordinary program the robot ships — a 2D window, a 3D scene viewer,
anything. The core never learns what it is, which is what lets a device show an
exercise however suits it.

---

## Driving it with natural language

What a therapist can say depends entirely on what the robot declared; the core
supplies the shape, never the vocabulary.

- *"A bit gentler."* → `set_parameters` on whichever parameter the spec exposes,
  refused by the validator if the step is too large for where the device is
- *"Start the exercise, ten repetitions."* → `start_<exercise>`, its settings
  validated from the exercise's own YAML
- *"That is too small."* → `configure_<exercise>` while it runs
- *"How am I doing?"* → `get_state`, reporting whatever the exercise scores
- *"Stop!"* → `e_stop` (latches; `resume` re-enables)

Values outside a robot's envelope come back rejected with a reason, including the
ones a schema cannot catch — a target more than 2 cm from where the hand actually
is, for instance.

---

## Extending it

### A new robot

Adding a robot is **writing a spec and a backend under `<robot-repo>/<module>/`** —
no file in `robot_control/` changes, and no existing robot is touched. The tools,
safety rules, resources and audit all regenerate from the spec. Name the
distribution `<robot>-robot` and the module `<robot>_robot`, so the module never
matches the directory above it. Worked example: an `anyexo` robot.

**1. Declare the spec — `anyexo/anyexo_robot/spec.yaml`.** *What* can be
controlled, and its hard safety envelope. This one file generates the MCP tools,
the validator rules and the `capabilities` resource.

```yaml
name: anyexo
description: a shoulder-elbow rehabilitation exoskeleton
parameters:
  - name: assist_level
    type: float
    description: How much the exo assists the patient's motion
    unit: fraction
    minimum: 0.0
    maximum: 1.0
    step: 0.05
    max_delta: 0.25
    default: 0.3
  - name: rom_limit
    type: float
    description: Allowed range-of-motion cap for the elbow
    unit: deg
    minimum: 0.0
    maximum: 140.0
    step: 5.0
    max_delta: 20.0
    default: 90.0
actions:                    # optional
  - name: start
    description: Begin the assisted exercise
  - name: stop
    description: Stop and hold position
  # structured action? give it input_schema: <json-schema> instead of params
```

Load it with a two-line `spec.py`:

```python
from pathlib import Path
from robot_control.spec import load_spec

ANYEXO_SPEC = load_spec(Path(__file__).parent / "spec.yaml")
```

`load_spec` rejects unknown keys, so a typo like `maximumm` fails loudly at
startup rather than silently dropping a safety limit. The fields:

- **`minimum`/`maximum`** are the *hard* bounds — anything outside is rejected
  before it reaches hardware, and emitted in the JSON schema so the model sees them.
- **`max_delta`** is the largest gap allowed between a **commanded** value and the
  device's **actual** value as reported by `get_state` — the guarantee a static JSON
  schema cannot express. It is *not* measured against the previous command. Where a
  parameter has a measurable physical counterpart, override
  `RobotBackend.actual_parameters` to return the measurement; the default returns
  the commanded values, correct for gains and simulated backends. A consequence to
  design around: a target can never be commanded further than `max_delta` from where
  the device is now, so a large move legitimately takes several commands.
- **`step`** is advisory granularity shown to the model.
- **`ActionDef.input_schema`** (a raw JSON Schema) is for a *structured* action whose
  argument is not scalar — a scene of objects, a path of points — validated and
  clamped inside the backend.

**2. Write the backend — `anyexo/anyexo_robot/backend.py`.** *How* to move the
device. This is the **only** module that imports `rclpy` / touches hardware.

```python
import rclpy
from rclpy.node import Node
from robot_control.backend import RobotBackend

from .spec import ANYEXO_SPEC

class AnyexoBackend(RobotBackend):
    @property
    def spec(self): return ANYEXO_SPEC

    def start(self):   ...   # rclpy.init(), create node/pubs/subs, spin thread
    def stop(self):    ...   # publish safe values, tear down cleanly

    def get_state(self) -> dict:
        # {"parameters": {...}, "telemetry": {...},
        #  "status": {"estopped": bool, ...}, "exercise": {"running": bool}}
        ...
    def apply_parameters(self, params: dict) -> dict:   # already validated
        ...                                             # return {"error": ...} to refuse
    def do_action(self, name, args) -> dict:            # only if the spec has actions
        ...
    def emergency_stop(self) -> dict:  ...   # make safe immediately; must latch
    def resume(self) -> dict:          ...   # clear the latch; stay passive

BACKEND = AnyexoBackend
```

Contract notes:
- `apply_parameters` receives **already-validated** scalar parameters; refuse a
  command by returning a dict with an `"error"` key, which the server surfaces as
  `status: "error"`.
- `get_state()` must follow the `parameters`/`telemetry`/`status` shape so the
  generic resources stay robot-agnostic; a device that clamps or disables its own
  output should say so under `status`, so the limit is visible rather than silent.
- `emergency_stop` must always succeed and **latch**; `resume` clears it and leaves
  the device passive (no motion until re-commanded).

**3. Register it with one entry point** — installing the distribution is the
registration:

```toml
# the robot's own pyproject.toml
[project.entry-points."robot_control.robots"]
anyexo = "anyexo_robot"
```

```bash
pip install -e ../anyexo
```

`known_robots()` reads the entry-point group and imports the named module on
demand, so a robot's dependencies are only imported on its own path. Registering
more than one backend hosts **several robots in one server**; tool names
auto-prefix per robot (`anyexo_set_parameters`).

**4. Optionally ship a viewer** and name it from an exercise's `viewer:` field, if
the device is worth watching.

Tools, validation, resources and the audit log regenerate from the spec, and no
file in `robot_control/` is edited.

### A new exercise

Exercises belong to a robot, in that robot's own repository — mechanics included.
This repo holds only the contract, in `robot_control/exercise.py`. Declare
``<robot-repo>/<module>/exercises/<name>/<name>.yaml`` with the bounds *that device*
allows, and a module beside it implementing `Exercise` and exposing `EXERCISE`:

```python
from robot_control.exercise import Exercise, ExerciseSpec, load_exercise_spec

class ReachExercise(Exercise):
    ...                     # configure / reset / update / status / view

SPEC = load_exercise_spec(Path(__file__).parent / "reach.yaml")

def EXERCISE() -> ReachExercise:
    return ReachExercise(SPEC)
```

The server picks it up, generates its tools from the YAML, ticks it at 30 Hz and
publishes its geometry to whatever draws the robot. Offering the same exercise on
a second device is a YAML with different numbers.

---

## Tests

```bash
python -m pytest -q                      # 53 tests: no robot, no hardware, no model
```

The unit tests deliberately need no ROS, no hardware and no LLM: the safety
claims must be checkable by a reader on any machine, in well under a second. Each
test file defends one claim:

| File | Covers | Claim it defends |
|---|---|---|
| `tests/test_validator.py` | bounds, type coercion, the `max_delta` rate cap, all-or-nothing, rejection messages | a deterministic validator rejects unsafe commands before they reach a backend, independently of model quality |
| `tests/test_server_safety.py` | e-stop latching, refusal while latched, deliberate resume, audit-trail completeness | e-stop latches and recovery is deliberate; every command and verdict is recorded |

`tests/fake_robot.py` is not a test but the in-memory stand-in backend the others
drive, so the guarantees can be exercised without hardware. Tests that name a
robot — spec-to-schema generation, scene clamping, exercise ownership, the
end-to-end MCP self-test — live with that robot, because they are assertions about
that device.

---

## Status and next steps

**Working:** robot-agnostic core with validator and audit; a first device
driven end to end on real hardware from its own repository; e-stop and resume;
exercises configured over MCP and drawn by viewers the robot ships; Claude
Desktop and Claude Code as clients; and a **local model** — the lab
demonstrator adjusting a real robot's parameters against a model hosted on the
same machine.

**Next:**

1. **A larger model, on a bigger host** — moving from a small local model to a
   larger one served elsewhere is a client-side change with *zero* server
   changes, which is the model-agnostic claim demonstrated rather than asserted.
   Tool-calling reliability in small models held up: the calls were valid, and
   the failures were prompt construction and token budget, not schema
   conformance.
2. **HTTP+SSE transport** and auth for a distributed interface-PC ↔ robot-PC lab
   setup.
3. **Metrics** — exercises report their own progress, and a device may estimate
   the user's own effort; where it does, that estimate is uncalibrated and needs
   validating against a known load before it can be used.
4. **Streaming telemetry** instead of request/response state.
5. **A second real device** (ANYexo), which is what actually proves the
   robot-agnostic claim: with one robot installed, the claim is asserted rather
   than tested. The step-by-step path is in the first robot's repository README.
6. **Workspace-relative exercise geometry.** Each robot now owns its exercise
   YAML, so the bounds are per device — but they are still
   absolute metres rather than a fraction of a declared workspace, so a new
   robot must state its own numbers rather than inheriting sensible ones.
