# Copyright DEWETRON GmbH 2026
# Python port of odkex_replay_message.cpp
#
# Reads a simple CSV file whose rows are:
#   <time_seconds>,<hex_byte0> <hex_byte1> ...
# and replays its contents as a looping ASYNC binary channel.
#
# Example CSV (CAN frame at t=0.001 s):
#   0.001,00 00 00 08 12 34 56 78
#
# The channel type can be changed at runtime; the supported types and
# their fixed payload sizes mirror the C++ original.

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Optional

import py_oxygen_sdk
import py_oxygen_sdk.api as api
import py_oxygen_sdk.framework as fw

log = logging.getLogger("replay_message")

PLUGIN_NAME = "ODKEX_REPLAY_MESSAGE_PY"

KEY_INPUT_FILE   = "ODKEX_REPLAY_MESSAGE_PY/InputFile"
KEY_CHANNEL_TYPE = "ODKEX_REPLAY_MESSAGE_PY/ChannelType"
# Neon uses a different key for the logical channel-type classification
KEY_NEON_TYPE    = "ChannelType"

SELECT_INPUT_FILE = "ODKEX_REPLAY_MESSAGE_PY/SelectInputFile"

TRANSLATION_EN = """<?xml version="1.0"?>
<TS version="2.1" language="en" sourcelanguage="en">
    <context><name>ConfigKeys</name>
        <message><source>ODKEX_REPLAY_MESSAGE_PY/InputFile</source><translation>Input File</translation></message>
        <message><source>ODKEX_REPLAY_MESSAGE_PY/ChannelType</source><translation>Channel Type</translation></message>
    </context>
    <context><name>ConfigValues</name>
        <message><source>ODKEX_REPLAY_MESSAGE_PY/SelectInputFile</source><translation>Select Input File</translation></message>
    </context>
</TS>"""

# Maps channel-type name -> (SampleFormat, fixed_payload_bytes)
# fixed_payload_bytes == 0 means variable-length (CAN, FLEXRAY).
SUPPORTED_TYPES: dict[str, tuple[api.SampleFormat, int]] = {
    "ARINC_429":   (api.SampleFormat.BYTE,             4),
    "MILSTD_1553": (api.SampleFormat.BYTE,            74),
    "CAN":         (api.SampleFormat.CAN_MESSAGE,       0),
    "FLEXRAY":     (api.SampleFormat.FLEXRAY_MESSAGE,   0),
}

TIMEBASE_FREQUENCY = 1_000_000.0   # 1 MHz, same as C++ original


def _parse_csv(path: Path) -> dict[int, bytes]:
    """Return {tick: payload_bytes} sorted by tick."""
    result: dict[int, bytes] = {}
    try:
        with path.open(newline="") as fh:
            first_row = True
            for row in csv.reader(fh):
                if len(row) < 2:
                    continue
                try: 
                    time_s = float(row[0].strip())
                except:
                    if first_row: #ignore header line if present
                        continue
                    raise
                first_row = False
                raw    = bytes(int(b, 16) for b in row[1].strip().split())
                tick   = int(time_s * TIMEBASE_FREQUENCY)
                result[tick] = raw
    except Exception as exc:
        log.error("CSV parse error: %s", exc)
    return result


class ReplayMessageInstance(fw.PySoftwareChannelInstance):

    def __init__(self):
        super().__init__()

        self._input_file = fw.EditableFilePathProperty(
            fw.FileType.INPUT_FILE,
            "",                         # initial filename
            SELECT_INPUT_FILE,          # dialog title key
            "",                         # default path
            ["Supported Files (*.csv)"],
        )
        self._input_file.visibility = "PUBLIC"

        # Build channel-type selector with all supported options
        initial = api.Property(KEY_CHANNEL_TYPE, ("ARINC_429", "DAQChannelType"))
        self._channel_type = fw.SelectableProperty(initial)
        self._channel_type.visibility = "PUBLIC"
        for name in SUPPORTED_TYPES:
            self._channel_type.add_option(
                api.Property(KEY_CHANNEL_TYPE, (name, "DAQChannelType")))

        # Mirror that carries the logical Neon channel type classification
        self._neon_channel_type = fw.SelectableProperty(initial)

        self._values:         dict[int, bytes] = {}
        self._file_duration:  int = 0
        self._next_tick:      int = 0

    # ------------------------------------------------------------------
    # create
    # ------------------------------------------------------------------
    def create(self, host) -> None:
        ch = self.root_channel
        ch.default_name = "Replay channel"
        ch.set_sample_format(
            api.SampleOccurrence.ASYNC,
            api.SampleFormat.CAN_MESSAGE,
            1,
        )
        ch.set_simple_timebase(TIMEBASE_FREQUENCY)
        ch.deletable = True
        ch.add_property(KEY_INPUT_FILE,   self._input_file)
        ch.add_property(KEY_CHANNEL_TYPE, self._channel_type)
        ch.add_property(KEY_NEON_TYPE,    self._neon_channel_type)

    # ------------------------------------------------------------------
    # init
    # ------------------------------------------------------------------
    def init(self, params: fw.InitParams) -> fw.InitResult:
        return fw.InitResult(True)

    # ------------------------------------------------------------------
    # configure  (called when loading a saved setup)
    # ------------------------------------------------------------------
    def configure(self, request, channel_id_map: dict) -> bool:
        # Analysis mode is not supported for a live-replay channel
        # (requires host.is_analysis_mode_active() added by patch 3)
        self.configure_from_telegram(request, channel_id_map)
        return True

    # ------------------------------------------------------------------
    # update
    # ------------------------------------------------------------------
    def update(self) -> bool:
        # Sync logical channel type with selected type
        self._neon_channel_type.value = self._channel_type.value

        type_name = self._channel_type.value.value[0]   # tuple (str, enum_type)
        if type_name not in SUPPORTED_TYPES:
            self.root_channel.valid = False
            return False

        fmt, max_size = SUPPORTED_TYPES[type_name]
        self.root_channel.set_sample_format(api.SampleOccurrence.ASYNC, fmt, max_size)
        self.root_channel.set_simple_timebase(TIMEBASE_FREQUENCY)

        # Re-parse the file on every config change (matches C++ behaviour)
        path = Path(self._input_file.filename)
        self._values        = {}
        self._file_duration = 0

        if path.is_file():
            raw = _parse_csv(path)
            for tick, payload in raw.items():
                clipped = payload[:max_size] if max_size else payload
                self._values[tick]  = clipped
                self._file_duration = max(self._file_duration, tick + 1)

        is_valid = self._file_duration > 0
        self.root_channel.valid = is_valid
        return is_valid

    # ------------------------------------------------------------------
    # update_input_channel_ids  (nothing to do; no input channels)
    # ------------------------------------------------------------------
    def update_input_channel_ids(self, channel_mapping: dict) -> None:
        pass

    # ------------------------------------------------------------------
    # prepare_processing
    # ------------------------------------------------------------------
    def prepare_processing(self, host) -> None:
        ts = host.get_master_timestamp()
        rate_factor    = TIMEBASE_FREQUENCY / ts.frequency
        self._next_tick = int(ts.ticks * rate_factor)

    # ------------------------------------------------------------------
    # process
    # ------------------------------------------------------------------
    def process(self, ctx: fw.ProcessingContext, host) -> None:
        if self._file_duration == 0:
            return

        ch = self.root_channel
        ts = host.get_master_timestamp()
        rate_factor  = TIMEBASE_FREQUENCY / ts.frequency
        target_tick  = int(ts.ticks * rate_factor)

        tick = self._next_tick
        while tick < target_tick:
            rel_base = tick // self._file_duration
            rel_tick = tick  % self._file_duration

            # Find the next scheduled sample at or after rel_tick
            future = {t: v for t, v in self._values.items() if t >= rel_tick}
            if not future:
                # No more samples in this loop iteration; jump to next cycle
                tick = min(target_tick, (rel_base + 1) * self._file_duration)
            else:
                next_rel = min(future)
                abs_tick = rel_base * self._file_duration + next_rel
                payload  = future[next_rel]
                host.add_sample(ch, abs_tick, bytes(payload))
                tick = abs_tick + 1

        self._next_tick = tick

    # ------------------------------------------------------------------
    # stop_processing
    # ------------------------------------------------------------------
    def stop_processing(self, host) -> None:
        pass


# ======================================================================
#  Plugin
# ======================================================================

class ReplayMessagePlugin(fw.PySoftwareChannelPlugin):

    def get_software_channel_info(self) -> api.RegisterSoftwareChannel:
        info = api.RegisterSoftwareChannel()
        info.service_name        = "CreateReplayChannelPy"
        info.display_name        = "PYOXY: Simple message file replay"
        info.display_group       = "Data Input"
        info.description         = (
            "Adds a message channel that delivers samples read from a CSV file."
        )
        info.short_name          = "Py Mess Repl"
        info.acquisition_capable = True
        info.analysis_capable    = False
        info.is_licensed         = True
        return info

    def register_resources(self) -> None:
        self.add_translation(TRANSLATION_EN)

    def validate_input_channels(self, input_channels, invalid_channels) -> bool:
        # This plugin takes no input channels
        return True

    def create_instance(self) -> ReplayMessageInstance:
        return ReplayMessageInstance()


# ======================================================================
#  Entry point
# ======================================================================

if __name__ == "__main__":
    import sys
    endpoint = sys.argv[1] if len(sys.argv) > 1 else "tcp://127.0.0.1:9336"
    logging.basicConfig(level=logging.DEBUG)
    py_oxygen_sdk.run(PLUGIN_NAME, ReplayMessagePlugin(), endpoint)