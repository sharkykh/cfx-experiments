import argparse
import ast
import fnmatch
import re
from typing import (
    Dict,
    List,
    Set,
)

from pathlib import Path

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

LUA_EVENTS = re.compile(
    r'^\s*(?P<func>AddEventHandler|Trigger(?:Client|Server)?Event|RegisterNetEvent)'
    r'\(["\'](?P<event>[^"\']+)["\']\s*[,)]',
    re.MULTILINE
)

# JS events:
#   emit
#   emitNet
#   on
#   onNet

JS_EVENTS = re.compile(
    r'^\s*(?P<func>on|onNet|emit|emitNet)'
    r'\(["\'](?P<event>[^"\']+)["\']\s*[,)]',
    re.MULTILINE
)


CATEGORY_FOLDER = re.compile(
    r'\[[^\]]+\]'
)

MANIFEST_SCRIPT_KEY = re.compile(
    r'^((?:client|server|shared)_scripts?)\s*',
    re.MULTILINE
)

MANIFEST_MULTI_COMMENT = re.compile(
    r'--\[\[[^\]]*\]\](?:--)?'
)

MANIFEST_SINGLE_COMMENT = re.compile(
    r'--(?!\[\[).+$\r?\n',
    re.MULTILINE
)


class EventMatch:
    def __init__(self, match: re.Match):
        self.data: Dict[str, str] = match.groupdict()

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
        )

    @property
    def is_event_emitter(self) -> bool:
        return self.function in (
            'TriggerEvent',
            'TriggerClientEvent',
            'TriggerServerEvent',
            'emit',
            'emitNet',
        )

def export_to_file(data: str, file: Path):
    if file.is_file():
        answer = input(f'{file!s} already exists, overwrite? [Y/n] ').strip().lower()
        if answer and answer != 'y':
            return False

    with file.open('w', encoding='utf-8', newline='\n') as fh:
        fh.write(data)

    return True

def file_suffix_filter(files: List[Path], suffixes: List[str]):
    for path in files:
        if path.suffix in suffixes:
            yield path

def is_ignored_event(name: str, patterns: List[str]):
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)

class CfxEventChecker:
    def __init__(
        self,
        path: str,
        debug: bool,
        ignore_events: List[str],
        ignore_resource: List[str],
    ):
        self.handlers: Dict[str, Set[str]] = dict()
        self.emitters: Dict[str, Set[str]] = dict()

        self.path = Path(path).resolve()
        self.debug = debug

        self.ignored_events: List[str] = list(dict.fromkeys(IGNORED_EVENTS + ignore_events))
        self.ignored_resources: List[str] = list(dict.fromkeys(IGNORED_RESOURCES + ignore_resource))

    def debug_print(self, *args, **kwargs):
        if self.debug:
            print(*args, **kwargs)

    def parse_resource_manifest(self, manifest_path: Path) -> List[Path]:
        files: List[Path] = []

        resource_path: Path = manifest_path.parent
        resource_name = resource_path.name

        if resource_name in self.ignored_resources:
            self.debug_print(f'# skipping resource {resource_name}')
            return []

        # if this manifest file is in a `[name]` folder, filter it out
        if re.fullmatch(CATEGORY_FOLDER, resource_name):
            self.debug_print(f'# skipping resource {resource_name}')
            return []

        try:
            contents = manifest_path.read_text('utf-8')
        except Exception as error:
            print(f'#[ERROR]# Unable to read {manifest_path!s}: {error}')
            return []

        temp_files: List[str] = []

        # remove all multiline comments
        contents = re.sub(MANIFEST_MULTI_COMMENT, '', contents)
        # remove single line comments
        contents = re.sub(MANIFEST_SINGLE_COMMENT, '', contents)

        for match in re.finditer(MANIFEST_SCRIPT_KEY, contents):
            # start of value
            start = match.end()

            # client_script('client.lua')
            # client_scripts({\n'client.lua'\n"main.lua"})
            if contents[start] == '(':
                istart = start + 1
                iend = contents.find(')', istart)
                if contents[istart] == '{':
                    istart += 1
                    iend = contents.index('}', istart)
                    values = ast.literal_eval('[' + contents[istart:iend] + ']')
                    temp_files.extend(values)
                else:
                    value = ast.literal_eval(contents[istart:iend])
                    temp_files.append(value)
                continue

            # client_script 'client.lua'
            # server_script "main.lua"
            if contents[start] in ("'", '"'):
                istart = start + 1
                iend = contents.index(contents[start], istart)
                value = contents[istart:iend]
                temp_files.append(value)
                continue

            # client_scripts {\n'client.lua'\n"main.lua"}
            if contents[start] == '{':
                istart = start + 1
                iend = contents.index('}', istart)
                values = ast.literal_eval('[' + contents[istart:iend] + ']')
                temp_files.extend(values)
                continue

            # Unhandled cases
            raise ValueError(f'Error: Unhandled match in {manifest_path.relative_to(self.path).as_posix()}\n{match}')

        for value in temp_files:
            if value.startswith('@'):
                continue

            # Filter files by extensions
            files += file_suffix_filter(
                resource_path.glob(value),
                ('.lua', '.js')
            )

        return files

    def process(self):
        manifests: List[Path] = [
            *self.path.rglob('fxmanifest.lua'),
            *self.path.rglob('__resource.lua'),
        ]

        for manifest_path in manifests:
            self.debug_print(f'>>> Found manifest: {manifest_path.relative_to(self.path).as_posix()}')

            for cur_path in self.parse_resource_manifest(manifest_path):
                self.debug_print(f'>>> Processing file: {cur_path.relative_to(self.path).as_posix()}')

                event_patterns = {
                    '.lua': LUA_EVENTS,
                    '.js': JS_EVENTS,
                }
                self.process_file(cur_path, event_patterns.get(cur_path.suffix))

    def process_file(self, file: Path, pattern: re.Pattern):
        try:
            contents = file.read_text('utf-8')
        except Exception as error:
            print(f'#[ERROR]# Unable to read {file!s}: {error}')
            return

        for match in re.finditer(pattern, contents):
            self.process_match(match, file)

    def process_match(self, match: re.Match, file: Path):
        resource_path = file.relative_to(self.path).as_posix()
        result = EventMatch(match)

        if is_ignored_event(result.event_name, self.ignored_events):
            self.debug_print(f'>>> skipping IGNORED event {result.event_name}')
            return

        if result.is_event_handler:
            if result.event_name in self.handlers:
                self.handlers[result.event_name].add(resource_path)
            else:
                self.handlers[result.event_name] = {resource_path}

        if result.is_event_emitter:
            if result.event_name in self.emitters:
                self.emitters[result.event_name].add(resource_path)
            else:
                self.emitters[result.event_name] = {resource_path}

        # self.debug_print(f'File: {resource_path} | Is: {result.function} | Event: {result.event_name}')

    def results(self, out: str):
        data: List[str] = []
        for event_name, resources in self.handlers.items():
            if event_name not in self.emitters:
                paths = '   @ ' + '\n   @ '.join(resources)
                data.append(f'>> {event_name}\n{paths}')

        info = '\n'.join(data)
        if out:
            out_path = Path(out)
            export_to_file(info + '\n', out_path)
            print(f'Results written to: {out_path!s}')
        elif info:
            print(info)

    def results_reverse(self, out: str):
        data: List[str] = []
        for event_name, resources in self.emitters.items():
            if event_name not in self.handlers:
                paths = '   @ ' + '\n   @ '.join(resources)
                data.append(f'>> {event_name}\n{paths}')

        info = '\n'.join(data)
        if out:
            out_path = Path(out)
            export_to_file(info + '\n', out_path)
            print(f'Results written to: {out_path!s}')
        elif info:
            print(info)


def main(raw_args=None):
    parser = argparse.ArgumentParser(description='Look for possible non-emitted/non-triggered events')
    parser.add_argument('-o', '--out', help='Dump result to file')
    parser.add_argument('-r', '--reverse', action='store_true', help='Look for possible non-handled event emitters/triggers instead')
    parser.add_argument('-d', '--debug', action='store_true')
    parser.add_argument('-i', '--ignore', action='append', default=[], help='Add event name to ignore list (can use globbing - * ?)')
    parser.add_argument('-ir', '--ignore-resource', action='append', default=[], help='Add resource name to ignore list (no globbing support)')
    parser.add_argument('path', help='Path to server resources folder')

    args = parser.parse_args(raw_args)

    app = CfxEventChecker(
        path=args.path,
        debug=args.debug,
        ignore_events=args.ignore,
        ignore_resource=args.ignore_resource,
    )
    app.process()
    if args.reverse:
        app.results_reverse(args.out)
    else:
        app.results(args.out)


if __name__ == '__main__':
    main()
