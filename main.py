import sys
from pathlib import Path
from PySide6.QtWidgets import QApplication

from gui.main_window import MainWindow
from core import config


def get_app_dir() -> Path:
    """
    Where the database should live. When packaged with PyInstaller
    (--onefile or --onedir), sys.executable points at the actual .exe;
    when run as a plain script, __file__ points at main.py. Either way
    this resolves to a stable folder next to the app - never the onefile
    temp extraction dir, which gets wiped after the app closes.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def main():
    app = QApplication(sys.argv)
    db_path = str(get_app_dir() / config.DEFAULT_DB_FILENAME)
    window = MainWindow(db_path=db_path)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
