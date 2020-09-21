# config: utf-8
"""
Script that scans FXServer resources in search of possible non-emitted/non-triggered events.
Requires Python 3.7+

https://gist.github.com/sharkykh/e57ba52e70c8d1f060cf5c952fff9b75
"""

import argparse
import ast
import fnmatch
import re
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

# A list of ignored event names, can use globs (*, ?)
IGNORED_EVENTS = [
    '__cfx_internal:*',
    # NUI Callback Events (JS)
    '__cfx_nui:*',

    # Core events
    'gameEventTriggered',
    'onClientResourceStart',
    'onClientResourceStop',
    'onResourceStart',
    'onResourceStarting',
    'onResourceStop',
    'onServerResourceStart',
    'onServerResourceStop',
    'onResourceListRefresh',
    'playerConnecting',
    'playerDropped',
    'populationPedCreating',
    'rconCommand',

    # OneSync events
    'weaponDamageEvent',
    'vehicleComponentControlEvent',
    'respawnPlayerPedEvent',
    'explosionEvent',
    'entityCreated',
    'entityCreating',
    'entityRemoved',

    # OneSync Bigmode/infinity events
    'playerEnteredScope',
    'playerLeftScope',

    # baseevents
    'baseevents:*',

    # chat
    'chatMessage',
    'chat:*',

    # sessionmanager
    'hostingSession',
    'hostedSession',
    'sessionHostResult',

    # spawnmanager
    'playerSpawned',

    # mapmanager
    'mapmanager:*',
    'onClientMapStart',
    'onClientMapStop',
    'onClientGameTypeStart',
    'onClientGameTypeStop',
    'onMapStart',
    'onMapStop',
    'onGameTypeStart',
    'onGameTypeStop',
]

# Lua events:
#   AddEventHandler
#   TriggerEvent
#   TriggerClientEvent
#   TriggerServerEvent
#   RegisterNetEvent
#       RegisterServerEvent

LUA_EVENTS = re.compile(
    r'(?:^|[ \t]+)(?P<func>'
        r'AddEventHandler|Trigger(?:Client|Server)?Event'
        r'|Register(?:Net|Server)Event'
    r')'
    r'\(\s*["\'](?P<event>[^"\']+)["\']\s*[,)]',
    re.MULTILINE
)

# JS events:
#   on
#       addEventListener
#       AddEventHandler
#   onNet
#       addNetEventListener
#   emit
#       TriggerEvent
#   emitNet
#       TriggerClientEvent
#       TriggerServerEvent
#       TriggerLatentClientEvent
#       TriggerLatentServerEvent
#   RegisterNetEvent
#       RegisterServerEvent

JS_EVENTS = re.compile(
    r'(?:^|[ \t]+)(?P<func>'
        r'on|onNet|emit|emitNet'
        r'|add(?:Net)?EventListener|AddEventHandler'
        r'|Trigger(?:(?:Latent)?(?:Client|Server))?Event'
        r'|Register(?:Net|Server)Event'
    r')'
    r'\(\s*["\'](?P<event>[^"\']+)["\']\s*[,)]',
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

    def parse_manifest(self) -> List[Tuple[Path, str]]:
        files: List[Tuple[Path, str]] = []

        try:
            contents = self.manifest.read_text('utf-8')
        except Exception as error:
            print(f'#[ERROR]# Unable to read {self.manifest!s}: {error}')
            return []

        temp_files: List[Tuple[str, str]] = []

        # remove all block comments
        contents = re.sub(LUA_BLOCK_COMMENT, '', contents)
        # remove single line comments
        contents = re.sub(LUA_SINGLE_COMMENT, '', contents)

        for match in re.finditer(self.MANIFEST_SCRIPT_KEY, contents):
            # script_type (client / server / shared)
            script_type = match.group(1)
            # start of value
            start = match.end()

            values_gen = self._parse_values(contents[start:])

            try:
                temp_files += (
                    (v, script_type) for v in values_gen
                )
            except ValueError as error:
                rel_path = self.manifest.relative_to(self.base_path).as_posix()
                end = start + re.search(LINE_MAP_REGEX, contents[start:]).end()
                raise ValueError(
                    f'Error: Unhandled match in: {rel_path}'
                    f'\n{match.group()}{contents[start:end]}'
                )

        for value, script_type in temp_files:
            # Filter out dependencies
            if value.startswith('@'):
                continue

            # Filter files by extensions
            expanded = file_suffix_filter(
                self.root.glob(value),
                ('.lua', '.js')
            )

            files += ((path, script_type) for path in expanded)

        return files

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

class CfxEventChecker:
    def __init__(
        self,
        path: str,
        config_path: str,
        ignore_events: List[str],
        ignore_resources: List[str],
        ignore_paths: List[str],
    ):
        self.handlers: Dict[str, EventMatch] = dict()
        self.emitters: Dict[str, EventMatch] = dict()
        self.registers: Dict[str, EventMatch] = dict()

        self.path: Path = Path(path).resolve()
        self.config = CfxConfig(path=config_path)

        self.ignored_events: List[str] = list(dict.fromkeys(IGNORED_EVENTS + ignore_events))
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

            for cur_path, script_type in resource.parse_manifest():
                Debug.print(f'>>> Processing {script_type} file: {cur_path.relative_to(self.path).as_posix()}')

                self.process_file(cur_path, script_type)

    def process_file(self, path: Path, script_type: str):
        try:
            contents = path.read_text('utf-8')
        except Exception as error:
            print(f'#[ERROR]# Unable to read {path!s}: {error}')
            return

        suffix = path.suffix

        if suffix == '.lua':
            pattern = LUA_EVENTS

            # remove all block comments
            contents = re.sub(LUA_BLOCK_COMMENT, self.comment_replace, contents)
            # remove single line comments
            contents = re.sub(LUA_SINGLE_COMMENT, self.comment_replace, contents)

        elif suffix == '.js':
            pattern = JS_EVENTS

            # remove all comments
            contents = re.sub(JS_COMMENTS, self.comment_replace, contents)
        else:
            raise ValueError('Unsupported file type')

        line_map = [m.end() for m in re.finditer(LINE_MAP_REGEX, contents)]
        for match in re.finditer(pattern, contents):
            for line_no, pos in enumerate(line_map, 1):
                if pos > match.start():
                    break

            self.process_match(match, path, line_no, script_type)

    def process_match(self, match: re.Match, resource_path: Path, line_no: int, script_type: str):
        result = EventMatch(match)

        if result.is_ignored_event(self.ignored_events):
            Debug.print(f'>>> skipping IGNORED event {result.event_name}')
            return

        if result.is_event_handler:
            if result.event_name not in self.handlers:
                self.handlers[result.event_name] = result

            self.handlers[result.event_name].add(resource_path, line_no, script_type)

        if result.is_event_emitter:
            if result.event_name not in self.emitters:
                self.emitters[result.event_name] = result

            self.emitters[result.event_name].add(resource_path, line_no, script_type)

        if result.is_net_event_register:
            if result.event_name not in self.registers:
                self.registers[result.event_name] = result

            self.registers[result.event_name].add(resource_path, line_no, script_type)

        # Debug.print(f'File: {resource_path} [{script_type}] | Is: {result.function} | Event: {result.event_name}')

    @staticmethod
    def comment_replace(match: re.Match):
        return '\n' * match.group(0).count('\n')

    def results(self, out: str, triggers: bool):
        data: List[str] = []

        if triggers:
            data += [
                'Listing file paths for triggered events,',
                'that **possibly** do not have defined handlers anywhere',
            ]

            check = self.emitters.values()
            compare = self.handlers
        else:
            data += [
                'Listing file paths for events that have defined handlers,',
                'and are **possibly** not triggered anywhere',
            ]

            check = self.handlers.values()
            compare = self.emitters

        data += [
            '**Tip:** Copy path line, Ctrl+P and paste to quickly jump to location',
            '',
        ]

        for match in check:
            if match.event_name not in compare:
                data += [
                    f'# {match.event_name}',
                    match.formatted_paths,
                    '',
                ]


        info = '\n'.join(data)
        if out:
            out_path = Path(out)
            export_to_file(info, out_path)
            print(f'Results written to: {out_path!s}')
        elif info:
            print(info)


def main(raw_args=None):
    parser = argparse.ArgumentParser(description='Look for possible non-emitted/non-triggered events')
    parser.add_argument('-o', '--out',
                        help='Dump result to file')
    parser.add_argument('-r', '--reverse', action='store_true',
                        help='Look for possible non-handled event emitters/triggers instead')
    parser.add_argument('-d', '--debug', action='store_true')
    parser.add_argument('-i', '--ignore', action='append', default=[],
                        help='Add event name to ignored events (can use globbing - * ?)')
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

    app = CfxEventChecker(
        path=args.path,
        config_path=args.cfg,
        ignore_events=args.ignore,
        ignore_resources=args.ignore_resource,
        ignore_paths=args.ignore_path,
    )
    app.process()
    app.results(args.out, args.reverse)


if __name__ == '__main__':
    main()
