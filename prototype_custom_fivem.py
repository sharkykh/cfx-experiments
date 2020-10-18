import json
import sys
from pathlib import Path
from typing import List

def remove_component(json_bak_path: Path, json_path: Path, component: str):
    with json_bak_path.open(encoding='utf-8') as fin, \
        json_path.open('w', encoding='utf-8', newline='\n') as fout:
        data: List[str] = json.load(fin)
        data.remove(component)
        json.dump(data, fout, indent=2)

def main_server(fxserver: Path):
    # - rename svadhesive.dll
    # - remove svadhesive from components.json
    svadhesive = fxserver / 'svadhesive.dll'
    svadhesive_bak = svadhesive.with_name(f'x{svadhesive.name}')
    components = fxserver / 'components.json'
    components_bak = components.with_name(f'{components.name}.bak')
    active = svadhesive_bak.is_file()

    if active:
        svadhesive_bak.rename(svadhesive)
        print(f'renamed {svadhesive_bak.name} to {svadhesive.name}')
        components_bak.replace(components)
        print(f'restored {components.name} backup')
        print('server: back to normal')
    else:
        svadhesive.rename(svadhesive_bak)
        print(f'renamed {svadhesive.name} to {svadhesive_bak.name}')
        components.replace(components_bak)
        remove_component(components_bak, components, 'svadhesive')
        print(f'removed `svadhesive` from {components.name}')

        print('server: activated')


def main_client(fivem: Path):
    # - rename adhesive.dll
    # - new file: FiveM.exe.formaldev
    # - new file: nobootstrap.txt
    # - replace the files you want (back them up)
    # - remove adhesive from components.json
    adhesive = fivem / 'adhesive.dll'
    adhesive_bak = adhesive.with_name(f'x{adhesive.name}')
    formaldev = fivem / 'FiveM.exe.formaldev'
    nobootstrap = fivem / 'nobootstrap.txt'
    components = fivem / 'components.json'
    components_bak = components.with_name(f'{components.name}.bak')

    active = adhesive_bak.is_file() or formaldev.is_file() or nobootstrap.is_file()

    if active:
        adhesive_bak.rename(adhesive)
        print(f'renamed {adhesive_bak.name} to {adhesive.name}')
        formaldev.unlink()
        print(f'deleted {formaldev.name}')
        nobootstrap.unlink()
        print(f'deleted {nobootstrap.name}')
        components_bak.replace(components)
        print(f'restored {components.name} backup')
        print('client: back to normal')
    else:
        adhesive.rename(adhesive_bak)
        print(f'renamed {adhesive.name} to {adhesive_bak.name}')
        formaldev.touch()
        print(f'created {formaldev.name}')
        nobootstrap.touch()
        print(f'created {nobootstrap.name}')
        components.replace(components_bak)
        remove_component(components_bak, components, 'adhesive')
        print(f'removed `adhesive` from {components.name}')
        print('client: activated')

def main():
    if len(sys.argv[1:]) != 1:
        print('Path to "FiveM Application Data" or "FXServer" as first and only argument')
        return

    path = Path(sys.argv[1]).resolve()
    if not path.is_dir():
        print('Provide a path to a valid folder')

    if (path / 'CitizenFX.ini').is_file():
        return main_client(path)

    if (path / 'FXServer.exe').is_file():
        return main_server(path)

    print('FiveM/FXServer not detected')

if __name__ == '__main__':
    try:
        main()
    finally:
        input('press ENTER to exit')
