"""
K-Hunter Trading System - Main Entry Point
실행 스크립트: python run.py
"""

import sys
import os

# UTF-8 인코딩 설정
if sys.stdout:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr:
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# 프로젝트 루트 추가
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt
from loguru import logger

# 로깅 설정
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    level="INFO"
)
logger.add(
    "logs/khunter_{time:YYYY-MM-DD}.log",
    rotation="1 day",
    retention="7 days",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
    level="DEBUG"
)


def main():
    """메인 함수"""
    # logs 디렉토리 생성
    os.makedirs("logs", exist_ok=True)

    logger.info("=" * 50)
    logger.info("K-Hunter Trading System Starting...")
    logger.info("=" * 50)

    # High DPI 지원
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    # 애플리케이션 생성
    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    # 메인 윈도우
    from src.ui.main_window import MainWindow
    window = MainWindow()
    window.show()

    logger.info("Application started successfully")

    # 이벤트 루프 실행
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
