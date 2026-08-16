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

from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QLineEdit,
    QFileDialog, QVBoxLayout, QHBoxLayout
)
from PyQt5.QtGui import QPixmap, QIcon, QFont
from PyQt5.QtCore import Qt, QTimer

# Logger konfigurieren
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("video_controller.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

MPV_SOCKET = "/tmp/mpvsocket"
DBC_PATH = "/home/ipek/Bescheunigt/TSI_VFR.dbc"  # Absoluter Pfad zur DBC-Datei

class VideoController(QWidget):
    def __init__(self):
        super().__init__()

        # Fenster
        self.setWindowTitle("Videostimulation v0.1")
        self.setWindowIcon(QIcon("/home/ipek/Videostimulation/IPEK_logo.png"))
        self.setGeometry(100, 100, 600, 550)

        # Variablen
        self.mpv_process = None
        self.video_path = ""
        self.dat_path = ""
        self.dat_times = None
        self.dat_speeds = None
        self.dat_time_offset = 0.0            # falls Video und .dat einen Offset haben
        self.target_speed_kmh = 0.0
        self.last_speed_factor = None
        self.language = "DE"  # Standard-Sprache
        self.playing = False  # Flag: True = Video läuft, False = pausiert

        # ==========================================
        # TEST MEASUREMENT AND RESULT LOGGING
        # ==========================================
        self.test_results = []
        self.test_start_time = None
        self.last_speed_can_perf_time = None

        # Parameter für Regelung / Limits
        self.min_factor = 0.2
        self.max_factor = 4.0
        self.smoothing_alpha = 0.2  # 0..1, größer = schnelleres Anpassen
        self.pause_speed_threshold = 0.5  # km/h, unter dem wir als "Pause" interpretieren
        self.factor_change_threshold = 0.02  # minimale Änderung, um Befehl abzusetzen

        # Linguistik
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

        # CAN vorbereiten
        can_channel = os.environ.get("CAN_CHANNEL", "can0")
        try:
            self.bus = can.interface.Bus(interface="socketcan", channel=can_channel)
            self.dbc = cantools.database.load_file("/home/ipek/Beschleunigt/TSI_VFR.dbc")
            self.can_ok = True
            logger.info(f"CAN-Interface erfolgreich geöffnet: {can_channel}")
        except Exception as e:
            logger.error(f"Fehler beim Initialisieren von CAN auf {can_channel}: {e}")
            self.bus = None
            self.can_ok = False

        # UI und Timer
        self.init_ui()
        self.timer = QTimer()
        self.timer.timeout.connect(self.main_loop)
        self.timer.start(100)  # Hauptloop alle 100 ms

        if self.can_ok:
            self.can_timer = QTimer()
            self.can_timer.timeout.connect(self.read_can_data)
            self.can_timer.start(10)  # CAN-Polling sehr schnell (10ms)

    def init_ui(self):
        layout = QVBoxLayout()

        # Logo
        logo_label = QLabel()
        logo_path = "/home/ipek/Videostimulation/IPEK_logo.png"
        pixmap = QPixmap(logo_path)
        if pixmap.isNull():
            logger.warning(f"Logo konnte nicht geladen werden: {logo_path}")
        else:
            logo_label.setPixmap(pixmap)
            logo_label.setAlignment(Qt.AlignCenter)
            layout.addWidget(logo_label)

        # Sprachumschalter
        self.btn_language = QPushButton(self.texts[self.language]["switch"])
        self.btn_language.setFixedWidth(80)
        self.btn_language.clicked.connect(self.switch_language)
        layout.addWidget(self.btn_language, alignment=Qt.AlignCenter)

        # Video-Auswahl
        self.label_video = QLabel(self.texts[self.language]["video"])
        self.entry_video = QLineEdit()
        self.entry_video.setReadOnly(True)
        self.btn_video = QPushButton(self.texts[self.language]["browse"])
        self.btn_video.clicked.connect(lambda: self.browse_file("video", self.entry_video))
        h_video = QHBoxLayout()
        h_video.addWidget(self.entry_video)
        h_video.addWidget(self.btn_video)
        layout.addWidget(self.label_video)
        layout.addLayout(h_video)

        # DAT-Auswahl
        self.label_dat = QLabel(self.texts[self.language]["dat"])
        self.entry_dat = QLineEdit()
        self.entry_dat.setReadOnly(True)
        self.btn_dat = QPushButton(self.texts[self.language]["browse"])
        self.btn_dat.clicked.connect(lambda: self.browse_file("dat", self.entry_dat))
        h_dat = QHBoxLayout()
        h_dat.addWidget(self.entry_dat)
        h_dat.addWidget(self.btn_dat)
        layout.addWidget(self.label_dat)
        layout.addLayout(h_dat)

        # Start Button
        self.start_button = QPushButton(self.texts[self.language]["start"])
        self.start_button.setFont(QFont("Arial", 14, QFont.Bold))
        self.start_button.setFixedHeight(50)
        self.start_button.clicked.connect(self.start_mpv)
        layout.addWidget(self.start_button, alignment=Qt.AlignCenter)

        # Play/Pause/Stop Buttons
        h_controls = QHBoxLayout()
        self.play_button = QPushButton("▶")
        self.pause_button = QPushButton("⏸")
        self.stop_button = QPushButton("■")
        for btn in [self.play_button, self.pause_button, self.stop_button]:
            btn.setFont(QFont("DejaVu Sans", 18))
            h_controls.addWidget(btn)
        self.play_button.clicked.connect(self.play_action)
        self.pause_button.clicked.connect(self.pause_action)
        self.stop_button.clicked.connect(self.stop_action)
        layout.addLayout(h_controls)

        # Labels für Geschwindigkeiten und Wiedergabe
        self.speed_label = QLabel(self.texts[self.language]["speed"])
        self.target_label = QLabel(self.texts[self.language]["target"])
        self.playback_factor_label = QLabel(self.texts[self.language]["factor"])
        for lbl in [self.speed_label, self.target_label, self.playback_factor_label]:
            lbl.setFont(QFont("Arial", 12, QFont.Bold))
            layout.addWidget(lbl, alignment=Qt.AlignCenter)

        self.setLayout(layout)

    def switch_language(self):
        self.language = "EN" if self.language == "DE" else "DE"
        texts = self.texts[self.language]

        self.label_video.setText(texts["video"])
        self.label_dat.setText(texts["dat"])
        self.start_button.setText(texts["start"])
        self.btn_language.setText(texts["switch"])
        self.btn_video.setText(texts["browse"])
        self.btn_dat.setText(texts["browse"])
        self.speed_label.setText(texts["speed"])
        self.target_label.setText(texts["target"])
        self.playback_factor_label.setText(texts["factor"])

    def browse_file(self, file_type, entry):
        filters = {
            "video": ("Video auswählen", "MP4 Dateien (*.mp4)"),
            "dat": ("DAT-Datei auswählen", "DAT Dateien (*.dat)"),
        }
        title, file_filter = filters[file_type]
        path, _ = QFileDialog.getOpenFileName(self, title, "", file_filter)
        if path:
            setattr(self, f"{file_type}_path", path)
            entry.setText(path)
            if file_type == "dat":
                self.load_dat_file(path)

    # MPV Steuerung
    def start_mpv(self):
        # sicherstellen, dass video_path gesetzt ist
        if not getattr(self, "video_path", "") or not self.video_path.lower().endswith(".mp4"):
            logger.error("Keine gültige MP4-Datei ausgewählt!")
            return

        if os.path.exists(MPV_SOCKET):
            try:
                os.remove(MPV_SOCKET)
            except Exception as e:
                logger.warning(f"Konnte alten mpv Socket nicht löschen: {e}")

        if hasattr(self, "mpv_process") and self.mpv_process:
            self.mpv_process.terminate()
            self.mpv_process.wait()

        # mpv starten (loop, IPC)
        self.mpv_process = subprocess.Popen([
            "mpv", f"--input-ipc-server={MPV_SOCKET}", "--loop", self.video_path
        ])
        # kurz warten, bis mpv Socket bereit ist
        time.sleep(1.0)
        # direkt pausieren, bis Play gedrückt wird
        self.send_command({"command": ["set_property", "pause", True]})
        self.playing = False
        self.last_speed_factor = None

        # ==========================================
        # START NEW TEST MEASUREMENT
        # ==========================================
        self.test_results = []
        self.test_start_time = time.perf_counter()

        logger.info(
            "OLD SYSTEM test measurement started"
        )

    def send_command(self, command):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.connect(MPV_SOCKET)
                client.send((json.dumps(command) + "\n").encode())
        except Exception as e:
            logger.error(f"Socket-Verbindung zu mpv fehlgeschlagen: {e}")

    # Play/Pause Buttons
    def play_action(self):
        if self.video_path:
            self.send_command({"command": ["set_property", "pause", False]})
            self.playing = True

    def pause_action(self):
        if self.video_path:
            self.send_command({"command": ["set_property", "pause", True]})
            self.playing = False

    def stop_action(self):
        # ==========================================
        # EXPORT OLD SYSTEM TEST RESULTS
        # ==========================================
        self.export_test_results()
        # Stop MPV
        self.send_command({"command": ["stop"]})

        # Clear UI
        self.entry_video.clear()
        self.entry_dat.clear()

        self.speed_label.setText(
            self.texts[self.language]["speed"]
        )

        self.target_label.setText(
            self.texts[self.language]["target"]
        )

        self.playback_factor_label.setText(
            self.texts[self.language]["factor"]
        )

        # Clear file data
        self.video_path = ""
        self.dat_path = ""
        self.dat_times = None
        self.dat_speeds = None

        # Stop simulation state
        self.playing = False

        # Reset measurement state
        self.test_start_time = None
        self.last_speed_can_perf_time = None

        logger.info(
            "OLD SYSTEM test stopped"
        )
        
    # DAT Verarbeitung
    def load_dat_file(self, path):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            name_line = [l for l in lines if l.startswith("#Name")][0]
            col_names = name_line.strip().lstrip("#").split("\t")[1:]
            time_idx = col_names.index("Time")
            speed_idx = col_names.index("Car.v")
            data_start = next(i for i, l in enumerate(lines) if not l.startswith("#"))
            data = [l.strip().split("\t") for l in lines[data_start:] if l.strip()]
            times = [float(row[time_idx]) for row in data]
            speeds = [float(row[speed_idx]) * 3.6 for row in data]  # m/s -> km/h
            self.dat_times = np.array(times)
            self.dat_speeds = np.array(speeds)
            logger.info(f".dat Datei geladen mit {len(self.dat_times)} Einträgen.")
            # sanity-check (optional)
            if len(self.dat_times) >= 2:
                dt_median = np.median(np.diff(self.dat_times))
                logger.info(f".dat median dt = {dt_median:.4f} s")
        except Exception as e:
            logger.error(f"Fehler beim Laden der .dat Datei: {e}")
            self.dat_times = None
            self.dat_speeds = None

    def video_speed_from_dat(self, current_time):
        """
        Interpoliert die Geschwindigkeit aus der .dat-Tabelle für den gegebenen current_time.
        current_time sollte bereits den dat_time_offset enthalten, falls notwendig.
        """
        if self.dat_times is None or self.dat_speeds is None:
            return 0.0
        # np.interp gibt den linearen Interpolationswert (extrapoliert konstant am Rand)
        return float(np.interp(current_time, self.dat_times, self.dat_speeds))

    # CAN Abfrage
    def read_can_data(self):
        if not self.can_ok:
            return
        try:
            msg = self.bus.recv(timeout=0.01)
            if msg:
                logger.debug(f"Empfangen: ID=0x{msg.arbitration_id:X}, Daten={msg.data.hex()}")
                if msg.arbitration_id == 0x220:
                    try:
                        decoded = self.dbc.decode_message(msg.arbitration_id, msg.data)
                        if "Whl1_CdVxActl" in decoded:
                            mps = decoded["Whl1_CdVxActl"]
                            self.target_speed_kmh = mps * 3.6

                            # Record speed CAN reception time for latency measurement
                            self.last_speed_can_perf_time = time.perf_counter()

                    except Exception as decode_err:
                        logger.error(f"Fehler beim Dekodieren: {decode_err}")
        except Exception as e:
            logger.error(f"Fehler beim CAN-Empfang: {e}")

    # Hauptloop
    def main_loop(self):
        # Start processing-time measurement
        processing_start = time.perf_counter()

        # Voraussetzungen prüfen
        if not self.playing or self.dat_times is None or not self.video_path:
            return

        current_time = self.get_current_playback_time()
        # ggf. Offset anwenden (falls Video und dat nicht auf 0 synchron sind)
        current_time_corrected = current_time + self.dat_time_offset

        dat_speed = self.video_speed_from_dat(current_time_corrected)

        self.speed_label.setText(f"{self.texts[self.language]['speed'].split(':')[0]}: {dat_speed:.2f} km/h")
        self.target_label.setText(f"{self.texts[self.language]['target'].split(':')[0]}: {self.target_speed_kmh:.2f} km/h")

        # Wenn Ziel nahezu 0 -> Pause (kleine Hysterese kann nötig sein)
        if self.target_speed_kmh < self.pause_speed_threshold:
            # pausiere nur, wenn Ziel wirklich nahe 0
            self.send_command({"command": ["set_property", "pause", True]})
            self.playback_factor_label.setText(f"{self.texts[self.language]['factor'].split(':')[0]}: Pause")
            return

        # ansonsten sicherstellen, dass nicht pausiert ist
        self.send_command({"command": ["set_property", "pause", False]})

        # wenn gültiger dat_speed vorhanden, berechne Faktor
        if dat_speed > 0.01:
            raw_factor = self.target_speed_kmh / dat_speed

            # schütze vor extremen/unannehmbaren Werten
            factor = float(np.clip(raw_factor, self.min_factor, self.max_factor))

            # smoothing / low-pass
            if self.last_speed_factor is None:
                smoothed = factor
            else:
                alpha = self.smoothing_alpha
                smoothed = (1 - alpha) * self.last_speed_factor + alpha * factor

            # nur senden, wenn Änderung relevant
            if self.last_speed_factor is None or abs(smoothed - self.last_speed_factor) > self.factor_change_threshold:
                self.send_command({"command": ["set_property", "speed", smoothed]})
                logger.debug(f"Setze mpv speed: {smoothed:.3f} (raw={raw_factor:.3f}, dat_speed={dat_speed:.2f}, target={self.target_speed_kmh:.2f})")
                self.last_speed_factor = smoothed

            self.playback_factor_label.setText(f"{self.texts[self.language]['factor'].split(':')[0]}: {smoothed:.2f}x")

            # ==========================================
            # STORE OLD SYSTEM TEST RESULT
            # ==========================================
            
            processing_end = time.perf_counter()
            processing_time_ms = (
                processing_end - processing_start
            ) * 1000.0
            if self.last_speed_can_perf_time is not None:
                can_to_control_latency_ms = (
                    processing_end - self.last_speed_can_perf_time
                ) * 1000.0
            else:
                can_to_control_latency_ms = None

            if self.test_start_time is not None:
                elapsed_time_s = (
                    processing_end - self.test_start_time
                )

                speed_difference_kmh = (
                    self.target_speed_kmh - dat_speed
                )
                self.test_results.append({
                    "elapsed_time_s": elapsed_time_s,
                    "target_speed_kmh": self.target_speed_kmh,
                    "reference_speed_kmh": dat_speed,
                    "raw_factor": raw_factor,
                    "applied_factor": smoothed,
                    "speed_difference_kmh": speed_difference_kmh,
                    "processing_time_ms": processing_time_ms,
                    "can_to_control_latency_ms": can_to_control_latency_ms,
                })
        else:
            # dat_speed zu klein -> nichts tun, evtl. Pause anzeigen
            self.playback_factor_label.setText(self.texts[self.language]["factor"])
    
    def export_test_results(self):
        # ==========================================
        # EXPORT OLD SYSTEM TEST RESULTS TO CSV
        # ==========================================
        if not self.test_results:
            logger.warning(
                "No OLD SYSTEM test results available for export"
            )
            return
        try:
            import csv
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filename = (
                f"OLD_SYSTEM_{timestamp}.csv"
            )
            fieldnames = [
                "elapsed_time_s",
                "target_speed_kmh",
                "reference_speed_kmh",
                "raw_factor",
                "applied_factor",
                "speed_difference_kmh",
                "processing_time_ms",
                "can_to_control_latency_ms",
            ]
            with open(
                filename,
                "w",
                newline="",
                encoding="utf-8"
            ) as csvfile:
                writer = csv.DictWriter(
                    csvfile,
                    fieldnames=fieldnames
                )

                writer.writeheader()
                writer.writerows(
                    self.test_results
                )
            logger.info(
                f"OLD SYSTEM test results exported: {filename}"
            )

        except Exception as e:
            logger.error(
                f"Error exporting OLD SYSTEM test results: {e}"
            )

    def get_current_playback_time(self):
        """
        Liest time-pos von mpv via IPC. Robust gegenüber mehreren JSON-Zeilen.
        """
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.connect(MPV_SOCKET)
                client.send(b'{"command": ["get_property", "time-pos"]}\n')
                # read loop (kurz), mpv kann mehrere JSON-Nachrichten senden
                data = b""
                client.settimeout(0.2)
                try:
                    while True:
                        chunk = client.recv(4096)
                        if not chunk:
                            break
                        data += chunk
                        # safety: kurze Abbruchbedingung, falls genug gelesen
                        if b"\n" in data:
                            break
                except socket.timeout:
                    pass
                text = data.decode(errors="ignore").strip()
                # nimm die erste nicht-leere Zeile und parse JSON
                for line in text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        return float(obj.get("data", 0.0))
                    except Exception:
                        continue
                return 0.0
        except Exception as e:
            logger.warning(f"Fehler beim Abfragen der Abspielzeit: {e}")
            return 0.0

    def closeEvent(self, event):
        if self.mpv_process:
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
