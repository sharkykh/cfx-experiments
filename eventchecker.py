import argparse
import os
import re
from typing import Dict, List, Set

from pathlib import Path

IGNORED_FOLDER_NAMES = [
    'node_modules',
    'dist',
    '[builders]',
]

IGNORED_EVENTS = [
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

    # chat
    'chatMessage',
    'chat:addMessage',
    'chat:addSuggestion',
    'chat:removeSuggestion',
    'chat:addTemplate',
    'chat:clear',

    # sessionmanager
    'hostingSession',
    'hostedSession',

    # mapmanager
    'mapmanager:roundEnded',

    # ...
    '__cfx_internal:serverPrint',
    '__cfx_internal:commandFallback',
]


# Lua events:
#   AddEventHandler
#   RegisterNetEvent
#   TriggerEvent
#   TriggerServerEvent

LUA_EVENTS = re.compile(
    r'\b(?P<func>AddEventHandler|RegisterNetEvent|Trigger(?:Client|Server)?Event)'
    r'\(["\'](?P<event>[^"\']+)["\']\s*[,)]'
)

# JS events:
#   emit
#   emitNet
#   onNet
#   on

JS_EVENTS = re.compile(
    r'\b(?P<func>on|onNet|emit|emitNet)'
    # r"""(?:\("(?P<event>[^"]+)"|\('(?P<event>[^']+)'|\(`(?P<event>[^`]+)`"""
    # r')\s*[,)]'
    r'\(["\'](?P<event>[^"\']+)["\']\s*[,)]'
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


def file_suffix_filter(file: str):
    _, _, suffix = file.rpartition('.')
    return suffix in ('lua', 'js')


class CfxEventChecker:
    def __init__(
        self,
        path: str,
        debug: bool,
        ignore: List[str],
        ignore_dir: List[str],
    ):
        self.handlers: Dict[str, Set[str]] = dict()
        self.emitters: Dict[str, Set[str]] = dict()

        self.path = Path(path)
        self.debug = debug

        self.ignores = [name for name in ignore if name not in IGNORED_EVENTS]
        self.ignores_dirs = [name for name in ignore_dir if name not in IGNORED_FOLDER_NAMES]

    def debug_print(self, *args, **kwargs):
        if self.debug:
            print(*args, **kwargs)

    def process(self):
        for root, dirs, files in os.walk(self.path.resolve()):
            ignored_dirs = [folder for folder in (IGNORED_FOLDER_NAMES + self.ignores_dirs) if folder in dirs]
            for folder in ignored_dirs:
                dirs.remove(folder)
                self.debug_print(f'>>> skipping {folder}')

            for file in filter(file_suffix_filter, files):
                cur_path = Path(root, file)
                # self.debug_print(cur_path)
                suffix = cur_path.suffix
                if suffix == '.lua':
                    self.process_file(cur_path, LUA_EVENTS)
                elif suffix == '.js':
                    self.process_file(cur_path, JS_EVENTS)

    def process_file(self, file: Path, pattern: re.Pattern):
        try:
            contents = file.read_text('utf-8')
        except Exception as error:
            self.debug_print(f'>>> Unable to read {str(file)}')
            return

        for match in re.finditer(pattern, contents):
            self.process_match(match, file)

    def process_match(self, match: re.Match, file: Path):
        resource_path = file.relative_to(self.path).as_posix()
        result = EventMatch(match)

        if result.event_name in IGNORED_EVENTS + self.ignores:
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
                self.emitters[result.event_name] = set()

        # self.debug_print(f'File: {resource_path} | Is: {result.function} | Event: {result.event_name}')

    def results(self):
        for event_name, resources in self.handlers.items():
            if event_name not in self.emitters:
                paths = '   # ' + '\n   # '.join(resources)
                print(f'>> {event_name}\n{paths}')


def main(raw_args=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('path', help='Path to server resources folder')
    parser.add_argument('-d', '--debug', action='store_true')
    parser.add_argument('-i', '--ignore', nargs='*', action='append', default=[], help='Add event name to ignore list')
    parser.add_argument('-id', '--ignore-dir', nargs='*', action='append', default=[], help='Add folder name to ignore list')

    args = parser.parse_args(raw_args)

    app = CfxEventChecker(
        path=args.path,
        debug=args.debug,
        ignore=args.ignore,
        ignore_dir=args.ignore_dir,
    )
    app.process()
    app.results()


if __name__ == '__main__':
    main()
