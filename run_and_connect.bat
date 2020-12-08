@ECHO OFF

:: Server port number
SET SERVER_PORT=30120

:: Path to FXServer.exe
SET SERVER_EXE="C:\FiveM\server\FXServer.exe"
:: Path to server-data folder
SET SERVER_DATA="C:\FiveM\server\server-data"

:: Run server (use your server arguments here, paths are relative to the "SERVER_DATA" path)
START /D %SERVER_DATA% "" %SERVER_EXE% +exec server.cfg +set onesync on +set svgui_disable 1

:: Run FiveM and connect to localhost
START "" "fivem://connect/localhost:%SERVER_PORT%"
