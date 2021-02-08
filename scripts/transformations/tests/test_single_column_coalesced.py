import sys
from pathlib import Path
import pytest

from loki import (
    OFP, OMNI, FP, Subroutine, Dimension, FindNodes, Loop, Assignment,
    CallStatement, Scalar, Array, Pragma, pragmas_attached, fgen
)

# Bootstrap the local transformations directory for custom transformations
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
# pylint: disable=wrong-import-position,wrong-import-order
from transformations import SingleColumnCoalescedTransformation


@pytest.fixture(scope='module', name='horizontal')
def fixture_horizontal():
    return Dimension(name='horizontal', size='nlon', index='jl', bounds=('start', 'end'))


@pytest.fixture(scope='module', name='vertical')
def fixture_vertical():
    return Dimension(name='vertical', size='nz', index='jk')


@pytest.fixture(scope='module', name='blocking')
def fixture_blocking():
    return Dimension(name='blocking', size='nb', index='b')


@pytest.mark.parametrize('frontend', [OFP, OMNI, FP])
def test_single_column_coalesced_simple(frontend, horizontal, vertical):
    """
    Test removal of vector loops in kernel and re-insertion of the
    horizontal loop in the "driver".
    """

    fcode_driver = """
  SUBROUTINE column_driver(nlon, nz, q, t, nb)
    INTEGER, INTENT(IN)   :: nlon, nz  ! Size of the horizontal and vertical
    REAL, INTENT(INOUT)   :: t(nlon,nz,nb)
    REAL, INTENT(INOUT)   :: q(nlon,nz,nb)
    INTEGER :: b, start, end

    start = 1
    end = nlon
    do b=1, nb
      call compute_column(start, end, nlon, nz, q(:,:,b), t(:,:,b))
    end do
  END SUBROUTINE column_driver
"""

    fcode_kernel = """
  SUBROUTINE compute_column(start, end, nlon, nz, q, t)
    INTEGER, INTENT(IN) :: start, end  ! Iteration indices
    INTEGER, INTENT(IN) :: nlon, nz    ! Size of the horizontal and vertical
    REAL, INTENT(INOUT) :: t(nlon,nz)
    REAL, INTENT(INOUT) :: q(nlon,nz)
    INTEGER :: jl, jk
    REAL :: c

    c = 5.345
    DO jk = 2, nz
      DO jl = start, end
        t(jl, jk) = c * k
        q(jl, jk) = q(jl, jk-1) + t(jl, jk) * c
      END DO
    END DO

    ! The scaling is purposefully upper-cased
    DO JL = START, END
      Q(JL, NZ) = Q(JL, NZ) * C
    END DO
  END SUBROUTINE compute_column
"""
    kernel = Subroutine.from_source(fcode_kernel, frontend=frontend)
    driver = Subroutine.from_source(fcode_driver, frontend=frontend)
    driver.enrich_calls(kernel)  # Attach kernel source to driver call

    scc_transform = SingleColumnCoalescedTransformation(
        horizontal=horizontal, vertical=vertical,
        hoist_column_arrays=False
    )
    scc_transform.apply(driver, role='driver', targets=['compute_column'])
    scc_transform.apply(kernel, role='kernel')

    # Ensure we have two nested loops in the kernel
    # (the hoisted horizontal and the native vertical)
    kernel_loops = FindNodes(Loop).visit(kernel.body)
    assert len(kernel_loops) == 2
    assert kernel_loops[1] in FindNodes(Loop).visit(kernel_loops[0].body)
    assert kernel_loops[0].variable == 'jl'
    assert kernel_loops[0].bounds == 'start:end'
    assert kernel_loops[1].variable == 'jk'
    assert kernel_loops[1].bounds == '2:nz'

    # Ensure all expressions and array indices are unchanged
    assigns = FindNodes(Assignment).visit(kernel.body)
    assert fgen(assigns[1]).lower() == 't(jl, jk) = c*k'
    assert fgen(assigns[2]).lower() == 'q(jl, jk) = q(jl, jk - 1) + t(jl, jk)*c'
    assert fgen(assigns[3]).lower() == 'q(jl, nz) = q(jl, nz)*c'

    # Ensure only one loop in the driver
    driver_loops = FindNodes(Loop).visit(driver.body)
    assert len(driver_loops) == 1
    assert driver_loops[0].variable == 'b'
    assert driver_loops[0].bounds == '1:nb'

    # Ensure we have a kernel call in the driver loop
    kernel_calls = FindNodes(CallStatement).visit(driver_loops[0])
    assert len(kernel_calls) == 1
    assert kernel_calls[0].name == 'compute_column'


@pytest.mark.parametrize('frontend', [OFP, OMNI, FP])
def test_single_column_coalesced_demote(frontend, horizontal, vertical):
    """
    Test that local array variables that do not contain the
    vertical dimension are demoted and privativised.
    """

    fcode_driver = """
  SUBROUTINE column_driver(nlon, nz, nb, q)
    INTEGER, INTENT(IN)   :: nlon, nz, nb  ! Array dimensions
    REAL, INTENT(INOUT)   :: q(nlon,nz,nb)
    INTEGER :: b, start, end

    start = 1
    end = nlon
    do b=1, nb
      call compute_column(start, end, nlon, nz, q(:,:,b))
    end do
  END SUBROUTINE column_driver
"""

    fcode_kernel = """
  SUBROUTINE compute_column(start, end, nlon, nz, q)
    INTEGER, INTENT(IN) :: start, end  ! Iteration indices
    INTEGER, INTENT(IN) :: nlon, nz    ! Size of the horizontal and vertical
    REAL, INTENT(INOUT) :: q(nlon,nz)
    REAL :: t(nlon,nz)
    REAL :: a(nlon)
    REAL :: b(nlon,psize)
    INTEGER, PARAMETER :: psize = 3
    INTEGER :: jl, jk
    REAL :: c

    c = 5.345
    DO jk = 2, nz
      DO jl = start, end
        t(jl, jk) = c * k
        q(jl, jk) = q(jl, jk-1) + t(jl, jk) * c
      END DO
    END DO

    ! The scaling is purposefully upper-cased
    DO JL = START, END
      a(jl) = Q(JL, 1)
      b(jl, 1) = Q(JL, 2)
      b(jl, 2) = Q(JL, 3)
      b(jl, 3) = a(jl) * (b(jl, 1) + b(jl, 2))

      Q(JL, NZ) = Q(JL, NZ) * C + b(jl, 3)
    END DO
  END SUBROUTINE compute_column
"""
    kernel = Subroutine.from_source(fcode_kernel, frontend=frontend)
    driver = Subroutine.from_source(fcode_driver, frontend=frontend)
    driver.enrich_calls(kernel)  # Attach kernel source to driver call

    scc_transform = SingleColumnCoalescedTransformation(
        horizontal=horizontal, vertical=vertical,
        hoist_column_arrays=False
    )
    scc_transform.apply(driver, role='driver', targets=['compute_column'])
    scc_transform.apply(kernel, role='kernel')

    # Ensure correct array variables shapes
    assert isinstance(kernel.variable_map['a'], Scalar)
    assert isinstance(kernel.variable_map['b'], Array)
    assert isinstance(kernel.variable_map['c'], Scalar)
    assert isinstance(kernel.variable_map['t'], Array)
    assert isinstance(kernel.variable_map['q'], Array)

    # Ensure that parameter-sized array b got demoted only
    assert kernel.variable_map['b'].shape == ((3,) if frontend is OMNI else ('psize',))
    assert kernel.variable_map['t'].shape == ('nlon', 'nz')
    assert kernel.variable_map['q'].shape == ('nlon', 'nz')

    # Ensure relevant expressions and array indices are unchanged
    assigns = FindNodes(Assignment).visit(kernel.body)
    assert fgen(assigns[1]).lower() == 't(jl, jk) = c*k'
    assert fgen(assigns[2]).lower() == 'q(jl, jk) = q(jl, jk - 1) + t(jl, jk)*c'
    assert fgen(assigns[3]).lower() == 'a = q(jl, 1)'
    assert fgen(assigns[4]).lower() == 'b(1) = q(jl, 2)'
    assert fgen(assigns[5]).lower() == 'b(2) = q(jl, 3)'
    assert fgen(assigns[6]).lower() == 'b(3) = a*(b(1) + b(2))'
    assert fgen(assigns[7]).lower() == 'q(jl, nz) = q(jl, nz)*c + b(3)'


@pytest.mark.parametrize('frontend', [OFP, OMNI, FP])
def test_single_column_coalesced_hoist(frontend, horizontal, vertical, blocking):
    """
    Test hoisting of column temporaries to "driver" level.
    """

    fcode_driver = """
  SUBROUTINE column_driver(nlon, nz, q, nb)
    INTEGER, INTENT(IN)   :: nlon, nz  ! Size of the horizontal and vertical
    REAL, INTENT(INOUT)   :: q(nlon,nz,nb)
    INTEGER :: b, start, end

    start = 1
    end = nlon
    do b=1, nb
      call compute_column(start, end, nlon, nz, q(:,:,b))
    end do
  END SUBROUTINE column_driver
"""

    fcode_kernel = """
  SUBROUTINE compute_column(start, end, nlon, nz, q)
    INTEGER, INTENT(IN) :: start, end  ! Iteration indices
    INTEGER, INTENT(IN) :: nlon, nz    ! Size of the horizontal and vertical
    REAL, INTENT(INOUT) :: q(nlon,nz)
    REAL :: t(nlon,nz)
    INTEGER :: jl, jk
    REAL :: c

    c = 5.345
    DO jk = 2, nz
      DO jl = start, end
        t(jl, jk) = c * k
        q(jl, jk) = q(jl, jk-1) + t(jl, jk) * c
      END DO
    END DO

    ! The scaling is purposefully upper-cased
    DO JL = START, END
      Q(JL, NZ) = Q(JL, NZ) * C
    END DO
  END SUBROUTINE compute_column
"""
    kernel = Subroutine.from_source(fcode_kernel, frontend=frontend)
    driver = Subroutine.from_source(fcode_driver, frontend=frontend)
    driver.enrich_calls(kernel)  # Attach kernel source to driver call

    scc_transform = SingleColumnCoalescedTransformation(
        horizontal=horizontal, vertical=vertical, block_dim=blocking,
        hoist_column_arrays=True
    )
    scc_transform.apply(driver, role='driver', targets=['compute_column'])
    scc_transform.apply(kernel, role='kernel')

    # Ensure we have only one loop left in kernel
    kernel_loops = FindNodes(Loop).visit(kernel.body)
    assert len(kernel_loops) == 1
    assert kernel_loops[0].variable == 'jk'
    assert kernel_loops[0].bounds == '2:nz'

    # Ensure all expressions and array indices are unchanged
    assigns = FindNodes(Assignment).visit(kernel.body)
    assert fgen(assigns[1]).lower() == 't(jl, jk) = c*k'
    assert fgen(assigns[2]).lower() == 'q(jl, jk) = q(jl, jk - 1) + t(jl, jk)*c'
    assert fgen(assigns[3]).lower() == 'q(jl, nz) = q(jl, nz)*c'

    # Ensure we have two nested driver loops
    driver_loops = FindNodes(Loop).visit(driver.body)
    assert len(driver_loops) == 2
    assert driver_loops[1] in FindNodes(Loop).visit(driver_loops[0].body)
    assert driver_loops[0].variable == 'b'
    assert driver_loops[0].bounds == '1:nb'
    assert driver_loops[1].variable == 'jl'
    assert driver_loops[1].bounds == 'start:end'

    # Ensure we have a kernel call in the driver loop
    kernel_calls = FindNodes(CallStatement).visit(driver_loops[0])
    assert len(kernel_calls) == 1
    assert kernel_calls[0].name == 'compute_column'
    assert ('jl', 'jl') in kernel_calls[0].kwarguments
    assert 't(:,:,b)' in kernel_calls[0].arguments

    # Ensure that column local `t(nlon,nz)` has been hoisted
    assert 't' in kernel.argnames
    assert kernel.variable_map['t'].type.intent.lower() == 'inout'
    # TODO: Shape doesn't translate correctly yet.
    assert driver.variable_map['t'].dimensions == ('nlon', 'nz', 'nb')
    # assert driver.variable_map['t'].shape == ('nlon', 'nz', 'nb')


@pytest.mark.parametrize('frontend', [OFP, OMNI, FP])
def test_single_column_coalesced_openacc(frontend, horizontal, vertical, blocking):
    """
    Test the correct addition of OpenACC pragmas to SCC format code (no hoisting).
    """

    fcode_driver = """
  SUBROUTINE column_driver(nlon, nz, q, nb)
    INTEGER, INTENT(IN)   :: nlon, nz  ! Size of the horizontal and vertical
    REAL, INTENT(INOUT)   :: q(nlon,nz,nb)
    INTEGER :: b, start, end

    start = 1
    end = nlon
    do b=1, nb
      call compute_column(start, end, nlon, nz, q(:,:,b))
    end do
  END SUBROUTINE column_driver
"""

    fcode_kernel = """
  SUBROUTINE compute_column(start, end, nlon, nz, q)
    INTEGER, INTENT(IN) :: start, end  ! Iteration indices
    INTEGER, INTENT(IN) :: nlon, nz    ! Size of the horizontal and vertical
    REAL, INTENT(INOUT) :: q(nlon,nz)
    REAL :: t(nlon,nz)
    REAL :: a(nlon)
    REAL :: b(nlon,psize)
    INTEGER, PARAMETER :: psize = 3
    INTEGER :: jl, jk
    REAL :: c

    c = 5.345
    DO jk = 2, nz
      DO jl = start, end
        t(jl, jk) = c * k
        q(jl, jk) = q(jl, jk-1) + t(jl, jk) * c
      END DO
    END DO

    ! The scaling is purposefully upper-cased
    DO JL = START, END
      a(jl) = Q(JL, 1)
      b(jl, 1) = Q(JL, 2)
      b(jl, 2) = Q(JL, 3)
      b(jl, 3) = a(jl) * (b(jl, 1) + b(jl, 2))

      Q(JL, NZ) = Q(JL, NZ) * C
    END DO
  END SUBROUTINE compute_column
"""
    kernel = Subroutine.from_source(fcode_kernel, frontend=frontend)
    driver = Subroutine.from_source(fcode_driver, frontend=frontend)
    driver.enrich_calls(kernel)  # Attach kernel source to driver call

    # Test OpenACC annotations on non-hoisted version
    scc_transform = SingleColumnCoalescedTransformation(
        horizontal=horizontal, vertical=vertical, block_dim=blocking,
        hoist_column_arrays=False, directive='openacc'
    )
    scc_transform.apply(driver, role='driver', targets=['compute_column'])
    scc_transform.apply(kernel, role='kernel')

    # Ensure routine is anntoated at vector level
    pragmas = FindNodes(Pragma).visit(kernel.body)
    assert len(pragmas) == 3
    assert pragmas[0].keyword == 'acc'
    assert pragmas[0].content == 'routine vector'

    # Ensure vector and seq loops are annotated, including
    # privatized variable `b`
    with pragmas_attached(kernel, Loop):
        kernel_loops = FindNodes(Loop).visit(kernel.body)
        assert len(kernel_loops) == 2
        assert kernel_loops[0].pragma[0].keyword == 'acc'
        assert kernel_loops[0].pragma[0].content == 'loop vector private(b)'
        assert kernel_loops[1].pragma[0].keyword == 'acc'
        assert kernel_loops[1].pragma[0].content == 'loop seq'

    # Ensure a single outer parallel loop in driver
    with pragmas_attached(driver, Loop):
        driver_loops = FindNodes(Loop).visit(driver.body)
        assert len(driver_loops) == 1
        assert driver_loops[0].pragma[0].keyword == 'acc'
        assert driver_loops[0].pragma[0].content == 'parallel loop gang'


@pytest.mark.parametrize('frontend', [OFP, OMNI, FP])
def test_single_column_coalesced_hoist_openacc(frontend, horizontal, vertical, blocking):
    """
    Test the correct addition of OpenACC pragmas to SCC format code
    when hoisting column array temporaries to driver.
    """

    fcode_driver = """
  SUBROUTINE column_driver(nlon, nz, q, nb)
    INTEGER, INTENT(IN)   :: nlon, nz  ! Size of the horizontal and vertical
    REAL, INTENT(INOUT)   :: q(nlon,nz,nb)
    INTEGER :: b, start, end

    start = 1
    end = nlon
    do b=1, nb
      call compute_column(start, end, nlon, nz, q(:,:,b))
    end do
  END SUBROUTINE column_driver
"""

    fcode_kernel = """
  SUBROUTINE compute_column(start, end, nlon, nz, q)
    INTEGER, INTENT(IN) :: start, end  ! Iteration indices
    INTEGER, INTENT(IN) :: nlon, nz    ! Size of the horizontal and vertical
    REAL, INTENT(INOUT) :: q(nlon,nz)
    REAL :: t(nlon,nz)
    REAL :: a(nlon)
    REAL :: b(nlon,psize)
    INTEGER, PARAMETER :: psize = 3
    INTEGER :: jl, jk
    REAL :: c

    c = 5.345
    DO jk = 2, nz
      DO jl = start, end
        t(jl, jk) = c * k
        q(jl, jk) = q(jl, jk-1) + t(jl, jk) * c
      END DO
    END DO

    ! The scaling is purposefully upper-cased
    DO JL = START, END
      a(jl) = Q(JL, 1)
      b(jl, 1) = Q(JL, 2)
      b(jl, 2) = Q(JL, 3)
      b(jl, 3) = a(jl) * (b(jl, 1) + b(jl, 2))

      Q(JL, NZ) = Q(JL, NZ) * C
    END DO
  END SUBROUTINE compute_column
"""
    kernel = Subroutine.from_source(fcode_kernel, frontend=frontend)
    driver = Subroutine.from_source(fcode_driver, frontend=frontend)
    driver.enrich_calls(kernel)  # Attach kernel source to driver call

    # Test OpenACC annotations on non-hoisted version
    scc_transform = SingleColumnCoalescedTransformation(
        horizontal=horizontal, vertical=vertical, block_dim=blocking,
        hoist_column_arrays=True, directive='openacc'
    )
    scc_transform.apply(driver, role='driver', targets=['compute_column'])
    scc_transform.apply(kernel, role='kernel')

    with pragmas_attached(kernel, Loop):
        # Ensure routine is anntoated at vector level
        kernel_pragmas = FindNodes(Pragma).visit(kernel.body)
        assert len(kernel_pragmas) == 1
        assert kernel_pragmas[0].keyword == 'acc'
        assert kernel_pragmas[0].content == 'routine seq'

        # Ensure only a single `seq` loop is left
        kernel_loops = FindNodes(Loop).visit(kernel.body)
        assert len(kernel_loops) == 1
        assert kernel_loops[0].pragma[0].keyword == 'acc'
        assert kernel_loops[0].pragma[0].content == 'loop seq'

    # Ensure two levels of blocked parallel loops in driver
    with pragmas_attached(driver, Loop):
        driver_loops = FindNodes(Loop).visit(driver.body)
        assert len(driver_loops) == 2
        assert driver_loops[0].pragma[0].keyword == 'acc'
        assert driver_loops[0].pragma[0].content == 'parallel loop gang'
        assert driver_loops[1].pragma[0].keyword == 'acc'
        assert driver_loops[1].pragma[0].content == 'loop vector'

        # Ensure deviece allocation and teardown via `!$acc enter/exit data`
        driver_pragmas = FindNodes(Pragma).visit(driver.body)
        assert len(driver_pragmas) == 2
        assert driver_pragmas[0].keyword == 'acc'
        assert driver_pragmas[0].content == 'enter data create(t)'
        assert driver_pragmas[1].keyword == 'acc'
        assert driver_pragmas[1].content == 'exit data delete(t)'
