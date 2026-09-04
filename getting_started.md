
# Getting Started with pyOxygenSDK

This guide is for Python developers who want to write an Oxygen plugin
using pyOxygenSDK and have little or no C++ background. You do not need to
read or write any C++ to use pyOxygenSDK. You will occasionally need to *read*
a C++ header file as documentation (more on that below), but never
compile or write any C++ code yourself.

## 1. Who this is for

You already know Python. You may never have developed for Oxygen  before,
and terms like "channel", "telegram", or "sample format" may be new to you.
This guide introduces those concepts as they come up.

## 2. The big picture

Oxygen is the host application: a running program that acquires,
processes, and displays measurement data (voltages, temperatures, CAN bus
traffic, and so on) coming from DEWETRON hardware or from other sources.

A plugin is a separate program that adds new functionality to Oxygen,
most commonly a new calculated channel: something that reads one or more
existing channels and produces a new one (a sum, an average, a filtered
signal, and so on).

With pyOxygenSDK, your plugin is an ordinary Python script. When you run it, it
opens a network connection (usually a local TCP connection) to an
already-running Oxygen instance, introduces itself, and then waits for
Oxygen to ask it to do things: create a channel, update its
configuration, process a block of samples, and so on. pyOxygenSDK handles the
connection and message exchange for you; you only write the Python
methods that respond to those requests.

You do not need to understand the network protocol itself. py_oxygen_sdk's
`run()` function does that part. Your job is to write two Python classes:

- one that describes a single instance of your calculated channel
  (`py_oxygen_sdk.framework.PySoftwareChannelInstance`), and
- one that describes your plugin as a whole, mainly "what is this
  plugin called, and how do I create a new instance of it"
  (`py_oxygen_sdk.framework.PySoftwareChannelPlugin`).

## 3. Vocabulary

A short glossary of terms you will run into. Skip ahead and come back to
this section as needed.

- **Host**: the running Oxygen application your plugin connects to.
- **Channel**: a named stream of measurement data, e.g. "Voltage 1" or
  your own calculated channel. Every channel has a data type (see "sample
  format" below), a unit, a range, and a sample rate.
- **Sample**: one data point in a channel, tagged with a timestamp.
- **Sample format**: the type of each sample's value: a 64-bit float
  (`DOUBLE`), a 32-bit integer, a UTF-8 string, and so on.
- **Sample occurrence**: whether samples arrive at a fixed rate
  (`SYNC`, e.g. "every 1 millisecond") or irregularly, whenever an event
  happens (`ASYNC`, e.g. a CAN bus message).
- **Property** (also called a config item): a named setting on a channel,
  such as its name, unit, or (for your plugin) a user-chosen input
  channel or calculation mode. Properties can be read-only (reporting
  something) or editable (the user can change them in the Oxygen UI).
- **Instance**: one "copy" of your plugin's calculated channel. If a user
  adds your "Sum Channel" calculation to their setup twice, with
  different input channels each time, that is two instances.
- **Tick / timebase**: internally, sample timestamps are counted in
  integer "ticks" rather than fractional seconds, to avoid rounding
  errors. A timebase tells you how many ticks correspond to one second
  (the frequency) and, for some channels, an additional time offset.
  `py_oxygen_sdk.api.convert_time_to_tick_at_or_after(...)` and
  `py_oxygen_sdk.api.convert_tick_to_time(...)` convert between the two.
- **Telegram**: an internal, XML-based message Oxygen and your plugin
  exchange, e.g. "here is the new configuration" or "here is a block of
  samples". You will see this word in some class names
  (`UpdateChannelsTelegram`) and in the C++ header documentation, but you
  never construct or parse XML yourself; pyOxygenSDK converts these to and
  from ordinary Python objects for commonly used functionality.

## 4. Installing

py_oxygen_sdk is a compiled Python extension, built separately for each
supported Python minor version (3.12 and newer). Install the wheel that
matches your interpreter:

    pip install py_oxygen_sdk

Check that the import works before writing any plugin code:

    python -c "import py_oxygen_sdk; print(py_oxygen_sdk.__file__)"

If this fails, fix that first; nothing else in this guide will work
until a plain `import py_oxygen_sdk` succeeds.

## 5. Your first plugin, step by step

We will build a tiny plugin that creates one output channel and fills it
with a constant value. It does nothing useful on its own, but it shows
every piece you need before moving on to something real.

### Step 1: the instance class

Create a file `hello_channel.py`:

```python
import py_oxygen_sdk
import py_oxygen_sdk.api as api
import py_oxygen_sdk.framework as fw


class HelloInstance(fw.PySoftwareChannelInstance):

    def create(self, host):
        # Called once, right after this instance is created.
        # self.root_channel is the one output channel every instance
        # starts out with.
        channel = self.root_channel
        channel.default_name = "HelloChannel"
        channel.set_sample_format(
            api.SampleOccurrence.SYNC,
            api.SampleFormat.DOUBLE,
            1)
        channel.deletable = True

    def update(self):
        # Called whenever the configuration might need to be
        # re-validated (e.g. after the user changes a property).
        # Return True if the channel is in a valid, usable state.
        self.root_channel.set_simple_timebase(10.0)  # 10 Hz
        return True

    def configure(self, request, channel_id_map):
        # Called when a saved setup is being loaded. For a simple
        # plugin with a fixed channel layout, this one line is enough.
        self.configure_from_telegram(request, channel_id_map)
        return True

    def process(self, ctx, host):
        # Called repeatedly while acquisition is running. This is
        # where you read input data (if any) and write output data.
        host.add_sample(self.root_channel, ctx.master_timestamp.ticks, 42.0)
```

Every method above corresponds to something Oxygen asks your instance to
do at a specific moment: `create` once at the start, `update` whenever
configuration changes, `configure` when loading a saved setup, and
`process` continuously while measuring. You do not call these methods
yourself; pyOxygenSDK calls them for you at the right time.

### Step 2: the plugin class

```python
class HelloPlugin(fw.PySoftwareChannelPlugin):

    def get_software_channel_info(self):
        # Describes how this plugin appears in Oxygen's
        # "Add Channel" dialog.
        info = api.RegisterSoftwareChannel()
        info.service_name = "HelloChannelPy"
        info.display_name = "Hello: constant value channel"
        info.display_group = "Examples"
        info.description = "A minimal example plugin."
        return info

    def validate_input_channels(self, input_channels, invalid_channels):
        # This plugin does not use input channels, so always valid.
        return True

    def create_instance(self):
        return HelloInstance()
```

### Step 3: running the plugin

```python
if __name__ == "__main__":
    py_oxygen_sdk.run("HELLO_CHANNEL_PY", HelloPlugin(), "tcp://127.0.0.1:9336")
```

The first argument is a unique internal name for your plugin. The third
is the address Oxygen is listening on for plugin connections; check your
Oxygen installation's plugin/remote settings for the correct port if
9336 does not work.

### Step 4: using the plugin in Oxygen

Oxygen has to know some basic information about the plugin to make it
appear in the "Plugin Overview" section of the Setup page and to
allow communication with the script. This is done by storing an XML
file in one of the folders where Oxygen expects plugins.

For this example we can create the file hello_channel_py.plugin.xml
and save it in C:\Users\Public\Documents\Dewetron\Oxygen\Plugins

The content would look like that:
<?xml version="1.0"?>
<!-- OxygenPlugin.name: unique internal name of the plugin. Has to match the one used in the python script -->
<!-- OxygenPlugin.uuid: globally unique identifier for the plugin. Create a new one with generators like https://www.guidgen.com/ for every project -->
<OxygenPlugin
    name="HELLO_CHANNEL_PY"
    uuid="DBF5E498-FE28-476A-87F1-694F2DA369E2"
    version="0.1"
    >
  <Info name="A simple plugin implemented in Python"/> <!-- Name and other information that is shown to the user in the Plugin Overview. Can be localized. -->
  <Host minimum_version="8.1"/>
  <UsesUIExtensions/>
  <External>
    <!-- Start: Interpreter and commandline arguments to start the plugin. Oxygen will automatically do that if command is valid.
	            Alternatively you can also start the script from an IDE once Oxygen is running. -->
	<!-- Start.command: Path of the Python interpreter executable that should be used. -->
	<!-- Start.arguments: All commandline arguments, including scripts file and connection information, as well as venv setup etc if necessary. -->
    <Start
        command="C:\dev\Python312\python.exe"
        arguments="D:\projects\pyoxy\hello_channel.py #{PluginServerEndpoint}"
        show_window="true"
        >
      <Env inherit="true">
        PYTHONPATH=%PYTHONPATH%;D:\projects\pyoxy\
      </Env> <!-- setup path to ensure that py_oxygen_sdk module can be found if not installed in the current python environement -->
    </Start>
  </External>
</OxygenPlugin>

Start Oxygen and check if the plugin information is visible "Plugin Overview".
State Initializing... means that the manifest was properly processed and Oxygen waits for the plugin process to connect.
In State Ready there is an established connection and  the plugin should be selectable from
Oxygen's "Add Channel" dialog, under the group you named ("Examples").

## 6. Channels and properties in a bit more depth

Every plugin instance has one or more `PluginChannel` objects (accessible
as `self.root_channel`, or via `self.add_output_channel(...)` if you need
more than one). A channel has:

- `default_name` / `name`: how it is labeled.
- `unit`: e.g. `"V"`, `"degC"`, or `""` for unitless.
- `range`: the expected minimum/maximum value, as an `api.Range`.
- A sample format, set with `set_sample_format(occurrence, format, dimension)`.
- A timebase (for `SYNC` channels), set with `set_simple_timebase(frequency)`.

Properties are how a channel exposes settings to the user, and how your
plugin exposes settings to itself. There are several property classes in
`py_oxygen_sdk.framework`, matching the kind of value they hold:

```python
self.input_channel = fw.EditableChannelIDProperty()
self.input_channel.visibility = "PUBLIC"   # shows up in the Oxygen UI

self.window_size = fw.EditableUnsignedProperty(3, 1, 99)  # value, min, max
self.window_size.visibility = "PUBLIC"
```

Attach a property to a channel in `create()`:

```python
channel.add_property("MY_PLUGIN/WindowSize", self.window_size)
```

The first argument is a string key. Pick your own, but prefix it with
your plugin's name to avoid clashing with another plugin's keys (this is
a convention, not something pyOxygenSDK enforces). You will see the same
pattern in the C++ SDK's own examples, using `#define`d constants instead
of literal strings; in Python, a plain string is fine.

## 7. The processing loop

While Oxygen is acquiring data, your instance's `process(ctx, host)`
method is called repeatedly. `ctx` (a `ProcessingContext`) tells you what
time window is being processed right now
(`ctx.window_start`, `ctx.window_end`, in seconds) and gives you access to
your input channels' data. `host` lets you write output samples and query
the host application.

Three ways to read input data, roughly simplest to most flexible:

```python
# 1. One array covering the whole window. Safe to keep as long as you like.
samples = ctx.get_samples(channel_id, proxy)

# 2. Zero-copy, block by block. Fast, but only valid during this
#    process() call; call .copy() if you need to keep the data.
for samples, ticks in ctx.get_blocks(channel_id, proxy):
    ...

# 3. Sample by sample, with control over gaps. Most flexible, and the
#    right choice for algorithms with an explicit sliding window
#    (e.g. a moving average, see moving_average.py).
it = ctx.get_iterator(channel_id)
it.set_skip_gaps(False)
while it.valid:
    value = it.value()
    it.advance()
```

Writing output data:

```python
host.add_sample(channel, timestamp, value)               # a single sample
host.add_samples(channel, first_timestamp, samples)       # a contiguous block
```

`proxy` above is an `InputChannel` object, obtained with
`self.get_input_channel_proxy(channel_id)`. You get input channel ids
from a property (typically an `EditableChannelIDProperty` or
`EditableChannelIDListProperty`) that the user filled in through the
Oxygen UI when adding your channel.

For a complete, working example that reads two input channels and
combines them, see `sum_channels.py`. For a per-sample sliding-window
example, see `moving_average.py`.

## 8. Reading the SDK headers as documentation

pyOxygenSDK wraps a C++ SDK (the "ODK"), and some of that SDK's documentation
exists only as comments inside C++ header files, particularly:

- `odkapi_message_ids.inc` (message identifiers)
- `odkapi_oxygen_queries.h` (the `query()` / `get_value()` context and
  item strings)
- `odkapi_config_item_keys.h` (channel property key strings)

You do not need to know C++ syntax to read these files usefully. They are
almost entirely lists of named constants defined with C preprocessor
macros, and once you know the shape of each macro, the files read like a
plain reference table. This section explains how.

Also see the full, browsable C++ API reference at:

    https://dewetron.github.io/OXYGEN-SDK/index.html

That site documents the underlying C++ classes and functions pyOxygenSDK wraps.
Names differ (C++ `camelCase` becomes Python `snake_case`, and so on),
but the concepts, parameters, and behavior described there apply equally
to the Python bindings. If you are ever unsure what a pyOxygenSDK method
actually does under the hood, searching for its C++ counterpart there is
often the fastest way to find out.

### 8.1. Message ids

`odkapi_message_ids.inc` is not something you use directly from Python
very often (pyOxygenSDK's higher-level methods send these messages for you),
but it is useful background for understanding what is happening when you
call things like `host.query(...)`.

A typical line looks like this:

```cpp
MSG_ID(NOTIFY_CHANNEL_CONFIG_CHANGED, GENERAL_FUNCTIONS, 0x000509, "always 0",
    "<ChannelConfigChanged/> (oxy_plugin_common::ChannelConfigChangedTelegram) containing the changed properties",
    " ", "0: no error");
```

Read `MSG_ID(name, category, value, key, input, output, ret)` as a table
row with these columns:

- **name**: `NOTIFY_CHANNEL_CONFIG_CHANGED`, an internal identifier for
  this message.
- **category**: `GENERAL_FUNCTIONS`, which group of functionality it
  belongs to.
- **value**: `0x000509`, its numeric id (combined with the category's
  base value; not something you need to compute yourself).
- **key**: what the "key" parameter means for this specific message
  (here: "always 0", meaning it is unused).
- **input**: what data is sent along with the message, in this case an
  XML telegram matching a specific C++ class.
- **output**: what is sent back in response, if anything.
- **ret**: what the numeric return/error code means.

You will not send raw message ids from Python. Instead, look at which
pyOxygenSDK method already wraps a message you are interested in (for example,
`PyHost.update_channel_state()` wraps `UPDATE_CHANNEL_STATE`, and
`PyHost.log()` wraps `LOG_MESSAGE`). If you find a message in this file
with no matching pyOxygenSDK method, that functionality is not exposed yet;
see `PORTING_GUIDE.md`'s "What is not yet supported" section.

### 8.2. Queries: `odkapi_oxygen_queries.h`

This file is more directly useful, because `PyHost.query()` and
`PyHost.get_value()` take exactly the "context" and "item" strings
defined here.

A typical block looks like this:

```cpp
STATIC_CONTEXT( PluginHost, "#PluginHost", "Properties and functionality of the plugin host application (not necessarily oxygen)");

READ_ONLY_PROPERTY( PluginHost,     VendorName,         IfStringValue,      "Vendor of the host application");
READ_ONLY_PROPERTY( PluginHost,     Name,               IfStringValue,      "Name of the host application (without version)");
```

Read this as:

- `STATIC_CONTEXT(ContextName, "string value", "description")` defines a
  named context. The context string, `"#PluginHost"` here, is the first
  argument you pass to `query()`/`get_value()`.
- `READ_ONLY_PROPERTY(ContextName, PropertyName, ValueType, "description")`
  defines an item you can read within that context. `ValueType` tells you
  what kind of Python value to expect back (e.g. `IfStringValue` becomes
  a plain Python `str`; see `PORTING_GUIDE.md` section on value types for
  the less obvious ones).
- `READ_WRITE_PROPERTY(...)` is the same, but you may also write it via
  `query()` with a value argument.
- `WRITE_ONLY_PROPERTY(...)` can be set but not read back.

To read the host's vendor name from Python:

```python
vendor = host.get_value("#PluginHost", "VendorName")
```

Some contexts are parameterized, and the comment above them spells out
the pattern with a placeholder, for example:

```cpp
// #Oxygen#Channels#<ChId>|Name   get the name of the channel (IfStringValue; alias for corresponding config item)
```

Here `<ChId>` is a placeholder you fill in yourself with an actual
channel id:

```python
name = host.get_value("#Oxygen#Channels#" + str(channel_id), "Name")
```

(In practice, prefer `InputChannel.name` if you already have an
`InputChannel` proxy object; this manual `query()`/`get_value()` pattern
is for the less common cases nothing higher-level covers yet.)

### 8.3. Channel property (config item) keys: `odkapi_config_item_keys.h`

This file lists the standard property keys every channel supports, each
documented with a comment directly above its `#define`:

```cpp
/// User-specified name of the channel. Not necessarily unique.
/// Value: String
#define ODK_CI_KEY_CHANNEL_NAME "Neon/Name"

/// Indicates whether the channel should acquire data during acquisition
/// Value: Bool
#define ODK_CI_KEY_CHANNEL_ACTIVE "Neon/Active"
```

Read each entry as: comment describes what the property means and what
Python type its value has ("Value: String", "Value: Bool", and so on);
the quoted string after `#define` (`"Neon/Name"`, `"Neon/Active"`) is the
actual key string you use from Python. The `#define`d name itself
(`ODK_CI_KEY_CHANNEL_NAME`) is only a C++-side convenience constant; from
Python you use the quoted string directly:

```python
active_prop = proxy.get_config_property("Neon/Active")
print(active_prop.value)   # True or False
```

Most of the time you will not need this file for your own custom
properties (you invent your own key strings, as shown in section 6), but
it is the reference for the built-in properties every channel already
has, such as name, unit, sample rate, and color.

## 9. Common pitfalls

- **Forgetting `update()` must return `True`.** If your channel never
  seems to become usable, check that `update()` actually returns `True`
  once your configuration is valid; returning `False` (or `None`, which
  Python treats as falsy) marks the channel invalid.
- **Keeping a `GuardedArray` past `process()`.** Arrays from
  `get_blocks()`/`get_variable_blocks()` are only valid during the current
  `process()` call. Call `.copy()` if you need the data afterwards; see
  `PORTING_GUIDE.md` section 6 for details.
- **Confusing seconds and ticks.** `ctx.window_start`/`window_end` are in
  seconds; most sample-writing calls expect ticks. Use
  `api.convert_time_to_tick_at_or_after(time_in_seconds, frequency)` to
  convert.
- **Exceptions seem to vanish.** Uncaught exceptions inside a callback
  (like `process()`) are printed to stderr and forwarded to the host's
  log, but they do not stop your plugin process; make sure you are
  actually looking at stderr or the Oxygen log while developing.

## 10. Where to go next

- `sum_channels.py`, `moving_average.py`, `resample_source.py`: complete,
  working example plugins.
- https://dewetron.github.io/OXYGEN-SDK/index.html: the full C++ API
  reference for the underlying SDK.
