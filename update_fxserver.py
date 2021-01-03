import argparse
import ctypes
import random
import string
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple, Optional, cast
from urllib.parse import urljoin
from zipfile import ZipFile

import bs4
import requests


def debug_file():
    return Path.cwd().resolve() / 'page.html'

def get_random_string(length):
    chars = string.ascii_letters + string.digits
    return ''.join(random.sample(chars, length))

class Artifact(NamedTuple):
    version: int
    published: Optional[datetime]
    url: str

def get_api_latest_artifact(debug: bool = False):
    if debug:
        base = 'https://runtime.fivem.net/artifacts/fivem/build_server_windows/master/'
        data = {
            'recommended': '2967',
            'optional': '3071',
            'latest': '3155',
            'critical': '2524',
            'recommended_download': urljoin(base, '2967-2b71645c6a0aa659e8df6ac34a3a1e487e95aedb/server.zip'),
            'optional_download': urljoin(base, '3071-31b78e9d17dcf63887a5abe0bc36c9f886b2fc3b/server.zip'),
            'latest_download': urljoin(base, '3155-0d1e9a970c3722847642e71abb36d833057f6402/server.zip'),
            'critical_download': urljoin(base, '2524-c1cb49c3aef1ad58d622a34de3bdbaf66f7dd0bb/server.zip'),
        }
    else:
        resp = requests.get(
            url='https://changelogs-live.fivem.net/api/changelog/versions/win32/server',
            # params={get_random_string(5): get_random_string(5)},
            headers={'User-Agent': 'FXServer Updater Tool/v0.1'},
        )
        data = resp.json()

    return Artifact(
        version=int(data['latest']),
        published=None,
        url=data['latest_download'],
    )

def get_artifact(is_debug):
    base_url = 'https://runtime.fivem.net/artifacts/fivem/build_server_windows/master/'

    if is_debug:
        df = debug_file()
        print(f'using {df}')
        body = df.read_text('utf-8')
    else:
        resp = requests.get(
            url=base_url,
            params={get_random_string(5): get_random_string(5)},
            headers={'User-Agent': 'FXServer Updater Tool/v0.1'},
        )
        body = resp.text

    soup = bs4.BeautifulSoup(body, 'html.parser')
    for element in soup.select('nav > a.panel-block:not([href=".."])'):
        version = element.select_one('.level-left').get_text(strip=True)
        published = element.select_one('.level-right > .level-item').get_text(strip=True)
        relative_url = element['href']
        yield Artifact(
            version=int(version),
            published=datetime.fromisoformat(published).replace(tzinfo=timezone.utc),
            url=urljoin(base_url, relative_url),
        )

    del soup

def get_latest_artifact(is_debug: bool) -> Optional[Artifact]:
    try:
        return get_api_latest_artifact(is_debug)
    except Exception:
        pass

    try:
        return next(get_artifact(is_debug))
    except StopIteration:
        print('no artifacts found?')
        return

def download(artifact: Artifact, path: Path) -> bool:
    with requests.get(artifact.url, stream=True) as r:
        r.raise_for_status()

        cur_size = 0
        size = int(r.headers['Content-Length'])

        print(f'downloading server artifact {artifact.version}... ', end='')
        with path.open('wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
                cur_size += len(chunk)
                done = int((cur_size / size) * 100)
                sys.stdout.write(f'\rdownloading server artifact {artifact.version}... {done}%')
                sys.stdout.flush()

    sys.stdout.write('\n')
    sys.stdout.flush()

    return True

def get_server_artifact_version(server: Path) -> int:
    try:
        server_version = get_version_string(
            str(server / 'citizen-server-impl.dll'),
            'FileVersion',
        )
        return int(server_version.rsplit('.', 1)[1])
    except FileNotFoundError:
        return -1

class Arguments(argparse.Namespace):
    artifact_version: int


def main(raw_args=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('artifact_version', type=int, nargs='?', default=-1)

    args: Arguments = parser.parse_args(raw_args)  # type: ignore

    # here = Path(__file__).parent
    here = Path.cwd().resolve()
    server = here / 'server'
    is_debug = debug_file().is_file()

    current_artifact = get_server_artifact_version(server)
    wanted_artifact = args.artifact_version

    print(f'current artifact: {current_artifact}')

    if wanted_artifact <= -1:
        artifact = get_latest_artifact(is_debug)
        latest_str = 'latest'

        if not artifact:
            return

        if artifact.version < current_artifact:
            print(f'{latest_str} artifact is older than current')
            return
    else:
        try:
            artifact = next(a for a in get_artifact(is_debug) if a.version == wanted_artifact)
        except StopIteration:
            print(f'artifact {wanted_artifact} not found')
            return
        latest_str = 'requested'

    if current_artifact == artifact.version:
        print(f'{latest_str} artifact is already installed')
        return
    else:
        print(f'{latest_str} artifact: {artifact.version}')

    path = here / f'server_{artifact.version}.zip'

    if not path.is_file():
        result = download(artifact, path)

        if not result:
            return

    server.rename(f'server_{current_artifact}')

    with ZipFile(path) as zf:
        zf.extractall(server)

    # path.unlink()

    # Just make sure the new version is correct
    if artifact.version != get_server_artifact_version(server):
        print(f'installed version is {current_artifact}, but shows up as {artifact.version}')

    print('done')

def get_version_string(filename, what, language=None):
    """
    returns the requested version information from the given file

    `language` should be an 8-character string combining both the language and
    codepage (such as "040904b0"); if None, the first language in the translation
    table is used instead
    """
    # VerQueryValue() returns an array of that for VarFileInfo\Translation
    #
    class LANGANDCODEPAGE(ctypes.Structure):
        _fields_ = [
            ("wLanguage", ctypes.c_uint16),
            ("wCodePage", ctypes.c_uint16)]

    wstr_file = ctypes.wstring_at(filename)

    # getting the size in bytes of the file version info buffer
    size = ctypes.windll.version.GetFileVersionInfoSizeW(wstr_file, None)
    if size == 0:
        raise ctypes.WinError()

    buffer = ctypes.create_string_buffer(size)

    # getting the file version info data
    if ctypes.windll.version.GetFileVersionInfoW(wstr_file, None, size, buffer) == 0:
        raise ctypes.WinError()

    # VerQueryValue() wants a pointer to a void* and DWORD; used both for
    # getting the default language (if necessary) and getting the actual data
    # below
    value = ctypes.c_void_p(0)
    value_size = ctypes.c_uint(0)

    if language is None:
        # file version information can contain much more than the version
        # number (copyright, application name, etc.) and these are all
        # translatable
        #
        # the following arbitrarily gets the first language and codepage from
        # the list
        ret = ctypes.windll.version.VerQueryValueW(
            buffer, ctypes.wstring_at(r"\VarFileInfo\Translation"),  # type: ignore
            ctypes.byref(value), ctypes.byref(value_size))

        if ret == 0:
            raise ctypes.WinError()

        # value points to a byte inside buffer, value_size is the size in bytes
        # of that particular section

        # casting the void* to a LANGANDCODEPAGE*
        lcp = ctypes.cast(value, ctypes.POINTER(LANGANDCODEPAGE))

        # formatting language and codepage to something like "040904b0"
        language = "{0:04x}{1:04x}".format(
            lcp.contents.wLanguage, lcp.contents.wCodePage)  # type: ignore

    # getting the actual data
    res = ctypes.windll.version.VerQueryValueW(
        buffer, ctypes.wstring_at("\\StringFileInfo\\" + language + "\\" + what),
        ctypes.byref(value), ctypes.byref(value_size))

    if res == 0:
        raise ctypes.WinError()

    # value points to a string of value_size characters, minus one for the
    # terminating null
    return ctypes.wstring_at(cast(int, value.value), value_size.value - 1)

if __name__ == '__main__':
    main()
    # try:
    #     main()
    # finally:
    #     input('press ENTER to exit')
