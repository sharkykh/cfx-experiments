import ctypes

ipaddr = '127.0.0.1'

ctypes.windll.shell32.ShellExecuteW(None, None, f'fivem://connect/{ipaddr}', None, None, 1)
