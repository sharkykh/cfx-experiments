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


CATEGORY_FOLDER = re.compile(
    r'\[[^\]]+\]'
)

MANIFEST_SCRIPT_KEY = re.compile(
    r'^((?:client|server|shared)_scripts?)\s*',
    re.MULTILINE
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


class EventMatch:
    def __init__(self, match: re.Match):
        self.data: Dict[str, str] = match.groupdict()
        self.locations: Dict[Path, int] = {}

    def add(self, path: Path, line: int):
        self.locations[path] = line

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
            f'{path!s}:{line}'
            for path, line in self.locations.items()
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

def is_ignored_path(manifest: Path, paths: List[str]):
    return any(path in manifest.parents for path in paths)

class CfxEventChecker:
    def __init__(
        self,
        path: str,
        debug: bool,
        ignore_events: List[str],
        ignore_resources: List[str],
        ignore_paths: List[str],
    ):
        self.handlers: Dict[str, EventMatch] = dict()
        self.emitters: Dict[str, EventMatch] = dict()
        self.registers: Dict[str, EventMatch] = dict()

        self.path = Path(path).resolve()
        self.debug = debug

        self.ignored_events: List[str] = list(dict.fromkeys(IGNORED_EVENTS + ignore_events))
        self.ignored_resources: List[str] = list(dict.fromkeys(IGNORED_RESOURCES + ignore_resources))
        self.ignored_paths: List[str] = [
            Path(path) for path
            in dict.fromkeys(IGNORED_PATHS + ignore_paths)
        ]

    def debug_print(self, *args, **kwargs):
        if self.debug:
            print(*args, **kwargs)

    def parse_resource_manifest(self, manifest_path: Path) -> List[Path]:
        files: List[Path] = []

        resource_path: Path = manifest_path.parent
        resource_name = resource_path.name

        if resource_name in self.ignored_resources:
            self.debug_print(f'>>> skipping IGNORED resource {resource_name}')
            return []

        # if this manifest file is in a `[name]` folder, filter it out
        if re.fullmatch(CATEGORY_FOLDER, resource_name):
            self.debug_print(f">>> skipping resource {resource_name} because it's in a category folder")
            return []

        try:
            contents = manifest_path.read_text('utf-8')
        except Exception as error:
            print(f'#[ERROR]# Unable to read {manifest_path!s}: {error}')
            return []

        temp_files: List[str] = []

        # remove all block comments
        contents = re.sub(LUA_BLOCK_COMMENT, '', contents)
        # remove single line comments
        contents = re.sub(LUA_SINGLE_COMMENT, '', contents)

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
            rel_path = manifest_path.relative_to(self.path).as_posix()
            raise ValueError(f'Error: Unhandled match in {rel_path}\n{match}')

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
            rel_path = manifest_path.relative_to(self.path)

            if is_ignored_path(rel_path, self.ignored_paths):
                self.debug_print(f'>>> skipping IGNORED path {rel_path.as_posix()}')
                continue

            self.debug_print(f'>>> Found manifest: {rel_path.as_posix()}')

            for cur_path in self.parse_resource_manifest(manifest_path):
                self.debug_print(f'>>> Processing file: {cur_path.relative_to(self.path).as_posix()}')

                self.process_file(cur_path)

    def process_file(self, path: Path):
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

        line_map = [m.end() for m in re.finditer(r'.*(\n|$)', contents)]
        for match in re.finditer(pattern, contents):
            for line_no, pos in enumerate(line_map, 1):
                if pos > match.start():
                    break

            self.process_match(match, path, line_no)

    def process_match(self, match: re.Match, resource_path: Path, line_no: int):
        result = EventMatch(match)

        if result.is_ignored_event(self.ignored_events):
            self.debug_print(f'>>> skipping IGNORED event {result.event_name}')
            return

        if result.is_event_handler:
            if result.event_name not in self.handlers:
                self.handlers[result.event_name] = result

            self.handlers[result.event_name].add(resource_path, line_no)

        if result.is_event_emitter:
            if result.event_name not in self.emitters:
                self.emitters[result.event_name] = result

            self.emitters[result.event_name].add(resource_path, line_no)

        if result.is_net_event_register:
            if result.event_name not in self.registers:
                self.registers[result.event_name] = result

            self.registers[result.event_name].add(resource_path, line_no)

        # self.debug_print(f'File: {resource_path} | Is: {result.function} | Event: {result.event_name}')

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
                        help='Add event name to ignore list (can use globbing - * ?)')
    parser.add_argument('-ir', '--ignore-resource', action='append', default=[],
                        help='Add resource name to ignore list (no globbing support)')
    parser.add_argument('-ip', '--ignore-path', action='append', default=[],
                        help='Add an ignored path to ignore list - can be used to ignore complete folders (no globbing support)')
    parser.add_argument('path',
                        help='Path to server resources folder')

    args = parser.parse_args(raw_args)

    app = CfxEventChecker(
        path=args.path,
        debug=args.debug,
        ignore_events=args.ignore,
        ignore_resources=args.ignore_resource,
        ignore_paths=args.ignore_path,
    )
    app.process()
    app.results(args.out, args.reverse)


if __name__ == '__main__':
    main()
