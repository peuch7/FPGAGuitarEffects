# sim/Makefile - every check that runs without a board or Vivado.
#
#   make -C sim            all of the below
#   make -C sim check      fx.py check: specs, gen/ freshness, lint, per-effect smoke + cycle budget
#   make -C sim compare    bit-exact against ../VerilogSim's router (needs that checkout)
#   make -C sim audio      I2S pin-to-pin: codec model -> i2s_rx -> router -> i2s_tx -> codec model
#   make -C sim cli        the CLI firmware on the host, against a fake patch memory
#   make -C sim gen-tests  unit tests of the generator (needs pytest)
#
# Needs Verilator 5 (--timing), a C compiler and Python 3.

ROOT    := $(abspath ..)
BUILD   := $(ROOT)/build/sim
PYTHON  ?= python3
VFLAGS  := --binary --timing -j 0 -Wno-fatal -Wno-lint -Wno-style

.PHONY: all check compare audio cli gen-tests clean

all: check audio cli gen-tests compare

check:
	cd $(ROOT) && $(PYTHON) scripts/fx.py check

compare:
	cd $(ROOT) && $(PYTHON) sim/compare_verilogsim.py

audio:
	mkdir -p $(BUILD)
	cd $(ROOT) && verilator $(VFLAGS) --top-module tb_audio_path -Mdir $(BUILD)/obj_audio \
		-f gen/sim/sources.f sim/tb_audio_path.sv > $(BUILD)/audio_build.log
	$(BUILD)/obj_audio/Vtb_audio_path | grep -v '^- '

cli:
	cd $(ROOT) && $(PYTHON) sim/host/test_cli.py

gen-tests:
	cd $(ROOT) && $(PYTHON) -m pytest -q scripts/tests

clean:
	rm -rf $(BUILD)
