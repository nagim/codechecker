# -------------------------------------------------------------------------
#                     The CodeChecker Infrastructure
#   This file is distributed under the University of Illinois Open Source
#   License. See LICENSE.TXT for details.
# -------------------------------------------------------------------------
""""""

import os

from libcodechecker.logger import LoggerFactory

LOG = LoggerFactory.get_new_logger('ENV')


def get_log_env(logfile, context, original_env):
    """
    Environment for logging. With the ld logger.
    Keep the original environment unmodified as possible.
    Only environment variables required for logging are changed.
    """
    new_env = original_env

    new_env[context.env_var_cc_logger_bin] = context.path_logger_bin

    new_env['LD_PRELOAD'] = context.logger_lib_name

    try:
        original_ld_library_path = new_env['LD_LIBRARY_PATH']
        new_env['LD_LIBRARY_PATH'] = context.path_logger_lib + ':' + \
            original_ld_library_path
    except:
        new_env['LD_LIBRARY_PATH'] = context.path_logger_lib

    # Set ld logger logfile.
    new_env[context.env_var_cc_logger_file] = logfile

    return new_env


def get_check_env(path_env_extra, ld_lib_path_extra):
    """
    Extending the checker environment.
    Check environment is extended to find tools if they ar not on
    the default places.
    """
    new_env = os.environ.copy()

    if len(path_env_extra) > 0:
        extra_path = ':'.join(path_env_extra)
        LOG.debug_analyzer(
            'Extending PATH environment variable with: ' + extra_path)

        try:
            new_env['PATH'] = extra_path + ':' + new_env['PATH']
        except:
            new_env['PATH'] = extra_path

    if len(ld_lib_path_extra) > 0:
        extra_lib = ':'.join(ld_lib_path_extra)
        LOG.debug_analyzer(
            'Extending LD_LIBRARY_PATH environment variable with: ' +
            extra_lib)
        try:
            original_ld_library_path = new_env['LD_LIBRARY_PATH']
            new_env['LD_LIBRARY_PATH'] = \
                extra_lib + ':' + original_ld_library_path
        except:
            new_env['LD_LIBRARY_PATH'] = extra_lib

    return new_env


def extend_analyzer_cmd_with_resource_dir(analyzer_cmd,
                                          compiler_resource_dir):
    """
    GCC and Clang handles the certain compiler intrinsics for specific
    instruction sets (like SSE, AVX) differently.  They have their own set of
    macros in a bunch of include files (e.g. xmmintrin.h) in the include dir
    of the resource dir of the compiler.  Since we execute the analysis with
    Clang we must ensure that theses resource headers are included from the
    proper place. One option to do this is the usage of -resource-dir.  On the
    other hand, to make the analysis work we also have to add the default
    includes of the original compiler (which maybe a GCC cross compiler).  We
    add these default includes of the original compiler with -isystem.

    Our empirical results show that -resource_dir has lower priority than
    -isystem.  Unfortunately in Clang the implementation about resource
    includes is very messy, it is merged with the handling of the upcoming
    modules feature from C++20.  The corresponding functions from Clang:
        clang::ApplyHeaderSearchOptions
        HeaderSearch::LookupFile
        DirectoryLookup::LookupFile.
    Based on our emprical results, the only solution is to add the resource
    includes with -isystem before adding the system headers of the original
    compiler. Since we don't use the -resource_dir switch, it is more explicit
    and describes our intentions more cleanly if we just simply disable the
    inludes from the resource dir.
    """
    if len(compiler_resource_dir) == 0:
        return

    resource_inc = compiler_resource_dir
    # Resource include path must end with "/include".
    basename = os.path.basename(os.path.normpath(resource_inc))
    if basename != 'include':
        resource_inc = os.path.join(resource_inc, "include")

    analyzer_cmd.extend(['-nobuiltininc',
                         '-isystem', resource_inc])
