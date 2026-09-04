# Copyright DEWETRON GmbH 2026
# Python port of odkex_bin_detector.cpp
#
# Detects the minimum and maximum values of a vector channel together
# with the bin (index) at which each extremum occurs.  Two pairs of
# output channels can be individually enabled/disabled at runtime.

from __future__ import annotations

import logging
import numpy as np

import py_oxygen_sdk
import py_oxygen_sdk.api as api
import py_oxygen_sdk.framework as fw

log = logging.getLogger("bin_detector")

PLUGIN_NAME = "ODKEX_BIN_DETECTOR_PY"

KEY_INPUT_CHANNEL   = "ODKEX_BIN_DETECTOR_PY/MyInputChannel"
ENABLE_MIN_CHANNELS = "ODKEX_BIN_DETECTOR_PY/EnableMin"
ENABLE_MAX_CHANNELS = "ODKEX_BIN_DETECTOR_PY/EnableMax"

TRANSLATION_EN = """<?xml version="1.0"?>
<TS version="2.1" language="en" sourcelanguage="en">
    <context><name>ConfigKeys</name>
        <message><source>ODKEX_BIN_DETECTOR_PY/MyInputChannel</source><translation>Input Channel</translation></message>
        <message><source>ODKEX_BIN_DETECTOR_PY/EnableMin</source><translation>Enable Minimum</translation></message>
        <message><source>ODKEX_BIN_DETECTOR_PY/EnableMax</source><translation>Enable Maximum</translation></message>
    </context>
</TS>"""


class BinDetectorInstance(fw.PySoftwareChannelInstance):

    def __init__(self):
        super().__init__()

        self._input_channel = fw.EditableChannelIDProperty()
        self._input_channel.visibility = "PUBLIC"

        self._enable_min = fw.EditableStringProperty("On")
        self._enable_min.visibility = "PUBLIC"
        self._enable_min.add_option("On")
        self._enable_min.add_option("Off")
        self._enable_min.set_arbitrary_string(False)

        self._enable_max = fw.EditableStringProperty("On")
        self._enable_max.visibility = "PUBLIC"
        self._enable_max.add_option("On")
        self._enable_max.add_option("Off")
        self._enable_max.set_arbitrary_string(False)

        # Output channel handles, created lazily in _sync_output_channels()
        self._min_value_ch = None
        self._min_bin_ch   = None
        self._max_value_ch = None
        self._max_bin_ch   = None

        self._dimension = 0
        self._timebase_frequency = 0.0

    # ------------------------------------------------------------------
    # create
    # ------------------------------------------------------------------
    def create(self, host) -> None:
        self.root_channel.default_name = "Bin Detector Group"
        self.root_channel.deletable = True
        self.root_channel.add_property(KEY_INPUT_CHANNEL,   self._input_channel)
        self.root_channel.add_property(ENABLE_MIN_CHANNELS, self._enable_min)
        self.root_channel.add_property(ENABLE_MAX_CHANNELS, self._enable_max)

    # ------------------------------------------------------------------
    # init
    # ------------------------------------------------------------------
    def init(self, params: fw.InitParams) -> fw.InitResult:
        ids = list(params.input_channel_ids)
        if ids:
            self._input_channel.value = ids[0]
        result = fw.InitResult(True)
        result.show_channel_details(self.root_channel.local_id)
        return result

    # ------------------------------------------------------------------
    # configure  (called when loading a saved setup)
    # ------------------------------------------------------------------
    def configure(self, request, channel_id_map: dict) -> bool:
        self.configure_from_telegram(request, channel_id_map)
        # Restore named channel handles from the telegram
        self._min_value_ch = self.get_output_channel_by_key("min_value")
        self._min_bin_ch   = self.get_output_channel_by_key("min_bin")
        self._max_value_ch = self.get_output_channel_by_key("max_value")
        self._max_bin_ch   = self.get_output_channel_by_key("max_bin")
        return True

    # ------------------------------------------------------------------
    # update
    # ------------------------------------------------------------------
    def update(self) -> bool:
        proxies = self.get_input_channel_proxies()
        if not proxies:
            return False

        proxy = self.get_input_channel_proxy(self._input_channel.value)
        if proxy is None:
            return False

        fmt = proxy.data_format
        if fmt.sample_value_type != api.SampleValueType.SAMPLE_VALUE_VECTOR:
            return False
        if fmt.sample_dimension == 0:
            return False

        self._dimension = fmt.sample_dimension
        self._sync_output_channels(proxy)
        return True

    # ------------------------------------------------------------------
    # init_timebases
    # ------------------------------------------------------------------
    def init_timebases(self, host) -> None:
        self._timebase_frequency = 0.0
        proxy = self.get_input_channel_proxy(self._input_channel.value)
        if proxy and proxy.update_time_base():
            self._timebase_frequency = max(
                self._timebase_frequency, proxy.time_base.frequency)

        for ch in self.get_output_channels():
            ch.set_simple_timebase(self._timebase_frequency)

    # ------------------------------------------------------------------
    # process
    # ------------------------------------------------------------------
    def process(self, ctx: fw.ProcessingContext, host) -> None:
        ch_id = self._input_channel.value
        proxy = self.get_input_channel_proxy(ch_id)
        if proxy is None:
            return

        # Iterate block-by-block using zero-copy views
        for samples, ticks in ctx.get_blocks(ch_id, proxy):
            # samples shape: (N, dim) for vector channels
            if samples.ndim == 1:
                samples = samples.reshape(-1, self._dimension)

            n = len(samples)
            for i in range(n):
                row = samples[i].astype(np.float64)
                tick = int(ticks[i])

                if self._min_value_ch is not None:
                    used = self._min_value_ch.used_property
                    if used is None or used.value:
                        min_idx = int(np.argmin(row))
                        host.add_sample(self._min_value_ch, tick, float(row[min_idx]))
                if self._min_bin_ch is not None:
                    used = self._min_bin_ch.used_property
                    if used is None or used.value:
                        min_idx = int(np.argmin(row))
                        host.add_sample(self._min_bin_ch, tick, float(min_idx))

                if self._max_value_ch is not None:
                    used = self._max_value_ch.used_property
                    if used is None or used.value:
                        max_idx = int(np.argmax(row))
                        host.add_sample(self._max_value_ch, tick, float(row[max_idx]))
                if self._max_bin_ch is not None:
                    used = self._max_bin_ch.used_property
                    if used is None or used.value:
                        max_idx = int(np.argmax(row))
                        host.add_sample(self._max_bin_ch, tick, float(max_idx))

    # ------------------------------------------------------------------
    # update_input_channel_ids
    # ------------------------------------------------------------------
    def update_input_channel_ids(self, channel_mapping: dict) -> None:
        cur = self._input_channel.value
        self._input_channel.value = channel_mapping.get(cur, cur)

    # ------------------------------------------------------------------
    # Private: create or remove output channels to match enable flags
    # ------------------------------------------------------------------
    def _sync_output_channels(self, proxy) -> None:
        input_range = proxy.range
        unit        = proxy.unit
        name        = proxy.name

        want_min = (self._enable_min.value == "On")
        want_max = (self._enable_max.value == "On")

        # --- min pair ---
        if want_min and self._min_value_ch is None:
            self._min_value_ch = self.add_output_channel("min_value")
            self._min_bin_ch   = self.add_output_channel("min_bin")

        if not want_min and self._min_value_ch is not None:
            self.remove_output_channel(self._min_value_ch)
            self.remove_output_channel(self._min_bin_ch)
            self._min_value_ch = None
            self._min_bin_ch   = None

        # --- max pair ---
        if want_max and self._max_value_ch is None:
            self._max_value_ch = self.add_output_channel("max_value")
            self._max_bin_ch   = self.add_output_channel("max_bin")

        if not want_max and self._max_value_ch is not None:
            self.remove_output_channel(self._max_value_ch)
            self.remove_output_channel(self._max_bin_ch)
            self._max_value_ch = None
            self._max_bin_ch   = None

        # --- configure present channels ---
        if self._min_value_ch is not None:
            self._min_value_ch.default_name = name + "_min_Value"
            self._min_value_ch.set_sample_format(
                api.SampleOccurrence.ASYNC, api.SampleFormat.DOUBLE, 1)
            self._min_value_ch.deletable = True
            self._min_value_ch.range     = input_range
            self._min_value_ch.unit      = unit

        if self._min_bin_ch is not None:
            self._min_bin_ch.default_name = name + "_min_Bin"
            self._min_bin_ch.set_sample_format(
                api.SampleOccurrence.ASYNC, api.SampleFormat.FLOAT, 1)
            self._min_bin_ch.deletable = True

        if self._max_value_ch is not None:
            self._max_value_ch.default_name = name + "_max_Value"
            self._max_value_ch.set_sample_format(
                api.SampleOccurrence.ASYNC, api.SampleFormat.DOUBLE, 1)
            self._max_value_ch.deletable = True
            self._max_value_ch.range     = input_range
            self._max_value_ch.unit      = unit

        if self._max_bin_ch is not None:
            self._max_bin_ch.default_name = name + "_max_Bin"
            self._max_bin_ch.set_sample_format(
                api.SampleOccurrence.ASYNC, api.SampleFormat.FLOAT, 1)
            self._max_bin_ch.deletable = True


# ======================================================================
#  Plugin
# ======================================================================

class BinDetectorPlugin(fw.PySoftwareChannelPlugin):

    def get_software_channel_info(self) -> api.RegisterSoftwareChannel:
        info = api.RegisterSoftwareChannel()
        info.service_name        = "DetectMinMaxBinsPy"
        info.display_name        = "Example Plugin in Python: Bin detector"
        info.display_group       = "Basic Math"
        info.description         = "Detect min/max values and bin indices of a vector channel"
        info.short_name          = "BIN"
        info.acquisition_capable = True
        info.analysis_capable    = True
        info.is_licensed         = True
        return info

    def register_resources(self) -> None:
        self.add_translation(TRANSLATION_EN)

    def validate_input_channels(self, input_channels, invalid_channels) -> bool:
        return len(invalid_channels) == 0

    def create_instance(self) -> BinDetectorInstance:
        return BinDetectorInstance()


# ======================================================================
#  Entry point
# ======================================================================

if __name__ == "__main__":
    import sys
    endpoint = sys.argv[1] if len(sys.argv) > 1 else "tcp://127.0.0.1:9336"
    logging.basicConfig(level=logging.DEBUG)
    py_oxygen_sdk.run(PLUGIN_NAME, BinDetectorPlugin(), endpoint)
	