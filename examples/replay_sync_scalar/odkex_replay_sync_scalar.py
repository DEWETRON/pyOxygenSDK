# Copyright DEWETRON GmbH 2026
# Python port of odkex_replay_sync_scalar.cpp
#
# Reads a CSV file of numeric rows and replays one column as a looping
# synchronous DOUBLE channel.  A custom QML request (messageId=1,
# name="checkCSVFile") validates a candidate file without creating a
# channel, matching the behaviour of the original C++ plugin.

from __future__ import annotations

import csv
import logging
import math
from pathlib import Path
from typing import List, Optional

import py_oxygen_sdk
import py_oxygen_sdk.api as api
import py_oxygen_sdk.framework as fw

log = logging.getLogger("replay_sync_scalar")
SCRIPT_DIR = Path(__file__).resolve().parent

PLUGIN_NAME = "ODKEX_REPLAY_SYNC_SCALAR_PY"

KEY_INPUT_FILE   = "ODKEX_REPLAY_SYNC_SCALAR_PY/InputFile"
KEY_COLUMN_INDEX = "ODKEX_REPLAY_SYNC_SCALAR_PY/ColumnIndex"

TRANSLATION_EN = """<?xml version="1.0"?>
<TS version="2.1" language="en" sourcelanguage="en">
    <context><name>ConfigKeys</name>
        <message>
            <source>ODKEX_REPLAY_SYNC_SCALAR_PY/InputFile</source>
            <translation>Input File</translation>
        </message>
        <message>
            <source>ODKEX_REPLAY_SYNC_SCALAR_PY/ColumnIndex</source>
            <translation>Column Index</translation>
        </message>
    </context>
    <context><name>ODKEX_REPLAY_SYNC_SCALAR_PY/AddChannel</name>
        <message>
            <source>Not a valid CSV file</source>
            <translation>No valid CSV file selected</translation>
        </message>
    </context>
</TS>"""


# -----------------------------------------------------------------------
#  CSV helper
# -----------------------------------------------------------------------

def _parse_csv(path: Path) -> List[List[float]]:
    """Return a list of rows, each row a list of floats.  Returns [] on error."""
    result: List[List[float]] = []
    try:
        with path.open(newline="") as fh:
            for row in csv.reader(fh):
                nums: List[float] = []
                for cell in row:
                    cell = cell.strip()
                    if cell:
                        nums.append(float(cell))
                if nums:
                    result.append(nums)
    except Exception as exc:
        log.error("CSV parse error in '%s': %s", path, exc)
        return []
    return result


# -----------------------------------------------------------------------
#  Channel instance
# -----------------------------------------------------------------------

class ReplaySyncScalarInstance(fw.PySoftwareChannelInstance):

    def __init__(self) -> None:
        super().__init__()

        self._input_file = fw.EditableFilePathProperty(
            fw.FileType.INPUT_FILE,
            "",                              # initial filename
            "Select CSV File",               # dialog title
            "",                              # default path
            ["CSV Files (*.csv)"],
        )
        self._input_file.visibility = "PUBLIC"

        self._column_index = fw.EditableUnsignedProperty(0, 0, 0)
        self._column_index.visibility = "PUBLIC"

        # sample_rate_property is managed by the framework via setSamplerate;
        # we retrieve it after create() has been called.
        self._sample_rate: Optional[fw.EditableScalarProperty] = None

        self._values: List[float] = []
        self._next_tick: int = 0

    # ------------------------------------------------------------------
    # create  — called once when a fresh instance is constructed
    # ------------------------------------------------------------------
    def create(self, host) -> None:
        ch = self.root_channel
        ch.default_name = "Replay channel"
        ch.set_sample_format(
            api.SampleOccurrence.SYNC,
            api.SampleFormat.DOUBLE,
            1,
        )
        ch.sample_rate = api.Scalar(1000.0, "Hz")

        # Retrieve the framework-managed sample-rate property and configure it.
        self._sample_rate = ch.sample_rate_property
        self._sample_rate.set_min_max_constraint(0.01, 10_000_000.0)
        self._sample_rate.add_option(1.0)
        self._sample_rate.add_option(100.0)
        self._sample_rate.add_option(1000.0)

        ch.set_simple_timebase(self._sample_rate.value.value)
        ch.deletable = True

        ch.add_property(KEY_INPUT_FILE,   self._input_file)
        ch.add_property(KEY_COLUMN_INDEX, self._column_index)

    # ------------------------------------------------------------------
    # init  — called after create() for a brand-new channel
    # ------------------------------------------------------------------
    def init(self, params: fw.InitParams) -> fw.InitResult:
        # Accept an optional pre-selected CSV file passed via QML properties.
        for name, value in params.properties:
            if name == f"{PLUGIN_NAME}/CSVFile":
                self._input_file.filename = str(value)
                break

        # Run update() so the channel validity is established immediately.
        self.update()

        result = fw.InitResult(True)
        result.show_channel_details(self.root_channel.local_id)
        return result

    # ------------------------------------------------------------------
    # configure  — called when a saved setup is loaded
    # ------------------------------------------------------------------
    def configure(self, request, channel_id_map: dict) -> bool:
        self.configure_from_telegram(request, channel_id_map)
        return True

    # ------------------------------------------------------------------
    # update  — called on every config change
    # ------------------------------------------------------------------
    def update(self) -> bool:
        path = Path(self._input_file.filename)
        rows = _parse_csv(path) if path.is_file() else []

        self._values = []

        if not rows:
            self.root_channel.valid = False
            self.root_channel.range_property.live = False
            return False

        num_cols = max(len(r) for r in rows)
        # Clamp column index to valid range.
        col = int(self._column_index.value)
        # Update the allowed range now that we know the column count.
        self._column_index.set_min_max_constraint(0, max(0, num_cols - 1))
        col = min(col, num_cols - 1)

        range_min =  math.inf
        range_max = -math.inf

        for row in rows:
            val = row[col] if col < len(row) else math.nan
            self._values.append(val)
            if not math.isnan(val):
                range_min = min(range_min, val)
                range_max = max(range_max, val)

        is_valid = range_min <= range_max

        ch = self.root_channel
        ch.range = api.Range(range_min, range_max, "", "")
        ch.valid = is_valid
        ch.range_property.live = is_valid

        if self._sample_rate is not None:
            ch.set_simple_timebase(self._sample_rate.value.value)

        return is_valid

    # ------------------------------------------------------------------
    # update_input_channel_ids  — no input channels; nothing to do
    # ------------------------------------------------------------------
    def update_input_channel_ids(self, channel_mapping: dict) -> None:
        pass

    # ------------------------------------------------------------------
    # prepare_processing  — compute the first tick to emit
    # ------------------------------------------------------------------
    def prepare_processing(self, host) -> None:
        if self._sample_rate is None or not self._values:
            return
        ts = host.get_master_timestamp()
        rate_factor = self._sample_rate.value.value / ts.frequency
        self._next_tick = int(ts.ticks * rate_factor)

    # ------------------------------------------------------------------
    # process  — emit contiguous blocks of samples
    # ------------------------------------------------------------------
    def process(self, ctx: fw.ProcessingContext, host) -> None:
        if not self._values or self._sample_rate is None:
            return

        ch_local   = self.root_channel
        ts         = host.get_master_timestamp()
        rate       = self._sample_rate.value.value
        n_values   = len(self._values)

        rate_factor  = rate / ts.frequency
        target_tick  = int(ts.ticks * rate_factor)

        tick = self._next_tick
        while tick < target_tick:
            # Index into the looping value array.
            idx = tick % n_values
            # How many samples can we send in one contiguous block?
            # Limited by: (a) end of the value array, (b) target_tick.
            max_from_array = n_values - idx
            max_to_target  = target_tick - tick
            count = min(max_from_array, max_to_target)

            import array as _array
            block = _array.array("d", self._values[idx : idx + count])
            host.add_samples(ch_local, tick, block)

            tick += count

        self._next_tick = tick

    # ------------------------------------------------------------------
    # stop_processing
    # ------------------------------------------------------------------
    def stop_processing(self, host) -> None:
        self._next_tick = 0


# -----------------------------------------------------------------------
#  Plugin
# -----------------------------------------------------------------------

class ReplaySyncScalarPlugin(fw.PySoftwareChannelPlugin):

    def __init__(self) -> None:
        super().__init__()
        # Set up a custom request handler so QML can validate CSV files
        # before a channel is created (matches checkCSVFile in C++ original).
        self._custom = fw.CustomRequestHandler()
        self._custom.register_function(1, "checkCSVFile", self._check_csv_file)
        self.add_custom_request_handler(self._custom)

    # ------------------------------------------------------------------
    # Plugin metadata
    # ------------------------------------------------------------------
    def get_software_channel_info(self) -> api.RegisterSoftwareChannel:
        info = api.RegisterSoftwareChannel()
        info.service_name        = "CreateChannel"
        info.display_name        = "PYOXY: Simple file replay"
        info.display_group       = "Data Input"
        info.description         = (
            "Adds a synchronous channel that delivers samples read from a CSV file."
        )
        info.ui_item_add         = "AddReplaySyncScalarChannel";
        info.short_name          = "Py Simp Repl"
        info.acquisition_capable = True
        info.analysis_capable    = False
        info.is_licensed         = True
        return info

    def register_resources(self) -> None:
        self.add_translation(TRANSLATION_EN)
        rcc_path = SCRIPT_DIR / "res.rcc";
        with open(rcc_path, "rb") as f:
            res = f.read()
            self.add_qt_resources(res)

    def validate_input_channels(self, input_channels, invalid_channels) -> bool:
        # This plugin creates a generator channel and takes no input.
        return True

    def create_instance(self) -> ReplaySyncScalarInstance:
        return ReplaySyncScalarInstance()

    # ------------------------------------------------------------------
    # Custom request handler (called from QML via CustomPluginRequest)
    # ------------------------------------------------------------------
    def _check_csv_file(self, params: fw.PropertyList) -> fw.PropertyList:
        """
        QML calls this with params containing "filename" (string).
        Returns a PropertyList with a bool "valid".
        """
        try:
            filename = params.get_by_name("filename").value
        except Exception:
            filename = ""

        path = Path(str(filename))
        rows = _parse_csv(path) if path.is_file() else []
        is_valid = len(rows) > 0

        result = api.PropertyList()
        result.append(api.Property("valid", is_valid))
        return result


# -----------------------------------------------------------------------
#  Entry point
# -----------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    endpoint = sys.argv[1] if len(sys.argv) > 1 else "tcp://127.0.0.1:9336"
    logging.basicConfig(level=logging.DEBUG)
    py_oxygen_sdk.run(PLUGIN_NAME, ReplaySyncScalarPlugin(), endpoint)
