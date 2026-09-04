# Copyright DEWETRON GmbH 2026
# Python port of odkex_sum_channels.cpp

from __future__ import annotations

import math
import logging
from typing import Optional

import py_oxygen_sdk
import py_oxygen_sdk.api as api
import py_oxygen_sdk.framework as fw
import numpy as np

log = logging.getLogger("sum_channels")

PLUGIN_NAME    = "ODK_SUM_CHANNELS_PY"
KEY_INPUT_CHANNELS = "ODK_SUM_CHANNELS_PY/MyInputChannels"
KEY_CALC_MODE      = "ODK_SUM_CHANNELS_PY/Mode"


TRANSLATION_EN = """<?xml version="1.0"?>
<TS version="2.1" language="en" sourcelanguage="en">
    <context><name>ConfigKeys</name>
        <message><source>ODK_SUM_CHANNELS_PY/MyInputChannels</source><translation>Input Channels</translation></message>
        <message><source>ODK_SUM_CHANNELS_PY/Mode</source><translation>Mode</translation></message>
    </context>
</TS>""";

TRANSLATION_DE = """<?xml version="1.0"?>
<TS version="2.1" language="de" sourcelanguage="en">
    <context><name>ConfigKeys</name>
        <message><source>ODK_SUM_CHANNELS_PY/MyInputChannels</source><translation>Eingangskan<byte value="xe4"/>le</translation></message>
        <message><source>ODK_SUM_CHANNELS_PY/Mode</source><translation>Modus</translation></message>
    </context>
</TS>""";

class SumChannelsInstance(fw.PySoftwareChannelInstance):

    def __init__(self):
        super().__init__()

        self.m_input_channels = fw.EditableChannelIDListProperty()
        self.m_input_channels.visibility = "PUBLIC"

        self.m_calculation_mode = fw.SelectableProperty(
            api.Property(KEY_CALC_MODE, ("Sum", ""))
        )
        self.m_calculation_mode.visibility = "PUBLIC"
        self.m_calculation_mode.add_option(api.Property(KEY_CALC_MODE, ("Sum", "")))
        self.m_calculation_mode.add_option(api.Property(KEY_CALC_MODE, ("Difference", "")))

        self._timebase_frequency = 0.0
        self._current_values = [math.nan, math.nan]
        self._resampling_enabled = False

    # ------------------------------------------------------------------
    # create  -  called once after the instance is constructed
    # ------------------------------------------------------------------
    def create(self, host: fw.PyHost) -> None:
        ch = self.root_channel
        ch.default_name = "SumChannel"
        ch.set_sample_format(
            api.SampleOccurrence.SYNC,
            api.SampleFormat.DOUBLE,
            1
        )
        ch.deletable = True
        ch.valid = True
        ch.add_property(KEY_INPUT_CHANNELS, self.m_input_channels)
        ch.add_property(KEY_CALC_MODE, self.m_calculation_mode)

    # ------------------------------------------------------------------
    # init  -  called when the user creates a new instance from the GUI
    # ------------------------------------------------------------------
    def init(self, params: fw.InitParams) -> fw.InitResult:
        channel_ids = list(params.input_channel_ids)
        self.m_input_channels.value = channel_ids

        result = fw.InitResult(True)
        result.show_channel_details(self.root_channel.local_id)
        return result

    # ------------------------------------------------------------------
    # configure  -  called when loading a saved setup
    # ------------------------------------------------------------------
    def configure(self, request, channel_id_map: dict) -> bool:
        self.configure_from_telegram(request, channel_id_map)
        return True

    # ------------------------------------------------------------------
    # update  -  called on every configuration change
    # ------------------------------------------------------------------
    def update(self) -> bool:
        unit = ""
        range_min = 0.0
        range_max = 0.0
        sample_rate_max = 0.0

        for proxy in self.get_input_channel_proxies():
            r = proxy.range
            range_min += r.min_value
            range_max += r.max_value

            if not unit:
                unit = proxy.unit

            sr = proxy.sample_rate
            sample_rate_max = max(sample_rate_max, sr.value)

        if sample_rate_max == 0.0:
            sample_rate_max = 100.0

        ch = self.root_channel
        ch.range = api.Range(range_min, range_max, unit)
        ch.unit = unit
        ch.set_simple_timebase(sample_rate_max)

        self._timebase_frequency = sample_rate_max
        self._update_needs_resampling()

        channel_ids = self.m_input_channels.value
        if len(channel_ids) != 2:
            return False

        is_valid = True
        sync_cnt = 0
        async_cnt = 0
        for ch_id in channel_ids:
            proxy = self.get_input_channel_proxy(ch_id)
            if proxy.update_data_format():
                fmt = proxy.data_format
                if fmt.sample_value_type != api.SampleValueType.SAMPLE_VALUE_SCALAR:
                    is_valid = False
                    break
                if fmt.sample_occurrence == api.SampleOccurrence.SYNC:
                    sync_cnt += 1
                elif fmt.sample_occurrence == api.SampleOccurrence.ASYNC:
                    async_cnt += 1
                else:
                    is_valid = False
                    break
        return is_valid

    # ------------------------------------------------------------------
    # update_input_channel_ids  -  called when channel IDs are remapped
    # ------------------------------------------------------------------
    def update_input_channel_ids(self, channel_mapping: dict) -> None:
        # Base class updates properties automatically; we only need to
        # remove IDs that became invalid (sentinel value max uint64).
        current_ids = self.m_input_channels.value
        valid_ids = [
            ch_id for ch_id in current_ids
            if ch_id != 0xFFFFFFFFFFFFFFFF
        ]
        self.m_input_channels.value = valid_ids

    # ------------------------------------------------------------------
    # init_timebases
    # ------------------------------------------------------------------
    def init_timebases(self, host) -> None:
        self._update_needs_resampling()

    # ------------------------------------------------------------------
    # prepare_processing  -  called once before acquisition starts
    # ------------------------------------------------------------------
    def prepare_processing(self, host) -> None:
        self._current_values = [math.nan, math.nan]

    # ------------------------------------------------------------------
    # process  -  hot path, called every processing block
    # ------------------------------------------------------------------
    def process(self, ctx: fw.ProcessingContext, host) -> None:
        ch_ids = self.m_input_channels.value
        if len(ch_ids) < 2:
            return

        start_sample = api.convert_time_to_tick_at_or_after(
            ctx.window_start, self._timebase_frequency
        )
        end_sample = api.convert_time_to_tick_at_or_after(
            ctx.window_end, self._timebase_frequency
        )
        n_samples = end_sample - start_sample
        if n_samples <= 0:
            return

        proxy_a = self.get_input_channel_proxy(ch_ids[0])
        proxy_b = self.get_input_channel_proxy(ch_ids[1])

        # m_calculation_mode.value returns a PropertyAdapter wrapping an ENUM
        # property.  PropertyAdapter.getValue() returns a (value_str, type_str)
        # tuple for ENUM types (see py_property_adapter.cpp: value_to_py).
        mode_prop = self.m_calculation_mode.value   # -> PropertyAdapter
        mode_tuple = mode_prop.value                # -> ("Sum", "ODK_SUM_CHANNELS/Mode")
        compute_sum = (mode_tuple[0] == "Sum")

        if not self._resampling_enabled:
            # Fast path: channels share the same sample rate.
            # Use get_samples() rather than zipping blocks, because two
            # channels at the same rate can still deliver different block
            # boundaries within a single processing window.
            samples_a = ctx.get_samples(ch_ids[0], proxy_a)
            samples_b = ctx.get_samples(ch_ids[1], proxy_b)

            out_len = min(len(samples_a), len(samples_b))
            if out_len <= 0:
                return

            result = (
                samples_a[:out_len] + samples_b[:out_len]
                if compute_sum
                else samples_a[:out_len] - samples_b[:out_len]
            )
            host.add_samples(self.root_channel, start_sample, result)
        else:
            # Resampling path: each input may have a different sample rate or
            # be ASYNC, so the number of available samples per channel has no
            # fixed relationship to n_samples.  We resample each channel onto
            # the uniform output timeline by holding the last known value
            # forward (zero-order hold), matching the C++ iterator approach.
            n = int(n_samples)
            if n <= 0:
                return

            def resample_to_output(ch_id, proxy) -> np.ndarray:
                """Map available samples onto the n output ticks via zero-order hold."""
                out = np.full(n, math.nan)
                blocks = ctx.get_blocks(ch_id, proxy)
                last_val = math.nan
                out_idx  = 0
                freq_in  = proxy.time_base.frequency   # requires time_base binding

                for samples, ticks in blocks:
                    for s_idx in range(len(samples)):
                        # Convert input tick to output-rate tick index
                        input_time  = float(ticks[s_idx]) / freq_in
                        output_tick = api.convert_time_to_tick_at_or_after(
                            input_time, self._timebase_frequency)
                        fill_end = min(int(output_tick) - int(start_sample), n)

                        # Fill output slots before this sample with the previous value
                        while out_idx < fill_end:
                            out[out_idx] = last_val
                            out_idx += 1

                        last_val = float(samples[s_idx])

                # Fill any remaining output slots with the last value
                while out_idx < n:
                    out[out_idx] = last_val
                    out_idx += 1

                return out

            vec_a = resample_to_output(ch_ids[0], proxy_a)
            vec_b = resample_to_output(ch_ids[1], proxy_b)

            result = vec_a + vec_b if compute_sum else vec_a - vec_b
            host.add_samples(self.root_channel, start_sample, result)

    # ------------------------------------------------------------------
    # stop_processing
    # ------------------------------------------------------------------
    def stop_processing(self, host) -> None:
        pass

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------
    def _update_needs_resampling(self) -> None:
        for proxy in self.get_input_channel_proxies():
            proxy.update_time_base()
            tb  = proxy.time_base
            fmt = proxy.data_format
            if (fmt.sample_occurrence != api.SampleOccurrence.SYNC
                    or tb.frequency != self._timebase_frequency):
                self._resampling_enabled = True
                return
        self._resampling_enabled = False


# ======================================================================
#  Plugin
# ======================================================================

class SumChannelsPlugin(fw.PySoftwareChannelPlugin):

    def get_software_channel_info(self) -> api.RegisterSoftwareChannel:
        info = api.RegisterSoftwareChannel()
        info.service_name        = "AddSyncAsyncPy"
        #info.service_name        = "AddSyncAsync"
        info.display_name        = "Python: Sum channels"
        info.display_group       = "Basic Math"
        info.description         = "Adds a channel that calculates the sum of two input channels."
        info.short_name          = "SUM"
        info.acquisition_capable = True
        info.analysis_capable    = True
        #info.is_licensed         = True
        return info

    def register_resources(self) -> None:
        # Translations are not yet exposed via pyoxy.
        # Uncomment when add_translation() is available on the plugin:
        self.add_translation(TRANSLATION_EN)
        self.add_translation(TRANSLATION_DE)
        pass

    def validate_input_channels(self, input_channels, invalid_channels) -> bool:
        return len(invalid_channels) == 0

    def create_instance(self) -> SumChannelsInstance:
        return SumChannelsInstance()


# ======================================================================
#  Entry point
# ======================================================================

if __name__ == "__main__":
    import sys
    endpoint = sys.argv[1] if len(sys.argv) > 1 else "tcp://127.0.0.1:9336"
    logging.basicConfig(level=logging.DEBUG)
    py_oxygen_sdk.run(PLUGIN_NAME, SumChannelsPlugin(), endpoint)
	