@echo off
set OUT=C:\Users\meko srl\.cursor\Matteo_folder\SGAI\_d_root_2025_list.txt
echo Listing D:\Sentenza_*_2025.pdf ...
dir /b "D:\Sentenza_*_2025.pdf" > "%OUT%" 2>nul
echo DONE
find /c /v "" "%OUT%"
