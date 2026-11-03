"""Tests for scripts/fx.py and fxlib.

Most tests work on a scratch copy of the repo (the `repo` fixture), so they can
add and remove effects freely. Tests that need Verilator skip themselves when
it is missing.

    python3 -m pytest -q scripts/tests
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from fxlib import routermap as rm  # noqa: E402
from fxlib import sim  # noqa: E402
from fxlib.layout import Layout  # noqa: E402
from fxlib.project import FxError, Project, derive_spec  # noqa: E402
from fxlib.spec import Spec, SpecError  # noqa: E402

needs_verilator = pytest.mark.skipif(shutil.which("verilator") is None, reason="needs verilator")

# A core module with its own port names, a narrow knob, and no reset: the case
# the generated wrapper exists for.
TREMOLO_SV = """\
`timescale 1ns / 1ps
module tremolo (
    input  logic               clock,
    input  logic signed [23:0] x,
    input  logic               x_valid,
    input  logic [7:0]         depth,     // narrower than a knob
    input  logic [15:0]        rate,
    output logic signed [23:0] y,
    output logic               y_valid
);
    always_ff @(posedge clock) begin
        y       <= x - ((x >>> 8) * $signed({1'b0, depth}));
        y_valid <= x_valid;
    end
    wire unused = ^rate;
endmodule
"""


# The same core with the contract's port names, which is what derive_spec reads.
TREMOLO_CONTRACT_SV = (TREMOLO_SV.replace("clock", "clk")
                       .replace("x_valid", "sample_in_valid").replace("y_valid", "sample_out_valid")
                       .replace(" x,", " sample_in,").replace(" y,", " sample_out,")
                       .replace("y       <= x - ((x", "sample_out <= sample_in - ((sample_in")
                       .replace("y_valid <= x_valid", "sample_out_valid <= sample_in_valid"))


@pytest.fixture
def repo(tmp_path) -> Project:
    for d in ("router", "effects", "presets", "rtl", "gen"):
        shutil.copytree(ROOT / d, tmp_path / d)
    (tmp_path / "sim").mkdir()
    shutil.copyfile(ROOT / "sim" / "tb_router.sv", tmp_path / "sim" / "tb_router.sv")
    shutil.copyfile(ROOT / "README.md", tmp_path / "README.md")
    return Project(Layout(tmp_path))


def tremolo_spec(tmp_path: Path) -> Path:
    (tmp_path / "in").mkdir(exist_ok=True)
    (tmp_path / "in" / "tremolo.sv").write_text(TREMOLO_SV)
    spec = {
        "name": "tremolo", "description": "Amplitude wobble.", "module": "tremolo",
        "ports": {"clk": "clock", "rst_n": None, "sample_in": "x", "sample_in_valid": "x_valid",
                  "sample_out": "y", "sample_out_valid": "y_valid"},
        "sources": ["tremolo.sv"],
        "params": [
            {"name": "depth", "index": 0, "default": 0.5, "scale": "raw", "min": 0, "max": 255,
             "factor": 0.00392156862745098, "doc": "0 = none, 1 = full"},
            {"name": "rate", "index": 1, "default": 400, "scale": "raw", "unit": "cHz", "doc": "LFO"},
        ],
    }
    path = tmp_path / "in" / "tremolo.json"
    path.write_text(json.dumps(spec))
    return path


# -- the committed repo --------------------------------------------------------

def test_committed_gen_is_up_to_date():
    assert Project().gen(check_only=True) == [], "run: python3 scripts/fx.py gen"


def test_registry_ids_are_pinned():
    # Ids are what a saved patch means. Changing one is a breaking change, so it
    # has to be deliberate: update this test along with it.
    reg = Project().registry
    assert reg.effects == {"overdrive": 1, "distortion": 2, "delay": 3, "echo": 4, "chorus": 5, "reverb": 6}


# -- generation ------------------------------------------------------------------

def test_gen_is_idempotent(repo):
    repo.gen()
    assert repo.gen() == []


def test_readme_table_lists_every_effect(repo):
    repo.gen()
    text = repo.layout.readme.read_text()
    for spec, fx_id in repo.effects():
        assert f"| {fx_id} | {spec.name} |" in text


def test_power_on_image_in_svh(repo):
    stages = [stage for _, stage in dict((n, s) for n, _, s in repo.presets())["pedalboard"]]
    repo.registry.power_on = "pedalboard"
    repo.registry.save()
    repo.gen()
    svh = (repo.layout.gen_rtl / "router_patch.svh").read_text()
    for addr, value in rm.power_on_image(stages).items():
        assert f"mem[{addr}] = 32'h{value:08x};" in svh


def test_knob_value_rules():
    q16 = Spec.load(ROOT / "effects" / "delay" / "effect.json").by_name("mix")
    assert q16.to_bits("0.5") == 32768          # 32767.5 rounds half to even
    assert q16.to_bits("50%") == 32768
    assert q16.to_bits(0.25) == 16384
    assert q16.to_bits("1234") == 1234          # a plain integer is raw
    assert q16.to_bits("1.0") == 65535
    with pytest.raises(SpecError, match="outside"):
        q16.to_bits("1.5")
    rate = Spec.load(ROOT / "effects" / "chorus" / "effect.json").by_name("rate")   # raw, factor 0.01
    assert rate.to_bits("0.55") == 55 and rate.to_bits("8.0") == 800
    with pytest.raises(SpecError, match="outside"):
        rate.to_bits("0.05")                    # 5 is below min 10
    samples = Spec.load(ROOT / "effects" / "delay" / "effect.json").by_name("delay_samples")
    assert samples.to_bits("2.5") == 2 and samples.to_bits("3.5") == 4      # half to even


@needs_verilator
def test_power_on_image_runs_like_the_same_preset_written_by_the_arm(repo, tmp_path):
    # The bitstream's power-on patch (BRAM initial contents) must behave exactly
    # like the CLI writing that preset: same words, same sound.
    repo.registry.power_on = "pedalboard"
    repo.registry.save()
    repo.gen()
    work = tmp_path / "work"
    work.mkdir()
    exe = sim.compile_tb(repo.layout, work)
    stages = [stage for _, stage in dict((n, s) for n, _, s in repo.presets())["pedalboard"]]
    (work / "arm.patch").write_text("".join(
        f"{a:x} {v:x}\n" for a, v in rm.bank_writes(stages, 1) + [(rm.CTRL_WORD, 1)]))
    outs = []
    for extra in ([], [f"+patch={work / 'arm.patch'}"]):
        out = work / f"out{len(outs)}.txt"
        subprocess.run([str(exe), "+n=3000", f"+out={out}", *extra], check=True, capture_output=True)
        outs.append(out.read_text())
    assert len(outs[0].split()) == 3000 and outs[0] == outs[1]


# -- ids -------------------------------------------------------------------------

def test_ids_are_append_only(repo, tmp_path):
    assert repo.remove_effect("echo") == 4
    assert repo.registry.retired == {"echo": 4}
    fx_id = repo.import_effect(Spec.load(tremolo_spec(tmp_path)))
    assert fx_id == 7, "a retired id must not be reused"
    assert repo.import_effect(Spec.load(repo.layout.spec_path("echo"))) == 4
    repo.gen()
    svh = (repo.layout.gen_rtl / "router_patch.svh").read_text()
    assert "`define ROUTER_N_FX        7" in svh


def test_remove_refuses_effect_used_by_a_preset(repo):
    with pytest.raises(FxError, match="used by"):
        repo.remove_effect("delay")
    assert "delay" in repo.registry.effects


# -- spec validation -------------------------------------------------------------

@pytest.mark.parametrize("change, message", [
    (lambda s: s.update(name="Bad-Name"), "lower-case identifier"),
    (lambda s: s["params"][1].update(index=0), "share index"),
    (lambda s: s["params"][0].update(default=2.0), "outside"),
    (lambda s: s["params"][0].update(scale="log"), "scale"),
    (lambda s: s.update(top="tremolo_top"), "not both"),
    (lambda s: s["ports"].update(sample_in=None), "cannot be null"),
    (lambda s: s.update(sources=["missing.sv"]), "not found"),
    (lambda s: s.update(reference_model="x"), "unknown keys"),
    (lambda s: s["params"][0].update(index="0"), "index must be int"),
    (lambda s: s["params"][0].update(max=255.5), "max must be int"),
    (lambda s: s["params"][0].update(default=[1]), "default must be"),
    (lambda s: s.update(sources="tremolo.sv"), "sources must be a list"),
    (lambda s: s.update(sources=["tremolo.txt"]), "sources must be"),
    (lambda s: s.update(params={"a": 1}), "params must be a list"),
])
def test_spec_errors(tmp_path, change, message):
    path = tremolo_spec(tmp_path)
    raw = json.loads(path.read_text())
    change(raw)
    path.write_text(json.dumps(raw))
    with pytest.raises((SpecError, FxError), match=message):
        Spec.load(path).validate()


def test_rtl_mismatch_is_caught_before_anything_is_registered(repo, tmp_path):
    path = tremolo_spec(tmp_path)
    raw = json.loads(path.read_text())
    raw["params"][1]["port"] = "speed"          # no such port on tremolo
    path.write_text(json.dumps(raw))
    with pytest.raises(FxError, match="does not exist"):
        repo.import_effect(Spec.load(path))
    assert "tremolo" not in repo.registry.effects


def test_wide_knob_port_is_refused(tmp_path):
    (tmp_path / "wide.sv").write_text(TREMOLO_CONTRACT_SV.replace("[15:0]        rate", "[19:0]        rate"))
    with pytest.raises(FxError, match="knobs are at most 16"):
        derive_spec([tmp_path / "wide.sv"], name="wide")


def test_module_name_clash_is_refused_and_leaves_nothing_behind(repo, tmp_path):
    (tmp_path / "delay2.sv").write_text(TREMOLO_CONTRACT_SV.replace("module tremolo", "module delay"))
    spec = derive_spec([tmp_path / "delay2.sv"], name="delay2")
    before = repo.layout.registry.read_text()
    with pytest.raises(FxError, match="module delay is defined by both"):
        repo.import_effect(spec)
    assert "delay2" not in repo.registry.effects
    assert not repo.layout.effect_dir("delay2").exists()
    assert repo.layout.registry.read_text() == before
    repo.validate()                 # the project is still fine


def test_failed_re_import_keeps_the_old_files(repo, tmp_path):
    original = (repo.layout.effect_dir("delay") / "rtl" / "delay.sv").read_text()
    spec = Spec.load(repo.layout.spec_path("delay"))
    (tmp_path / "delay.sv").write_text(TREMOLO_CONTRACT_SV.replace("module tremolo", "module chorus"))
    spec.sources = [str(tmp_path / "delay.sv"), str(repo.layout.effect_dir("delay") / "rtl" / "delay_top.sv")]
    spec.root = Path("/")
    with pytest.raises(FxError):
        repo.import_effect(spec)    # chorus is taken, and delay_top's core is gone
    assert (repo.layout.effect_dir("delay") / "rtl" / "delay.sv").read_text() == original
    repo.validate()


def test_remove_unknown_effect(repo):
    with pytest.raises(FxError, match="not an active effect"):
        repo.remove_effect("nope")


def test_generated_files_are_utf8_with_lf(repo):
    repo.gen()
    for path in list(repo.layout.gen.rglob("*.*")) + [repo.layout.registry]:
        data = path.read_bytes()
        assert b"\r\n" not in data, path
        data.decode("utf-8")


# -- generated wrappers ------------------------------------------------------------

def test_derived_spec_from_core_rtl(tmp_path):
    (tmp_path / "x.sv").write_text(TREMOLO_CONTRACT_SV)
    spec = derive_spec([tmp_path / "x.sv"])
    assert spec.module == "tremolo" and spec.name == "tremolo"
    assert [(p.name, p.index, p.max) for p in spec.params] == [("depth", 0, 255), ("rate", 1, 65535)]
    assert spec.ports == {"rst_n": None}


def test_derived_spec_from_contract_wrapper():
    spec = derive_spec([ROOT / "effects" / "chorus" / "rtl" / "chorus_top.sv",
                        ROOT / "effects" / "chorus" / "rtl" / "chorus.sv"])
    assert spec.top == "chorus_top" and spec.name == "chorus"
    assert [p.name for p in spec.params] == ["rate", "depth", "mix", "base_ms"]


def test_wrapper_slices_and_renames(repo, tmp_path):
    repo.import_effect(Spec.load(tremolo_spec(tmp_path)))
    repo.gen()
    text = (repo.layout.gen_rtl / "tremolo_top.sv").read_text()
    assert ".clock" in text and "(clk)" in text and ".x " in text
    assert "knob_depth[7:0]" in text
    assert "wire unused_rst_n = rst_n;" in text
    assert "gen/rtl/tremolo_top.sv" in (repo.layout.gen_vivado / "sources.tcl").read_text()


def test_narrow_q16_knob_takes_the_top_bits(repo, tmp_path):
    path = tremolo_spec(tmp_path)
    raw = json.loads(path.read_text())
    raw["params"][0] = {"name": "depth", "index": 0, "default": 0.5, "scale": "q16"}
    path.write_text(json.dumps(raw))
    repo.import_effect(Spec.load(path))
    repo.gen()
    assert "knob_depth[15:8]" in (repo.layout.gen_rtl / "tremolo_top.sv").read_text()


def test_raw_knob_too_big_for_its_port_is_refused(repo, tmp_path):
    path = tremolo_spec(tmp_path)
    raw = json.loads(path.read_text())
    raw["params"][0]["max"] = 1000
    path.write_text(json.dumps(raw))
    with pytest.raises(FxError, match="does not fit the 8-bit port"):
        repo.import_effect(Spec.load(path))


@needs_verilator
def test_an_effect_named_like_the_bank_ports_still_lints(repo, tmp_path):
    # The bank's ports are fx_sel, fx_params...; an effect called "fx" used to
    # declare fx_params and fx_sel again.
    path = tremolo_spec(tmp_path)
    raw = json.loads(path.read_text())
    raw["name"] = "fx"
    path.write_text(json.dumps(raw))
    repo.import_effect(Spec.load(path))
    repo.gen()
    ok, text = sim.lint(repo.layout)
    assert ok, text


def test_removed_wrapper_is_deleted(repo, tmp_path):
    repo.import_effect(Spec.load(tremolo_spec(tmp_path)))
    repo.gen()
    repo.remove_effect("tremolo")
    repo.gen()
    assert not (repo.layout.gen_rtl / "tremolo_top.sv").exists()


@needs_verilator
def test_new_effect_lints_and_runs_through_the_router(repo, tmp_path):
    repo.import_effect(Spec.load(tremolo_spec(tmp_path)))
    repo.gen()
    ok, text = sim.lint(repo.layout)
    assert ok, text
    ok, lines = sim.smoke(repo, n=32)
    assert ok, "\n".join(lines)
    assert any("tremolo" in l and "ok" in l for l in lines)


@needs_verilator
def test_retired_id_in_a_patch_is_bypassed_not_hung(repo, tmp_path):
    repo.remove_effect("echo")
    repo.gen()
    work = tmp_path / "work"
    work.mkdir()
    exe = sim.compile_tb(repo.layout, work)
    stats = sim.run_patch(repo.layout, exe, work, "gap", [(4, [0] * 8)], 256, 32)
    assert stats["samples_out"] == stats["samples_in"] and not stats["fx_timeout"]


# -- the command line --------------------------------------------------------------

def fx(repo, *args):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "fx.py"), "--root",
                           str(repo.layout.root), *map(str, args)], capture_output=True, text=True)


def test_add_from_bare_rtl_on_the_command_line(repo, tmp_path):
    (tmp_path / "tremolo.sv").write_text(TREMOLO_CONTRACT_SV)
    res = fx(repo, "add", tmp_path / "tremolo.sv")
    assert res.returncode == 0, res.stderr
    assert "added tremolo as id 7" in res.stdout
    assert (repo.layout.gen_rtl / "tremolo_top.sv").exists()
    assert '"tremolo", 7' in (repo.layout.gen_sw / "fx_table.c").read_text()


def test_re_adding_an_effect_from_its_own_folder_keeps_it(repo):
    before = (repo.layout.effect_dir("chorus") / "rtl" / "chorus.sv").read_text()
    res = fx(repo, "add", repo.layout.spec_path("chorus"))
    assert res.returncode == 0, res.stderr
    assert "added chorus as id 5" in res.stdout
    assert (repo.layout.effect_dir("chorus") / "rtl" / "chorus.sv").read_text() == before
    assert (repo.layout.effect_dir("chorus") / "rtl" / "chorus_sine_table.svh").exists()


def test_power_on_rolls_back_when_the_project_does_not_check_out(repo):
    (repo.layout.presets / "broken.json").write_text(
        json.dumps({"name": "broken", "stages": [{"effect": "nope"}]}))
    before = repo.layout.registry.read_text()
    res = fx(repo, "power-on", "broken")
    assert res.returncode == 1 and "not in the build" in res.stderr
    assert repo.layout.registry.read_text() == before


def test_corrupt_registry_is_an_error_not_a_crash(repo):
    repo.layout.registry.write_text("{ not json")
    res = fx(repo, "list")
    assert res.returncode == 1 and "cannot read the registry" in res.stderr


def test_list(repo):
    res = fx(repo, "list")
    assert res.returncode == 0 and "reverb" in res.stdout
