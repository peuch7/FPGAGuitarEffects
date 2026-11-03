# `scripts/fx.py` reference

`fx.py` manages which effects are in the bitstream. Everything that depends on the
effect list is **generated** from three inputs, so changing the list is one command
rather than edits across RTL, C, Tcl and docs.

```
effects/registry.json          ids, power-on preset ─┐
effects/<name>/effect.json     one spec per effect  ─┼─► fx.py gen ─► gen/rtl/effect_bank.sv
presets/<name>.json            named chains         ─┘                gen/rtl/router_patch.svh
                                                                      gen/rtl/<name>_top.sv (when generated)
                                                                      gen/sw/router_map.h
                                                                      gen/sw/fx_table.{c,h}
                                                                      gen/vivado/sources.tcl
                                                                      gen/sim/sources.f
                                                                      README.md (effects table)
```

It needs Python 3.8 or newer and nothing else. `check` also uses Verilator when it is
installed. On Windows, run it as `py scripts\fx.py ...` if `python3` isn't on the
PATH.

## Commands

| command | does |
|---|---|
| `fx.py list [-v]` | effects with ids, top modules and status notes; `-v` adds knobs. Then retired ids, the power-on preset and the presets. |
| `fx.py add SPEC.json [--root DIR] [--name N]` | add an effect described by a spec. Sources are relative to the spec, or to `--root`. |
| `fx.py add SPEC.json RTL.sv ...` | the same, with the sources given on the command line instead of in the spec |
| `fx.py add RTL.sv ... [--module M] [--name N]` | add from bare RTL: a starting spec is derived from the ports and printed |
| `fx.py add effects/<name>/effect.json` | re-register an effect after editing its files or spec; it keeps its id |
| `fx.py remove NAME [--delete]` | take an effect out of the build. Its id is retired. `--delete` also removes `effects/NAME/`. Refused while a preset uses it. |
| `fx.py power-on PRESET` | the preset the bitstream starts with |
| `fx.py gen [--check]` | regenerate everything; `--check` only reports what is stale (exit 1 if anything is) |
| `fx.py check [-v] [--no-sim] [--keep DIR]` | validate, check `gen/` is fresh, lint with Verilator, smoke-test every effect in the router |

`add`, `remove` and `power-on` finish by running `gen`. Every command exits 0 on
success and 1 with an `error:` line otherwise.

**`add` is all or nothing:**
1. It checks the spec and the RTL where they are.
2. It copies the sources (and every header they `` `include ``, found next to them)
   into `effects/<name>/rtl/`.
3. It writes the normalized spec and registers the id.
4. It validates the whole project with the new effect in it.

If any step fails (a module name another effect already uses, say), `effects/<name>/`
and the registry are restored exactly as they were.

## The spec: `effects/<name>/effect.json`

```jsonc
{
  "name": "delay",                       // required: lower-case identifier, matches the folder
  "description": "Fixed delay line...",  // CLI `fx`, README table
  "owner": "",                           // optional, free text
  "status": "",                          // optional: a known issue, shown in `fx.py list` and the README
  "top": "delay_top",                    // EITHER a contract wrapper among the sources
  "module": "delay",                     // OR the core module: fx.py generates gen/rtl/<name>_top.sv
  "ports": {"sample_in": "x"},           // with "module": core port names that differ from the contract
  "sources": ["rtl/delay.sv", "rtl/delay_top.sv"],   // required: .sv .v .svh .vh, relative to the spec
  "params": [ { ...knob... }, ... ]      // up to 8
}
```

Any other key is refused, so a typo can't be silently ignored. `fx.py add` rewrites
the spec in normalized form: `sources` become `rtl/<file>`, and included headers are
added.

### A knob

```jsonc
{
  "name": "feedback",     // required: lower-case identifier, unique in the effect
  "index": 1,             // required: slot on the knob bus, 0..7, unique
  "scale": "q16",         // "q16" (0..65535 = 0..1.0) or "raw" (default)
  "min": 0, "max": 65535, // raw bounds, integers; default 0 and 65535
  "factor": 1.0,          // natural = raw (or raw/65535 for q16) * factor + offset
  "offset": 0.0,
  "default": 0.4,         // any value the knob accepts (see below); default 0
  "unit": "",             // shown by the CLI
  "doc": "...",           // shown by the CLI
  "port": "fb"            // with "module": the core port carrying this knob (default: the name)
}
```

Every field is type-checked: `index`, `min` and `max` must be integers, `factor` and
`offset` numbers, and so on.

### The effect contract

A `top` module must have exactly these ports, as `router/rtl/effect_contract.svh`
describes:

```
input  clk, rst_n, sample_in[23:0] (signed), sample_in_valid, params[127:0]
output sample_out[23:0] (signed), sample_out_valid
```

Knob *i* is `params[16*i +: 16]`, the `` `PARAM(i) `` macro.

### Generated wrappers (`"module"`)

With `"module"`, fx.py writes `gen/rtl/<name>_top.sv`:
- **Contract ports** connect to the core's ports, renamed through `"ports"`. If the
  core has no reset, set `"rst_n": null`.
- **Each knob** connects to its `port`.
- **A knob port narrower than 16 bits** gets:
  - the **top** bits of a `q16` knob, so the fraction spans the port's range;
  - the **low** bits of a `raw` knob, whose `max` must then fit the port.

**Validation** (on `add` and `gen`):
- every port the spec names exists, with the right direction;
- samples are 24 bits;
- knob ports are at most 16 bits;
- every input of the core is accounted for.

Port lists must be ANSI-style (declared in the module header), which is what every
effect here uses.

## Knob values

One grammar is used everywhere: in specs (`default`), in presets, and on the CLI
(`sw/fx_cli/knob.c` implements it in C, and the tests check the two agree):

| text | kind | meaning |
|---|---|---|
| `0.5`, `.5`, `5.`, `1e-3`, `-0.25` | natural | `(value − offset) / factor`, × 65535 if q16, rounded half to even |
| `50%`, `12.5%` | natural | the number / 100, then as above |
| `32768`, `+12`, `010` | raw | the 16-bit field, in decimal |
| `0x8000` | raw | the 16-bit field, in hex |
| anything else | — | refused: `0b101`, `1_000`, `0x1.8p1`, `inf`, `nan`, `1.5e`, `50%%` |

In JSON, a number with a fraction or exponent (`0.5`, `4.0`) is natural, and an
integer (`4`) is raw, exactly as if written as text. That is why overdrive's `drive`
default is `4.0` (4x) and not `4` (4/256x).

The result must lie in the knob's raw `min`..`max`.

## Presets: `presets/<name>.json`

```json
{
  "name": "pedalboard",
  "description": "Overdrive (8x) into delay into reverb.",
  "stages": [
    {"effect": "overdrive", "params": {"drive": 2048, "tone": 0.55, "level": 0.7}},
    {"effect": "delay", "params": {"delay_samples": 14400, "feedback": 0.35, "mix": 0.3}}
  ]
}
```

- `name` must match the file name.
- At most 7 stages, each effect at most once, only effects in the build.
- Knobs not listed take their defaults.

Presets are compiled into the CLI (`preset <name>`). One of them is the
**power-on preset** (`fx.py power-on <name>`). It is written into the patch RAM's
initial contents, so the board starts on it; changing it needs a bitstream rebuild.

## Ids

The id is what the ARM writes into patch memory to select an effect, so ids are
stable. `effects/registry.json` holds them:

```json
{
  "power_on": "clean",
  "effects": {"overdrive": 1, "distortion": 2, "delay": 3, "echo": 4, "chorus": 5, "reverb": 6},
  "retired": {}
}
```

- **A new effect** gets the highest id ever used, plus one.
- **A removed effect's id is retired, never reused,** so an old patch or preset can't
  silently select a different effect.
- **Re-adding a removed effect** brings its old id back.
- **Ids run 1..255; 0 is END.** The router skips ids above the highest registered one,
  and the effect bank passes the sample through for an id with no instance.

`scripts/tests/test_fx.py::test_registry_ids_are_pinned` fails if the current ids
change, so a change has to be deliberate.

## Validation (`gen` and `check`)

| check | why |
|---|---|
| names are lower-case identifiers; the effect name matches its folder; not `end` | they become `FX_<NAME>` macros, C identifiers, CLI words |
| knob slots 0..7 unique, names unique, scales valid, defaults in range, `min ≤ max` | the knob bus and the CLI depend on it |
| sources exist, are `.sv/.v/.svh/.vh`, and sit inside `effects/<name>/` | the Vivado and simulator lists are built from them |
| every `` `include `` is among the effect's own headers (or the router's) | includes resolve on a shared include path |
| **module names are unique across all effects, the router and the board RTL** | Vivado has one module namespace |
| **header file names are unique across all effects** | Vivado has one include path |
| a `top` has exactly the contract ports; a `module` maps onto them | the effect bank instantiates it blindly |
| presets: known effects, at most 7 stages, no duplicates, valid values | presets go into the bitstream and the CLI |
| the power-on preset exists | its image goes into the patch RAM |

`fx.py check` adds:
- **Freshness.** `gen/` must match what `gen` would write.
- **Lint.** `verilator --lint-only` on `router_pl` with the generated bank. Errors fail
  the check; warnings are counted (`-v` prints them).
- **Smoke test.** Each effect runs alone in the router (through BRAM port A, the way
  the ARM writes it) with its default knobs. The check:
  - requires one output per input and no timeout;
  - measures the effect's clocks per stage;
  - runs the 7 most expensive effects in one chain at the board's 256 clocks per
    sample and requires no overrun.

```
specs, registry and presets: ok (6 effects)
gen/: up to date
verilator lint: 13 warnings
empty patch: 6 clocks per sample
  overdrive    id 1      11 clocks/stage  ok
  ...
worst patch (chorus -> reverb -> overdrive -> delay -> distortion -> echo): about 84 of 256 clocks per sample: fits
  run at 256 clocks/sample: busy 84 clocks, no overrun
check passed
```

## Generated files

| file | contents |
|---|---|
| `gen/rtl/effect_bank.sv` | one `<name>_top` instance per effect, its knob register, the result mux, the pass-through for unknown ids |
| `gen/rtl/router_patch.svh` | `ROUTER_*` memory-map macros, `FX_<NAME>` ids, `ROUTER_N_FX` (the highest id), `ROUTER_PATCH_INIT` (power-on image) |
| `gen/rtl/<name>_top.sv` | contract wrappers for specs that give `module` (deleted again when no longer needed) |
| `gen/sw/router_map.h` | `ROUTER_FX_<NAME>` ids, the memory map, `router_write_slot()` and the other access helpers |
| `gen/sw/fx_table.h` / `.c` | `fx_effects[]` (knobs with slot, scale, range, default, factor, unit, doc), `fx_presets[]`, `fx_power_on_preset` |
| `gen/vivado/sources.tcl` | adds every RTL file and header to the open Vivado project and sets the include path. Idempotent; drops repo files no longer in the build. |
| `gen/sim/sources.f` | the same list as a Verilator/Icarus command file, relative to the repo root |
| `README.md` | the table between the `fx:effects-table` markers |

`gen/` is committed so a fresh clone builds without Python. All files are written as
UTF-8 with LF line endings on every OS (`.gitattributes` keeps git consistent), so a
Windows and a macOS checkout generate identical bytes.

## Library layout (`scripts/fxlib/`)

| module | role |
|---|---|
| `layout.py` | where everything lives, relative to a repo root (tests use scratch copies) |
| `textio.py` | UTF-8, LF-only file reading and writing |
| `routermap.py` | **the patch memory map**: the one place to change slot counts, bank addresses, RAM size |
| `spec.py` | spec and knob model, value grammar, spec validation |
| `registry.py` | ids, append-only assignment, retirement, the power-on preset |
| `verilog_ports.py` | comment stripping, `` `include `` and module discovery, ANSI port-list parsing |
| `project.py` | presets, cross-effect validation, `add`/`remove`, deriving a spec from RTL, rendering |
| `render_rtl.py`, `render_c.py`, `render_lists.py` | the generated files' text |
| `sim.py` | `check`'s lint and smoke test |

Tests are in `scripts/tests/test_fx.py`; run them with `make -C sim gen-tests`, which
needs `pytest`.
