##############################################################################
#.rst:
#
# loki_find_executables
# =====================
#
# Find Loki's executable frontend scripts and make them available as
# (imported) targets. ::
#
#   loki_find_executables()
#
# It adds all scripts in the list `LOKI_EXECUTABLES` using `add_executable`,
# either by setting explicitly the path to the installed scripts or by
# searching for them using `find_program` if Loki is not being installed by CMake.
#
# Additionally, `clawfc` is also being searched for and made available as
# an executable, if it has not been exported as a target already.
#
# Input variables
# ---------------
#
# :LOKI_EXECUTABLES:    The names of all Loki executables.
# :loki_HAVE_NO_INSTALL: If True, Loki is considered not to be installed by
#                       CMake and all executables are searched for using
#                       `find_program`.
# :Python3_VENV_BIN:    The `bin` directory path of Loki's virtual environment.
#                       Executable scripts are used from this folder if
#                       `loki_HAVE_NO_INSTALL` is false.
# :loki_HAVE_CLAW:      If True, then CLAW should be installed and usable and
#                       `clawfc` is added as an executable.
#
##############################################################################
macro( loki_find_executables )

    ecbuild_debug( "LOKI_EXECUTABLES=${LOKI_EXECUTABLES}" )

    # Make Loki executables (and clawfc) available as imported executable targets
    # (this is required for the macros in loki_transform to set up their environment)
    if( ${loki_HAVE_NO_INSTALL} )

        # Make CLI executables available in add_custom_command by searching
        # for them on the $PATH using find_program
        foreach( _exe_name IN LISTS LOKI_EXECUTABLES )
            if( NOT TARGET ${_exe_name} )
                find_program( _exe_program NAMES ${_exe_name} )
                add_executable( ${_exe_name} IMPORTED GLOBAL )
                set_property( TARGET ${_exe_name} PROPERTY IMPORTED_LOCATION ${_exe_program} )
                ecbuild_debug( "Adding executable ${_exe_name} from ${_exe_program}" )
                unset( _exe_program CACHE )
            endif()
        endforeach()

    else()

        # Make CLI executables available in add_custom_command by setting
        # their location to the virtual environment's bin folder
        foreach( _exe_name IN LISTS LOKI_EXECUTABLES )
            if( NOT TARGET ${_exe_name} )
                add_executable( ${_exe_name} IMPORTED GLOBAL )
                set_property( TARGET ${_exe_name} PROPERTY IMPORTED_LOCATION ${Python3_VENV_BIN}/${_exe_name} )
                ecbuild_debug( "Adding executable ${_exe_name} from ${Python3_VENV_BIN}/${_exe_name}" )
            endif()
        endforeach()

        if( ${loki_HAVE_CLAW} )
            add_dependencies( loki-transform.py clawfc )
        endif()

    endif()

    # Find clawfc on the PATH to make available as target
    if( ${loki_HAVE_NO_INSTALL} OR NOT ${loki_HAVE_CLAW} )
        if( NOT TARGET clawfc )
            find_program ( _CLAWFC_EXECUTABLE NAMES clawfc )
            add_executable( clawfc IMPORTED GLOBAL )
            set_property( TARGET clawfc PROPERTY IMPORTED_LOCATION ${_CLAWFC_EXECUTABLE} )
            ecbuild_debug( "Adding executable clawfc from ${_CLAWFC_EXECUTABLE}" )
        endif()
    endif()

endmacro()