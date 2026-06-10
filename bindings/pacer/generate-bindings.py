#! /usr/bin/env python3

from pathlib import Path

import litgen

LITGEN_USE_NANOBIND = True


def my_litgen_options() -> litgen.LitgenOptions:
    # configure your options here
    options = litgen.LitgenOptions()
    options.bind_library = litgen.BindLibraryType.nanobind

    # ///////////////////////////////////////////////////////////////////
    #  Root namespace
    # ///////////////////////////////////////////////////////////////////
    # The namespace pacer is the C++ root namespace for the generated bindings
    # (i.e. no submodule will be generated for it in the python bindings)
    options.namespaces_root = ["pacer"]

    # //////////////////////////////////////////////////////////////////
    # Basic functions bindings
    # ////////////////////////////////////////////////////////////////////
    # No specific option is needed for these basic bindings
    # litgen will add the docstrings automatically in the python bindings

    # //////////////////////////////////////////////////////////////////
    # Classes and structs bindings
    # //////////////////////////////////////////////////////////////////
    # No specific option is needed for these bindings.
    # - Litgen will automatically add a default constructor with named parameters
    #   for structs that have no constructor defined in C++.
    #  - A class will publish only its public methods and members

    # ////////////////////////////////////////////////////////////////////
    # Override virtual methods in python
    # ////////////////////////////////////////////////////////////////////
    # RawGPSSource is an abstract base whose virtual methods can be overridden
    # from python (used to feed synthetic / test GPS sources into the engine).
    options.class_override_virtual_methods_in_python__regex = "^RawGPSSource$"

    # ////////////////////////////////////////////////////////////////////
    # Template specializations / ignores for the real pacer types
    # ////////////////////////////////////////////////////////////////////
    # PointInTime<GPSSample> is the only instantiation we expose.
    options.class_template_options.add_specialization("PointInTime", ["GPSSample"])

    # The CRTP operator-mixin bases (ops.hpp) are implementation detail, not API.
    options.class_template_options.add_ignore("VectorOperators")
    options.class_template_options.add_ignore("PointwiseOperators")
    options.class_template_options.add_ignore("LinearOperators")

    # Generic template helpers we don't want auto-specialized into the bindings.
    options.fn_template_options.add_ignore("Interpolate")
    options.fn_template_options.add_ignore("ToPoint")

    # ApproxEqual is an internal geometry helper (the single epsilon-equality used by
    # Segment::operator==), not part of the Python API — keep it off the binding surface.
    options.fn_exclude_by_name__regex = "^ApproxEqual$"

    # ////////////////////////////////////////////////////////////////////
    # Format the python stubs with black
    # ////////////////////////////////////////////////////////////////////
    # Set to True if you want the stub file to be formatted with black
    options.python_run_black_formatter = True

    return options


def autogenerate() -> None:
    repository_dir = Path(__file__).parent.parent.parent

    header_files = [
        repository_dir / "pacer/datatypes/datatypes.hpp",
        repository_dir / "pacer/geometry/geometry.hpp",
        repository_dir / "pacer/laps/laps.hpp",
        repository_dir / "pacer/gps-source/gps-source.hpp",
    ]

    output_cpp_pydef_file = repository_dir / "bindings/pacer/nanobind_pacer.cpp"

    litgen.write_generated_code_for_files(
        options=my_litgen_options(),
        input_cpp_header_files=[str(p) for p in header_files],
        output_cpp_pydef_file=output_cpp_pydef_file,
        output_stub_pyi_file=str(repository_dir / "bindings/pacer/pacer/__init__.pyi"),
    )


if __name__ == "__main__":
    autogenerate()
