# sim/Makefile - every check that runs without a board or Vivado.
#
#   make -C sim            all of the below
#   make -C sim check      fx.py check: specs, gen/ freshness, lint, per-effect smoke + cycle budget
#   make -C sim audio      I2S pin-to-pin: codec model -> i2s_rx -> router -> i2s_tx -> codec model
#   make -C sim delay      regression: shortening the delay while it runs
#   make -C sim cli        the CLI firmware on the host, against a fake patch memory
#   make -C sim gen-tests  unit tests of the generator (needs pytest)
#
# Needs Verilator 5 (--timing), a C compiler and Python 3.

ROOT    := $(abspath ..)
BUILD   := $(ROOT)/build/sim
PYTHON  ?= python3
VFLAGS  := --binary --timing -j 0 -Wno-fatal -Wno-lint -Wno-style

.PHONY: all check audio delay cli gen-tests clean

all: check audio delay cli gen-tests

check:
	cd $(ROOT) && $(PYTHON) scripts/fx.py check

# Testbench binaries exit non-zero on failure ($fatal); keep that exit status.
audio:
	mkdir -p $(BUILD)
	cd $(ROOT) && verilator $(VFLAGS) --top-module tb_audio_path -Mdir $(BUILD)/obj_audio \
		-f gen/sim/sources.f sim/tb_audio_path.sv > $(BUILD)/audio_build.log
	$(BUILD)/obj_audio/Vtb_audio_path > $(BUILD)/audio.log; s=$$?; grep -v '^- ' $(BUILD)/audio.log; exit $$s

delay:
	mkdir -p $(BUILD)
	cd $(ROOT) && verilator $(VFLAGS) --top-module tb_delay_wrap -Mdir $(BUILD)/obj_delay +incdir+router/rtl \
		effects/delay/rtl/delay.sv effects/delay/rtl/delay_top.sv sim/tb_delay_wrap.sv > $(BUILD)/delay_build.log
	$(BUILD)/obj_delay/Vtb_delay_wrap > $(BUILD)/delay.log; s=$$?; grep -v '^- ' $(BUILD)/delay.log; exit $$s

cli:
	cd $(ROOT) && $(PYTHON) sim/host/test_cli.py

gen-tests:
	cd $(ROOT) && $(PYTHON) -m pytest -q scripts/tests

clean:
	rm -rf $(BUILD)
