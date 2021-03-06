#!/usr/bin/env python
# -------------------------------------------------------------------------
#                     The CodeChecker Infrastructure
#   This file is distributed under the University of Illinois Open Source
#   License. See LICENSE.TXT for details.
# -------------------------------------------------------------------------
import argparse
import json
import os

import failure_lib as lib


def existsInSourcesRoot(entry, sources_root):
    """
    Returns true if the given file in the compile commands really available
    in the sources-root dir
    """
    if os.path.isabs(entry['file']):
        real_path = os.path.join(
            sources_root,
            entry['file'].lstrip(os.path.sep))
        return os.path.exists(real_path)

    real_path = os.path.join(
        sources_root,
        entry['directory'].lstrip(
            os.path.sep),
        entry['file'])
    return os.path.exists(real_path)


def prepare(compile_command_json, sources_root):
    """
    Read a compile cmd json file and change all paths with a prefix
    (sources_root).  Returns the modified json data.
    """
    json_data = lib.load_json_file(compile_command_json)
    result_json = []
    sources_root_abs = os.path.abspath(sources_root)
    for entry in json_data:
        if not existsInSourcesRoot(entry, sources_root):
            continue

        entry['directory'] =\
            lib.change_paths(entry['directory'],
                             lib.IncludePathModifier(sources_root_abs))

        cmd = entry['command']
        compiler, compilerEnd = lib.find_path_end(cmd.lstrip(), 0)
        entry['command'] = compiler +\
            lib.change_paths(cmd[compilerEnd:],
                             lib.IncludePathModifier(sources_root_abs))

        entry['file'] =\
            lib.change_paths(entry['file'],
                             lib.IncludePathModifier(sources_root_abs))

        result_json.append(entry)
    return result_json


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Prepare compile cmd json '
                                     'to execute in local environmennt.')
    parser.add_argument('compile_command_json')
    parser.add_argument('--sources_root', default='./sources-root')
    args = parser.parse_args()

    print(
        json.dumps(
            prepare(
                args.compile_command_json,
                args.sources_root),
            indent=4))
