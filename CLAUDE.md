# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`pedalboard` is a thin Python package wrapping a large C++ extension module (`pedalboard_native`) built on JUCE and pybind11. Nearly all behaviour — plugins, audio I/O, VST3/AU hosting — lives in the C++ headers under `pedalboard/`. Changing anything but `pedalboard/*.py` requires a recompile.

## Setup and build

Submodules are mandatory (JUCE, rubberband, lame, libgsm, pybind11 under `vendors/`):

```shell
git submodule update --init --recursive
```

The build backend is `scikit-build-core` driving `CMakeLists.txt`. `setup.py` also exists (a legacy `Pybind11Extension`-based path that globs all `.cpp`/`.mm` under `pedalboard/`); `pip install` always goes through CMake, so prefer that:

```shell
pip install pybind11 "scikit-build-core[pyproject]"
pip install --no-build-isolation -ve .   # add -Ceditable.rebuild=true to auto-rebuild C++ on import
```

Or reproduce CI's environment exactly: `uv sync --locked --all-extras --dev`.

Build-time environment variables (read by both CMake and `setup.py`):

- `DEBUG=1` — `-O0 -g`, needed for gdb/lldb.
- `USE_MARCH_NATIVE=1` — optimize for the local CPU. Default on Linux x86_64 is a portable AVX baseline (`HAVE_AVX`); the old `USE_PORTABLE_SIMD` variable is gone. Don't commit a native-arch default — published `x86_64` wheels must stay AVX-only, and `tests/test_linux_wheel_cpu_compatibility.py` guards this.
- `DISABLE_LTO=1` — much faster link, negligible perf loss for test builds.
- `USE_ASAN` / `USE_TSAN` / `USE_MSAN=1` — sanitizer builds (CI runs an ASan job per Python version).
- `CC`/`CXX` set to `ccache <compiler>` (or `scripts/ccache_g++` / `scripts/ccache_clang++`) for fast rebuilds; CMake also picks up `ccache` automatically if installed.

A full cold build is slow (all of JUCE compiles). Expect minutes, not seconds.

## Testing

```shell
pytest                                   # whole suite
pytest tests/test_io.py                  # one file
pytest tests/test_io.py::test_basic_formats -k wav   # one test / filter
pytest --cov=pedalboard --cov-fail-under=80          # CI's coverage gate is 80%
```

- `tests/conftest.py` shards tests via `NUM_TEST_WORKERS` / `TEST_WORKER_INDEX` (1-based) with no pytest plugin — only relevant on CI.
- `tests/test_external_plugins.py` is the largest and slowest file. It needs real VST3/AU binaries downloaded by `python tests/download_test_plugins.py`, which requires `GCS_ASSET_BUCKET_NAME` + `GCS_READER_SERVICE_ACCOUNT_KEY` (Spotify-internal). Without them, those tests silently skip — a green local run does not mean external-plugin code paths were exercised.
- `tests/test_type_hints.py` runs mypy/pyright/ty over `tests/mypy_fixtures/`; fixtures with `fail` in the filename are expected to fail type-checking. Skipped when `CI` or `CIBW_BUILD` is set.
- `tox` is largely vestigial — its `format`/`check-formatting` commands are commented out. Use the tools below directly.

## Lint, format, type-check

CI runs these; match them:

```shell
pip install 'ruff==0.15.22'              # pin matters: 0.16 widened default rules
ruff check pedalboard
ruff format --check pedalboard --diff
pyright pedalboard tests/test_*.py
clang-format -style=LLVM -i <file>       # v14; there is no .clang-format file, vendors/ is excluded
```

Ruff config lives in `pyproject.toml` (line length 100, `lint.select` deliberately unset). `flake8` settings in `tox.ini` are stale.

## Generated files — do not hand-edit

- `pedalboard_native/**/*.pyi` are generated stubs; `pedalboard_native/` contains **no** implementation.
- `docs/` HTML is generated and committed.

Both come from one script, which also runs `pybind11-stubgen`, post-processes the output, validates with `mypy.stubtest` (allowlist: `stubtest.allowlist`), and builds Sphinx docs:

```shell
python scripts/generate_type_stubs_and_docs.py          # regenerate; --check to verify only
```

Docstrings live in the C++ pybind11 bindings, so user-facing documentation changes usually mean editing a `.h` file and regenerating.

`pedalboard/version.py` is the version source of truth — CMake parses it to set the project version. `pyproject.toml`'s `version` is a separate literal that must be bumped to match in the same commit.

## C++ architecture

`pedalboard/python_bindings.cpp` is the single pybind11 module entry point. Every plugin header exposes an `init_<name>(py::module &)` function, and `PYBIND11_MODULE` calls them in order, assigning each to a submodule:

- top level `pedalboard_native` — the built-in effects
- `.utils` — `Mix`, `Chain`, `time_stretch`
- `._internal` — test-only plugins (`AddLatency`, the `FixedBlockSize`/`ForceMono`/`PrimeWithSilence`/`Resample` template test wrappers)
- `.io` — `AudioFile`, `ReadableAudioFile`, `ResampledReadableAudioFile`, `WriteableAudioFile`, `StreamResampler`, `AudioStream`

Layers:

- **`Plugin.h`** — abstract base: `prepare(ProcessSpec)`, `process(ProcessContextReplacing<float>)`, `reset()`, plus optional `getLatencyHint()` and `acceptsAudioInput()`. Each instance carries its own `std::mutex` (use `std::scoped_lock`/`std::lock` when taking more than one) and caches the last channel layout and `ProcessSpec`.
- **`JucePlugin<DSPType>`** — adapts any type with `prepare`/`process`/`reset` (i.e. a `juce::dsp` block) to `Plugin`. The `DEFINE_DSP_SETTER_AND_GETTER` macro adds a validated, cached parameter that forwards to the DSP object. Most of `plugins/` is a few dozen lines on top of this.
- **`PluginContainer`** — base for `Mix`/`Chain`/`Pedalboard`; implements the Python sequence protocol in C++ and rejects instrument plugins (anything with `acceptsAudioInput() == false`).
- **`plugin_templates/`** — composable wrappers that normalise what the inner plugin sees: a fixed block size, mono input, pre-primed silence, or a different sample rate. Prefer composing these over re-implementing buffering inside a new plugin.
- **`process.h`** — the real processing loop: chunks audio at `DEFAULT_BUFFER_SIZE` (8192), and compensates for plugin latency. The contract is that `process()` returns how many samples it produced, right-aligned in the buffer; `process.h` consolidates the resulting gaps.
- **`juce_overrides/`** — patched copies of JUCE sources (FLAC, MP3, WAV, VST3 host, convolution, macOS windowing) plus the `include_juce_*` unity-build translation units. JUCE's own FLAC/MP3 paths are compiled out via `-D` flags; patch here rather than modifying the `JUCE/` submodule.
- **`ExternalPlugin.h`** — VST3/AU hosting. Requires a `juce::MessageManager` and pumps its dispatch loop; much of the complexity is making a GUI-oriented plugin API safe to call from arbitrary Python threads.

Two cross-cutting invariants worth knowing before touching C++:

1. **GIL.** Audio work releases the GIL (`py::gil_scoped_release`) so Pedalboard parallelises without `multiprocessing`. Both `pedalboard/__init__.py` and `pedalboard/io/__init__.py` `import numpy` purely to force pybind11's lazy NumPy import to happen under the GIL — removing those imports reintroduces a real deadlock. The module is declared `py::mod_gil_not_used()` (free-threading compatible).
2. **Channel layout.** NumPy arrays are interpreted by `detectChannelLayout` in `BufferUtils.h`, assuming the smaller dimension is channels. Square arrays are ambiguous, so each `Plugin` remembers the last detected layout until `reset()` is called from Python.

## Python layer

- `pedalboard/__init__.py` re-exports `pedalboard_native` and `pedalboard_native.utils`, then injects `_AVAILABLE_PLUGIN_CLASSES` (`VST3Plugin`, `AudioUnitPlugin` — each imported in a `try`, since availability is platform-dependent).
- `pedalboard/_pedalboard.py` holds the only non-trivial Python logic: `Pedalboard` (a subclass of the native `Chain`), `load_plugin`, and the machinery that maps a plugin's human-readable parameter names to Python attributes — `AudioProcessorParameter`, `strip_common_float_suffixes` (a suffix trie for `"5.0 kHz"`-style values), `WrappedBool`, and the `__getattr__`/`__setattr__` interception on external plugins. Changes here affect every third-party plugin, so lean on `tests/test_external_plugins.py`.

## Adding a built-in plugin

1. Add `pedalboard/plugins/MyPlugin.h` with a `Pedalboard::MyPlugin` class (usually `JucePlugin<...>`) and an `init_my_plugin(py::module &)`.
2. Include the header and call `init_my_plugin(m)` in `pedalboard/python_bindings.cpp`.
3. Add `tests/test_my_plugin.py`; rebuild, run `python scripts/generate_type_stubs_and_docs.py`, and commit the regenerated `.pyi` and `docs/` changes.

`docs/source/internals.rst` covers the licensing, binary-size, and platform constraints to weigh before vendoring new DSP code.

## Conventions

- Commit messages and PR titles are scoped: `module-name: One line description` (imperative, no trailing period, <72 chars), with `Problem` / `Solution` / `Result` sections in the body.
- C++ is LLVM-style clang-format; Python is `ruff format`.
- Everything is GPLv3 and statically linked. New dependencies must be GPLv3-compatible, statically linkable, and buildable on macOS, Windows, and Linux.
