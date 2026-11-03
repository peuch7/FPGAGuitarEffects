"""Tests for scripts/fx.py and fxlib.

Most tests work on a scratch copy of the repo (the `repo` fixture), so they can
add and remove effects freely. Tests that need Verilator or the sibling
VerilogSim checkout skip themselves when those are missing.

    python3 -m pytest -q scripts/tests
"""

from __future__ import annotations

import json
import random
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

VERILOGSIM = ROOT.parent / "VerilogSim"
needs_verilator = pytest.mark.skipif(shutil.which("verilator") is None, reason="needs verilator")
needs_verilogsim = pytest.mark.skipif(not (VERILOGSIM / "configs" / "effects").is_dir(),
                                      reason="needs ../VerilogSim")

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


def test_registry_matches_verilogsim_ids():
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
    stages = [stage for _, stage in dict((n, s) for n, _, s in repo.presets())["example_mesh"]]
    repo.registry.power_on = "example_mesh"
    repo.registry.save()
    repo.gen()
    svh = (repo.layout.gen_rtl / "router_patch.svh").read_text()
    for addr, value in rm.power_on_image(stages).items():
        assert f"mem[{addr}] = 32'h{value:08x};" in svh


@needs_verilogsim
def test_svh_and_c_header_match_verilogsim(repo):
    repo.registry.power_on = "example_mesh"
    repo.registry.save()
    repo.gen()

    def code(path, comment):
        return [l for l in path.read_text().splitlines() if l.strip() and not l.lstrip().startswith(comment)]

    assert code(repo.layout.gen_rtl / "router_patch.svh", "//") == \
        code(VERILOGSIM / "rtl" / "router" / "router_patch.svh", "//")
    ours = (repo.layout.gen_sw / "router_map.h").read_text()
    theirs = (VERILOGSIM / "sw" / "router_map.h").read_text()
    assert ours[ours.index("#ifndef"):].replace("effects/<name>/effect.json", "configs/effects/<name>.json") \
        == theirs[theirs.index("#ifndef"):]


@needs_verilogsim
def test_knob_bits_match_wavsim(repo):
    sys.path.insert(0, str(VERILOGSIM / "tools"))
    try:
        from wavsim.manifest import Manifest, ManifestError
    except ImportError as exc:   # wavsim needs numpy for some modules
        pytest.skip(f"cannot import wavsim: {exc}")
    rng = random.Random(3)
    for spec, _ in repo.effects():
        theirs = Manifest.load(VERILOGSIM / "configs" / "effects" / f"{spec.name}.json")
        assert spec.resolve() == theirs.resolve()
        for p in spec.params:
            for _ in range(50):
                text = f"{rng.uniform(-0.05, 1.05) * (p.natural(p.hi) - p.natural(p.lo)) + p.natural(p.lo):.4f}"
                try:
                    want = theirs.by_name(p.name).to_bits(text)
                except ManifestError:
                    want = None
                try:
                    got = p.to_bits(text)
                except SpecError:
                    got = None
                if want is not None and want > 65535:
                    continue        # the manifest allows 65536; we clamp to the 16-bit slot
                assert got == want, (spec.name, p.name, text)


# -- ids -------------------------------------------------------------------------

def test_ids_are_append_only(repo, tmp_path):
    assert repo.remove_effect("echo") == 4
    assert repo.registry.retired == {"echo": 4}
    fx_id = repo.import_effect(Spec.load(tremolo_spec(tmp_path)), "test\n")
    assert fx_id == 7, "a retired id must not be reused"
    assert repo.import_effect(Spec.load(repo.layout.spec_path("echo")), "test\n") == 4
    repo.gen()
    svh = (repo.layout.gen_rtl / "router_patch.svh").read_text()
    assert "`define ROUTER_N_FX        7" in svh


def test_remove_refuses_effect_used_by_a_preset(repo):
    with pytest.raises(FxError, match="used by"):
        repo.remove_effect("delay")
    assert "delay" in repo.registry.effects


def test_upstream_id_conflict_is_refused(repo, tmp_path):
    spec = Spec.load(tremolo_spec(tmp_path))
    with pytest.raises(FxError, match="belongs to"):
        repo.import_effect(spec, "test\n", wanted_id=3)


# -- spec validation -------------------------------------------------------------

@pytest.mark.parametrize("change, message", [
    (lambda s: s.update(name="Bad-Name"), "lower-case identifier"),
    (lambda s: s["params"][1].update(index=0), "share index"),
    (lambda s: s["params"][0].update(default=2.0), "outside"),
    (lambda s: s["params"][0].update(scale="log"), "scale"),
    (lambda s: s.update(top="tremolo_top"), "not both"),
    (lambda s: s["ports"].update(sample_in=None), "cannot be null"),
    (lambda s: s.update(sources=["missing.sv"]), "not found"),
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
        repo.import_effect(Spec.load(path), "test\n")
    assert "tremolo" not in repo.registry.effects


def test_wide_knob_port_is_refused(tmp_path):
    (tmp_path / "wide.sv").write_text(TREMOLO_CONTRACT_SV.replace("[15:0]        rate", "[19:0]        rate"))
    with pytest.raises(FxError, match="knobs are at most 16"):
        derive_spec([tmp_path / "wide.sv"], name="wide")


def test_module_name_clash_is_refused(repo, tmp_path):
    (tmp_path / "delay2.sv").write_text(TREMOLO_CONTRACT_SV.replace("module tremolo", "module delay"))
    spec = derive_spec([tmp_path / "delay2.sv"], name="delay2")
    repo.import_effect(spec, "test\n")
    with pytest.raises(FxError, match="module delay is defined by both"):
        repo.validate()


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
    repo.import_effect(Spec.load(tremolo_spec(tmp_path)), "test\n")
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
    repo.import_effect(Spec.load(path), "test\n")
    repo.gen()
    assert "knob_depth[15:8]" in (repo.layout.gen_rtl / "tremolo_top.sv").read_text()


def test_raw_knob_too_big_for_its_port_is_refused(repo, tmp_path):
    path = tremolo_spec(tmp_path)
    raw = json.loads(path.read_text())
    raw["params"][0]["max"] = 1000
    path.write_text(json.dumps(raw))
    with pytest.raises(FxError, match="does not fit the 8-bit port"):
        repo.import_effect(Spec.load(path), "test\n")


def test_removed_wrapper_is_deleted(repo, tmp_path):
    repo.import_effect(Spec.load(tremolo_spec(tmp_path)), "test\n")
    repo.gen()
    repo.remove_effect("tremolo")
    repo.gen()
    assert not (repo.layout.gen_rtl / "tremolo_top.sv").exists()


@needs_verilator
def test_new_effect_lints_and_runs_through_the_router(repo, tmp_path):
    repo.import_effect(Spec.load(tremolo_spec(tmp_path)), "test\n")
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


# -- bringing effects in from VerilogSim ---------------------------------------------

@needs_verilogsim
@needs_verilator
def test_sync_lowpass_and_add_highpass_end_to_end(repo, tmp_path):
    # The manifest path: VerilogSim's own spec, with its contract wrapper.
    done = repo.sync_effects(VERILOGSIM, ["lowpass"])
    assert done[0][0] == "lowpass" and done[0][1] == 7
    # The RTL-only path: the core module, spec derived, wrapper generated.
    spec = derive_spec([VERILOGSIM / "rtl" / "effects" / "highpass" / "highpass.sv"], name="highpass2")
    assert spec.module == "highpass" and [p.name for p in spec.params] == ["cutoff_hz"]
    spec.params[0].min, spec.params[0].default = 100, 200
    repo.import_effect(spec, "test\n")
    repo.gen()
    assert (repo.layout.gen_rtl / "highpass2_top.sv").exists()
    ok, text = sim.lint(repo.layout)
    assert ok, text
    ok, lines = sim.smoke(repo, n=32)
    assert ok, "\n".join(lines)
    table = (repo.layout.gen_sw / "fx_table.c").read_text()
    assert '"lowpass", 7' in table and '"highpass2", 8' in table


def test_cli_entry_point(repo):
    res = subprocess.run([sys.executable, str(ROOT / "scripts" / "fx.py"), "--root", str(repo.layout.root),
                          "list"], capture_output=True, text=True)
    assert res.returncode == 0 and "reverb" in res.stdout
