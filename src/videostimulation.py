# Imports
import sys
import time
import socket
import json
import subprocess
import os
import logging
import numpy as np
import can
import cantools
import csv

from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QLineEdit,
    QFileDialog, QVBoxLayout, QHBoxLayout, QGroupBox, QFrame, QPlainTextEdit,QScrollArea 
)
from PyQt5.QtGui import QPixmap, QIcon, QFont
from PyQt5.QtCore import Qt, QTimer
from datetime import datetime
from pathlib import Path

# Configure logger
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("video_controller.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MPV_SOCKET = os.environ.get("MPV_SOCKET", "/tmp/mpvsocket")
DBC_PATH = os.environ.get("DBC_PATH", str(PROJECT_ROOT / "config" / "TSI_VFR.dbc"))


class VideoController(QWidget):
    def __init__(self):
        super().__init__()

        # Window
        self.setWindowTitle("Video Stimulation v0.1")
        self.setWindowIcon(QIcon(str(PROJECT_ROOT / "assets" / "Videostimulation_ICON.png")))
        self.setGeometry(100, 100, 650, 750)

        # Variables
        self.mpv_process = None
        self.video_path = ""
        self.dat_path = ""
        self.dat_times = None
        self.dat_speeds = None
        self.dat_time_offset = 0.0            # if video and .dat have an offset
        self.target_speed_kmh = 0.0
        self.language = "EN"  # Default language: English
        self.playing = False  # Flag: True = video playing, False = paused
        self.dat_speed_col_name = "Car.v"  # Default for CarMaker, user can change this
        self.dat_time_col_name = "Time"

        # Parameters for control / Limits
        # Operating playback region
        self.operating_min_factor = 0.5
        self.operating_max_factor = 2.0

        # Safety clamp limits
        self.min_factor = 0.3
        self.max_factor = 3.0

        # Ultra-smooth synchronization
        self.smoothing_alpha = 0.5

        # Reduce unnecessary mpv updates
        self.factor_change_threshold = 0.01

        # Rate limiting
        self.max_factor_step = 0.10

        # Low speed handling
        self.pause_speed_threshold = 0.5 #km/h
        
        self.is_paused_for_zero_speed = False  #auto-paused due to zero speed

        # Cached playback timing
        self.cached_playback_time = 0.0
        self.last_time_update = time.time()
        self.last_mpv_sync_time = time.time()

        # Sync monitoring
        self.last_sent_factor = 1.0
        self.mpv_launched = False

        self.displayed_target_speed = 0.0
        self.displayed_dat_speed = 0.0
        self.last_can_received_time = time.time()
        # Thesis results logging
        self.test_results = []
        self.test_start_time = None
        self.last_speed_can_perf_time = None

        # Language texts (DE and EN)
        self.texts = {
            "DE": {
                "video": "Bitte hinterlegen Sie hier Ihre Video-Datei (.mp4):",
                "dat": "Bitte hinterlegen Sie hier Ihre Dat-Datei (.dat):",
                "start": "Start",
                "switch": "EN",
                "browse": "Durchsuchen",
                "speed": "Aktuelle Geschwindigkeit aus .dat: n/a",
                "target": "Zielgeschwindigkeit (CAN): n/a",
                "factor": "Wiedergabefaktor: n/a"
            },
            "EN": {
                "video": "Please select your video file (.mp4):",
                "dat": "Please select your DAT file (.dat):",
                "start": "Start",
                "switch": "DE",
                "browse": "Browse",
                "speed": "Current speed from .dat: n/a",
                "target": "Target speed (CAN): n/a",
                "factor": "Playback factor: n/a"
            }
        }

        # Prepare CAN
        can_channel = os.environ.get("CAN_CHANNEL", "can0")
        try:
            self.bus = can.interface.Bus(interface="socketcan", channel=can_channel)
            self.dbc = cantools.database.load_file(DBC_PATH)
            self.can_ok = True
            logger.info(f"CAN interface successfully opened: {can_channel}")
        except Exception as e:
            logger.error(f"Error initializing CAN on {can_channel}: {e}")
            self.bus = None
            self.can_ok = False

        # UI and Timer
        self.init_ui()
        self.timer = QTimer()
        self.timer.timeout.connect(self.main_loop)
        self.timer.start(33)  # 30 hz synchronization loop
        if self.can_ok:
            self.can_timer = QTimer()
            self.can_timer.timeout.connect(self.read_can_data)
            self.can_timer.start(10)  # CAN polling very fast (10ms)

    def init_ui(self):
        # Main layout with margins and spacing
        layout = QVBoxLayout()
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        # ========== LOGO ==========
        logo_label = QLabel()
        logo_path = str(PROJECT_ROOT / "assets" / "Videostimulation_ICON.png")
        pixmap = QPixmap(logo_path)
        if pixmap.isNull():
            logger.warning(f"Could not load logo: {logo_path}")
        else:
            logo_label.setPixmap(pixmap)
            logo_label.setAlignment(Qt.AlignCenter)
            layout.addWidget(logo_label)

        # ========== LANGUAGE SWITCH WITH FLAG STYLE ==========
        language_frame = QFrame()
        language_frame.setStyleSheet("""
            QFrame {
                background-color: #e8e8e8;
                border-radius: 25px;
                padding: 3px;
            }
        """)
        language_layout = QHBoxLayout()
        language_layout.setContentsMargins(10, 5, 10, 5)
        
        self.btn_language = QPushButton("🇪🇺 EN/DE")
        self.btn_language.setStyleSheet("""
            QPushButton {
                background-color: #FFA500;
                color: white;
                border: none;
                border-radius: 20px;
                padding: 6px 20px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #FF8C00;
            }
        """)
        self.btn_language.setFixedWidth(110)
        self.btn_language.clicked.connect(self.switch_language)
        language_layout.addWidget(self.btn_language, alignment=Qt.AlignCenter)
        
        language_frame.setLayout(language_layout)
        layout.addWidget(language_frame, alignment=Qt.AlignCenter)

        self.can_status_label = QLabel("CAN Status: Initializing...")
        self.can_status_label.setStyleSheet("color: orange; font-size: 11px; font-weight: bold;")
        layout.addWidget(self.can_status_label, alignment=Qt.AlignCenter)

        # ========== VIDEO SELECTION GROUP ==========
        video_group = QGroupBox("1. VIDEO INPUT")
        video_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 2px solid #cccccc;
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 10px;
                background-color: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
        """)
        video_layout = QVBoxLayout()
        video_layout.setSpacing(6)

        # Row 1: Path display, Browse button, Clear button
        h_video_row1 = QHBoxLayout()
        self.entry_video = QLineEdit()
        self.entry_video.setReadOnly(True)
        self.entry_video.setPlaceholderText("Select an MP4 file...")
        self.entry_video.setStyleSheet("border: 1px solid #cccccc; border-radius: 4px; padding: 8px;")
        
        self.btn_video = QPushButton("📂 BROWSE")
        self.btn_video.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 8px 15px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #0b7dda;
            }
        """)
        self.btn_video.clicked.connect(lambda: self.browse_file("video", self.entry_video))
        
        # Red Cross button for clearing
        self.btn_video_clear = QPushButton("✖")
        self.btn_video_clear.setFixedWidth(35)
        self.btn_video_clear.setStyleSheet("""
            QPushButton {
                background-color: #f44336;
                color: white;
                border: none;
                border-radius: 5px;
                font-weight: bold;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #d32f2f;
            }
        """)
        self.btn_video_clear.clicked.connect(lambda: self.clear_file("video"))
        
        h_video_row1.addWidget(self.entry_video)
        h_video_row1.addWidget(self.btn_video)
        h_video_row1.addWidget(self.btn_video_clear)
        video_layout.addLayout(h_video_row1)

        # Row 2: Status indicator and file info
        h_video_row2 = QHBoxLayout()
        self.video_status_label = QLabel("⚪")
        self.video_status_label.setFixedWidth(30)
        self.video_status_label.setStyleSheet("font-size: 14px;")
        
        self.video_info_label = QLabel("No video selected")
        self.video_info_label.setStyleSheet("color: #666666; font-size: 11px;")
        
        h_video_row2.addWidget(self.video_status_label)
        h_video_row2.addWidget(self.video_info_label)
        video_layout.addLayout(h_video_row2)

        video_group.setLayout(video_layout)
        layout.addWidget(video_group)

        # ========== DAT SELECTION GROUP ==========
        dat_group = QGroupBox("2. REFERENCE DATA (DAT)")
        dat_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 2px solid #cccccc;
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 10px;
                background-color: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
        """)
        dat_layout = QVBoxLayout()
        dat_layout.setSpacing(6)

        # Row 1: Path display, Browse button, Clear button
        h_dat_row1 = QHBoxLayout()
        self.entry_dat = QLineEdit()
        self.entry_dat.setReadOnly(True)
        self.entry_dat.setPlaceholderText("Select a DAT / TRC / CSV file...")
        self.entry_dat.setStyleSheet("border: 1px solid #cccccc; border-radius: 4px; padding: 8px;")
        
        self.btn_dat = QPushButton("📂 BROWSE")
        self.btn_dat.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 8px 15px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #0b7dda;
            }
        """)
        self.btn_dat.clicked.connect(lambda: self.browse_file("dat", self.entry_dat))
        
        # Red Cross button for clearing
        self.btn_dat_clear = QPushButton("✖")
        self.btn_dat_clear.setFixedWidth(35)
        self.btn_dat_clear.setStyleSheet("""
            QPushButton {
                background-color: #f44336;
                color: white;
                border: none;
                border-radius: 5px;
                font-weight: bold;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #d32f2f;
            }
        """)
        self.btn_dat_clear.clicked.connect(lambda: self.clear_file("dat"))
        
        h_dat_row1.addWidget(self.entry_dat)
        h_dat_row1.addWidget(self.btn_dat)
        h_dat_row1.addWidget(self.btn_dat_clear)
        dat_layout.addLayout(h_dat_row1)

        # Row 2: Status indicator and file info
        h_dat_row2 = QHBoxLayout()
        self.dat_status_label = QLabel("⚪")
        self.dat_status_label.setFixedWidth(30)
        self.dat_status_label.setStyleSheet("font-size: 14px;")
        
        self.dat_info_label = QLabel("No DAT file selected")
        self.dat_info_label.setStyleSheet("color: #666666; font-size: 11px;")
        
        h_dat_row2.addWidget(self.dat_status_label)
        h_dat_row2.addWidget(self.dat_info_label)
        dat_layout.addLayout(h_dat_row2)
        
        #Row 3 for dynamic column name
        h_dat_row3 = QHBoxLayout()
        col_label = QLabel("Speed Column Name:")
        col_label.setStyleSheet("color: #333333; font-size: 11px;")
        self.entry_dat_col = QLineEdit()
        self.entry_dat_col.setText("Car.v") # Default value
        self.entry_dat_col.setMaximumHeight(25)
        self.entry_dat_col.setStyleSheet("border: 1px solid #cccccc; border-radius: 4px; padding: 4px; font-size: 11px;")
        h_dat_row3.addWidget(col_label)
        h_dat_row3.addWidget(self.entry_dat_col)

        dat_layout.addLayout(h_dat_row3)

        # Row 4: Dynamic time column name
        h_dat_row4 = QHBoxLayout()
        time_col_label = QLabel("Time Column Name:")
        time_col_label.setStyleSheet(
             "color: #333333; font-size: 11px;"
            )
        self.entry_time_col = QLineEdit()
        self.entry_time_col.setText("Time")
        self.entry_time_col.setMaximumHeight(25)
        self.entry_time_col.setStyleSheet(
            "border: 1px solid #cccccc; "
            "border-radius: 4px; "
            "padding: 4px; "
            "font-size: 11px;"
        )
        h_dat_row4.addWidget(time_col_label)
        h_dat_row4.addWidget(self.entry_time_col)
        dat_layout.addLayout(h_dat_row4)
        dat_group.setLayout(dat_layout)

        dat_group.setLayout(dat_layout)
        layout.addWidget(dat_group)

        # ========== START BUTTON ==========
        self.start_button = QPushButton("▶ START SIMULATION")
        self.start_button.setFont(QFont("Arial", 14, QFont.Bold))
        self.start_button.setStyleSheet("""
            QPushButton {
                background-color: #2e7d32;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1b5e20;
            }
            QPushButton:pressed {
                background-color: #0d3b0f;
            }
        """)
        self.start_button.setFixedHeight(50)
        self.start_button.clicked.connect(self.start_mpv)
        layout.addWidget(self.start_button, alignment=Qt.AlignCenter)

        # ========== PLAYBACK CONTROL BUTTONS ==========
        h_controls = QHBoxLayout()
        h_controls.setSpacing(15)
        
        self.play_button = QPushButton("▶ PLAY")
        self.pause_button = QPushButton("⏸ PAUSE")
        self.stop_button = QPushButton("■ STOP")
        
        button_style = """
            QPushButton {
                background-color: #555555;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 8px 20px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #333333;
            }
        """
        
        for btn in [self.play_button, self.pause_button, self.stop_button]:
            btn.setStyleSheet(button_style)
            h_controls.addWidget(btn)
        
        self.play_button.clicked.connect(self.play_action)
        self.pause_button.clicked.connect(self.pause_action)
        self.stop_button.clicked.connect(self.stop_action)
        layout.addLayout(h_controls)

        #Warning Label
        self.warning_label = QLabel("")
        self.warning_label.setStyleSheet("color: red; font-size: 11px; font-weight: bold;")
        self.warning_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.warning_label)

        # ========== LIVE DATA BOX - PROPERLY ALIGNED ==========
        data_group = QGroupBox("LIVE DATA")
        data_group.setStyleSheet("""
            QGroupBox {
                background-color: #1a1a2e;
                border: 2px solid #16213e;
                border-radius: 8px;
                margin-top: 8px;
                padding-top: 8px;
            }
            QGroupBox::title {
                color: #e0e0e0;
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
        """)
        data_layout = QVBoxLayout()
        data_layout.setSpacing(8)
        data_layout.setContentsMargins(15, 10, 15, 10)  # Left/right margins for alignment

        # Speed display labels with custom styling
        self.speed_label = QLabel(self.texts[self.language]["speed"])
        self.target_label = QLabel(self.texts[self.language]["target"])
        self.playback_factor_label = QLabel(self.texts[self.language]["factor"])

        for lbl in [self.speed_label, self.target_label, self.playback_factor_label]:
            lbl.setFont(QFont("Arial", 10))
            lbl.setAlignment(Qt.AlignCenter)  # Center align text
            lbl.setStyleSheet("""
                background-color: #0f3460;
                color: #e94560;
                padding: 10px;
                border-radius: 6px;
                font-weight: bold;
            """)
            data_layout.addWidget(lbl)

        data_group.setLayout(data_layout)
        layout.addWidget(data_group)
        
        # ========== NEW: CAN BUS MONITOR TERMINAL ==========
        can_group = QGroupBox("CAN BUS MONITOR")
        can_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 2px solid #333333;
                border-radius: 8px;
                margin-top: 8px;
                padding-top: 8px;
                background-color: #1e1e1e;
            }
            QGroupBox::title {
                color: #00ff00;
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
        """)
        can_layout = QVBoxLayout()
        can_layout.setContentsMargins(5, 5, 5, 5)
        
        self.can_log_display = QPlainTextEdit()
        self.can_log_display.appendPlainText("CAN Monitor Initialized")
        self.can_log_display.setReadOnly(True)
        # Terminal styling (Black background, green text, monospace font)
        self.can_log_display.setStyleSheet("""
            QPlainTextEdit {
                background-color: #000000;
                color: #00ff00;
                border: 1px solid #333333;
                border-radius: 4px;
                font-family: 'Courier New', monospace;
                font-size: 10px;
                padding: 5px;
            }
        """)
        
        self.can_log_display.setMaximumBlockCount(50) 
        can_layout.addWidget(self.can_log_display)
        
        can_group.setLayout(can_layout)
        layout.addWidget(can_group)

        # ========== STATUS BAR ==========
        self.status_label = QLabel("✓ System Ready - Select video and DAT file to begin")
        self.status_label.setStyleSheet("""
            QLabel {
                color: #2e7d32;
                font-size: 10px;
                padding: 6px;
                background-color: #e8f5e9;
                border-radius: 5px;
            }
        """)
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)
        
        container = QWidget()
        container.setLayout(layout)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(container)
        
        main_layout = QVBoxLayout()
        main_layout.addWidget(scroll)
        
        self.setLayout(main_layout)

    def switch_language(self):
        self.language = "EN" if self.language == "DE" else "DE"
        texts = self.texts[self.language]

        # Update language button text
        self.btn_language.setText("🇪🇺 EN/DE")
       
        # Update Start button
        self.start_button.setText("▶ " + texts["start"].upper() + " SIMULATION")
       
        # Update Browse buttons (keep icons, change text)
        self.btn_video.setText("📂 " + texts["browse"].upper())
        self.btn_dat.setText("📂 " + texts["browse"].upper())
       
        # Update speed display labels
        self.speed_label.setText(texts["speed"])
        self.target_label.setText(texts["target"])
        self.playback_factor_label.setText(texts["factor"])
       
        # Update the placeholder texts
        if hasattr(self, 'entry_video'):
            if self.language == "DE":
                self.entry_video.setPlaceholderText("Wählen Sie eine MP4-Datei...")
                self.entry_dat.setPlaceholderText("Wählen Sie eine DAT / TRC / CSV-Datei...")
                self.status_label.setText("✓ System bereit - Wählen Sie Video und DAT-Datei aus")
            else:
                self.entry_video.setPlaceholderText("Select an MP4 file...")
                self.entry_dat.setPlaceholderText("Select a DAT / TRC / CSV file...")
                self.status_label.setText("✓ System Ready - Select video and DAT file to begin")
       
        logger.info(f"Language switched to: {self.language}")

    def browse_file(self, file_type, entry):
        filters = {
            "dat": (
                "Select DAT/TRC/CSV file",
                "Data Files (*.dat *.trc *.csv)"
            ),
            "video": (
                "Select video",
                "MP4 files (*.mp4)"
            ),
        }
        title, file_filter = filters[file_type]
        path, _ = QFileDialog.getOpenFileName(
            self,
            title,
            "",
            file_filter
        )
        if not path:
            return

    
        # VIDEO FILE
        if file_type == "video":
            self.video_path = path
            entry.setText(path)
            self.update_video_info(path)
            return

    
        # REFERENCE FILE DAT / CSV / TRC
    
        self.dat_path = path
        entry.setText(path)

        # Reset old reference data before loading
        self.dat_times = None
        self.dat_speeds = None

        extension = os.path.splitext(path)[1].lower()

        if extension == ".dat":
            self.load_dat_file(path)
            file_format = "DAT"

        elif extension == ".csv":
            self.load_csv_file(path)
            file_format = "CSV"

        elif extension == ".trc":
            self.load_trc_file(path)
            file_format = "TRC"

        else:
            self.status_label.setText(
                "❌ Error: Unsupported reference file format"
            )

            self.dat_status_label.setText("❌")
            self.dat_status_label.setStyleSheet(
                "color: red; font-size: 16px;"
            )
            self.dat_info_label.setText(
                "Unsupported file format"
            )

            self.dat_path = ""
            return

        # ==========================================
        # VALIDATE LOADED DATA
        # ==========================================
        if (
            self.dat_times is None
            or self.dat_speeds is None
            or len(self.dat_times) == 0
            or len(self.dat_speeds) == 0
        ):
            self.dat_status_label.setText("❌")
            self.dat_status_label.setStyleSheet(
                "color: red; font-size: 16px;"
            )

            self.dat_info_label.setText(
                f"{file_format} file could not be loaded"
            )

            # Keep loader error message in status_label
            self.dat_path = ""
            return

        # ==========================================
        # SUCCESS
        # ==========================================
        self.dat_status_label.setText("✅")
        self.dat_status_label.setStyleSheet(
            "color: green; font-size: 16px;"
        )
        self.dat_info_label.setText(
            f"📊 {os.path.basename(path)} "
            f"({file_format} Format, "
            f"{len(self.dat_times)} samples)"
        )
        self.status_label.setText(
            f"✓ {file_format} file loaded successfully"
        )

    def update_video_info(self, filepath):
        """Show video file information"""
        if not filepath:
            self.video_info_label.setText("No video selected")
            return
        
        import os
        size = os.path.getsize(filepath) / (1024 * 1024)  # Size in MB
        filename = os.path.basename(filepath)  # Just the file name
        
        # Try to get video duration
        duration = "Unknown"
        try:
            import subprocess
            result = subprocess.run(
                ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                 '-of', 'default=noprint_wrappers=1:nokey=1', filepath],
                capture_output=True, text=True, timeout=5
            )
            if result.stdout:
                dur_sec = float(result.stdout.strip())
                minutes = int(dur_sec // 60)
                seconds = int(dur_sec % 60)
                duration = f"{minutes}:{seconds:02d} min"
        except:
            pass
        
        self.video_info_label.setText(f"📹 {filename} ({size:.1f} MB, {duration})")
        self.video_status_label.setText("✅")
        self.video_status_label.setStyleSheet("color: green; font-size: 16px;")
        self.status_label.setText(f"✓ Video loaded: {filename}")

    def update_dat_info(self, filepath):
        """Show DAT file information"""
        if not filepath:
            self.dat_info_label.setText("No DAT file selected")
            return
        
        import os
        size = os.path.getsize(filepath) / 1024  # Size in KB
        filename = os.path.basename(filepath)
        
        data_points = len(self.dat_times) if self.dat_times is not None else 0
        
        self.dat_info_label.setText(f"📊 {filename} ({size:.1f} KB, {data_points} samples)")
        self.dat_status_label.setText("✅")
        self.dat_status_label.setStyleSheet("color: green; font-size: 16px;")
        self.status_label.setText(f"✓ DAT file loaded: {filename}")

    def clear_file(self, file_type):
        """Clear selected file"""
        if file_type == "video":
            self.video_path = ""
            self.entry_video.clear()
            self.video_info_label.setText("No video selected")
            self.video_status_label.setText("⚪")
            self.video_status_label.setStyleSheet("")
            self.status_label.setText("Video cleared - Select a new video file")
        elif file_type == "dat":
            self.dat_path = ""
            self.entry_dat.clear()
            self.dat_times = None
            self.dat_speeds = None
            self.dat_info_label.setText("No DAT file selected")
            self.dat_status_label.setText("⚪")
            self.dat_status_label.setStyleSheet("")
            self.status_label.setText("DAT file cleared - Select a new DAT file")

    # MPV Control
        # MPV Control
    def start_mpv(self):
        # VALIDATE VIDEO
    
        if (
            not self.video_path
            or not self.video_path.lower().endswith(".mp4")
            or not os.path.isfile(self.video_path)
        ):
            logger.error("No valid MP4 file selected")

            self.status_label.setText(
                "❌ Error: Please select a valid MP4 video file"
            )
            return

    
        # VALIDATE REFERENCE FILE
    
        if not self.dat_path:
            self.status_label.setText(
                "❌ Error: Please select a DAT, CSV, or TRC reference file"
            )
            return

        if (
            self.dat_times is None
            or self.dat_speeds is None
            or len(self.dat_times) == 0
            or len(self.dat_speeds) == 0
        ):
            self.status_label.setText(
                "❌ Error: Reference file contains no valid speed data"
            )
            return

        if len(self.dat_times) != len(self.dat_speeds):
            self.status_label.setText(
                "❌ Error: Time and speed data length mismatch"
            )
            return
   
        # Clean up old socket if it exists
        if os.path.exists(MPV_SOCKET):
            try:
                os.remove(MPV_SOCKET)
            except Exception as e:
                logger.warning(f"Could not delete old mpv socket: {e}")

        # Terminate old process if running
        if hasattr(self, "mpv_process") and self.mpv_process:
            self.mpv_process.terminate()
            self.mpv_process.wait()
            self.mpv_process = None

        # Reset synchronization state
        self.cached_playback_time = 0.0
        self.last_time_update = time.time()
        self.last_mpv_sync_time = time.time()
        self.last_sent_factor = 1.0
        self.is_paused_for_zero_speed = False
        # Reset thesis results logging
        self.test_results = []
        self.test_start_time = time.perf_counter()

        logger.info("Thesis results logging started.")
        
        # Mark MPV as NOT launched yet
        self.mpv_launched = False
        
        # ARM THE SYSTEM (Do not launch video yet!)
        self.playing = True
        self.status_label.setText("🟡 System Armed - Waiting for vehicle movement...")

    def launch_mpv_background(self):
        """Launches MPV only when the car actually starts moving"""
        self.status_label.setText("▶ Vehicle moving - Launching Video...")
        
        self.mpv_process = subprocess.Popen([
            "mpv",
            f"--input-ipc-server={MPV_SOCKET}",
            "--loop",

            # LOW LATENCY SETTINGS
            "--profile=low-latency",
            "--hwdec=auto",       # ADDED: Raspberry Pi GPU decoding
            "--no-cache",
            "--vd-lavc-threads=2",
            "--video-sync=display-vdrop",
            "--interpolation=no",
            "--hr-seek=no",
            "--framedrop=vo",

            self.video_path
        ])
        
        # Short wait for socket to be created by OS
        time.sleep(0.3) 
        
        # Start playing immediately
        self.mpv_launched = True
        self.is_paused_for_zero_speed = False
        logger.info("MPV launched dynamically due to vehicle movement.")

    def send_command(self, command):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.connect(MPV_SOCKET)
                client.send((json.dumps(command) + "\n").encode())
        except Exception as e:
            logger.error(f"Socket connection to mpv failed: {e}")

    # Play/Pause Buttons
    def play_action(self):
        # Now redundant because START does this, but kept so UI button doesn't crash
        pass 

    def pause_action(self):
        if self.mpv_launched:
            self.send_command({"command": ["set_property", "pause", True]})
            self.playing = False
            self.status_label.setText("⏸ Manually Paused")

    def stop_action(self):
        self.export_test_results()
        # Only send stop command if MPV actually launched
        if self.mpv_launched:
            self.send_command({"command": ["stop"]})
            
        self.entry_video.clear()
        self.entry_dat.clear()
        self.speed_label.setText(self.texts[self.language]["speed"])
        self.target_label.setText(self.texts[self.language]["target"])
        self.playback_factor_label.setText(self.texts[self.language]["factor"])
        self.video_path = ""
        self.dat_path = ""
        self.dat_times = None
        self.dat_speeds = None
        self.playing = False
        self.mpv_launched = False
        self.status_label.setText("■ Stopped - Select new files to begin")

    # DAT Processing
    def load_dat_file(self, path):
        # Update the variable based on what the user typed in the UI
        self.dat_speed_col_name = self.entry_dat_col.text().strip()
        self.dat_time_col_name = self.entry_time_col.text().strip()
        
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            name_line = [l for l in lines if l.startswith("#Name")][0]
            col_names = name_line.strip().lstrip("#").split("\t")[1:]
            
            # Check if time column exists
            try:
                time_idx = col_names.index(self.dat_time_col_name)
            except ValueError:
                self.status_label.setText(
                    f"❌ Error: Time column '{self.dat_time_col_name}' not found in DAT file."
                )
                self.dat_times = None
                self.dat_speeds = None
                return

            # Use the custom column name provided by the user
            try:
                speed_idx = col_names.index(self.dat_speed_col_name)
            except ValueError:
                self.status_label.setText(f"❌ Error: Column '{self.dat_speed_col_name}' not found in DAT file.")
                self.dat_times = None
                self.dat_speeds = None
                return

            data_start = next(i for i, l in enumerate(lines) if not l.startswith("#"))
            data = [l.strip().split("\t") for l in lines[data_start:] if l.strip()]
            times = [float(row[time_idx]) for row in data] # Assuming Time is index 0
            speeds = [float(row[speed_idx]) * 3.6 for row in data]
            self.dat_times = np.array(times)
            self.dat_speeds = np.array(speeds)
            logger.info(f"DAT file loaded using column: {self.dat_speed_col_name} with {len(self.dat_times)} entries.")
        except Exception as e:
            logger.error(f"Error loading DAT file: {e}")
            self.status_label.setText(f"❌ Error reading DAT file: {e}")
            self.dat_times = None
            self.dat_speeds = None

    def load_trc_file(self, path):
        """Parses a Vector .trc file by decoding CAN data using the existing DBC file"""
        try:
            times = []
            speeds = []
            with open(path, "r") as f:
                for line in f:
                    line = line.strip()
                    # Skip comments, empty lines, and metadata blocks
                    if line.startswith(';') or not line:
                        continue
                    
                    parts = line.split()
                    if len(parts) < 5: 
                        continue
                    
                    try:
                        timestamp = float(parts[0])
                        can_id = int(parts[2], 16) # CAN ID is the 3rd column in a TRC file
                        
                        # Only process the wheel speed message we care about
                        if can_id == 0x220:
                            # Combine the data bytes (columns 3 to end) into a bytes object
                            data_bytes = bytes.fromhex("".join(parts[3:]))
                            
                            # Use our existing DBC decoder!
                            decoded = self.dbc.decode_message(can_id, data_bytes)
                            
                            if "Whl1_CdVxActl" in decoded:
                                mps = decoded["Whl1_CdVxActl"]
                                times.append(timestamp)
                                speeds.append(mps * 3.6)
                    except Exception as e:
                        logger.debug(f"Skipping malformed TRC line: {e}")
                        
            self.dat_times = np.array(times)
            self.dat_speeds = np.array(speeds)
            logger.info(f"TRC file loaded with {len(self.dat_times)} valid speed entries.")
            self.status_label.setText(f"✓ TRC file loaded ({len(self.dat_times)} samples)")
            
        except Exception as e:
            logger.error(f"Error loading TRC file: {e}")
            self.status_label.setText(f"❌ Error reading TRC file: {e}")
            self.dat_times = None
            self.dat_speeds = None        
    
    def load_csv_file(self, path):
        self.dat_speed_col_name = self.entry_dat_col.text().strip()
        self.dat_time_col_name = self.entry_time_col.text().strip()

        try:
            import csv
            with open(path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                headers = reader.fieldnames
                if headers is None:
                    raise Exception("CSV header not found")
                
                # Find time column entered by user
                if self.dat_time_col_name not in headers:
                    raise Exception(
                        f"Time column '{self.dat_time_col_name}' not found."
                    )
                time_col = self.dat_time_col_name

                # Find speed column entered by user

                if self.dat_speed_col_name not in headers:

                    raise Exception(
                       f"Column '{self.dat_speed_col_name}' not found."
                    )

                times = []
                speeds = []

                for row in reader:
                    try:
                        time_value = float(
                            row[time_col]
                        )
                        speed_value = float(
                            row[self.dat_speed_col_name]
                        )

                        times.append(time_value)
                        speeds.append(speed_value)
                    except (ValueError, TypeError):
                        # Skip unit rows such as "s" and "km/h"
                        continue

                if len(times) == 0:
                    raise Exception(
                        "No valid numeric time/speed rows found in CSV file."
                    )

                self.dat_times = np.array(times)
                self.dat_speeds = np.array(speeds)

                logger.info(
                    f"CSV loaded using column "
                    f"{self.dat_speed_col_name}"
                )

                self.status_label.setText(
                    f"✓ CSV loaded ({len(times)} samples)"
                )

        except Exception as e:

            logger.error(
                f"Error loading CSV file: {e}"
            )

            self.status_label.setText(
                f"❌ Error reading CSV file: {e}"
            )

            self.dat_times = None
            self.dat_speeds = None

    def video_speed_from_dat(self, current_time):
        """
        Interpolates speed from the DAT table for the given current_time.
        current_time should already include dat_time_offset if necessary.
        """
        if self.dat_times is None or self.dat_speeds is None:
            return 0.0
        # np.interp returns the linear interpolation value (constant extrapolation at edges)
        return float(np.interp(current_time, self.dat_times, self.dat_speeds))

    # CAN Query
    def read_can_data(self):
        if not self.can_ok:
            return
        try:
            msg = self.bus.recv(timeout=0.01)
            if msg:
                self.last_can_received_time = time.time()

                timestamp = time.strftime('%H:%M:%S.%f')[:-3]
                log_text = f"[{timestamp}] ID: 0x{msg.arbitration_id:03X} | Data: {msg.data.hex().upper()}"


                logger.debug(f"Received: ID=0x{msg.arbitration_id:X}, Data={msg.data.hex()}")
                if msg.arbitration_id == 0x220:  # P_Kin1 - Whl1_CdVxActl
                    try:
                        # Timestamp relevant speed CAN message
                        self.last_speed_can_perf_time = time.perf_counter()
                        decoded = self.dbc.decode_message(msg.arbitration_id, msg.data)
                        if "Whl1_CdVxActl" in decoded:
                            mps = decoded["Whl1_CdVxActl"]
                            self.target_speed_kmh = mps * 3.6
                            log_text += f"  --> SPEED: {self.target_speed_kmh:.2f} km/h"
                    except Exception as decode_err:
                        log_text += "  [DECODING ERROR]"
                self.can_log_display.appendPlainText(log_text)

        except Exception as e:
            logger.error(f"Error receiving CAN data: {e}")
    
    # RATE LIMITER
    def limit_factor_change(self, new_factor):

        delta = new_factor - self.last_sent_factor

        if abs(delta) > self.max_factor_step:

           if delta > 0:
              new_factor = self.last_sent_factor + self.max_factor_step

           else:
              new_factor = self.last_sent_factor - self.max_factor_step

        return new_factor
    
    # Main loop
        # Main loop
    def main_loop(self):
        

        # ==========================================
        # 1. UPDATE CAN STATUS UI
        # ==========================================
        time_since_last_can = time.time() - self.last_can_received_time
        if not self.can_ok:
            self.can_status_label.setText("CAN Status: ❌ Interface Error (Check cable/commands)")
            self.can_status_label.setStyleSheet("color: red; font-size: 11px; font-weight: bold;")
        elif time_since_last_can > 1.0:  # No CAN message for 1 second
            self.can_status_label.setText("CAN Status: ⚠️ No Data Received")
            self.can_status_label.setStyleSheet("color: orange; font-size: 11px; font-weight: bold;")
        else:
            self.can_status_label.setText("CAN Status: ✅ Receiving Messages")
            self.can_status_label.setStyleSheet("color: green; font-size: 11px; font-weight: bold;")

        # Check prerequisites
        if not self.playing or self.dat_times is None or not self.video_path:
            return    

        # ==========================================
        # 2. ZERO-SPEED / WAITING LOGIC
        # ==========================================
        if self.target_speed_kmh < self.pause_speed_threshold:
            self.speed_label.setText(f"{self.texts[self.language]['speed'].split(':')[0]}: 0.00 km/h")
            self.target_label.setText(f"{self.texts[self.language]['target'].split(':')[0]}: {self.target_speed_kmh:.2f} km/h")
            
            # If MPV is already running, pause it
            if self.mpv_launched and not self.is_paused_for_zero_speed:
                self.send_command({"command": ["set_property", "pause", True]})
                self.is_paused_for_zero_speed = True
                self.last_time_update = time.time()
            
            self.playback_factor_label.setText(f"{self.texts[self.language]['factor'].split(':')[0]}: WAITING FOR MOVEMENT")
            return  # Skip the rest of the loop!
        
        else:
            # ==========================================
            # 3. CAR IS MOVING (> 0.5 km/h)
            # ==========================================
            if not self.mpv_launched:
                # FIRST TIME MOVEMENT: Launch MPV now!
                self.launch_mpv_background()
                self.last_time_update = time.time()
                return # Skip math this tick to let MPV initialize
            
            # If MPV was running but paused, unpause it
            if self.is_paused_for_zero_speed:
                self.send_command({"command": ["set_property", "pause", False]})
                self.is_paused_for_zero_speed = False
                self.last_time_update = time.time()

        # ==========================================
        # 4. PLAYBACK FACTOR MATH (Only runs if MPV is alive)
        # ==========================================
        if self.mpv_launched:
            # Start software processing measurement
            processing_start = time.perf_counter()
            current_time = self.get_current_playback_time()
            # Apply offset if necessary (if video and dat are not synchronized to 0)
            current_time_corrected = current_time + self.dat_time_offset

            dat_speed = self.video_speed_from_dat(current_time_corrected)

            # --- DISPLAY SMOOTHER (Slows down text for human eyes) ---
            alpha_display = 0.15  
            self.displayed_target_speed = (1 - alpha_display) * self.displayed_target_speed + alpha_display * self.target_speed_kmh
            self.displayed_dat_speed = (1 - alpha_display) * self.displayed_dat_speed + alpha_display * dat_speed

            # UPDATE UI WITH SMOOTHED NUMBERS
            self.speed_label.setText(f"{self.texts[self.language]['speed'].split(':')[0]}: {self.displayed_dat_speed:.1f} km/h")
            self.target_label.setText(f"{self.texts[self.language]['target'].split(':')[0]}: {self.displayed_target_speed:.1f} km/h")
            
            # PLAYBACK FACTOR CALCULATION
            if dat_speed > 1.0:

               raw_factor = self.target_speed_kmh / dat_speed

               # --- LIMIT WARNING LOGIC (Check BEFORE clamping) ---
               if raw_factor < self.min_factor:
                   self.warning_label.setText(f"⚠️ WARNING: Required factor {raw_factor:.2f} is below min limit ({self.min_factor}). Video clamped.")
               elif raw_factor > self.max_factor:
                   self.warning_label.setText(f"⚠️ WARNING: Required factor {raw_factor:.2f} exceeds max limit ({self.max_factor}). Video clamped.")
               else:
                   self.warning_label.setText("") # Clear warning if back to normal

               # Clamp factor (Safety net)
               raw_factor = float(np.clip(
                   raw_factor,
                   self.min_factor,
                   self.max_factor
               ))

               # SMOOTHING FILTER (For actual video control - stays fast)
               smoothed = (
                   (1 - self.smoothing_alpha) * self.last_sent_factor
                   + self.smoothing_alpha * raw_factor
               )
               
               # RATE LIMITER
               smoothed = self.limit_factor_change(smoothed)
               
               # UPDATE ONLY IF NEEDED
               if abs(smoothed - self.last_sent_factor) > self.factor_change_threshold:

                  self.send_command({
                      "command": ["set_property", "speed", smoothed]
                    })

                  self.last_sent_factor = smoothed

                  logger.debug(
                      f"SYNC | raw={raw_factor:.3f} "
                      f"smooth={smoothed:.3f} "
                      f"video={dat_speed:.2f} "
                      f"target={self.target_speed_kmh:.2f}"
                    )

               self.playback_factor_label.setText(
                    f"{self.texts[self.language]['factor'].split(':')[0]}: {smoothed:.2f}x"
                )
               # THESIS RESULTS LOGGING
               
               if self.test_start_time is not None:
                   elapsed_time_s = (
                       time.perf_counter() - self.test_start_time
                       )
                   speed_difference_kmh = abs(
                       self.target_speed_kmh - dat_speed
                       )
                   # Calculate Raspberry Pi software processing time
                   processing_time_ms = (
                       time.perf_counter() - processing_start
                    ) * 1000.0
                   
                   # Calculate latest speed-CAN receive to control-loop point latency
                   if self.last_speed_can_perf_time is not None:
                       can_to_control_latency_ms = (
                           time.perf_counter()
                           - self.last_speed_can_perf_time
                        ) * 1000.0
                   else:
                       can_to_control_latency_ms = None

                   result_row = {
                       "elapsed_time_s": round(elapsed_time_s, 4),
                       "target_speed_kmh": round(self.target_speed_kmh, 3),
                       "reference_speed_kmh": round(dat_speed, 3),
                       "raw_factor": round(raw_factor, 4),
                       "applied_factor": round(self.last_sent_factor, 4),
                       "speed_difference_kmh": round(speed_difference_kmh, 3),
                       "processing_time_ms": round(processing_time_ms, 4),
                       "can_to_control_latency_ms": (
                           round(can_to_control_latency_ms, 4)
                           if can_to_control_latency_ms is not None
                           else None
                        )
                    }
                   self.test_results.append(result_row)
                   if len(self.test_results) % 30 == 0:
                       logger.info(
                           f"RESULT LOG | Samples={len(self.test_results)} | "
                           f"Target={self.target_speed_kmh:.2f} km/h | "
                           f"Reference={dat_speed:.2f} km/h | "
                           f"Raw PF={raw_factor:.3f} | "
                           f"Applied PF={self.last_sent_factor:.3f} | "
                           f"Difference={speed_difference_kmh:.2f} km/h"
                           )
               
            else:
                # dat_speed too small 
                self.playback_factor_label.setText(self.texts[self.language]["factor"])

    def get_current_playback_time(self):
        # SAFETY: Don't try to read socket if MPV hasn't launched yet!
        if not self.mpv_launched:
            return 0.0

        now = time.time()
        dt = now - self.last_time_update
        self.last_time_update = now

        self.cached_playback_time += dt * self.last_sent_factor

        if now - self.last_mpv_sync_time > 2.5:
            self.last_mpv_sync_time = now
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.connect(MPV_SOCKET)
                    client.send(b'{"command": ["get_property", "time-pos"]}\n')
                    client.settimeout(0.1) # Fast fail
                    data = client.recv(4096).decode(errors="ignore").strip()
                    for line in data.splitlines():
                        if line.strip():
                            obj = json.loads(line.strip())
                            real_time = float(obj.get("data", 0.0))
                            
                            # If MPV is lagging behind our prediction (real_time is less than our cache)
                            time_diff = real_time - self.cached_playback_time
                            
                            # If MPV is lagging more than 0.5 seconds behind, ignore MPV and trust our cache
                            if time_diff < -0.5:
                                logger.warning(f"MPV LAG DETECTED (Diff: {time_diff:.2f}s). Trusting local cache to prevent sync destruction.")
                            else:
                                # Otherwise, MPV is close enough, sync to it
                                self.cached_playback_time = real_time
                            
                            break
            except Exception:
                pass # If it fails, just keep using cached time

        return self.cached_playback_time
    
    def export_test_results(self):
        if not self.test_results:
            logger.info("No thesis test results available to export.")
            self.status_label.setText("⚠️ No test results to save")
            return

        # Create thesis results folder
        results_folder = Path(os.environ.get("RESULTS_DIR", str(PROJECT_ROOT / "data" / "generated_results")))

        try:
            os.makedirs(results_folder, exist_ok=True)
            # Create unique timestamp 
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            # Result filename
            filename = f"NEW_SYSTEM_{timestamp}.csv"

            # Complete output path
            filepath = os.path.join(
                results_folder,
                filename
            )

            # CSV column names
            fieldnames = [
                "elapsed_time_s",
                "target_speed_kmh",
                "reference_speed_kmh",
                "raw_factor",
                "applied_factor",
                "speed_difference_kmh",
                "processing_time_ms",
                "can_to_control_latency_ms"
            ]

            # Write results to CSV
            with open(
                filepath,
                "w",
                newline="",
                encoding="utf-8"
            ) as csv_file:
                writer = csv.DictWriter(
                    csv_file,
                    fieldnames=fieldnames
                )

                writer.writeheader()

                writer.writerows(
                    self.test_results
                )
            logger.info(
                f"Thesis results exported successfully: {filepath}"
            )

            self.status_label.setText(
                f"✓ Results saved: {filename}"
            )
        except Exception as e:
            logger.error(
                f"Error exporting thesis results: {e}"
            )
            self.status_label.setText(
                f"❌ Results export failed: {e}"
            )

    def closeEvent(self, event):
        # Safety check before terminating
        if hasattr(self, 'mpv_process') and self.mpv_process:
            self.mpv_process.terminate()
            self.mpv_process.wait()
        event.accept()

def main():
    app = QApplication(sys.argv)
    controller = VideoController()
    controller.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
