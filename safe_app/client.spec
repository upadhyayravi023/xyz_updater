from PyInstaller.utils.hooks import collect_all
from pathlib import Path
root = Path(__file__).resolve().parent
pil = collect_all("PIL")
a = Analysis([str(root / "client.py")], pathex=[str(root)], binaries=pil[1], datas=pil[0], hiddenimports=pil[2], excludes=["pynput", "openai"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="OpenTxtAssistant", console=False, onefile=True, upx=False, disable_windowed_traceback=False)
