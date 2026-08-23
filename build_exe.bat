@echo off
chcp 65001 >nul
REM ============================================================
REM 桌灵 DeskSprite 一键打包脚本（PyInstaller）
REM 在安装好全部依赖的机器上运行（python -m pip install -r requirements.txt）
REM 产物：dist\DeskSprite.exe（单文件，双击即启动，无控制台窗口）
REM 首次运行会在 exe 同目录生成 settings.json，密钥需复制 .env.example 为 .env 填写
REM 依赖安装失败时（镜像源缺包），脚本会自动改用官方源重试
REM ============================================================
cd /d "%~dp0"

echo [1/3] 安装 PyInstaller...
python -m pip install pyinstaller
if errorlevel 1 (
    echo 默认源失败，改用官方源重试...
    python -m pip install pyinstaller -i https://pypi.org/simple
)
if errorlevel 1 ( echo 安装 PyInstaller 失败 & pause & exit /b 1 )

echo [2/3] 打包中（约几分钟）...
python -m PyInstaller --noconfirm --clean --noconsole --onefile ^
    --name DeskSprite ^
    --icon assets\icon.ico ^
    --collect-all faster_whisper ^
    --collect-all ctranslate2 ^
    main.py
if errorlevel 1 ( echo 打包失败，请检查上方报错 & pause & exit /b 1 )

echo [3/3] 完成！
echo 产物：dist\DeskSprite.exe
echo 提示：
echo   - 首次启动前把 .env.example 复制为 .env 并填入密钥
echo   - Whisper 模型默认 small（首次自动下载），可在设置里改
pause
