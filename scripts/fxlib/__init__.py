"""fxlib — the effect registry and code generator behind scripts/fx.py.

Everything that depends on *which* effects are in the bitstream is generated
from three inputs, so adding an effect never means editing several files by hand:

    effects/registry.json        effect ids (append-only) and the power-on preset
    effects/<name>/effect.json   one spec per effect: sources, top module, knobs
    presets/*.json               chains of effects, same format as VerilogSim's

Outputs go to gen/ (see render_*.py). The router core in router/rtl and the
board RTL in rtl/ do not depend on the effect list, so they are never generated.
"""
