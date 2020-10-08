import ast
import re
import sys
from pathlib import Path
from typing import (
    Dict,
    Iterable,
    List,
    Match,
    Tuple,
    Union,
)

LUA_BLOCK_COMMENT = re.compile(
    r'--\[\[.*?\]\](?:--)?',
    re.DOTALL
)

LUA_SINGLE_COMMENT = re.compile(
    r'--(?!\[\[).+$\r?\n',
    re.MULTILINE
)

LINE_MAP_REGEX = re.compile(
    r'.*(\n|$)'
)


class CfxResource:
    MANIFEST_KEY = re.compile(
        r'(?:^[ \t]*)(?P<key>\w+)\s*',
        re.MULTILINE
    )

    _manifest_major_versions = [
        '__resource.lua',
        'fxmanifest.lua',
    ]

    def __init__(self, manifest_path: Union[Path, str]):
        self.manifest_path = (manifest_path if isinstance(manifest_path, Path) else Path(manifest_path)).resolve()

        try:
            self.major_version = self._manifest_major_versions.index(self.manifest_path.name) + 1
        except IndexError:
            print(f'Error: Unable to parse manifest major version from file name: {self.manifest_path.name}')
            return

        self._data = self._parse_manifest()

    @property
    def data(self):
        return self._data

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

                # if value in ('yes', '1'):
                #     value = True
                # elif value in ('no', '0'):
                #     value = False

                yield value
            return

        # client_script 'client.lua'
        # server_script "main.lua"
        if contents[0] in ("'", '"'):
            istart = 1
            iend = contents.index(contents[0], istart)
            value = contents[istart:iend]

            # if value in ('yes', '1'):
            #     value = True
            # elif value in ('no', '0'):
            #     value = False

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

    def _parse_manifest(self) -> Dict[Union[str, Tuple[str]], List[str]]:
        data = {}

        try:
            contents = self.manifest_path.read_text('utf-8')
        except Exception as error:
            print(f'#[ERROR]# Unable to read {self.manifest_path!s}: {error}')
            return {}

        def comment_replace(match: Match[str]):
            return '\n' * match.group(0).count('\n')

        # remove all block comments
        contents = re.sub(LUA_BLOCK_COMMENT, comment_replace, contents)
        # remove single line comments
        contents = re.sub(LUA_SINGLE_COMMENT, comment_replace, contents)

        line_map = [m.end() for m in re.finditer(LINE_MAP_REGEX, contents)]
        line_no = -1
        next_index = 0

        for match in self.MANIFEST_KEY.finditer(contents):
            for line_no, pos in enumerate(line_map[next_index:], next_index + 1):
                if pos > match.start():
                    next_index = line_no
                    break

            info = match.groupdict()

            # key name
            key = info['key']
            key2 = None

            # start of value
            start = match.end()

            if key in ('data_file', 'data_files'):
                istart = start + 1
                iend = contents.index(contents[start], istart)
                key2 = contents[istart:iend]
                start = iend + 2

            values_gen = self._parse_values(contents[start:])

            try:
                data_key = (key, key2) if key2 else key
                if data_key not in data:
                    data[data_key] = []

                data[data_key] += values_gen
            except ValueError as error:
                end = start + re.search(LINE_MAP_REGEX, contents[start:]).end()
                raise ValueError(
                    f'Error: Unhandled match in: {self.manifest_path!s}:{line_no}'
                    f'\n{match.group()}{contents[match.end():end]}'
                )

        return data

    @property
    def manifest_version(self) -> str:
        if self.major_version == 1:
            if 'resource_manifest_version' not in self._data:
                raise KeyError(f'no version for ' + str(self.manifest_path))

            return self._data['resource_manifest_version'][0]

        if self.major_version == 2:
            if 'fx_version' not in self._data:
                raise KeyError(f'no version for ' + str(self.manifest_path))

            return self._data['fx_version'][0]

        raise KeyError(f'unmatched major version for ' + str(self.manifest_path))

def print_test(resource: CfxResource):
    for key, values in resource.data.items():
        if len(values) == 1:
            values = values[0]
        else:
            values = '\n  ' + '\n  '.join(values)

        if isinstance(key, tuple):
            print(key[0], key[1], values)
        else:
            print(key, values)

    # print('version', resource.manifest_version)
    # print('path', str(resource.manifest_path))

def main(raw_args: List[str]):
    if len(raw_args) == 0:
        cwd = Path().resolve()
        paths: List[Union[Path, str]] = [
            *cwd.rglob('fxmanifest.lua'),
            *cwd.rglob('__resource.lua'),
        ]
    elif len(raw_args) == 1:
        paths: List[Union[Path, str]] = [raw_args[0]]
    else:
        print('Error: Needs manifest path')
        return

    for path in paths:
        resource = CfxResource(
            manifest_path=path
        )
        print_test(resource)


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    finally:
        input('Press ENTER to continue')
