import pytest
import ctypes as ct
import numpy as np
import os
from pathlib import Path

from loki import SourceFile, OMNI, FortranMaxTransformation
from loki.build import Builder, Obj, Lib, execute
from loki.build.max_compiler import compile


def check_maxeler():
    """
    Check if Maxeler environment variables are specified.
    """
    maxeler_vars = {'MAXCOMPILERDIR', 'MAXELEROSDIR'}
    return maxeler_vars <= os.environ.keys()


# Skip tests in this module if Maxeler environment not present
pytestmark = pytest.mark.skipif(not check_maxeler(),
                                reason='Maxeler compiler not installed')


@pytest.fixture(scope='module')
def simulator():

    class MaxCompilerSim(object):

        def __init__(self):
            name = '%s_pytest' % os.getlogin()
            self.base_cmd = ['maxcompilersim', '-n', name]
            os.environ.update({'SLIC_CONF': 'use_simulation=%s' % name,
                               'LD_PRELOAD': ('%s/lib/libmaxeleros.so' %
                                              os.environ['MAXELEROSDIR'])})
            self.maxeleros = ct.CDLL(os.environ['MAXELEROSDIR'] + '/lib/libmaxeleros.so')

        def restart(self):
            cmd = self.base_cmd + ['-c', 'MAX5C', 'restart']
            execute(cmd)

        def stop(self):
            cmd = self.base_cmd + ['stop']
            execute(cmd)

        def run(self, target, *args):
            cmd = [str(target)]
            if args is not None:
                cmd += [str(a) for a in args]
            self.restart()
            execute(cmd)
            self.stop()

        def call(self, fn, *args):
            self.restart()
            fn(*args)
            self.stop()

    return MaxCompilerSim()


@pytest.fixture(scope='module')
def build_dir():
    return Path(__file__).parent / 'build'


@pytest.fixture(scope='module')
def refpath():
    return Path(__file__).parent / 'maxeler.f90'


@pytest.fixture(scope='module')
def builder(refpath):
    path = refpath.parent
    return Builder(source_dirs=path, build_dir=path/'build')


@pytest.fixture(scope='module')
def reference(refpath, builder):
    """
    Compile and load the reference solution
    """
    builder.clean()

    sources = ['maxeler.f90']
    objects = [Obj(source_path=s) for s in sources]
    lib = Lib(name='ref', objs=objects, shared=False)
    lib.build(builder=builder)
    return lib.wrap(modname='ref', sources=sources, builder=builder)


def max_transpile(routine, refpath, builder, objects=None, wrap=None):
    builder.clean()

    # Create transformation object and apply
    f2max = FortranMaxTransformation()
    f2max.apply(routine=routine, path=refpath.parent)

    # Build and wrap the cross-compiled library
#    objects = (objects or []) + [Obj(source_path=f2max.wrapperpath.name),
#                                 Obj(source_path=f2max.c_path.name)]
#    lib = Lib(name='fmax_%s' % routine.name, objs=objects, shared=False)
#    lib.build(builder=builder)
#
#    return lib.wrap(modname='mod_%s' % routine.name, builder=builder,
#                    sources=(wrap or []) + [f2max.wrapperpath.name])


def test_simulator(simulator):
    """
    Starts and stops the Maxeler Simulator.
    """
    simulator.restart()
    simulator.stop()
    assert True


def test_passthrough(simulator, build_dir, refpath):
    """
    A simple test streaming data to the DFE and back to CPU.
    """
    compile(c_src=refpath.parent / 'passthrough', maxj_src=refpath.parent / 'passthrough',
            build_dir=build_dir, target='PassThrough', manager='PassThroughMAX5CManager',
            package='passthrough')
    simulator.run(build_dir / 'PassThrough')


def test_passthrough_ctypes(simulator, build_dir, refpath):
    """
    A simple test streaming data to the DFE and back to CPU, called via ctypes
    """
    # First, build shared library
    compile(c_src=refpath.parent / 'passthrough', maxj_src=refpath.parent / 'passthrough',
            build_dir=build_dir, target='libPassThrough.so', manager='PassThroughMAX5CManager',
            package='passthrough')
    lib = ct.CDLL(build_dir / 'libPassThrough.so')

    # Extract function interfaces for CPU and DFE version
    func_cpu = lib.PassThroughCPU
    func_cpu.restype = None
    func_cpu.argtypes = [ct.c_int, ct.POINTER(ct.c_uint32), ct.POINTER(ct.c_uint32)]

    func_dfe = lib.passthrough
    func_dfe.restype = None
    func_dfe.argtypes = [ct.c_uint64, ct.c_void_p, ct.c_size_t, ct.c_void_p, ct.c_size_t]

    # Create input/output data structures
    size = 1024
    data_in = [i+1 for i in range(size)]
    data_out = size * [0]

    array_type = ct.c_uint32 * size
    size_bytes = ct.c_size_t(ct.sizeof(ct.c_uint32) * size)
    data_in = array_type(*data_in)
    expected_out = array_type(*data_out)
    data_out = array_type(*expected_out)

    # Run CPU function
    func_cpu(ct.c_int(size), data_in, expected_out)
    assert list(data_in) == list(expected_out)

    # Run DFE function
    simulator.call(func_dfe, ct.c_uint64(size), data_in, size_bytes, data_out, size_bytes)
    assert list(data_in) == list(data_out)


def test_routine_axpy(refpath, reference, builder):

    # Test the reference solution
    n = 10
    a = -3.
    x = np.zeros(shape=(n,), order='F') + range(n)
    y = np.zeros(shape=(n,), order='F') + range(n) + 10.
    reference.routine_axpy(n=n, a=-3, x=x, y=y)
    assert np.all(a * np.array(range(n), order='F') + y == x)

    # Generate the transpiled kernel
    source = SourceFile.from_file(refpath, frontend=OMNI, xmods=[refpath.parent])
    max_kernel = max_transpile(source['routine_axpy'], refpath, builder)


def test_routine_shift(refpath, reference, builder):

    # Test the reference solution
    length = 10
    scalar = 7
    vector_in = np.array(range(length), order='F', dtype=np.intc)
    vector_out = np.zeros(length, order='F', dtype=np.intc)
    reference.routine_shift(length, scalar, vector_in, vector_out)
    assert np.all(vector_out == np.array(range(length)) + scalar)

    # Generate the transpiled kernel
    source = SourceFile.from_file(refpath, frontend=OMNI, xmods=[refpath.parent])
    max_kernel = max_transpile(source['routine_shift'], refpath, builder)
