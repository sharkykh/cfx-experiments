# config: utf-8
"""
Script that scans FXServer resources and prints a resource dependency tree.
Requires Python 3.7+

=============================
| This is still a prototype |
=============================

TODO:
- [x] read dependencies from manifest
- [x] read `*_script '@resource/*'` from manifest
- [x] using exports in Lua files
- [x] using exports in JS files
- [ ] better results (not sure what it should be)
"""

import argparse
import ast
import fnmatch
import re
import sys
from pathlib import Path
from typing import (
    Dict,
    Iterable,
    List,
    Set,
    Tuple,
)

# A list of ignored paths (can be used to ignore complete folders), no glob support
IGNORED_PATHS = [
]

# A list of ignored resources by resource name, no glob support
IGNORED_RESOURCES = [
]

# Lua exports call:
#   exports.resource:function
#   exports["foo-resource"]:function
#   exports['bar.resource']:function
LUA_EXPORTS = re.compile(
    r'(?:^|[ \t]+)exports'
    r'(?:\.(?P<resource1>\w+)|\[\s*["\'](?P<resource2>[^"\']+)["\']\s*\]):',
    re.MULTILINE
)

# JS exports call:
#   exports.resource.function
#   exports["foo-resource"].function
#   exports['bar.resource']['function-name']
JS_EXPORTS = re.compile(
    r'(?:^|[ \t]+)exports'
    r'(?:\.(?P<resource1>\w+)|\[\s*["\'](?P<resource2>[^"\']+)["\']\s*\])[.\[]',
    re.MULTILINE
)

CFG_KEY = re.compile(
    r'(?P<command>[a-z]+)[ \t]+(?P<name>[a-z\d_.-]+)',
    re.IGNORECASE
)

CATEGORY_FOLDER = re.compile(
    r'\[[^\]]+\]'
)

LUA_BLOCK_COMMENT = re.compile(
    r'--\[\[.*?\]\](?:--)?',
    re.DOTALL
)

LUA_SINGLE_COMMENT = re.compile(
    r'--(?!\[\[).+$\r?\n',
    re.MULTILINE
)

JS_COMMENTS = re.compile(
    r'(?:(?:^|\s)\/\/(.+?)$)|(?:\/\*(.*?)\*\/)',
    re.MULTILINE | re.DOTALL
)

LINE_MAP_REGEX = re.compile(
    r'.*(\n|$)'
)

def export_to_file(data: str, file: Path):
    if file.is_file():
        answer = input(f'{file!s} already exists, overwrite? [Y/n] ').strip().lower()
        if answer and answer != 'y':
            return False

    with file.open('w', encoding='utf-8', newline='\n') as fh:
        fh.write(data)

    return True

def file_suffix_filter(files: Iterable[Path], suffixes: Iterable[str]) -> Iterable[Path]:
    for path in files:
        if path.suffix in suffixes:
            yield path

def is_ignored_path(rel_path: Path, paths: List[str]):
    return any(path in rel_path.parents for path in paths)

class Debug:
    enabled: bool = False

    @classmethod
    def print(cls, *args, **kwargs):
        if cls.enabled:
            print(*args, **kwargs)

class LocationInfo:
    def __init__(self, path: Path, line: int, script_type: str):
        self.path: Path = path
        self.line: int = line
        self.script_type: str = script_type

    def __str__(self):
        return f'{self.path!s}:{self.line}'

class EventMatch:
    def __init__(self, match: re.Match):
        self.data: Dict[str, str] = match.groupdict()
        self.locations: Dict[Path, LocationInfo] = {}

    def add(self, path: Path, line: int, script_type: str):
        self.locations[path] = LocationInfo(
            path=path,
            line=line,
            script_type=script_type,
        )

    @property
    def function(self) -> str:
        return self.data['func']

    @property
    def event_name(self) -> str:
        return self.data['event']

    @property
    def is_event_handler(self) -> bool:
        return self.function in (
            'AddEventHandler',
            'on',
            'onNet',
            'addEventListener',
            'addNetEventListener',
        )

    @property
    def is_event_emitter(self) -> bool:
        return self.function in (
            'TriggerEvent',
            'TriggerClientEvent',
            'TriggerServerEvent',
            'emit',
            'emitNet',
            'TriggerLatentClientEvent',
            'TriggerLatentServerEvent',
        )

    @property
    def is_net_event_register(self) -> bool:
        return self.function in (
            'RegisterNetEvent',
            'RegisterServerEvent',
        )

    def is_ignored_event(self, ignored_events: List[str]) -> bool:
        return any(fnmatch.fnmatch(self.event_name, pattern) for pattern in ignored_events)

    @property
    def formatted_paths(self) -> str:
        return '\n'.join(
            map(str, self.locations.values())
        )

class CfxResource:
    MANIFEST_DEPENDENCY_KEY = re.compile(
        r'(?:^|[ \t]+)dependenc(?:y|ies)\s*',
        re.MULTILINE
    )

    MANIFEST_SCRIPT_KEY = re.compile(
        r'(?:^|[ \t]+)(?:(client|server|shared)_scripts?)\s*',
        re.MULTILINE
    )

    def __init__(self, manifest_path: Path, base_path: Path):
        self.manifest: Path = manifest_path.resolve()
        self.base_path: Path = base_path

        self.root: Path = self.manifest.parent
        self.name: str = self.root.name
        self.rel_path: Path = self.root.relative_to(self.base_path)

    def _parse_values(self, contents: str) -> Iterable[str]:
        # client_script('client.lua')
        # client_scripts({\n'client.lua'\n"main.lua"})
        if contents[0] == '(':
            istart = 1
            iend = contents.find(')', istart)
            if contents[istart] == '{':
                istart += 1
                iend = contents.index('}', istart)
                values = ast.literal_eval('[' + contents[istart:iend] + ']')
                yield from values
            else:
                value = ast.literal_eval(contents[istart:iend])
                yield value
            return

        # client_script 'client.lua'
        # server_script "main.lua"
        if contents[0] in ("'", '"'):
            istart = 1
            iend = contents.index(contents[0], istart)
            value = contents[istart:iend]
            yield value
            return

        # client_scripts {\n'client.lua'\n"main.lua"}
        if contents[0] == '{':
            istart = 1
            iend = contents.index('}', istart)
            values = ast.literal_eval('[' + contents[istart:iend] + ']')
            yield from values
            return

        # Unhandled match
        raise ValueError

    def parse_manifest(self) -> Tuple[
        Dict[str, List[str]],
        List[Tuple[Path, str]]
    ]:
        deps: Dict[str, List[str]] = {}
        files: List[Tuple[Path, str]] = []

        def add_deps(values):
            if self.name in deps:
                for value in values:
                    if value not in deps[self.name]:
                        deps[self.name].append(value)
            elif values:
                deps[self.name] = []
                for value in values:
                    if value not in deps[self.name]:
                        deps[self.name].append(value)

        try:
            contents = self.manifest.read_text('utf-8')
        except Exception as error:
            print(f'#[ERROR]# Unable to read {self.manifest!s}: {error}')
            return []

        # remove all block comments
        contents = re.sub(LUA_BLOCK_COMMENT, '', contents)
        # remove single line comments
        contents = re.sub(LUA_SINGLE_COMMENT, '', contents)

        for match in re.finditer(self.MANIFEST_DEPENDENCY_KEY, contents):
            # start of value
            start = match.end()

            values_gen = self._parse_values(contents[start:])

            try:
                add_deps(values_gen)
            except ValueError as error:
                rel_path = self.manifest.relative_to(self.base_path).as_posix()
                end = start + re.search(LINE_MAP_REGEX, contents[start:]).end()
                raise ValueError(
                    f'Error: Unhandled match in: {rel_path}'
                    f'\n{match.group()}{contents[start:end]}'
                )

        temp_files: List[Tuple[str, str]] = []

        for match in re.finditer(self.MANIFEST_SCRIPT_KEY, contents):
            # script_type (client / server / shared)
            script_type = match.group(1)
            # start of value
            start = match.end()

            values_gen = self._parse_values(contents[start:])
            try:
                for value in values_gen:
                    if value.startswith('@'):
                        add_deps([value[1:].split('/', 1)[0]])
                    else:
                        temp_files.append((value, script_type))
            except ValueError as error:
                rel_path = self.manifest.relative_to(self.base_path).as_posix()
                end = start + re.search(LINE_MAP_REGEX, contents[start:]).end()
                raise ValueError(
                    f'Error: Unhandled match in: {rel_path}'
                    f'\n{match.group()}{contents[start:end]}'
                )

        for value, script_type in temp_files:
            # Filter files by extensions
            expanded = file_suffix_filter(
                self.root.glob(value),
                ('.lua', '.js')
            )

            files += ((path, script_type) for path in expanded)

        return deps, files

class CfxConfig:
    def __init__(
        self,
        path: str,
    ):
        self.path: Path = Path(path).resolve() if path else None
        self.resources: List[str] = self.parse_resources()

        if Debug.enabled:
            resources_list = ',\n  '.join(
                ', '.join(rc)
                for rc in (
                    self.resources[i:i + 10]
                    for i in range(0, len(self.resources), 10)
                )
            )

            Debug.print(f'>>> Loaded resources: [\n  {resources_list}\n]')

    @property
    def available(self) -> bool:
        return bool(self.path) and self.path.is_file()

    def is_resource_enabled(self, name: str) -> bool:
        return name in self.resources

    def parse_resources(self) -> List[str]:
        if not self.available:
            return []

        started: Dict[str, None] = {}
        seen_paths: Set[Path] = set()

        def _parse_contents_r(path: Path) -> None:
            Debug.print(f'>>> Processing config: {path.relative_to(self.path.parent).as_posix()}')

            if path in seen_paths:
                print(f'#[WARN]# Cyclic exec {path!s}')
                return

            seen_paths.add(path)

            if not self.path.is_file():
                return

            try:
                contents = path.read_text('utf-8')
            except Exception as error:
                print(f'#[ERROR]# Unable to read {path!s}: {error}')
                return

            line_no: int
            raw_line: str
            for line_no, raw_line in enumerate(contents.splitlines(), 1):
                line = raw_line.strip()

                # Filter out empty lines and comments
                if not line or line.startswith('#'):
                    continue

                match = re.match(CFG_KEY, line)
                if not match:
                    continue

                info = match.groupdict()

                if info['command'] == 'exec':
                    new_path = self.path.parent.joinpath(info['name']).resolve()
                    _parse_contents_r(new_path)
                    continue

                if info['command'] in ('ensure', 'start', 'restart'):
                    # Ignore if already started to keep initial position
                    if info['name'] in started:
                        continue

                    started[info['name']] = None
                    continue

                if info['command'] == 'stop' and info['name'] in started:
                    del started[info['name']]
                    continue

                # raise ValueError(
                #     f'Error: Unhandled match in: {path}:{line_no}'
                #     f'\n{match}'
                # )

        _parse_contents_r(self.path)

        return list(started)

class CfxDependencyTree:
    def __init__(
        self,
        path: str,
        config_path: str,
        ignore_resources: List[str],
        ignore_paths: List[str],
    ):
        self.dependencies: Dict[str, List[str]] = dict()

        self.path: Path = Path(path).resolve()
        self.config = CfxConfig(path=config_path)

        self.ignored_resources: List[str] = list(dict.fromkeys(IGNORED_RESOURCES + ignore_resources))
        self.ignored_paths: List[Path] = [
            Path(path) for path
            in dict.fromkeys(IGNORED_PATHS + ignore_paths)
        ]

    def process(self):
        manifests: List[Path] = [
            *self.path.rglob('fxmanifest.lua'),
            *self.path.rglob('__resource.lua'),
        ]

        for manifest_path in manifests:
            resource = CfxResource(
                manifest_path=manifest_path,
                base_path=self.path,
            )

            if is_ignored_path(resource.rel_path, self.ignored_paths):
                Debug.print(f'>>> skipping IGNORED path {resource.rel_path.as_posix()}')
                continue

            Debug.print(f'>>> Found manifest: {resource.rel_path.as_posix()}')

            if self.config.available and not self.config.is_resource_enabled(resource.name):
                Debug.print(f'>>> skipping DISABLED resource {resource.name}')
                continue

            if resource.name in self.ignored_resources:
                Debug.print(f'>>> skipping IGNORED resource {resource.name}')
                continue

            # if this manifest file is in a `[name]` folder, filter it out
            if re.fullmatch(CATEGORY_FOLDER, resource.name):
                Debug.print(f">>> skipping resource {resource.name} because it's in a category folder")
                continue

            deps, files = resource.parse_manifest()
            self.dependencies.update(deps)

            for cur_path, script_type in files:
                Debug.print(f'>>> Processing {script_type} file: {cur_path.relative_to(self.path).as_posix()}')

                self.process_file(cur_path, resource, script_type)

    def process_file(self, path: Path, resource: CfxResource, script_type: str):
        try:
            contents = path.read_text('utf-8')
        except Exception as error:
            print(f'#[ERROR]# Unable to read {path!s}: {error}')
            return

        suffix = path.suffix

        if suffix == '.lua':
            pattern = LUA_EXPORTS

            # remove all block comments
            contents = re.sub(LUA_BLOCK_COMMENT, self.comment_replace, contents)
            # remove single line comments
            contents = re.sub(LUA_SINGLE_COMMENT, self.comment_replace, contents)

        elif suffix == '.js':
            pattern = JS_EXPORTS

            # remove all comments
            contents = re.sub(JS_COMMENTS, self.comment_replace, contents)
        else:
            raise ValueError('Unsupported file type')

        line_map = [m.end() for m in re.finditer(LINE_MAP_REGEX, contents)]
        for match in re.finditer(pattern, contents):
            for line_no, pos in enumerate(line_map, 1):
                if pos > match.start():
                    break

            info = match.groupdict()
            dep_name = info['resource1'] or info['resource2']

            if resource.name not in self.dependencies:
                self.dependencies[resource.name] = []

            if dep_name not in self.dependencies[resource.name]:
                Debug.print(f'Found dependency {resource.name} > {dep_name}')
                self.dependencies[resource.name].append(dep_name)

    @staticmethod
    def comment_replace(match: re.Match):
        return '\n' * match.group(0).count('\n')

    def results(self, out: str, dependents: bool):
        data: List[str] = []

        if not dependents:
            for resource_name, deps in self.dependencies.items():
                data.append(f'- {resource_name} - depends on:')
                data.extend([
                    f'  - {dep}' for dep in deps
                ])

        else:
            # dependency: [resources using it]
            dependencies_reversed = {}
            for resource_name, deps in self.dependencies.items():
                for dep in deps:
                    if dep not in dependencies_reversed:
                        temp[dep] = [resource_name]
                    else:
                        dependencies_reversed[dep].append(resource_name)

            for resource_name, dependents in dependencies_reversed.items():
                data.append(f"{resource_name} - dependent resources:")
                data.extend([
                    f'  - {dep}' for dep in dependents
                ])

        info = '\n'.join(data)
        if out:
            out_path = Path(out)
            export_to_file(info, out_path)
            print(f'Results written to: {out_path!s}', file=sys.stderr)
        elif info:
            print(info)

    def results_advanced(self):
        # https://stackoverflow.com/a/5288547
        def resolve_dependencies(arg: Dict[str, List[str]]):
            """
                Dependency resolver

            "arg" is a dependency dictionary in which
            the values are the dependencies of their respective keys.
            """
            d: Dict[str, Set[str]] = {k: set(v) for k, v in arg.items()}
            r: List[Set[str]] = []
            while d:
                # values not in keys (items without dep)
                t = set(i for v in d.values() for i in v) # - set(d.keys())
                # and keys without value (items without dep)
                t.update(k for k, v in d.items() if not v)
                # can be done right away
                r.append(t)
                # and cleaned up
                d = {k: (v - t) for k, v in d.items() if v}
            return r

        import json
        l_depedencies, l_dependents = [list(v) for v in resolve_dependencies(self.dependencies)]

        # dependents_per_resource = dict(sorted((
        #     (k, len([1 for k2, v in self.dependencies.items() if k in v]))
        #     for k in self.dependencies.keys()
        # ), key=lambda i: i[1], reverse=True))

        def bubble_sort(arr: List[str]):
            n = len(arr)

            # Traverse through all array elements
            for i in range(n):

                # Last i elements are already in place
                for j in range(0, n-i-1):

                    # traverse the array from 0 to n-i-1
                    # Swap if the element found is greater
                    # than the next element
                    if arr[j] in self.dependencies and arr[j+1] not in self.dependencies[arr[j]]:
                        arr[j], arr[j+1] = arr[j+1], arr[j]

        bubble_sort(l_depedencies)

        print(json.dumps(
            # sorted(
            #     l_depedencies,
            #     key=lambda k: len([1 for k2, v in self.dependencies.items() if k in v]),
            #     reverse=True
            # ),
            l_depedencies,
            indent=4,
        ))


def main(raw_args=None):
    parser = argparse.ArgumentParser(description='Generate a resource dependency tree')
    parser.add_argument('-o', '--out',
                        help='Dump result to file')
    parser.add_argument('-r', '--reverse', action='store_true',
                        help='Print resources for each dependency instead of dependencies for each resource')
    parser.add_argument('-d', '--debug', action='store_true')
    parser.add_argument('-ir', '--ignore-resource', action='append', default=[],
                        help='Add resource name to ignored resources (no globbing support)')
    parser.add_argument('-ip', '--ignore-path', action='append', default=[],
                        help='Add path to ignored paths - can be used to ignore complete folders (no globbing support)')
    parser.add_argument('-c', '--cfg',
                        help='Path to the server config file to only use check enabled resources')
    parser.add_argument('path',
                        help='Path to server resources folder')

    args = parser.parse_args(raw_args)

    # Set global debug level
    Debug.enabled = args.debug

    app = CfxDependencyTree(
        path=args.path,
        config_path=args.cfg,
        ignore_resources=args.ignore_resource,
        ignore_paths=args.ignore_path,
    )
    app.process()
    app.results(args.out, args.reverse)
    # app.results_advanced()


if __name__ == '__main__':
    main()
